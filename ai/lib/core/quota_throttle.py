"""A machine-wide backoff shared by every agent that hits a 429.

Two review pipelines on one host used to each keep a process-local ladder
(``threading.Lock`` plus ``time.monotonic()``). After a shared quota 429 they
backed off independently and re-collided on the way up, because a monotonic
clock is not comparable across processes and a lock in one address space is
invisible to another.

The wait is a wall-clock timestamp in a JSON file, not a held flock. A crash
mid-backoff is therefore not a stale lock: the next process reads ``resume_at``
and waits out whatever is left. A dead pid in the file is diagnostic only, the
same way ``job_slots.holders()`` ignores one.

The file is rewritten in place under ``LOCK_EX``. ``os.replace`` would swap
the name onto a new inode, and two writers could then each hold ``LOCK_EX``
on a different inode — the flock follows the inode, not the path.
"""

# doc-group: platform

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import threading
import time
from pathlib import Path

import core.log
from core.workbench_paths import state_dir

LOCK_NAME = "quota-throttle.lock"

# Shared by every instance in this process. fcntl.flock is process-granularity,
# so two threads would both hold LOCK_EX unless they take this first.
_THREAD_LOCK = threading.Lock()


# ceiling: one machine-wide lock file, upgrade to per-credential files
# if two API keys or two providers share this host and a 429 on one
# should not stall the other.
def throttle_path() -> Path:
    """Where the shared backoff lives. Resolved per call, not at import.

    Tests relocate the state root after import; an import-time constant would
    pin the live directory the way ``job_slots.slots_dir`` used to.
    """
    return state_dir() / LOCK_NAME


class QuotaThrottle:
    """Throttle shared across pipeline agents, including across processes.

    When any agent hits a 429, every pending agent waits before launching.
    ``report_exhausted`` is the only writer; ``wait_if_needed`` is an unlocked
    best-effort read so a torn file fails open rather than stalling a review.
    """

    def __init__(self, backoff: float = 30.0, max_backoff: float = 120.0):
        self._resume_at: float = 0.0
        self._backoff = backoff
        self._initial = backoff
        self._max_backoff = max_backoff

    def report_exhausted(self, model: str) -> float:
        """Record a 429 and return how long the caller should sleep.

        The caller sleeps — this method never holds a lock across a wait.
        Threading lock then flock, always, and neither is held across a sleep.
        """
        with _THREAD_LOCK:
            wait = self._write_exhausted(model)
        core.log.warn(f"Quota exhausted on {model} — backing off {wait:.0f}s")
        return wait

    def wait_if_needed(self) -> None:
        """Sleep until the shared resume time, without taking a blocking flock.

        A blocked flock here would stall a whole review on a writer that is
        only rewriting a few hundred bytes. A torn read is fail-open: a missed
        wait re-collides once, which is cheaper than serialising launches.
        """
        resume_at = max(_disk_resume_at(), self._resume_at)
        remaining = resume_at - time.time()
        if remaining > self._max_backoff:
            remaining = self._max_backoff
        if remaining > 0:
            core.log.info(f"Throttle: waiting {remaining:.0f}s for quota to recover")
            time.sleep(remaining)

    def _write_exhausted(self, model: str) -> float:
        """Locked read-modify-write of the on-disk ladder, or in-memory fallback."""
        now = time.time()
        path = throttle_path()
        try:
            handle = open(path, "a+")
        except OSError:
            return self._advance(self._backoff, now)

        try:
            fcntl.flock(handle, fcntl.LOCK_EX)
        except OSError:
            with contextlib.suppress(OSError):
                handle.close()
            return self._advance(self._backoff, now)

        try:
            backoff = self._backoff_from_state(_read_handle(handle), now)
            wait = self._advance(backoff, now)
            _rewrite_handle(handle, {
                "resume_at": self._resume_at,
                "backoff": self._backoff,
                "updated_at": now,
                "pid": os.getpid(),
                "model": model,
            })
            return wait
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(handle, fcntl.LOCK_UN)
            with contextlib.suppress(OSError):
                handle.close()

    def _backoff_from_state(self, state: dict, now: float) -> float:
        """The ladder step to wait this time, from disk, or ``self._backoff``.

        Idle reset keys on ``updated_at`` versus ``max_backoff``, not on
        ``now >= resume_at``. A retry that 429s again as ``resume_at`` expires
        would otherwise restart at 30s forever.
        """
        updated_at = state.get("updated_at")
        if (
            isinstance(updated_at, (int, float))
            and now - float(updated_at) >= self._max_backoff
        ):
            return self._initial
        disk = state.get("backoff")
        if isinstance(disk, (int, float)) and disk > 0:
            return float(disk)
        return self._backoff

    def _advance(self, backoff: float, now: float) -> float:
        """Apply one step of the ladder to in-memory state and return the wait."""
        wait = backoff
        self._resume_at = now + wait
        self._backoff = min(backoff * 2, self._max_backoff)
        return wait


def _read_handle(handle) -> dict:
    """Parse the file under the flock we already hold. Failure is empty state."""
    try:
        handle.seek(0)
        raw = handle.read()
        if not raw:
            return {}
        data = json.loads(raw)
    except (OSError, ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _rewrite_handle(handle, record: dict) -> None:
    """Overwrite the file in place so the flock stays on this inode."""
    try:
        handle.seek(0)
        handle.truncate()
        json.dump(record, handle)
        handle.flush()
    except OSError:
        pass


def _disk_resume_at() -> float:
    """Unlocked best-effort read of ``resume_at``. Parse or I/O errors are 0."""
    try:
        data = json.loads(throttle_path().read_text())
    except (OSError, ValueError, TypeError):
        return 0.0
    if not isinstance(data, dict):
        return 0.0
    value = data.get("resume_at")
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0
