"""Tests for the ledger's lifecycle: resolving keys, transitions, leases and orphans.

Leases are flocks, which conflict between open file descriptions even within
one process, so an entry resolved in the test process holds its lease against
the ledger's own probe exactly as another process would.
"""

import contextlib
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import core.proc  # noqa: E402
import otto_queue.lease  # noqa: E402
import otto_queue.ledger  # noqa: E402
import otto_queue.paths  # noqa: E402
from otto_queue.ledger import Ledger, Outcome  # noqa: E402
from otto_queue.model import MAX_JOB_ATTEMPTS, EntryClass, EntryKind, EntryState, NewEntry, retry_delay  # noqa: E402
from otto_queue_support import set_columns

# Accepts one entry of argv[3]'s kind, runs it, prints its id, then waits to be killed.
HOLDER = textwrap.dedent("""
    import os, sys, time
    from pathlib import Path
    sys.path.insert(0, sys.argv[1])
    import otto_queue.lease
    import otto_queue.ledger
    from otto_queue.model import EntryKind, NewEntry
    root, kind = Path(sys.argv[2]), EntryKind(sys.argv[3])
    ledger = otto_queue.ledger.Ledger.open(root)
    res = ledger.resolve(NewEntry(argv=("true",), cwd="/", kind=kind), force=False, pid=os.getpid())
    lease = res.lease or otto_queue.lease.take(root, res.entry.id)
    ledger.mark_running(res.entry.id, os.getpid())
    print(res.entry.id, flush=True)
    time.sleep(60)
""")


def _kill_wait(proc):
    proc.kill()
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=30)


@pytest.fixture
def ledger(tmp_path):
    return Ledger.open(tmp_path)


@pytest.fixture
def leases():
    held = []
    yield held
    for lease in held:
        lease.release()


def _resolve(ledger, leases, key=None, **kw):
    res = ledger.resolve(NewEntry(argv=("true",), cwd="/", idem_key=key, **kw),
                         force=False, pid=os.getpid())
    if res.lease is not None:
        leases.append(res.lease)
    return res


def _holder(root, kind):
    proc = subprocess.Popen([sys.executable, "-c", HOLDER, str(LIB_DIR), str(root), kind.value],
                            stdout=subprocess.PIPE, text=True)
    return proc, int(proc.stdout.readline())


def test_resolve_records_a_queued_ticket_holding_its_lease(ledger, leases):
    res = _resolve(ledger, leases)
    assert (res.outcome, res.entry.state, res.entry.pid) == (Outcome.NEW, EntryState.QUEUED, os.getpid())
    assert not otto_queue.lease.orphaned(ledger.root, res.entry.id)


def test_resolve_releases_a_ticket_lease_when_it_fails_after_taking_it(ledger, leases, monkeypatch):
    real_entry = otto_queue.ledger._entry

    def boom(_row):
        raise RuntimeError("post-take")

    monkeypatch.setattr(otto_queue.ledger, "_entry", boom)
    with pytest.raises(RuntimeError, match="post-take"):
        ledger.resolve(NewEntry(argv=("true",), cwd="/"), force=False, pid=os.getpid())
    monkeypatch.setattr(otto_queue.ledger, "_entry", real_entry)
    res = _resolve(ledger, leases)
    assert res.outcome is Outcome.NEW
    assert res.lease is not None


def test_a_queued_job_holds_no_lease(ledger, leases):
    res = _resolve(ledger, leases, kind=EntryKind.JOB)
    assert res.lease is None
    assert ledger.reap_orphans() == []


def test_a_key_matching_a_live_entry_joins_it(ledger, leases):
    first = _resolve(ledger, leases, "k")
    second = _resolve(ledger, leases, "k")
    assert (second.outcome, second.entry.id) == (Outcome.JOINED, first.entry.id)
    assert len(ledger.entries()) == 1


def test_a_key_matching_a_done_entry_reuses_it(ledger, leases):
    first = _resolve(ledger, leases, "k")
    ledger.mark_running(first.entry.id, os.getpid())
    assert ledger.finish(first.entry.id, 0) is EntryState.DONE
    second = _resolve(ledger, leases, "k")
    assert (second.outcome, second.entry.id, second.entry.exit_status) == (Outcome.REUSED, first.entry.id, 0)


def test_a_key_matching_a_failed_entry_runs_again(ledger, leases):
    first = _resolve(ledger, leases, "k")
    ledger.finish(first.entry.id, 3)
    assert _resolve(ledger, leases, "k").outcome is Outcome.NEW


