"""Plan and escalate stages in the shared engine, driven by StubAdapter."""

import sys
from pathlib import Path
from unittest.mock import patch

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import agent.invoke
import fix.containment
import fix.engine
from core.phases import Phase
from pr.fix import Escalation, FixOutcome, Handoff

from fix_engine_support import StubAdapter, _answer, _run, head, landed, snapshots  # noqa: F401

REVIEWER = "UNIQUE_REVIEWER_ASK"


class StagedAdapter(StubAdapter):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.plan_phase = Phase.COMMENTS_PLAN
        self.implement_phase = Phase.COMMENTS_IMPLEMENT
        self.escalate_phase = Phase.COMMENTS_ESCALATE
        self.model = "adapter-model"

    def items(self):
        items = super().items()
        for item in items:
            item.body = REVIEWER
        return items


def _tick(path: Path, label: str, reason: str = "") -> None:
    text = path.read_text()
    old = f"- [ ] {label}"
    if reason and f"{old} — <why>" in text:
        text = text.replace(f"{old} — <why>", f"- [x] {label} — {reason}", 1)
    else:
        text = text.replace(old, f"- [x] {label}", 1)
    path.write_text(text)


def _write_plan(adapter, plan):
    for path in sorted(adapter.artifacts.glob("plan-tracking-*.md")):
        _tick(path, plan)
        if plan == "fix":
            path.write_text(path.read_text().replace(
                "- [x] fix",
                "- [x] fix\n\nFiles: a.py\nChange: edit f\nTest: test_x\n",
                1,
            ))


def _write_escalate(adapter, escalate, reason, analysis):
    box = escalate or "needs a person"
    for path in sorted(adapter.artifacts.glob("escalate-tracking-*.md")):
        _tick(path, box, reason)
        if analysis:
            path.write_text(path.read_text().replace(
                f"- [x] {box}", f"Analysis: {analysis}\n\n- [x] {box}", 1,
            ))


def _staged_fix(
    adapter, *, plan="fix", implement="fixed", escalate=None,
    implement_reason="test_x — asserts x", escalate_reason="ask the author",
    analysis="",
):
    captured = []

    def run_fix(phase, prompt, **kwargs):
        captured.append((phase, prompt, kwargs))
        if phase is Phase.COMMENTS_PLAN:
            _write_plan(adapter, plan)
        elif phase is Phase.COMMENTS_IMPLEMENT:
            _tick(adapter.tracking_path, implement, implement_reason)
        elif phase is Phase.COMMENTS_ESCALATE:
            _write_escalate(adapter, escalate, escalate_reason, analysis)
        else:
            return _answer(adapter)(phase, prompt, **kwargs)
        return agent.invoke.FixResult(0, None)

    run_fix.captured = captured  # type: ignore[attr-defined]
    return run_fix


def test_no_hooks_matches_today_s_run_fix_calls(tmp_path, landed, head):
    adapter = StubAdapter(tmp_path)
    run, inv = _run(adapter)
    assert inv.call_count == 1
    assert inv.call_args.args[0] is Phase.CI_FIX
    assert run.outcomes[0].outcome is FixOutcome.FIXED
    assert inv.call_args.kwargs.get("model") is None


def test_plan_fix_implement_sees_only_the_plan(tmp_path, landed, head):
    adapter = StagedAdapter(tmp_path)
    stub = _staged_fix(adapter)
    run, inv = _run(adapter, run_fix=stub)
    assert [o.outcome for o in run.outcomes] == [FixOutcome.FIXED]
    phases = [c.args[0] for c in inv.call_args_list]
    assert Phase.COMMENTS_PLAN in phases
    assert Phase.COMMENTS_IMPLEMENT in phases
    implement_prompts = [
        c.args[1] for c in inv.call_args_list
        if c.args[0] is Phase.COMMENTS_IMPLEMENT
    ]
    assert implement_prompts
    assert REVIEWER not in implement_prompts[0]
    assert "edit f" in implement_prompts[0]


def test_plan_failed_is_escalated_and_can_fix(tmp_path, landed, head):
    adapter = StagedAdapter(tmp_path)
    stub = _staged_fix(
        adapter, implement="plan failed", implement_reason="no such fn",
        escalate="fixed", escalate_reason="test_x",
    )
    run, inv = _run(adapter, run_fix=stub)
    phases = [c.args[0] for c in inv.call_args_list]
    assert Phase.COMMENTS_ESCALATE in phases
    assert run.outcomes[0].outcome is FixOutcome.FIXED
    assert run.outcomes[0].handoff is None
    assert run.outcomes[0].escalation is Escalation.RESOLVED


