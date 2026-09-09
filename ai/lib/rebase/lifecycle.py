"""Driving a rebase to completion — the step loop and the two ways it ends.

``fresh`` starts one and ``drive_to_completion`` resumes one; both converge on
the same loop, which advances a step at a time until git says the rebase is
over and ``rebase_success`` lands what was replayed. Every exit is either that,
a refusal, or an abort that leaves the branch where it started.
"""

# doc-group: platform

from __future__ import annotations

from agent import backend as ai_backend
from core import log
from core.proc import CmdResult
from core.trail import Trail, tdecision, terr, tfail, tinfo, tspan
from git import client as git_client
from git import topology as git_topology
from pr import context as pr_context
from pr.domains import RebaseStatus

from . import inspect as rebase_inspect
from . import land as rebase_land
from . import refusals
from . import resolve_ai as rebase_resolve
from . import target as rebase_target
from . import types as rebase_types

CONFLICT_FILE_BUDGET = rebase_types.CONFLICT_FILE_BUDGET
ConflictReport = rebase_types.ConflictReport
MAX_REBASE_STEPS = rebase_types.MAX_REBASE_STEPS
RebaseOutcome = rebase_types.RebaseOutcome
ResolutionTally = rebase_types.ResolutionTally
RunMode = rebase_types.RunMode


def rebase_continue(cwd: str) -> CmdResult:
    """Continue the rebase without stopping for a commit message.

    `core.editor=true` is what keeps an unattended run unattended: git opens the
    editor for a commit whose message it wants confirmed, and `true` exits zero
    without touching the file, so the message is taken as it stands.
    """
    return git_client.run(
        "rebase", "--continue", cwd=cwd, config={"core.editor": "true"},
    )


def drive_to_completion(
    cwd: str, ctx: pr_context.ResolvedContext, mode: RunMode, *,
    target_ref: str, force: bool = False,
    trail: Trail | None = None,
    on_check_failure: rebase_land.CheckFailureFix | None = None,
) -> int:
    """Drive an in-progress rebase to completion, handling all intermediate states.

    Loops until the rebase finishes, handling conflicts (via AI when --fix),
    empty commits (via --skip), and stuck states (abort on unexpected failure).
    """
    with tspan(trail, "drive_to_completion"):
        return _drive_loop(
            cwd, ctx, mode, target_ref=target_ref, force=force, trail=trail,
            on_check_failure=on_check_failure,
        )


def _drive_loop(
    cwd: str, ctx: pr_context.ResolvedContext, mode: RunMode, *,
    target_ref: str, force: bool = False,
    trail: Trail | None = None,
    on_check_failure: rebase_land.CheckFailureFix | None = None,
) -> int:
    tally = ResolutionTally()

    for _ in range(MAX_REBASE_STEPS):
        if not rebase_inspect.rebase_in_progress(cwd):
            return rebase_success(
                cwd, ctx, mode, tally, target_ref=target_ref, trail=trail,
                on_check_failure=on_check_failure,
            )

        rc, conflict_found = _drive_one_step(
            cwd, ctx, mode, tally, target_ref=target_ref, force=force, trail=trail,
        )
        if rc is not None:
            return rc
        if conflict_found:
            tally.commits += 1

    terr(trail, "drive_to_completion", f"rebase did not complete after {MAX_REBASE_STEPS} steps")
    log.error(f"Rebase did not complete after {MAX_REBASE_STEPS} steps — aborting.")
    git_client.run("rebase", "--abort", cwd=cwd)
    return 1


def _drive_one_step(
    cwd: str, ctx: pr_context.ResolvedContext, mode: RunMode,
    tally: ResolutionTally, *, target_ref: str, force: bool = False,
    trail: Trail | None = None,
) -> tuple[int | None, bool]:
    conflicts = rebase_inspect.detect_conflicts(cwd)
    if not conflicts:
        return step_advance(cwd, trail=trail), False

    rc = step_conflicts(
        cwd, ctx, mode, conflicts, tally, target_ref=target_ref, force=force,
        trail=trail,
    )
    if rc is not None:
        return rc, False
    return None, True


def _report_conflicts_and_stop(
    cwd: str, ctx: pr_context.ResolvedContext, *, target_ref: str,
) -> int:
    """Persist status=conflicts, emit the report, and return the conflicts exit code."""
    RebaseOutcome(status=RebaseStatus.CONFLICTS, target_base=target_ref).save(ctx)
    ConflictReport.from_repo(cwd).emit()
    return 3


