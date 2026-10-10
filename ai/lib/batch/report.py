"""The compact document `pr batch status`, `pr batch next` and a run's last line print.

One builder, three projections: `build` is the whole report, `next_view` its
actionable part (also the `run_summary` line), and `decision_detail` one
decision with its full payload and log tail.

| Section | Content |
|---|---|
| `run` | `id`, `status`, `active` (the scheduler lock is held), `exit_hint`, `hint` |
| `counts` | items by status, open decisions |
| `items` | non-terminal items only: worktree, branch, `remote_sha`, `pre_rebase_head`, `stacked_on`, `base_ref`, step statuses |
| `next` | one entry per open decision: `decision`, `pr`, `kind`, `why`, `prep`, `worktree`, `actions`, `commands`, a small `payload`, `log`, `session_log` |

`exit_hint` is what `run`/`resume` exit with for the run as it stands: 0 once
it is `done` or `cancelled`, 10 while it waits on a person, 1 when it stopped
without settling (interrupted — `pr batch resume` it), and null while a
scheduler holds the run. A review decision's finding counts leave declined
findings out of must/should/nit and count them apart as `declined`.

`next` is empty while a scheduler holds the run — it is still moving items,
so the reader waits for its `run_summary` — and once the run is `done` or
`cancelled`. Otherwise it offers `prep` and `pr batch resolve` commands, never
a `pr` subcommand against a batch item.
"""

# doc-group: batch

from __future__ import annotations

from collections import Counter

import batch.outcomes
import batch.publish
import batch.resolve
import review.paths
from batch.model import (Decision, DecisionKind, DirtyReason, EvidenceKind, Item, Run, RunStatus,
                         Step, stacked_on_items)
from review.types import SEVERITY_MUST, SEVERITY_NIT, SEVERITY_SHOULD, severity_by_key

SCHEMA_VERSION = 1
EXIT_OK = 0
EXIT_WAITING = 10
EXIT_FAILED = 1
_STATUS_EXIT = {RunStatus.DONE: EXIT_OK, RunStatus.CANCELLED: EXIT_OK,
                RunStatus.WAITING: EXIT_WAITING}
_FINISHED = frozenset({RunStatus.DONE, RunStatus.CANCELLED})
_EXCERPT = 200
_NEXT_KEYS = ("schema_version", "run", "counts", "next")
# What a `next` entry keeps of each kind's payload; the rest is behind --decision.
_PAYLOAD_KEYS: dict[DecisionKind, tuple[str, ...]] = {
    DecisionKind.FAILED: ("reason", "detail", "exit_code", "commits"),
    DecisionKind.REBASE_CONFLICT: ("files", "remaining_commits"),
    DecisionKind.REBASE_REFUSED: ("status", "override", "remedy", "detail"),
    DecisionKind.COMMENT_ITEM: ("id", "outcome", "file", "line", "replyable"),
    DecisionKind.PUBLISH: ("drafted", "track", "closeout"),
    DecisionKind.DIRTY_WORKTREE: ("path", "reason"),
}


def exit_code(status: RunStatus) -> int:
    """What `run`/`resume` exit with for a run that stopped at *status*: 10 is waiting."""
    return _STATUS_EXIT.get(status, EXIT_FAILED)


def _evidence(decision: Decision, kind: EvidenceKind) -> list[dict]:
    return [e for e in decision.payload.get("evidence", []) if e.get("kind") == kind.value]


def _evidence_kinds(decision: Decision) -> list[str]:
    return [e.get("kind", "") for e in decision.payload.get("evidence", [])]


def _finding_counts(decision: Decision) -> dict | None:
    """Open findings by severity, declined ones left out of those and counted apart."""
    findings = [f for e in _evidence(decision, EvidenceKind.OPEN_FINDINGS)
                for f in e.get("findings", [])]
    if not findings:
        return None
    labels = [f.get("severity") for f in findings if not f.get("declined")]
    counts = {name: labels.count(severity_by_key(key).label)
              for name, key in (("must", SEVERITY_MUST), ("should", SEVERITY_SHOULD),
                                ("nit", SEVERITY_NIT))}
    counts["declined"] = len(findings) - len(labels)
    return counts


