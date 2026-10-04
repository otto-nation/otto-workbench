"""The rebase commands behind `pr rebase`: start, abort, push, and the run that picks one.

Each command resolves the branch's target, takes the lease the rebase needs,
and records the outcome in the PR's state file. `cli.pr_rebase` is the command
over these — the parser, the run lock, the trail. The rebase mechanics are the
rest of this package (`rebase.lifecycle`, `rebase.land`, `rebase.lease`, …).
"""

# doc-group: pr-state

from __future__ import annotations

from dataclasses import dataclass

import core.log
import core.publishing
import core.run_lock
import core.trail
from core.trail import Trail
import git.client
import pr.context
import pr.state
from pr.domains import RebaseStatus
import rebase.inspect
import rebase.land
import rebase.lease
import rebase.lifecycle
import rebase.pr_snapshot
import rebase.stash
import rebase.target
import rebase.types


@dataclass(frozen=True)
class RebaseTarget:
    ctx: pr.context.ResolvedContext
    cwd: str
    target_ref: str


def _resolve(args) -> RebaseTarget:
    """Turn parsed args into the checkout and target the run acts on."""
    ctx = pr.context.resolve(
        repo_dir=args.repo_dir, branch=args.branch, pr_ref=args.pr,
    )
    cwd = str(ctx.require_worktree())
    snapshot = rebase.pr_snapshot.fetch(cwd, ctx)
    target_ref = rebase.target.resolve_target_ref(
        cwd, ctx, args.onto, snapshot=snapshot,
    )
    return RebaseTarget(ctx=ctx, cwd=cwd, target_ref=target_ref)


# ── Subcommands ─────────────────────────────────────────────────────────────


def cmd_abort(
    cwd: str, ctx: pr.context.ResolvedContext, *, target_ref: str,
) -> int:
    """Abort an in-progress rebase and reset state."""
    core.log.info("Aborting rebase...")
    r = git.client.run("rebase", "--abort", cwd=cwd)
    if r.ok:
        rebase.types.RebaseOutcome(status=RebaseStatus.ABORTED, target_base=target_ref).save(ctx)
        core.log.ok("Rebase aborted.")
    return r.returncode


def cmd_push(
    cwd: str, ctx: pr.context.ResolvedContext, *, target_ref: str,
    verify: bool = True,
    snapshot: rebase.pr_snapshot.PRSnapshot | None = None,
    trail: Trail | None = None,
) -> int:
    """Force-push after a completed rebase."""
    if rebase.inspect.rebase_in_progress(cwd):
        core.trail.terr(trail, "push", "rebase still in progress")
        core.log.error("Cannot push — rebase still in progress.")
        return 1

    state = rebase.types.load_or_init(ctx)
    if not state.rebase.updated_at:
        core.trail.terr(trail, "push", "no recorded rebase to push")
        core.log.error("Cannot push — no rebase recorded for this branch.")
        core.log.dim("Run `pr rebase` first, or push by hand.")
        return 1

    # The lease the rebase recorded, not one rebuilt here. By now HEAD is the
    # rewritten tip and origin/<branch> is whatever the rebase's own fetch
    # brought down, so neither reading can say what the remote was at before
    # the replay — which is the only thing a lease may name.
    lease = rebase.lease.PushLease(
        branch=ctx.branch, expect=state.rebase.lease_expect,
    )
    # An empty expect is a real value ("the remote must not have this ref
    # yet"), but it is also what a state file written before `lease_expect`
    # existed deserializes to. The two are indistinguishable in state.json, so
    # check the claim against the checkout's own view of the remote rather
    # than trusting it blindly — a branch this old cannot legitimately still
    # be unpushed.
    if lease.creates_the_ref and rebase.inspect.ref_exists(
        cwd, f"refs/remotes/origin/{ctx.branch}",
    ):
        core.trail.terr(
            trail, "push", "recorded lease is stale — origin already has this branch",
            data={"branch": ctx.branch},
        )
        core.log.error("Cannot push — the recorded rebase has no lease, but origin "
                  "already has this branch.")
        core.log.dim("Run `pr rebase` again (without --no-push) to record a fresh "
                "lease, or push by hand after checking what origin holds.")
        return 1

    rebase.pr_snapshot.name_the_open_pr(snapshot, trail=trail)
    core.log.info("Force-pushing...")
    landed = rebase.land.land_rebased(
        cwd, args=lease.args, verify=verify, trail=trail,
    )
    if not landed.ok:
        core.trail.terr(
            trail, "push", "force-push failed",
            data={"status": str(landed.status), "resume": landed.resume},
        )
        return 1

    rebase.types.RebaseOutcome(
        commits_replayed=(state.rebase.commits_replayed
                          or git.client.commits_ahead(cwd, target_ref=target_ref)),
        conflicts_resolved=state.rebase.conflicts_resolved,
        files_resolved=state.rebase.files_resolved,
        files_stale=state.rebase.files_stale,
        force_pushed=True,
        lease_expect=state.rebase.lease_expect,
        target_base=target_ref,
    ).save(ctx)
    core.log.ok("Force-pushed successfully.")
    return 0


