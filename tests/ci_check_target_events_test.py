"""`pr ci` / `ci-check` files step_started/step_finished in its target's history.

Revert: delete `history.started()` in cli.ci_check.main; the started tests fail.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from conftest import make_ctx  # noqa: E402
import cli.ci_check  # noqa: E402
import cli.queue_gate  # noqa: E402
import core.proc  # noqa: E402
import core.run_lock  # noqa: E402
import core.run_events  # noqa: E402
import pr.ci_check  # noqa: E402
import pr.ci_runs  # noqa: E402
import pr.context  # noqa: E402
import rebase.ci_fix  # noqa: E402


@pytest.fixture
def target(tmp_path):
    path = tmp_path / "target"
    path.mkdir()
    return path


def _main(target, argv, *, pr_number=42, run_ci=None, fix_code=0, **main_kw):
    ctx = make_ctx(target_dir=target, worktree_root=target, pr_number=pr_number)
    with patch.object(pr.context, "resolve", return_value=ctx), \
         patch.object(core.run_lock, "claim_for_process"), \
         patch.object(pr.ci_check, "run_ci", side_effect=run_ci or (lambda *a: MagicMock())), \
         patch.object(rebase.ci_fix, "run_fix", return_value=fix_code):
        return cli.ci_check.main(argv, **main_kw)


def _kinds(target):
    return [(e["kind"], e["step"], e.get("status"), e.get("exit_code"))
            for e in core.run_events.read(target).events]


def test_a_batch_ci_step_without_fix_lands_in_the_target_history(target):
    assert _main(target, ["--no-rebase", "--head-sha", "abc"], install_signal_handler=False) == 0
    assert _kinds(target) == [("step_started", "ci", None, None), ("step_finished", "ci", "done", 0)]
    assert {e["item"] for e in core.run_events.read(target).events} == {"owner/repo#42"}


def test_a_fix_run_files_its_exit_code(target):
    assert _main(target, ["--fix"], fix_code=4, install_signal_handler=False) == 4
    assert _kinds(target)[-1] == ("step_finished", "ci", "failed", 4)


def test_no_run_to_report_is_exit_1_and_filed(target):
    def gone(*a):
        raise pr.ci_runs.RunUnavailable("No checks found")

    assert _main(target, [], run_ci=gone, install_signal_handler=False) == 1
    assert _kinds(target)[-1] == ("step_finished", "ci", "failed", 1)


def test_a_branch_with_no_pr_files_nothing(target):
    assert _main(target, [], pr_number=None, install_signal_handler=False) == 0
    assert not core.run_events.events_path(target).exists()


def test_the_queued_parent_files_nothing_and_the_admitted_child_files_once(target, monkeypatch):
    monkeypatch.setattr(core.proc, "install_stop_handler", lambda *a: None)
    with patch.object(cli.queue_gate, "gate", return_value=0):
        assert _main(target, ["--fix"]) == 0
    assert not core.run_events.events_path(target).exists()
    monkeypatch.setenv(cli.queue_gate.QUEUED_MARKER, "1")
    assert _main(target, ["--fix"]) == 0
    assert [k for k, *_ in _kinds(target)] == ["step_started", "step_finished"]
