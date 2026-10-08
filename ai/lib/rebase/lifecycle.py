"""Driving a rebase to completion — the step loop and the two ways it ends.

``fresh`` starts one and ``drive_to_completion`` resumes one; both converge on
the same loop, which advances a step at a time until git says the rebase is
over and ``rebase_success`` lands what was replayed. Every exit is either that,
a refusal, or an abort that leaves the branch where it started.
"""

# doc-group: platform

from __future__ import annotations

import dataclasses

import agent.backend
import core.log
import core.publishing
from core.proc import CmdResult
from core.trail import Trail, tdecision, terr, tfail, tinfo, tspan
import git.client
import git.topology
import pr.context
from pr.domains import RebaseStatus

from . import inspect as rebase_inspect
from . import land as rebase_land
from . import lease as rebase_lease
from . import pr_snapshot as rebase_pr_snapshot
from . import refusals
from . import replay_audit
from . import resolve_ai as rebase_resolve
from . import target as rebase_target
from . import types as rebase_types

CONFLICT_FILE_BUDGET = rebase_types.CONFLICT_FILE_BUDGET
CONFLICT_RESOLUTION_BUDGET = rebase_types.CONFLICT_RESOLUTION_BUDGET
ConflictReport = rebase_types.ConflictReport
MAX_REBASE_STEPS = rebase_types.MAX_REBASE_STEPS
RebaseOutcome = rebase_types.RebaseOutcome
ResolutionTally = rebase_types.ResolutionTally
RunMode = rebase_types.RunMode

# Re-exported rather than defined here: `git stash pop` replays the worktree
# onto the rewritten branch and is as able to reuse a cached resolution as any
# rebase step, so the constant belongs where both this module and `stash` can
# reach it. See `rebase.types.RERERE_CONFIG` for what leaving it enabled cost.
RERERE_CONFIG = rebase_types.RERERE_CONFIG

# `core.editor=true` is what keeps an unattended run unattended: git opens the
# editor for a commit whose message it wants confirmed, and `true` exits zero
# without touching the file, so the message is taken as it stands.
#
# It belongs on the fresh `git rebase` too, not just `--continue`: under
# `--autosquash` a `squash!` commit asks for the combined message *during* the
# initial replay, and without this the run halts there with "there was a problem
# with the editor". A `fixup!` never asks, which is why the gap stayed hidden.
UNATTENDED_CONFIG = {"core.editor": git.client.NO_EDITOR}

REBASE_CONFIG = {**RERERE_CONFIG, **UNATTENDED_CONFIG}

# Paired with `UNATTENDED_CONFIG`: the config says which editor to use, and this
# makes sure nothing in the environment outranks it. Both halves are needed —
# see `git.client.unattended_env` for the precedence table and what an inherited
# `GIT_EDITOR` did to an unattended rebase.
#
# Re-exported rather than defined here because the same environment is owed to
# every child that may reach git without this process choosing the argv, and an
# AI agent holding a shell is the other one. Two copies of the variable list
# would be two places for the next variable to be added to only one of.
unattended_env = git.client.unattended_env


def _continue_command(mode: RunMode) -> str:
    """What a refusal tells the operator to run once each listed change is
    confirmed superseded, prefixed with `replay_audit.ALLOW_ENV=1`.

    The run's own command rather than git's: a bare `git rebase --continue`
    would finish the replay without the run that started it, leaving its
    state file describing a stop. Carries `--no-push` when the run that hit
    the refusal was started with it — a resume ignores `--force` and
    `--fork-point` (see `cmd_start`), so those two are never worth repeating
    here, but `--no-push` still governs whether the resume pushes.
    """
    if mode is RunMode.FIX_ONLY:
        return "pr rebase --fix --no-push"
    return "pr rebase --fix"


def rebase_continue(cwd: str) -> CmdResult:
    """Continue the rebase without stopping for a commit message."""
    return git.client.run(
        "rebase", "--continue", cwd=cwd, config=REBASE_CONFIG,
        env=unattended_env(),
    )


