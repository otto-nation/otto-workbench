"""Slots: how many entries of each class run at once on this machine, held as flocks.

Each class has `policy.limit(class)` slot files under `queue/slots/`. An
admitted entry holds one with a non-blocking `flock` for as long as it runs,
and the kernel drops it when the holder exits, SIGKILL included, so a crashed
ticket cannot strand capacity.

Whether there is room is not decided here. The caller passes a `Policy`
(`batch.admission.QueuePolicy`), which owns the limits and the host check;
this module only counts and holds. `try_admit` runs under `admit.lock`, taken
through `core.flock_wait`, so sampling the host and claiming a slot are one
step machine-wide. The lock serialises samples but does not account for
memory a just-admitted entry has not allocated yet: the next waiter can
still see the same free gigabyte. If that lock cannot be had within
`ADMIT_LOCK_WAIT_S`, the claim goes ahead without it: the slot flocks still
bound the count, and only the host check can race.
"""

# doc-group: queue

from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import core.flock_wait
import otto_queue.paths
from otto_queue.model import EntryClass

# The critical section is a few flock probes and one host sample, so a waiter
# kept out this long is behind a wedged holder, not a busy one.
ADMIT_LOCK_WAIT_S = 5.0


class Decision(Protocol):
    """An admission answer; `batch.admission.Verdict` is one."""

    @property
    def admit(self) -> bool: ...

    @property
    def reason(self) -> str: ...


class Policy(Protocol):
    """Who decides whether there is room: `batch.admission.QueuePolicy` in production."""

    def limit(self, entry_class: EntryClass) -> int: ...

    def verdict(self, entry_class: EntryClass, running: int) -> Decision: ...


@dataclass(frozen=True)
class Slot:
    """One held slot. `release` once the entry it admitted has finished."""

    entry_class: EntryClass
    index: int
    fd: int

    def release(self) -> None:
        with contextlib.suppress(OSError):
            os.close(self.fd)


@dataclass(frozen=True)
class Admission:
    """The slot when admitted, else None; `reason` says why either way."""

    slot: Slot | None
    reason: str

    @property
    def admitted(self) -> bool:
        return self.slot is not None


def _probe(root: Path, entry_class: EntryClass, index: int) -> int | None:
    """An fd holding slot *index*, or None when it is taken or cannot be opened."""
    path = otto_queue.paths.slot_path(root, entry_class, index)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o644)
    except OSError:
        return None
    try:
        locked = core.flock_wait.try_lock(fd)
    except OSError:
        locked = False
    if not locked:
        os.close(fd)
        return None
    return fd


def try_admit(root: Path, policy: Policy, entry_class: EntryClass) -> Admission:
    """Claim a free *entry_class* slot when *policy* says there is room; never blocks on a slot."""
    # ceiling: admitted-but-young heavy entries are not subtracted from the sample;
    # once two heavy entries are seen admitted on the same headroom, subtract their
    # QUEUE_HEAVY_ESTIMATE.
    with core.flock_wait.held(otto_queue.paths.admit_lock_path(root), wait_s=ADMIT_LOCK_WAIT_S):
        return _admit_locked(root, policy, entry_class)


def _admit_locked(root: Path, policy: Policy, entry_class: EntryClass) -> Admission:
    limit = max(1, policy.limit(entry_class))
    free: list[Slot] = []
    running = 0
    for index in range(limit):
        fd = _probe(root, entry_class, index)
        if fd is None:
            running += 1
        elif free:
            os.close(fd)
        else:
            free.append(Slot(entry_class, index, fd))
    if not free:
        return Admission(None, f"all {limit} {entry_class.value} slots are taken")
    try:
        verdict = policy.verdict(entry_class, running)
    except BaseException:
        free[0].release()
        raise
    if not verdict.admit:
        free[0].release()
        return Admission(None, verdict.reason)
    return Admission(free[0], verdict.reason)
