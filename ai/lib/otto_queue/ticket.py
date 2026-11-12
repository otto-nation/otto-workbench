"""Tickets: run a command in this process once the queue admits it.

`run_ticket` is what `otto-queue run --ticket` does. Interactive callers (a
Herdr pane, a CLI call) use it because the work stays in the caller's
terminal: visible, interruptible, and dying with the pane, exactly as it does
unqueued. What the queue adds is that the work waits its turn, counts against
the machine's budget, and runs once per idempotency key.

1. **Resolve.** `Ledger.resolve` either answers a keyed request from an entry
   with the same key or records a new `queued` row holding its lease. A
   matching `done` entry returns its recorded status without running. A
   matching live entry is joined: the ticket waits for it and returns its
   status. `force` skips the match. A joined entry whose holder died is not a
   result: the ticket resolves again and, finding nothing live, runs the work
   itself.
2. **Wait.** Every `POLL_S`, orphans are reaped, and only the oldest waiting
   ticket of the class asks `otto_queue.slots.try_admit`. Each new reason for
   waiting is printed once. `cancel` or Ctrl-C while waiting ends the ticket
   with 130. Reaching `wait_s` with no slot records `tempfail` and returns 75
   (EX_TEMPFAIL), so a caller can tell "never got room" from a failure.
3. **Run.** The row moves to `running`, and the command runs through
   `core.signal_relay.run_child(foreground=True)`: stdio inherited, the
   terminal's Ctrl-C and hangup reaching it directly, SIGTERM (`otto-queue
   cancel`) forwarded. Its exit status is recorded (75 as `tempfail`, never a
   failure) and returned.

The command sees `OTTO_QUEUE_ENTRY`, its entry id. A ticket started under that
variable runs its command directly: a nested heavy ticket would otherwise wait
for a slot its own parent holds. When the ledger stays busy past the open
deadline, the ticket returns 75 without running. When the ledger cannot be
opened at all, the command runs unqueued with a warning. The queue's absence
degrades to today's behaviour, never to work not done.

ceiling: the ticket process, not its command, holds the lease and the slot, so
a ticket killed with SIGKILL while its command runs leaves the command running
untracked and its row `failed`. Upgrade trigger: if a command is seen outliving
its killed ticket, have the child inherit the lease and slot descriptors.
"""

# doc-group: queue

from __future__ import annotations

import contextlib
import os
import signal
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import core.log
import core.proc
import core.signal_relay
import otto_queue.lease
import otto_queue.ledger
import otto_queue.slots
from otto_queue.model import Entry, EntryClass, EntryState, NewEntry

# How long a ticket waits for a slot before ending as `tempfail`. A review takes
# about ten minutes, so an hour is several heavy entries deep: long enough that
# only a ticket stuck behind a wedged entry times out.
DEFAULT_WAIT_S = 3600.0

# Between admission attempts, and between looks at a joined entry.
POLL_S = 0.5

ENTRY_ENV = "OTTO_QUEUE_ENTRY"


class TicketOutcome(StrEnum):
    RAN = "ran"
    JOINED = "joined"
    REUSED = "reused"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    NESTED = "nested"
    UNQUEUED = "unqueued"


@dataclass(frozen=True)
class TicketRequest:
    """What to run, where, in which class, and how to recognise a repeat of it."""

    argv: tuple[str, ...]
    cwd: str
    entry_class: EntryClass = EntryClass.HEAVY
    idem_key: str | None = None
    force: bool = False
    wait_s: float = DEFAULT_WAIT_S

    def entry(self) -> NewEntry:
        return NewEntry(argv=self.argv, cwd=self.cwd, entry_class=self.entry_class,
                        idem_key=self.idem_key)


@dataclass(frozen=True)
class TicketResult:
    """The status to exit with, how it was reached, and the entry behind it."""

    exit_status: int
    outcome: TicketOutcome
    entry_id: int | None = None


class _WaitEnd(StrEnum):
    ADMITTED = "admitted"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


@dataclass(frozen=True)
class _Wait:
    end: _WaitEnd
    slot: otto_queue.slots.Slot | None = None


