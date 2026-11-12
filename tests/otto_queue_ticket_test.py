"""Tests for otto_queue.ticket: admission, idempotency, cancellation and the budget across processes.

`run_ticket` installs signal handlers while its command runs, so it is always
called on the main thread; timers on other threads stand in for a second process.
"""

import json
import os
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import core.proc  # noqa: E402
import core.signal_relay  # noqa: E402
import core.timeouts  # noqa: E402
import otto_queue.paths  # noqa: E402
import otto_queue.ticket  # noqa: E402
import otto_queue.lease  # noqa: E402
import otto_queue.slots  # noqa: E402
from otto_queue.ledger import Ledger  # noqa: E402
from otto_queue.model import LIVE_STATES, EntryState, NewEntry  # noqa: E402
from otto_queue.ticket import TicketOutcome, TicketRequest, TicketResult  # noqa: E402

PY = sys.executable

# One process per ticket, each admitting against a fixed limit of 2.
DRIVER = """
import os, sys
from types import SimpleNamespace
sys.path.insert(0, sys.argv[1])
import otto_queue.ticket

class Room:
    def limit(self, entry_class):
        return 2

    def verdict(self, entry_class, running):
        return SimpleNamespace(admit=True, reason="room")

request = otto_queue.ticket.TicketRequest(argv=tuple(sys.argv[2:]), cwd=os.getcwd())
result = otto_queue.ticket.run_ticket(request, Room(), poll_s=0.05, notify=lambda _m: None)
sys.exit(result.exit_status)
"""

# Records how many copies of itself run at once, under a flock.
PEAK = """
import fcntl, json, sys, time

def bump(path, delta):
    with open(path, "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        state = json.loads(f.read() or '{"now": 0, "peak": 0}')
        state["now"] += delta
        state["peak"] = max(state["peak"], state["now"])
        f.seek(0)
        f.truncate()
        f.write(json.dumps(state))

bump(sys.argv[1], 1)
time.sleep(1.0)
bump(sys.argv[1], -1)
"""


class Room:
    def __init__(self, admit=True, raises=None):
        self._admit, self._raises = admit, raises

    def limit(self, entry_class):
        return 2

    def verdict(self, entry_class, running):
        if self._raises is not None:
            raise self._raises
        return SimpleNamespace(admit=self._admit, reason="room" if self._admit else "no room")


def _touch(path):
    return (PY, "-c", f"open({str(path)!r}, 'w').write('ran')")


def _exit(code):
    return (PY, "-c", f"raise SystemExit({code})")


def _run(argv, policy=None, messages=None, **kw):
    request = TicketRequest(argv=tuple(argv), cwd=os.getcwd(), **kw)
    sink = messages if messages is not None else []
    return otto_queue.ticket.run_ticket(request, policy or Room(), poll_s=0.05, notify=sink.append)


def test_a_ticket_runs_its_command_and_records_done(tmp_path):
    result = _run(_touch(tmp_path / "ran"))
    assert (result.exit_status, result.outcome) == (0, TicketOutcome.RAN)
    assert (tmp_path / "ran").exists()
    assert Ledger.open().get(result.entry_id).state is EntryState.DONE


@pytest.mark.parametrize("code,state", [(os.EX_TEMPFAIL, EntryState.TEMPFAIL), (3, EntryState.FAILED)])
def test_the_commands_status_is_returned_and_recorded(code, state):
    result = _run(_exit(code))
    assert (result.exit_status, result.outcome) == (code, TicketOutcome.RAN)
    entry = Ledger.open().get(result.entry_id)
    assert (entry.state, entry.exit_status) == (state, code)


def test_a_command_that_cannot_start_is_failed_with_127():
    result = _run(["/nonexistent/otto-queue-test"])
    assert result.exit_status == core.proc.MISSING_RETURNCODE
    assert Ledger.open().get(result.entry_id).state is EntryState.FAILED


def test_the_command_sees_its_entry_id(tmp_path):
    out = tmp_path / "id"
    result = _run((PY, "-c", f"import os; open({str(out)!r}, 'w').write(os.environ['OTTO_QUEUE_ENTRY'])"))
    assert out.read_text() == str(result.entry_id)


