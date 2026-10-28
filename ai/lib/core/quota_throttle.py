"""A machine-wide backoff shared by every agent that hits a 429 on one provider.

Two review pipelines on one host used to each keep a process-local ladder
(``threading.Lock`` plus ``time.monotonic()``). After a shared quota 429 they
backed off independently and re-collided on the way up, because a monotonic
clock is not comparable across processes and a lock in one address space is
invisible to another.

A 429 is a fact about one provider's quota, so each provider has a ladder and a
file of its own: a 429 from xai holds back the agents calling xai, not the ones
calling Claude. Agents that name no provider share the default's, which is the
file this module always had.

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
import re
import threading
import time
from pathlib import Path

import core.log
from core.workbench_paths import state_dir

LOCK_NAME = "quota-throttle.lock"

# Shared by every instance in this process. fcntl.flock is process-granularity,
# so two threads would both hold LOCK_EX unless they take this first.
_THREAD_LOCK = threading.Lock()

# What a provider id may not carry into a file name. Provider ids come from a
# reference or Pi's own settings, so this is belt and braces, not a parser.
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


# ceiling: one lock file per provider, upgrade to per-credential files
# if two API keys for one provider share this host and a 429 on one
# should not stall the other. A bare id and the same provider written out are
# separate keys too, so their 429s are not shared until a caller resolves the
# default provider's name.
def throttle_path(provider: str | None = None) -> Path:
    """Where *provider*'s shared backoff lives. Resolved per call, not at import.

    ``None`` is the file for agents that name no provider. Tests relocate the
    state root after import; an import-time constant would pin the live
    directory the way ``job_slots.slots_dir`` used to.
    """
    if not provider:
        return state_dir() / LOCK_NAME
    stem, suffix = LOCK_NAME.rsplit(".", 1)
    return state_dir() / f"{stem}-{_UNSAFE.sub('_', provider)}.{suffix}"


class QuotaThrottle:
    """Throttle shared across pipeline agents, including across processes.

    When any agent hits a 429, every pending agent calling the same provider
    waits before launching. ``report_exhausted`` is the only writer;
    ``wait_if_needed`` is an unlocked best-effort read so a torn file fails open
    rather than stalling a review. Both take the provider the agent calls, with
    ``None`` for one that names none.
    """

    def __init__(self, backoff: float = 30.0, max_backoff: float = 120.0):
        self._resume_at: dict[str | None, float] = {}
        self._backoff: dict[str | None, float] = {}
        self._initial = backoff
        self._max_backoff = max_backoff

    def resume_at(self, provider: str | None = None) -> float:
        """The epoch this process last set *provider*'s resume time to, 0 if never."""
        return self._resume_at.get(provider or None, 0.0)

    def report_exhausted(self, model: str, provider: str | None = None) -> float:
        """Record a 429 and return how long the caller should sleep.

        The caller sleeps — this method never holds a lock across a wait.
        Threading lock then flock, always, and neither is held across a sleep.
        """
        provider = provider or None
        with _THREAD_LOCK:
            wait = self._write_exhausted(model, provider)
        where = f" ({provider})" if provider else ""
        core.log.warn(f"Quota exhausted on {model}{where} — backing off {wait:.0f}s")
        return wait

    def wait_if_needed(self, provider: str | None = None) -> None:
        """Sleep until *provider*'s shared resume time, without taking a blocking flock.

        A blocked flock here would stall a whole review on a writer that is
        only rewriting a few hundred bytes. A torn read is fail-open: a missed
        wait re-collides once, which is cheaper than serialising launches.
        """
        provider = provider or None
        resume_at = max(_disk_resume_at(provider), self.resume_at(provider))
        remaining = resume_at - time.time()
        if remaining > self._max_backoff:
            remaining = self._max_backoff
        if remaining > 0:
            core.log.info(f"Throttle: waiting {remaining:.0f}s for quota to recover")
            time.sleep(remaining)

    def _write_exhausted(self, model: str, provider: str | None) -> float:
        """Locked read-modify-write of the on-disk ladder, or in-memory fallback."""
        now = time.time()
        path = throttle_path(provider)
        try:
            handle = open(path, "a+")
        except OSError:
            return self._advance(self._step(provider), now, provider)

        try:
            fcntl.flock(handle, fcntl.LOCK_EX)
        except OSError:
            with contextlib.suppress(OSError):
                handle.close()
            return self._advance(self._step(provider), now, provider)

        try:
            backoff = self._backoff_from_state(_read_handle(handle), now, provider)
            wait = self._advance(backoff, now, provider)
            _rewrite_handle(handle, {
                "resume_at": self._resume_at[provider],
                "backoff": self._backoff[provider],
                "updated_at": now,
                "pid": os.getpid(),
                "model": model,
                "provider": provider,
            })
            return wait
        finally:
            with contextlib.suppress(OSError):
                fcntl.flock(handle, fcntl.LOCK_UN)
            with contextlib.suppress(OSError):
                handle.close()

    def _step(self, provider: str | None) -> float:
        """The ladder step this process would take next for *provider*."""
        return self._backoff.get(provider, self._initial)

    def _backoff_from_state(self, state: dict, now: float, provider: str | None) -> float:
        """The ladder step to wait this time, from disk, or this process's own.

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
        return self._step(provider)

    def _advance(self, backoff: float, now: float, provider: str | None) -> float:
        """Apply one step of *provider*'s ladder to in-memory state and return the wait."""
        wait = backoff
        self._resume_at[provider] = now + wait
        self._backoff[provider] = min(backoff * 2, self._max_backoff)
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


def _disk_resume_at(provider: str | None = None) -> float:
    """Unlocked best-effort read of *provider*'s ``resume_at``. Parse or I/O errors are 0."""
    try:
        data = json.loads(throttle_path(provider).read_text())
    except (OSError, ValueError, TypeError):
        return 0.0
    if not isinstance(data, dict):
        return 0.0
    value = data.get("resume_at")
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0
