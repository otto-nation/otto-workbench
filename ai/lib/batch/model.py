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
    CI = "ci"
    COMMENTS = "comments"
    REVIEW = "review"


STEP_ORDER: tuple[Step, ...] = (Step.REBASE, Step.CI, Step.COMMENTS, Step.REVIEW)


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    SKIPPED = "skipped"
    # classify still sets this; a schema-2 run records findings instead of opening a decision.
    NEEDS_DECISION = "needs_decision"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class ItemStatus(StrEnum):
    QUEUED = "queued"
    WAITING_ADMISSION = "waiting_admission"
    RUNNING = "running"
    # Load-only: schema-2 runs record needs_session instead of opening a decision.
    AWAITING_DECISION = "awaiting_decision"
    READY_TO_PUBLISH = "ready_to_publish"
    DONE = "done"
    NEEDS_SESSION = "needs_session"
    DROPPED = "dropped"
    SKIPPED_CLOSED = "skipped_closed"


class RunStatus(StrEnum):
    RUNNING = "running"
    # Load-only: schema-2 runs never wait on a person.
    WAITING = "waiting"
    DONE = "done"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class DecisionKind(StrEnum):
    COMMENT_ITEM = "comment_item"
    REBASE_CONFLICT = "rebase_conflict"
    REBASE_REFUSED = "rebase_refused"
    OPEN_FINDINGS = "open_findings"
    # One per (item, step) needing a person; payload {"evidence": [...]}.
    # OPEN_FINDINGS is no longer emitted and stays only so older state files load.
    STEP_REVIEW = "step_review"
    DIRTY_WORKTREE = "dirty_worktree"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    PUBLISH = "publish"


class DirtyReason(StrEnum):
    """Why a `dirty_worktree` decision's worktree may not take a step: its payload `reason`."""

    REBASE_IN_PROGRESS = "rebase_in_progress"
    DIRTY = "dirty"


class EvidenceKind(StrEnum):
    """What a `step_review` decision's evidence entries are, and where each is read.

    open_findings — review.md's open findings; checks_unverified — a Fix-Checks
    trailer of red/timed_out/error on the step's commits; ci_unfixed — ci-check's
    stdout tally; one_sided — `pr rebase`'s stdout report; stale — `pr rebase`'s
    stdout `files_stale` (generated files taken from the base and not rebuilt);
    stacked_on — another item in the run whose publish goes first;
    ci_failing — read-only ci-check's failing jobs.
    """

    OPEN_FINDINGS = "open_findings"
    CHECKS_UNVERIFIED = "checks_unverified"
    CI_UNFIXED = "ci_unfixed"
    CI_FAILING = "ci_failing"
    ONE_SIDED = "one_sided"
    STALE = "stale"
    STACKED_ON = "stacked_on"


class FindingReason(StrEnum):
    """Why a PR still needs a focused session. Persisted on the item in schema v2."""

    CONFLICT_HANDWRITTEN = "conflict_handwritten"
    CONFLICT_GENERATED = "conflict_generated"
    STALE_GENERATED = "stale_generated"
    STACK_WAIT_BASE = "stack_wait_base"
    STACK_CARRIES_BASE_COMMITS = "stack_carries_base_commits"
    WORKTREE_COLLISION = "worktree_collision"
    DIRTY_WORKTREE = "dirty_worktree"
    WRONG_BRANCH = "wrong_branch"
    CI_FAILING = "ci_failing"
    REVIEW_FINDINGS = "review_findings"
    THREADS_NEED_HUMAN = "threads_need_human"
    CLOSEOUT_OWED = "closeout_owed"
    READY_TO_PUSH = "ready_to_push"
    PUSH_REFUSED = "push_refused"
    BUDGET_EXHAUSTED = "budget_exhausted"
    FAILED_UNKNOWN = "failed_unknown"


class Severity(StrEnum):
    BLOCK = "block"
    WAIT = "wait"
    ACT = "act"


SEVERITY: dict[FindingReason, Severity] = {
    FindingReason.CONFLICT_HANDWRITTEN: Severity.BLOCK,
    FindingReason.CONFLICT_GENERATED: Severity.BLOCK,
    FindingReason.STALE_GENERATED: Severity.BLOCK,
    FindingReason.STACK_WAIT_BASE: Severity.WAIT,
    FindingReason.STACK_CARRIES_BASE_COMMITS: Severity.BLOCK,
    FindingReason.WORKTREE_COLLISION: Severity.BLOCK,
    FindingReason.DIRTY_WORKTREE: Severity.BLOCK,
    FindingReason.WRONG_BRANCH: Severity.BLOCK,
    FindingReason.CI_FAILING: Severity.ACT,
    FindingReason.REVIEW_FINDINGS: Severity.BLOCK,
    FindingReason.THREADS_NEED_HUMAN: Severity.ACT,
    FindingReason.CLOSEOUT_OWED: Severity.ACT,
    FindingReason.READY_TO_PUSH: Severity.ACT,
    FindingReason.PUSH_REFUSED: Severity.BLOCK,
    FindingReason.BUDGET_EXHAUSTED: Severity.BLOCK,
    FindingReason.FAILED_UNKNOWN: Severity.BLOCK,
}


