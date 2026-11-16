"""Tests for otto_queue.ticket: waiting through ledger contention."""

import os
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import core.signal_relay  # noqa: E402
import otto_queue.ledger  # noqa: E402
import otto_queue.ticket  # noqa: E402
from otto_queue.ledger import Ledger, LedgerBusy, LedgerError  # noqa: E402
from otto_queue.model import EntryState  # noqa: E402
from otto_queue.ticket import TicketOutcome, TicketRequest  # noqa: E402
from otto_queue_support import Room, exit_argv, touch_argv  # noqa: E402


def _ticket(tmp_path, argv, **kw):
    request = TicketRequest(argv=tuple(argv), cwd=os.getcwd(), **kw)
    messages = []
    result = otto_queue.ticket.run_ticket(
        request, Room(), root=tmp_path, poll_s=0.05, notify=messages.append)
    return result, messages


def test_wait_admits_after_transient_get_failures(tmp_path, monkeypatch):
    real = Ledger.get
    seen = {"n": 0}

    def flaky(self, entry_id):
        seen["n"] += 1
        if seen["n"] <= 2:
            raise LedgerBusy("busy")
        return real(self, entry_id)

    monkeypatch.setattr(Ledger, "get", flaky)
    result, messages = _ticket(tmp_path, exit_argv(0))
    assert (result.exit_status, result.outcome) == (0, TicketOutcome.RAN)
    assert Ledger.open(tmp_path).get(result.entry_id).state is EntryState.DONE
    assert any("ledger busy; still waiting" in m for m in messages)


def test_wait_returns_75_when_the_ledger_stays_busy(tmp_path, monkeypatch):
    def boom(self, entry_id):
        raise LedgerBusy("busy")

    monkeypatch.setattr(Ledger, "get", boom)
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
