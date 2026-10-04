"""Turn what a finished step left behind into a step status and decisions."""

# doc-group: batch

from __future__ import annotations

import json
import re
from dataclasses import dataclass

import pr.fix
import pr.state
import pr.target
import review.document
import review.paths
from batch.model import DecisionKind, Item, Step, StepStatus
from rebase.types import CONFLICTS_EXIT, REFUSAL_EXIT
from review.types import severity_by_key

# The phrase core.run_lock.report_busy prints; confirmed in Task 0 Step 5.
LOCK_BUSY_MARKER = "another pr run already owns this"
_OWED = (pr.fix.FixOutcome.NEEDS_HUMAN, pr.fix.FixOutcome.DEFERRED)
_SYNTHETIC_ID = re.compile(r"^(ic|rb)-\d+-\d+$")


@dataclass(frozen=True)
class DecisionDraft:
    kind: DecisionKind
    payload: dict


@dataclass(frozen=True)
class StepResult:
    status: StepStatus
    decisions: list[DecisionDraft]
    # The tip a completed rebase started from, from its stdout report.
    pre_rebase_head: str = ""


def _json(stdout: str) -> dict | None:
    try:
        value = json.loads(stdout)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _rebase_payload(stdout: str, log_tail: list[str]) -> dict:
    parsed = _json(stdout)
    return parsed if parsed is not None else {"log_tail": log_tail}


def _load_pr_state(item: Item):
    try:
        key = pr.target.repo_key_from_origin(item.worktree or item.repo_dir)
    except OSError:
        return None
    return pr.state.load_state(pr.target.target_dir(key, item.branch)) if key else None


def recorded_pre_rebase_head(item: Item) -> str:
    """The pre-rebase tip `pr rebase` saved for *item*'s branch, or ""."""
    state = _load_pr_state(item)
    return state.rebase.pre_rebase_head if state is not None else ""


def comment_items(item: Item) -> list[dict]:
    state = _load_pr_state(item)
    if state is None:
        return []
    return [{"id": i.id, "outcome": i.outcome.value, "summary": i.summary, "reason": i.reason,
             "file": i.file, "line": i.line, "replyable": not _SYNTHETIC_ID.match(i.id)}
            for i in state.fix.fix.items if i.outcome in _OWED]


def open_findings(item: Item) -> list[dict]:
    path = review.paths.self_review_file_path(item.repo, item.branch)
    if not path.is_file():
        return []
    doc = review.document.ReviewDocument.parse(path.read_text())
    # Finding stores the description in `body`, not `title`. The payload key stays `title`.
    return [{"severity": severity_by_key(f.severity).label, "title": f.body,
             "declined": f.declined} for f in doc.open_findings]


def _failed(exit_code: int, log_tail: list[str]) -> StepResult:
    # The lock-busy message is the child's final stderr line and log_tail keeps
    # the last 40 lines, so it is always inside the tail.
    reason = "busy" if any(LOCK_BUSY_MARKER in line for line in log_tail) else "error"
    return StepResult(StepStatus.FAILED, [DecisionDraft(DecisionKind.FAILED, {
        "reason": reason, "exit_code": exit_code, "log_tail": log_tail})])


def _needs(kind: DecisionKind, payloads: list[dict]) -> StepResult:
    return StepResult(StepStatus.NEEDS_DECISION, [DecisionDraft(kind, p) for p in payloads])


def classify(step: Step, exit_code: int, stdout: str, *, item: Item,
             log_tail: list[str]) -> StepResult:
    if step is Step.REBASE and exit_code == CONFLICTS_EXIT:
        return _needs(DecisionKind.REBASE_CONFLICT, [_rebase_payload(stdout, log_tail)])
    if step is Step.REBASE and exit_code == REFUSAL_EXIT:
        return _needs(DecisionKind.REBASE_REFUSED, [_rebase_payload(stdout, log_tail)])
    if exit_code != 0:
        return _failed(exit_code, log_tail)
    if step is Step.COMMENTS and (owed := comment_items(item)):
        return _needs(DecisionKind.COMMENT_ITEM, owed)
    if step is Step.REVIEW and (findings := open_findings(item)):
        return _needs(DecisionKind.OPEN_FINDINGS, [{"findings": findings}])
    pre = str((_json(stdout) or {}).get("pre_rebase_head", "")) if step is Step.REBASE else ""
    return StepResult(StepStatus.DONE, [], pre_rebase_head=pre)
