"""The persisted shape of a `pr batch` run.

Enum string values are written to state files and NDJSON events, so they are
stable: renaming a member is free, changing a value is a schema break and
bumps `Run.schema_version`.
"""

# doc-group: batch

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Step(StrEnum):
    REBASE = "rebase"
    COMMENTS = "comments"
    REVIEW = "review"


STEP_ORDER: tuple[Step, ...] = (Step.REBASE, Step.COMMENTS, Step.REVIEW)


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    SKIPPED = "skipped"
    NEEDS_DECISION = "needs_decision"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class ItemStatus(StrEnum):
    QUEUED = "queued"
    WAITING_ADMISSION = "waiting_admission"
    RUNNING = "running"
    AWAITING_DECISION = "awaiting_decision"
    READY_TO_PUBLISH = "ready_to_publish"
    DONE = "done"
    DROPPED = "dropped"
    SKIPPED_CLOSED = "skipped_closed"


class RunStatus(StrEnum):
    RUNNING = "running"
    WAITING = "waiting"
    DONE = "done"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class DecisionKind(StrEnum):
    COMMENT_ITEM = "comment_item"
    REBASE_CONFLICT = "rebase_conflict"
    REBASE_REFUSED = "rebase_refused"
    OPEN_FINDINGS = "open_findings"
    DIRTY_WORKTREE = "dirty_worktree"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    PUBLISH = "publish"


TERMINAL_ITEM = frozenset({ItemStatus.DONE, ItemStatus.DROPPED, ItemStatus.SKIPPED_CLOSED})


@dataclass
class StepRecord:
    step: Step
    status: StepStatus = StepStatus.PENDING
    exit_code: int | None = None
    started_at: str = ""
    ended_at: str = ""
    log_path: str = ""
    # Ran without publishing; its push/post is owed to the item's publish decision.
    drafted: bool = False
    # Named by --select: an instruction, so admission never skips it as not needed.
    explicit: bool = False


@dataclass
class Decision:
    id: str
    item: str
    # A Step value, or "worktree" / "publish" for decisions outside the steps.
    step: str
    kind: DecisionKind
    payload: dict = field(default_factory=dict)
    created_at: str = ""
    resolution: str = ""
    resolved_at: str = ""

    @property
    def open(self) -> bool:
        return not self.resolution


@dataclass
class Item:
    key: str
    repo: str
    repo_dir: str
    pr: int
    branch: str
    head_sha: str
    worktree: str = ""
    status: ItemStatus = ItemStatus.QUEUED
    steps: list[StepRecord] = field(default_factory=list)
    # Comment item ids the operator chose to file as tracking issues at publish.
    track: list[str] = field(default_factory=list)
    wait_reason: str = ""
    # Local HEAD moved during this run, so a self-review is owed whatever GitHub's head says.
    head_moved: bool = False

    def step(self, step: Step) -> StepRecord:
        for rec in self.steps:
            if rec.step is step:
                return rec
        raise KeyError(f"{self.key} has no {step.value} step")

    def has(self, step: Step) -> bool:
        return any(rec.step is step for rec in self.steps)

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_ITEM


@dataclass
class Run:
    id: str
    started_at: str
    steps: list[Step]
    pool: int
    auto_publish: list[Step]
    status: RunStatus = RunStatus.RUNNING
    trail_root: str = ""
    items: list[Item] = field(default_factory=list)
    decisions: list[Decision] = field(default_factory=list)
    schema_version: int = 1

    def item(self, key: str) -> Item:
        for it in self.items:
            if it.key == key:
                return it
        raise KeyError(key)

    def decision(self, decision_id: str) -> Decision:
        for d in self.decisions:
            if d.id == decision_id:
                return d
        raise KeyError(decision_id)

    def open_decisions(self, item_key: str | None = None) -> list[Decision]:
        return [d for d in self.decisions
                if d.open and (item_key is None or d.item == item_key)]
