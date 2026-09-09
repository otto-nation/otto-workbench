"""Which commit carries one review thread's change, and how firmly.

Every reviewer-facing surface asks this — the summary row, the thread reply, and
the blob permalink under it — and four mechanisms used to answer it
independently, each written for the caller that noticed first. Three of them
were wrong for some caller. The one answer lives here, and the callers decide
how to *render* an entry with no citation rather than inventing one.

The distinction the module exists to keep is between a fact about the branch and
a fact about a row. "Commits landed outside the pass" is the first; "which
commit carries this thread" is the second, and the second may not be inferred
from the first. Stamping every row with whatever is at HEAD names a commit
picked for having no relationship to the row.

:class:`AddressingHistory` is the per-row evidence that makes an honest answer
possible: the newest commit to touch the one line a thread is anchored to, dated
against when the reviewer opened it. It is a memo because `git log -L` costs a
process per location and every surface asks about the same threads.
"""

# doc-group: publishing

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from git import client as git_client
from git import topology as git_topology
from git.land import CommitStatus, LandResult
from pr.fix import SettledBy
from pr.thread_models import CommentItem, ReportThread


class CommitClaim(StrEnum):
    """How firmly one thread's change can be pinned to a commit.

    Render-time only, never persisted: this is what a run may *say* about a
    row, not a fact recorded about the branch.

    The three that carry no SHA are the point of the type. "This entry has no
    commit" used to be one case, and two reviewer-facing surfaces wanted
    opposite renderings of it, so whichever was patched last inverted the
    other. They are separate answers now.
    """

    # The entry names the commit that carried it, recorded when the fix pass
    # that landed it committed. Wins over anything the running pass could say.
    RECORDED = "recorded"
    # The row rode the running pass's own commit. Whether that commit may be
    # named is the pass's question, not the row's — an unpushed one would 404.
    PASS = "pass"
    # Work landed outside the pass, and the row's own line history says which
    # commit carries it.
    RESOLVED = "resolved"
    # The same, with nothing at the row's line to resolve it: the thread is
    # outdated, or the fix landed somewhere the thread is not anchored to.
    UNDETERMINED = "undetermined"
    # No commit carries this row: it was satisfied without one, or it was
    # replayed from a round that recorded none.
    UNRECORDED = "unrecorded"


@dataclass
class CommitPushResult:
    sha: str | None
    # A land.CommitStatus. Typed as `str` because a status read back out
    # of a state file is a plain string, and CommitStatus is a StrEnum
    # precisely so the two compare equal.
    status: str
    error: str
    # Where `sha` came from, which decides what an entry with no commit of its
    # own inherits from it. The default is the only honest answer for a pass
    # that committed its own work; `history_rewrite.reconciled_commit` is the
    # one place that recovers a SHA from somewhere else and says so.
    claim: CommitClaim = CommitClaim.PASS


# A commit that may not be citable outward. `push_failed` is git refusing;
# `push_held` is us refusing, because the gate was shut when the pass ran;
# `push_lost` is git reporting a push the remote did not keep; `push_unverified`
# is a push nobody could confirm either way. All four leave the SHA unsafe to
# cite — a commit link that 404s for the reviewer reading it is worse than a
# reply deferred a round.
_UNPUSHED_STATUSES = frozenset({
    CommitStatus.PUSH_FAILED, CommitStatus.PUSH_HELD, CommitStatus.PUSH_LOST,
    CommitStatus.PUSH_UNVERIFIED,
})


def commit_unpushed(status: str) -> bool:
    return status in _UNPUSHED_STATUSES


def pass_commit(wt_path: Path, result: LandResult) -> CommitPushResult:
    """The owner's landing, as the pass records and renders it.

    The SHA is abbreviated because every other SHA this command persists is —
    the snapshot HEAD, each thread's `commit_sha`, each `read_sha`. A state file
    that mixed the two widths would make one commit recorded twice look like
    two, which is exactly the comparison :func:`attribute_commit` turns on.
    """
    short = git_client.out(
        "rev-parse", "--short", result.sha, cwd=wt_path,
    ) if result.sha else ""
    return CommitPushResult(short or None, result.status, result.error)


