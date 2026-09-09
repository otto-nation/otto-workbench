"""Preflight refusals — the four questions asked before a branch is replayed.

Each check answers with a ``RefusalReport`` or None, and ``refuse`` is what
turns one into the shared exit code. A refusal leaves the worktree untouched
and nothing pushed, which is the guarantee the whole module exists to keep.
"""

# doc-group: platform

from __future__ import annotations

from dataclasses import asdict

from core import log
from core.trail import Trail, terr
from gh import landed as branch_landed
from git import client as git_client
from pr import context as pr_context
from pr.domains import RebaseStatus

from . import inspect as rebase_inspect
from . import types as rebase_types

CONFLICT_FILE_BUDGET = rebase_types.CONFLICT_FILE_BUDGET
REFUSAL_EXIT = rebase_types.REFUSAL_EXIT
REFUSAL_OVERRIDE_FLAG = rebase_types.REFUSAL_OVERRIDE_FLAG
RebaseOutcome = rebase_types.RebaseOutcome
RefusalReport = rebase_types.RefusalReport
RefusalSignal = rebase_types.RefusalSignal


def as_refusal(
    landed: branch_landed.Landed | None, branch: str,
) -> RefusalReport | None:
    """`branch_landed`'s evidence as this script's refusal payload, or None.

    The one place the two vocabularies meet. `Landed` carries no branch name —
    its detail lines describe the comparison rather than who was compared — so
    naming the branch is what this adds.
    """
    if landed is None:
        return None
    return RefusalReport(
        branch=branch, signal=landed.signal.value, detail=landed.detail,
        commits_ahead=landed.commits_ahead, pr_number=landed.pr_number,
    )


def tracker_landed_check(
    cwd: str, ctx: pr_context.ResolvedContext,
) -> RefusalReport | None:
    """Evidence from GitHub that the branch's PR merged, or None.

    Split from the git signals because it asks about ``ctx``, not HEAD, which
    is what lets it run before the branch is checked out. That ordering is the
    whole point: for a branch whose PR merged and whose remote was deleted,
    ``fetch --prune`` removes ``origin/<branch>``, so a checkout from that ref
    fails — the refusal has to come first or it never comes at all.

    The most authoritative signal, and the only one that survives a squash
    merge once the target ref has moved on with unrelated work.
    """
    return as_refusal(branch_landed.by_tracker(
        cwd, branch=ctx.branch, repo=ctx.repo, pr_number=ctx.pr_number,
    ), ctx.branch)


def git_landed_check(
    cwd: str, ctx: pr_context.ResolvedContext, *, target_ref: str,
) -> RefusalReport | None:
    """Evidence from git that the branch's work is in the target ref, or None.

    Every signal here compares HEAD, so this only means anything once the
    branch is checked out — which is why it is a separate call from the tracker
    check rather than `branch_landed.check`'s single ladder.
    """
    return as_refusal(
        branch_landed.by_git(cwd, target_ref=target_ref), ctx.branch,
    )


def unrelated_history_check(
    cwd: str, ctx: pr_context.ResolvedContext, *, target_ref: str,
) -> RefusalReport | None:
    """Refuse a branch that shares no history with the ref it would rebase onto.

    Exact rather than heuristic: git either finds a merge base or it does not.
    A branch with none was cut from a different root — a repo re-initialised
    over an existing checkout leaves a whole population of them — and rebasing
    it replays its entire history onto that unrelated root, conflicting on every
    commit because there is no common ancestor to merge against.

    Asked before the landed signals, which compare against a ref this branch has
    no relationship to and so answer nothing.
    """
    if rebase_inspect.shares_history(cwd, target_ref=target_ref):
        return None
    return RefusalReport(
        branch=ctx.branch, signal=RefusalSignal.NO_MERGE_BASE.value,
        detail=f"no commit in common with {target_ref}",
        status=RebaseStatus.UNRELATED_HISTORY.value,
    )


# What each refusal tells the operator would happen if it did not refuse. Keyed
# by status so a caller cannot pair a refusal with another one's explanation,
# and `{ref}` is the resolved base, not necessarily origin/main.
REFUSAL_HINTS = {
    RebaseStatus.ALREADY_LANDED.value:
        "Rebasing would replay work already in {ref} and force-push it back, "
        "recreating a remote branch the merge deleted.",
    RebaseStatus.UNRELATED_HISTORY.value:
        "Rebasing would replay the branch's entire history onto an unrelated "
        "root, conflicting on every commit. Retarget the branch, or recreate "
        "it from {ref}.",
    RebaseStatus.CONFLICTS_OVER_BUDGET.value:
        "A branch conflicting this widely with {ref} has usually had its work "
        "land in another shape. Resolving that many conflicts unattended "
        "rewrites files the branch never touched.",
}


def refuse(
    ctx: pr_context.ResolvedContext, report: RefusalReport, *, target_ref: str,
    trail: Trail | None = None,
) -> int:
    """Report a refused rebase and stop, on the shared exit code."""
    terr(trail, "preflight", f"refusing to rebase ({report.signal})", data=asdict(report))
    log.error(f"Refusing to rebase {report.branch} — {report.detail}.")
    log.dim(REFUSAL_HINTS[report.status].format(ref=target_ref))
    log.dim(f"Pass {REFUSAL_OVERRIDE_FLAG} to rebase it anyway.")
    RebaseOutcome(status=RebaseStatus(report.status), target_base=target_ref).save(ctx)
    report.emit()
    return REFUSAL_EXIT


def refuse_over_budget(
    cwd: str, ctx: pr_context.ResolvedContext, spread: int, *, target_ref: str,
    trail: Trail | None = None,
) -> int:
    """Abort a rebase conflicting too widely to be resolved unattended.

    The only refusal raised mid-rebase rather than in the preflight: how far a
    branch and its base have diverged is not knowable until git says so. The
    abort restores the branch, so the refusal leaves the worktree where the
    preflight refusals do — untouched, with nothing pushed.
    """
    git_client.run("rebase", "--abort", cwd=cwd)
    return refuse(ctx, RefusalReport(
        branch=ctx.branch, signal=RefusalSignal.CONFLICTS_OVER_BUDGET.value,
        detail=f"conflicts in {spread} files, over the "
               f"{CONFLICT_FILE_BUDGET}-file budget",
        status=RebaseStatus.CONFLICTS_OVER_BUDGET.value,
    ), target_ref=target_ref, trail=trail)


