"""Tests for batch.store.reap: the only way a finished run directory is removed."""

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pytest  # noqa: E402

import batch.plan  # noqa: E402
import batch.store  # noqa: E402
import core.run_lock  # noqa: E402
from batch.model import Item, Run, RunStatus, Step, StepRecord  # noqa: E402
from batch.requests import Decide  # noqa: E402
from batch_session_support import seed_raw  # noqa: E402
from run_lock_support import held_elsewhere  # noqa: E402


def _yes(_run):
    return True


def _no(_run):
    return False


def _item():
    return Item(key="o/r#1", repo="o/r", repo_dir="/r", pr=1, branch="b", head_sha="aaa",
                steps=[StepRecord(Step.REBASE)])


def _run(run_id="r1", status=RunStatus.DONE):
    return Run(id=run_id, started_at="t", steps=[Step.REBASE], pool=1, auto_publish=[],
               status=status, items=[_item()],
               ref_namespace="refs/pr-batch/abcd", ref_dirs=["/r"])


def _put(run_id="r1", status=RunStatus.DONE):
    """Persist a finished run without a session — a terminal seed() would drop refs."""
    return seed_raw(_run(run_id, status))


def _tombstone(run_id):
    return batch.store.batch_root() / f"{run_id}.reaping"


def test_pending_requests_keep_the_run(monkeypatch):
    """A queued request keeps the run before it is ever renamed to a tombstone."""
    _put("r1")
    batch.store.write_request("r1", Decide(decision="d1", action="retry"))
    moved = []
    real = batch.store.os.replace

    def spy(src, dst):
        moved.append(dst)
        real(src, dst)

    monkeypatch.setattr(batch.store.os, "replace", spy)
    assert batch.store.reap("r1", expired=_yes, command="gc") is batch.store.ReapResult.PENDING
    assert moved == []
    assert batch.store.run_dir("r1").is_dir()
    assert not _tombstone("r1").exists()


def test_a_fresh_run_is_kept():
    _put("r1")
    assert batch.store.reap("r1", expired=_no, command="gc") is batch.store.ReapResult.FRESH
    assert batch.store.run_dir("r1").is_dir()


def test_a_busy_lock_keeps_the_run():
    _put("r1")
    record = {"pid": os.getppid(), "command": "pr batch run", "started": "t0",
              "released": None, "role": "driver"}
    lock = batch.store.run_dir("r1") / core.run_lock.LOCK_FILE
    with held_elsewhere(lock, record):
        assert batch.store.reap("r1", expired=_yes, command="gc") is batch.store.ReapResult.BUSY
    assert batch.store.run_dir("r1").is_dir()


def test_a_request_written_during_the_tombstone_window_keeps_the_run(monkeypatch):
    """A write that lands after the pending check but before rmtree is seen.

    `os.replace` is wrapped so the planted file appears in the tombstone
    immediately after the rename — the same place a racing `write_request`
    that beat the rename would now live.
    """
    _put("r1")
    real = os.replace

    def plant(src, dst):
        real(src, dst)
        dst_path = Path(dst)
        if not dst_path.name.endswith(".reaping"):
            return
        reqs = dst_path / "requests"
        reqs.mkdir(parents=True, exist_ok=True)
        (reqs / "late.json").write_text("{}")

    monkeypatch.setattr(batch.store.os, "replace", plant)
    assert batch.store.reap("r1", expired=_yes, command="gc") is batch.store.ReapResult.PENDING
    assert batch.store.run_dir("r1").is_dir()
    assert (batch.store.run_dir("r1") / "requests" / "late.json").is_file()
    assert not _tombstone("r1").exists()


def test_write_request_after_removal_does_not_recreate_the_dir():
    _put("r1")
    assert batch.store.reap("r1", expired=_yes, command="gc") is batch.store.ReapResult.REMOVED
    assert not batch.store.run_dir("r1").exists()
    assert not _tombstone("r1").exists()
    with pytest.raises(batch.store.RunNotFound):
        batch.store.write_request("r1", Decide(decision="d1", action="retry"))
    assert not batch.store.run_dir("r1").exists()


def test_refs_drop_failure_keeps_the_run(monkeypatch):
    _put("r1")
    monkeypatch.setattr(batch.plan, "drop_refs", lambda *a, **k: False)
    assert batch.store.reap("r1", expired=_yes, command="gc") is batch.store.ReapResult.REFS_KEPT
    assert batch.store.run_dir("r1").is_dir()
    assert not _tombstone("r1").exists()


def test_removed_leaves_no_dir_and_no_tombstone(monkeypatch):
    _put("r1")
    monkeypatch.setattr(batch.plan, "drop_refs", lambda *a, **k: True)
    assert batch.store.reap("r1", expired=_yes, command="gc") is batch.store.ReapResult.REMOVED
    assert not batch.store.run_dir("r1").exists()
    assert not _tombstone("r1").exists()


def test_run_ids_skips_tombstones():
    _put("live")
    tomb = _tombstone("dead")
    tomb.mkdir(parents=True)
    (tomb / batch.store.STATE_FILE).write_text("{}")
    assert batch.store.run_ids() == ["live"]


def test_a_missing_run_is_missing():
    assert batch.store.reap("nope", expired=_yes, command="gc") is batch.store.ReapResult.MISSING