@dataclass(frozen=True)
class CommitAttribution:
    """Which commit carries one thread's change, and how well that is known.

    `sha` is filled only when a reviewer could open that commit and find the
    change in it, so `cited` is the whole question a renderer has to ask before
    writing a link. `claim` says why, which is what lets callers render the
    uncitable cases differently without re-deriving a SHA of their own.
    """

    claim: CommitClaim
    sha: str = ""

    @property
    def cited(self) -> bool:
        """Whether a commit may be named for this entry."""
        return bool(self.sha)


def attribute_commit(
    entry: CommentItem,
    cp: CommitPushResult,
    history: "AddressingHistory | None" = None,
    thread: ReportThread | None = None,
) -> CommitAttribution:
    """Which commit carries this entry's change — the one answer to that question.

    Every reviewer-facing surface reads this: the summary row, the thread
    reply, and the blob permalink under it. Four mechanisms used to answer it
    independently and three of them were wrong for some caller, because each
    was written for the caller that noticed first.

    An empty `entry.commit_sha` is not one case, it is four, and the whole
    reason this function exists is that its callers wanted contradictory
    things from it:

    - UNRECORDED — the pass recorded a commit and this entry is not stamped
      with it, so the entry was fixed in an earlier round or satisfied with no
      commit at all. Crediting the pass would name a commit that does not
      carry the change.
    - RESOLVED — the work landed outside the pass and `history` found the
      commit that changed this row's own line after the reviewer asked. That
      is per-row evidence, which is the thing reconciliation lacked.
    - UNDETERMINED — the same, with nothing at the row's line to go on, or a
      row :func:`handled_outside` recognises, which `history` declines to cite
      for.

    "Commits landed outside the pass" is a fact about the branch; "which commit
    carries this row" is a fact about the row. Reconciliation only ever knew
    the first, so it may not answer the second — stamping every row with the
    commit that happens to be HEAD names one picked for having no relationship
    to the row, and a branch that moved by exactly one commit makes that no
    truer. A row replayed from an earlier round is the case that proves it: its
    fix landed rounds ago, and the commit reconciliation recovered demonstrably
    does not carry it. The line history is the second kind of answer: the
    newest commit to touch the line a thread is anchored to is evidence about
    that thread, not a guess standing in for one.

    Passing no `history` keeps that decline, which is what a caller with no
    worktree to read can honestly claim.

    Callers decide how to render an entry with no citation. They do not get to
    invent one.
    """
    sha = getattr(entry, "commit_sha", "")
    pass_sha = cp.sha or ""
    published = cp.status == CommitStatus.PUSHED
    if sha and sha != pass_sha:
        # An earlier round's commit, and one the remote already has: this round
        # could only have read it back out of a state file the last one pushed.
        return CommitAttribution(CommitClaim.RECORDED, sha)
    if sha:
        # Stamped with the pass's own commit, so only the pass knows whether it
        # is safe to name yet.
        return CommitAttribution(CommitClaim.PASS, pass_sha if published else "")
    if cp.claim is CommitClaim.UNDETERMINED:
        # ceiling: one SHA per row, resolved from the one line the thread is
        # anchored to. A fix that also landed at a line the thread does not
        # point at is invisible here, and widening this to carry n commits
        # would not find it — the evidence is missing, not unrepresentable.
        # Upgrade when a row records a fingerprint of the change it fixed,
        # which is what would let a content search reach past the anchor.
        framing = history.framing(entry, thread) if history else AddressedFraming(False)
        if framing.in_response and framing.cited:
            return CommitAttribution(CommitClaim.RESOLVED, framing.sha)
        return CommitAttribution(CommitClaim.UNDETERMINED)
    if pass_sha:
        return CommitAttribution(CommitClaim.UNRECORDED)
    # The pass has no commit of its own and none landed outside it, so there is
    # nothing entry-specific to say and the pass-level status is the whole
    # story for this row.
    return CommitAttribution(CommitClaim.PASS)


