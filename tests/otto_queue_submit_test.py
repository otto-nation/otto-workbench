"""Tests for otto_queue.submit: queue for the worker, or run here when no worker can start."""

import os
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

import core.proc  # noqa: E402
import otto_queue.lease  # noqa: E402
import otto_queue.submit  # noqa: E402
import otto_queue.supervisor  # noqa: E402
import otto_queue.ticket  # noqa: E402
from otto_queue.ledger import Ledger, LedgerBusy  # noqa: E402
from otto_queue.model import EntryClass, EntryKind, EntryState, NewEntry  # noqa: E402
from otto_queue.submit import SubmitMode, SubmitOutcome, SubmitRequest  # noqa: E402
from otto_queue_support import Room, touch_argv  # noqa: E402

PY = sys.executable

# A subprocess ticket that holds a key until SIGKILL: writes *started*, then sleeps.
TICKET_HOLD = """
import os, sys
sys.path.insert(0, sys.argv[1])
from types import SimpleNamespace
import otto_queue.ticket

class Room:
    def limit(self, entry_class):
        return 2

    def verdict(self, entry_class, running, *, host_room=True):
        return SimpleNamespace(admit=True, reason="room")

started, key = sys.argv[2], sys.argv[3]
request = otto_queue.ticket.TicketRequest(
    argv=(sys.executable, "-c", f"open({started!r}, 'w').write('1'); import time; time.sleep(60)"),
    cwd=os.getcwd(), idem_key=key)
otto_queue.ticket.run_ticket(request, Room(), poll_s=0.05, notify=lambda _m: None)
"""


def _alive(_root):
    return otto_queue.supervisor.Wake(True, "stub worker")


def _down(_root):
    return otto_queue.supervisor.Wake(False, "no supervisor in this test")


def _submit(argv, wake, messages=None, **kw):
    # Pinned: these suites exercise run-here unless a test names another mode.
    kw.setdefault("mode", SubmitMode.RUN_HERE)
    sink = messages if messages is not None else []
    request = SubmitRequest(argv=tuple(argv), cwd=os.getcwd(), **kw)
    return otto_queue.submit.submit(request, Room(), wake=wake, notify=sink.append)


def _submit_with(policy, argv, wake, **kw):
    kw.setdefault("mode", SubmitMode.RUN_HERE)
    request = SubmitRequest(argv=tuple(argv), cwd=os.getcwd(), **kw)
    return otto_queue.submit.submit(request, policy, wake=wake, notify=lambda _m: None)


def _await_path(path):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.05)
    pytest.fail(f"{path} was never created")


def test_a_job_keeps_its_label():
    result = _submit(("true",), _alive, label="nightly")
    assert Ledger.open().get(result.entry_id).label == "nightly"


def test_a_job_run_here_keeps_its_label(tmp_path):
    result = _submit(touch_argv(tmp_path / "ran"), _down, label="nightly")
    entry = Ledger.open().get(result.entry_id)
    assert (entry.label, entry.kind) == ("nightly", EntryKind.JOB)


# Waits for a slot that never comes, saying so once it is about to run the job here.
RUN_HERE_DRIVER = """
import os, sys
from types import SimpleNamespace
sys.path.insert(0, sys.argv[1])
import otto_queue.submit, otto_queue.supervisor

class Never:
    def limit(self, entry_class):
        return 2

    def verdict(self, entry_class, running, *, host_room=True):
        return SimpleNamespace(admit=False, reason="no room")

def say(message):
    if "running entry" in message:
        open(sys.argv[2], "w").write("waiting")

request = otto_queue.submit.SubmitRequest(
    argv=("true",), cwd=os.getcwd(), mode=otto_queue.submit.SubmitMode.RUN_HERE)
otto_queue.submit.submit(request, Never(), wake=lambda _r: otto_queue.supervisor.Wake(False, "none"), notify=say)
"""