def run_ticket(request: TicketRequest, policy: otto_queue.slots.Policy, *,
               root: Path | None = None, poll_s: float = POLL_S,
               notify: Callable[[str], None] = core.log.info) -> TicketResult:
    """Run *request* once the queue admits it; see the module docstring."""
    if os.environ.get(ENTRY_ENV):
        return TicketResult(_run(request, None), TicketOutcome.NESTED)
    try:
        ledger = otto_queue.ledger.Ledger.open(root)
    except otto_queue.ledger.LedgerBusy as exc:
        core.log.warn(f"otto-queue: queue busy: {exc}")
        return TicketResult(os.EX_TEMPFAIL, TicketOutcome.TIMED_OUT)
    except otto_queue.ledger.LedgerError as exc:
        core.log.warn(f"otto-queue: running unqueued: {exc}")
        return TicketResult(_run(request, None), TicketOutcome.UNQUEUED)
    while True:
        res = ledger.resolve(request.entry(), force=request.force, pid=os.getpid())
        if res.outcome is otto_queue.ledger.Outcome.NEW:
            return _run_new(ledger, res, request, policy, poll_s, notify)
        if res.outcome is otto_queue.ledger.Outcome.REUSED:
            notify(f"otto-queue: entry {res.entry.id} already did this; not running it again")
            return TicketResult(res.entry.exit_status or 0, TicketOutcome.REUSED, res.entry.id)
        joined = _join(ledger, res.entry, poll_s, notify)
        if joined is not None:
            return joined


def _join(ledger: otto_queue.ledger.Ledger, entry: Entry, poll_s: float,
          notify: Callable[[str], None]) -> TicketResult | None:
    """Wait for *entry* to end and return its status; None when it ended with none (orphaned)."""
    entry_id = entry.id
    notify(f"otto-queue: joining entry {entry_id}, which is {entry.state.value}")
    while entry is not None and entry.state.live:
        time.sleep(poll_s)
        ledger.reap_orphans()
        entry = ledger.get(entry_id)
    if entry is None or entry.exit_status is None:
        notify(f"otto-queue: entry {entry_id} ended without a result; queuing again")
        return None
    outcome = TicketOutcome.CANCELLED if entry.state is EntryState.CANCELLED else TicketOutcome.JOINED
    return TicketResult(entry.exit_status, outcome, entry_id)


def _record_crash(ledger: otto_queue.ledger.Ledger, entry_id: int, exc: BaseException) -> None:
    # Terminal before the lease (and any slot) is released: a live row
    # whose lease is gone is an orphan, and reap_orphans marks it failed.
    if isinstance(exc, KeyboardInterrupt):
        ledger.cancel(entry_id)
        ledger.finish(entry_id, core.proc.INTERRUPT_RETURNCODE)
        return
    ledger.fail(entry_id)


def _run_new(ledger: otto_queue.ledger.Ledger,
             res: otto_queue.ledger.Resolution, request: TicketRequest,
             policy: otto_queue.slots.Policy, poll_s: float, notify: Callable[[str], None]) -> TicketResult:
    entry_id = res.entry.id
    try:
        return _admit_and_run(ledger, entry_id, request, policy, poll_s, notify)
    except BaseException as exc:
        _record_crash(ledger, entry_id, exc)
        if isinstance(exc, KeyboardInterrupt):
            return TicketResult(core.proc.INTERRUPT_RETURNCODE, TicketOutcome.CANCELLED, entry_id)
        raise
    finally:
        res.lease.release()


