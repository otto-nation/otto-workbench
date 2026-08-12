"""Tests for the worktree run lock."""

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pytest

import worktree_lock
from worktree_lock import LOCK_ENV, LOCK_FILE, LockBusy, acquire


@pytest.fixture(autouse=True)
def _clear_lock_env():
    """Never inherit a real run's lock marker into a test."""
    saved = os.environ.pop(LOCK_ENV, None)
    yield
    os.environ.pop(LOCK_ENV, None)
    if saved is not None:
        os.environ[LOCK_ENV] = saved


def test_acquire_is_noop_without_worktree():
    with acquire(None, command="pr status", started="t"):
        assert LOCK_ENV not in os.environ


def test_acquire_creates_lock_file_and_marks_env(tmp_path):
    with acquire(tmp_path, command="pr review", started="2026-08-12T00:00:00+00:00"):
        assert os.environ[LOCK_ENV] == str(tmp_path.resolve())
        record = json.loads((tmp_path / ".workbench" / LOCK_FILE).read_text())
        assert record["pid"] == os.getpid()
        assert record["command"] == "pr review"
        assert record["started"] == "2026-08-12T00:00:00+00:00"


def test_env_marker_cleared_after_release(tmp_path):
    with acquire(tmp_path, command="pr review", started="t"):
        pass
    assert LOCK_ENV not in os.environ


def test_lock_is_released_for_the_next_run(tmp_path):
    with acquire(tmp_path, command="pr review", started="t"):
        pass
    # A sequential second run must not see the first as a live holder.
    with acquire(tmp_path, command="pr comments", started="t"):
        record = json.loads((tmp_path / ".workbench" / LOCK_FILE).read_text())
        assert record["command"] == "pr comments"


def test_concurrent_acquire_raises_lock_busy(tmp_path):
    """flock conflicts across open file descriptions, so a second acquire
    fails even from this same process once the env marker is cleared."""
    with acquire(tmp_path, command="pr review --fix", started="2026-08-12T07:21:19+00:00"):
        del os.environ[LOCK_ENV]
        with pytest.raises(LockBusy) as excinfo:
            with acquire(tmp_path, command="pr review --fix", started="t"):
                pytest.fail("second acquire should not have entered the block")

    exc = excinfo.value
    assert exc.holder["pid"] == os.getpid()
    assert exc.holder["command"] == "pr review --fix"
    assert "pr review --fix" in str(exc)
    assert "2026-08-12T07:21:19+00:00" in str(exc)


def test_reentrant_acquire_in_same_process_tree(tmp_path):
    """Delegates inherit LOCK_ENV and must pass straight through."""
    with acquire(tmp_path, command="pr review", started="t"):
        with acquire(tmp_path, command="claude-review", started="t"):
            # The child must not overwrite the parent's ownership record.
            record = json.loads((tmp_path / ".workbench" / LOCK_FILE).read_text())
            assert record["command"] == "pr review"
        # Leaving the inner block must not release the parent's lock.
        assert os.environ[LOCK_ENV] == str(tmp_path.resolve())


def test_reentrancy_is_keyed_on_the_worktree(tmp_path):
    """A different worktree is a different lock, not a free pass."""
    other = tmp_path / "other"
    other.mkdir()
    with acquire(tmp_path, command="pr review", started="t"):
        with acquire(other, command="pr review", started="t"):
            assert (other / ".workbench" / LOCK_FILE).exists()


def test_lock_released_when_body_raises(tmp_path):
    with pytest.raises(RuntimeError):
        with acquire(tmp_path, command="pr review", started="t"):
            raise RuntimeError("boom")
    assert LOCK_ENV not in os.environ
    with acquire(tmp_path, command="pr review", started="t"):
        pass


def test_report_busy_names_the_holder(tmp_path, capsys):
    exc = LockBusy({"pid": 4242, "command": "pr review", "started": "t"}, tmp_path)
    worktree_lock.report_busy(exc)
    err = capsys.readouterr().err
    assert "pr review" in err
    assert "4242" in err


def test_lock_busy_tolerates_an_unreadable_holder_record(tmp_path):
    """The flock enforces exclusion; a garbled record must still report."""
    lock_path = tmp_path / ".workbench" / LOCK_FILE
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text("not json")
    with acquire(tmp_path, command="pr review", started="t"):
        del os.environ[LOCK_ENV]
        lock_path.write_text("not json")
        with pytest.raises(LockBusy) as excinfo:
            with acquire(tmp_path, command="pr review", started="t"):
                pytest.fail("second acquire should not have entered the block")
    assert "unknown command" in str(excinfo.value)
