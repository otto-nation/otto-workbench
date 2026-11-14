"""Plan JSON on disk: atomic write, load, clear, outside_pr fill."""

import json
import sys
from pathlib import Path

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from fix.plan_model import PLAN_SCHEMA, ItemPlan, PlanMode, PlanVerdict
from fix.plan_store import clear, load, plan_path, save, with_paths


def _plan(**kw) -> ItemPlan:
    fields = dict(
        schema=PLAN_SCHEMA, item_id="T1", verdict=PlanVerdict.FIX,
        mode=PlanMode.ACT, reason="do it", files=("a.py",), change="edit f",
        test="test_f — asserts f", item_digest="abc", head_sha="def",
    )
    fields.update(kw)
    return ItemPlan(**fields)


def test_save_round_trips(tmp_path):
    plan = _plan()
    path = save(plan, tmp_path)
    assert path == plan_path(tmp_path, "T1")
    assert load(tmp_path, "T1") == plan


def test_save_is_atomic(tmp_path):
    """The dest is JSON, never a half-written file from this helper."""
    path = save(_plan(), tmp_path)
    raw = json.loads(path.read_text())
    assert raw["item_id"] == "T1"
    assert raw["verdict"] == "fix"


def test_missing_plan_loads_as_none(tmp_path):
    assert load(tmp_path, "nope") is None


def test_old_json_without_optional_fields_loads(tmp_path):
    path = plan_path(tmp_path, "T1")
    path.write_text(json.dumps({
        "schema": PLAN_SCHEMA, "item_id": "T1", "verdict": "fix",
        "mode": "act", "reason": "do it", "files": ["a.py"],
        "change": "edit f", "test": "t",
    }))
    loaded = load(tmp_path, "T1")
    assert loaded is not None
    assert loaded.outside_pr == ()
    assert loaded.item_digest == ""
    assert loaded.head_sha == ""
    assert loaded.model == ""


def test_clear_removes_plans(tmp_path):
    save(_plan(), tmp_path)
    save(_plan(item_id="T2"), tmp_path)
    clear(tmp_path)
    assert load(tmp_path, "T1") is None
    assert load(tmp_path, "T2") is None


def test_clear_of_a_missing_directory_is_a_noop(tmp_path):
    clear(tmp_path / "absent")


def test_with_paths_fills_outside_pr():
    plan = with_paths(_plan(files=("a.py", "b.py")), frozenset({"a.py"}))
    assert plan.outside_pr == ("b.py",)