def cmd_start(
    cwd: str, ctx: pr.context.ResolvedContext, mode: rebase.types.RunMode,
    force: bool = False, *, target_ref: str, fork_point: str = "",
    verify: bool = True,
    snapshot: rebase.pr_snapshot.PRSnapshot | None = None,
    trail: Trail | None = None,
) -> int:
    """Start or resume a rebase onto the resolved target ref.

    ``force`` waives the already-landed preflight, which only a fresh rebase
    runs: a resumed one is already past the point the refusal protects.

    The conflict budget is waived on the resume path for the same reason, and
    for a sharper one — it refuses by aborting, and a resumed rebase is one an
    operator may have half-resolved by hand.

    ``fork_point`` likewise applies only to a fresh rebase: it selects which
    commits git replays, and a resumed rebase's todo list was written by the
    run that started it.
    """
    if rebase.inspect.rebase_in_progress(cwd):
        target_ref = rebase.target.resume_target_ref(ctx, target_ref, trail=trail)
        core.trail.tdecision(
            trail, "rebase_state", "detected in-progress rebase",
            reason="rebase-merge or rebase-apply directory exists",
        )
        if fork_point:
            core.log.warn(f"Ignoring --fork-point {fork_point} — the in-progress "
                     "rebase already has its list of commits to replay.")
        core.log.info("Detected in-progress rebase — resuming...")
        try:
            return rebase.lifecycle.drive_to_completion(
                cwd, ctx, mode, target_ref=target_ref, force=True,
                verify=verify, snapshot=snapshot, trail=trail,
            )
        finally:
            # An earlier run may have stashed and then stopped with the rebase
            # still in progress, holding the entry rather than popping it into
            # a conflicted index. This run is the one that finishes the rebase,
            # so it is the one that owes the restore — `restore` is a no-op
            # when there is no auto-stash or the rebase is still unfinished.
            rebase.stash.restore(cwd, mode, trail=trail)

    stashed = rebase.stash.auto_stash(cwd, trail=trail)
    if stashed is None:
        return 1

    core.trail.tdecision(
        trail, "rebase_state", "starting fresh rebase",
        reason="no in-progress rebase detected",
    )
    try:
        return rebase.lifecycle.fresh(
            cwd, ctx, mode, force=force, target_ref=target_ref,
            fork_point=fork_point, verify=verify, snapshot=snapshot,
            trail=trail,
        )
    finally:
        # try/finally rather than a trailing call: the run lock gets this right
        # and the stash did not, so an exception — or the 90-minute kill the
        # skill itself warns about — left the user's uncommitted work sitting
        # on the stack with nothing on the console saying it was there.
        if stashed:
            rebase.stash.restore(cwd, mode, trail=trail)


# ── CLI ─────────────────────────────────────────────────────────────────────