@dataclass
class Finding:
    """One reason a PR still needs a person.

    Persisted on `Item.findings` in schema 2 (`log_path` is the stored key).
    `to_dict()` stays the report shape (`log`). `core.serde` round-trips it.
    """

    reason: FindingReason
    step: str
    detail: str = ""
    files: list[str] = field(default_factory=list)
    log_path: str = ""
    data: dict = field(default_factory=dict)
    created_at: str = ""

    def to_dict(self) -> dict:
        return {"reason": self.reason.value, "step": self.step, "detail": self.detail,
                "files": list(self.files), "data": dict(self.data),
                "log": self.log_path or None}


def severity_of(finding: Finding) -> Severity:
    raw = finding.data.get("severity")
    if raw:
        return Severity(raw)
    return SEVERITY[finding.reason]


TERMINAL_ITEM = frozenset({
    ItemStatus.DONE, ItemStatus.NEEDS_SESSION, ItemStatus.DROPPED, ItemStatus.SKIPPED_CLOSED,
})

# The one owner of "a run that has ended": statuses a run never leaves.
# store (refs drop on commit), inbox (settle_idle), gc (removal candidates),
# report and the CLI all read this set rather than spelling it out.
FINISHED_RUN = frozenset({RunStatus.DONE, RunStatus.CANCELLED})


@dataclass
class CancelState:
    kill: bool = False
    requested_at: str = ""


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
    # HEAD when the step first started, kept across retries: work an earlier attempt
    # committed — even one interrupted before it finished — stays work the publish owes.
    # Empty in state written before it existed, which means "this attempt's start".
    start_head: str = ""
    # Named by --select: an instruction, so admission never skips it as not needed.
    explicit: bool = False
    # The non-merge commits each attempt added (`head_before..HEAD`, full SHAs):
    # what publish checks the branch still carries. Never recorded for the
    # rebase step, whose commits are replays of ones already on the branch.
    # Cleared wherever `drafted` is.
    commits: list[str] = field(default_factory=list)


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


def stacked_on_items(decision: Decision) -> list[str]:
    """Item keys a decision's `stacked_on` evidence names."""
    return [e.get("item", "") for e in decision.payload.get("evidence", [])
            if e.get("kind") == EvidenceKind.STACKED_ON.value]


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
    # Load-only: comment item ids a v1 publish decision tracked.
    track: list[str] = field(default_factory=list)
    wait_reason: str = ""
    # Local HEAD moved during this run, so a self-review is owed whatever GitHub's head says.
    head_moved: bool = False
    # The PR head GitHub reported when the batch planned this item: the commit
    # the batch worked from, and so the lease its publish pushes under. Empty
    # only in runs saved before the field existed; see __post_init__.
    remote_sha: str = ""
    # The key of another item in this run whose branch is this PR's base; its publish goes first.
    stacked_on: str = ""
    # The PR's base branch from the plan: publish bounds its search for remote
    # commits the local branch dropped at the merge-base with origin's copy.
    base_ref: str = ""
    # The tip a batch rebase of this item started from, from the rebase's own
    # report: what `undo` resets to, and what publish checks holds remote_sha.
    pre_rebase_head: str = ""
    # The head a batch publish pushed, or "" when nothing was pushed.
    published_sha: str = ""
    # Load-only: commits a v1 force-publish confirmed past.
    forced: list[str] = field(default_factory=list)
    # The origin key of the item's checkout, recorded at plan time.
    # Empty in runs saved before it existed.
    repo_key: str = ""
    # Copied from PlanRow.is_draft so the report can require a comment before
    # a hand push on a non-draft PR. Additive; older state loads as False.
    is_draft: bool = False
    # Schema-2 recordings; empty in v1 state and until a v2 run records one.
    findings: list[Finding] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.remote_sha:
            self.remote_sha = self.head_sha

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
    # Load-only for v1 runs; schema 2 uses auto_publish_ff.
    auto_publish: list[Step]
    status: RunStatus = RunStatus.RUNNING
    trail_root: str = ""
    items: list[Item] = field(default_factory=list)
    # Load-only: schema-2 runs record findings and never open a decision.
    decisions: list[Decision] = field(default_factory=list)
    # Skip the pre-push hook on publish pushes (`pr batch run --no-verify`).
    no_verify: bool = False
    # The operator focus every review step is given (`pr batch run --focus`),
    # persisted so resume replays it. Additive; the enum-value rule in the
    # module docstring does not apply.
    focus: list[str] = field(default_factory=list)
    # The plan's private ref namespace and the repos it was fetched into;
    # dropped when the run reaches a terminal state.
    ref_namespace: str = ""
    ref_dirs: list[str] = field(default_factory=list)
    cancel: CancelState | None = None
    schema_version: int = 1
    # None is a run saved before this field existed and means every step runs
    # with AI. An empty list is a default unattended run: no step uses AI.
    with_ai: list[Step] | None = None
    # --auto-publish ff. auto_publish (list[Step]) is load-only for v1 runs.
    auto_publish_ff: bool = False

    def uses_ai(self, step: Step) -> bool:
        return self.with_ai is None or step in self.with_ai

    def item(self, key: str) -> Item:
        for it in self.items:
            if it.key == key:
                return it
        raise KeyError(key)

    def open_decisions(self, item_key: str | None = None) -> list[Decision]:
        return [d for d in self.decisions
                if d.open and (item_key is None or d.item == item_key)]