def stamp_pass_commit(fixed: list[CommentItem], sha: str) -> None:
    """Record the pass's commit on the entries it just landed.

    The only place a *pass* attributes a thread to a commit — the `--settle`
    flag is the other writer of `commit_sha`, and it writes the operator's
    answer rather than the pass's. Everything downstream reads the field back
    through :func:`attribute_commit` instead of re-deriving a SHA from whichever
    pass happens to be running, which is how a row fixed three rounds ago came
    to be credited to today's commit.

    Idempotent by design, not by accident: an entry that already names a
    commit keeps it, so calling this again on the same entries (e.g. once
    before replying to fixed threads, again when the outcomes are built) is
    safe and a no-op on the second call. The reply queue spans rounds too, so
    a drained entry keeps the commit that actually fixed it.
    """
    if not sha:
        return
    for entry in fixed:
        if not getattr(entry, "commit_sha", ""):
            entry.commit_sha = sha


def stamp_read_sha(entries: list[CommentItem], sha: str) -> None:
    """Record the tree an entry's line numbers were read in.

    The single writer of `entry.read_sha`, and idempotent for the same reason
    :func:`stamp_pass_commit` is: the reply queue spans rounds, so an entry
    drained later must keep the tree its lines were actually read in rather
    than be relabelled with whichever head the draining round happens to be on.
    """
    if not sha:
        return
    for entry in entries:
        if not getattr(entry, "read_sha", ""):
            entry.read_sha = sha


def find_addressing_commit(
    wt_path: Path, filepath: str, line: int = 0,
) -> str | None:
    """The branch commit that last changed `line` of `filepath`, or None.

    `git log -1 -- <file>` answers a coarser question — the newest commit
    touching the file at all — so every thread on one file was stamped with
    the same SHA and at most one of them had landed there. `git log -L` traces
    the one line the thread is about, which is the change, not the file.

    A line is required, and no line means no citation. This resolves a commit
    for an entry no fix pass ever recorded one for, so there is nothing else to
    fall back to: the file-granular answer is the defect, not a safety net.
    """
    if not line:
        return None
    base = f"origin/{git_topology.default_branch_cached(wt_path)}"
    # A non-zero exit means the range does not resolve at HEAD — the file was
    # renamed away, or the line is past its end. Nothing here can say which
    # commit carried the change, so nothing is claimed, and the empty default
    # reads the same way as git answering with nothing.
    sha = git_client.out(
        "log", "-1", "--format=%H", "--no-patch",
        f"-L{line},{line}:{filepath}", f"{base}..HEAD", cwd=wt_path,
    )
    return sha if sha else None


def commit_timestamp(wt_path: Path, sha: str) -> float:
    """When `sha` was committed, in POSIX seconds, or 0.0 when unknown.

    The committer date, not the author date: a rebased or cherry-picked fix
    keeps the author date it was first written at, which would place work
    landed in response to a review before the review that asked for it.
    """
    try:
        return float(git_client.out("show", "-s", "--format=%ct", sha, cwd=wt_path))
    except ValueError:
        return 0.0


def thread_opened_at(thread: ReportThread | None) -> float:
    """When the reviewer opened `thread`, in POSIX seconds, or 0.0 when unknown.

    The first comment on the thread, not the newest. What a reply must not deny
    is that the branch moved after the reviewer asked; the later comments are
    routinely our own replies from the round that moved it, and reading one of
    those would place every fix before the question it answered.
    """
    if not thread or not thread.comments:
        return 0.0
    stamp = str(thread.comments[0].get("createdAt", "") or "")
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def handled_outside(entry: CommentItem) -> bool:
    """Whether this entry records work the fix pass did not land.

    The one question :meth:`AddressingHistory.framing` and the summary's fixed
    cell both ask before naming a commit for a row, so the summary cell, the
    thread reply and the permalink under it cannot answer it differently.

    Such a row can still name a commit that was *recorded* for it — the SHA
    --settle resolved, or the single commit that landed outside the pass — but
    never one inferred from its line history, and when nothing names one the
    cell says the work was handled rather than crediting whatever the running
    pass happened to commit.

    Answered from the provenance the record carries rather than from the wording
    of the reason beside it: the reason is written for a person, and a renderer
    matching on it is one rewording away from crediting a commit for work that
    commit does not contain.
    """
    return entry.settled_by is not SettledBy.PASS


