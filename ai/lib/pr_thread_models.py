"""Typed domain objects for PR review thread processing.

Persistence-oriented structures live in pr_domains.py and pr_comments_fix.py;
these model the runtime pipeline: triage, classification, tracking, and
fix-pass results.
"""

# doc-group: publishing

from __future__ import annotations

from dataclasses import dataclass, field

import serde
from pr_comments_state import ThreadState
from pr_fix import FixOutcome, ItemOutcome


# ── Core types ─────────────────────────────────────────────────────────────


@dataclass
class CommentItem:
    """A PR review comment at any pipeline stage.

    Covers inline review threads, decomposed top-level comment items,
    and post-classification entries. Fields unused at a given stage
    default to empty values.
    """

    id: str = ""
    file: str = ""
    line: int = 0
    reviewer: str = ""
    summary: str = ""
    reason: str = ""
    reasoning: str = ""
    state: str = ""
    source_id: str = ""
    source_type: str = ""
    index: int = 0
    classification: str = ""
    verification: str = ""
    complexity: str = ""
    body: str = ""
    # Where in the tree the verdict can be checked. A verdict posted back to a
    # reviewer has to point at code, so triage cites the location it read.
    evidence_file: str = ""
    evidence_line: int = 0
    # The commit that fixed this entry, when it is known to be an older one than
    # the pass now running. Set when an entry is rebuilt from an ItemOutcome to
    # drain a deferred reply queue; empty for an entry the current pass fixed,
    # which the pass's own SHA covers.
    commit_sha: str = ""
    # The tree `line` and `evidence_line` were read in. A line number is a
    # coordinate in one tree and means nothing in another, so a permalink into a
    # different commit has to drop its anchor rather than send the reviewer to
    # whatever code inherited the number. Empty means "not recorded", which
    # reads as "cannot anchor".
    read_sha: str = ""

    def __post_init__(self) -> None:
        self.line = int(self.line or 0)
        self.index = int(self.index or 0)
        self.evidence_line = int(self.evidence_line or 0)

    def has_evidence(self) -> bool:
        """Whether this item cites a location a permalink can point at."""
        return bool(self.evidence_file) and self.evidence_line > 0

    def to_outcome(
        self, outcome: FixOutcome, reason: str = "",
    ) -> ItemOutcome:
        """This entry as the record writes it — everything but the reviewer.

        The login is the item as GitHub handed it over rather than a fact about
        what the pass did, so it is recorded beside the record in
        `FixSummary.reviewers` and does not travel here. `from_outcome` is what
        puts the two back together.
        """
        return ItemOutcome(
            id=self.id,
            file=self.file,
            line=self.line,
            summary=self.summary,
            outcome=outcome,
            reason=reason or self.reason or self.reasoning,
            commit_sha=self.commit_sha,
            read_sha=self.read_sha,
        )

    @classmethod
    def from_outcome(
        cls, outcome: ItemOutcome, reviewer: str = "", *, reason_field: str = "reason",
    ) -> "CommentItem":
        """A recorded outcome as an entry again, for a surface that renders one.

        The replay path for everything `--finish` picks up out of state: the
        reply queue, the deferred tracking issue, the re-rendered summary. Each
        of those reads entries rather than records, so a round drained from disk
        goes through here and every renderer downstream sees one type.

        `reason_field` is which of the two the outcome's reason lands in. The
        reply templates read `reasoning` — a dismissal's justification is the
        body of the reply — while the summary table and the tracking issue read
        `reason`, so the caller names the one its surface will look at rather
        than filling both and letting a renderer pick the wrong half.
        """
        return cls(
            id=outcome.id,
            file=outcome.file,
            line=outcome.line,
            reviewer=reviewer,
            summary=outcome.summary,
            commit_sha=outcome.commit_sha,
            read_sha=outcome.read_sha,
            **{reason_field: outcome.reason},
        )


# ── Triage result types ──────────────────────────────────────────────────


@dataclass
class TriageStats:
    total: int = 0
    actionable: int = 0
    questions: int = 0
    approvals: int = 0
    conflicting: int = 0
    valid: int = 0
    invalid: int = 0
    comment_items_total: int = 0
    comment_items_actionable: int = 0


@dataclass
class TriageResult:
    """Complete triage classification output from AI."""

    threads: list[CommentItem] = field(default_factory=list)
    comment_items: list[CommentItem] = field(default_factory=list)
    stats: TriageStats = field(default_factory=TriageStats)


@dataclass
class ClassificationResult:
    """Result of classifying triage entries into action categories.

    fixable contains the raw CommentItem objects (downstream consumers
    like _build_tracking_file need the full AI fields).
    """

    fixable: list = field(default_factory=list)
    needs_human: list[CommentItem] = field(default_factory=list)
    dismissed: list[CommentItem] = field(default_factory=list)
    already_addressed: list[CommentItem] = field(default_factory=list)


# ── Fix tracking types ────────────────────────────────────────────────────


