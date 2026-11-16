"""Read live review entries from the otto-queue ledger for the PR board.

A missing, locked, corrupt, or old-schema ledger is no queue info, not an
error. The board never writes the ledger and never waits out SQLITE_BUSY.
"""

# doc-group: board

from __future__ import annotations

import sqlite3
from pathlib import Path

import otto_queue.ledger
import otto_queue.paths
from board.model import QueueWork
from otto_queue.model import LIVE_STATES, EntryState

_NEEDED = frozenset({"subject", "action", "terminal"})
_LIVE = tuple(sorted(state.value for state in LIVE_STATES))
_LIVE_IN = "(" + ", ".join("?" for _ in _LIVE) + ")"
# sqlite busy-handler wait. Zero so a locked ledger is empty, not a hang.
_NO_WAIT = 0.0


def review_work(root: Path | None = None) -> dict[str, QueueWork]:
    """Live review tickets keyed by ``owner/repo#N``.

    Never raises. ``{}`` when the ledger is missing, busy, unreadable, or
    from before ``subject``/``action``/``terminal``.
    """
    path = otto_queue.paths.db_path(root or otto_queue.paths.queue_root())
    if not path.is_file():
        return {}
    try:
        return _read(path)
    except Exception:
        return {}


def _is_review_action(action: str | None) -> bool:
    if not action:
        return False
    name = action[3:] if action.startswith("pr ") else action
    if name.startswith("batch:"):
        name = name[6:]
    return name == "review"


def _read(path: Path) -> dict[str, QueueWork]:
    conn = None
    try:
        conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=_NO_WAIT)
        conn.row_factory = sqlite3.Row
        names = {
            row[1]
            for row in conn.execute("PRAGMA table_info(entries)").fetchall()
        }
        if not _NEEDED <= names:
            return {}
        rows = conn.execute(
            f"SELECT id, state, subject, action, terminal FROM entries "
            f"WHERE state IN {_LIVE_IN}",
            _LIVE,
        ).fetchall()
    except sqlite3.Error as exc:
        if otto_queue.ledger._is_busy(exc):
            return {}
        raise
    finally:
        if conn is not None:
            conn.close()
    out: dict[str, QueueWork] = {}
    for row in rows:
        subject = row["subject"]
        if not subject or not _is_review_action(row["action"]):
            continue
        try:
            state = EntryState(row["state"])
        except ValueError:
            continue
        if state not in LIVE_STATES:
            continue
        current = out.get(subject)
        if current is not None and not _beats(state, row["id"], current):
            continue
        out[subject] = QueueWork(
            state=state, entry=row["id"], terminal=row["terminal"],
        )
    return out


def _beats(state: EntryState, entry_id: int, current: QueueWork) -> bool:
    if state is EntryState.RUNNING and current.state != EntryState.RUNNING:
        return True
    if state is EntryState.QUEUED and current.state == EntryState.RUNNING:
        return False
    return entry_id > current.entry