def test_force_records_a_new_entry_whatever_the_key_matches(ledger, leases):
    first = _resolve(ledger, leases, "k")
    ledger.finish(first.entry.id, 0)
    forced = ledger.resolve(NewEntry(argv=("true",), cwd="/", idem_key="k"), force=True, pid=1)
    leases.append(forced.lease)
    assert forced.outcome is Outcome.NEW and forced.entry.id != first.entry.id


def test_a_key_never_joins_an_entry_whose_holder_is_gone(ledger, leases):
    first = ledger.resolve(NewEntry(argv=("true",), cwd="/", idem_key="k"), force=False, pid=1)
    first.lease.release()
    assert _resolve(ledger, leases, "k").outcome is Outcome.NEW
    assert ledger.get(first.entry.id).state is EntryState.FAILED


@pytest.mark.parametrize("code,state", [(0, EntryState.DONE), (os.EX_TEMPFAIL, EntryState.TEMPFAIL),
                                        (3, EntryState.FAILED)])
def test_finish_records_the_state_an_exit_status_implies(ledger, leases, code, state):
    res = _resolve(ledger, leases)
    ledger.mark_running(res.entry.id, os.getpid())
    assert ledger.finish(res.entry.id, code) is state
    entry = ledger.get(res.entry.id)
    assert (entry.state, entry.exit_status, entry.attempts) == (state, code, 1)


def test_cancelling_a_queued_entry_records_the_interrupt_status(ledger, leases):
    res = _resolve(ledger, leases)
    assert ledger.cancel(res.entry.id).state is EntryState.QUEUED
    entry = ledger.get(res.entry.id)
    assert (entry.state, entry.exit_status) == (EntryState.CANCELLED, core.proc.INTERRUPT_RETURNCODE)
    assert ledger.mark_running(res.entry.id, os.getpid()) is False


def test_finish_after_cancel_keeps_cancelled_and_records_the_status(ledger, leases):
    res = _resolve(ledger, leases)
    ledger.mark_running(res.entry.id, os.getpid())
    assert ledger.cancel(res.entry.id).state is EntryState.RUNNING
    assert ledger.finish(res.entry.id, 143) is EntryState.CANCELLED
    assert ledger.get(res.entry.id).exit_status == 143


def test_cancel_of_an_unknown_entry_is_none(ledger):
    assert ledger.cancel(99) is None


def test_a_sigkilled_ticket_holder_leaves_its_ticket_failed(ledger):
    proc, entry_id = _holder(ledger.root, EntryKind.TICKET)
    proc.send_signal(signal.SIGKILL)
    proc.wait(timeout=30)
    assert ledger.reap_orphans() == [entry_id]
    entry = ledger.get(entry_id)
    assert (entry.state, entry.exit_status) == (EntryState.FAILED, None)


def test_a_sigkilled_job_goes_back_to_queued(ledger):
    proc, entry_id = _holder(ledger.root, EntryKind.JOB)
    proc.send_signal(signal.SIGKILL)
    proc.wait(timeout=30)
    assert ledger.reap_orphans() == [entry_id]
    entry = ledger.get(entry_id)
    assert (entry.state, entry.attempts, entry.pid) == (EntryState.QUEUED, 1, None)


def test_a_live_holder_is_not_reaped(ledger):
    proc, entry_id = _holder(ledger.root, EntryKind.TICKET)
    try:
        assert ledger.reap_orphans() == []
        assert ledger.get(entry_id).state is EntryState.RUNNING
    finally:
        proc.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=30)


def test_first_waiting_is_the_oldest_queued_ticket_of_the_class(ledger, leases):
    _resolve(ledger, leases, kind=EntryKind.JOB)
    first = _resolve(ledger, leases)
    second = _resolve(ledger, leases)
    _resolve(ledger, leases, entry_class=EntryClass.LIGHT)
    assert ledger.first_waiting(EntryClass.HEAVY) == first.entry.id
    ledger.mark_running(first.entry.id, os.getpid())
    assert ledger.first_waiting(EntryClass.HEAVY) == second.entry.id