def test_a_run_here_killed_before_admission_leaves_a_queued_job(tmp_path):
    waiting = tmp_path / "waiting"
    proc = subprocess.Popen([PY, "-c", RUN_HERE_DRIVER, str(LIB_DIR), str(waiting)])
    try:
        _await_path(waiting)
    finally:
        proc.kill()
        proc.wait()
    ledger = Ledger.open()
    ledger.reap_orphans()
    # The state directory is fresh per test, so the one job is entry 1.
    entry = ledger.get(1)
    assert (entry.kind, entry.state) == (EntryKind.JOB, EntryState.QUEUED)


def test_run_here_that_finds_no_slot_returns_75_and_leaves_it_queued(tmp_path, monkeypatch):
    monkeypatch.setattr(otto_queue.ticket, "DEFAULT_WAIT_S", 0.3)
    result = _submit_with(Room(admit=False), touch_argv(tmp_path / "ran"), _down)
    entry = Ledger.open().get(result.entry_id)
    assert (result.exit_status, result.outcome) == (75, SubmitOutcome.BUSY)
    assert (entry.kind, entry.state, entry.exit_status) == (EntryKind.JOB, EntryState.QUEUED, None)
    assert not (tmp_path / "ran").exists()


def test_with_a_worker_the_job_is_queued_and_not_run_here(tmp_path):
    result = _submit(touch_argv(tmp_path / "ran"), _alive)
    entry = Ledger.open().get(result.entry_id)
    assert (result.exit_status, result.outcome) == (0, SubmitOutcome.QUEUED)
    assert (entry.kind, entry.state) == (EntryKind.JOB, EntryState.QUEUED)
    assert not (tmp_path / "ran").exists()


def test_without_a_worker_the_job_runs_here_and_says_so(tmp_path):
    messages = []
    result = _submit(touch_argv(tmp_path / "ran"), _down, messages)
    entry = Ledger.open().get(result.entry_id)
    assert (result.exit_status, result.outcome) == (0, SubmitOutcome.RAN_HERE)
    assert (tmp_path / "ran").exists()
    assert (entry.kind, entry.state) == (EntryKind.JOB, EntryState.DONE)
    assert any(f"no worker (no supervisor in this test); running entry {entry.id} here" in m
               for m in messages)


def test_a_job_a_worker_took_meanwhile_is_left_to_it(tmp_path):
    def wake_late(root):
        ledger = Ledger.open(root)
        [queued] = [e for e in ledger.entries() if e.state is EntryState.QUEUED]
        ledger.mark_running(queued.id, 1)
        return _down(root)

    result = _submit(touch_argv(tmp_path / "ran"), wake_late)
    assert result.outcome is SubmitOutcome.JOINED
    assert not (tmp_path / "ran").exists()


def test_a_done_key_is_reused_without_waking_anyone():
    ledger = Ledger.open()
    done = ledger.resolve(NewEntry(argv=("true",), cwd="/", idem_key="k", kind=EntryKind.JOB),
                          force=False, pid=None).entry.id
    ledger.mark_running(done, 1)
    ledger.finish(done, 0)

    def refuse(_root):
        raise AssertionError("woke the worker for a reused entry")

    result = _submit(("true",), refuse, idem_key="k")
    assert (result.outcome, result.entry_id) == (SubmitOutcome.REUSED, done)


def test_a_live_key_is_joined():
    first = _submit(("true",), _alive, idem_key="maintenance", reuse=False)
    second = _submit(("true",), _alive, idem_key="maintenance", reuse=False)
    assert (second.outcome, second.entry_id) == (SubmitOutcome.JOINED, first.entry_id)


