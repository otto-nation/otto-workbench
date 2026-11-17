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
   status. Reaching `wait_s` while the holder is still live returns 75 without
   cancelling it. `force` skips the match. A joined entry whose holder died is
   not a result: the ticket resolves again and, finding nothing live, runs the
   work itself.
2. **Wait.** Polls start at `POLL_S` for `FAST_WAIT_S`, then back off
   geometrically to `POLL_CAP_S` (jittered ±20% so waiters do not lockstep;
   the interval resets when the wait reason or joined state changes). A sleep
   never runs past `wait_s`. A waiter that is not first only reads the head of
   the queue each poll and reaps orphans at most every `REAP_EVERY_S`; the first
   waiter reaps, then asks `otto_queue.slots.try_admit`. The first wait prints
   a short block naming the entry and how to check or cancel it; later waits
   print only when the kind of reason changes, at most once per 30s, with no
   changing numbers. Admission after a wait prints how long it took.
   `LedgerBusy` after the ledger's own retries is printed once (`ledger busy;
   still waiting`) and is not a crash: the ticket keeps waiting until `wait_s`,
   then returns 75. `cancel`, Ctrl-C, or a `SystemExit` from a stop handler
   while waiting ends the ticket with that exit status (130 when none is
   given). Reaching `wait_s` with no slot records `tempfail` and returns 75
   (EX_TEMPFAIL), so a caller can tell "never got room" from a failure.

`LABEL_ENV`, `WAIT_ENV`, `SUBJECT_ENV` and `ACTION_ENV` are the names
`cli.queue_gate` and `pr batch` use to pass a display label, wait bound, and
structured subject/action into a ticket they construct. `run_ticket` does not
read them: the `otto-queue run` flags stay the only input to the CLI.
`HC_TERMINAL_ID` is different: `TicketRequest.entry` copies it onto the row as
`terminal` when it is set, so a ticket started from a herdr pane records which.
3. **Run.** The row moves to `running`, and the command runs through
   `core.signal_relay.run_child(foreground=True)`: stdio inherited, the
   terminal's Ctrl-C and hangup reaching it directly, SIGTERM (`otto-queue
   cancel`) forwarded. Its exit status is recorded (75 as `tempfail`, never a
   failure) and returned. Recording retries inside the ledger; if it still
   cannot be written, the command's exit status is returned anyway, never
   replaced by an infrastructure error. A crash while recording is logged and
   the original exception is re-raised.

The command sees `OTTO_QUEUE_ENTRY`, its entry id. A ticket started under that
variable runs its command directly: a nested heavy ticket would otherwise wait
for a slot its own parent holds. When the ledger stays busy past the open
deadline, or stays unreachable for the whole wait, the ticket returns 75
without running. When the ledger cannot be opened at all, the command runs
unqueued with a warning. The queue's absence degrades to today's behaviour,
never to work not done.

ceiling: the ticket process, not its command, holds the lease and the slot, so
a ticket killed with SIGKILL while its command runs leaves the command running
untracked and its row `failed`. Upgrade trigger: if a command is seen outliving
its killed ticket, have the child inherit the lease and slot descriptors.
"""

# doc-group: queue

from __future__ import annotations

import contextlib
import os
import random
import signal
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import core.log
import core.proc
import core.signal_relay
import otto_queue.job
import otto_queue.lease
import otto_queue.ledger
import otto_queue.paths
import otto_queue.slots
import otto_queue.wait_report
from otto_queue.model import (  # noqa: F401  env names are re-exported for callers
    ACTION_ENV, ENTRY_ENV, LABEL_ENV, SUBJECT_ENV, WAIT_ENV, Entry, EntryClass, EntryKind,
    EntryState, NewEntry)

# How long a ticket waits for a slot before ending as `tempfail`. A review takes
# about ten minutes, so an hour is several heavy entries deep: long enough that
# only a ticket stuck behind a wedged entry times out.
DEFAULT_WAIT_S = 3600.0

# Between admission attempts, and between looks at a joined entry. Stays at
# POLL_S for FAST_WAIT_S, then doubles each POLL_S of extra wait up to POLL_CAP_S.
POLL_S = 0.5
FAST_WAIT_S = 5.0
POLL_CAP_S = 3.0
REAP_EVERY_S = 5.0

# Printed once when a wait poll hits LedgerBusy after the ledger's own retries.
_BUSY_NOTICE = "otto-queue: ledger busy; still waiting"

TERMINAL_ENV = "HC_TERMINAL_ID"


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
    reuse: bool = True
    wait_s: float = DEFAULT_WAIT_S
    label: str | None = None
    subject: str | None = None
    action: str | None = None

    def entry(self) -> NewEntry:
        return NewEntry(argv=self.argv, cwd=self.cwd, entry_class=self.entry_class,
                        idem_key=self.idem_key, label=self.label,
                        subject=self.subject, action=self.action,
                        terminal=os.environ.get(TERMINAL_ENV) or None)


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
        res = ledger.resolve(request.entry(), force=request.force, pid=os.getpid(),
                             reuse=request.reuse)
        if res.outcome is otto_queue.ledger.Outcome.NEW:
            return run_claimed(ledger, res, request, policy, poll_s=poll_s, notify=notify)
        if res.outcome is otto_queue.ledger.Outcome.REUSED:
            notify(f"otto-queue: entry {res.entry.id} already did this; not running it again")
            return TicketResult(res.entry.exit_status or 0, TicketOutcome.REUSED, res.entry.id)
        joined = _join(ledger, res.entry, request, policy, poll_s, notify)
        if joined is not None:
            return joined


def _join(ledger: otto_queue.ledger.Ledger, entry: Entry, request: TicketRequest,
          policy: otto_queue.slots.Policy, poll_s: float,
          notify: Callable[[str], None]) -> TicketResult | None:
    """Wait for *entry* to end and return its status; None when it ended with none (orphaned).

    A queued job nobody will start — no worker is alive — is claimed and run
    here instead, so the wait cannot outlast every worker.
    """
    entry_id = entry.id
    reporter = otto_queue.wait_report.WaitReporter(
        entry_id, entry.entry_class.value, notify)
    deadline = time.monotonic() + request.wait_s
    origin = time.monotonic()
    last_state = entry.state
    rng = random.Random()
    busy_said = False
    while entry is not None and entry.state.live:
        try:
            ran = _run_unstarted(ledger, entry, policy, poll_s, notify, deadline)
        except otto_queue.ledger.LedgerBusy:
            ran = None
            busy_said = _note_busy(notify, busy_said)
        if ran is not None:
            return ran
        if time.monotonic() >= deadline:
            notify(f"otto-queue: entry {entry_id} did not finish in {request.wait_s:g}s")
            return TicketResult(os.EX_TEMPFAIL, TicketOutcome.TIMED_OUT, entry_id)
        reporter.waiting(f"behind entry {entry_id}, which queued first")
        state = None if entry is None else entry.state
        if state != last_state:
            origin = time.monotonic()
            last_state = state
        _poll_sleep(poll_delay(time.monotonic() - origin, rng, poll_s), deadline)
        try:
            ledger.reap_orphans()
            entry = ledger.get(entry_id)
        except otto_queue.ledger.LedgerBusy:
            busy_said = _note_busy(notify, busy_said)
    if entry is None or entry.exit_status is None:
        notify(f"otto-queue: entry {entry_id} ended without a result; queuing again")
        return None
    outcome = TicketOutcome.CANCELLED if entry.state is EntryState.CANCELLED else TicketOutcome.JOINED
    return TicketResult(entry.exit_status, outcome, entry_id)


def _run_unstarted(ledger: otto_queue.ledger.Ledger, entry: Entry,
                   policy: otto_queue.slots.Policy, poll_s: float,
                   notify: Callable[[str], None], deadline: float) -> TicketResult | None:
    """Run *entry* here when it is a queued job and no worker is alive to start it; else None.

    The job stays a job (`adopt_job`), so a ticket killed before it starts leaves
    it queued. Anything but a finished run is None: the caller re-reads the row.
    """
    if entry.kind is not EntryKind.JOB or entry.state is not EntryState.QUEUED:
        return None
    if entry.not_before is not None and entry.not_before > time.time():
        return None
    if otto_queue.lease.worker_alive(ledger.root):
        return None
    log = otto_queue.paths.log_path(ledger.root, entry.id)
    notify(f"otto-queue: no worker is running; running entry {entry.id} here (log: {log})")
    adoption = adopt_job(ledger, entry, policy, wait_s=max(0.0, deadline - time.monotonic()),
                         poll_s=poll_s, notify=notify)
    if adoption.outcome is AdoptOutcome.RAN:
        return TicketResult(adoption.exit_status or 0, TicketOutcome.RAN, entry.id)
    return None


class AdoptOutcome(StrEnum):
    RAN = "ran"
    RETRY = "retry"
    TAKEN = "taken"
    TIMED_OUT = "timed_out"
    LEFT_QUEUE = "left_queue"


@dataclass(frozen=True)
class Adoption:
    """How `adopt_job` ended; *exit_status* is the command's, set when it ran here."""

    outcome: AdoptOutcome
    exit_status: int | None = None


def adopt_job(ledger: otto_queue.ledger.Ledger, entry: Entry, policy: otto_queue.slots.Policy,
              *, wait_s: float, poll_s: float = POLL_S,
              notify: Callable[[str], None] = core.log.info,
              clock: Callable[[], float] = time.time) -> Adoption:
    """Run queued job *entry* in this process without making it a ticket.

    Waits for a slot holding no lease, so a waiter killed meanwhile leaves the
    row a queued job and nothing reaps it. Once admitted it takes the job's own
    lease and runs the command in this process group, output to the job's log.
    TIMED_OUT leaves the row untouched; LEFT_QUEUE means it stopped being queued
    while waiting; TAKEN means another runner holds the lease.
    """
    request = TicketRequest(argv=entry.argv, cwd=entry.cwd, entry_class=entry.entry_class,
                            wait_s=wait_s)
    wait = _wait_for_slot(ledger, entry.id, request, policy, poll_s, notify)
    try:
        if wait.end is _WaitEnd.TIMED_OUT:
            return Adoption(AdoptOutcome.TIMED_OUT)
        if wait.end is _WaitEnd.CANCELLED:
            return Adoption(AdoptOutcome.LEFT_QUEUE)
        result = otto_queue.job.run_leased(ledger, entry.id, clock, foreground=True)
    finally:
        _release_slot(wait.slot)
    if result.outcome is otto_queue.job.JobOutcome.NOT_CLAIMED:
        return Adoption(AdoptOutcome.TAKEN)
    if result.outcome is otto_queue.job.JobOutcome.RETRY:
        return Adoption(AdoptOutcome.RETRY, result.exit_status)
    return Adoption(AdoptOutcome.RAN, result.exit_status)


def _record_crash(ledger: otto_queue.ledger.Ledger, entry_id: int, exc: BaseException) -> None:
    # Terminal before the lease (and any slot) is released: a live row
    # whose lease is gone is an orphan, and reap_orphans marks it failed.
    # A LedgerError here must not replace *exc*: the original traceback is
    # the one the caller re-raises.
    try:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            ledger.cancel(entry_id)
            code = exc.code if isinstance(getattr(exc, "code", None), int) and exc.code else (
                core.proc.INTERRUPT_RETURNCODE)
            ledger.finish(entry_id, code)
            return
        ledger.fail(entry_id)
    except otto_queue.ledger.LedgerError:
        core.log.warn(f"otto-queue: could not record crash for entry {entry_id}")


def run_claimed(ledger: otto_queue.ledger.Ledger, res: otto_queue.ledger.Resolution,
                request: TicketRequest, policy: otto_queue.slots.Policy, *,
                poll_s: float = POLL_S, notify: Callable[[str], None] = core.log.info) -> TicketResult:
    """Wait for a slot and run *request* for the NEW entry *res* this process holds the lease of.

    Only `run_ticket` calls it, for a ticket it recorded.
    """
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


def _record_status(ledger: otto_queue.ledger.Ledger, entry_id: int,
                   code: int) -> EntryState | None:
    """Record *code*; on LedgerError keep the command's status rather than raising."""
    try:
        return ledger.finish(entry_id, code)
    except otto_queue.ledger.LedgerError:
        core.log.warn(f"otto-queue: could not record exit {code} for entry {entry_id}")
        return None


def poll_delay(waited_s: float, rng, poll_s: float = POLL_S) -> float:
    """Seconds to sleep after a wait that has already lasted *waited_s*.

    *poll_s* for the first `FAST_WAIT_S`, then doubles each *poll_s* of extra
    wait up to `POLL_CAP_S`. Each sleep is jittered ±20% and never above the cap.
    """
    if waited_s < FAST_WAIT_S:
        base = poll_s
    else:
        steps = int((waited_s - FAST_WAIT_S) / poll_s)
        # Bound the exponent: an unbounded one overflows float conversion after
        # about a thousand doublings of a long wait. 2**64 is far past the cap.
        base = min(poll_s * (2 ** min(steps, 64)), POLL_CAP_S)
    return min(base * rng.uniform(0.8, 1.2), POLL_CAP_S)


def _poll_sleep(delay: float, deadline: float) -> None:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return
    time.sleep(min(delay, remaining))


def _note_busy(notify: Callable[[str], None], already: bool) -> bool:
    if not already:
        notify(_BUSY_NOTICE)
    return True


def _release_slot(slot: otto_queue.slots.Slot | None) -> None:
    if slot is not None:
        slot.release()


def _run_after_wait(ledger: otto_queue.ledger.Ledger, entry_id: int,
                    request: TicketRequest, wait: _Wait,
                    notify: Callable[[str], None]) -> TicketResult:
    if wait.end is _WaitEnd.CANCELLED:
        return TicketResult(core.proc.INTERRUPT_RETURNCODE, TicketOutcome.CANCELLED, entry_id)
    if wait.end is _WaitEnd.TIMED_OUT:
        _record_status(ledger, entry_id, os.EX_TEMPFAIL)
        notify(f"otto-queue: entry {entry_id} found no room in {request.wait_s:g}s")
        return TicketResult(os.EX_TEMPFAIL, TicketOutcome.TIMED_OUT, entry_id)
    try:
        started = ledger.mark_running(entry_id, os.getpid())
    except otto_queue.ledger.LedgerBusy:
        notify(f"otto-queue: entry {entry_id} found no room in {request.wait_s:g}s")
        return TicketResult(os.EX_TEMPFAIL, TicketOutcome.TIMED_OUT, entry_id)
    code = _run(request, entry_id) if started else core.proc.INTERRUPT_RETURNCODE
    if not started:
        return TicketResult(code, TicketOutcome.CANCELLED, entry_id)
    state = _record_status(ledger, entry_id, code)
    outcome = TicketOutcome.CANCELLED if state is EntryState.CANCELLED else TicketOutcome.RAN
    return TicketResult(code, outcome, entry_id)


def _admit_and_run(ledger: otto_queue.ledger.Ledger, entry_id: int,
                  request: TicketRequest, policy: otto_queue.slots.Policy,
                  poll_s: float, notify: Callable[[str], None]) -> TicketResult:
    wait: _Wait | None = None
    try:
        wait = _wait_for_slot(ledger, entry_id, request, policy, poll_s, notify)
        return _run_after_wait(ledger, entry_id, request, wait, notify)
    except BaseException as exc:
        _record_crash(ledger, entry_id, exc)
        raise
    finally:
        _release_slot(wait.slot if wait is not None else None)


def _slot_once(ledger: otto_queue.ledger.Ledger, entry_id: int,
               entry_class: EntryClass, policy: otto_queue.slots.Policy,
               *, reap: bool) -> tuple[_Wait | None, str]:
    jobs = otto_queue.lease.worker_alive(ledger.root)
    entry, first = ledger.wait_poll(entry_id, entry_class, jobs=jobs)
    if entry is None or entry.state is not EntryState.QUEUED:
        return _Wait(_WaitEnd.CANCELLED), ""
    admission = _try_once(ledger, entry_id, entry_class, policy, first, reap=reap)
    if admission.slot is not None:
        return _Wait(_WaitEnd.ADMITTED, admission.slot), ""
    return None, admission.reason


def _wait_for_slot(ledger: otto_queue.ledger.Ledger, entry_id: int,
                 request: TicketRequest, policy: otto_queue.slots.Policy,
                 poll_s: float, notify: Callable[[str], None]) -> _Wait:
    deadline = time.monotonic() + request.wait_s
    origin = time.monotonic()
    said = ""
    rng = random.Random()
    last_reap = time.monotonic()
    busy_said = False
    reporter = otto_queue.wait_report.WaitReporter(
        entry_id, request.entry_class.value, notify)
    while True:
        now = time.monotonic()
        reap = now - last_reap >= REAP_EVERY_S
        try:
            wait, reason = _slot_once(
                ledger, entry_id, request.entry_class, policy, reap=reap)
        except otto_queue.ledger.LedgerBusy:
            wait, reason = None, said
            busy_said = _note_busy(notify, busy_said)
            reap = False
        last_reap = now if reap else last_reap
        if wait is not None and wait.end is _WaitEnd.ADMITTED:
            reporter.admitted()
        if wait is not None:
            return wait
        if reason != said:
            reporter.waiting(reason)
            said = reason
            origin = time.monotonic()
        if time.monotonic() >= deadline:
            return _Wait(_WaitEnd.TIMED_OUT)
        _poll_sleep(poll_delay(time.monotonic() - origin, rng, poll_s), deadline)


def _try_once(ledger: otto_queue.ledger.Ledger, entry_id: int, entry_class: EntryClass,
              policy: otto_queue.slots.Policy, first: int | None, *,
              reap: bool) -> otto_queue.slots.Admission:
    """Ask for a slot, unless an older entry of the class is still waiting.

    An older entry is a ticket, or a ready job while a worker is alive.
    A waiter that is not first skips `reap_orphans` unless *reap*.
    """
    if first is not None and first != entry_id and not reap:
        return otto_queue.slots.Admission(None, f"behind entry {first}, which queued first")
    ledger.reap_orphans()
    first = ledger.first_waiting(entry_class, jobs=otto_queue.lease.worker_alive(ledger.root))
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