def test_old_code_writes_into_a_newer_schema(tmp_path, monkeypatch, leases):
    current = otto_queue.ledger.MIGRATIONS
    monkeypatch.setattr(otto_queue.ledger, "MIGRATIONS",
                        (*current, ("ALTER TABLE entries ADD COLUMN note TEXT",)))
    Ledger.open(tmp_path)
    monkeypatch.setattr(otto_queue.ledger, "MIGRATIONS", current)
    res = _resolve(Ledger.open(tmp_path), leases)
    assert res.outcome is Outcome.NEW and res.entry.state is EntryState.QUEUED


def _job(ledger, leases, **kw):
    return _resolve(ledger, leases, kind=EntryKind.JOB, **kw).entry.id


def test_retry_delay_doubles_from_a_minute_up_to_half_an_hour():
    assert [retry_delay(n) for n in (1, 2, 3, 6, 7, 10)] == [60.0, 120.0, 240.0, 1800.0, 1800.0, 1800.0]


def test_requeue_puts_a_running_job_back_with_its_retry_time(ledger, leases):
    entry_id = _job(ledger, leases)
    ledger.mark_running(entry_id, os.getpid())
    assert ledger.requeue(entry_id, os.EX_TEMPFAIL, 1060.0)
    entry = ledger.get(entry_id)
    assert (entry.state, entry.attempts, entry.exit_status, entry.not_before, entry.pid) == (
        EntryState.QUEUED, 1, os.EX_TEMPFAIL, 1060.0, None)


def test_requeue_leaves_a_cancelled_job_cancelled(ledger, leases):
    entry_id = _job(ledger, leases)
    ledger.mark_running(entry_id, os.getpid())
    ledger.cancel(entry_id)
    assert not ledger.requeue(entry_id, os.EX_TEMPFAIL, 1060.0)
    assert ledger.get(entry_id).state is EntryState.CANCELLED


def test_next_job_waits_for_the_retry_time(ledger, leases):
    first = _job(ledger, leases)
    second = _job(ledger, leases)
    ledger.mark_running(first, os.getpid())
    ledger.requeue(first, os.EX_TEMPFAIL, 2000.0)
    assert ledger.next_job(EntryClass.HEAVY, 1000.0).id == second
    ledger.mark_running(second, os.getpid())
    assert ledger.next_job(EntryClass.HEAVY, 1000.0) is None
    assert ledger.next_job(EntryClass.HEAVY, 2000.0).id == first


def test_live_jobs_counts_queued_and_running_jobs_only(ledger, leases):
    _resolve(ledger, leases)
    running = _job(ledger, leases)
    _job(ledger, leases)
    ledger.mark_running(running, os.getpid())
    assert ledger.live_jobs() == 2


def test_a_job_orphaned_at_its_attempt_cap_fails(ledger):
    proc, entry_id = _holder(ledger.root, EntryKind.JOB)
    set_columns(ledger.root, entry_id, attempts=MAX_JOB_ATTEMPTS)
    proc.send_signal(signal.SIGKILL)
    proc.wait(timeout=30)
    assert ledger.reap_orphans() == [entry_id]
    assert ledger.get(entry_id).state is EntryState.FAILED


def test_worker_alive_follows_the_worker_lock(tmp_path):
    assert not otto_queue.lease.worker_alive(tmp_path)
    held = otto_queue.lease.take_worker(tmp_path)
    try:
        assert otto_queue.lease.worker_alive(tmp_path)
        assert otto_queue.lease.take_worker(tmp_path) is None
    finally:
        held.release()
    assert not otto_queue.lease.worker_alive(tmp_path)


def test_releasing_the_worker_lock_does_not_unlink_the_file(tmp_path):
    held = otto_queue.lease.take_worker(tmp_path)
    path = otto_queue.paths.worker_lock_path(tmp_path)
    assert path.exists()
    held.release()
    assert path.exists()


def test_concurrent_worker_alive_probes_do_not_see_a_worker(tmp_path):
    held = otto_queue.lease.take_worker(tmp_path)
    held.release()
    start = tmp_path / "start"
    child = textwrap.dedent("""
        import sys, time
        from pathlib import Path
        sys.path.insert(0, sys.argv[1])
        import otto_queue.lease
        root, flag = Path(sys.argv[2]), Path(sys.argv[3])
        while not flag.exists():
            time.sleep(0.01)
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            if otto_queue.lease.worker_alive(root):
                raise SystemExit(1)
        raise SystemExit(0)
    """)
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", child, str(LIB_DIR), str(tmp_path), str(start)])
        for _ in range(2)
    ]
    time.sleep(0.1)
    start.touch()
    try:
        codes = [proc.wait(timeout=30) for proc in procs]
    finally:
        for proc in procs:
            _kill_wait(proc)
    assert codes == [0, 0]