@dataclass
class TrackingResult:
    """What the fix agent recorded, sorted back onto the entries it was handed.

    Keyed by :class:`~pr_fix.FixOutcome` rather than held in a field per verdict:
    the vocabulary belongs to the tracking file, and a named field for each of
    its members would have to grow here every time that file learns to say
    something new. Two dicts rather than one because only an inline thread has
    somewhere to reply — the entries decomposed out of top-level comments are
    reported on but never replied to, so every surface downstream needs the two
    apart or together on demand rather than merged on the way in.
    """

    threads: dict[FixOutcome, list[CommentItem]] = field(default_factory=dict)
    items: dict[FixOutcome, list[CommentItem]] = field(default_factory=dict)

    def _side(self, item: bool) -> dict[FixOutcome, list[CommentItem]]:
        return self.items if item else self.threads

    def bucket(self, outcome: FixOutcome, *, item: bool = False) -> list[CommentItem]:
        """The entries recorded under one outcome, threads by default."""
        return self._side(item).get(outcome, [])

    def both(self, outcome: FixOutcome) -> list[CommentItem]:
        """Threads and comment items alike, for a surface that treats them the same."""
        return self.bucket(outcome) + self.bucket(outcome, item=True)

    def add(self, outcome: FixOutcome, entry: CommentItem, *, item: bool = False) -> None:
        """Record one entry under one outcome."""
        self._side(item).setdefault(outcome, []).append(entry)

    def merge(self, other: TrackingResult) -> None:
        """Accumulate another result's entries into this one."""
        for item, side in ((False, other.threads), (True, other.items)):
            for outcome, entries in side.items():
                self._side(item).setdefault(outcome, []).extend(entries)

    def drop(self, outcome: FixOutcome) -> None:
        """Forget everything recorded under one outcome, threads and items both.

        What a retry supersedes: the entries it was handed have been decided
        again, and keeping the first answer beside the second would report both.
        """
        self.threads.pop(outcome, None)
        self.items.pop(outcome, None)


# ── Report types ──────────────────────────────────────────────────────────


@dataclass
class ReportThread:
    """A thread in the PR report, combining GitHub data with lifecycle state."""

    id: str = ""
    state: ThreadState = ThreadState.NEW
    classification: str | None = None
    reviewer: str = ""
    comments: list[dict] = field(default_factory=list)
    is_resolved: bool = False
    file: str = ""
    line: int | None = None
    # The login that makes a comment on this thread ours. Carried per thread
    # because the reply upsert decides edit-vs-post from comment authorship,
    # several call layers below the PRReport that knows the login. Empty means
    # "cannot tell", which the upsert reads as post rather than edit.
    my_login: str = ""


@dataclass
class PRReport:
    """Assembled PR report passed between pipeline stages."""

    repo: str = ""
    pr_number: int = 0
    my_login: str = ""
    threads: list[ReportThread] = field(default_factory=list)
    issue_comments: list[dict] = field(default_factory=list)
    review_body_comments: list[dict] = field(default_factory=list)
    verdicts: list[dict] = field(default_factory=list)


# ── Fix pass result types ─────────────────────────────────────────────────


@dataclass
class CommentFixResult:
    """Complete results from a comment fix pass."""

    fixed: list[CommentItem] = field(default_factory=list)
    needs_human: list[CommentItem] = field(default_factory=list)
    dismissed: list[CommentItem] = field(default_factory=list)
    already_addressed: list[CommentItem] = field(default_factory=list)
    deferred: list[CommentItem] = field(default_factory=list)
    commit_sha: str | None = None
    commit_status: str = ""
    replies_posted: int = 0
    summary_url: str | None = None
    summary_deferred: bool = False
    # Per-batch, not pass-wide: the fix pass runs one agent invocation per batch
    # of items, each with its own budget. `batches` is what makes these readable.
    max_turns: int = 0
    max_budget: float = 0.0
    batches: int = 0


# ── Deserialization helpers ───────────────────────────────────────────────


def _lenient_from_dict(cls, raw):
    """`serde.from_dict`, but a wrong-shaped raw value defaults instead of raising.

    This reads AI-generated triage JSON, which is malformed occasionally by
    nature — a thread entry that comes back as a bare string, `stats` as an
    empty list. `serde.from_dict` rejects a non-dict top level with
    `TypeError` so a state file can be discarded; here there is no file to
    discard, only one entry in a batch, and the caller — a single triage
    pass — should not crash for the whole PR over one malformed field.
    Neither `CommentItem` nor `TriageStats` has a required field, so the only
    way this raises is the non-dict case, not a missing-field one.
    """
    try:
        return serde.from_dict(cls, raw)
    except TypeError:
        return cls()


def _lenient_list(raw):
    """The list behind a triage key, or an empty one if it is anything else.

    `d.get(key, [])` only falls back when the key is absent, and the model
    emits the key with an explicit `null` often enough that iterating the
    result is its own crash — one the per-entry wrapper below cannot catch,
    because it never gets called. A scalar is no more iterable than `None`.
    """
    return raw if isinstance(raw, list) else []


def triage_result_from_dict(d: dict) -> TriageResult:
    """Parse AI triage JSON output into typed structures."""
    threads = [_lenient_from_dict(CommentItem, t) for t in _lenient_list(d.get("threads"))]
    items = [_lenient_from_dict(CommentItem, it) for it in _lenient_list(d.get("comment_items"))]
    stats = _lenient_from_dict(TriageStats, d.get("stats", {}))
    return TriageResult(threads=threads, comment_items=items, stats=stats)
