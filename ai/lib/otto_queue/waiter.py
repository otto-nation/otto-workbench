"""Wait: block until a queue entry ends and answer with its status.

`otto-queue wait ID` is the second half of `otto-queue submit`: submit records
a durable job and returns its id, wait follows it. A waiter holds nothing the
entry depends on, so one that dies or times out loses no work — wait again with
the same id. It prints where the output goes first: a job's log file, or the
terminal that started a ticket.

A queued job with no worker alive is run by the waiter itself
(`otto_queue.ticket.join` adopts it), so waiting cannot outlast every worker.
Timing out returns 75 and leaves the entry as it was. Ctrl-C propagates and
leaves the entry as it was; `otto-queue cancel` is what stops an entry.
"""

# doc-group: queue

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import core.log
import otto_queue.ledger
import otto_queue.paths
import otto_queue.slots
import otto_queue.ticket
from core.exit_codes import ExitCode
from otto_queue.model import Entry, EntryKind


class WaitOutcome(StrEnum):
    FINISHED = "finished"
    TIMED_OUT = "timed_out"
    NOT_FOUND = "not_found"
    NO_RESULT = "no_result"


@dataclass(frozen=True)
class WaitResult:
    """The status to exit with, how it was reached, and the entry waited on."""

    exit_status: int
    outcome: WaitOutcome
    entry_id: int


def wait_entry(entry_id: int, policy: otto_queue.slots.Policy, *, root: Path | None = None,
               wait_s: float = otto_queue.ticket.DEFAULT_WAIT_S,
               poll_s: float = otto_queue.ticket.POLL_S,
               notify: Callable[[str], None] = core.log.info) -> WaitResult:
    """Wait up to *wait_s* for entry *entry_id* to end; see the module docstring.

    `LedgerBusy` and `LedgerError` from opening the ledger propagate to the caller.
    """
    ledger = otto_queue.ledger.Ledger.open(root)
    entry = ledger.get(entry_id)
    if entry is None:
        notify(f"otto-queue: no entry {entry_id}")
        return WaitResult(ExitCode.FAILURE, WaitOutcome.NOT_FOUND, entry_id)
    notify(_where_output_goes(ledger.root, entry))
    if entry.state.live:
        result = otto_queue.ticket.join(ledger, entry, wait_s=wait_s, policy=policy,
                                        poll_s=poll_s, notify=notify)
        if result is not None and result.outcome is otto_queue.ticket.TicketOutcome.TIMED_OUT:
            notify(f"otto-queue: entry {entry_id} is still {_state(ledger, entry_id)}; "
                   f"run `otto-queue wait {entry_id}` again")
            return WaitResult(ExitCode.TEMPFAIL, WaitOutcome.TIMED_OUT, entry_id)
        if result is not None:
            return WaitResult(result.exit_status, WaitOutcome.FINISHED, entry_id)
        entry = ledger.get(entry_id)
    return _ended(entry, entry_id, notify)


def _ended(entry: Entry | None, entry_id: int, notify: Callable[[str], None]) -> WaitResult:
    if entry is not None and not entry.state.live and entry.exit_status is not None:
        return WaitResult(entry.exit_status, WaitOutcome.FINISHED, entry_id)
    notify(f"otto-queue: entry {entry_id} ended without a status")
    return WaitResult(ExitCode.FAILURE, WaitOutcome.NO_RESULT, entry_id)


def _state(ledger: otto_queue.ledger.Ledger, entry_id: int) -> str:
    entry = ledger.get(entry_id)
    return entry.state.value if entry is not None else "unknown"


def _where_output_goes(root: Path, entry: Entry) -> str:
    if entry.kind is EntryKind.JOB:
        return f"otto-queue: entry {entry.id}: output in {otto_queue.paths.log_path(root, entry.id)}"
    return f"otto-queue: entry {entry.id} is a ticket; its output is in the terminal that started it"
