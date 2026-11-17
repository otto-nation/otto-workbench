"""Quiet wait notices for a ticket that has not yet been admitted.

The first wait prints a short block naming the entry and how to check or
cancel it. Later waits print only when the *kind* of reason changes, at most
once per `REPEAT_S`, with digits stripped so a wobbling metric is not a new
line. Admission after a wait prints how long it took. Output is append-only
even on a TTY: an in-place `\r` status is skipped so the reporter stays a
pure, testable clock-and-notify object.

`reason_kind` classifies the wording `otto_queue.admission` and the slot/queue
gates produce; those strings remain the owner of why a wait happened.
"""

# doc-group: queue

from __future__ import annotations

import time
from collections.abc import Callable
from enum import StrEnum

REPEAT_S = 30.0


class WaitKind(StrEnum):
    BEHIND = "behind an older entry"
    SLOTS = "all slots taken"
    MEMORY = "memory"
    CPU = "CPU pressure"
    OTHER = "other"


def reason_kind(reason: str) -> WaitKind:
    """Which wait kind *reason* is, ignoring digits and percents."""
    text = reason.strip().lower()
    if text.startswith("behind entry"):
        return WaitKind.BEHIND
    if "slots are taken" in text or text.startswith("at the concurrency limit"):
        return WaitKind.SLOTS
    if text.startswith("cpu pressure"):
        return WaitKind.CPU
    if text.startswith("waiting for memory") or text.startswith("memory pressure"):
        return WaitKind.MEMORY
    return WaitKind.OTHER


def format_duration(seconds: float) -> str:
    """Whole seconds as `4m 3s`, with hours only when the wait lasted that long."""
    total = max(0, int(seconds))
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


class WaitReporter:
    """Said-state for one entry's wait. *clock* is `time.monotonic` in production."""

    def __init__(self, entry_id: int, entry_class: str, notify: Callable[[str], None],
                 *, clock: Callable[[], float] = time.monotonic,
                 repeat_s: float = REPEAT_S) -> None:
        self._entry_id = entry_id
        self._entry_class = entry_class
        self._notify = notify
        self._clock = clock
        self._repeat_s = repeat_s
        self._started: float | None = None
        self._said_at: float | None = None
        self._kind: WaitKind | None = None
        self._printed_kind: WaitKind | None = None

    def waiting(self, reason: str) -> bool:
        """Record a wait. Returns True when the kind changed (or this is the first)."""
        kind = reason_kind(reason)
        now = self._clock()
        if self._started is None:
            self._started = now
            self._kind = kind
            self._printed_kind = kind
            self._said_at = now
            self._notify(
                f"otto-queue: queued as entry {self._entry_id} ({self._entry_class}). "
                f"Waiting for room: {kind.value}.")
            self._notify(
                f"  status: otto-queue status    "
                f"cancel: otto-queue cancel {self._entry_id}    "
                f"Herdr: the Queue panel")
            return True
        changed = kind != self._kind
        self._kind = kind
        if (kind != self._printed_kind and self._said_at is not None
                and now - self._said_at >= self._repeat_s):
            self._notify(
                f"otto-queue: entry {self._entry_id} still waiting: {kind.value}")
            self._printed_kind = kind
            self._said_at = now
        return changed

    def admitted(self) -> None:
        """Print how long the wait took; silent when nothing was ever waited."""
        if self._started is None:
            return
        waited = format_duration(self._clock() - self._started)
        self._notify(
            f"otto-queue: entry {self._entry_id} admitted after {waited}")
