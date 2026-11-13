"""The queue's vocabulary: what an entry is, which class it runs in, and the states it moves through.

Every value here is persisted in the ledger, so the enums' string values are
stable. A machine mid-sync runs old and new code against one file, so a value
is added, never renamed or removed — the rule the schema follows too.

| State | Meaning |
|---|---|
| `queued` | accepted; waiting for a slot (ticket), or for the worker or its retry time (job) |
| `running` | admitted and started; its lease is held |
| `done` | exited 0 |
| `failed` | exited non-zero, could not start, or its holder died (no exit status) |
| `cancelled` | stopped by `otto-queue cancel`, or Ctrl-C while waiting |
| `tempfail` | exited 75 (EX_TEMPFAIL), or never got a slot: its resource was busy, which is not a failure |
"""

# doc-group: queue

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum


class EntryKind(StrEnum):
    """A ticket runs in its caller's process; a job is run by the worker."""

    TICKET = "ticket"
    JOB = "job"


class EntryClass(StrEnum):
    """Heavy entries are counted and memory-checked; light ones are counted only."""

    HEAVY = "heavy"
    LIGHT = "light"


class EntryState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TEMPFAIL = "tempfail"

    @property
    def live(self) -> bool:
        """Still waiting or running."""
        return self in LIVE_STATES


LIVE_STATES = frozenset({EntryState.QUEUED, EntryState.RUNNING})


def state_for_exit(exit_status: int) -> EntryState:
    """The state an exit status records: 0 is done, 75 (EX_TEMPFAIL) tempfail, anything else failed."""
    if exit_status == 0:
        return EntryState.DONE
    if exit_status == os.EX_TEMPFAIL:
        return EntryState.TEMPFAIL
    return EntryState.FAILED


# A job that exits 75 runs again after RETRY_BASE_S, doubling per run up to
# RETRY_CAP_S, and ends `tempfail` once it has run MAX_JOB_ATTEMPTS times. An
# orphaned job is re-queued under the same cap, so a job whose runner dies every
# time ends `failed` rather than looping.
MAX_JOB_ATTEMPTS = 10
RETRY_BASE_S = 60.0
RETRY_CAP_S = 1800.0


def retry_delay(attempts: int) -> float:
    """Seconds before a job that has run *attempts* times and exited 75 may run again."""
    return min(RETRY_BASE_S * 2 ** max(0, attempts - 1), RETRY_CAP_S)


@dataclass(frozen=True)
class NewEntry:
    """A request to accept: what to run, where, and how to identify repeats of it."""

    argv: tuple[str, ...]
    cwd: str
    kind: EntryKind = EntryKind.TICKET
    entry_class: EntryClass = EntryClass.HEAVY
    idem_key: str | None = None
    env_allow: tuple[str, ...] = ()


@dataclass(frozen=True)
class Entry:
    """One ledger row."""

    id: int
    kind: EntryKind
    entry_class: EntryClass
    idem_key: str | None
    argv: tuple[str, ...]
    cwd: str
    env_allow: tuple[str, ...]
    state: EntryState
    attempts: int
    exit_status: int | None
    log_path: str | None
    result: str | None
    pid: int | None
    created_at: float
    started_at: float | None
    finished_at: float | None
    not_before: float | None = None

    @property
    def holds_lease(self) -> bool:
        """Whether a live process must hold this entry's lease: any running entry, and a waiting ticket."""
        return self.state is EntryState.RUNNING or (
            self.kind is EntryKind.TICKET and self.state is EntryState.QUEUED)
