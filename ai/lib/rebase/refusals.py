"""Preflight refusals — the four questions asked before a branch is replayed.

Each check answers with a ``RefusalReport`` or None, and ``refuse`` is what
turns one into the shared exit code. A refusal leaves the worktree untouched
and nothing pushed, which is the guarantee the whole module exists to keep.
"""

# doc-group: platform

from __future__ import annotations

from dataclasses import asdict, dataclass

from core import log
from core.trail import Trail, terr
from gh import budget as gh_budget
from gh import landed as branch_landed
from git import client as git_client
from pr import context as pr_context
from pr.domains import RebaseStatus

from . import inspect as rebase_inspect
from . import pr_snapshot as rebase_pr_snapshot
from . import types as rebase_types

# The budgets themselves are not re-exported here. This module renders a
# breach its caller already measured, and an alias nothing in the file reads is
# a second place for the next reader to look for the number's owner.
REFUSAL_EXIT = rebase_types.REFUSAL_EXIT
REFUSAL_OVERRIDE_FLAG = rebase_types.REFUSAL_OVERRIDE_FLAG
RebaseOutcome = rebase_types.RebaseOutcome
RefusalReport = rebase_types.RefusalReport
RefusalSignal = rebase_types.RefusalSignal

# The flag that turns a refusal into a narrower replay rather than waiving it.
# Named here because both the refusal text and the CLI must spell it the same,
# and a remedy naming a flag that does not parse is the dead end the partial
# landing signal was added to avoid.
FORK_POINT_FLAG = "--fork-point"


@dataclass(frozen=True)
class BudgetBreach:
    """Which conflict budget a step would cross, and what it would have spent.

    Two budgets share one refusal status, so the caller passes the signal that
    fired alongside the sentence describing it rather than letting this module
    guess which count was the one that ran out.
    """

    signal: RefusalSignal
    detail: str


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
    snapshot: rebase_pr_snapshot.PRSnapshot | None = None,
) -> RefusalReport | None:
    """Evidence from GitHub that the branch's PR merged, or None.

    Split from the git signals because it asks about ``ctx``, not HEAD, which
    is what lets it run before the branch is checked out. That ordering is the
    whole point: for a branch whose PR merged and whose remote was deleted,
    ``fetch --prune`` removes ``origin/<branch>``, so a checkout from that ref
    fails — the refusal has to come first or it never comes at all.

    The most authoritative signal, and the only one that survives a squash
    merge once the target ref has moved on with unrelated work.

    Reads *snapshot* when the caller has one, so the state and the PR's base
    come from a single ``gh pr view``. The unanswered snapshot is not a refusal:
    a tracker that said nothing is not a tracker saying the branch landed.

    With one exception, which is the whole reason ``PRSnapshot.refused``
    exists. A read the budget breaker declined is not the tracker having
    nothing to say — it is us not asking, about a PR that is knowable, while
    the one signal that survives a squash merge goes unread. Proceeding there
    force-pushes on the strength of a question we skipped, so it refuses
    instead, on its own status: nothing here found the work landed, and the
    report must not claim otherwise.

    Every other unanswered read still proceeds. A machine with no ``gh``, no
    auth or no network cannot answer this question at any point, and a refusal
    keyed on that would mean `pr rebase` never runs there at all.

    Both paths draw that distinction, each from the read that made it. The
    snapshot carries ``refused`` from ``fetch``; the snapshotless path gets
    ``looked`` from ``by_tracker``, which consults the latch immediately after
    its own call. Neither asks the latch from *here*, which would refuse on a
    latch some earlier, unrelated call armed even where this read succeeded and
    found the PR open — the false refusal the whole design is arranged to
    avoid.

    The snapshotless path is `pr ci --fix`, which rebases without fetching a
    snapshot of its own, and direct `pr-rebase` invocations. It used to keep a
    best-effort contract on the reading that only `--repo-dir` reached it; `pr
    ci --fix` spends GraphQL resolving its own context before it gets here, so
    it is the path most likely to meet an armed latch, and it force-pushes.
    """
    if snapshot is not None:
        if snapshot.refused:
            return _refused_report(ctx, snapshot.remedy)
        if not snapshot.merged:
            return None
        return as_refusal(branch_landed.merged_report(
            branch_landed.MergedPR(number=snapshot.number, url=snapshot.url),
        ), ctx.branch)
    verdict = branch_landed.by_tracker(
        cwd, branch=ctx.branch, repo=ctx.repo, pr_number=ctx.pr_number,
    )
    if not verdict.looked:
        return _refused_report(ctx, verdict.remedy)
    return as_refusal(verdict.landed, ctx.branch)


