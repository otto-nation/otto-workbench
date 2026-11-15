"""`pr.target_events`: a step's start and finish filed in its PR target's history.

Revert: drop the `is_dir()` guard in StepHistory._file; the missing-directory
test then warns on stderr and fails.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from conftest import make_ctx  # noqa: E402
import core.run_events  # noqa: E402
import pr.target_events  # noqa: E402

AT = "2026-11-14T00:00:00+00:00"


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(core.run_events, "now_iso", lambda: AT)
    monkeypatch.setattr(pr.target_events, "_warned", set())


def _events(target):
    return core.run_events.read(target).events


def test_record_is_the_core_envelope_for_target_kinds():
    assert pr.target_events.record("decision", run="inv", item="o/r#1", verdict="fixed") == {
        "schema_version": 1, "kind": "decision", "at": AT, "run": "inv",
        "item": "o/r#1", "verdict": "fixed"}
    with pytest.raises(ValueError):
        pr.target_events.record("run_started", run="inv", item="o/r#1")


@pytest.mark.parametrize("code, status", [(0, "done"), (3, "failed"), (None, "failed")])
def test_started_and_finished_are_filed_under_the_pr(tmp_path, code, status):
    target = tmp_path / "t"
    target.mkdir()
    history = pr.target_events.step_history(make_ctx(target_dir=target), "inv-1", "ci")
    history.started()
    history.finished(code)
    assert _events(target) == [
        {"schema_version": 1, "kind": "step_started", "at": AT, "run": "inv-1",
         "item": "owner/repo#42", "step": "ci"},
        {"schema_version": 1, "kind": "step_finished", "at": AT, "run": "inv-1",
         "item": "owner/repo#42", "step": "ci", "status": status, "exit_code": code},
    ]


def test_a_branch_with_no_pr_files_nothing(tmp_path):
    target = tmp_path / "t"
    target.mkdir()
    history = pr.target_events.step_history(make_ctx(target_dir=target, pr_number=None),
                                            "inv", "comments")
    assert history is pr.target_events.NO_HISTORY
    history.started()
    history.finished(0)
    assert not core.run_events.events_path(target).exists()


def test_a_target_directory_that_does_not_exist_is_not_created_or_warned_about(tmp_path, capsys):
    target = tmp_path / "never-claimed"
    history = pr.target_events.step_history(make_ctx(target_dir=target), "inv", "ci")
    history.started()
    history.finished(0)
    assert not target.exists()
    assert capsys.readouterr().err == ""


def test_a_failed_append_warns_once_per_target_and_never_raises(tmp_path, capsys):
    target = tmp_path / "t"
    target.mkdir()
    # A directory where the file should be: every append raises IsADirectoryError.
    core.run_events.events_path(target).mkdir()
    history = pr.target_events.step_history(make_ctx(target_dir=target), "inv", "ci")
    history.started()
    history.finished(0)
    err = capsys.readouterr().err
    assert err.count("pr: could not record events for owner/repo#42") == 1
    assert "the command continues" in err
