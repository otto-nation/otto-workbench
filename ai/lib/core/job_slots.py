"""A machine-wide pool of test-parallelism slots.

Sizing a test run from the load average cannot work, and the reason is not a
tuning problem. A one-minute average lags the load it reports, so two suites
started within a minute of each other both read an idle machine and both take
the full cap: 24 heavy processes on 18 cores, each run slower than if it had
taken half. Measured on this machine, the bats suite runs in 299s when it gets
12 jobs and 377s when a concurrent run has pushed it down to 7 — and that
7-job run was itself sized from load its own sibling had not yet produced.

A slot is *held*, not sampled. A run that started 200ms ago has already taken
its slots, so the next one sees what is left rather than what the kernel has
got round to averaging. That is the whole difference: the reading cannot lag
because there is no reading.

The pool is a fixed set of files under ``<state>/test-slots/``, one per slot,
each claimed with a non-blocking ``fcntl.flock``. The kernel drops every one
when the holder exits for any reason, including SIGKILL, so there is no stale
state to reap and a crashed run cannot strand capacity. Slot files are never
deleted; their presence says nothing about whether anything holds them, the
same way ``run_lock.py``'s records do not.

Nothing ever blocks on a slot. A run that finds the pool empty proceeds at
*floor* parallelism anyway, deliberately overshooting the pool rather than
waiting: a queue would make the third worktree's pre-push sit silent for the
length of two suites, which is how people learn ``--no-verify``. The floor is
small enough that the overshoot stays bounded — three concurrent suites on 18
cores take 12, 5 and 2 rather than 12, 12 and 12.

Distinct from ``run_lock.py`` (exclusive, one target, serialises ``pr`` runs)
and ``tree_lock.py`` (shared, one worktree, publishes a fact). This one is
counted, machine-wide, and hands out capacity.
"""

# doc-group: platform

from __future__ import annotations

import contextlib
import datetime
import fcntl
import json
import os
from pathlib import Path

from core.workbench_paths import state_dir

SLOTS_DIRNAME = "test-slots"
LOCK_ENV = "WORKBENCH_TEST_SLOTS"

# Cores the pool never hands out, so a machine saturated with test runs still
# has something left for the shell the developer is typing into.
RESERVED_CORES = 1


def slots_dir() -> Path:
    """Where the pool lives. Created by the first claim, not by this call."""
    return state_dir() / SLOTS_DIRNAME


def pool_size(cores: int) -> int:
    """How many slots the machine offers in total.

    Deliberately not capped at the per-run cap. The two limits answer different
    questions: the cap is where one suite stops getting faster (measured at 12
    on this repo), while the pool is what the machine has. Capping the pool at
    12 on an 18-core box would idle five cores whenever a second run was up,
    since the first run would be holding the entire pool.
    """
    return max(1, cores - RESERVED_CORES)


def _slot_path(index: int) -> Path:
    # Three digits, so the names still sort in numeric order on a machine with
    # more than 99 cores — pool_size is deliberately uncapped, and two digits
    # would file slot-100 ahead of slot-99 in holders()' sorted listing.
    return slots_dir() / f"slot-{index:03d}.lock"


def _record(handle, index: int, command: str) -> None:
    """Write who holds this slot, under the flock we already hold.

    Diagnostic only — the flock is what reserves capacity. A record that
    cannot be written must not fail a claim that succeeded, so every error
    here is swallowed.
    """
    try:
        handle.seek(0)
        handle.truncate()
        json.dump({
            "pid": os.getpid(),
            "slot": index,
            "command": command,
            "started": datetime.datetime.now().isoformat(timespec="seconds"),
        }, handle)
        handle.flush()
    except (OSError, ValueError):
        pass


def _take(index: int, command: str):
    """Claim one slot, or return None when it is held or unusable.

    Opened "a+" rather than "w" so a failed claim cannot destroy the current
    holder's record on the way past. Every failure answers None: a held slot
    and a filesystem that cannot flock both mean "no capacity from here", and
    a machine whose state dir is unwritable should still run its tests.
    """
    try:
        handle = open(_slot_path(index), "a+")
    except OSError:
        return None
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    _record(handle, index, command)
    return handle