def drive_to_completion(
    cwd: str, ctx: pr.context.ResolvedContext, mode: RunMode, *,
    target_ref: str, force: bool = False,
    tally: ResolutionTally | None = None,
    lease: rebase_lease.PushLease | None = None,
    verify: bool = True,
    snapshot: rebase_pr_snapshot.PRSnapshot | None = None,
    trail: Trail | None = None,
) -> int:
    """Drive an in-progress rebase to completion, handling all intermediate states.

    Loops until the rebase finishes, handling conflicts (via AI when --fix),
    empty commits (via --skip), and stuck states (abort on unexpected failure).

    ``tally`` carries in what the caller's own git call already observed — the
    fresh rebase resolves from the rerere cache before this loop starts, and a
    tally created here would not have seen it.

    ``lease`` is what the push will be made under. ``fresh`` resolves one from
    the tip it read before its fetch and passes it; a run resuming a rebase
    started by an earlier process has no such reading to inherit, so one is
    recovered here from the remote-tracking ref instead — the original
    process's fetch is what last moved it, and nothing has fetched since, so
    it still holds that same value. The rebase state directory's ``orig-head``
    is the tempting reading and the wrong one: it is the *local* branch tip
    when the rebase began, so unpushed local commits leave it ahead of the
    remote, and naming it in the lease fails the eventual push with ``stale
    info`` even though the remote never moved — see
    ``rebase.lease.remembered_tip``.
    """
    if lease is None:
        lease = rebase_lease.resolve(
            cwd, ctx.branch, rebase_lease.remembered_tip(cwd, ctx.branch),
        )
    with tspan(trail, "drive_to_completion"):
        return _drive_loop(
            cwd, ctx, mode, target_ref=target_ref, force=force, tally=tally,
            lease=lease, verify=verify, snapshot=snapshot, trail=trail,
        )


def _drive_loop(
    cwd: str, ctx: pr.context.ResolvedContext, mode: RunMode, *,
    target_ref: str, force: bool = False,
    tally: ResolutionTally | None = None,
    lease: rebase_lease.PushLease | None = None,
    verify: bool = True,
    snapshot: rebase_pr_snapshot.PRSnapshot | None = None,
    trail: Trail | None = None,
) -> int:
    tally = tally if tally is not None else ResolutionTally()

    for _ in range(MAX_REBASE_STEPS):
        if not rebase_inspect.rebase_in_progress(cwd):
            return rebase_success(
                cwd, ctx, mode, tally, target_ref=target_ref,
                lease=lease, verify=verify, snapshot=snapshot, trail=trail,
            )

        rc, conflict_found = _drive_one_step(
            cwd, ctx, mode, tally, target_ref=target_ref, force=force, trail=trail,
        )
        if rc is not None:
            return rc
        if conflict_found:
            tally.commits += 1

    terr(trail, "drive_to_completion",
         f"rebase did not complete after {MAX_REBASE_STEPS} steps",
         data={"files_resolved": tally.files, "commits": tally.commits})
    core.log.error(f"Rebase did not complete after {MAX_REBASE_STEPS} steps — aborting.")
    # Recorded before the abort, which is what destroys the evidence. A runaway
    # loop is the one place an abort is still right — something is cycling and
    # leaving it half-replayed helps nobody — but the run must not also be
    # silent about the work it just threw away.
    RebaseOutcome(
        status=RebaseStatus.ABORTED,
        conflicts_resolved=len(tally.files),
        files_resolved=tally.files,
        files_stale=tally.stale,
        target_base=target_ref,
    ).save(ctx)
    git.client.run("rebase", "--abort", cwd=cwd)
    return 1


def _drive_one_step(
    cwd: str, ctx: pr.context.ResolvedContext, mode: RunMode,
    tally: ResolutionTally, *, target_ref: str, force: bool = False,
    trail: Trail | None = None,
) -> tuple[int | None, bool]:
    conflicts = rebase_inspect.detect_conflicts(cwd)
    if not conflicts:
        return step_advance(
            cwd, ctx, tally, mode=mode, target_ref=target_ref, trail=trail,
        ), False

    rc = step_conflicts(
        cwd, ctx, mode, conflicts, tally, target_ref=target_ref, force=force,
        trail=trail,
    )
    if rc is not None:
        return rc, False
    return None, True


def _stopped_outcome(tally: ResolutionTally, *, target_ref: str) -> RebaseOutcome:
    """The `conflicts` outcome a stopped run records, one-sided regions included.

    The regions are saved at every stop because the resume is a new process:
    `resumed_tally` reads them back, so a region resolved before the stop
    still holds the eventual push.
    """
    return RebaseOutcome(
        status=RebaseStatus.CONFLICTS,
        conflicts_resolved=len(tally.files),
        files_resolved=tally.files,
        files_stale=tally.stale,
        files_one_sided=tally.one_sided,
        one_sided_regions=tally.one_sided_regions,
        pre_rebase_head=tally.pre_rebase_head,
        target_base=target_ref,
    )


def resumed_tally(cwd: str, ctx: pr.context.ResolvedContext) -> ResolutionTally:
    """The tally a resumed rebase continues from.

    The tip it started at comes from git's own `orig-head`; the one-sided
    regions from the stop an earlier process recorded — only when that record
    is a stop, so a finished rebase's regions never leak into the next one.
    """
    prior = rebase_types.load_or_init(ctx).rebase
    tally = ResolutionTally(pre_rebase_head=rebase_inspect.rebase_orig_head(cwd))
    if prior.status == RebaseStatus.CONFLICTS.value:
        tally.one_sided = list(prior.files_one_sided)
        tally.one_sided_regions = list(prior.one_sided_regions)
    return tally


