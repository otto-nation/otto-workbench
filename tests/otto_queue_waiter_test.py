"""Tests for `otto_queue.waiter.wait_entry`: following a submitted entry to its status.

`wait_entry` may run a job itself, which installs signal handlers, so it is called on the main thread.
"""

import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import otto_queue.lease  # noqa: E402
import otto_queue.paths  # noqa: E402
from otto_queue.ledger import Ledger  # noqa: E402
from otto_queue.model import EntryKind, EntryState, NewEntry  # noqa: E402
from otto_queue.waiter import WaitOutcome, wait_entry  # noqa: E402
from otto_queue_support import Room, exit_argv, touch_argv  # noqa: E402

# Waits on a job with a policy that never admits, announcing itself once it is waiting.
DRIVER = """
import sys
from types import SimpleNamespace
sys.path.insert(0, sys.argv[1])
import otto_queue.waiter

class Never:
    def limit(self, entry_class):
        return 2

    def verdict(self, entry_class, running):
        return SimpleNamespace(admit=False, reason="no room")

def say(message):
    if "running entry" in message:
        open(sys.argv[3], "w").write("waiting")

otto_queue.waiter.wait_entry(int(sys.argv[2]), Never(), wait_s=60, poll_s=0.05, notify=say)
"""


def _job(argv):
    ledger = Ledger.open()
    res = ledger.resolve(NewEntry(argv=tuple(argv), cwd=os.getcwd(), kind=EntryKind.JOB), force=False, pid=None)
    return ledger, res.entry.id


def _wait(entry_id, policy=None, messages=None, wait_s=5.0):
    sink = messages if messages is not None else []
    return wait_entry(entry_id, policy or Room(), wait_s=wait_s, poll_s=0.05, notify=sink.append)


def test_a_finished_entry_returns_its_status_at_once():
    ledger, entry_id = _job(exit_argv(0))
    assert ledger.claim_as_ticket(entry_id, os.getpid()) is not None
    ledger.mark_running(entry_id, os.getpid())
    ledger.finish(entry_id, 4)
    result = _wait(entry_id)
    assert (result.exit_status, result.outcome) == (4, WaitOutcome.FINISHED)


def test_an_unknown_id_is_not_found():
    Ledger.open()
    result = _wait(999)
    assert (result.exit_status, result.outcome) == (1, WaitOutcome.NOT_FOUND)


def test_a_queued_job_with_no_worker_is_run_by_the_waiter(tmp_path):
    messages = []
    ledger, entry_id = _job(exit_argv(3))
    result = _wait(entry_id, messages=messages)
    entry = ledger.get(entry_id)
    assert (result.exit_status, result.outcome) == (3, WaitOutcome.FINISHED)
    assert (entry.kind, entry.state, entry.exit_status) == (EntryKind.JOB, EntryState.FAILED, 3)
    assert str(otto_queue.paths.log_path(ledger.root, entry_id)) in messages[0]
    assert "attempt 1" in Path(entry.log_path).read_text()


def test_a_cancelled_entry_returns_130():
    ledger, entry_id = _job(touch_argv("/nonexistent/never"))
    ledger.cancel(entry_id)
    result = _wait(entry_id)
    assert (result.exit_status, result.outcome) == (130, WaitOutcome.FINISHED)


def test_a_timeout_leaves_it_queued_and_returns_75(tmp_path):
    messages = []
    ledger, entry_id = _job(touch_argv(tmp_path / "ran"))
    result = _wait(entry_id, Room(admit=False), messages, wait_s=0.3)
    entry = ledger.get(entry_id)
    assert (result.exit_status, result.outcome) == (75, WaitOutcome.TIMED_OUT)
    assert (entry.state, entry.exit_status) == (EntryState.QUEUED, None)
    assert any("again" in m for m in messages)
    assert not (tmp_path / "ran").exists()


def test_a_rewait_after_a_killed_waiter_runs_the_same_entry(tmp_path):
    ledger, entry_id = _job(touch_argv(tmp_path / "ran"))
    waiting = tmp_path / "waiting"
    proc = subprocess.Popen([sys.executable, "-c", DRIVER, str(LIB_DIR), str(entry_id), str(waiting)])
    try:
        deadline = time.monotonic() + 20
        while not waiting.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert waiting.exists()
        proc.send_signal(signal.SIGKILL)
    finally:
        proc.kill()
        proc.wait()
    ledger.reap_orphans()
    row = ledger.get(entry_id)
    assert (row.kind, row.state) == (EntryKind.JOB, EntryState.QUEUED)
    result = _wait(entry_id)
    assert (result.exit_status, result.entry_id) == (0, entry_id)
    assert ledger.get(entry_id).state is EntryState.DONE
    assert (tmp_path / "ran").exists()


def test_waiting_on_a_job_the_worker_runs_returns_its_status(tmp_path):
    ledger, entry_id = _job(touch_argv(tmp_path / "ran"))
    worker = otto_queue.lease.take_worker(ledger.root)

    def worker_runs_it():
        ledger.mark_running(entry_id, os.getpid())
        ledger.finish(entry_id, 4)

    timer = threading.Timer(0.3, worker_runs_it)
    timer.start()
    try:
        result = _wait(entry_id)
    finally:
        timer.join()
        worker.release()
    assert (result.exit_status, result.outcome) == (4, WaitOutcome.FINISHED)
    assert not (tmp_path / "ran").exists()