def test_escalation_confirms_needs_human_with_its_reason(tmp_path, landed, head):
    adapter = StagedAdapter(tmp_path)
    stub = _staged_fix(
        adapter, implement="plan failed", implement_reason="no such fn",
        escalate="needs a person", escalate_reason="ask the author",
        analysis="the plan named a missing helper",
    )
    run, _ = _run(adapter, run_fix=stub)
    outcome = run.outcomes[0]
    assert outcome.outcome is FixOutcome.NEEDS_HUMAN
    assert outcome.escalation is Escalation.CONFIRMED
    assert outcome.reason == "ask the author"
    assert "missing helper" in outcome.escalation_note


def test_plan_needs_human_is_not_escalated(tmp_path, landed, head):
    adapter = StagedAdapter(tmp_path)

    def run_fix(phase, prompt, **kwargs):
        if phase is Phase.COMMENTS_PLAN:
            for path in sorted(adapter.artifacts.glob("plan-tracking-*.md")):
                _tick(path, "needs a person", "design call")
            return agent.invoke.FixResult(0, None)
        raise AssertionError(f"unexpected phase {phase}")
    run, inv = _run(adapter, run_fix=run_fix)
    assert inv.call_count == 1
    assert inv.call_args.args[0] is Phase.COMMENTS_PLAN
    assert run.outcomes[0].outcome is FixOutcome.NEEDS_HUMAN
    assert run.outcomes[0].handoff is Handoff.PLAN_NEEDS_HUMAN


def test_partially_usable_plan_hands_the_unusable_item_back(
    tmp_path, landed, head,
):
    adapter = StagedAdapter(tmp_path, count=2)
    inner = _staged_fix(adapter, escalate="needs a person")

    def run_fix(phase, prompt, **kwargs):
        result = inner(phase, prompt, **kwargs)
        if phase is Phase.COMMENTS_PLAN:
            for path in sorted(adapter.artifacts.glob("plan-tracking-*.md")):
                # Un-answer the second item's section.
                before, mark, tail = path.read_text().partition("<!-- fix:i1 -->")
                path.write_text(before + mark + tail.replace("- [x]", "- [ ]"))
        return result
    run, inv = _run(adapter, run_fix=run_fix)
    by_id = {o.id: o for o in run.outcomes}
    assert Phase.CI_FIX not in [c.args[0] for c in inv.call_args_list]
    assert by_id["i1"].outcome is FixOutcome.NEEDS_HUMAN
    assert by_id["i1"].handoff is Handoff.PLAN_INVALID
    assert by_id["i0"].outcome is FixOutcome.FIXED


def test_adapter_escalates_false_skips_escalation(tmp_path, landed, head):
    adapter = StagedAdapter(tmp_path)
    adapter.escalates = lambda outcome, staged=None: False  # type: ignore[method-assign]
    stub = _staged_fix(
        adapter, implement="plan failed", implement_reason="no such fn",
    )
    run, inv = _run(adapter, run_fix=stub)
    phases = [c.args[0] for c in inv.call_args_list]
    assert Phase.COMMENTS_ESCALATE not in phases
    assert run.outcomes[0].handoff is Handoff.PLAN_FAILED


def test_escalate_is_one_shot_and_plan_passes_no_model(tmp_path, landed, head):
    adapter = StagedAdapter(tmp_path)
    stub = _staged_fix(
        adapter, implement="plan failed", implement_reason="x",
        escalate="needs a person",
    )
    _, inv = _run(adapter, run_fix=stub)
    by_phase = {c.args[0]: c for c in inv.call_args_list}
    assert by_phase[Phase.COMMENTS_PLAN].kwargs.get("model") is None
    assert by_phase[Phase.COMMENTS_ESCALATE].kwargs.get("model") is None
    assert by_phase[Phase.COMMENTS_IMPLEMENT].kwargs.get("model") == "adapter-model"
    assert by_phase[Phase.COMMENTS_ESCALATE].kwargs.get("produced") is None


def test_plan_stage_is_wrapped_in_containment(tmp_path, landed, head):
    adapter = StagedAdapter(tmp_path)
    stub = _staged_fix(adapter)
    phases = []
    real = fix.containment.around

    def wrapped(workdir, artifacts, phase, trail):
        phases.append(phase)
        return real(workdir, artifacts, phase, trail)

    with patch.object(fix.containment, "around", side_effect=wrapped):
        _run(adapter, run_fix=stub)
    assert "plan" in phases


def test_unusable_plan_takes_today_s_path(tmp_path, landed, head):
    adapter = StagedAdapter(tmp_path)
    def run_fix(phase, prompt, **kwargs):
        if phase is Phase.COMMENTS_PLAN:
            return agent.invoke.FixResult(0, None)
        return _answer(adapter)(phase, prompt, **kwargs)
    run, inv = _run(adapter, run_fix=run_fix)
    phases = [c.args[0] for c in inv.call_args_list]
    assert Phase.COMMENTS_PLAN in phases
    assert Phase.CI_FIX in phases
    assert run.outcomes[0].outcome is FixOutcome.FIXED
