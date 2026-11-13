"""Tests for otto_queue.job: the process that runs one queued job and records its result.

`run_job` installs signal handlers while its command runs, so it is called on the main thread.
"""

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import core.log  # noqa: E402
import otto_queue.job  # noqa: E402
import otto_queue.lease  # noqa: E402
from otto_queue.job import JobOutcome  # noqa: E402
from otto_queue.ledger import Ledger, LedgerBusy  # noqa: E402
from otto_queue.model import MAX_JOB_ATTEMPTS, EntryKind, EntryState, NewEntry  # noqa: E402
from otto_queue_support import exit_argv, set_columns, touch_argv  # noqa: E402

PY = sys.executable


def _job(argv):
    ledger = Ledger.open()
    res = ledger.resolve(NewEntry(argv=tuple(argv), cwd=os.getcwd(), kind=EntryKind.JOB), force=False, pid=None)
    return ledger, res.entry.id


def test_a_job_runs_records_done_and_logs_its_output():
    ledger, entry_id = _job((PY, "-c", "print('hello from the job')"))
    result = otto_queue.job.run_job(entry_id, None)
    entry = ledger.get(entry_id)
    assert (result.outcome, entry.state, entry.exit_status, entry.attempts) == (
        JobOutcome.FINISHED, EntryState.DONE, 0, 1)
    assert "hello from the job" in Path(entry.log_path).read_text()


def test_exit_75_requeues_with_backoff_and_the_attempt_count_rises():
    ledger, entry_id = _job(exit_argv(75))
    first = otto_queue.job.run_job(entry_id, None, clock=lambda: 1000.0)
    entry = ledger.get(entry_id)
    assert (first.outcome, entry.state, entry.attempts, entry.exit_status, entry.not_before) == (
        JobOutcome.RETRY, EntryState.QUEUED, 1, 75, 1060.0)
    otto_queue.job.run_job(entry_id, None, clock=lambda: 2000.0)
    entry = ledger.get(entry_id)
    assert (entry.state, entry.attempts, entry.not_before) == (EntryState.QUEUED, 2, 2120.0)


def test_exit_75_on_the_last_attempt_ends_tempfail():
    ledger, entry_id = _job(exit_argv(75))
    set_columns(ledger.root, entry_id, attempts=MAX_JOB_ATTEMPTS - 1)
    result = otto_queue.job.run_job(entry_id, None)
    entry = ledger.get(entry_id)
    assert (result.outcome, entry.state, entry.attempts) == (JobOutcome.FINISHED, EntryState.TEMPFAIL, MAX_JOB_ATTEMPTS)


def test_a_cancelled_job_is_not_run(tmp_path):
    ledger, entry_id = _job(touch_argv(tmp_path / "ran"))
    ledger.cancel(entry_id)
    assert otto_queue.job.run_job(entry_id, None).outcome is JobOutcome.NOT_CLAIMED
    assert not (tmp_path / "ran").exists()


def test_a_second_runner_for_one_job_does_not_run_it(tmp_path):
    ledger, entry_id = _job(touch_argv(tmp_path / "ran"))
    held = otto_queue.lease.take(ledger.root, entry_id)
    try:
        assert otto_queue.job.run_job(entry_id, None).outcome is JobOutcome.NOT_CLAIMED
    finally:
        held.release()
    assert not (tmp_path / "ran").exists()
    assert ledger.get(entry_id).state is EntryState.QUEUED


def test_the_slot_descriptor_is_closed_when_the_job_ends(tmp_path):
    _, entry_id = _job(exit_argv(0))
    fd = os.open(tmp_path / "slot", os.O_RDWR | os.O_CREAT)
    otto_queue.job.run_job(entry_id, fd)
    with pytest.raises(OSError):
        os.fstat(fd)


def test_the_job_sees_its_entry_id(tmp_path):
    out = tmp_path / "id"
    _, entry_id = _job((PY, "-c", f"import os; open({str(out)!r}, 'w').write(os.environ['OTTO_QUEUE_ENTRY'])"))
    otto_queue.job.run_job(entry_id, None)
    assert out.read_text() == str(entry_id)


def test_a_record_failure_fails_the_row_and_propagates(monkeypatch):
    ledger, entry_id = _job(exit_argv(0))

    def boom(self, entry_id, exit_status):
        raise LedgerBusy("busy")

    monkeypatch.setattr(Ledger, "finish", boom)
    with pytest.raises(LedgerBusy):
        otto_queue.job.run_job(entry_id, None)
    assert ledger.get(entry_id).state is EntryState.FAILED


def test_a_command_that_cannot_start_is_logged():
    ledger, entry_id = _job(("no-such-command-xyz",))
    result = otto_queue.job.run_job(entry_id, None)
    entry = ledger.get(entry_id)
    assert result.exit_status == 127
    assert "cannot run no-such-command-xyz" in Path(entry.log_path).read_text()


def test_a_closed_slot_fd_warns_and_the_job_still_runs(tmp_path, monkeypatch):
    ledger, entry_id = _job(exit_argv(0))
    fd = os.open(tmp_path / "slot", os.O_RDWR | os.O_CREAT)
    os.close(fd)
    warnings = []
    monkeypatch.setattr(core.log, "warn", warnings.append)
    result = otto_queue.job.run_job(entry_id, fd)
    entry = ledger.get(entry_id)
    assert (result.outcome, entry.state, entry.exit_status) == (
        JobOutcome.FINISHED, EntryState.DONE, 0)
    assert any(
        f"job {entry_id} was handed no slot descriptor (fd {fd})" in message
        for message in warnings)


def test_an_execute_failure_keeps_its_exception_when_fail_is_busy(monkeypatch):
    _, entry_id = _job(exit_argv(0))

    def boom_execute(entry, log):
        raise RuntimeError("command died")

    def boom_fail(self, entry_id):
        raise LedgerBusy("busy")

    monkeypatch.setattr(otto_queue.job, "_execute", boom_execute)
    monkeypatch.setattr(Ledger, "fail", boom_fail)
    with pytest.raises(RuntimeError, match="command died"):
        otto_queue.job.run_job(entry_id, None)