def why(decision: Decision) -> str:
    """One sentence saying why *decision* is waiting on a person."""
    p, kind = decision.payload, decision.kind
    if kind is DecisionKind.FAILED:
        return batch.outcomes.why(p.get("reason", ""))
    if kind is DecisionKind.REBASE_CONFLICT:
        files = p.get("files") or []
        return (f"the rebase stopped on conflicts in {len(files)} file(s)" if files
                else "the rebase stopped on conflicts")
    if kind is DecisionKind.REBASE_REFUSED:
        return f"pr rebase refused: {p.get('detail') or p.get('status') or 'no reason given'}"
    if kind in (DecisionKind.STEP_REVIEW, DecisionKind.OPEN_FINDINGS):
        kinds = _evidence_kinds(decision) or ["open_findings"]
        return f"the {decision.step} step needs a person: {', '.join(kinds)}"
    if kind is DecisionKind.COMMENT_ITEM:
        return f"a review comment needs a person ({p.get('outcome', 'deferred')})"
    if kind is DecisionKind.DIRTY_WORKTREE:
        return ("a rebase is paused in the worktree"
                if p.get("reason") == DirtyReason.REBASE_IN_PROGRESS
                else "the worktree has uncommitted changes")
    if kind is DecisionKind.INTERRUPTED:
        return f"the {decision.step} step was interrupted before it finished"
    if kind is DecisionKind.PUBLISH:
        parts = []
        if p.get("drafted"):
            parts.append(f"drafted work is ready to publish: {', '.join(p['drafted'])}")
        if p.get("closeout"):
            parts.append(f"pr comments owes this PR a closeout: {p['closeout']}")
        return "; ".join(parts) or "drafted work is ready to publish"
    # Every kind today is named above; a kind added later still gets a true sentence.
    return f"the {decision.step} step needs a person ({kind.value})"


def prep(decision: Decision, item: Item) -> str | None:
    """Work outside `pr` the decision needs before it is answered, or None."""
    p, kind = decision.payload, decision.kind
    if kind is DecisionKind.REBASE_CONFLICT:
        files = ", ".join(p.get("files") or []) or "the conflicted files"
        return f"resolve {files} in {item.worktree}, then retry"
    counts = _finding_counts(decision) if kind is DecisionKind.STEP_REVIEW else None
    if counts is not None:
        path = review.paths.self_review_file_path(item.repo, item.branch)
        return (f"read {path}; finding counts "
                f"{counts['must']}/{counts['should']}/{counts['nit']} (must/should/nit), "
                f"{counts['declined']} declined")
    if kind is DecisionKind.STEP_REVIEW and (bases := stacked_on_items(decision)):
        return f"wait for {', '.join(bases)} to finish; `pr batch resume` retries this step"
    if kind is DecisionKind.FAILED and \
            p.get("reason") == batch.publish.Refusal.NOT_INCORPORATED_REMOTE.value:
        return f"check {'; '.join(p.get('commits', []))} against {item.remote_sha}"
    if kind is DecisionKind.COMMENT_ITEM:
        where = f"{p['file']}:{p.get('line', 0)}" if p.get("file") else "the PR conversation"
        return f"thread {p.get('id', '')} on {where}: {p.get('summary', '')[:_EXCERPT]}"
    if kind is DecisionKind.DIRTY_WORKTREE and p.get("stash"):
        return (f"commit or stash the changes in {p.get('path') or item.worktree} "
                f"({p['stash']}), then retry")
    return None


def _small_payload(decision: Decision) -> dict:
    if decision.kind in (DecisionKind.STEP_REVIEW, DecisionKind.OPEN_FINDINGS):
        small: dict = {"evidence": _evidence_kinds(decision)}
        if (counts := _finding_counts(decision)) is not None:
            small["findings"] = counts
        if bases := stacked_on_items(decision):
            small["stacked_on"] = bases
        return small
    keys = _PAYLOAD_KEYS.get(decision.kind, ())
    return {k: decision.payload[k] for k in keys if k in decision.payload}