def test_an_unopenable_ledger_runs_the_command_unqueued(tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("")
    request = SubmitRequest(argv=touch_argv(tmp_path / "ran"), cwd=os.getcwd(), mode=SubmitMode.RUN_HERE)
    result = otto_queue.submit.submit(request, Room(), root=blocker / "queue", wake=_alive, notify=lambda _m: None)
    assert (result.exit_status, result.outcome, (tmp_path / "ran").exists()) == (0, SubmitOutcome.UNQUEUED, True)


def test_a_killed_ticket_is_run_here(tmp_path):
    started = tmp_path / "started"
    ran = tmp_path / "ran"
    proc = subprocess.Popen([PY, "-c", TICKET_HOLD, str(LIB_DIR), str(started), "k"])
    try:
        _await_path(started)
        threading.Timer(0.3, proc.kill).start()
        result = _submit(touch_argv(ran), _down, idem_key="k")
        assert ran.exists()
        assert result.exit_status == 0
    finally:
        proc.kill()
        proc.wait(timeout=10)


def test_joining_a_live_ticket_returns_its_status(tmp_path):
    ledger = Ledger.open()
    held = ledger.resolve(NewEntry(argv=("x",), cwd="/", idem_key="k"), force=False, pid=os.getpid())
    ledger.mark_running(held.entry.id, os.getpid())
    threading.Timer(0.3, ledger.finish, (held.entry.id, 3)).start()
    try:
        result = _submit(touch_argv(tmp_path / "ran"), _alive, idem_key="k")
        assert (result.exit_status, result.outcome) == (3, SubmitOutcome.JOINED)
        assert not (tmp_path / "ran").exists()
    finally:
        held.lease.release()


def test_run_here_uses_the_claimed_rows_argv_and_class(tmp_path):
    Ledger.open().resolve(
        NewEntry(argv=touch_argv(tmp_path / "light"), cwd=os.getcwd(),
                 kind=EntryKind.JOB, entry_class=EntryClass.LIGHT, idem_key="k"),
        force=False, pid=None)
    result = _submit(touch_argv(tmp_path / "heavy"), _down, idem_key="k")
    assert (tmp_path / "light").exists()
    assert not (tmp_path / "heavy").exists()
    assert Ledger.open().get(result.entry_id).entry_class is EntryClass.LIGHT


# passes-at-base: LedgerBusy after queueing already escaped submit
def test_ledger_busy_after_queueing_propagates(monkeypatch):
    def boom(self, entry_id, pid, *, log_path=None):
        raise LedgerBusy("busy")

    monkeypatch.setattr(Ledger, "mark_running", boom)
    with pytest.raises(LedgerBusy):
        _submit(("true",), _down)


# passes-at-base: F5 already returns JOINED 0 while the row stays a queued job
def test_a_job_whose_lease_is_held_is_left_to_the_worker(tmp_path):
    held = []

    def wake_late(root):
        ledger = Ledger.open(root)
        [queued] = [e for e in ledger.entries() if e.state is EntryState.QUEUED]
        held.append(otto_queue.lease.take(root, queued.id))
        return _down(root)

    try:
        result = _submit(touch_argv(tmp_path / "ran"), wake_late)
        entry = Ledger.open().get(result.entry_id)
        assert (result.exit_status, result.outcome) == (0, SubmitOutcome.JOINED)
        assert (entry.kind, entry.state) == (EntryKind.JOB, EntryState.QUEUED)
        assert not (tmp_path / "ran").exists()
    finally:
        for lease in held:
            lease.release()


# passes-at-base: resolve already honours reuse=False
def test_reuse_false_runs_a_done_key_again(tmp_path):
    ledger = Ledger.open()
    done = ledger.resolve(NewEntry(argv=("true",), cwd="/", idem_key="k", kind=EntryKind.JOB),
                          force=False, pid=None).entry.id
    ledger.mark_running(done, 1)
    ledger.finish(done, 0)
    result = _submit(touch_argv(tmp_path / "ran"), _down, idem_key="k", reuse=False)
    assert result.outcome is SubmitOutcome.RAN_HERE
    assert result.entry_id != done
    assert (tmp_path / "ran").exists()


def test_queue_only_without_a_worker_withdraws_the_job_and_runs_nothing(tmp_path):
    messages = []
    result = _submit(touch_argv(tmp_path / "ran"), _down, messages, mode=SubmitMode.QUEUE_ONLY)
    entry = Ledger.open().get(result.entry_id)
    assert (result.exit_status, result.outcome) == (os.EX_UNAVAILABLE, SubmitOutcome.NO_WORKER)
    assert entry.state is EntryState.CANCELLED
    assert entry.exit_status == core.proc.INTERRUPT_RETURNCODE
    assert not (tmp_path / "ran").exists()
    assert any(f"no worker (no supervisor in this test); withdrew entry {entry.id}" in m for m in messages)


def test_queue_only_without_a_worker_leaves_a_job_another_submitter_queued(tmp_path):
    first = _submit(touch_argv(tmp_path / "ran"), _alive, idem_key="K")
    assert first.outcome is SubmitOutcome.QUEUED
    messages = []
    second = _submit(touch_argv(tmp_path / "ran"), _down, messages, idem_key="K", mode=SubmitMode.QUEUE_ONLY)
    entry = Ledger.open().get(second.entry_id)
    assert (second.exit_status, second.outcome) == (0, SubmitOutcome.JOINED)
    assert second.entry_id == first.entry_id
    assert (entry.kind, entry.state) == (EntryKind.JOB, EntryState.QUEUED)
    assert not (tmp_path / "ran").exists()
    assert any(f"joined entry {first.entry_id}" in m for m in messages)


def test_queue_only_leaves_a_job_a_worker_took_meanwhile(tmp_path):
    def wake_late(root):
        ledger = Ledger.open(root)
        [queued] = [e for e in ledger.entries() if e.state is EntryState.QUEUED]
        ledger.mark_running(queued.id, 1)
        return _down(root)

    result = _submit(touch_argv(tmp_path / "ran"), wake_late, mode=SubmitMode.QUEUE_ONLY)
    assert (result.exit_status, result.outcome) == (0, SubmitOutcome.JOINED)
    assert Ledger.open().get(result.entry_id).state is EntryState.RUNNING


def test_a_withdrawn_key_is_queued_afresh_next_time():
    first = _submit(("true",), _down, idem_key="auto-task:dream:20261112", mode=SubmitMode.QUEUE_ONLY)
    second = _submit(("true",), _alive, idem_key="auto-task:dream:20261112", mode=SubmitMode.QUEUE_ONLY)
    assert second.outcome is SubmitOutcome.QUEUED and second.entry_id != first.entry_id


def test_queue_only_with_an_unopenable_ledger_runs_nothing(tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("")
    messages = []
    request = SubmitRequest(argv=touch_argv(tmp_path / "ran"), cwd=os.getcwd(), mode=SubmitMode.QUEUE_ONLY)
    result = otto_queue.submit.submit(
        request, Room(), root=blocker / "queue", wake=_alive, notify=messages.append)
    assert (result.exit_status, result.outcome) == (os.EX_UNAVAILABLE, SubmitOutcome.NO_WORKER)
    assert not (tmp_path / "ran").exists()
    assert any("queue unavailable" in m and "ran nothing" in m for m in messages)


def test_add_without_a_worker_returns_at_once_and_leaves_it_queued(tmp_path):
    messages = []
    t0 = time.monotonic()
    result = _submit(touch_argv(tmp_path / "ran"), _down, messages, mode=SubmitMode.ADD)
    entry = Ledger.open().get(result.entry_id)
    assert time.monotonic() - t0 < 5
    assert (result.exit_status, result.outcome) == (0, SubmitOutcome.QUEUED)
    assert (entry.kind, entry.state) == (EntryKind.JOB, EntryState.QUEUED)
    assert not (tmp_path / "ran").exists()
    assert any(f"otto-queue wait {entry.id}" in m for m in messages)


def test_the_default_mode_adds_only(tmp_path):
    request = SubmitRequest(argv=touch_argv(tmp_path / "ran"), cwd=os.getcwd())
    result = otto_queue.submit.submit(request, Room(), wake=_down, notify=lambda _m: None)
    assert (result.exit_status, result.outcome) == (0, SubmitOutcome.QUEUED)
    assert not (tmp_path / "ran").exists()


def test_add_joins_a_live_ticket_without_waiting(tmp_path):
    started = tmp_path / "started"
    proc = subprocess.Popen([PY, "-c", TICKET_HOLD, str(LIB_DIR), str(started), "k"])
    try:
        _await_path(started)
        t0 = time.monotonic()
        result = _submit(touch_argv(tmp_path / "ran"), _down, idem_key="k", mode=SubmitMode.ADD)
        assert time.monotonic() - t0 < 5
        entry = Ledger.open().get(result.entry_id)
        assert (result.exit_status, result.outcome) == (0, SubmitOutcome.JOINED)
        assert entry.state.live and entry.state is not EntryState.CANCELLED
        assert not (tmp_path / "ran").exists()
    finally:
        proc.kill()
        proc.wait(timeout=10)


def test_add_with_an_unopenable_ledger_runs_nothing(tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("")
    request = SubmitRequest(argv=touch_argv(tmp_path / "ran"), cwd=os.getcwd(), mode=SubmitMode.ADD)
    result = otto_queue.submit.submit(request, Room(), root=blocker / "queue", wake=_alive, notify=lambda _m: None)
    assert (result.exit_status, result.outcome) == (os.EX_UNAVAILABLE, SubmitOutcome.NO_WORKER)
    assert not (tmp_path / "ran").exists()


def test_queue_only_joins_a_live_ticket_without_waiting(tmp_path):
    started = tmp_path / "started"
    ran = tmp_path / "ran"
    proc = subprocess.Popen([PY, "-c", TICKET_HOLD, str(LIB_DIR), str(started), "k"])
    try:
        _await_path(started)
        t0 = time.monotonic()
        result = _submit(touch_argv(ran), _down, idem_key="k", mode=SubmitMode.QUEUE_ONLY)
        assert time.monotonic() - t0 < 5
        entry = Ledger.open().get(result.entry_id)
        assert (result.exit_status, result.outcome) == (0, SubmitOutcome.JOINED)
        assert not ran.exists()
        assert entry.state.live
        assert entry.state is not EntryState.CANCELLED
    finally:
        proc.kill()
        proc.wait(timeout=10)


def test_queue_only_with_a_worker_joins_a_live_ticket_without_waiting(tmp_path):
    started = tmp_path / "started"
    ran = tmp_path / "ran"
    proc = subprocess.Popen([PY, "-c", TICKET_HOLD, str(LIB_DIR), str(started), "k"])
    try:
        _await_path(started)
        t0 = time.monotonic()
        result = _submit(touch_argv(ran), _alive, idem_key="k", mode=SubmitMode.QUEUE_ONLY)
        assert time.monotonic() - t0 < 5
        entry = Ledger.open().get(result.entry_id)
        assert (result.exit_status, result.outcome) == (0, SubmitOutcome.JOINED)
        assert not ran.exists()
        assert entry.state.live
        assert entry.state is not EntryState.CANCELLED
    finally:
        proc.kill()
        proc.wait(timeout=10)


def test_queue_only_missed_claim_on_a_live_ticket_returns_joined(tmp_path):
    held = []

    def wake_claim(root):
        ledger = Ledger.open(root)
        [queued] = [e for e in ledger.entries() if e.state is EntryState.QUEUED]
        claimed = ledger.claim_as_ticket(queued.id, os.getpid())
        if claimed is not None:
            held.append(claimed)
        return _down(root)

    try:
        result = _submit(touch_argv(tmp_path / "ran"), wake_claim, mode=SubmitMode.QUEUE_ONLY, idem_key="race")
        entry = Ledger.open().get(result.entry_id)
        assert (result.exit_status, result.outcome) == (0, SubmitOutcome.JOINED)
        assert not (tmp_path / "ran").exists()
        assert entry.kind is EntryKind.TICKET and entry.state.live
    finally:
        for claimed in held:
            claimed.lease.release()


def test_queue_only_missed_claim_on_a_done_entry_returns_its_status(tmp_path):
    def wake_done(root):
        ledger = Ledger.open(root)
        [queued] = [e for e in ledger.entries() if e.state is EntryState.QUEUED]
        claimed = ledger.claim_as_ticket(queued.id, os.getpid())
        ledger.finish(claimed.entry.id, 0)
        claimed.lease.release()
        return _down(root)

    result = _submit(touch_argv(tmp_path / "ran"), wake_done, mode=SubmitMode.QUEUE_ONLY)
    entry = Ledger.open().get(result.entry_id)
    assert (result.exit_status, result.outcome) == (0, SubmitOutcome.REUSED)
    assert entry.state is EntryState.DONE
    assert not (tmp_path / "ran").exists()
