"""Tests for the vacated-holder retry and the flock errno split."""

import errno
import fcntl
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pytest  # noqa: E402

import core.run_lock  # noqa: E402
from core.run_lock import Busy, LockBusy  # noqa: E402
from run_lock_support import held_elsewhere, live_pid  # noqa: E402


def test_a_released_record_is_retried_until_the_flock_frees(tmp_path):
    """A vacated flock (released record, still held from another descriptor) is waited out."""
    path = tmp_path / core.run_lock.LOCK_FILE
    handle = open(path, "a+")
    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    json.dump(
        {"pid": 1, "command": "probe", "started": "t", "released": "already"},
        handle,
    )
    handle.flush()

    def unlock():
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()

    timer = threading.Timer(0.1, unlock)
    timer.start()
    try:
        with core.run_lock.acquire(tmp_path, command="pr x", started="t"):
            record = json.loads(path.read_text())
        assert record["command"] == "pr x"
        assert record["released"] is None
    finally:
        timer.join()


def test_a_live_holder_is_refused_with_no_sleep(tmp_path, monkeypatch):
    slept = []
    monkeypatch.setattr(core.run_lock.time, "sleep", slept.append)
    path = tmp_path / core.run_lock.LOCK_FILE
    with live_pid() as pid, \
         held_elsewhere(path, {
             "pid": pid, "command": "pr review", "started": "t", "released": None,
         }), \
         pytest.raises(LockBusy):
        with core.run_lock.acquire(tmp_path, command="pr x", started="t"):
            pytest.fail("a live holder must refuse without waiting")
    assert slept == []


def test_a_dead_pid_with_released_null_is_vacated(tmp_path, monkeypatch):
    proc = subprocess.Popen(["sleep", "30"])
    pid = proc.pid
    proc.kill()
    proc.wait()
    slept = []
    monkeypatch.setattr(core.run_lock.time, "sleep", slept.append)
    path = tmp_path / core.run_lock.LOCK_FILE
    with held_elsewhere(path, {
            "pid": pid, "command": "gone", "started": "t", "released": None,
         }), pytest.raises(LockBusy) as exc:
        with core.run_lock.acquire(tmp_path, command="pr x", started="t"):
            pytest.fail("a dead pid must not be treated as a live holder")
    assert slept
    assert core.run_lock.classify_busy(exc.value) is Busy.VACATED


def test_the_vacated_budget_is_honoured(tmp_path, monkeypatch):
    pauses = []
    monkeypatch.setattr(core.run_lock.time, "sleep", pauses.append)
    real = core.run_lock.fcntl.flock

    def busy_flock(fd, flags):
        if flags == fcntl.LOCK_EX | fcntl.LOCK_NB:
            raise BlockingIOError()
        return real(fd, flags)

    monkeypatch.setattr(core.run_lock.fcntl, "flock", busy_flock)
    with pytest.raises(LockBusy):
        with core.run_lock.acquire(tmp_path, command="pr x", started="t"):
            pytest.fail("must give up when the flock never frees")
    assert pauses
    assert sum(pauses) <= core.run_lock.VACATED_BUDGET_S
    assert max(pauses) <= core.run_lock.VACATED_PAUSE_MAX_S


def test_a_non_blocking_io_oserror_propagates(tmp_path, monkeypatch):
    def no_locks(fd, flags):
        raise OSError(errno.ENOLCK, "No locks available")

    monkeypatch.setattr(core.run_lock.fcntl, "flock", no_locks)
    with pytest.raises(OSError) as exc:
        with core.run_lock.acquire(tmp_path, command="pr x", started="t"):
            pytest.fail("ENOLCK must not become LockBusy")
    assert exc.value.errno == errno.ENOLCK


def test_retry_delays_sum_to_at_most_the_budget():
    delays = list(core.run_lock.retry_delays())
    assert delays[0] == pytest.approx(core.run_lock.VACATED_PAUSE_S)
    assert delays[0] == pytest.approx(0.01)
    assert sum(delays) <= core.run_lock.VACATED_BUDGET_S
    assert all(d <= core.run_lock.VACATED_PAUSE_MAX_S for d in delays)


def test_is_live_table(monkeypatch):
    self_pid = os.getpid()
    live_proc = subprocess.Popen(["sleep", "30"])
    dead_proc = subprocess.Popen(["sleep", "30"])
    dead_proc.kill()
    dead_proc.wait()

    def deny(pid, sig):
        raise PermissionError

    try:
        cases = (
            ({}, False, False),
            ({"released": "now", "pid": live_proc.pid}, False, False),
            ({"released": None, "pid": "x"}, False, False),
            ({"released": None, "pid": self_pid}, False, False),
            ({"released": None, "pid": self_pid}, True, True),
            ({"released": None, "pid": dead_proc.pid}, False, False),
            ({"released": None, "pid": live_proc.pid}, False, True),
            ({"released": None, "pid": live_proc.pid}, True, True),
        )
        for record, allow_self, expected in cases:
            assert core.run_lock.is_live(record, allow_self=allow_self) is expected
        monkeypatch.setattr(core.run_lock.os, "kill", deny)
        assert core.run_lock.is_live({"released": None, "pid": 1}) is True
    finally:
        # Undo before kill: patching core.run_lock.os.kill patches os.kill for the
        # whole process, and Popen.kill() goes through it.
        monkeypatch.undo()
        live_proc.kill()
        live_proc.wait()