def test_a_done_key_returns_its_status_without_running(tmp_path):
    first = _run(_touch(tmp_path / "first"), idem_key="k")
    second = _run(_touch(tmp_path / "second"), idem_key="k")
    assert second == TicketResult(0, TicketOutcome.REUSED, first.entry_id)
    assert not (tmp_path / "second").exists()


def test_force_runs_even_when_the_key_is_done(tmp_path):
    _run(_touch(tmp_path / "first"), idem_key="k")
    forced = _run(_touch(tmp_path / "second"), idem_key="k", force=True)
    assert forced.outcome is TicketOutcome.RAN and (tmp_path / "second").exists()


def test_a_live_key_is_joined_and_its_status_returned(tmp_path):
    ledger = Ledger.open()
    held = ledger.resolve(NewEntry(argv=("x",), cwd="/", idem_key="k"), force=False, pid=os.getpid())
    ledger.mark_running(held.entry.id, os.getpid())
    threading.Timer(0.3, ledger.finish, (held.entry.id, 3)).start()
    result = _run(_touch(tmp_path / "ran"), idem_key="k")
    held.lease.release()
    assert result == TicketResult(3, TicketOutcome.JOINED, held.entry.id)
    assert not (tmp_path / "ran").exists()


def test_a_joined_entry_whose_holder_dies_is_run_here(tmp_path):
    ledger = Ledger.open()
    held = ledger.resolve(NewEntry(argv=("x",), cwd="/", idem_key="k"), force=False, pid=os.getpid())
    threading.Timer(0.3, held.lease.release).start()
    result = _run(_touch(tmp_path / "ran"), idem_key="k")
    assert result.outcome is TicketOutcome.RAN and result.entry_id != held.entry.id
    assert (tmp_path / "ran").exists()


def test_a_ticket_waits_behind_an_older_one_and_times_out_as_tempfail(tmp_path):
    ledger = Ledger.open()
    older = ledger.resolve(NewEntry(argv=("x",), cwd="/"), force=False, pid=os.getpid())
    messages = []
    result = _run(_touch(tmp_path / "ran"), messages=messages, wait_s=0.3)
    older.lease.release()
    assert (result.exit_status, result.outcome) == (os.EX_TEMPFAIL, TicketOutcome.TIMED_OUT)
    assert ledger.get(result.entry_id).state is EntryState.TEMPFAIL
    assert any(f"behind entry {older.entry.id}" in m for m in messages)
    assert not (tmp_path / "ran").exists()


def test_cancelling_a_waiting_ticket_ends_it_with_130(tmp_path):
    ledger = Ledger.open()

    def cancel_waiting():
        waiting = [e for e in ledger.entries() if e.state is EntryState.QUEUED]
        ledger.cancel(waiting[0].id)

    threading.Timer(0.3, cancel_waiting).start()
    result = _run(_touch(tmp_path / "ran"), policy=Room(admit=False), wait_s=10)
    assert (result.exit_status, result.outcome) == (core.proc.INTERRUPT_RETURNCODE, TicketOutcome.CANCELLED)
    assert ledger.get(result.entry_id).state is EntryState.CANCELLED
    assert not (tmp_path / "ran").exists()


def test_ctrl_c_while_waiting_cancels_the_entry(tmp_path):
    result = _run(_touch(tmp_path / "ran"), policy=Room(raises=KeyboardInterrupt()))
    assert result.outcome is TicketOutcome.CANCELLED
    assert Ledger.open().get(result.entry_id).state is EntryState.CANCELLED


def test_a_ticket_inside_a_ticket_runs_directly(tmp_path, monkeypatch):
    monkeypatch.setenv(otto_queue.ticket.ENTRY_ENV, "41")
    result = _run(_touch(tmp_path / "ran"), policy=Room(admit=False), wait_s=10)
    assert result == TicketResult(0, TicketOutcome.NESTED)
    assert Ledger.open().entries() == []