@dataclass(frozen=True)
class AddressedFraming:
    """Whether a satisfied thread was satisfied in response to the reviewer.

    `in_response` is the whole reviewer-facing question. "The code does what
    you asked" is true either way; what separates the two is whether the code
    did it before the reviewer spoke, and only one of those readings tells them
    their comment needed no action.

    `sha` is the commit that made it true, when the branch can name one. It is
    populated only alongside a reading it actually supports — a commit older
    than the comment cannot be the one a fix pass just landed — so `cited` is
    the one question a renderer asks before writing a commit link.
    """

    in_response: bool
    sha: str = ""

    @property
    def cited(self) -> bool:
        """Whether a commit may be named for this entry."""
        return bool(self.sha)


class AddressingHistory:
    """When each satisfied thread's code became true, relative to the review.

    The triage verdict `already_addressed` reads current HEAD, which includes
    commits made earlier in the same review cycle — so a thread the pass fixed
    on round one comes back satisfied on round two, and a flat "already
    addressed" tells that reviewer their comment was moot. That is rejection,
    which is the opposite of what the verdict means.

    The reply and the summary row both ask this object the same question so the
    two cannot disagree, and it caches both git lookups because `git log -L` is
    a process per location and every surface asks about the same threads.
    """

    def __init__(self, wt_path: Path | None) -> None:
        self._wt_path = wt_path
        # Keyed by location rather than by file: two threads on one file are the
        # case this reply got wrong, so a per-file cache would re-impose the
        # answer `find_addressing_commit` was narrowed to stop giving.
        self._commits: dict[tuple[str, int], str] = {}
        self._committed_at: dict[str, float] = {}

    def framing(
        self,
        entry: CommentItem,
        thread: ReportThread | None,
        acted: bool = False,
    ) -> AddressedFraming:
        """How this entry's reply and summary row should read.

        `acted` is for the caller that already knows the pass fixed the entry
        and only came here because the resolver could not cite a commit for it.
        That knowledge outranks anything the branch can show, but it does not
        supply a citation: a commit predating the comment is not the one that
        carried a fix made after it, so the entry keeps the in-response reading
        and names nothing.

        An entry :func:`handled_outside` recognises names nothing either, for a
        different reason. `git log -L` answers what last changed the line, which
        is evidence about a row the pass itself fixed and about nothing else:
        the reason on such a row says the pass did not, and reconciliation
        stamps it from a thread merely looking settled on GitHub — resolution
        also covers answered, deferred and declined. Crediting the newest commit
        to touch a busy file to one of those contradicts the reply standing on
        the thread. How the row reads is untouched; only the commit link goes.
        """
        sha = self._commit_for(entry)
        landed_after = bool(sha) and self._postdates(sha, thread)
        if acted and not landed_after:
            return AddressedFraming(True)
        if handled_outside(entry):
            return AddressedFraming(landed_after)
        # A line no branch commit touched is code that predates the branch, and
        # so predates the review: that is the genuine already-addressed case and
        # the one the flat prefix is reserved for.
        return AddressedFraming(landed_after, sha)

    def _commit_for(self, entry: CommentItem) -> str:
        """The branch commit behind this entry's code, or "" when there is none."""
        if not self._wt_path:
            return ""
        # Triage's citation where there is one: it names the code that makes
        # the reviewer's point already true, which is the line whose history
        # answers "when did it become true?". A stored outcome carries no
        # citation, so it falls back to the location GitHub anchored the thread
        # at.
        evidence_file = getattr(entry, "evidence_file", "")
        evidence_line = int(getattr(entry, "evidence_line", 0) or 0)
        if evidence_file and evidence_line:
            where = (evidence_file, evidence_line)
        else:
            where = (entry.file, int(entry.line or 0))
        if where not in self._commits:
            self._commits[where] = find_addressing_commit(self._wt_path, *where) or ""
        return self._commits[where]

    def _postdates(self, sha: str, thread: ReportThread | None) -> bool:
        """Whether `sha` landed after the reviewer opened `thread`.

        Either timestamp missing reads as "no", which keeps the pre-existing
        prefix: claiming credit for a fix is the assertion that needs evidence,
        and there is none when the run cannot date one side of the comparison.
        """
        if sha not in self._committed_at:
            self._committed_at[sha] = commit_timestamp(self._wt_path, sha)
        landed_at = self._committed_at[sha]
        asked_at = thread_opened_at(thread)
        return bool(landed_at and asked_at) and landed_at > asked_at