def _report_conflicts_and_stop(
    cwd: str, ctx: pr.context.ResolvedContext, *, target_ref: str,
    tally: ResolutionTally | None = None,
) -> int:
    """Persist status=conflicts, emit the report, and return the conflicts exit code.

    *tally* is what the run resolved before it stopped, and is recorded rather
    than dropped. A stop is not an abort: the rebase stays in the worktree with
    everything already resolved staged in it, so the state file has to say what
    that is — a resume reads it, and a `pr status` reading a bare
    ``status=conflicts`` would report a run that did nothing.
    """
    tally = tally if tally is not None else ResolutionTally()
    _stopped_outcome(tally, target_ref=target_ref).save(ctx)
    ConflictReport.from_repo(cwd).emit()
    return rebase_types.CONFLICTS_EXIT


def _over_budget(
    tally: ResolutionTally, conflicts: list[str],
) -> refusals.BudgetBreach | None:
    """Whichever conflict budget this step would cross, or None.

    Two counts, measuring different things — see `pr.domains` for why one is
    not enough. Spread is checked first because it is the older signal and the
    one whose refusal text an operator is likelier to recognise; either alone
    stops the rebase.
    """
    spread = len(set(tally.files) | set(conflicts))
    if spread > CONFLICT_FILE_BUDGET:
        return refusals.BudgetBreach(
            signal=rebase_types.RefusalSignal.CONFLICTS_OVER_BUDGET,
            detail=f"conflicts in {spread} files, over the "
                   f"{CONFLICT_FILE_BUDGET}-file budget",
        )

    depth = tally.depth + len(conflicts)
    if depth > CONFLICT_RESOLUTION_BUDGET:
        return refusals.BudgetBreach(
            signal=rebase_types.RefusalSignal.RESOLUTIONS_OVER_BUDGET,
            detail=f"{depth} conflict resolutions across {spread} file(s), "
                   f"over the {CONFLICT_RESOLUTION_BUDGET}-resolution budget",
        )
    return None


def _record_failed(
    ctx: pr.context.ResolvedContext, tally: ResolutionTally, *, target_ref: str,
) -> None:
    """Persist what a failed run resolved before it stopped.

    Every path that gives up owes this. A run that ends without writing state
    leaves the next reader — `pr status`, `cmd_push`, the operator — with the
    previous run's summary and no sign that anything happened since.
    """
    _stopped_outcome(tally, target_ref=target_ref).save(ctx)


def _restore_conflicts(cwd: str, paths: list[str]) -> None:
    """Bring the conflict back into each of *paths*, for a refused resolution.

    `git checkout -m` redoes the merge between HEAD and the commit under
    replay regardless of what the index currently holds, which is what lets it
    recreate a conflict this run had already resolved and staged. It is a
    no-op, though, for a path the two sides never actually conflicted over —
    `audit_replay` audits every path the replayed commit touched, not only the
    ones this step had conflicts in, so a cleanly auto-merged file can still be
    named here (a questionable three-way merge `survival.audit` flagged
    `blocking`). There is nothing in the index beforehand that tells the two
    cases apart, so the check happens after: a path still clean once the
    checkout has run had nothing to restore.
    """
    r = git.client.run("checkout", "-m", "--", *paths, cwd=cwd)
    if not r.ok:
        core.log.warn(
            f"Could not restore the conflict in {', '.join(paths)}: "
            f"{r.stderr.strip()}"
        )
        return
    restored = set(rebase_inspect.detect_conflicts(cwd))
    missing = [p for p in paths if p not in restored]
    if missing:
        core.log.warn(
            f"No conflict to restore in {', '.join(missing)} — git merged "
            "them without one; revert the discarded change(s) by hand."
        )