def step_conflicts(
    cwd: str, ctx: pr_context.ResolvedContext, mode: RunMode,
    conflicts: list[str], tally: ResolutionTally, *,
    target_ref: str, force: bool = False, trail: Trail | None = None,
) -> int | None:
    """Handle a rebase step with conflicts. Returns exit code to stop, or None to continue."""
    if not mode.resolves_conflicts:
        return _report_conflicts_and_stop(cwd, ctx, target_ref=target_ref)

    if not ai_backend.is_available():
        terr(trail, "resolve_conflicts", "AI backend unavailable")
        log.error("Cannot resolve conflicts — AI backend unavailable.")
        return _report_conflicts_and_stop(cwd, ctx, target_ref=target_ref)

    spread = len(set(tally.files) | set(conflicts))
    if not force and spread > CONFLICT_FILE_BUDGET:
        return refusals.refuse_over_budget(
            cwd, ctx, spread, target_ref=target_ref, trail=trail,
        )

    sha, subject = rebase_inspect.rebase_head_info(cwd)
    remaining = rebase_inspect.remaining_rebase_commits(cwd)

    log.info(
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
    if resolved is None:
        terr(trail, "resolve_conflicts", "AI resolution failed, aborting rebase")
        log.error("AI conflict resolution failed — aborting rebase.")
        git_client.run("rebase", "--abort", cwd=cwd)
        return 1
    tally.absorb(resolved)

    # Stage any remaining unstaged changes from post-resolution fixups (e.g. go mod tidy)
    if git_client.lines("diff", "--name-only", cwd=cwd):
        git_client.run("add", "-u", cwd=cwd)

    r = rebase_continue(cwd)
    if r.ok:
        return None

    if r.stderr.strip():
        log.dim(r.stderr.strip())

    # rebase --continue returns non-zero when the current commit is applied
    # but the *next* commit has conflicts — that's normal, not a failure.
    if rebase_inspect.rebase_in_progress(cwd):
        return None

    tfail(trail, "step_conflicts", "rebase --continue failed after resolution",
           output=r.combined_output)
    log.error("rebase --continue failed after conflict resolution — aborting.")
    git_client.run("rebase", "--abort", cwd=cwd)
    return 1


def step_advance(cwd: str, *, trail: Trail | None = None) -> int | None:
    """Advance rebase when there are no conflicts. Returns exit code to stop, or None to continue."""
    if rebase_inspect.is_empty_patch(cwd):
        sha, subject = rebase_inspect.rebase_head_info(cwd)
        log.info(f"Skipping empty commit {sha} — {subject}")
        tdecision(
            trail, "step", f"skipping empty commit {sha}",
            reason="patch already applied upstream",
            data={"commit": sha, "subject": subject},
        )
        r = git_client.run("rebase", "--skip", cwd=cwd)
        if not r.ok:
            log.error(f"git rebase --skip failed (exit {r.returncode})")
            return 1
        return None

    r = rebase_continue(cwd)
    if r.ok:
        return None

    if r.stderr.strip():
        log.dim(r.stderr.strip())

    # rebase --continue can return non-zero when the next commit has conflicts
    if rebase_inspect.rebase_in_progress(cwd):
        return None

    tfail(trail, "step_advance", "rebase --continue failed without conflicts",
           output=r.combined_output, data={"exit_code": r.returncode})
    log.error("rebase --continue failed without conflicts — aborting.")
    git_client.run("rebase", "--abort", cwd=cwd)
    return 1


def fresh(
    cwd: str, ctx: pr_context.ResolvedContext, mode: RunMode,
    force: bool = False, *, target_ref: str,
    trail: Trail | None = None,
    on_check_failure: rebase_land.CheckFailureFix | None = None,
) -> int:
    """Start a fresh rebase onto the target ref."""
    default = git_topology.default_branch(cwd)
    if ctx.branch == default:
        terr(trail, "preflight", f"on protected branch {ctx.branch}")
        log.error("Cannot rebase — currently on protected branch.")
        return 1

    log.info("Fetching origin...")
    # --prune is defense in depth behind the already-landed preflight, not a
    # substitute for it: dropping the remote-tracking ref of a branch deleted
    # on merge is what stops --force-with-lease from being satisfied by a stale
    # lease and silently recreating the remote branch.
    fetch = git_client.run("fetch", "--prune", "origin", cwd=cwd)
    if not fetch.ok:
        log.warn(f"Fetch failed — rebasing against potentially stale {target_ref}.")
        if fetch.stderr.strip():
            log.dim(fetch.stderr.strip())

    # Before the checkout, deliberately: --prune has just dropped
    # origin/<branch> for a branch whose PR merged and whose remote was
    # deleted, and the checkout below starts from exactly that ref. Asking the
    # tracker first is what turns "Cannot checkout feat/x" into the refusal
    # this preflight exists to give.
    landed = None if force else refusals.tracker_landed_check(cwd, ctx)
    if landed is not None:
        return refusals.refuse(ctx, landed, target_ref=target_ref, trail=trail)

    if ctx.current_branch != ctx.branch:
        display = ctx.current_branch or "detached HEAD"
        # Defense in depth: pr_context now creates a worktree per branch, but
        # --repo-dir bypasses that and can point straight at the default
        # branch's worktree. Checking out here would leave a feature branch
        # sitting in main/, where the next tool that syncs main/ to
        # origin/<default> hard-resets the branch out from under it.
        if ctx.current_branch == default:
            terr(trail, "preflight", f"refusing to check out {ctx.branch} into the {display} worktree")
            log.error(f"Refusing to check out {ctx.branch} into the {display} worktree.")
            log.dim(f"Run 'wt switch {ctx.branch}' and retry with --repo-dir on that worktree.")
            return 1
        log.info(f"Worktree on {display}, checking out {ctx.branch}...")
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

    log.info(f"Rebasing onto {target_ref}...")
    r = git_client.run("rebase", target_ref, cwd=cwd)

    if r.ok and not rebase_inspect.rebase_in_progress(cwd):
        return rebase_success(
            cwd, ctx, mode, target_ref=target_ref, trail=trail,
            on_check_failure=on_check_failure,
        )

    if not rebase_inspect.rebase_in_progress(cwd):
        tfail(trail, "rebase", f"git rebase failed (exit {r.returncode})", output=r.combined_output)
        log.error(f"git rebase failed (exit {r.returncode})")
        if r.stderr.strip():
            log.dim(r.stderr.strip())
        return 1

    return drive_to_completion(
        cwd, ctx, mode, target_ref=target_ref, force=force, trail=trail,
        on_check_failure=on_check_failure,
    )


def rebase_success(
    cwd: str, ctx: pr_context.ResolvedContext, mode: RunMode,
    tally: ResolutionTally | None = None, *, target_ref: str,
    trail: Trail | None = None,
    on_check_failure: rebase_land.CheckFailureFix | None = None,
) -> int:
    """Handle rebase completion — update state and optionally force-push."""
    tally = tally or ResolutionTally()

    # Counted before the push: its recovery paths add regeneration and
    # check-fix commits, which were never replayed from the old branch.
    replayed = git_client.commits_ahead(cwd, target_ref=target_ref)

    # RunMode.PUSH lands from main() via cmd_push; every other mode lands here.
    # A mode that does not reach the remote still lands, because the publishing
    # gate — shut for those modes — is what stops it, and a held landing is what
    # carries the force-push command back as data.
    lands_here = mode is not RunMode.PUSH

    label = "Rebase complete" if not tally.commits else (
        f"Rebase complete — resolved {len(tally.files)} file(s) "
        f"across {tally.commits} commit(s)"
    )

    landed = None
    if lands_here:
        # Announced only when the push will actually happen; a held run says the
        # same thing once at the end, through the label and the resume line.
        if mode.reaches_remote:
            log.info(f"{label} — force-pushing...")
        landed = rebase_land.land_rebased(
            cwd, resolved_files=tally.files or None, trail=trail,
            on_check_failure=on_check_failure,
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
        # None rather than False for a run that never tried: a held landing did
        # exactly what --no-push asked for, and recording it as a failed push
        # would be the summary's own invention.
        force_pushed=None if landed is None or landed.held else landed.ok,
        target_base=target_ref,
    )
    outcome.save(ctx)
    outcome.emit()

    if landed is not None and not landed.held:
        if landed.ok:
            log.ok("Force-pushed.")
            return 0
        log.error("Force-push failed.")
        return 1

    # The label holds in every mode that reaches here — the rebase finished.
    # Under FIX_ONLY the AI's work is the whole point of the run and the user is
    # about to push it by hand, so name it rather than reporting a bare "clean".
    log.ok(label)
    if landed is not None:
        log.ok(f"Run `{landed.resume}` to push, or re-run without --no-push.")
    return 0


