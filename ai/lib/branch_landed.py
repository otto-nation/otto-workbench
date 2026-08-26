"""Whether a branch's work is already in the ref it would be measured against.

Two callers ask this, for opposite reasons, and neither can answer it alone.
`pr rebase` asks so it can refuse: replaying a branch whose work already landed
force-pushes back a remote branch the merge deleted. `push_intent` asks so it
can stay quiet: a recorded push the remote no longer has a ref for looks exactly
like a push that vanished, and every squash-merged branch in a repo that deletes
its head refs would otherwise be reported as one.

Three signals, in the order `check` tries them, none of them sufficient alone:

* `diff_is_empty` — the trees match. Catches a squash merge, whose commits are
  unreachable from the squashed commit, so nothing comparing commits notices the
  work arrive. Stops answering once the target ref moves on with unrelated work.
* `all_commits_upstream` — every commit has an equivalent patch id upstream.
  Catches a rebase or a merge-commit landing, and survives the target moving on.
  Misses a squash, which leaves no per-commit equivalent to match.
* `merged_pr` — GitHub says the PR merged. The only signal that survives a
  squash merge once the target ref has moved on, and the only one that costs a
  round trip, which is why the ladder reaches it last.

Every one of them answers "no" rather than raising when it cannot ask: a ref
that does not resolve, a base that was never fetched, a `gh` that is absent,
unauthenticated or rate-limited. "Landed" is the answer that suppresses
something — a refusal for one caller, a warning for the other — so a question
nobody could answer must never be able to produce it.

`check` is that ladder for a caller that wants one answer and would rather not
spend the round trip. The signals are exported one at a time as well, because
the ordering belongs to the caller: `pr rebase` asks the tracker *first*, and
before the checkout, because `fetch --prune` has just dropped the
`origin/<branch>` that checkout would start from — its refusal has to come
before the checkout or it never comes at all.

`Landed` carries no branch name. Each detail line describes the comparison
rather than who was compared, so a caller pairs it with whatever it calls the
branch and renders the two together.
"""

# doc-group: platform

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import gh_client
import git_client


class LandedSignal(StrEnum):
    """Which signal found the work already present."""

    PR_MERGED = "pr_merged"
    EMPTY_DIFF = "empty_diff"
    COMMITS_UPSTREAM = "commits_upstream"


@dataclass(frozen=True)
class MergedPR:
    """The tracker's answer when it reports the branch's PR merged.

    A named answer rather than a bare pair: the caller reads ``.number`` and
    ``.url`` instead of relying on the order two same-shaped fields happen to be
    returned in, and a third field later is an added attribute rather than a
    changed arity at every call site.
    """

    number: int
    url: str = ""


@dataclass(frozen=True)
class Landed:
    """Evidence that the work is already in the target ref.

    ``commits_ahead`` is None on the tracker path, which can run before the
    branch is checked out — there is no honest count to report from a worktree
    holding somebody else's HEAD. ``pr_number`` is set on that path only.
    """

    signal: LandedSignal
    detail: str
    commits_ahead: int | None = None
    pr_number: int | None = None


def merged_pr(
    cwd: str | Path, *, branch: str, repo: str = "", pr_number: int | None = None,
) -> MergedPR | None:
    """The merged PR for *branch* in *repo* per GitHub, or None.

    *pr_number* is asked about in preference to *branch* when the caller already
    resolved one, and *repo* is omitted from the query when empty so that `gh`
    infers it from the remote.

    Best effort by design: gh may be absent, unauthenticated, rate-limited, or
    the branch may have no PR at all. Every one of those is "the tracker has
    nothing to say", not "the branch is unlanded" — the git signals still get
    their turn. Only a state of MERGED counts, so an open or closed-unmerged PR
    reads the same as no answer.
    """
    target = str(pr_number) if pr_number else branch
    data = gh_client.pr_view(target, "state", "number", "url", repo=repo, cwd=cwd)
    if data.get("state") != "MERGED":
        return None
    return MergedPR(number=data.get("number") or 0, url=data.get("url") or "")


