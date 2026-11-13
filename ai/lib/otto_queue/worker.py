"""The worker: starts queued jobs as slots free up, and exits once the queue has been idle a while.

Started by its supervisor (`otto_queue.supervisor`): by `submit`, and at boot
or login so jobs a crash left queued drain. One runs per machine: a second finds
`queue/worker.lock` held (`otto_queue.lease.take_worker`) and leaves.

Every `poll_s`:

1. orphans are reaped: a job whose runner died goes back to the queue;
2. runners that exited are checked. One that left its job `queued` with its
   attempt count unchanged never started it (a missing interpreter, a refused
   scope), and that job is failed rather than started again every poll;
3. for each class, the oldest job whose retry time has come is started while it
   is the class's oldest waiting entry (`first_waiting(jobs=True)`: an older
   ticket goes first) and `otto_queue.slots.try_admit` grants a slot. The runner
   (`otto_queue.job`) inherits the slot's descriptor and the worker closes its
   own copy, so the slot is held exactly as long as the runner lives;
4. the ledger is compacted when it has not been for `COMPACT_EVERY_S`.

The worker leaves once no job has been queued (retry pending included) or
running for `idle_s`. It releases its lock first and then looks once more: a job
submitted between its last look and the release was handed to it by a `submit`
that saw its lock held, and would otherwise wait for the next wake.

Runners run in their own sessions (macOS) or scopes (Linux), so a worker that is
stopped or crashes leaves them running, and each records its own result.
"""

# doc-group: queue

from __future__ import annotations

import os
import shlex
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import core.log
import otto_queue.lease
import otto_queue.ledger
import otto_queue.slots
import otto_queue.supervisor
from otto_queue.model import Entry, EntryClass, EntryKind, EntryState

# Long enough to carry one process across a burst of submits (the next step of a
# batch arrives the moment one ends), short enough not to sit resident.
IDLE_S = 120.0

POLL_S = 1.0
COMPACT_EVERY_S = 3600.0

# How long a finished entry stays; see `Ledger.compact` for what is kept regardless.
RETENTION_S = 14 * 86400.0


@dataclass(frozen=True)
class Launcher:
    """How a job's runner is started: `job_exec` plus the entry id, inside a scope when `slice_name` is set."""

    job_exec: tuple[str, ...]
    slice_name: str | None = None

    def argv(self, entry: Entry, slot_fd: int) -> list[str]:
        inner = [*self.job_exec, str(entry.id), "--slot-fd", str(slot_fd)]
        if self.slice_name is None:
            return inner
        unit = f"otto-queue-{entry.id}-{entry.attempts + 1}"
        return otto_queue.supervisor.scope_argv(inner, slice_name=self.slice_name, unit=unit)


@dataclass(frozen=True)
class _Runner:
    attempts: int
    proc: subprocess.Popen


def run_worker(policy: otto_queue.slots.Policy, *, launcher: Launcher, root: Path | None = None,
               idle_s: float = IDLE_S, poll_s: float = POLL_S,
               notify: Callable[[str], None] = core.log.info) -> int:
    """Run queued jobs until the queue has been idle for *idle_s*; see the module docstring."""
    ledger = otto_queue.ledger.Ledger.open(root)
    while True:
        lock = otto_queue.lease.take_worker(ledger.root)
        if lock is None:
            notify("otto-queue: another worker is running")
            return 0
        try:
            _serve(ledger, policy, launcher, idle_s, poll_s, notify)
        finally:
            lock.release()
        if ledger.live_jobs() == 0:
            return 0


def _serve(ledger: otto_queue.ledger.Ledger, policy: otto_queue.slots.Policy, launcher: Launcher,
           idle_s: float, poll_s: float, notify: Callable[[str], None]) -> None:
    runners: dict[int, _Runner] = {}
    idle_since = time.monotonic()
    while True:
        ledger.reap_orphans()
        _reap_runners(ledger, runners, notify)
        for entry_class in EntryClass:
            _start_ready(ledger, policy, launcher, runners, entry_class, notify)
        _maybe_compact(ledger, notify)
        # A runner whose row is already terminal is still polled until reaped.
        if runners or ledger.live_jobs():
            idle_since = time.monotonic()
        elif time.monotonic() - idle_since >= idle_s:
            return
        time.sleep(poll_s)


def _start_ready(ledger: otto_queue.ledger.Ledger, policy: otto_queue.slots.Policy, launcher: Launcher,
                 runners: dict[int, _Runner], entry_class: EntryClass,
                 notify: Callable[[str], None]) -> None:
    while _start_one(ledger, policy, launcher, runners, entry_class, notify):
        pass


def _start_one(ledger: otto_queue.ledger.Ledger, policy: otto_queue.slots.Policy, launcher: Launcher,
               runners: dict[int, _Runner], entry_class: EntryClass,
               notify: Callable[[str], None]) -> bool:
    """Start the next ready job of *entry_class*; whether one was taken off the queue."""
    entry = ledger.next_job(entry_class, time.time())
    if entry is None or entry.id in runners:
        return False
    if ledger.first_waiting(entry_class, jobs=True) != entry.id:
        return False
    admission = otto_queue.slots.try_admit(ledger.root, policy, entry_class)
    if admission.slot is None:
        return False
    try:
        proc = subprocess.Popen(launcher.argv(entry, admission.slot.fd), pass_fds=(admission.slot.fd,),
                                stdin=subprocess.DEVNULL, start_new_session=True)
    except Exception as exc:
        ledger.fail(entry.id)
        notify(f"otto-queue: job {entry.id} could not start: {exc}")
        return True
    finally:
        admission.slot.release()
    runners[entry.id] = _Runner(entry.attempts, proc)
    notify(f"otto-queue: started job {entry.id}: {shlex.join(entry.argv)}")
    return True


def _reap_runners(ledger: otto_queue.ledger.Ledger, runners: dict[int, _Runner],
                  notify: Callable[[str], None]) -> None:
    for entry_id, runner in list(runners.items()):
        code = runner.proc.poll()
        if code is None:
            continue
        del runners[entry_id]
        _check_started(ledger, entry_id, runner, code, notify)


def _check_started(ledger: otto_queue.ledger.Ledger, entry_id: int, runner: _Runner, code: int,
                   notify: Callable[[str], None]) -> None:
    entry = ledger.get(entry_id)
    if entry is None or entry.state is not EntryState.QUEUED or entry.attempts != runner.attempts:
        return
    # job-exec exits 75 when the ledger is busy before it claims; try again next poll.
    if code == 0 or code == os.EX_TEMPFAIL or entry.kind is not EntryKind.JOB:
        return
    ledger.fail(entry_id)
    notify(f"otto-queue: job {entry_id}'s runner exited {code} without starting it; marked failed")


def _maybe_compact(ledger: otto_queue.ledger.Ledger, notify: Callable[[str], None]) -> None:
    now = time.time()
    last = ledger.compacted_at()
    if last is not None and now - last < COMPACT_EVERY_S:
        return
    pruned = ledger.compact(retention_s=RETENTION_S, now=now)
    if pruned:
        notify(f"otto-queue: pruned {len(pruned)} finished entries")
