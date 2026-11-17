"""Accept otto-queue tickets for batch steps, read their states, and cancel waiters.

The scheduler creates each queued step's ticket, holds the lease, and hands the
child the entry id and lease fd. A pid match against ticket rows is not used.
"""

# doc-group: batch

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import core.log
import otto_queue.ledger
from otto_queue.model import EntryClass, EntryKind, EntryState, NewEntry


@dataclass(frozen=True)
class Accepted:
    """A ticket the scheduler accepted and still holds the lease fd for."""

    entry_id: int
    fd: int


class QueueEntries:
    """Ledger access for accepting, watching, and cancelling batch-step tickets."""

    def __init__(self, root: Path | None = None):
        self._root = root
        self._ledger: otto_queue.ledger.Ledger | None = None
        self._disabled = False

    def accept(self, *, argv: Sequence[str], cwd: str, label: str, subject: str,
               action: str, entry_class: EntryClass) -> Accepted | None:
        """Record a new ticket and return its id and held lease fd.

        `None` when the ledger cannot be opened: the caller spawns without
        adoption. `LedgerBusy` propagates so the caller can refuse this tick.
        """
        ledger = self._open()
        if ledger is None:
            return None
        try:
            resolution = ledger.resolve(
                NewEntry(kind=EntryKind.TICKET, entry_class=entry_class,
                         argv=tuple(argv), cwd=cwd, label=label, subject=subject,
                         action=action),
                force=True, pid=os.getpid())
        except otto_queue.ledger.LedgerBusy:
            raise
        except otto_queue.ledger.LedgerError:
            return None
        lease = resolution.lease
        if lease is None:
            return None
        return Accepted(resolution.entry.id, lease.fd)

    def states(self, ids: Sequence[int]) -> dict[int, EntryState]:
        """Each known id's current state; empty when the ledger cannot be read."""
        ledger = self._open_or_none()
        if ledger is None:
            return {}
        try:
            return ledger.states(ids)
        except otto_queue.ledger.LedgerError:
            return {}

    def cancel(self, entry_id: int) -> None:
        """Cancel *entry_id*. The scheduler calls this only for still-queued rows."""
        ledger = self._open_or_none()
        if ledger is None:
            return
        # Imported here so `pr --help`, which loads batch for its registry, stays light.
        import otto_queue.ticket
        try:
            otto_queue.ticket.cancel_entry(ledger, entry_id)
        except otto_queue.ledger.LedgerError:
            return

    def _open_or_none(self) -> otto_queue.ledger.Ledger | None:
        try:
            return self._open()
        except otto_queue.ledger.LedgerBusy:
            return None

    def _open(self) -> otto_queue.ledger.Ledger | None:
        if self._ledger is not None:
            return self._ledger
        if self._disabled:
            return None
        try:
            self._ledger = otto_queue.ledger.Ledger.open(self._root)
        except otto_queue.ledger.LedgerBusy:
            raise
        except otto_queue.ledger.LedgerError as exc:
            self._disabled = True
            core.log.warn(f"otto-queue: batch cannot accept tickets: {exc}")
            return None
        return self._ledger