def diff_is_empty(cwd: str | Path, *, target_ref: str, rev: str = "HEAD") -> bool:
    """Whether *rev*'s tree is identical to *target_ref*'s.

    Tracker-agnostic, and the one signal that catches a squash merge: the
    branch's own commits are unreachable from the squashed commit, so nothing
    that compares commits notices the work landed.
    """
    return git_client.ok("diff", "--quiet", target_ref, rev, cwd=cwd)


def all_commits_upstream(
    cwd: str | Path, *, target_ref: str, rev: str = "HEAD",
) -> bool:
    """Whether every commit *rev* adds over *target_ref* has an equivalent there.

    ``git cherry`` compares patch ids, so it still recognises a commit that was
    rebased or amended on its way into the target ref — the case an empty diff
    misses once the target has moved on with unrelated work.
    """
    lines = git_client.lines("cherry", target_ref, rev, cwd=cwd)
    return bool(lines) and all(ln.startswith("-") for ln in lines)


def by_tracker(
    cwd: str | Path, *, branch: str, repo: str = "", pr_number: int | None = None,
) -> Landed | None:
    """Evidence from GitHub that the branch's PR merged, or None.

    Asks about *branch* rather than HEAD, which is what lets a caller run it
    before the branch is checked out — and what lets it answer for a branch that
    no longer exists anywhere but in the tracker's history.
    """
    merged = merged_pr(cwd, branch=branch, repo=repo, pr_number=pr_number)
    if merged is None:
        return None
    where = f" ({merged.url})" if merged.url else ""
    return Landed(
        signal=LandedSignal.PR_MERGED,
        detail=f"PR #{merged.number} is merged{where}",
        pr_number=merged.number,
    )


def by_git(cwd: str | Path, *, target_ref: str, rev: str = "HEAD") -> Landed | None:
    """Evidence from git that *rev*'s work is in *target_ref*, or None.

    Tried in order: an empty diff (squash merges), then matching patch ids
    (rebase and merge-commit landings). Each stands alone — neither survives
    every merge style — and both are tracker-agnostic, so they are what answers
    in a repo where gh cannot.
    """
    ahead = git_client.commits_ahead(cwd, target_ref=target_ref, rev=rev)

    # A rev with no commits of its own has nothing that could have landed, and
    # both signals below read as "already upstream" for it — vacuously, for the
    # freshly branched worktree that is the common case.
    #
    # The same count is also what a merge commit leaves, where the rev really is
    # upstream. The two are indistinguishable from here, so this answers None for
    # both and a caller that reads ancestry as evidence asks for it directly —
    # `push_intent._landed_elsewhere` does, before it reaches this.
    if ahead == 0:
        return None

    if diff_is_empty(cwd, target_ref=target_ref, rev=rev):
        return Landed(
            signal=LandedSignal.EMPTY_DIFF,
            detail=f"{ahead} commit(s) ahead of {target_ref} but no diff against it",
            commits_ahead=ahead,
        )

    if all_commits_upstream(cwd, target_ref=target_ref, rev=rev):
        return Landed(
            signal=LandedSignal.COMMITS_UPSTREAM,
            detail=f"all {ahead} commit(s) already have an equivalent in {target_ref}",
            commits_ahead=ahead,
        )

    return None


def check(
    cwd: str | Path, *, target_ref: str, branch: str, rev: str = "HEAD",
    repo: str = "", pr_number: int | None = None,
) -> Landed | None:
    """The git signals first, then the tracker, or None when none of them answer.

    The ladder for a caller with no ordering of its own. Git first because both
    of its signals are local ref reads against a ref the caller already has,
    where the tracker is a network round trip — so the round trip is spent only
    on the case the free signals cannot see, which is a squash merge whose
    target has moved on.
    """
    return by_git(cwd, target_ref=target_ref, rev=rev) or by_tracker(
        cwd, branch=branch, repo=repo, pr_number=pr_number,
    )
