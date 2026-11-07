"""Map a v1 decision to findings (spec §3.1).

The one owner of "decision → finding", read by the scheduler, the report, and
`upgrade_v1`. A schema-2 run never opens a decision: `record` appends to
`Item.findings` and sets `needs_session` when any finding is `block` or `wait`.
Act-only findings leave the item to close `done`.

| v1 decision | finding |
|---|---|
| `rebase_conflict` aborted, all classes `accept_theirs`/`regenerate` | `conflict_generated` |
| `rebase_conflict` else | `conflict_handwritten` (`data.classes`; `data.paused` when paused) |
| `rebase_refused` | `failed_unknown` |
| `step_review` + `stacked_on` | `stack_wait_base` |
| `step_review` + `one_sided`/`stale` | `stale_generated` |
| `step_review` evidence exactly `ci_failing` or `ci_unfixed` | `ci_failing` |
| `step_review` evidence exactly `open_findings` | `review_findings` (`data.severity` block if any open must-fix) |
| `step_review` other / mixed | `failed_unknown` (evidence kinds in `detail`) |
| `comment_item` | `threads_need_human` |
| `dirty_worktree` | `dirty_worktree` |
| `failed` | `wrong_branch` / `worktree_collision` / `budget_exhausted` / `push_refused` / `failed_unknown` |
| `interrupted` | none |
| `publish` | `ready_to_push` and/or `closeout_owed` |
"""

# doc-group: batch

from __future__ import annotations

from collections.abc import Callable

import batch.store
from batch.model import (Decision, DecisionKind, EvidenceKind, Finding, FindingReason,
                         Item, ItemStatus, Run, Severity, severity_of, stacked_on_items)
from batch.publish import Refusal
from rebase.types import ConflictStrategy
from review.types import SEVERITY_MUST, severity_by_key

CLOSEOUT_WHY = "closeout owed per pr state"
_PUSH_REFUSALS = frozenset(r.value for r in Refusal if r is not Refusal.WRONG_BRANCH)
_GENERATED = {ConflictStrategy.ACCEPT_THEIRS.value, ConflictStrategy.REGENERATE.value}


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
        classes = payload.get("classes") or {}
        data = {}
        if classes:
            data["classes"] = classes
        if payload.get("rebase_in_progress") or payload.get("tree") == "paused":
            data["paused"] = True
        generated = bool(payload.get("aborted") and classes
                         and all(c in _GENERATED for c in classes.values()))
        reason = (FindingReason.CONFLICT_GENERATED if generated
                  else FindingReason.CONFLICT_HANDWRITTEN)
        return [_finding(reason, decision, files=files, data=data)]
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
    if kinds in ({EvidenceKind.CI_FAILING.value}, {EvidenceKind.CI_UNFIXED.value}):
        jobs = sorted({j for e in evidence for j in (e.get("jobs") or [])})
        return _finding(FindingReason.CI_FAILING, decision, data={"jobs": jobs})
    if kinds == {EvidenceKind.OPEN_FINDINGS.value}:
        listed = [f for e in evidence for f in e.get("findings") or []]
        must = severity_by_key(SEVERITY_MUST).label
        block = any(not f.get("declined") and f.get("severity") == must for f in listed)
        counts: dict[str, int] = {}
        for f in listed:
            label = str(f.get("severity") or "")
            counts[label] = counts.get(label, 0) + 1
        sev = Severity.BLOCK if block else Severity.ACT
        return _finding(FindingReason.REVIEW_FINDINGS, decision,
                        data={"findings": listed, "counts": counts, "severity": sev.value})
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


def record(run: Run, item: Item, step: str, kind: DecisionKind, payload: dict, *,
           emit: Callable[..., None]) -> list[Finding]:
    """Append findings for one outcome; `needs_session` when any is not `act`."""
    found = findings_for(Decision(id="", item=item.key, step=step, kind=kind, payload=payload))
    at = batch.store.now_iso()
    for finding in found:
        finding.created_at = at
        item.findings.append(finding)
        emit("finding_created", run=run.id, item=item.key,
             reason=finding.reason.value, step=finding.step)
    if any(severity_of(f) is not Severity.ACT for f in found):
        item.status = ItemStatus.NEEDS_SESSION
    return found


def _finding(reason: FindingReason, decision: Decision, *, detail: str = "",
             files: list[str] | None = None, data: dict | None = None) -> Finding:
    payload = decision.payload
    return Finding(
        reason=reason, step=decision.step, detail=detail, files=list(files or []),
        log_path=str(payload.get("log") or payload.get("log_path") or ""),
        data=dict(data or {}),
    )
