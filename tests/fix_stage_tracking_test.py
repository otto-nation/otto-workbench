"""Plan / implement / escalate checklist vocabularies."""

import sys
from pathlib import Path

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import fix.stage_tracking as stage
import fix.tracking
from fix.plan_model import PlanVerdict
from fix.types import FixItem
from pr.fix import Escalation, FixOutcome, Handoff


def _item():
    return FixItem(id="T1", file="a.py", line=3, label="@r", body="reviewer text")


def _tick(text: str, label: str, reason: str = "") -> str:
    old = f"- [ ] {label}"
    if reason:
        return text.replace(f"{old} — <why>", f"- [x] {label} — {reason}", 1)
    return text.replace(old, f"- [x] {label}", 1)


def _write(path: Path, boxes, body="", title="t"):
    fix.tracking.write(path, title, [_item()], boxes)
    if body:
        text = path.read_text()
        text = text.replace("- [ ] ", body.rstrip() + "\n\n- [ ] ", 1)
        path.write_text(text)
    return path


class TestPlanVocabulary:
    def test_fix_round_trip_with_labelled_fields(self, tmp_path):
        path = _write(tmp_path / "p.md", stage.PLAN_BOXES, body=(
            "Files: a.py, tests/a_test.py\n"
            "Change: rename f to g\n"
            "more change\n"
            "Test: test_g — asserts g\n"
        ))
        path.write_text(_tick(path.read_text(), "fix"))
        plans = stage.parse_plan(path, [_item()])
        plan = plans["T1"]
        assert plan.verdict is PlanVerdict.FIX
        assert plan.files == ("a.py", "tests/a_test.py")
        assert "rename f to g" in plan.change
        assert "more change" in plan.change
        assert plan.test == "test_g — asserts g"
        assert plan.valid

    def test_dashed_file_names_survive(self, tmp_path):
        path = _write(tmp_path / "p.md", stage.PLAN_BOXES, body=(
            "Files:\n- -foo.py\n- a-\nChange: x\nTest: t\n"
        ))
        path.write_text(_tick(path.read_text(), "fix"))
        assert stage.parse_plan(path, [_item()])["T1"].files == ("-foo.py", "a-")

    def test_untouched_item_is_absent(self, tmp_path):
        path = _write(tmp_path / "p.md", stage.PLAN_BOXES)
        assert stage.parse_plan(path, [_item()]) == {}

    def test_dismiss_keeps_its_reason(self, tmp_path):
        path = _write(tmp_path / "p.md", stage.PLAN_BOXES)
        path.write_text(_tick(path.read_text(), "dismiss", "not our bug"))
        plan = stage.parse_plan(path, [_item()])["T1"]
        assert plan.verdict is PlanVerdict.DISMISS
        assert plan.reason == "not our bug"
        assert plan.valid


class TestImplementVocabulary:
    def test_plan_failed_is_needs_human_with_handoff(self, tmp_path):
        path = _write(tmp_path / "i.md", stage.IMPLEMENT_BOXES)
        path.write_text(_tick(
            path.read_text(), "plan failed", "no such function",
        ))
        [outcome] = stage.parse_implement(path)
        assert outcome.outcome is FixOutcome.NEEDS_HUMAN
        assert outcome.handoff is Handoff.PLAN_FAILED
        assert outcome.reason == "no such function"

    def test_fixed_round_trip(self, tmp_path):
        path = _write(tmp_path / "i.md", stage.IMPLEMENT_BOXES)
        path.write_text(_tick(path.read_text(), "fixed", "test_g — asserts g"))
        [outcome] = stage.parse_implement(path)
        assert outcome.outcome is FixOutcome.FIXED
        assert outcome.handoff is None
        assert outcome.reason == "test_g — asserts g"

    def test_untouched_item_is_deferred(self, tmp_path):
        path = _write(tmp_path / "i.md", stage.IMPLEMENT_BOXES)
        [outcome] = stage.parse_implement(path)
        assert outcome.outcome is FixOutcome.DEFERRED


class TestEscalateVocabulary:
    def test_needs_a_person_carries_analysis(self, tmp_path):
        path = _write(tmp_path / "e.md", stage.ESCALATE_BOXES, body=(
            "Analysis: two options\n"
            "pick the second\n"
        ))
        path.write_text(_tick(path.read_text(), "needs a person", "ask the author"))
        [outcome] = stage.parse_escalate(path)
        assert outcome.outcome is FixOutcome.NEEDS_HUMAN
        assert outcome.escalation is Escalation.CONFIRMED
        assert "two options" in outcome.escalation_note
        assert "pick the second" in outcome.escalation_note
        assert outcome.reason == "ask the author"

    def test_fixed_is_resolved(self, tmp_path):
        path = _write(tmp_path / "e.md", stage.ESCALATE_BOXES)
        path.write_text(_tick(path.read_text(), "fixed", "test_g"))
        [outcome] = stage.parse_escalate(path)
        assert outcome.outcome is FixOutcome.FIXED
        assert outcome.escalation is Escalation.RESOLVED
