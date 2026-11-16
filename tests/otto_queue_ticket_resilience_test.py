"""Tests for otto_queue.ticket: waiting through ledger contention."""

import os
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import core.signal_relay  # noqa: E402
import otto_queue.ledger  # noqa: E402
import otto_queue.ticket  # noqa: E402
from otto_queue.ledger import Ledger, LedgerBusy, LedgerError  # noqa: E402
from otto_queue.model import (  # noqa: E402
    Entry, EntryClass, EntryKind, EntryState, NewEntry)
from otto_queue.slots import Admission  # noqa: E402
from otto_queue.ticket import (  # noqa: E402
    FAST_WAIT_S, POLL_CAP_S, POLL_S, REAP_EVERY_S, TicketOutcome, TicketRequest,
    poll_delay)
from otto_queue_support import Room, exit_argv, touch_argv  # noqa: E402


def _ticket(tmp_path, argv, **kw):
    request = TicketRequest(argv=tuple(argv), cwd=os.getcwd(), **kw)
    messages = []
    result = otto_queue.ticket.run_ticket(
        request, Room(), root=tmp_path, poll_s=0.05, notify=messages.append)
    return result, messages


def test_wait_admits_after_transient_get_failures(tmp_path, monkeypatch):
    real = Ledger.wait_poll
    seen = {"n": 0}

    def flaky(self, entry_id, entry_class, *, jobs=False, now=None):
        seen["n"] += 1
        if seen["n"] <= 2:
            raise LedgerBusy("busy")
        return real(self, entry_id, entry_class, jobs=jobs, now=now)

    monkeypatch.setattr(Ledger, "wait_poll", flaky)
    result, messages = _ticket(tmp_path, exit_argv(0))
    assert (result.exit_status, result.outcome) == (0, TicketOutcome.RAN)
    assert Ledger.open(tmp_path).get(result.entry_id).state is EntryState.DONE
    assert any("ledger busy; still waiting" in m for m in messages)


def test_wait_returns_75_when_the_ledger_stays_busy(tmp_path, monkeypatch):
    def boom(self, *_a, **_k):
        raise LedgerBusy("busy")

    monkeypatch.setattr(Ledger, "wait_poll", boom)
    result, _ = _ticket(tmp_path, touch_argv(tmp_path / "ran"), wait_s=0.3)
    assert result.exit_status == os.EX_TEMPFAIL
    assert result.outcome is TicketOutcome.TIMED_OUT
    assert not (tmp_path / "ran").exists()


def test_finish_records_after_one_transient_failure(tmp_path, monkeypatch):
    remaining = [1]
    armed = [False]
    real = otto_queue.ledger._connect

    def flaky(root, *, timeout):
        if armed[0] and remaining[0] > 0:
            remaining[0] -= 1
            exc = sqlite3.OperationalError("locking protocol")
            exc.sqlite_errorcode = sqlite3.SQLITE_PROTOCOL
            exc.sqlite_errorname = "SQLITE_PROTOCOL"
            raise exc
        return real(root, timeout=timeout)

    def run_child(*_a, **_k):
        armed[0] = True
        return 3

    monkeypatch.setattr(otto_queue.ledger, "_connect", flaky)
    monkeypatch.setattr(otto_queue.ledger, "_sleep", lambda _s: None)
    monkeypatch.setattr(core.signal_relay, "run_child", run_child)
    result, _ = _ticket(tmp_path, exit_argv(3))
    assert result.exit_status == 3
    entry = Ledger.open(tmp_path).get(result.entry_id)
    assert (entry.state, entry.exit_status) == (EntryState.FAILED, 3)


def test_command_exit_is_kept_when_finish_cannot_record(tmp_path, monkeypatch):
    def boom(self, entry_id, code):
        raise LedgerBusy("busy")

    monkeypatch.setattr(Ledger, "finish", boom)
    result, _ = _ticket(tmp_path, exit_argv(3))
    assert result.exit_status == 3
    assert result.outcome is TicketOutcome.RAN


def test_record_crash_reraises_the_original_when_fail_raises(tmp_path, monkeypatch):
    def boom_run(*_a, **_k):
        raise RuntimeError("queue crashed")

    def boom_fail(self, entry_id):
        raise LedgerError("cannot record")

    monkeypatch.setattr(core.signal_relay, "run_child", boom_run)
    monkeypatch.setattr(Ledger, "fail", boom_fail)
    with pytest.raises(RuntimeError, match="queue crashed"):
        _ticket(tmp_path, exit_argv(0))


class _NoJitter:
    def uniform(self, a, b):
        assert (a, b) == (0.8, 1.2)
        return 1.0


def test_poll_delay_stays_at_poll_s_for_the_first_fast_window():
    rng = _NoJitter()
    assert poll_delay(0.0, rng) == POLL_S
    assert poll_delay(FAST_WAIT_S - 0.01, rng) == POLL_S
    assert poll_delay(FAST_WAIT_S, rng) == POLL_S


