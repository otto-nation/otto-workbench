"""Submit: hand a command to the worker as a job, or run it here when no worker can be started.

`otto-queue submit` returns as soon as the job is queued: unattended work
(scheduled maintenance now, `pr batch` steps next) outlives the process that
asked for it.

1. `Ledger.resolve` records a `queued` job, or answers from an entry with the
   same key as a ticket would: a `done` one is reused (unless `reuse` is off), a
   live one joined.
2. `otto_queue.supervisor.wake` starts the worker and confirms it.
3. When no worker comes up (no supervisor installed, no user bus), the job is
   claimed back as a ticket (`Ledger.claim_as_ticket`) and run here by
   `otto_queue.ticket.run_claimed`, and the caller is told so. A job a worker
   took in the meantime is left to it. Joining a live ticket waits for its
   status, and re-runs the work if that ticket ends without one. The queue's
   absence degrades to today's behaviour, never to work waiting for a worker
   that will not come. With `run_here` off (`--queue-only`) a live entry is
   joined at 0 without waiting or cancelling, a `done` entry's status is
   returned, and a job this process created (`NEW`) is cancelled instead —
   `submit` then returns 69 (EX_UNAVAILABLE) having run nothing. A job it only
   joined stays queued for the next supervisor start. That is for a caller
   that has its own way to run the command when there is no worker and must not
   block on it, such as maintenance's auto-task agents
   (`maintenance/bin/run-due-auto-tasks`). An unopenable ledger, or an entry
   that ended without a result, answers such a caller the same way.

A ledger that cannot be opened runs the command unqueued, as a ticket does; one
that stays busy returns 75. Exit 75 from a command run here is returned, not
retried: there is no worker to retry it.
"""

# doc-group: queue

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import core.log
import otto_queue.ledger
import otto_queue.slots
import otto_queue.supervisor
import otto_queue.ticket
from core.exit_codes import ExitCode
from otto_queue.model import Entry, EntryClass, EntryKind, EntryState, NewEntry


class SubmitOutcome(StrEnum):
    QUEUED = "queued"
    JOINED = "joined"
    REUSED = "reused"
    RAN_HERE = "ran_here"
    UNQUEUED = "unqueued"
    BUSY = "busy"
    NO_WORKER = "no_worker"


@dataclass(frozen=True)
class SubmitRequest:
    """What to run, where, in which class, and how to recognise a repeat of it."""

    argv: tuple[str, ...]
    cwd: str
    entry_class: EntryClass = EntryClass.HEAVY
    idem_key: str | None = None
    force: bool = False
    reuse: bool = True
    run_here: bool = True

    def entry(self) -> NewEntry:
        return NewEntry(argv=self.argv, cwd=self.cwd, kind=EntryKind.JOB,
                        entry_class=self.entry_class, idem_key=self.idem_key)

    def ticket(self) -> otto_queue.ticket.TicketRequest:
        return otto_queue.ticket.TicketRequest(argv=self.argv, cwd=self.cwd, entry_class=self.entry_class,
                                               idem_key=self.idem_key, force=self.force, reuse=self.reuse)


@dataclass(frozen=True)
class SubmitResult:
    """The status to exit with, how it was reached, and the entry behind it."""

    exit_status: int
    outcome: SubmitOutcome
    entry_id: int | None = None


def submit(request: SubmitRequest, policy: otto_queue.slots.Policy, *, root: Path | None = None,
           wake: Callable[[Path], otto_queue.supervisor.Wake] = otto_queue.supervisor.wake,
           notify: Callable[[str], None] = core.log.info) -> SubmitResult:
    """Queue *request* for the worker, or run it here; see the module docstring."""
    try:
        ledger = otto_queue.ledger.Ledger.open(root)
    except otto_queue.ledger.LedgerBusy as exc:
        core.log.warn(f"otto-queue: queue busy: {exc}")
        return SubmitResult(ExitCode.TEMPFAIL, SubmitOutcome.BUSY)
    except otto_queue.ledger.LedgerError as exc:
        return _unqueued(request, policy, root, str(exc), notify)
    res = ledger.resolve(request.entry(), force=request.force, pid=None, reuse=request.reuse)
    if res.outcome is otto_queue.ledger.Outcome.REUSED:
        notify(f"otto-queue: entry {res.entry.id} already did this; not running it again")
        return SubmitResult(res.entry.exit_status or 0, SubmitOutcome.REUSED, res.entry.id)
    woke = wake(ledger.root)
    if woke.alive:
        return _handed_over(ledger, res, request, policy, notify)
    return _run_here(ledger, res, request, policy, woke.reason, notify)


def _handed_over(ledger: otto_queue.ledger.Ledger, res: otto_queue.ledger.Resolution,
                 request: SubmitRequest, policy: otto_queue.slots.Policy,
                 notify: Callable[[str], None]) -> SubmitResult:
    if res.outcome is not otto_queue.ledger.Outcome.JOINED:
        notify(f"otto-queue: queued entry {res.entry.id}; "
               "`otto-queue status` shows it")
        return SubmitResult(0, SubmitOutcome.QUEUED, res.entry.id)
    if res.entry.kind is EntryKind.JOB or not request.run_here:
        notify(f"otto-queue: joined entry {res.entry.id}; "
               "`otto-queue status` shows it")
        return SubmitResult(0, SubmitOutcome.JOINED, res.entry.id)
    return _wait_on_ticket(request, policy, ledger.root, notify)


