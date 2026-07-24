"""Typed domain objects for PR review thread processing.

Persistence-oriented structures live in pr_state.py; these model the
runtime pipeline: triage, classification, tracking, and fix-pass results.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import serde
from pr_state import ThreadAction, ThreadOutcome


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

    def __post_init__(self) -> None:
        self.line = int(self.line or 0)
        self.index = int(self.index or 0)

    def to_outcome(
        self, action: ThreadAction, reason: str = "",
    ) -> ThreadOutcome:
        return ThreadOutcome(
            id=self.id,
            file=self.file,
            line=self.line,
            reviewer=self.reviewer,
            summary=self.summary,
            action=action.value,
            reason=reason or self.reason or self.reasoning,
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


# ── Fix tracking types ────────────────────────────────────────────────────


@dataclass
class TrackingResult:
    """Results from parsing the fix-tracking markdown file."""

    fixed: list[CommentItem] = field(default_factory=list)
    deferred: list[CommentItem] = field(default_factory=list)
    fixed_items: list[CommentItem] = field(default_factory=list)
    deferred_items: list[CommentItem] = field(default_factory=list)


# ── Report types ──────────────────────────────────────────────────────────


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


# ── Fix pass result types ─────────────────────────────────────────────────


@dataclass
class CommentFixResult:
    """Complete results from a comment fix pass."""

    fixed: list[CommentItem] = field(default_factory=list)
    needs_human: list[CommentItem] = field(default_factory=list)
    dismissed: list[CommentItem] = field(default_factory=list)
    deferred: list[CommentItem] = field(default_factory=list)
    commit_sha: str | None = None
    commit_status: str = ""
    replies_posted: int = 0
    summary_url: str | None = None
    summary_deferred: bool = False
    max_turns: int = 0
    max_budget: float = 0.0


# ── Deserialization helpers ───────────────────────────────────────────────


def triage_result_from_dict(d: dict) -> TriageResult:
    """Parse AI triage JSON output into typed structures."""
    threads = [serde.from_dict(CommentItem, t) for t in d.get("threads", [])]
    items = [serde.from_dict(CommentItem, it) for it in d.get("comment_items", [])]
    stats = serde.from_dict(TriageStats, d.get("stats", {}))
    return TriageResult(threads=threads, comment_items=items, stats=stats)
