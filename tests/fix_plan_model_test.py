"""Plan records: validity, digest stability, path classification."""

import hashlib
import sys
from pathlib import Path

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from fix.plan_model import (
    PLAN_SCHEMA, ItemPlan, PlanMode, PlanVerdict, classify_paths, item_digest,
    unsafe_relpath,
)
from fix.types import FixItem


def _plan(**kw) -> ItemPlan:
    fields = dict(
        schema=PLAN_SCHEMA, item_id="t1", verdict=PlanVerdict.FIX,
        mode=PlanMode.ACT, reason="do it", files=("a.py",), change="edit f",
        test="test_f — asserts f",
    )
    fields.update(kw)
    return ItemPlan(**fields)


def test_fix_needs_files_and_change():
    assert _plan().valid
    assert not _plan(files=()).valid
    assert not _plan(change="").valid
    assert not _plan(change="  ").valid


def test_needs_human_and_dismiss_need_a_reason():
    assert _plan(verdict=PlanVerdict.NEEDS_HUMAN, files=(), change="").valid
    assert not _plan(
        verdict=PlanVerdict.NEEDS_HUMAN, files=(), change="", reason="",
    ).valid
    assert _plan(verdict=PlanVerdict.DISMISS, files=(), change="").valid
    assert not _plan(
        verdict=PlanVerdict.DISMISS, files=(), change="", reason="",
    ).valid


def test_already_addressed_needs_a_reason():
    assert _plan(
        verdict=PlanVerdict.ALREADY_ADDRESSED, files=(), change="",
        reason="a.py:3",
    ).valid
    assert not _plan(
        verdict=PlanVerdict.ALREADY_ADDRESSED, files=(), change="", reason="",
    ).valid


def test_wrong_schema_is_invalid():
    assert not _plan(schema=0).valid


def test_digest_is_sha256_of_the_rendered_item():
    item = FixItem(id="t1", file="a.py", line=3, label="@r", body="do the thing")
    payload = "t1|a.py|3|@r|do the thing"
    assert item_digest(item) == hashlib.sha256(payload.encode()).hexdigest()


def test_digest_is_stable_across_calls():
    item = FixItem(id="t1", file="a.py", line=3, label="@r", body="x")
    assert item_digest(item) == item_digest(item)


def test_parent_absolute_and_git_paths_are_rejected():
    assert unsafe_relpath("../secret")
    assert unsafe_relpath("/etc/passwd")
    assert unsafe_relpath(".git/config")
    assert unsafe_relpath("src/.git/hooks")
    assert not unsafe_relpath("ai/lib/fix.py")


def test_outside_pr_is_the_in_tree_subset_not_on_the_pr():
    rejected, outside = classify_paths(
        ("a.py", "tests/a_test.py", "../x", "/abs"),
        pr_files=frozenset({"a.py"}),
    )
    assert rejected == ("../x", "/abs")
    assert outside == ("tests/a_test.py",)


def test_unknown_pr_files_flags_nothing_outside():
    rejected, outside = classify_paths(("a.py", "b.py"), pr_files=None)
    assert rejected == ()
    assert outside == ()
