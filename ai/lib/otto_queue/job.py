"""The process that runs one job: `otto-queue job-exec ID --slot-fd N`, started by the worker.

The worker admits the job and starts this process holding the admitted slot:
the descriptor is inherited, so the slot stays held exactly as long as this
process lives, whether or not the worker does. Here the job's lease is taken,
then the row moves to `running`, so only one copy of this process ever runs a
job — a second, started by a worker that restarted, finds the lease held or the
row no longer `queued` and leaves. The command runs in its own session with
stdin empty and its output appended to `queue/logs/<id>.log`. `otto-queue
cancel` sends SIGTERM to this process, which forwards it to the command's group.

When the command exits, this process records the result itself, so a worker
that died meanwhile loses nothing:

| Exit | Recorded |
|---|---|
| 75, before the job has run `MAX_JOB_ATTEMPTS` times | `queued` again, `not_before` = now + `retry_delay(attempts)`, the 75 kept as `exit_status` |
| 75 on the last attempt | `tempfail` |
| anything else | the state `state_for_exit` gives; a cancelled entry stays `cancelled` |

ceiling: this process, not the command, holds the lease, so a SIGKILL of this
process while its command runs leaves the command running untracked and the
row re-queued: the job runs twice. An uncaught SIGTERM outside the relay
window (after mark_running before forwarding_signals, or after run_child
before _record) has the same effect — the row is re-queued and the job may
run again. Upgrade trigger: if a job is seen running twice, have the command
die with its runner (PR_SET_PDEATHSIG on Linux).
"""

# doc-group: queue

from __future__ import annotations

import contextlib
import os
import shlex
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import core.log
import core.signal_relay
import otto_queue.lease
import otto_queue.ledger
import otto_queue.paths
import otto_queue.ticket
from otto_queue.model import MAX_JOB_ATTEMPTS, Entry, EntryState, retry_delay


class JobOutcome(StrEnum):
    NOT_CLAIMED = "not_claimed"
    RETRY = "retry"
    FINISHED = "finished"


@dataclass(frozen=True)
class JobResult:
    """How the run ended, the command's status, and the state recorded for it."""

    outcome: JobOutcome
    exit_status: int = 0
    state: EntryState | None = None


def run_job(entry_id: int, slot_fd: int | None, *, root: Path | None = None,
            clock: Callable[[], float] = time.time) -> JobResult:
    """Run queued job *entry_id*, holding the slot on *slot_fd*; see the module docstring."""
    if slot_fd is not None:
        try:
            os.fstat(slot_fd)
        except OSError:
            core.log.warn(
                f"otto-queue: job {entry_id} was handed no slot descriptor (fd {slot_fd}): "
                "admission is not holding its slot")
    try:
        return _run_leased(otto_queue.ledger.Ledger.open(root), entry_id, clock)
    finally:
        _close(slot_fd)


def _close(fd: int | None) -> None:
    if fd is None:
        return
    with contextlib.suppress(OSError):
        os.close(fd)


def _run_leased(ledger: otto_queue.ledger.Ledger, entry_id: int,
                clock: Callable[[], float]) -> JobResult:
    try:
        lease = otto_queue.lease.take(ledger.root, entry_id)
    except otto_queue.lease.LeaseHeld:
        return JobResult(JobOutcome.NOT_CLAIMED)
    try:
        return _run_claimed(ledger, entry_id, clock)
    finally:
        lease.release()


def _run_claimed(ledger: otto_queue.ledger.Ledger, entry_id: int,
                 clock: Callable[[], float]) -> JobResult:
    log = otto_queue.paths.log_path(ledger.root, entry_id)
    if not ledger.mark_running(entry_id, os.getpid(), log_path=str(log)):
        return JobResult(JobOutcome.NOT_CLAIMED)
    entry = ledger.get(entry_id)
    try:
        code = _execute(entry, log)
    except BaseException:
        # Terminal before the lease is released, or the reaper re-queues it.
        with contextlib.suppress(BaseException):
            ledger.fail(entry_id)
        raise
    try:
        return _record(ledger, entry, code, clock)
    except BaseException:
        with contextlib.suppress(BaseException):
            ledger.fail(entry_id)
        raise


def _execute(entry: Entry, log: Path) -> int:
    log.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env[otto_queue.ticket.ENTRY_ENV] = str(entry.id)
    with open(log, "ab") as out:
        out.write(f"otto-queue: entry {entry.id}, attempt {entry.attempts}: {shlex.join(entry.argv)}\n".encode())
        out.flush()
        code = core.signal_relay.run_child(list(entry.argv), env=env, cwd=entry.cwd,
                                           origin="otto-queue", output=out)
        out.write(f"otto-queue: exit {code}\n".encode())
    return code


def _record(ledger: otto_queue.ledger.Ledger, entry: Entry, code: int,
            clock: Callable[[], float]) -> JobResult:
    if code == os.EX_TEMPFAIL and entry.attempts < MAX_JOB_ATTEMPTS:
        if ledger.requeue(entry.id, code, clock() + retry_delay(entry.attempts)):
            return JobResult(JobOutcome.RETRY, code, EntryState.QUEUED)
    return JobResult(JobOutcome.FINISHED, code, ledger.finish(entry.id, code))