def test_a_waiter_and_a_fresh_take_cannot_both_hold_the_worker_lock(tmp_path):
    held = otto_queue.lease.take_worker(tmp_path)
    proc = subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent("""
            import os, sys
            from pathlib import Path
            sys.path.insert(0, sys.argv[1])
            import otto_queue.lease
            import otto_queue.paths
            root = Path(sys.argv[2])
            fd = os.open(otto_queue.paths.worker_lock_path(root), os.O_RDWR | os.O_CLOEXEC)
            print("ready", flush=True)
            lock = otto_queue.lease.take_worker(root)
            print("got" if lock is not None else "none", flush=True)
            if lock is not None:
                import time
                time.sleep(60)
            os.close(fd)
        """), str(LIB_DIR), str(tmp_path)],
        stdout=subprocess.PIPE, text=True)
    again = None
    try:
        assert proc.stdout.readline().strip() == "ready"
        time.sleep(0.15)
        held.release()
        again = otto_queue.lease.take_worker(tmp_path)
        got = proc.stdout.readline().strip()
        assert not (got == "got" and again is not None)
    finally:
        if again is not None:
            again.release()
        proc.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=30)


def test_first_waiting_with_jobs_is_one_queue_across_kinds(ledger, leases):
    job = _job(ledger, leases)
    ticket = _resolve(ledger, leases).entry.id
    assert ledger.first_waiting(EntryClass.HEAVY) == ticket
    assert ledger.first_waiting(EntryClass.HEAVY, jobs=True) == job


def test_first_waiting_skips_a_job_whose_retry_time_has_not_come(ledger, leases):
    job = _job(ledger, leases)
    ledger.mark_running(job, os.getpid())
    ledger.requeue(job, os.EX_TEMPFAIL, 2000.0)
    ticket = _resolve(ledger, leases).entry.id
    assert ledger.first_waiting(EntryClass.HEAVY, jobs=True, now=1000.0) == ticket
    assert ledger.first_waiting(EntryClass.HEAVY, jobs=True, now=2000.0) == job


def test_without_reuse_a_done_key_runs_again(ledger, leases):
    done = _resolve(ledger, leases, key="k").entry.id
    ledger.mark_running(done, os.getpid())
    ledger.finish(done, 0)
    res = ledger.resolve(NewEntry(argv=("true",), cwd="/", idem_key="k", kind=EntryKind.JOB),
                         force=False, pid=None, reuse=False)
    assert res.outcome is Outcome.NEW and res.entry.id != done


def test_without_reuse_a_live_key_is_still_joined(ledger, leases):
    live = _job(ledger, leases, key="k")
    res = ledger.resolve(NewEntry(argv=("true",), cwd="/", idem_key="k", kind=EntryKind.JOB),
                         force=False, pid=None, reuse=False)
    assert (res.outcome, res.entry.id) == (Outcome.JOINED, live)


def test_claim_as_ticket_takes_a_queued_job_with_its_lease(ledger, leases):
    job = _job(ledger, leases)
    claimed = ledger.claim_as_ticket(job, os.getpid())
    leases.append(claimed.lease)
    assert (claimed.entry.kind, claimed.entry.state, claimed.entry.pid) == (EntryKind.TICKET, EntryState.QUEUED, os.getpid())
    assert not otto_queue.lease.orphaned(ledger.root, job)


def test_claim_as_ticket_leaves_a_job_a_worker_has_started(ledger, leases):
    job = _job(ledger, leases)
    ledger.mark_running(job, os.getpid())
    assert ledger.claim_as_ticket(job, os.getpid()) is None
    assert ledger.get(job).kind is EntryKind.JOB


def test_claim_as_ticket_returns_none_when_the_jobs_lease_is_held(ledger, leases):
    job = _job(ledger, leases)
    leases.append(otto_queue.lease.take(ledger.root, job))
    assert ledger.claim_as_ticket(job, os.getpid()) is None
    entry = ledger.get(job)
    assert (entry.kind, entry.state) == (EntryKind.JOB, EntryState.QUEUED)


def test_mark_running_records_the_log_path(ledger, leases):
    job = _job(ledger, leases)
    ledger.mark_running(job, os.getpid(), log_path="/tmp/q.log")
    assert ledger.get(job).log_path == "/tmp/q.log"
