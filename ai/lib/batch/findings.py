"""Map a v1 decision to findings (spec §3.1).

The one owner of "decision → finding", read by the scheduler, the report, and
`upgrade_v1`. A schema-2 run never opens a decision.

| v1 decision | finding |
|---|---|
| `rebase_conflict` | `conflict_handwritten` (`data.classes` when present; `data.paused` when the tree is paused) |
| `rebase_refused` | `failed_unknown` |
| `step_review` + `stacked_on` | `stack_wait_base` |
| `step_review` + `one_sided`/`stale` | `stale_generated` |
| `step_review` + `open_findings` | `failed_unknown` until `review_findings` lands |
| `step_review` other | `failed_unknown` (evidence kinds in `detail`) |
| `comment_item` | `threads_need_human` |
| `dirty_worktree` | `dirty_worktree` |
| `failed` | `wrong_branch` / `worktree_collision` / `budget_exhausted` / `push_refused` / `failed_unknown` |
| `interrupted` | none |
| `publish` | `ready_to_push` and/or `closeout_owed` |
"""

# doc-group: batch

from __future__ import annotations

from batch.model import (Decision, DecisionKind, EvidenceKind, Finding, FindingReason, Item,
                         Run, stacked_on_items)
from batch.publish import Refusal

CLOSEOUT_WHY = "closeout owed per pr state"
_PUSH_REFUSALS = frozenset(r.value for r in Refusal if r is not Refusal.WRONG_BRANCH)


def findings_from_decisions(run: Run, item: Item) -> list[Finding]:
    """Map *item*'s open v1 decisions to findings (spec §3.1)."""
    found: list[Finding] = []
    for decision in run.open_decisions(item.key):
        found.extend(findings_for(decision))
    return found


def findings_for(decision: Decision) -> list[Finding]:
    kind, payload = decision.kind, decision.payload
    if kind is DecisionKind.INTERRUPTED:
        return []
    if kind is DecisionKind.REBASE_CONFLICT:
        files = list(payload.get("files") or [])
        data = {}
        if payload.get("classes"):
            data["classes"] = payload["classes"]
        if payload.get("rebase_in_progress") or payload.get("tree") == "paused":
            data["paused"] = True
        return [_finding(FindingReason.CONFLICT_HANDWRITTEN, decision, files=files, data=data)]
    if kind is DecisionKind.REBASE_REFUSED:
        return [_finding(FindingReason.FAILED_UNKNOWN, decision,
                         detail=str(payload.get("detail") or payload.get("status") or ""),
                         data={"remedy": payload.get("remedy", "")})]
    if kind is DecisionKind.STEP_REVIEW:
        return [_step_review(decision)]
    if kind is DecisionKind.COMMENT_ITEM:
        return [_finding(FindingReason.THREADS_NEED_HUMAN, decision)]
    if kind is DecisionKind.DIRTY_WORKTREE:
        return [_finding(FindingReason.DIRTY_WORKTREE, decision,
                         detail=str(payload.get("reason") or ""))]
    if kind is DecisionKind.FAILED:
        return [_failed(decision)]
    if kind is DecisionKind.PUBLISH:
        return _publish_findings(decision)
    return [_finding(FindingReason.FAILED_UNKNOWN, decision)]


def _step_review(decision: Decision) -> Finding:
    stacked = stacked_on_items(decision)
    if stacked:
        return _finding(FindingReason.STACK_WAIT_BASE, decision, data={"after": stacked})
    evidence = decision.payload.get("evidence") or []
    kinds = {e.get("kind") for e in evidence}
    if EvidenceKind.ONE_SIDED.value in kinds or EvidenceKind.STALE.value in kinds:
        files = [f for e in evidence for f in e.get("files") or []]
        return _finding(FindingReason.STALE_GENERATED, decision, files=files,
                        data={"one_sided": EvidenceKind.ONE_SIDED.value in kinds})
    kinds_list = [e.get("kind", "") for e in evidence]
    return _finding(FindingReason.FAILED_UNKNOWN, decision,
                    detail=", ".join(kinds_list) or EvidenceKind.OPEN_FINDINGS.value)


def _failed(decision: Decision) -> Finding:
    reason = str(decision.payload.get("reason") or "")
    if reason == Refusal.WRONG_BRANCH.value:
        return _finding(FindingReason.WRONG_BRANCH, decision,
                        detail=str(decision.payload.get("detail") or ""))
    if decision.step == "publish" and reason in _PUSH_REFUSALS:
        return _finding(FindingReason.PUSH_REFUSED, decision,
                        detail=str(decision.payload.get("detail") or ""),
                        data={"refusal": reason})
    if reason == FindingReason.WORKTREE_COLLISION.value:
        return _finding(FindingReason.WORKTREE_COLLISION, decision,
                        detail=str(decision.payload.get("detail") or ""))
    if reason == "quota_exhausted":
        return _finding(FindingReason.BUDGET_EXHAUSTED, decision)
    return _finding(FindingReason.FAILED_UNKNOWN, decision,
                    detail=str(decision.payload.get("detail") or ""),
                    data={"reason": reason} if reason else {})


def _publish_findings(decision: Decision) -> list[Finding]:
    payload = decision.payload
    found: list[Finding] = []
    if payload.get("drafted") or payload.get("unpublished") or not payload.get("closeout"):
        found.append(_finding(FindingReason.READY_TO_PUSH, decision))
    if payload.get("closeout"):
        found.append(_finding(FindingReason.CLOSEOUT_OWED, decision, detail=CLOSEOUT_WHY))
    return found


def _finding(reason: FindingReason, decision: Decision, *, detail: str = "",
             files: list[str] | None = None, data: dict | None = None) -> Finding:
    payload = decision.payload
    return Finding(
        reason=reason, step=decision.step, detail=detail, files=list(files or []),
        log_path=str(payload.get("log") or payload.get("log_path") or ""),
        data=dict(data or {}),
    )
