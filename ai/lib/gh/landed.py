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

`partial_landing` answers a fourth question the three above cannot, and only
`pr rebase` asks it: whether a *prefix* of the branch is upstream while the
rest is not. All three signals here are all-or-nothing — they report the
branch's work present or absent — so a branch six of whose seven commits have
landed reads to every one of them as a branch that landed nothing. It is kept
apart from `check`'s ladder because it is not evidence the work is done: it is
evidence that replaying the branch whole would reapply the landed part on top
of itself, which is a different finding with a different remedy.

Every one of them answers "no" rather than raising when it cannot ask: a ref
that does not resolve, a base that was never fetched, a `gh` that is absent,
unauthenticated or offline. "Landed" is the answer that suppresses something —
a refusal for one caller, a warning for the other — so a question nobody could
answer must never be able to produce it.

One "no" is different, and is why `merged_pr` and `by_tracker` return a type
rather than an optional. When the budget breaker declines the tracker read, no
call is made at all: the absence of a merged PR is manufactured by this process
rather than reported by GitHub, and it is indistinguishable from the answer
that lets a force-push proceed. Both carry ``looked`` so a caller can tell "the
tracker says no" from "the tracker was never asked", and each decides for
itself — `pr rebase` refuses rather than replay, `push_intent` leaves the
record unanswered for a later sweep. Collapsing the two is how a spent budget
turns into a force-push over merged work.

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

from gh import budget as gh_budget
from gh import client as gh_client
from git import client as git_client


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
class TrackerAnswer:
    """What the tracker said about a branch, and whether it was asked at all.

    ``looked`` is the read's own success, carried for the reason
    `PendingReview.looked` and `BotReviews.looked` are: every caller here acts
    on the *absence* of a merged PR, and a refused call produces that absence
    without GitHub having said anything. The budget breaker turns "call
    refused" into "empty answer", and an empty answer from this module is the
    one signal that survives a squash merge — so a caller that cannot tell the
    two apart force-pushes over merged work.

    Only a budget refusal sets it False. A `gh` that is absent, unauthenticated
    or offline stays True: that machine cannot answer the question at any
    point, and a caller that refused on it would never run there at all. The
    distinction is drawn here rather than by each caller because only this
    layer sees the refusal — `client.pr_view` collapses it into an empty dict
    on its way out.

    `merged_pr`'s budget-refusal branch is the only constructor site that ever
    passes ``looked=False`` — nothing else in this module should.
    """

    merged: MergedPR | None = None
    looked: bool = True
    remedy: str = ""


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


@dataclass(frozen=True)
class LandedVerdict:
    """The evidence, and whether every signal that could speak got to.

    ``looked`` is False only when the tracker read was refused by the budget
    breaker — see `TrackerAnswer`. A caller acting on ``landed is None`` must
    check it first: the two mean "nothing found the work upstream" and "one
    signal never ran", and only the first is a finding.
    """

    landed: Landed | None = None
    looked: bool = True
    remedy: str = ""


def merged_pr(
    cwd: str | Path, *, branch: str, repo: str = "", pr_number: int | None = None,
) -> TrackerAnswer:
    """What GitHub says about *branch*'s PR, and whether it was asked.

    *pr_number* is asked about in preference to *branch* when the caller already
    resolved one, and *repo* is omitted from the query when empty so that `gh`
    infers it from the remote.

    Best effort by design: gh may be absent, unauthenticated, offline, or the
    branch may have no PR at all. Every one of those is "the tracker has
    nothing to say", not "the branch is unlanded" — the git signals still get
    their turn. Only a state of MERGED counts, so an open or closed-unmerged PR
    reads the same as no answer.

    The one case that is *not* best effort is a read the budget breaker
    declined. That is not the tracker having nothing to say — it is us not
    asking, about a PR that is knowable, while the only signal surviving a
    squash merge goes unread. It comes back ``looked=False`` so a caller can
    tell it from an answer, and the latch is consulted after the call rather
    than before: one that armed *during* this read is the case that matters,
    and one that expired between the two readings would have let the call
    through anyway.
    """
    target = str(pr_number) if pr_number else branch
    # Neither is set only when the caller resolved no branch and no number,
    # which `client.pr_view` would turn into a bare `gh pr view` — answering
    # about whatever PR the *cwd*'s branch points at. Every caller today
    # passes a resolved branch, so this is the guard that keeps that true
    # rather than a case anything reaches; `pr_snapshot.fetch` draws the same
    # line for the same reason.
    if not target:
        return TrackerAnswer()
    data = gh_client.pr_view(target, "state", "number", "url", repo=repo, cwd=cwd)
    if not data:
        latch = gh_budget.latched(gh_budget.Resource.GRAPHQL)
        if latch is not None:
            return TrackerAnswer(looked=False, remedy=latch.remedy())
        return TrackerAnswer()
    if data.get("state") != "MERGED":
        return TrackerAnswer()
    return TrackerAnswer(merged=MergedPR(
        number=data.get("number") or 0, url=data.get("url") or "",
    ))


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