def _refused_report(ctx: pr_context.ResolvedContext, remedy: str) -> RefusalReport:
    """The refusal for a tracker read the budget breaker declined to make.

    *remedy* is the one `PRSnapshot.fetch` captured from the latch at read
    time. Falls back to the generic hint only for a snapshot built with no
    remedy of its own — a live read always carries one, since it is captured
    in the same instant the latch is observed.
    """
    remedy = remedy or gh_budget.BUDGET_EXHAUSTED_HINT
    return RefusalReport(
        branch=ctx.branch, signal=RefusalSignal.TRACKER_REFUSED.value,
        detail=f"GitHub was not asked whether the PR merged — {remedy}",
        status=RebaseStatus.TRACKER_UNREAD.value,
    )


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


def partially_landed_check(
    cwd: str, ctx: pr_context.ResolvedContext, *, target_ref: str,
) -> RefusalReport | None:
    """Refuse a branch whose leading commits are already in the target ref.

    The gap between the two landed signals. Both of those ask whether the
    branch's work is *entirely* upstream, so a branch with six landed commits
    and one unlanded reads to them exactly like a branch that landed nothing —
    `git cherry` reports a `+` for the seventh and `all(...)` is False. There
    is no count of unique commits at which that stops being true; a branch with
    one commit left is structurally incapable of tripping it.

    Replaying such a branch reapplies the landed prefix on top of itself, which
    conflicts every one of those commits against the version of itself already
    in the base — the shape that spends a resolution call per file per commit
    and ends by rewriting the base's copy back to the branch's.

    Unlike every other refusal here, this one has a remedy the tool can carry
    out: the fork point it names is what `--fork-point` takes, and the replay
    becomes `git rebase --onto <base> <fork-point>`. See
    `branch_landed.partial_landing` for how the prefix is identified.
    """
    partial = branch_landed.partial_landing(cwd, target_ref=target_ref)
    if partial is None:
        return None
    return RefusalReport(
        branch=ctx.branch,
        signal=RefusalSignal.PARTIALLY_LANDED.value,
        detail=(
            f"{partial.landed} of {partial.total} commit(s) are already in "
            f"{target_ref} — the branch forked at {partial.fork_point} "
            f"({partial.fork_subject})"
        ),
        commits_ahead=partial.total,
        status=RebaseStatus.PARTIALLY_LANDED.value,
        remedy=f"{FORK_POINT_FLAG} {partial.fork_point}",
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
    RebaseStatus.PARTIALLY_LANDED.value:
        "Replaying the whole branch would reapply the commits already in "
        "{ref} on top of themselves, conflicting each against its own landed "
        "version. Replay only what is left instead — the fork point to skip "
        "to is in the refusal above.",
    RebaseStatus.TRACKER_UNREAD.value:
        "Whether this branch's PR already merged is the one thing a squash "
        "merge leaves no trace of in git, and the quota to ask ran out. "
        "Rebasing now would force-push without it. Wait for the refill, or "
        "check the PR yourself.",
}


def refuse(
    ctx: pr_context.ResolvedContext, report: RefusalReport, *, target_ref: str,
    trail: Trail | None = None,
) -> int:
    """Report a refused rebase and stop, on the shared exit code."""
    terr(trail, "preflight", f"refusing to rebase ({report.signal})", data=asdict(report))
    log.error(f"Refusing to rebase {report.branch} — {report.detail}.")
    log.dim(REFUSAL_HINTS[report.status].format(ref=target_ref))
    # The narrower way out before the blunt one, where there is a narrower way.
    # `--force` waives every check at once, so offering it first to a refusal
    # that has an exact remedy trains the operator to reach past the fix.
    if report.remedy:
        log.dim(f"Re-run with {report.remedy} to replay only what is left.")
    log.dim(f"Pass {REFUSAL_OVERRIDE_FLAG} to rebase it anyway.")
    RebaseOutcome(status=RebaseStatus(report.status), target_base=target_ref).save(ctx)
    report.emit()
    return REFUSAL_EXIT


def refuse_over_budget(
    cwd: str, ctx: pr_context.ResolvedContext, breach: BudgetBreach, *,
    target_ref: str, trail: Trail | None = None,
) -> int:
    """Abort a rebase conflicting too widely, or too often, to resolve unattended.

    The only refusals raised mid-rebase rather than in the preflight: how far a
    branch and its base have diverged, and how many resolutions that costs, are
    not knowable until git says so. The abort restores the branch, so the
    refusal leaves the worktree where the preflight refusals do — untouched,
    with nothing pushed.

    Both budgets share ``CONFLICTS_OVER_BUDGET`` as their status, since the
    operator's options are identical, and differ in ``signal``, which is what
    says which count ran out.
    """
    git_client.run("rebase", "--abort", cwd=cwd)
    return refuse(ctx, RefusalReport(
        branch=ctx.branch, signal=breach.signal.value, detail=breach.detail,
        status=RebaseStatus.CONFLICTS_OVER_BUDGET.value,
    ), target_ref=target_ref, trail=trail)


