"""Turn what a finished step left behind into a step status and decisions."""

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


def _json(stdout: str) -> dict:
    try:
        value = json.loads(stdout or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _load_pr_state(item: Item):
    key = pr.target.repo_key_from_origin(item.worktree or item.repo_dir)
    return pr.state.load_state(pr.target.target_dir(key, item.branch)) if key else None


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
    return [{"severity": str(f.severity), "title": f.body} for f in doc.open_findings]


def _failed(exit_code: int, log_tail: list[str]) -> StepResult:
    reason = "busy" if any(LOCK_BUSY_MARKER in line for line in log_tail) else "error"
    return StepResult(StepStatus.FAILED, [DecisionDraft(DecisionKind.FAILED, {
        "reason": reason, "exit_code": exit_code, "log_tail": log_tail})])


def _needs(kind: DecisionKind, payloads: list[dict]) -> StepResult:
    return StepResult(StepStatus.NEEDS_DECISION, [DecisionDraft(kind, p) for p in payloads])


def classify(step: Step, exit_code: int, stdout: str, *, item: Item,
             log_tail: list[str]) -> StepResult:
    if step is Step.REBASE and exit_code == CONFLICTS_EXIT:
        return _needs(DecisionKind.REBASE_CONFLICT, [_json(stdout)])
    if step is Step.REBASE and exit_code == REFUSAL_EXIT:
        return _needs(DecisionKind.REBASE_REFUSED, [_json(stdout)])
    if exit_code != 0:
        return _failed(exit_code, log_tail)
    if step is Step.COMMENTS and (owed := comment_items(item)):
        return _needs(DecisionKind.COMMENT_ITEM, owed)
    if step is Step.REVIEW and (findings := open_findings(item)):
        return _needs(DecisionKind.OPEN_FINDINGS, [{"findings": findings}])
    return StepResult(StepStatus.DONE, [])
