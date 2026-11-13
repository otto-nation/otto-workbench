"""The run-session recipe and `note`'s checks.

Revert: in recipe, use `run.items[0].repo_dir` instead of checkout_of(run); in note,
skip the finding_keys membership check.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.run_session  # noqa: E402
import batch.store  # noqa: E402
import core.run_events  # noqa: E402
from batch.model import Finding, FindingReason, Verdict  # noqa: E402
from batch_report_support import make_item, make_run  # noqa: E402


def test_recipe_names_the_session_after_the_run_and_its_repo():
    run = make_run([make_item(1)], run_id="20261114T000000-abcd")
    run.checkouts = ["/r"]
    got = batch.run_session.recipe(run)
    sid = "batch-20261114T000000-abcd"
    name = "batch o/r 20261114T000000-abcd"
    assert got.to_dict() == {
        "schema_version": 1, "session_id": sid, "name": name, "cwd": "/r",
        "argv": ["pi", "--session-id", sid, "--name", name],
        "env": {"WORKBENCH_RUN_DIR": str(batch.store.run_dir(run.id))},
    }


def test_a_run_saved_before_checkouts_uses_its_items_checkout():
    run = make_run([make_item(1, repo_dir="/abs/r"), make_item(2, repo_dir="/abs/r")])
    assert batch.run_session.recipe(run).cwd == "/abs/r"


def test_the_recorded_checkout_wins_over_the_items():
    run = make_run([make_item(1, repo_dir="/item/r")])
    run.checkouts = ["/given/r"]
    assert batch.run_session.recipe(run).cwd == "/given/r"


def test_a_run_over_two_checkouts_has_no_run_session():
    run = make_run([make_item(1)])
    run.checkouts = ["/a", "/b"]
    with pytest.raises(batch.run_session.RunSessionError, match="2 checkouts"):
        batch.run_session.recipe(run)


def test_a_run_over_two_repos_has_no_run_session():
    run = make_run([make_item(1), make_item(2, key="x/y#2", repo="x/y")])
    run.checkouts = ["/r"]
    with pytest.raises(batch.run_session.RunSessionError, match="2 repos"):
        batch.run_session.recipe(run)


def test_a_run_with_no_items_is_named_after_its_checkout():
    run = make_run([], run_id="r9")
    run.checkouts = ["/src/widget"]
    assert batch.run_session.recipe(run).name == "batch widget r9"


def _noted_run():
    item = make_item(1, findings=[Finding(FindingReason.CI_FAILING, "ci")])
    run = make_run([item], run_id="20261114T000000-abcd")
    batch.store.run_dir(run.id).mkdir(parents=True)
    return run, item


def test_note_appends_one_decision_and_returns_it():
    run, item = _noted_run()
    rec = batch.run_session.note(run, item, "ci_failing:ci", Verdict.DEFERRED,
                                 "  flaky runner  ", ["pr ci --fix"])
    assert rec["kind"] == "decision"
    assert {k: rec[k] for k in ("run", "item", "finding", "verdict", "reason", "commands")} == {
        "run": run.id, "item": "o/r#1", "finding": "ci_failing:ci", "verdict": "deferred",
        "reason": "flaky runner", "commands": ["pr ci --fix"]}
    assert core.run_events.read(batch.store.run_dir(run.id)).events == [rec]


def test_note_accepts_a_finding_the_report_derives():
    run, item = _noted_run()
    rec = batch.run_session.note(run, item, "closeout_owed:comments", Verdict.FIXED, "posted")
    assert rec["finding"] == "closeout_owed:comments"


def test_note_refuses_a_finding_the_item_does_not_have():
    run, item = _noted_run()
    with pytest.raises(batch.run_session.RunSessionError, match="review_findings:review"):
        batch.run_session.note(run, item, "review_findings:review", Verdict.FIXED, "done")
    assert core.run_events.read(batch.store.run_dir(run.id)).events == []


def test_note_refuses_an_empty_reason():
    run, item = _noted_run()
    with pytest.raises(batch.run_session.RunSessionError, match="reason"):
        batch.run_session.note(run, item, "ci_failing:ci", Verdict.FIXED, "   ")