def _run_here(ledger: otto_queue.ledger.Ledger, res: otto_queue.ledger.Resolution,
              request: SubmitRequest, policy: otto_queue.slots.Policy, reason: str,
              notify: Callable[[str], None]) -> SubmitResult:
    if not request.run_here and res.outcome is not otto_queue.ledger.Outcome.NEW:
        notify(f"otto-queue: joined entry {res.entry.id}; "
               "`otto-queue status` shows it")
        return SubmitResult(0, SubmitOutcome.JOINED, res.entry.id)
    claimed = ledger.claim_as_ticket(res.entry.id, os.getpid())
    if claimed is None:
        return _after_missed_claim(ledger, res.entry.id, request, policy, notify)
    if not request.run_here:
        return _withdraw(ledger, claimed, reason, notify)
    notify(f"otto-queue: no worker ({reason}); running entry {res.entry.id} as a ticket here")
    result = otto_queue.ticket.run_claimed(
        ledger, claimed, _ticket_for(claimed.entry, request), policy, notify=notify)
    return SubmitResult(result.exit_status, SubmitOutcome.RAN_HERE, res.entry.id)


def _withdraw(ledger: otto_queue.ledger.Ledger, claimed: otto_queue.ledger.Resolution,
              reason: str, notify: Callable[[str], None]) -> SubmitResult:
    """Cancel a job no worker will run, for a caller that runs it some other way on 69.

    Cancelled while its lease is still held, so no reaper sees it orphaned in between.
    """
    try:
        ledger.cancel(claimed.entry.id)
    finally:
        claimed.lease.release()
    notify(f"otto-queue: no worker ({reason}); withdrew entry {claimed.entry.id} and ran nothing")
    return SubmitResult(ExitCode.UNAVAILABLE, SubmitOutcome.NO_WORKER, claimed.entry.id)


def _unqueued(request: SubmitRequest, policy: otto_queue.slots.Policy, root: Path | None,
              reason: str, notify: Callable[[str], None]) -> SubmitResult:
    """The ledger cannot be opened: run the command here unqueued, as a ticket would, unless *run_here* is off."""
    if not request.run_here:
        notify(f"otto-queue: queue unavailable ({reason}); ran nothing")
        return SubmitResult(ExitCode.UNAVAILABLE, SubmitOutcome.NO_WORKER)
    result = otto_queue.ticket.run_ticket(request.ticket(), policy, root=root, notify=notify)
    return SubmitResult(result.exit_status, SubmitOutcome.UNQUEUED)


def _after_missed_claim(ledger: otto_queue.ledger.Ledger, entry_id: int,
                        request: SubmitRequest, policy: otto_queue.slots.Policy,
                        notify: Callable[[str], None]) -> SubmitResult:
    entry = ledger.get(entry_id)
    if entry is not None and entry.kind is EntryKind.JOB and entry.state.live:
        notify(f"otto-queue: entry {entry_id} is with the worker; leaving it to that run")
        return SubmitResult(0, SubmitOutcome.JOINED, entry_id)
    if request.run_here:
        return _wait_on_ticket(request, policy, ledger.root, notify)
    return _queue_only_miss(entry, entry_id, notify)


def _queue_only_miss(entry: Entry | None, entry_id: int,
                     notify: Callable[[str], None]) -> SubmitResult:
    if entry is not None and entry.state.live:
        notify(f"otto-queue: joined entry {entry_id}; leaving it running")
        return SubmitResult(0, SubmitOutcome.JOINED, entry_id)
    if entry is not None and entry.state is EntryState.DONE:
        notify(f"otto-queue: entry {entry_id} already did this; not running it again")
        return SubmitResult(entry.exit_status or 0, SubmitOutcome.REUSED, entry_id)
    notify(f"otto-queue: entry {entry_id} ended without a result; ran nothing")
    return SubmitResult(ExitCode.UNAVAILABLE, SubmitOutcome.NO_WORKER, entry_id)


def _wait_on_ticket(request: SubmitRequest, policy: otto_queue.slots.Policy,
                    root: Path, notify: Callable[[str], None]) -> SubmitResult:
    result = otto_queue.ticket.run_ticket(request.ticket(), policy, root=root, notify=notify)
    return _from_ticket(result)


def _ticket_for(entry: Entry, request: SubmitRequest) -> otto_queue.ticket.TicketRequest:
    return otto_queue.ticket.claimed_request(entry, request.ticket())


def _from_ticket(result: otto_queue.ticket.TicketResult) -> SubmitResult:
    mapped = {
        otto_queue.ticket.TicketOutcome.RAN: SubmitOutcome.RAN_HERE,
        otto_queue.ticket.TicketOutcome.NESTED: SubmitOutcome.RAN_HERE,
        otto_queue.ticket.TicketOutcome.TIMED_OUT: SubmitOutcome.BUSY,
        otto_queue.ticket.TicketOutcome.CANCELLED: SubmitOutcome.JOINED,
    }.get(result.outcome)
    if mapped is None:
        mapped = SubmitOutcome(result.outcome.value)
    return SubmitResult(result.exit_status, mapped, result.entry_id)