@dataclass(frozen=True)
class BranchCommit:
    """One commit the branch adds over the ref it is measured against."""

    sha: str
    subject: str


@dataclass(frozen=True)
class PartialLanding:
    """A leading run of the branch's commits is upstream; the rest is not.

    ``fork_point`` is the last commit of that run — the ref to replay *from*,
    which is what makes this the one finding here with an executable remedy.
    """

    landed: int
    unlanded: int
    fork_point: str
    fork_subject: str = ""

    @property
    def total(self) -> int:
        return self.landed + self.unlanded


def branch_commits(
    cwd: str | Path, *, target_ref: str, rev: str = "HEAD",
) -> list[BranchCommit]:
    """The commits *rev* adds over *target_ref*, oldest first."""
    lines = git_client.lines(
        "log", "--reverse", "--no-merges", "--format=%H%x1f%s",
        f"{target_ref}..{rev}", cwd=cwd,
    )
    commits = []
    for line in lines:
        sha, _, subject = line.partition("\x1f")
        if sha:
            commits.append(BranchCommit(sha=sha, subject=subject))
    return commits


def _patch_equivalent(
    cwd: str | Path, *, target_ref: str, rev: str,
) -> frozenset[str]:
    """Shas of *rev*'s commits that `git cherry` finds equivalent upstream."""
    equivalent = set()
    for line in git_client.lines("cherry", target_ref, rev, cwd=cwd):
        mark, _, sha = line.partition(" ")
        if mark == "-" and sha:
            equivalent.add(sha)
    return frozenset(equivalent)


def _same_subject_upstream(
    cwd: str | Path, *, subject: str, since: str, target_ref: str,
) -> str:
    """The oldest commit in ``since..target_ref`` with exactly *subject*, or "".

    ``--fixed-strings`` because a subject is prose and holds regex
    metacharacters, and the match is re-checked against the subject in full:
    `--grep` matches a substring of the message, so "fix: auth" would otherwise
    match "fix: auth token refresh" and claim a landing that never happened.
    """
    lines = git_client.lines(
        "log", "--reverse", "--fixed-strings", f"--grep={subject}",
        "--format=%H%x1f%s", f"{since}..{target_ref}", cwd=cwd,
    )
    for line in lines:
        sha, _, found = line.partition("\x1f")
        if found == subject:
            return sha
    return ""


