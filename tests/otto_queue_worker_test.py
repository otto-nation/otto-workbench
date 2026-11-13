"""Tests for otto_queue.worker: dispatch under the slot budget, launch failures, idling and its last look.

Runners are the real `otto-queue job-exec` through the shim, without systemd scopes.
"""

import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import otto_queue.lease  # noqa: E402
import otto_queue.paths  # noqa: E402
import otto_queue.slots  # noqa: E402
import otto_queue.worker  # noqa: E402
from otto_queue.ledger import Ledger  # noqa: E402
from otto_queue.model import EntryClass, EntryKind, EntryState, NewEntry  # noqa: E402
from otto_queue_support import PEAK, Room, set_columns, touch_argv  # noqa: E402

PY = sys.executable
SHIM = REPO_ROOT / "ai" / "bin" / "otto-queue"
LAUNCH = otto_queue.worker.Launcher(job_exec=(PY, str(SHIM), "job-exec"))


def _enqueue(argv, entry_class=EntryClass.HEAVY):
    res = Ledger.open().resolve(NewEntry(argv=tuple(argv), cwd=os.getcwd(), kind=EntryKind.JOB,
                                         entry_class=entry_class), force=False, pid=None)
    return res.entry.id


def _work(launcher=LAUNCH, idle_s=0.3, messages=None):
    sink = messages if messages is not None else []
    return otto_queue.worker.run_worker(Room(), launcher=launcher, idle_s=idle_s, poll_s=0.05, notify=sink.append)


def _wait_exited(proc):
    proc.wait(timeout=10)


class _Boom(otto_queue.worker.Launcher):
    def argv(self, entry, slot_fd):
        raise ValueError("bad argv")


class _OneSlot(Room):
    def limit(self, entry_class):
        return 1


def test_queued_jobs_run_and_finish(tmp_path):
    ids = [_enqueue(touch_argv(tmp_path / str(n))) for n in range(3)]
    assert _work() == 0
    assert all(Ledger.open().get(i).state is EntryState.DONE for i in ids)
    assert all((tmp_path / str(n)).exists() for n in range(3))


def test_n_jobs_with_a_heavy_limit_of_2_never_run_more_than_2(tmp_path):
    counter = tmp_path / "peak.json"
    for _ in range(5):
        _enqueue((PY, "-c", PEAK, str(counter)))
    _work()
    assert json.loads(counter.read_text())["peak"] == 2


def test_a_runner_that_never_starts_its_job_fails_it_once(tmp_path):
    entry_id = _enqueue(touch_argv(tmp_path / "ran"))
    broken = otto_queue.worker.Launcher(job_exec=(PY, "-c", "raise SystemExit(1)"))
    messages = []
    _work(launcher=broken, messages=messages)
    entry = Ledger.open().get(entry_id)
    assert (entry.state, entry.exit_status, entry.attempts) == (EntryState.FAILED, None, 0)
    assert any("without starting it" in m for m in messages)


def test_a_runner_that_exits_tempfail_without_claiming_leaves_the_job_queued(tmp_path):
    entry_id = _enqueue(touch_argv(tmp_path / "ran"))
    ledger = Ledger.open()
    started = {}
    busy = otto_queue.worker.Launcher(job_exec=(PY, "-c", f"raise SystemExit({os.EX_TEMPFAIL})"))
    assert otto_queue.worker._start_one(ledger, Room(), busy, started, EntryClass.HEAVY, lambda _m: None)
    _wait_exited(started[entry_id].proc)
    otto_queue.worker._reap_runners(ledger, started, lambda _m: None)
    entry = ledger.get(entry_id)
    assert (entry.state, entry.kind, entry.attempts) == (EntryState.QUEUED, EntryKind.JOB, 0)


def test_a_runner_that_declines_because_the_lease_is_held_leaves_the_job_queued(tmp_path):
    entry_id = _enqueue(touch_argv(tmp_path / "ran"))
    ledger = Ledger.open()
    held = otto_queue.lease.take(ledger.root, entry_id)
    started = {}
    try:
        assert otto_queue.worker._start_one(ledger, Room(), LAUNCH, started, EntryClass.HEAVY, lambda _m: None)
        _wait_exited(started[entry_id].proc)
        otto_queue.worker._reap_runners(ledger, started, lambda _m: None)
        entry = ledger.get(entry_id)
        assert (entry.state, entry.kind) == (EntryState.QUEUED, EntryKind.JOB)
    finally:
        held.release()