def holders() -> list[dict]:
    """Best-effort read of the records in every slot file.

    Purely diagnostic, and deliberately says nothing about whether a slot is
    currently held: a record naming a long-dead pid is the normal resting
    state of a released slot. Malformed files are skipped rather than raising.
    """
    found = []
    directory = slots_dir()
    if not directory.is_dir():
        return found
    # Sorted, because the "first" entry of an unsorted directory listing is a
    # different entry on another machine. The glob is pinned to the current
    # filename width so a pool that has changed shape does not report the old
    # names alongside the new: nothing ever deletes a slot file, so a rename
    # leaves its predecessors behind as records of holders that cannot return.
    for path in sorted(directory.glob("slot-[0-9][0-9][0-9].lock")):
        try:
            record = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(record, dict):
            found.append(record)
    return found


@contextlib.contextmanager
def claim(want: int, floor: int, cores: int, command: str = ""):
    """Hold up to *want* slots for the body, yielding how many were granted.

    The grant is whatever is free at this instant, never less than *floor* and
    never more than *want*. Falling back to the floor rather than waiting is
    the deliberate overshoot described in the module docstring: it bounds how
    badly a saturated machine is oversubscribed without ever making a run
    queue behind another one.

    Already ours: a nested invocation — ``run-tests`` re-execing itself under
    ``with-tree-lock`` — finds the marker and passes through, yielding the
    grant its parent recorded rather than claiming a second time. Re-entering
    the pool there would have a run competing with slots it is already
    holding, and would report a smaller grant than the one it is running at.
    The marker cannot prove the flocks are ours, for the same reason
    ``run_lock.py``'s cannot: proving it would mean probing a lock we hold,
    which fails precisely because we hold it.
    """
    inherited = _inherited_grant()
    if inherited is not None:
        yield inherited
        return

    want = max(1, want)
    floor = max(1, min(floor, want))

    previous = os.environ.get(LOCK_ENV)
    try:
        slots_dir().mkdir(parents=True, exist_ok=True)
    except OSError:
        # No pool is not a reason to refuse to run tests; it is a reason to
        # size the way a single run would have. The marker is still set, and
        # has to be: a caller that re-execs itself under the claim wrapper
        # reads it to know the claim already happened, and yielding without it
        # sent run-tests into an unbounded re-exec loop with nothing on screen.
        try:
            os.environ[LOCK_ENV] = str(want)
            yield want
        finally:
            _restore_marker(previous)
        return

    held = _take_up_to(want, cores, command)
    try:
        granted = max(len(held), floor)
        os.environ[LOCK_ENV] = str(granted)
        yield granted
    finally:
        _restore_marker(previous)
        _release(held)


def _take_up_to(want: int, cores: int, command: str) -> list:
    """Claim free slots, lowest index first, until *want* are held or none is left."""
    held = []
    for index in range(pool_size(cores)):
        if len(held) >= want:
            break
        handle = _take(index, command)
        if handle is not None:
            held.append(handle)
    return held


def _release(held: list) -> None:
    """Drop every held slot. Errors are suppressed: a handle we cannot close is
    released by the kernel at exit regardless, and raising here would turn a
    finished run into a failed one."""
    for handle in held:
        with contextlib.suppress(OSError):
            fcntl.flock(handle, fcntl.LOCK_UN)
        with contextlib.suppress(OSError):
            handle.close()


def _restore_marker(previous: str | None) -> None:
    """Put LOCK_ENV back the way we found it."""
    if previous is None:
        os.environ.pop(LOCK_ENV, None)
        return
    os.environ[LOCK_ENV] = previous


def _inherited_grant() -> int | None:
    """The grant an enclosing claim recorded, or None when there is none.

    A value that is not a positive integer is treated as no marker at all: it
    was exported by hand or left by something that is not us, and honouring it
    would size a run from a string nobody wrote deliberately.
    """
    raw = os.environ.get(LOCK_ENV)
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None