def test_poll_delay_doubles_geometrically_then_caps():
    rng = _NoJitter()
    assert poll_delay(FAST_WAIT_S + POLL_S, rng) == POLL_S * 2
    assert poll_delay(FAST_WAIT_S + 2 * POLL_S, rng) == POLL_S * 4
    assert poll_delay(FAST_WAIT_S + 3 * POLL_S, rng) == POLL_CAP_S
    assert poll_delay(60.0, rng) == POLL_CAP_S


def test_poll_delay_applies_jitter_without_exceeding_the_cap():
    high = SimpleNamespace(uniform=lambda a, b: 1.2)
    low = SimpleNamespace(uniform=lambda a, b: 0.8)
    assert poll_delay(0.0, high) == pytest.approx(POLL_S * 1.2)
    assert poll_delay(0.0, low) == pytest.approx(POLL_S * 0.8)
    assert poll_delay(60.0, high) == POLL_CAP_S
    assert poll_delay(60.0, low) == pytest.approx(POLL_CAP_S * 0.8)


def test_poll_sleep_never_sleeps_past_the_deadline(monkeypatch):
    slept = []
    monkeypatch.setattr(otto_queue.ticket.time, "sleep", slept.append)
    monkeypatch.setattr(otto_queue.ticket.time, "monotonic", lambda: 9.7)
    otto_queue.ticket._poll_sleep(3.0, 10.0)
    assert slept == [pytest.approx(0.3)]
    slept.clear()
    monkeypatch.setattr(otto_queue.ticket.time, "monotonic", lambda: 11.0)
    otto_queue.ticket._poll_sleep(3.0, 10.0)
    assert slept == []


class _Clock:
    def __init__(self):
        self.t = 0.0

    def monotonic(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


class _CountingLedger:
    def __init__(self, entry, first_id):
        self.root = Path("/nonexistent")
        self.entry = entry
        self.first_id = first_id
        self.reads = 0
        self.reaps = 0

    def wait_poll(self, entry_id, entry_class, *, jobs=False, now=None):
        self.reads += 1
        return self.entry, self.first_id

    def get(self, entry_id):
        self.reads += 1
        return self.entry

    def first_waiting(self, entry_class, *, jobs=False, now=None):
        self.reads += 1
        return self.first_id

    def reap_orphans(self):
        self.reaps += 1
        return []


def _queued(entry_id):
    return Entry(
        id=entry_id, kind=EntryKind.TICKET, entry_class=EntryClass.HEAVY, idem_key=None,
        argv=("true",), cwd="/", env_allow=(), state=EntryState.QUEUED, attempts=0,
        exit_status=None, log_path=None, result=None, pid=1, created_at=0.0,
        started_at=None, finished_at=None)


def _drive_wait(monkeypatch, ledger, entry_id, wait_s):
    clock = _Clock()
    monkeypatch.setattr(otto_queue.ticket.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(otto_queue.ticket.time, "sleep", clock.sleep)
    monkeypatch.setattr(otto_queue.ticket.random, "Random",
                        lambda: SimpleNamespace(uniform=lambda a, b: 1.0))
    request = TicketRequest(argv=("true",), cwd="/", wait_s=wait_s)
    return otto_queue.ticket._wait_for_slot(
        ledger, entry_id, request, Room(admit=False), POLL_S, lambda _m: None)


def test_a_non_first_waiter_reaps_at_most_every_reap_interval(monkeypatch):
    waiter = _queued(2)
    ledger = _CountingLedger(waiter, first_id=1)
    wait = _drive_wait(monkeypatch, ledger, waiter.id, wait_s=12.0)
    assert wait.end.value == "timed_out"
    assert ledger.reaps == int(12.0 / REAP_EVERY_S)
    assert ledger.reads > ledger.reaps


def test_the_first_waiter_reaps_on_every_poll(monkeypatch):
    waiter = _queued(1)
    ledger = _CountingLedger(waiter, first_id=1)
    monkeypatch.setattr(otto_queue.slots, "try_admit",
                        lambda *_a, **_k: Admission(None, "no room"))
    wait = _drive_wait(monkeypatch, ledger, waiter.id, wait_s=4.0)
    assert wait.end.value == "timed_out"
    assert ledger.reaps == 9


def test_wait_poll_returns_the_row_and_the_oldest_waiter(tmp_path):
    ledger = Ledger.open(tmp_path)
    older = ledger.resolve(
        NewEntry(argv=("x",), cwd="/"), force=False, pid=os.getpid())
    newer = ledger.resolve(
        NewEntry(argv=("y",), cwd="/"), force=False, pid=os.getpid())
    entry, first = ledger.wait_poll(newer.entry.id, EntryClass.HEAVY)
    assert (entry.id, first) == (newer.entry.id, older.entry.id)
    assert first == ledger.first_waiting(EntryClass.HEAVY)
    older.lease.release()
    newer.lease.release()