def _halt_if_discarding(
    cwd: str, ctx: pr.context.ResolvedContext, tally: ResolutionTally, *,
    target_ref: str, restore: bool, mode: RunMode = RunMode.FIX,
    trail: Trail | None = None,
) -> int | None:
    """Stop before a `--continue` that would commit a resolution discarding changes.

    The global prepare-commit-msg hook makes the same judgement and refuses
    the commit, but a refused `--continue` leaves the rebase stopped on the
    same commit with nothing unmerged — which this loop reads as a step to
    advance, and would `--continue` into the same refusal until the step cap
    aborted the run. Asking first turns that into one clean stop, with the
    files named, on machines with the hook and without it alike.

    *restore* brings the conflict back into each refused file
    (`git checkout -m`), for a resolution this run made: a resolution that
    discards changes is worth less than the markers it replaced. A resolution
    staged by hand before the run is left exactly as it is — it is somebody's
    work, and the refusal says how to redo it.

    None when the continue may go ahead.
    """
    # Fail open, exactly like the hook entry point this mirrors
    # (`replay_audit.main`): a bug in a ~400-line difflib-based audit must
    # never cost somebody a step's worth of already-staged resolutions, let
    # alone crash the whole run out from under them.
    try:
        audit = replay_audit.audit_replay(cwd)
    except Exception as exc:
        core.log.warn(f"replay audit could not run: {exc}")
        return None
    if audit is None:
        return None
    if audit.flagged:
        core.log.warn(replay_audit.render_advisory(audit))
        tally.note_one_sided([f.path for f in audit.flagged],
                             replay_audit.advisory_block(audit))
    if audit.ok:
        return None
    refused = [f.path for f in audit.refused]
    tdecision(
        trail, "replay_audit", f"resolution discards changes in {len(refused)} file(s)",
        reason="a change git had merged cleanly is missing from the resolution",
        data={"commit": audit.commit, "files": refused,
              "losses": {f.path: [loss.describe() for loss in f.blocking]
                         for f in audit.refused},
              "override": replay_audit.override_requested()},
    )
    core.log.error(replay_audit.render_refusal(audit, _continue_command(mode)))
    if replay_audit.override_requested():
        core.log.warn(f"{replay_audit.ALLOW_ENV}=1 — continuing anyway.")
        return None
    if restore:
        _restore_conflicts(cwd, refused)
    _stopped_outcome(tally, target_ref=target_ref).save(ctx)
    report = ConflictReport.from_repo(cwd)
    dataclasses.replace(report, files=sorted(set(report.files) | set(refused))).emit()
    return rebase_types.CONFLICTS_EXIT


def _halt_unresolved(
    cwd: str, ctx: pr.context.ResolvedContext, unresolved: list[str],
    tally: ResolutionTally, *, target_ref: str, trail: Trail | None = None,
) -> int:
    """Stop on files the AI could not resolve — without aborting the rebase.

    The abort this replaces is what made one unparseable answer cost a whole
    run: nine resolved files and a replayed commit were thrown away because a
    tenth file's resolution would not parse. Nothing about that failure makes
    the other nine wrong, and nothing about it makes the rebase unrecoverable.

    So the rebase is left exactly where it is. The resolved files are staged,
    the unresolved ones still carry their markers, and the exit code is the
    same 3 that `--no-fix` returns for conflicts a human must look at — which
    is what this now is. `cmd_start` resumes from here on the next run, and the
    auto-stash is held rather than popped into the conflicted index.
    """
    terr(trail, "resolve_conflicts",
         f"{len(unresolved)} file(s) could not be resolved",
         data={"unresolved": unresolved, "resolved": tally.files})
    core.log.error(f"Could not resolve {len(unresolved)} file(s): "
              f"{', '.join(unresolved)}")
    if tally.files:
        core.log.ok(f"Kept {len(tally.files)} resolution(s) already made — "
               "the rebase is paused, not aborted.")
    core.log.dim("Resolve the listed files by hand and re-run `pr rebase --fix` to "
            "continue, or `pr rebase --abort` to throw the whole replay away.")
    return _report_conflicts_and_stop(
        cwd, ctx, target_ref=target_ref, tally=tally,
    )


