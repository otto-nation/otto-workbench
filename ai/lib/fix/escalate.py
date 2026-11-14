"""One deep pass over items that are still going to a person.

No retries (``produced=None``). An item the plan already decided is not
escalated again (C1). Escalation never lifts a publishing hold (C4) — this
module does not touch holds.
"""

# doc-group: pipeline

from __future__ import annotations

import agent.phases
import fix.containment
import fix.engine
import fix.gate
from agent.registry import PHASES
from fix.batches import BatchStage, Settled, chunks, run_batch
from fix.gate import VerifyFn
from fix.stage_tracking import ESCALATE_BOXES, parse_escalate
from pr.fix import Escalation, FixOutcome, Handoff, ItemOutcome


def _apply(outcome: ItemOutcome, answer: ItemOutcome | None, asked: bool) -> bool:
    """Fold `answer` into `outcome`; True when it now claims a fix."""
    if answer is None:
        if asked:
            outcome.escalation = Escalation.UNANSWERED
        return False
    outcome.outcome = answer.outcome
    outcome.reason = answer.reason or outcome.reason
    outcome.escalation = answer.escalation
    outcome.escalation_note = answer.escalation_note
    if outcome.outcome is FixOutcome.FIXED:
        outcome.handoff = None
    return outcome.outcome.counts_as_fixed


def run(
    adapter: fix.engine.FixAdapter,
    settled: Settled,
    by_id: dict,
    trail,
    verify: VerifyFn | None = None,
) -> None:
    """Escalate candidates in `settled.outcomes`, mutating them in place."""
    phase = adapter.escalate_phase
    if phase is None:
        return
    for outcome in settled.outcomes:
        if (
            outcome.outcome is FixOutcome.NEEDS_HUMAN
            and outcome.verified is False
            and outcome.handoff is None
        ):
            outcome.handoff = Handoff.VERIFY_BROKEN
    staged = {entry.item.id: entry for entry in adapter.staged_items()}
    candidates = [
        outcome for outcome in settled.outcomes
        if adapter.escalates(outcome, staged.get(outcome.id))
    ]
    if not candidates:
        return
    items = [by_id[outcome.id] for outcome in candidates if outcome.id in by_id]
    if not items:
        return
    batched = chunks(items, agent.phases.phase_chunk_size(phase))
    if batched == [[]]:
        return
    name = PHASES[phase].label
    answers: dict[str, ItemOutcome] = {}
    for n, chunk in enumerate(batched, start=1):
        label = name if len(batched) == 1 else f"{name} (batch {n}/{len(batched)})"
        stage = BatchStage(
            phase=phase,
            template=PHASES[phase].template_for(),
            boxes=ESCALATE_BOXES,
            title="Escalate",
            tracking_path=adapter.escalate_tracking_path(n),
            session_log=adapter.escalate_session_log(n),
            use_adapter_model=False,
            one_shot=True,
            parse=parse_escalate,
        )
        run_batch(adapter, chunk, label, stage=stage)
        for outcome in parse_escalate(adapter.escalate_tracking_path(n)):
            answers[outcome.id] = outcome
    asked = {item.id for item in items}
    claimed = [
        outcome for outcome in settled.outcomes
        if _apply(outcome, answers.get(outcome.id), outcome.id in asked)
    ]
    if not claimed:
        return
    with fix.containment.around(
        adapter.workdir, adapter.artifacts, "verify-escalated", trail,
    ):
        fix.gate.verify_claims(
            claimed, verify, adapter, by_id, trail,
            scope_for=settled.scope_for,
        )
    for outcome in claimed:
        if outcome.outcome is FixOutcome.NEEDS_HUMAN:
            outcome.escalation = Escalation.FALSIFIED
