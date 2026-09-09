"""Which ref a run replays onto, and putting the worktree on the branch.

Two questions that travel together: what to rebase onto, and how to get the
worktree onto the branch being rebased without discarding commits either the
local ref or origin's holds alone. The second is the densest piece of
irreversible-action logic in the subsystem — every path through
``checkout_target_branch`` is either lossless or a refusal.

Not to be confused with ``pr.target``, which owns repo identity and target
*directories*. Nothing here touches either.
"""

# doc-group: platform

from __future__ import annotations

from core import log
from core.trail import Trail, tdecision, terr, tfail
from gh import client as gh_client
from git import client as git_client
from git import topology as git_topology
from pr import context as pr_context

from . import inspect as rebase_inspect
from . import types as rebase_types

RefDivergence = rebase_types.RefDivergence
UNPUSHED_SUBJECT_LIMIT = rebase_types.UNPUSHED_SUBJECT_LIMIT


def pr_base_branch(cwd: str, ctx: pr_context.ResolvedContext) -> str | None:
    """The branch the PR targets per GitHub, or None when it cannot say.

    Only asked when a PR number is already resolved: a branch with no PR yet
    has no base to report, and probing by branch name would spend a round trip
    on every rebase to learn nothing.

    Best effort like ``branch_landed.merged_pr`` — gh may be absent,
    unauthenticated or rate-limited, and the repo's default branch is the right
    answer for all but stacked and release-branch PRs.
    """
    if not ctx.pr_number:
        return None

    data = gh_client.pr_view(ctx.pr_number, "baseRefName", repo=ctx.repo, cwd=cwd)
    return data.get("baseRefName") or None


def resolve_target_ref(
    cwd: str, ctx: pr_context.ResolvedContext, onto: str | None,
    *, trail: Trail | None = None,
) -> str:
    """The ref this run rebases onto, most authoritative source first.

    Resolved once at entry and threaded through the run, so the rebase, the
    already-landed signals, the ahead count, the recorded ``target_base`` and
    the conflict prompts all name the same branch. A branch whose PR targets a
    release or a stack parent is replayed onto that base, not onto whatever the
    repo calls its trunk.
    """
    def decide(ref: str, reason: str) -> str:
        tdecision(trail, "target_ref", f"rebasing onto {ref}", reason=reason)
        return ref

    if onto:
        return decide(onto, "--onto flag set")

    base = pr_base_branch(cwd, ctx)
    if base:
        return decide(f"origin/{base}", f"PR #{ctx.pr_number} targets {base}")

    return decide(
        f"origin/{git_topology.default_branch(cwd)}",
        "no PR base to read — falling back to the repo's default branch",
    )


def resume_target_ref(
    ctx: pr_context.ResolvedContext, target_ref: str, *, trail: Trail | None = None,
) -> str:
    """The ref an in-progress rebase is actually replaying onto.

    A resume continues the rebase a prior run started, so it names the ref that
    run recorded rather than whatever this run resolved — the PR's base branch
    can change on GitHub between the two, and the commits are already being
    replayed onto the original.
    """
    recorded = rebase_types.recorded_target_base(ctx)
    if not recorded or recorded == target_ref:
        return target_ref
    tdecision(
        trail, "target_ref", f"resuming onto recorded {recorded}",
        reason=f"in-progress rebase was started against {recorded}, "
               f"not this run's resolved {target_ref}",
    )
    return recorded


def local_vs_remote(cwd: str, branch: str) -> RefDivergence:
    """Compare the local *branch* ref against origin's, without moving HEAD.

    Deliberately ref-to-ref rather than the ``origin/<branch>..HEAD`` that
    pr_context._unpushed_count asks: this runs while the worktree is still on
    some other branch, so HEAD is not the thing being measured.
    """
    r = git_client.run(
        "rev-list", "--left-right", "--count",
        f"origin/{branch}...refs/heads/{branch}", cwd=cwd,
    )
    if not r.ok:
        return RefDivergence()
    parts = r.stdout.split()
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        return RefDivergence()
    # Left of the symmetric difference is origin's side, right is the local ref.
    return RefDivergence(ahead=int(parts[1]), behind=int(parts[0]), comparable=True)


def unpushed_subjects(cwd: str, branch: str) -> list[str]:
    """Subjects of commits on local *branch* that origin's copy does not have."""
    return git_client.lines(
        "log", "--no-decorate", "--oneline",
        f"-{UNPUSHED_SUBJECT_LIMIT}", f"origin/{branch}..refs/heads/{branch}",
        cwd=cwd,
    )


def refuse_diverged(
    cwd: str, ctx: pr_context.ResolvedContext, div: RefDivergence,
    *, trail: Trail | None = None,
) -> int:
    """Report a local branch that neither ref can be discarded from."""
    subjects = unpushed_subjects(cwd, ctx.branch)
    terr(
        trail, "preflight", f"{ctx.branch} has diverged from origin/{ctx.branch}",
        data={"ahead": div.ahead, "behind": div.behind, "unpushed": subjects},
    )
    log.error(
        f"Refusing to check out {ctx.branch} — it holds {div.ahead} commit(s) "
        f"origin/{ctx.branch} does not, and origin holds {div.behind} it does not."
    )
    for subject in subjects:
        log.dim(f"  {subject}")
    # A list that stops at the cap without saying so reads as the whole set,
    # and the operator would go reconcile a branch they think they have seen.
    if div.ahead > len(subjects):
        log.dim(f"  ... and {div.ahead - len(subjects)} more")
    log.dim("Reconcile the two in that branch's own worktree, then retry.")
    return 1


def checkout_target_branch(
    cwd: str, ctx: pr_context.ResolvedContext, *, trail: Trail | None = None,
) -> int:
    """Put *cwd* on ctx.branch without discarding commits either ref holds alone.

    ``checkout -B <branch> origin/<branch>`` resets an existing local branch to
    the remote, so a commit that was never pushed leaves the branch ref before
    the rebase that exists to replay it ever runs. It stays the way a
    branch that is not here yet gets created, and the way one that is merely
    behind is fast-forwarded — both are lossless. An existing ref carrying its
    own commits is checked out as it stands instead, and true divergence is
    refused rather than resolved: replaying a stale local branch over a remote
    that was force-pushed and then force-pushing the result loses the same work
    in the other direction.
    """
    from_origin = ("checkout", "-B", ctx.branch, f"origin/{ctx.branch}")
    if not rebase_inspect.ref_exists(cwd, f"refs/heads/{ctx.branch}"):
        return run_checkout(cwd, ctx, from_origin, trail=trail)

    div = local_vs_remote(cwd, ctx.branch)
    if div.diverged:
        return refuse_diverged(cwd, ctx, div, trail=trail)

    # Not comparable here means the local ref resolved but origin's did not, so
    # every commit on it is unpushed and there is no remote to reset to anyway.
    if div.local_only_work or not div.comparable:
        return run_checkout(cwd, ctx, ("checkout", ctx.branch), trail=trail)

    return run_checkout(cwd, ctx, from_origin, trail=trail)


def run_checkout(
    cwd: str, ctx: pr_context.ResolvedContext, args: tuple[str, ...],
    *, trail: Trail | None = None,
) -> int:
    """Run one checkout, reporting git's own stderr when it refuses."""
    co = git_client.run(*args, cwd=cwd)
    if not co.ok:
        tfail(trail, "preflight", f"cannot checkout {ctx.branch}", output=co.combined_output)
        log.error(f"Cannot checkout {ctx.branch}: {co.stderr.strip()}")
        return 1
    return 0