def step_conflicts(
    cwd: str, ctx: pr.context.ResolvedContext, mode: RunMode,
    conflicts: list[str], tally: ResolutionTally, *,
    target_ref: str, force: bool = False, trail: Trail | None = None,
) -> int | None:
    """Handle a rebase step with conflicts. Returns exit code to stop, or None to continue."""
    if not mode.resolves_conflicts:
        return _report_conflicts_and_stop(
            cwd, ctx, target_ref=target_ref, tally=tally,
        )

    if not agent.backend.is_available():
        terr(trail, "resolve_conflicts", "AI backend unavailable")
        core.log.error("Cannot resolve conflicts — AI backend unavailable.")
        return _report_conflicts_and_stop(
            cwd, ctx, target_ref=target_ref, tally=tally,
        )

    if not force:
        over = _over_budget(tally, conflicts)
        if over is not None:
            return refusals.refuse_over_budget(
                cwd, ctx, over, target_ref=target_ref, trail=trail,
            )

    sha, subject = rebase_inspect.rebase_head_info(cwd)
    remaining = rebase_inspect.remaining_rebase_commits(cwd)

    core.log.info(
        f"Resolving {len(conflicts)} conflict(s) in {sha} — {subject} "
        f"({remaining} remaining)..."
    )
    tdecision(
        trail, "step", f"resolving {len(conflicts)} conflict(s)",
        reason=f"commit {sha} has unmerged files",
        data={"commit": sha, "files": conflicts, "remaining": remaining},
    )

    resolved = rebase_resolve.resolve_file_conflicts(
        conflicts, cwd, sha, subject, target_ref=target_ref, trail=trail,
    )
    tally.absorb(resolved)
    if not resolved.ok:
        return _halt_unresolved(
            cwd, ctx, resolved.failed, tally, target_ref=target_ref, trail=trail,
        )

    # Stage any remaining unstaged changes from post-resolution fixups (e.g. go mod tidy)
    if git.client.lines("diff", "--name-only", cwd=cwd):
        git.client.run("add", "-u", cwd=cwd)

    rc = _halt_if_discarding(
        cwd, ctx, tally, target_ref=target_ref, restore=True, mode=mode, trail=trail,
    )
    if rc is not None:
        return rc

    r = rebase_continue(cwd)
    if r.ok:
        return None

    if r.stderr.strip():
        core.log.dim(r.stderr.strip())

    # rebase --continue returns non-zero when the current commit is applied
    # but the *next* commit has conflicts — that's normal, not a failure.
    if rebase_inspect.rebase_in_progress(cwd):
        return None

    # No abort here, and none below in `step_advance`. Both are past the check
    # that says the rebase is no longer in progress, so `git rebase --abort`
    # had nothing to abort and failed silently — it only ever made the log
    # claim something the run had not done. What was missing instead is this:
    # a state file saying what the run resolved before git stopped taking it.
    tfail(trail, "step_conflicts", "rebase --continue failed after resolution",
           output=r.combined_output,
           data={"files_resolved": tally.files})
    core.log.error("rebase --continue failed after conflict resolution.")
    _record_failed(ctx, tally, target_ref=target_ref)
    return 1


def step_advance(
    cwd: str, ctx: pr.context.ResolvedContext,
    tally: ResolutionTally | None = None, *,
    target_ref: str, mode: RunMode = RunMode.FIX, trail: Trail | None = None,
) -> int | None:
    """Advance rebase when there are no conflicts. Returns exit code to stop, or None to continue."""
    tally = tally if tally is not None else ResolutionTally()
    # A step with nothing unmerged can still be concluding a conflict: one
    # resolved by hand, and staged, before this run picked the rebase up.
    # Asked before the empty-patch test, not after it: a whole-file
    # `checkout --ours` leaves the index equal to HEAD, which that test reads
    # as "already applied upstream" and skips — dropping the commit with a
    # log line saying nothing was lost. A commit genuinely upstream passes the
    # audit, because HEAD already holds its changes.
    rc = _halt_if_discarding(
        cwd, ctx, tally, target_ref=target_ref, restore=False, mode=mode, trail=trail,
    )
    if rc is not None:
        return rc

    if rebase_inspect.is_empty_patch(cwd):
        sha, subject = rebase_inspect.rebase_head_info(cwd)
        core.log.info(f"Skipping empty commit {sha} — {subject}")
        tdecision(
            trail, "step", f"skipping empty commit {sha}",
            reason="patch already applied upstream",
            # `remaining` rides on both `step` events or on neither. The
            # conflict step carried it and this one did not, so a run whose
            # commits were mostly empty reported no progress at all to anyone
            # reading the trail for it.
            data={"commit": sha, "subject": subject,
                  "remaining": rebase_inspect.remaining_rebase_commits(cwd)},
        )
        # Carries the same config as the other two: --skip drops the current
        # commit and goes straight on to apply the next, so the merges it runs
        # are as able to replay a recorded resolution as any other step's.
        r = git.client.run(
            "rebase", "--skip", cwd=cwd, config=REBASE_CONFIG,
            env=unattended_env(),
        )
        if not r.ok:
            core.log.error(f"git rebase --skip failed (exit {r.returncode})")
            return 1
        return None

    r = rebase_continue(cwd)
    if r.ok:
        return None

    if r.stderr.strip():
        core.log.dim(r.stderr.strip())

    # rebase --continue can return non-zero when the next commit has conflicts
    if rebase_inspect.rebase_in_progress(cwd):
        return None

    tfail(trail, "step_advance", "rebase --continue failed without conflicts",
           output=r.combined_output,
           data={"exit_code": r.returncode, "files_resolved": tally.files})
    core.log.error("rebase --continue failed without conflicts.")
    _record_failed(ctx, tally, target_ref=target_ref)
    return 1


