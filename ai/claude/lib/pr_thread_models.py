"""Typed domain objects for PR review thread processing.

Replaces the raw dicts and tuples that flow through review-threads and
its dependencies. Persistence-oriented structures live in pr_state.py;
these model the runtime pipeline: triage, classification, tracking, and
fix-pass results.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import serde
from pr_state import ThreadAction, ThreadOutcome


# ── Core entry types ────────────────────────────────────────────────────────


@dataclass
class ThreadEntry:
    """A review thread entry with identity and location.

    Pre-action counterpart of ThreadOutcome — does not carry action/reason.
    """

    thread_id: str = ""
    file: str = ""
    line: int = 0
    reviewer: str = ""
    summary: str = ""

    def to_outcome(
        self, action: ThreadAction, reason: str = "",
    ) -> ThreadOutcome:
        return ThreadOutcome(
            thread_id=self.thread_id,
            file=self.file,
            line=self.line,
            reviewer=self.reviewer,
            summary=self.summary,
            action=action.value,
            reason=reason,
        )


@dataclass
class ClassifiedEntry(ThreadEntry):
    """A ThreadEntry that has been classified with an action reason."""

    reason: str = ""
    reasoning: str = ""

    def to_outcome(
        self, action: ThreadAction, reason: str = "",
    ) -> ThreadOutcome:
        return ThreadOutcome(
            thread_id=self.thread_id,
            file=self.file,
            line=self.line,
            reviewer=self.reviewer,
            summary=self.summary,
            action=action.value,
            reason=reason or self.reason or self.reasoning,
        )


# ── AI triage output types ─────────────────────────────────────────────────


@dataclass
class TriageEntry:
    """A single thread's triage classification from AI output."""

    id: str = ""
    state: str = ""
    classification: str = ""
    verification: str = ""
    complexity: str = ""
    reasoning: str = ""
    file: str = ""
    line: int = 0
    reviewer: str = ""
    summary: str = ""

    def __post_init__(self) -> None:
        self.line = int(self.line or 0)

    def to_thread_entry(self) -> ThreadEntry:
        return ThreadEntry(
            thread_id=self.id,
            file=self.file,
            line=self.line,
            reviewer=self.reviewer,
            summary=self.summary,
        )


@dataclass
class CommentItem:
    """A decomposed item from a top-level PR comment, classified by AI."""

    id: str = ""
    source_id: str = ""
    source_type: str = ""
    index: int = 0
    reviewer: str = ""
    classification: str = ""
    verification: str = ""
    complexity: str = ""
    reasoning: str = ""
    summary: str = ""
    file: str = ""
    line: int = 0
    body: str = ""

    def __post_init__(self) -> None:
        self.line = int(self.line or 0)
        self.index = int(self.index or 0)

    def to_thread_entry(self) -> ThreadEntry:
        return ThreadEntry(
            thread_id=self.id,
            file=self.file,
            line=self.line,
            reviewer=self.reviewer,
            summary=self.summary,
        )


# ── Triage result types ────────────────────────────────────────────────────


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

    threads: list[TriageEntry] = field(default_factory=list)
    comment_items: list[CommentItem] = field(default_factory=list)
    stats: TriageStats = field(default_factory=TriageStats)


@dataclass
class ClassificationResult:
    """Result of classifying triage entries into action categories.

    fixable contains the raw TriageEntry/CommentItem objects (downstream
    consumers like _build_tracking_file need the full AI fields).
    """

    fixable: list = field(default_factory=list)
    needs_human: list[ClassifiedEntry] = field(default_factory=list)
    dismissed: list[ClassifiedEntry] = field(default_factory=list)


# ── Fix tracking types ──────────────────────────────────────────────────────


@dataclass
class TrackingResult:
    """Results from parsing the fix-tracking markdown file."""

    fixed: list[ThreadEntry] = field(default_factory=list)
    deferred: list[ClassifiedEntry] = field(default_factory=list)
    fixed_items: list[ThreadEntry] = field(default_factory=list)
    deferred_items: list[ClassifiedEntry] = field(default_factory=list)
    reconciled_count: int = 0


# ── Report types ────────────────────────────────────────────────────────────


@dataclass
class ReportThread:
    """A thread in the PR report, combining GitHub data with lifecycle state."""

    id: str = ""
    state: str = ""
    classification: str | None = None
    reviewer: str = ""
    comments: list[dict] = field(default_factory=list)
    is_resolved: bool = False
    file: str = ""
    line: int | None = None


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


# ── Fix pass result types ───────────────────────────────────────────────────


@dataclass
class CommentItemResults:
    """Breakdown of fix results for decomposed comment items."""

    fixed: list[ThreadEntry] = field(default_factory=list)
    needs_human: list[ClassifiedEntry] = field(default_factory=list)
    dismissed: list[ClassifiedEntry] = field(default_factory=list)
    deferred: list[ClassifiedEntry] = field(default_factory=list)


@dataclass
class FixPassResult:
    """Complete results from a fix pass."""

    fixed: list[ThreadEntry] = field(default_factory=list)
    needs_human: list[ClassifiedEntry] = field(default_factory=list)
    dismissed: list[ClassifiedEntry] = field(default_factory=list)
    deferred: list[ClassifiedEntry] = field(default_factory=list)
    commit_sha: str | None = None
    commit_status: str = ""
    replies_posted: int = 0
    summary_url: str | None = None
    summary_deferred: bool = False
    reconciled_count: int = 0
    max_turns: int = 0
    max_budget: float = 0.0
    comment_items: CommentItemResults = field(default_factory=CommentItemResults)


# ── Deserialization helpers ─────────────────────────────────────────────────


def triage_result_from_dict(d: dict) -> TriageResult:
    """Parse AI triage JSON output into typed structures."""
    threads = [serde.from_dict(TriageEntry, t) for t in d.get("threads", [])]
    items = [serde.from_dict(CommentItem, it) for it in d.get("comment_items", [])]
    stats = serde.from_dict(TriageStats, d.get("stats", {}))
    return TriageResult(threads=threads, comment_items=items, stats=stats)
