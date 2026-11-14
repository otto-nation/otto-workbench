"""Plan then implement, or fall back to today's agent path.

Called from the engine only when ``adapter.plan_phase`` is set. Existing
adapters leave that unset and never reach this module.
"""

# doc-group: pipeline

from __future__ import annotations

import core.log
import agent.phases
import fix.containment
import fix.engine
from agent.registry import PHASES
from fix.batches import Batch, BatchStage, chunks, run_batch, settle
from fix.plan_model import PlanMode, PlanVerdict, StagedItem
from fix.plan_store import save
from fix.stage_tracking import (
    IMPLEMENT_BOXES, PLAN_BOXES, parse_implement, parse_plan, plan_is_usable,
    render_plan_body,
)
from fix.types import FixItem
from pr.fix import FixOutcome, Handoff, ItemOutcome


def run_planned(adapter: fix.engine.FixAdapter, start: fix.engine._PassStart):
    """Plan, implement what the plan said to fix, else today's path."""
    items = list(start.items)
    if adapter.implement_phase is None:
        core.log.error("plan stage has no implement_phase — running today's path")
        return _legacy(adapter, start, items)
    staged = adapter.staged_items() or [StagedItem(item=item) for item in items]
    work = [entry.item for entry in staged]
    by_staged = {entry.item.id: entry for entry in staged}
    try:
        plans = _plan(adapter, start, work)
    except Exception as exc:
        core.log.error(f"plan stage failed — running today's path ({exc})")
        return _legacy(adapter, start, items)
    usable = {
        item_id: plan for item_id, plan in plans.items() if plan_is_usable(plan)
    }
    if work and not usable:
        core.log.error("plan stage returned unusable output — running today's path")
        return _legacy(adapter, start, items)
    return _act(adapter, start, work, by_staged, usable, items)


def _plan(adapter, start, items) -> dict[str, object]:
    phase = adapter.plan_phase
    assert phase is not None
    batched = chunks(items, agent.phases.phase_chunk_size(phase))
    if batched == [[]]:
        batched = []
    plans: dict[str, object] = {}
    name = PHASES[phase].label
    with fix.containment.around(
        adapter.workdir, adapter.artifacts, "plan", start.trail,
    ):
        for n, chunk in enumerate(batched, start=1):
            label = name if len(batched) == 1 else f"{name} (batch {n}/{len(batched)})"
            stage = BatchStage(
                phase=phase,
                template=PHASES[phase].template_for(),
                boxes=PLAN_BOXES,
                title="Plan",
                tracking_path=adapter.plan_tracking_path(n),
                session_log=adapter.plan_session_log(n),
                use_adapter_model=False,
            )
            run_batch(adapter, chunk, label, stage=stage)
            parsed = parse_plan(
                adapter.plan_tracking_path(n), chunk,
                head_sha=start.head_before,
                pr_files=adapter.pr_files(),
            )
            for plan in parsed.values():
                save(plan, adapter.plans_dir)
            plans.update(parsed)
    return plans


def _act(adapter, start, work, by_staged, usable, original):
    decided: list[ItemOutcome] = []
    implement: list[FixItem] = []
    plan_paths: dict[str, str] = {}
    fallback: list[FixItem] = []
    for item in work:
        plan = usable.get(item.id)
        if plan is None:
            fallback.append(item)
            continue
        path = save(plan, adapter.plans_dir)
        plan_paths[item.id] = str(path)
        mode = by_staged[item.id].mode if item.id in by_staged else PlanMode.ACT
        if plan.verdict is PlanVerdict.FIX and mode is PlanMode.ACT:
            implement.append(FixItem(
                id=item.id, file=item.file, line=item.line,
                label=item.label, body=render_plan_body(plan),
            ))
            continue
        decided.append(_from_plan(item, plan, str(path), mode))
    results: list[Batch] = []
    if decided:
        results.append(_batch(decided))
    local = 0
    if implement:
        impl_results, start, n = _run_stage(
            adapter, start, implement, _implement_stage(adapter),
        )
        settled = settle(
            adapter, impl_results, {item.id: item for item in implement},
            start.trail, stage=_implement_stage(adapter),
        )
        for outcome in settled.outcomes:
            outcome.plan_path = plan_paths.get(outcome.id, outcome.plan_path)
        results.append(_batch(settled.outcomes, settled.exit_code, settled.stop))
        local += n
    if fallback:
        legacy, start, n = _run_stage(adapter, start, fallback, None)
        results.extend(legacy)
        local += n
    unseen = [item for item in original if item.id not in {w.id for w in work}]
    if unseen:
        extra, start, n = _run_stage(adapter, start, unseen, None)
        results.extend(extra)
        local += n
    if not results:
        return _legacy(adapter, start, original)
    return fix.engine._AgentRun(start, tuple(results), local)


def _from_plan(item: FixItem, plan, path: str, mode: PlanMode) -> ItemOutcome:
    # Agent ALREADY_ADDRESSED/DISMISSED are dropped by by_outcome; C3 maps
    # those plan verdicts to DECLINED so they reach the user as a decline.
    note = plan.reason
    if mode is PlanMode.ADVISE or plan.verdict is PlanVerdict.NEEDS_HUMAN:
        return ItemOutcome(
            id=item.id, file=item.file, line=item.line,
            outcome=FixOutcome.NEEDS_HUMAN, reason=note,
            handoff=(
                Handoff.PLAN_ADVISED if mode is PlanMode.ADVISE
                else Handoff.PLAN_NEEDS_HUMAN
            ),
            evidence_file=plan.evidence_file,
            evidence_line=plan.evidence_line,
            plan_path=path,
        )
    return ItemOutcome(
        id=item.id, file=item.file, line=item.line,
        outcome=FixOutcome.DECLINED, reason=note,
        handoff=Handoff.PLAN_DECLINED,
        evidence_file=plan.evidence_file,
        evidence_line=plan.evidence_line,
        plan_path=path,
    )


def _implement_stage(adapter) -> BatchStage:
    phase = adapter.implement_phase
    assert phase is not None
    return BatchStage(
        phase=phase,
        template=PHASES[phase].template_for(),
        boxes=IMPLEMENT_BOXES,
        title="Implement",
        parse=parse_implement,
        use_adapter_model=True,
    )


def _legacy(adapter, start, items):
    results, start, n = _run_stage(adapter, start, items, None)
    return fix.engine._AgentRun(start, tuple(results), n)


def _run_stage(adapter, start, items, stage: BatchStage | None):
    phase = stage.phase if stage is not None else adapter.phase
    chunk_size = agent.phases.phase_chunk_size(phase)
    batched = chunks(items, chunk_size)
    if batched == [[]]:
        batched = []
    name = PHASES[phase].label
    results: list[Batch] = []
    dirty_before = set(start.dirty_before)
    for n, chunk in enumerate(batched, start=1):
        label = name if len(batched) == 1 else f"{name} (batch {n}/{len(batched)})"
        results.append(run_batch(
            adapter, chunk, label,
            before=(None if n > 1 else dirty_before),
            stage=stage,
        ))
    return results, start, len(batched)


def _batch(outcomes, exit_code=0, stop=None) -> Batch:
    return Batch(
        outcomes=outcomes, unproductive=None, exit_code=exit_code,
        max_turns=0, max_budget=0.0, stop=stop,
    )
