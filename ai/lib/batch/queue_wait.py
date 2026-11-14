"""Match live batch steps to otto-queue tickets they are waiting on.

A step's process is the ticket process: `StepProcess.start` Popens the `pr`
or `ci-check` shim, which execs Python in place, and the gate calls
`run_ticket` in-process. The ticket row's `pid` is written in the same
transaction that creates it, and a queued ticket holds its lease, so the pid
is live and cannot have been reused. Matching `StepProcess.pid` against
queued tickets is how the scheduler reports `wait_reason` without parsing
log prose or adding a side protocol.
"""

# doc-group: batch

from __future__ import annotations

from collections.abc import Collection
from pathlib import Path

import core.log
import otto_queue.ledger
from otto_queue.model import EntryKind, EntryState


class QueueWaits:
    """Map each pid that holds a queued ticket to that ticket's entry id."""

    def __init__(self, root: Path | None = None):
        self._root = root
        self._ledger: otto_queue.ledger.Ledger | None = None
        self._disabled = False

    def __call__(self, pids: Collection[int]) -> dict[int, int]:
        wanted = set(pids)
        if not wanted or self._disabled:
            return {}
        ledger = self._open()
        if ledger is None:
            return {}
        try:
            entries = ledger.entries()
        except otto_queue.ledger.LedgerError:
            return {}
        return {e.pid: e.id for e in entries
                if e.kind is EntryKind.TICKET and e.state is EntryState.QUEUED
                and e.pid is not None and e.pid in wanted}

    def _open(self) -> otto_queue.ledger.Ledger | None:
        if self._ledger is not None:
            return self._ledger
        try:
            self._ledger = otto_queue.ledger.Ledger.open(self._root)
        except otto_queue.ledger.LedgerError as exc:
            self._disabled = True
            core.log.warn(f"otto-queue: batch cannot watch tickets: {exc}")
            return None
        return self._ledger