def _select_mode(args) -> tuple[rebase.types.RunMode, str]:
    """Resolve --fix and --no-push into the single mode the run is driven by.

    --fix and --no-push are independent: the first says the AI may resolve
    conflicts, the second says nothing reaches the remote. Collapsing them into
    one flag is what made `--fix --no-push` force-push.
    """
    if args.fix and args.push:
        return rebase.types.RunMode.FIX, "--fix flag set"
    if args.fix:
        return rebase.types.RunMode.FIX_ONLY, "--fix with --no-push"
    if args.push:
        return rebase.types.RunMode.PUSH, "default mode"
    return rebase.types.RunMode.REBASE_ONLY, "--no-push flag set"


def _verify(args, trail: Trail) -> bool:
    """Whether the force-push runs the pre-push hook, recorded when it does not.

    `--no-verify` is the operator's call and never inferred: the trail is the
    only record that a branch reached the remote without its gate.
    """
    if args.no_verify:
        trail.decision("verify", "skipping the pre-push hook",
                       reason="--no-verify flag set")
    return not args.no_verify


def _run(args, ctx: pr.context.ResolvedContext, cwd: str, trail: Trail) -> int:
    if args.push_only:
        target = _resolve(args)
        # cmd_push lands through the publishing gate; without opening it the
        # force-push is held rather than issued — the same path --no-push uses.
        with core.publishing.run(post=True):
            return cmd_push(
                target.cwd, target.ctx, target_ref=target.target_ref,
                verify=_verify(args, trail), trail=trail,
            )

    if args.abort:
        trail.decision("mode", "selected abort", reason="--abort flag set")
        # Abort is the escape hatch for a broken or hung rebase — it should
        # not depend on a network call. The target ref only needs recording
        # in the aborted outcome, so prefer what the run that started the
        # rebase already resolved and fall back to a fresh resolution only
        # when no prior state exists to read.
        target_ref = rebase.types.recorded_target_base(ctx) or (
            rebase.target.resolve_target_ref(cwd, ctx, args.onto, trail=trail)
        )
        return cmd_abort(cwd, ctx, target_ref=target_ref)

    # One read of the PR for the whole run: the base to replay onto, whether it
    # already merged, and whether anyone is reviewing it were three questions
    # and are now one round trip. Harmless when gh cannot answer — an
    # unanswered snapshot refuses nothing and reports nothing.
    #
    # Still read under --onto, which needs no base from GitHub: the other two
    # questions are about the branch rather than the base, and a run that names
    # its own target is no less able to land on a merged PR or to rewrite one
    # somebody is reviewing.
    snapshot = rebase.pr_snapshot.fetch(cwd, ctx)
    target_ref = rebase.target.resolve_target_ref(
        cwd, ctx, args.onto, snapshot=snapshot, trail=trail,
    )

    mode, reason = _select_mode(args)
    trail.decision("mode", f"selected {mode}", reason=reason)
    # The one entry point where force-pushing is the command rather than a
    # side effect of it, so it is the one that opens the gate. `--no-push`
    # leaves it shut, and every push below then drafts its command instead
    # of running it — which is where the resume line comes from.
    #
    # Push is the default, so a bare `pr rebase` opens the gate. `run` scopes
    # that to this invocation: an in-process describe that was not given
    # `--post` must not inherit it.
    with core.publishing.run(post=mode.reaches_remote):
        if args.force:
            trail.decision("preflight", "waiving the already-landed check",
                           reason=f"{rebase.types.REFUSAL_OVERRIDE_FLAG} flag set")
        verify = _verify(args, trail)
        rc = cmd_start(cwd, ctx, mode, force=args.force, target_ref=target_ref,
                       fork_point=args.fork_point or "", verify=verify,
                       snapshot=snapshot, trail=trail)

        if rc == 0 and mode is rebase.types.RunMode.PUSH:
            rc = cmd_push(cwd, ctx, target_ref=target_ref, verify=verify,
                          snapshot=snapshot, trail=trail)
        return rc