def test_claiming_a_job_as_a_ticket_before_reap_leaves_the_ticket_live(tmp_path):
    entry_id = _enqueue(touch_argv(tmp_path / "ran"))
    ledger = Ledger.open()
    started = {}
    broken = otto_queue.worker.Launcher(job_exec=(PY, "-c", "raise SystemExit(1)"))
    assert otto_queue.worker._start_one(ledger, Room(), broken, started, EntryClass.HEAVY, lambda _m: None)
    _wait_exited(started[entry_id].proc)
    claimed = ledger.claim_as_ticket(entry_id, os.getpid())
    try:
        assert claimed is not None
        otto_queue.worker._reap_runners(ledger, started, lambda _m: None)
        entry = ledger.get(entry_id)
        assert (entry.kind, entry.state) == (EntryKind.TICKET, EntryState.QUEUED)
    finally:
        if claimed is not None:
            claimed.lease.release()


def test_a_second_worker_leaves_while_one_holds_the_lock(tmp_path):
    entry_id = _enqueue(touch_argv(tmp_path / "ran"))
    held = otto_queue.lease.take_worker(otto_queue.paths.queue_root())
    messages = []
    try:
        assert _work(messages=messages) == 0
    finally:
        held.release()
    assert any("another worker" in m for m in messages)
    assert Ledger.open().get(entry_id).state is EntryState.QUEUED


def test_an_older_waiting_ticket_goes_before_a_job():
    ledger = Ledger.open()
    ticket = ledger.resolve(NewEntry(argv=("true",), cwd="/"), force=False, pid=os.getpid())
    try:
        _enqueue(("true",))
        started = {}
        assert not otto_queue.worker._start_one(ledger, Room(), LAUNCH, started, EntryClass.HEAVY, lambda _m: None)
        assert started == {}
    finally:
        ticket.lease.release()


def test_the_worker_stays_for_a_job_waiting_out_its_retry(tmp_path, monkeypatch):
    entry_id = _enqueue(touch_argv(tmp_path / "ran"))
    set_columns(otto_queue.paths.queue_root(), entry_id, not_before=time.time() + 0.6)
    real = otto_queue.worker._serve
    calls = []

    def count_serve(*args):
        real(*args)
        calls.append(1)

    monkeypatch.setattr(otto_queue.worker, "_serve", count_serve)
    _work(idle_s=0.1)
    assert len(calls) == 1
    assert Ledger.open().get(entry_id).state is EntryState.DONE


def test_the_worker_looks_again_after_releasing_its_lock(tmp_path, monkeypatch):
    real = otto_queue.worker._serve
    calls = []

    def serve_then_submit(*args):
        real(*args)
        calls.append(1)
        if len(calls) == 1:
            _enqueue(touch_argv(tmp_path / "late"))

    monkeypatch.setattr(otto_queue.worker, "_serve", serve_then_submit)
    _work()
    assert len(calls) == 2
    assert (tmp_path / "late").exists()


def test_the_worker_compacts_a_ledger_not_compacted_recently():
    ledger = Ledger.open()
    old = ledger.resolve(NewEntry(argv=("true",), cwd="/", kind=EntryKind.JOB), force=False, pid=None).entry.id
    set_columns(ledger.root, old, state="failed", finished_at=0)
    _work()
    assert ledger.get(old) is None and ledger.compacted_at() is not None


def test_a_recently_compacted_ledger_is_not_compacted_again():
    ledger = Ledger.open()
    ledger.compact(retention_s=otto_queue.worker.RETENTION_S, now=time.time())
    stamp = ledger.compacted_at()
    old = ledger.resolve(NewEntry(argv=("true",), cwd="/", kind=EntryKind.JOB), force=False, pid=None).entry.id
    set_columns(ledger.root, old, state="failed", finished_at=0)
    _work()
    assert ledger.get(old) is not None and ledger.compacted_at() == stamp


def test_a_missing_runner_fails_the_job_and_releases_the_slot(tmp_path):
    entry_id = _enqueue(touch_argv(tmp_path / "ran"))
    messages = []
    _work(launcher=otto_queue.worker.Launcher(job_exec=("/nonexistent/otto",)), messages=messages)
    entry = Ledger.open().get(entry_id)
    assert entry.state is EntryState.FAILED
    assert any("could not start" in m for m in messages)
    admission = otto_queue.slots.try_admit(otto_queue.paths.queue_root(), _OneSlot(), EntryClass.HEAVY)
    assert admission.slot is not None
    admission.slot.release()


def test_a_launcher_that_raises_fails_the_job_and_releases_the_slot(tmp_path):
    entry_id = _enqueue(touch_argv(tmp_path / "ran"))
    messages = []
    _work(launcher=_Boom(job_exec=("true",)), messages=messages)
    entry = Ledger.open().get(entry_id)
    assert entry.state is EntryState.FAILED
    assert any("could not start" in m for m in messages)
    admission = otto_queue.slots.try_admit(otto_queue.paths.queue_root(), _OneSlot(), EntryClass.HEAVY)
    assert admission.slot is not None
    admission.slot.release()