def _resolved_fork_point(cwd: str, fork_point: str) -> str:
    """*fork_point* as a sha, or "" if it is not a commit reachable from HEAD.

    Checked before the replay rather than left to git, because git's own
    failure for a bad ``<upstream>`` is indistinguishable from a rebase that
    went wrong — and because a ref that resolves but is *not* an ancestor of
    HEAD silently replays a different set of commits than the operator asked
    for, which git reports as success.
    """
    sha = git.client.out("rev-parse", "--verify", f"{fork_point}^{{commit}}",
                         cwd=cwd)
    if not sha:
        return ""
    if not git.client.ok("merge-base", "--is-ancestor", sha, "HEAD", cwd=cwd):
        return ""
    return sha


def fresh(
    cwd: str, ctx: pr.context.ResolvedContext, mode: RunMode,
    force: bool = False, *, target_ref: str, fork_point: str = "",
    verify: bool = True,
    snapshot: rebase_pr_snapshot.PRSnapshot | None = None,
    trail: Trail | None = None,
) -> int:
    """Start a fresh rebase onto the target ref.

    ``fork_point`` is git's ``<upstream>`` argument, and naming it makes the
    replay ``git rebase --onto <target_ref> <fork_point>`` — only the commits
    after it. Without it, ``<newbase>`` and ``<upstream>`` are the same ref and
    the whole branch is replayed, which is right for every ordinary rebase and
    wrong for the one `refusals.partially_landed_check` catches: a branch whose
    prefix already landed. That refusal names the fork point to pass here, and
    before this parameter existed the tool could not express the fix it was
    recommending.
    """
    default = git.topology.default_branch(cwd)
    if ctx.branch == default:
        terr(trail, "preflight", f"on protected branch {ctx.branch}")
        core.log.error("Cannot rebase — currently on protected branch.")
        return 1

    # Before the fetch, which is the whole point: this is the remote tip as we
    # last saw it, and the fetch below replaces that reading with whatever the
    # remote holds now. Leasing against the post-fetch value is what lets a
    # colleague's push be overwritten by the replay — see `rebase.lease`.
    remembered = rebase_lease.remembered_tip(cwd, ctx.branch)

    core.log.info("Fetching origin...")
    # --prune is defense in depth behind the already-landed preflight, not a
    # substitute for it: dropping the remote-tracking ref of a branch deleted
    # on merge is what tells the lease the branch is gone, rather than leaving
    # a stale tracking ref to name in an expect that would recreate it.
    fetch = git.client.run("fetch", "--prune", "origin", cwd=cwd)
    if not fetch.ok:
        core.log.warn(f"Fetch failed — rebasing against potentially stale {target_ref}.")
        if fetch.stderr.strip():
            core.log.dim(fetch.stderr.strip())

    # Resolved here, between the fetch and the replay: the prune above is what
    # settles whether the remote still has the branch, which is the question
    # that picks between naming a commit and asserting there is none. Held as a
    # value from here on so nothing downstream re-reads a ref the rebase has
    # since moved.
    lease = rebase_lease.resolve(cwd, ctx.branch, remembered)

    # Before the checkout, deliberately: --prune has just dropped
    # origin/<branch> for a branch whose PR merged and whose remote was
    # deleted, and the checkout below starts from exactly that ref. Asking the
    # tracker first is what turns "Cannot checkout feat/x" into the refusal
    # this preflight exists to give.
    landed = None if force else refusals.tracker_landed_check(cwd, ctx, snapshot)
    if landed is not None:
        return refusals.refuse(ctx, landed, target_ref=target_ref, trail=trail)

    if ctx.current_branch != ctx.branch:
        display = ctx.current_branch or "detached HEAD"
        # Defense in depth: pr.context creates a worktree for a branch that has
        # none, from a bare repo or from the default branch's own worktree, so
        # this is reached only when `wt` could not make one. Checking out here
        # would leave a feature branch sitting in main/, where the next tool
        # that syncs main/ to origin/<default> hard-resets the branch out from
        # under it.
        if ctx.current_branch == default:
            terr(trail, "preflight", f"refusing to check out {ctx.branch} into the {display} worktree")
            core.log.error(f"Refusing to check out {ctx.branch} into the {display} worktree.")
            core.log.dim(f"Run 'wt switch {ctx.branch}' and retry with --repo-dir on that worktree.")
            return 1
        core.log.info(f"Worktree on {display}, checking out {ctx.branch}...")
        rc = rebase_target.checkout_target_branch(cwd, ctx, trail=trail)
        if rc != 0:
            return rc
        tdecision(
            trail, "branch_checkout",
            f"checked out {ctx.branch} (was {display})",
            reason="worktree was on wrong branch or detached HEAD",
        )

    # After the checkout, so HEAD is the branch these signals are asked about,
    # and after the fetch, so the target ref they compare against is current.
    # Unrelated history first: the landed signals compare HEAD against a ref an
    # unrelated branch has no relationship to, so they answer nothing there.
    unrelated = None if force else refusals.unrelated_history_check(cwd, ctx, target_ref=target_ref)
    if unrelated is not None:
        return refusals.refuse(ctx, unrelated, target_ref=target_ref, trail=trail)

    landed = None if force else refusals.git_landed_check(cwd, ctx, target_ref=target_ref)
    if landed is not None:
        return refusals.refuse(ctx, landed, target_ref=target_ref, trail=trail)

    # After the all-or-nothing landed signals, which are cheaper and whose
    # finding is stronger: a branch entirely upstream is not partially
    # upstream, and this walk costs a git call per commit in the prefix.
    # Skipped when the operator has already named a fork point, since the
    # refusal's only purpose is to ask for one.
    if not force and not fork_point:
        partial = refusals.partially_landed_check(cwd, ctx, target_ref=target_ref)
        if partial is not None:
            return refusals.refuse(ctx, partial, target_ref=target_ref, trail=trail)

    replay_from = ""
    if fork_point:
        replay_from = _resolved_fork_point(cwd, fork_point)
        if not replay_from:
            terr(trail, "preflight", f"unusable fork point {fork_point}")
            core.log.error(f"Cannot replay from {fork_point} — it is not a commit "
                      f"on {ctx.branch}.")
            core.log.dim("Pass a commit the branch descends from; the "
                    "partially-landed refusal names the one to use.")
            return 1
        tdecision(
            trail, "fork_point", f"replaying only the commits after {replay_from}",
            reason=f"{refusals.FORK_POINT_FLAG} {fork_point}",
            data={"fork_point": replay_from, "requested": fork_point},
        )
        core.log.info(f"Replaying only the commits after {replay_from[:8]}.")

    core.log.info(f"Rebasing onto {target_ref}...")
    # --autosquash unconditionally: it acts only on commits whose subject starts
    # with `fixup!` or `squash!`, which is a marker the author wrote to say
    # "fold this into that one". Honouring it replays fewer commits and drops a
    # conflict-prone one entirely; ignoring it, as this did, force-pushed the
    # marker commits back and left the branch to be cleaned up by hand. The
    # `fixup` alias in git/gitconfig.shared produces exactly these.
    #
    # Non-interactive since git 2.22 — no todo editor opens, so nothing here
    # waits on one.
    #
    # `--onto <target> <fork>` rather than a bare `<target>` when a fork point
    # was named: git collapses <newbase> and <upstream> onto one ref only in
    # the two-argument form, and keeping that form was what made the
    # partially-landed remedy unreachable through this tool.
    replay = (
        ["--onto", target_ref, replay_from] if replay_from else [target_ref]
    )
    # Threaded into the loop below rather than created there: the loop's first
    # action may be to resolve a conflict this call left behind, and a tally
    # made inside it would not be the one the whole run accumulates into.
    # Made before the replay so it holds the tip the replay starts from.
    tally = ResolutionTally(pre_rebase_head=git.client.head_sha(cwd=cwd))
    r = git.client.run(
        "rebase", "--autosquash", *replay, cwd=cwd, config=REBASE_CONFIG,
        env=unattended_env(),
    )

    if r.ok and not rebase_inspect.rebase_in_progress(cwd):
        return rebase_success(
            cwd, ctx, mode, tally, target_ref=target_ref,
            lease=lease, verify=verify, snapshot=snapshot, trail=trail,
        )

    if not rebase_inspect.rebase_in_progress(cwd):
        tfail(trail, "rebase", f"git rebase failed (exit {r.returncode})", output=r.combined_output)
        core.log.error(f"git rebase failed (exit {r.returncode})")
        if r.stderr.strip():
            core.log.dim(r.stderr.strip())
        return 1

    return drive_to_completion(
        cwd, ctx, mode, target_ref=target_ref, force=force, tally=tally,
        lease=lease, verify=verify, snapshot=snapshot, trail=trail,
    )