def partial_landing(
    cwd: str | Path, *, target_ref: str, rev: str = "HEAD",
) -> PartialLanding | None:
    """A leading run of *rev*'s commits already in *target_ref*, or None.

    Walks the branch oldest-first and stops at the first commit that is not
    upstream. Only a *prefix* counts: a branch whose third commit landed and
    whose second did not has not been partially rebased, it has had one commit
    cherry-picked, and replaying it from a fork point would drop the second.

    Two signals per commit, because neither alone sees this case:

    * an equivalent patch id, which `git cherry` finds — exact, and what
      catches a clean cherry-pick or rebase of the prefix;
    * an identical subject upstream, which catches what patch ids miss. One
      amendment anywhere in the landed prefix changes the context lines of
      every later commit's hunks, so all seven commits of a seven-commit
      prefix can diverge by patch id while every one of them is plainly there
      by subject. That is the case this whole function exists for, and the
      exact signal cannot see it.

    The subject signal is the loose one, so it is constrained twice over. A
    match must be the *whole* subject, and each subject match must be a
    descendant of the last one — a prefix that landed landed in order, and
    requiring the upstream commits to be in that same order is what stops a
    repeated subject like "chore: regenerate" from manufacturing a fork point
    out of two unrelated commits.

    A patch-id match does not advance that watermark, because `git cherry`
    reports *that* a commit has an equivalent upstream and not which one. The
    ordering is therefore enforced among subject matches rather than across
    both kinds, which can only ever widen the search — and the fork point
    stays safe either way, since every commit before it was found upstream by
    one signal or the other.

    Merge commits are excluded, as they are from `git cherry`, so a branch
    carrying one has it counted on neither side.

    Returns None unless both sides are non-empty. Everything landed is
    `by_git`'s finding, not this one, and nothing landed is the ordinary
    rebase.
    """
    base = git_client.out("merge-base", target_ref, rev, cwd=cwd).strip()
    if not base:
        return None

    commits = branch_commits(cwd, target_ref=target_ref, rev=rev)
    if len(commits) < 2:
        # One commit is all-or-nothing by construction: whichever way it goes,
        # one of the two sides of a partial landing is empty.
        return None

    equivalent = _patch_equivalent(cwd, target_ref=target_ref, rev=rev)
    fork = None
    previous_upstream = base
    for index, commit in enumerate(commits):
        if commit.sha in equivalent:
            fork = commit
            continue
        match = _same_subject_upstream(
            cwd, subject=commit.subject, since=previous_upstream,
            target_ref=target_ref,
        )
        if not match:
            return _partial(fork, landed=index, unlanded=len(commits) - index)
        previous_upstream = match
        fork = commit
    return None


def _partial(
    fork: BranchCommit | None, *, landed: int, unlanded: int,
) -> PartialLanding | None:
    """The finding, or None when there is no prefix to fork from.

    *fork* is None exactly when the walk stopped on the branch's first commit,
    which is the ordinary unlanded branch and the common case.
    """
    if fork is None or landed == 0 or unlanded == 0:
        return None
    return PartialLanding(
        landed=landed, unlanded=unlanded,
        fork_point=fork.sha, fork_subject=fork.subject,
    )


def by_tracker(
    cwd: str | Path, *, branch: str, repo: str = "", pr_number: int | None = None,
) -> LandedVerdict:
    """Evidence from GitHub that the branch's PR merged, and whether it answered.

    Asks about *branch* rather than HEAD, which is what lets a caller run it
    before the branch is checked out — and what lets it answer for a branch that
    no longer exists anywhere but in the tracker's history.

    Carries `TrackerAnswer.looked` through rather than collapsing it, because
    the caller that acts on a None verdict here is the one that force-pushes.
    """
    answer = merged_pr(cwd, branch=branch, repo=repo, pr_number=pr_number)
    return LandedVerdict(
        landed=merged_report(answer.merged),
        looked=answer.looked,
        remedy=answer.remedy,
    )


def merged_report(merged: MergedPR | None) -> Landed | None:
    """A merged PR as this module's evidence type, or None for no answer.

    Split from ``by_tracker`` so a caller that already read the PR — one that
    batched this question with others into a single ``gh pr view`` — phrases the
    finding exactly as the read-it-here path does, rather than assembling a
    second wording that drifts from this one.
    """
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
) -> LandedVerdict:
    """The git signals first, then the tracker, as one verdict.

    The ladder for a caller with no ordering of its own. Git first because both
    of its signals are local ref reads against a ref the caller already has,
    where the tracker is a network round trip — so the round trip is spent only
    on the case the free signals cannot see, which is a squash merge whose
    target has moved on.

    A git signal that finds the work is a complete answer on its own, so the
    tracker is never asked and ``looked`` stays True — there is no unread
    signal to warn about when the question is already settled.
    """
    found = by_git(cwd, target_ref=target_ref, rev=rev)
    if found is not None:
        return LandedVerdict(landed=found)
    return by_tracker(cwd, branch=branch, repo=repo, pr_number=pr_number)
