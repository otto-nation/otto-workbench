"""Tests for `otto_queue.ticket.adopt` and `adopt_job`.

`adopt` / `adopt_job` install signal handlers while a command runs, so they are called on the main thread.
"""

import contextlib
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import otto_queue.lease  # noqa: E402
import otto_queue.paths  # noqa: E402
from otto_queue.ledger import Ledger  # noqa: E402
from otto_queue.model import EntryKind, EntryState, NewEntry  # noqa: E402
from otto_queue.ticket import (  # noqa: E402
    AdoptOutcome, AdoptRefused, TicketOutcome, TicketRequest, adopt, adopt_job)
from otto_queue_support import Room, exit_argv, touch_argv  # noqa: E402

# Waits for a slot that never comes, announcing itself once it is waiting.
DRIVER = """
import sys
from types import SimpleNamespace
sys.path.insert(0, sys.argv[1])
from otto_queue.ledger import Ledger
import otto_queue.ticket

class Never:
    def limit(self, entry_class):
        return 2

    def verdict(self, entry_class, running):
        return SimpleNamespace(admit=False, reason="no room")

def say(_message):
    open(sys.argv[3], "w").write("waiting")

ledger = Ledger.open()
otto_queue.ticket.adopt_job(ledger, ledger.get(int(sys.argv[2])), Never(), wait_s=60, poll_s=0.05, notify=say)
"""


def _claimed_ticket(argv):
    ledger = Ledger.open()
    res = ledger.resolve(
        NewEntry(argv=tuple(argv), cwd=os.getcwd(), kind=EntryKind.TICKET),
        force=False, pid=os.getpid())
    return ledger, res


def _request(argv):
    return TicketRequest(argv=tuple(argv), cwd=os.getcwd())


def _job(argv):
    ledger = Ledger.open()
    res = ledger.resolve(NewEntry(argv=tuple(argv), cwd=os.getcwd(), kind=EntryKind.JOB), force=False, pid=None)
    return ledger, res.entry


def _adopt(ledger, entry, policy=None, wait_s=5.0):
    return adopt_job(ledger, entry, policy or Room(), wait_s=wait_s, poll_s=0.05, notify=lambda _m: None)


def test_an_adopted_job_runs_here_stays_a_job_and_logs(tmp_path):
    marker = tmp_path / "ran"
    ledger, entry = _job(touch_argv(marker))
    adoption = _adopt(ledger, entry)
    row = ledger.get(entry.id)
    assert (adoption.outcome, adoption.exit_status) == (AdoptOutcome.RAN, 0)
    assert (row.kind, row.state, row.attempts) == (EntryKind.JOB, EntryState.DONE, 1)
    assert row.log_path == str(otto_queue.paths.log_path(ledger.root, entry.id))
    assert marker.exists()


def test_an_adopted_job_that_exits_75_is_requeued():
    ledger, entry = _job(exit_argv(75))
    adoption = _adopt(ledger, entry)
    row = ledger.get(entry.id)
    assert (adoption.outcome, adoption.exit_status) == (AdoptOutcome.RETRY, 75)
    assert (row.state, row.exit_status) == (EntryState.QUEUED, 75)
    assert row.not_before is not None


def test_a_slot_timeout_leaves_a_queued_job_and_records_nothing(tmp_path):
    marker = tmp_path / "ran"
    ledger, entry = _job(touch_argv(marker))
    adoption = _adopt(ledger, entry, Room(admit=False), wait_s=0.3)
    row = ledger.get(entry.id)
    assert adoption.outcome is AdoptOutcome.TIMED_OUT
    assert (row.kind, row.state, row.exit_status) == (EntryKind.JOB, EntryState.QUEUED, None)
    assert not marker.exists()


def test_a_held_lease_means_another_runner_has_it(tmp_path):
    marker = tmp_path / "ran"
    ledger, entry = _job(touch_argv(marker))
    lease = otto_queue.lease.take(ledger.root, entry.id)
    try:
        adoption = _adopt(ledger, entry)
    finally:
        lease.release()
    assert adoption.outcome is AdoptOutcome.TAKEN
    assert not marker.exists()
    assert ledger.get(entry.id).state is EntryState.QUEUED
    assert _adopt(ledger, entry).outcome is AdoptOutcome.RAN


def test_a_job_cancelled_while_waiting_is_left(tmp_path):
    ledger, entry = _job(touch_argv(tmp_path / "ran"))
    timer = threading.Timer(0.2, lambda: ledger.cancel(entry.id))
    timer.start()
    try:
        adoption = _adopt(ledger, entry, Room(admit=False))
    finally:
        timer.join()
    assert adoption.outcome is AdoptOutcome.LEFT_QUEUE
    assert ledger.get(entry.id).state is EntryState.CANCELLED


def test_a_waiter_killed_before_admission_leaves_a_queued_job(tmp_path):
    ledger, entry = _job(touch_argv(tmp_path / "ran"))
    waiting = tmp_path / "waiting"
    proc = subprocess.Popen([sys.executable, "-c", DRIVER, str(LIB_DIR), str(entry.id), str(waiting)])
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
    row = ledger.get(entry.id)
    assert (row.kind, row.state, row.attempts) == (EntryKind.JOB, EntryState.QUEUED, 0)
    adoption = _adopt(ledger, row)
    assert adoption.outcome is AdoptOutcome.RAN
    assert ledger.get(entry.id).state is EntryState.DONE


def test_adopt_runs_a_claimed_ticket_and_records_done(tmp_path):
    marker = tmp_path / "ran"
    argv = touch_argv(marker)
    ledger, res = _claimed_ticket(argv)
    result = adopt(res.entry.id, res.lease.fd, _request(argv), Room(),
                   poll_s=0.05, notify=lambda _m: None)
    row = ledger.get(res.entry.id)
    assert (result.exit_status, result.outcome) == (0, TicketOutcome.RAN)
    assert (row.state, row.exit_status) == (EntryState.DONE, 0)
    assert marker.exists()


def test_a_forged_lease_fd_is_refused_and_leaves_the_entry(tmp_path):
    marker = tmp_path / "ran"
    argv = touch_argv(marker)
    ledger, res = _claimed_ticket(argv)
    forged = os.open(str(tmp_path / "forged"), os.O_RDWR | os.O_CREAT)
    try:
        with pytest.raises(AdoptRefused):
            adopt(res.entry.id, forged, _request(argv), Room(),
                  poll_s=0.05, notify=lambda _m: None)
        row = ledger.get(res.entry.id)
        assert (row.state, row.exit_status) == (EntryState.QUEUED, None)
        assert not marker.exists()
    finally:
        with contextlib.suppress(OSError):
            os.close(forged)
        res.lease.release()


def test_an_unknown_entry_id_is_refused():
    with pytest.raises(AdoptRefused):
        adopt(999, 0, _request(("true",)), Room(),
              poll_s=0.05, notify=lambda _m: None)
    assert Ledger.open().get(999) is None


def test_closing_the_lease_before_admission_fails_the_entry_after_reap():
    ledger, res = _claimed_ticket(("true",))
    assert ledger.get(res.entry.id).state is EntryState.QUEUED
    os.close(res.lease.fd)
    assert ledger.reap_orphans() == [res.entry.id]
    row = ledger.get(res.entry.id)
    assert (row.state, row.exit_status) == (EntryState.FAILED, None)
