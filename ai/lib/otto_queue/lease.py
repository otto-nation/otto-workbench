"""An entry's liveness: an `flock` on `queue/leases/<id>.lock`, held by the entry's process.

The kernel drops a flock when its holder exits for any reason, SIGKILL
included, so a live entry whose lease can be taken is orphaned. There are no
heartbeats or wall clocks to go stale. A ticket holds its lease from the
moment it is accepted, waiting included; a job holds one only while it runs.

A lease is taken inside the transaction that inserts its row, so no reader
ever sees a live ticket whose lease is not yet held. A missing lease file
therefore reads as orphaned. Anything else that stops the probe (permissions,
a filesystem without flock) reads as held, because reaping on uncertainty
would fail work that is still running.

The worker holds `queue/worker.lock` the same way for its whole life
(`take_worker`); `worker_alive` is how `submit` and tickets know one is
running.
"""

# doc-group: queue

from __future__ import annotations

import contextlib
import fcntl
import os
from dataclasses import dataclass
from pathlib import Path

import core.flock_wait
import otto_queue.paths


class LeaseHeld(RuntimeError):
    """The lease is already held: only possible if two rows claimed one id."""


@dataclass(frozen=True)
class Lease:
    """A held lease. `release` once the entry's row is terminal."""

    path: Path
    fd: int

    def release(self) -> None:
        """Unlink, then drop. Unlinked while still held, so a probe never takes a half-gone lease."""
        with contextlib.suppress(OSError):
            self.path.unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            os.close(self.fd)


def take(root: Path, entry_id: int) -> Lease:
    """Create and hold *entry_id*'s lease; `LeaseHeld` if another descriptor has it."""
    path = otto_queue.paths.lease_path(root, entry_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o644)
    if not core.flock_wait.try_lock(fd):
        os.close(fd)
        raise LeaseHeld(f"{path} is already held")
    return Lease(path, fd)


def orphaned(root: Path, entry_id: int) -> bool:
    """Whether nothing holds *entry_id*'s lease. Missing reads as orphaned; unreadable as held."""
    try:
        fd = os.open(otto_queue.paths.lease_path(root, entry_id), os.O_RDWR | os.O_CLOEXEC)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    try:
        return core.flock_wait.try_lock(fd)
    except OSError:
        return False
    finally:
        os.close(fd)


def discard(root: Path, entry_id: int) -> None:
    """Remove an orphaned ticket's lease file; ticket ids are never restarted."""
    with contextlib.suppress(OSError):
        otto_queue.paths.lease_path(root, entry_id).unlink(missing_ok=True)


# A probe (`worker_alive`) holds the worker lock for an instant, so a starting
# worker waits this long for it instead of mistaking the probe for a rival.
WORKER_TAKE_WAIT_S = 1.0


@dataclass(frozen=True)
class WorkerLock:
    """A held worker lock. `release` closes the fd and never unlinks `worker.lock`.

    Call `release` exactly once: a second close may close a reused fd number.
    """

    fd: int

    def release(self) -> None:
        """Drop the flock. The file stays so a waiter and a fresh take share one inode.

        Call exactly once: a second close may close a reused fd number.
        """
        with contextlib.suppress(OSError):
            os.close(self.fd)


def take_worker(root: Path) -> WorkerLock | None:
    """Hold `queue/worker.lock`; None when another worker holds it past `WORKER_TAKE_WAIT_S`."""
    path = otto_queue.paths.worker_lock_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o644)
    if core.flock_wait.acquire(fd, WORKER_TAKE_WAIT_S):
        return WorkerLock(fd)
    os.close(fd)
    return None


def worker_alive(root: Path) -> bool:
    """Whether a worker holds `queue/worker.lock` now. Missing or unreadable reads as no worker.

    The safe direction: a ticket that wrongly sees no worker only stops waiting
    behind jobs, while one that wrongly saw a worker could wait behind a job
    nobody will start.
    """
    try:
        fd = os.open(otto_queue.paths.worker_lock_path(root), os.O_RDWR | os.O_CLOEXEC)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    except OSError:
        return False
    else:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)
