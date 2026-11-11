"""How a board row reads: the slots every PR view draws, decided once.

`present` turns a `Row` into a `Presentation`: the ref, title, state tags,
author, branch, stats text, problems, and the status slots (review running,
batch, CI, review decision), plus the ordered badge lists a card
(``badges``) and a compact row (``row_badges``) draw. Cards and compact rows
never carry a ``pass`` status; positive state (CI passing, Approved, a
finished batch) is for the detail's ``ci``, ``decision`` and ``busy`` slots.
*reviewing* and *batch* are what a client's terminals and batch run know;
the board document passes neither. ``ai/herdr/app/mods/_lib/pr_status.js``
``prPresentation`` is the client twin, held to the same output by
``tests/fixtures/board_presentation.json``.
"""

# doc-group: board

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import board.columns
from board.model import CiStatus, Issue, IssueKind, PrState, Row, StatusKind


class BadgeKind(StrEnum):
    STATUS = "status"
    TEXT = "text"
    TAG = "tag"


class BatchTone(StrEnum):
    """A batch item's card-state tone (``_lib/batch.js`` ``cardState``)."""

    FAILED = "failed"
    ATTENTION = "attention"
    BUSY = "busy"
    DONE = "done"


@dataclass(frozen=True)
class Badge:
    kind: BadgeKind
    status: StatusKind | None
    text: str


@dataclass(frozen=True)
class BatchState:
    text: str
    tone: BatchTone


@dataclass(frozen=True)
class Stats:
    additions: str
    deletions: str
    files: str


@dataclass(frozen=True)
class Presentation:
    ref: str
    title: str
    tags: tuple[str, ...]
    author: str
    branch: str
    base_branch: str
    stats: Stats
    issues: tuple[Badge, ...]
    review: Badge | None
    busy: Badge | None
    ci: Badge | None
    decision: Badge | None
    detail_issues: tuple[Badge, ...]
    badges: tuple[Badge, ...]
    row_badges: tuple[Badge, ...]


TONE_STATUS: dict[BatchTone, StatusKind] = {
    BatchTone.FAILED: StatusKind.FAIL,
    BatchTone.ATTENTION: StatusKind.WARN,
    BatchTone.BUSY: StatusKind.RUNNING,
    BatchTone.DONE: StatusKind.PASS,
}

DECISIONS: dict[str, tuple[StatusKind, str]] = {
    "APPROVED": (StatusKind.PASS, "Approved"),
    "CHANGES_REQUESTED": (StatusKind.WARN, "Changes requested"),
    "REVIEW_REQUIRED": (StatusKind.NONE, "Review required"),
}

CI_PASSING = "CI passing"
REVIEW_RUNNING = "Review running"
BATCH_PREFIX = "Batch · "
BATCH_WORKING = "working"

_CI_KINDS = frozenset({
    IssueKind.CI_FAILING,
    IssueKind.CI_RUNNING,
    IssueKind.CI_UNKNOWN,
    IssueKind.CI_PENDING,
})
_DETAIL_HIDDEN = _CI_KINDS | {IssueKind.CHANGES_REQUESTED}


def _status(kind: StatusKind, text: str) -> Badge:
    return Badge(kind=BadgeKind.STATUS, status=kind, text=text)


def _of(issue: Issue) -> Badge:
    return _status(issue.status, issue.label)


def stats_text(additions: int, deletions: int, files: int) -> Stats:
    """The stats row's three strings: ``+N``, ``-N`` and ``N files`` (``1 file``)."""
    noun = "file" if files == 1 else "files"
    return Stats(additions=f"+{additions}", deletions=f"-{deletions}", files=f"{files} {noun}")


def _ref(row: Row) -> str:
    if not row.repo:
        return f"#{row.number}"
    return f"{row.repo.rsplit('/', 1)[-1]}#{row.number}"


def _tags(row: Row) -> tuple[str, ...]:
    pairs = (
        ("Merged", row.state is PrState.MERGED),
        ("In merge queue", row.in_merge_queue),
        ("Draft", row.is_draft),
    )
    return tuple(name for name, on in pairs if on)


def _ci(row: Row, raw: tuple[Issue, ...]) -> Badge | None:
    if row.state is PrState.MERGED:
        return None
    issue = next((i for i in raw if i.kind in _CI_KINDS), None)
    if issue is not None:
        return _of(issue)
    if row.ci_status is CiStatus.PASSING:
        return _status(StatusKind.PASS, CI_PASSING)
    return None


def _decision(value: str | None) -> Badge | None:
    if not value:
        return None
    if value in DECISIONS:
        kind, text = DECISIONS[value]
        return _status(kind, text)
    words = value.lower().replace("_", " ")
    return _status(StatusKind.NONE, words[:1].upper() + words[1:])


def _busy(row: Row, batch: BatchState | None) -> Badge | None:
    if batch is not None:
        return _status(TONE_STATUS[batch.tone], BATCH_PREFIX + batch.text)
    if row.busy:
        return _status(StatusKind.RUNNING, BATCH_PREFIX + BATCH_WORKING)
    return None


def _reviews(count: int) -> tuple[Badge, ...]:
    if count == 0:
        return ()
    noun = "review" if count == 1 else "reviews"
    return (Badge(kind=BadgeKind.TEXT, status=None, text=f"{count} {noun}"),)


def present(
    row: Row,
    *,
    reviewing: bool = False,
    batch: BatchState | None = None,
) -> Presentation:
    """*row*'s slots; *reviewing* and *batch* come from a client, never the document."""
    raw = board.columns.issues(row)
    issues = tuple(_of(issue) for issue in raw)
    review = _status(StatusKind.RUNNING, REVIEW_RUNNING) if reviewing else None
    busy = _busy(row, batch)
    live = tuple(
        badge for badge in (review, busy)
        if badge is not None and badge.status is not StatusKind.PASS
    )
    labels = tuple(Badge(kind=BadgeKind.TAG, status=None, text=name) for name in row.labels[:2])
    return Presentation(
        ref=_ref(row),
        title=row.title,
        tags=_tags(row),
        author=row.author,
        branch=row.branch,
        base_branch=row.base_branch,
        stats=stats_text(row.additions, row.deletions, row.changed_files),
        issues=issues,
        review=review,
        busy=busy,
        ci=_ci(row, raw),
        decision=_decision(row.review_decision),
        detail_issues=tuple(_of(i) for i in raw if i.kind not in _DETAIL_HIDDEN),
        badges=issues + live + _reviews(len(row.review_runs)) + labels,
        row_badges=issues + live,
    )
