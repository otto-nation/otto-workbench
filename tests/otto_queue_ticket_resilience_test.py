"""Tests for otto_queue.ticket: waiting through ledger contention."""

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import otto_queue.ticket  # noqa: E402
from otto_queue.ledger import Ledger, LedgerBusy  # noqa: E402
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
