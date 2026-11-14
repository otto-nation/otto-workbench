"""`batch.events.emit` prints what it always printed and files the same line in the run's history.

Revert: delete the `_append(line)` call in `emit`.
"""

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.events  # noqa: E402
import batch.store  # noqa: E402
import core.run_events  # noqa: E402

RID = "20261114T000000-abcd"
AT = "2026-11-14T00:00:00+00:00"


@pytest.fixture
def clock(monkeypatch):
    monkeypatch.setattr(batch.events, "now_iso", lambda: AT)
    monkeypatch.setattr(batch.events, "_warned", set())


def _run_dir():
    path = batch.store.run_dir(RID)
    path.mkdir(parents=True)
    return path


def test_stdout_is_unchanged_and_the_history_holds_the_same_line(clock, capsys):
    path = _run_dir()
    batch.events.emit("step_started", run=RID, item="o/r#1", step="rebase",
                      argv=["pr", "rebase"], log_path="/logs/x.log")
    expected = json.dumps({
        "schema_version": 1, "kind": "step_started", "at": AT, "run": RID,
        "item": "o/r#1", "step": "rebase", "argv": ["pr", "rebase"],
        "log_path": "/logs/x.log",
    }) + "\n"
    assert capsys.readouterr().out == expected
    assert core.run_events.events_path(path).read_text() == expected


def test_run_summary_is_filed_under_its_run_views_id(clock, capsys):
    path = _run_dir()
    batch.events.emit("run_summary", run={"id": RID, "status": "done"}, counts={})
    assert [e["kind"] for e in core.run_events.read(path).events] == ["run_summary"]


def test_an_event_naming_no_run_is_printed_but_not_filed(clock, capsys):
    path = _run_dir()
    batch.events.emit("item_skipped_held", item="o/r#1", holder=RID)
    assert '"item_skipped_held"' in capsys.readouterr().out
    assert not core.run_events.events_path(path).exists()


def test_a_failed_append_warns_once_per_run_and_never_raises(clock, capsys):
    # No run directory: every append fails.
    batch.events.emit("run_finished", run=RID, status="done")
    batch.events.emit("run_finished", run=RID, status="done")
    captured = capsys.readouterr()
    assert captured.out.count('"run_finished"') == 2
    assert captured.err.count("could not record") == 1
    assert RID in captured.err


def test_decision_is_a_known_kind_and_record_is_the_envelope(clock):
    assert batch.events.record("decision", run=RID, item="o/r#1") == {
        "schema_version": 1, "kind": "decision", "at": AT, "run": RID, "item": "o/r#1"}
    with pytest.raises(ValueError):
        batch.events.record("not_a_kind")


def test_the_latest_decision_per_item_and_finding_wins():
    events = [
        {"kind": "decision", "item": "o/r#1", "finding": "ci_failing:ci", "verdict": "deferred"},
        {"kind": "step_started", "item": "o/r#1"},
        {"kind": "decision", "item": "o/r#1", "finding": "ci_failing:ci", "verdict": "fixed"},
        {"kind": "decision", "item": "o/r#2", "finding": "ci_failing:ci", "verdict": "dismissed"},
    ]
    latest = batch.events.latest_decisions(events)
    assert set(latest) == {("o/r#1", "ci_failing:ci"), ("o/r#2", "ci_failing:ci")}
    assert latest[("o/r#1", "ci_failing:ci")]["verdict"] == "fixed"