def _log(decision: Decision, item: Item) -> str | None:
    p = decision.payload
    # Both are stable persisted keys: failed payloads carry `log`, interrupted ones `log_path`.
    if p.get("log") or p.get("log_path"):
        return p.get("log") or p.get("log_path")
    step = batch.resolve.step_of(decision)
    if step is None or not item.has(step):
        return None
    return item.step(step).log_path or None


def _session_log(decision: Decision, item: Item) -> str | None:
    if decision.payload.get("session_log"):
        return decision.payload["session_log"]
    if decision.step != Step.REVIEW.value:
        return None
    path = review.paths.self_review_dir(item.repo, item.branch) / review.paths.FILENAME_SESSION
    return str(path) if path.is_file() else None


def _actions(decision: Decision, item: Item) -> list[str]:
    return batch.resolve.available_actions(decision, item) if decision.open else []


def _commands(run: Run, decision: Decision, actions: list[str]) -> list[str]:
    return [batch.resolve.resolve_command(run.id, decision.id, a) for a in actions]


def _entry(run: Run, decision: Decision, *, full: bool = False) -> dict:
    item = run.item(decision.item)
    actions = _actions(decision, item)
    return {"decision": decision.id, "pr": item.key, "kind": decision.kind.value,
            "why": why(decision), "prep": prep(decision, item), "worktree": item.worktree,
            "actions": actions, "commands": _commands(run, decision, actions),
            "payload": dict(decision.payload) if full else _small_payload(decision),
            "log": _log(decision, item), "session_log": _session_log(decision, item)}


def _hint(run: Run, active: bool) -> str:
    if active:
        return "a scheduler is driving this run; wait for its run_summary line"
    if run.status in _FINISHED:
        return f"the run is {run.status.value}; nothing is left to answer"
    if run.open_decisions():
        return f"answer each `next` entry, then continue with `pr batch resume {run.id}`"
    return f"nothing is waiting on you; continue with `pr batch resume {run.id}`"


def _item_view(item: Item) -> dict:
    return {"pr": item.key, "status": item.status.value, "worktree": item.worktree,
            "branch": item.branch, "remote_sha": item.remote_sha,
            "pre_rebase_head": item.pre_rebase_head, "stacked_on": item.stacked_on,
            "base_ref": item.base_ref,
            "steps": {r.step.value: r.status.value for r in item.steps}}


def build(run: Run, *, active: bool) -> dict:
    """The whole report; *active* is whether a scheduler holds the run's lock."""
    owned = not active and run.status not in _FINISHED
    return {
        "schema_version": SCHEMA_VERSION,
        "run": {"id": run.id, "status": run.status.value, "active": active,
                "exit_hint": None if active else exit_code(run.status),
                "hint": _hint(run, active)},
        "counts": {"items": dict(Counter(i.status.value for i in run.items)),
                   "open_decisions": len(run.open_decisions())},
        "items": [_item_view(i) for i in run.items if not i.terminal],
        "next": [_entry(run, d) for d in run.open_decisions()] if owned else [],
    }


def next_view(report: dict) -> dict:
    """The part of *report* that needs action: everything but `items`."""
    return {k: report[k] for k in _NEXT_KEYS}


def decision_detail(run: Run, decision_id: str) -> dict:
    """One decision with its full payload (log tail included), open or resolved."""
    decision = run.decision(decision_id)
    return {"schema_version": SCHEMA_VERSION, "run": run.id, "step": decision.step,
            "created_at": decision.created_at, "resolution": decision.resolution,
            "resolved_at": decision.resolved_at, **_entry(run, decision, full=True)}


def decision_event(run: Run, decision: Decision) -> dict:
    """The fields a `decision_created` event carries: what an agent acts on, nothing more."""
    item = run.item(decision.item)
    return {"item": item.key, "decision": decision.id, "decision_kind": decision.kind.value,
            "why": why(decision),
            "commands": _commands(run, decision, _actions(decision, item))}