def test_an_unopenable_ledger_runs_the_command_unqueued(tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("")
    request = TicketRequest(argv=_touch(tmp_path / "ran"), cwd=os.getcwd())
    result = otto_queue.ticket.run_ticket(request, Room(), root=blocker / "queue", notify=list().append)
    assert result == TicketResult(0, TicketOutcome.UNQUEUED)
    assert (tmp_path / "ran").exists()


def test_a_busy_ledger_does_not_run_unqueued(tmp_path, monkeypatch):
    Ledger.open(tmp_path)
    holder = sqlite3.connect(str(otto_queue.paths.db_path(tmp_path)), isolation_level=None, timeout=0)
    try:
        holder.execute("BEGIN EXCLUSIVE")
        monkeypatch.setattr(core.timeouts, "LOCAL", 0.2)
        request = TicketRequest(argv=_touch(tmp_path / "ran"), cwd=os.getcwd())
        result = otto_queue.ticket.run_ticket(request, Room(), root=tmp_path, notify=list().append)
        assert result.exit_status == os.EX_TEMPFAIL
        assert not (tmp_path / "ran").exists()
    finally:
        holder.close()


def test_interrupt_while_running_records_exit_130(tmp_path, monkeypatch):
    def boom(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(core.signal_relay, "run_child", boom)
    result = _run(_touch(tmp_path / "ran"))
    assert result.exit_status == core.proc.INTERRUPT_RETURNCODE
    assert result.outcome is TicketOutcome.CANCELLED
    entry = Ledger.open().get(result.entry_id)
    assert (entry.state, entry.exit_status) == (EntryState.CANCELLED, core.proc.INTERRUPT_RETURNCODE)


def test_a_crash_in_the_queue_is_recorded_failed(tmp_path, monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("queue crashed")

    monkeypatch.setattr(core.signal_relay, "run_child", boom)
    with pytest.raises(RuntimeError, match="queue crashed"):
        _run(_touch(tmp_path / "ran"))
    [entry] = Ledger.open().entries()
    assert (entry.state, entry.exit_status) == (EntryState.FAILED, None)


@pytest.mark.parametrize("kwargs,outcome,interrupt_run",
                         [({}, TicketOutcome.RAN, False),
                          ({"policy": Room(admit=False), "wait_s": 0.3}, TicketOutcome.TIMED_OUT, False),
                          ({"policy": Room(raises=KeyboardInterrupt())}, TicketOutcome.CANCELLED, False),
                          ({}, TicketOutcome.CANCELLED, True)])
def test_the_row_is_terminal_before_lease_and_slot_release(
        tmp_path, monkeypatch, kwargs, outcome, interrupt_run):
    seen = []
    lease_release = otto_queue.lease.Lease.release
    slot_release = otto_queue.slots.Slot.release

    def row_state():
        entries = Ledger.open().entries()
        assert entries, "release with no ledger row"
        return Ledger.open().get(entries[-1].id).state

    def spy_lease(self):
        seen.append(row_state())
        lease_release(self)

    def spy_slot(self):
        # Admission rollbacks release a probe while the row is still queued;
        # the ticket's own slot is only released once it has been admitted.
        state = row_state()
        if state is not EntryState.QUEUED:
            seen.append(state)
        slot_release(self)

    monkeypatch.setattr(otto_queue.lease.Lease, "release", spy_lease)
    monkeypatch.setattr(otto_queue.slots.Slot, "release", spy_slot)
    if interrupt_run:
        def boom(*_args, **_kwargs):
            raise KeyboardInterrupt

        monkeypatch.setattr(core.signal_relay, "run_child", boom)
    result = _run(_touch(tmp_path / "ran"), **kwargs)
    assert result.outcome is outcome
    assert seen
    assert all(state not in LIVE_STATES for state in seen)


def test_six_heavy_tickets_never_run_more_than_two_at_once(tmp_path):
    counter = tmp_path / "peak.json"
    procs = [subprocess.Popen([PY, "-c", DRIVER, str(LIB_DIR), PY, "-c", PEAK, str(counter)])
             for _ in range(6)]
    assert [p.wait(timeout=120) for p in procs] == [0] * 6
    assert json.loads(counter.read_text())["peak"] == 2
    assert [e.state for e in Ledger.open().entries()] == [EntryState.DONE] * 6