def _admit_and_run(ledger: otto_queue.ledger.Ledger, entry_id: int,
                  request: TicketRequest, policy: otto_queue.slots.Policy,
                  poll_s: float, notify: Callable[[str], None]) -> TicketResult:
    wait = _wait_for_slot(ledger, entry_id, request, policy, poll_s, notify)
    if wait.end is _WaitEnd.CANCELLED:
        return TicketResult(core.proc.INTERRUPT_RETURNCODE, TicketOutcome.CANCELLED, entry_id)
    if wait.end is _WaitEnd.TIMED_OUT:
        ledger.finish(entry_id, os.EX_TEMPFAIL)
        notify(f"otto-queue: entry {entry_id} found no room in {request.wait_s:g}s")
        return TicketResult(os.EX_TEMPFAIL, TicketOutcome.TIMED_OUT, entry_id)
    try:
        started = ledger.mark_running(entry_id, os.getpid())
        code = _run(request, entry_id) if started else core.proc.INTERRUPT_RETURNCODE
        if not started:
            return TicketResult(code, TicketOutcome.CANCELLED, entry_id)
        state = ledger.finish(entry_id, code)
        outcome = TicketOutcome.CANCELLED if state is EntryState.CANCELLED else TicketOutcome.RAN
        return TicketResult(code, outcome, entry_id)
    except BaseException as exc:
        _record_crash(ledger, entry_id, exc)
        raise
    finally:
        wait.slot.release()


def _wait_for_slot(ledger: otto_queue.ledger.Ledger, entry_id: int,
                 request: TicketRequest, policy: otto_queue.slots.Policy,
                 poll_s: float, notify: Callable[[str], None]) -> _Wait:
    deadline = time.monotonic() + request.wait_s
    said = ""
    while True:
        entry = ledger.get(entry_id)
        if entry is None or entry.state is not EntryState.QUEUED:
            return _Wait(_WaitEnd.CANCELLED)
        admission = _try_once(ledger, entry_id, request.entry_class, policy)
        if admission.slot is not None:
            return _Wait(_WaitEnd.ADMITTED, admission.slot)
        if admission.reason != said:
            notify(f"otto-queue: entry {entry_id} is waiting: {admission.reason}")
            said = admission.reason
        if time.monotonic() >= deadline:
            return _Wait(_WaitEnd.TIMED_OUT)
        time.sleep(poll_s)


def _try_once(ledger: otto_queue.ledger.Ledger, entry_id: int, entry_class: EntryClass,
              policy: otto_queue.slots.Policy) -> otto_queue.slots.Admission:
    """Ask for a slot, unless an older ticket of the class is still waiting."""
    ledger.reap_orphans()
    first = ledger.first_waiting(entry_class)
    if first is not None and first != entry_id:
        return otto_queue.slots.Admission(None, f"behind entry {first}, which queued first")
    return otto_queue.slots.try_admit(ledger.root, policy, entry_class)


def _run(request: TicketRequest, entry_id: int | None) -> int:
    env = dict(os.environ)
    if entry_id is not None:
        env[ENTRY_ENV] = str(entry_id)
    return core.signal_relay.run_child(list(request.argv), env=env, cwd=request.cwd,
                                       origin="otto-queue", foreground=True)


class CancelOutcome(StrEnum):
    CANCELLED = "cancelled"
    SIGNALLED = "signalled"
    NOT_FOUND = "not_found"
    FINISHED = "finished"


@dataclass(frozen=True)
class CancelResult:
    outcome: CancelOutcome
    entry: Entry | None = None


def cancel_entry(ledger: otto_queue.ledger.Ledger, entry_id: int) -> CancelResult:
    """Cancel *entry_id*. A waiting entry stops waiting; a running one's process gets SIGTERM.

    The signal goes only to a process whose lease is still held, so a pid the
    kernel has since handed to something else is never signalled.
    """
    before = ledger.cancel(entry_id)
    if before is None:
        return CancelResult(CancelOutcome.NOT_FOUND)
    if not before.state.live:
        return CancelResult(CancelOutcome.FINISHED, before)
    if before.state is EntryState.QUEUED or before.pid is None:
        return CancelResult(CancelOutcome.CANCELLED, before)
    if otto_queue.lease.orphaned(ledger.root, entry_id):
        return CancelResult(CancelOutcome.CANCELLED, before)
    with contextlib.suppress(ProcessLookupError):
        os.kill(before.pid, signal.SIGTERM)
    return CancelResult(CancelOutcome.SIGNALLED, before)
