"""Timed exclusive `flock`, shared by fetch and board single-flight.

A blocking `fcntl.flock` has no timeout, and `signal.alarm` would break a
caller running off the main thread. Every waiter here polls `LOCK_EX|LOCK_NB`
until the deadline, then proceeds unlocked so the work still happens — a
timeout is not a refusal to fetch or to build the board.
"""

# doc-group: platform

from __future__ import annotations

import contextlib
import fcntl
import os
import time
from collections.abc import Iterator
from pathlib import Path


def try_lock(fd: int) -> bool:
    """One non-blocking `LOCK_EX` attempt on *fd*."""
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def acquire(fd: int, wait_s: float, poll_s: float = 0.05) -> bool:
    """Take `LOCK_EX` on *fd* within *wait_s*, polling rather than blocking."""
    deadline = time.monotonic() + wait_s
    while not try_lock(fd):
        if time.monotonic() >= deadline:
            return False
        time.sleep(poll_s)
    return True


def _open_lock(path: Path) -> int | None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        return os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o644)
    except OSError:
        return None


@contextlib.contextmanager
def held(path: Path | str, *, wait_s: float, poll_s: float = 0.05) -> Iterator[bool]:
    """Hold an exclusive flock on *path*; yield False if open or the wait fails.

    The caller still proceeds when this yields False — unlocked — so a missing
    parent or a wait that ran out does not skip the work the lock was
    serialising.
    """
    fd = _open_lock(Path(path))
    if fd is None:
        yield False
        return
    locked = False
    try:
        locked = acquire(fd, wait_s, poll_s)
        yield locked
    finally:
        if locked:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