def rebase_success(
    cwd: str, ctx: pr.context.ResolvedContext, mode: RunMode,
    tally: ResolutionTally | None = None, *, target_ref: str,
    lease: rebase_lease.PushLease | None = None,
    verify: bool = True,
    snapshot: rebase_pr_snapshot.PRSnapshot | None = None,
    trail: Trail | None = None,
) -> int:
    """Handle rebase completion — update state and optionally force-push."""
    tally = tally or ResolutionTally()

    # Counted before the push: its recovery paths add regeneration and
    # check-fix commits, which were never replayed from the old branch.
    replayed = git.client.commits_ahead(cwd, target_ref=target_ref)

    # RunMode.PUSH lands from main() via cmd_push; every other mode lands here.
    # A mode that does not reach the remote still lands, because the publishing
    # gate — shut for those modes — is what stops it, and a held landing is what
    # carries the force-push command back as data.
    lands_here = mode is not RunMode.PUSH
    one_sided = sorted(set(tally.one_sided))
    # Held, not refused: the replay is fine to keep, but a region resolved to
    # one side may have dropped the other side's change, so nothing reaches
    # the remote until somebody has looked — in a batch, in `pr ci --fix`, and
    # by hand alike. Under RunMode.PUSH the push is cmd_push's, after this
    # returns, and `pr rebase` skips it while the gate is held. The hold lasts
    # for the rest of this process, by design.
    if one_sided and mode.reaches_remote:
        core.publishing.hold(f"the rebase resolved {len(one_sided)} file(s) to one side")

    label = "Rebase complete" if not tally.commits else (
        f"Rebase complete — resolved {len(tally.files)} file(s) "
        f"across {tally.commits} commit(s)"
    )

    if lands_here and lease is None:
        # Nothing safe to name: the remote has the branch and this run never
        # read the tip it was at. The two fallbacks are a bare lease, which the
        # fetch would satisfy, and an empty expect, which git rejects against a
        # ref that exists. Stopping leaves the replay in the worktree, which is
        # recoverable; a wrong lease would not be.
        terr(trail, "force_push", "no lease could be named for the push",
             data={"branch": ctx.branch})
        core.log.error("Refusing to force-push — cannot tell what the remote was at.")
        core.log.dim("The rebase is complete in the worktree. Push it by hand after "
                "checking what origin holds.")
        RebaseOutcome(
            commits_replayed=replayed,
            conflicts_resolved=len(tally.files),
            files_resolved=tally.files,
            files_stale=tally.stale,
            files_one_sided=tally.one_sided,
            one_sided_regions=tally.one_sided_regions,
            pre_rebase_head=tally.pre_rebase_head,
            force_pushed=False,
            target_base=target_ref,
        ).save(ctx)
        return 1

    landed = None
    if lands_here:
        # Announced only when the push will actually happen; a held run says the
        # same thing once at the end, through the label and the resume line.
        if mode.reaches_remote and not one_sided:
            rebase_pr_snapshot.name_the_open_pr(snapshot, trail=trail)
            core.log.info(f"{label} — force-pushing...")
        # Deduplicated: this is the candidate set the pre-push repair matches a
        # failing hook's output against, and a file conflicting in several
        # replayed commits is listed once per commit in the tally.
        repairable = list(dict.fromkeys(tally.files))
        landed = rebase_land.land_rebased(
            cwd, resolved_files=repairable or None, args=lease.args,
            verify=verify, trail=trail,
        )
        if landed.ok:
            tinfo(trail, "force_push", "force-pushed to remote", data={"sha": landed.sha})
        elif not landed.held:
            terr(trail, "force_push", "force-push failed",
                  data={"status": str(landed.status), "resume": landed.resume})

    outcome = RebaseOutcome(
        commits_replayed=replayed,
        conflicts_resolved=len(tally.files),
        files_resolved=tally.files,
        files_stale=tally.stale,
        files_one_sided=tally.one_sided,
        one_sided_regions=tally.one_sided_regions,
        pre_rebase_head=tally.pre_rebase_head,
        # None rather than False for a run that never tried: a held landing did
        # exactly what --no-push asked for, and recording it as a failed push
        # would be the summary's own invention.
        force_pushed=None if landed is None or landed.held else landed.ok,
        # Saved even on a held run — especially then: `--no-push` is exactly the
        # case where the push happens in a later process that can no longer read
        # this value for itself.
        lease_expect=lease.expect if lease is not None else "",
        target_base=target_ref,
    )
    outcome.save(ctx)
    outcome.emit()

    if landed is not None and not landed.held:
        if landed.ok:
            core.log.ok("Force-pushed.")
            return 0
        core.log.error("Force-push failed.")
        return 1

    if one_sided:
        core.log.warn(f"Resolved to one side: {', '.join(one_sided)} — push held. "
                      "Review the regions above, then publish with `pr rebase --push-only`.")

    # The label holds in every mode that reaches here — the rebase finished.
    # Under FIX_ONLY the AI's work is the whole point of the run and the user is
    # about to push it by hand, so name it rather than reporting a bare "clean".
    core.log.ok(label)
    # A one-sided hold already named `--push-only` above. "Re-run without
    # --no-push" would start a fresh rebase with no one-sided record to
    # replay, so it would force-push straight past the hold.
    if landed is not None and not one_sided:
        core.log.ok(f"Run `{landed.resume}` to push, or re-run without --no-push.")
    return 0


