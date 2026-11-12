"""The per-machine ledger: one SQLite file holding every entry the queue has accepted.

`state_dir()/queue/queue.db`, stdlib `sqlite3`, in WAL mode, so a reader
(`otto-queue status`) never blocks a writer. A connection is opened per
operation and closed after it, so no process holds the file between calls,
and a read-modify-write runs under `BEGIN IMMEDIATE`: two processes deciding
about the same key are serialized by SQLite's own write lock.

Schema changes are additive only. `MIGRATIONS[n]` takes a ledger from version
n to n+1. Each statement creates something that did not exist or adds a
column — never renames, drops or repurposes one — and a test holds every
statement to that. The stored version is checked on every open:

| Stored version | What open does |
|---|---|
| older than this code | applies the missing migrations and records the new version |
| this code's | nothing |
| newer (newer code on this machine, mid-sync) | uses it as it is; never writes the version backwards |
| not a number | refuses with `LedgerError` |

A newer ledger is safe to use because every reader names its columns and
every new column is nullable or defaulted. A row whose kind, class or state
this code does not know was written by newer code, and readers skip it rather
than crash.

`resolve` is where idempotency lives. A keyed request matching a `done` entry
is REUSED (its recorded status is the answer, and nothing runs). One matching
a live entry is JOINED. Anything else, or any request with `force`, is a new
`queued` row: a ticket's row holds its lease from the same transaction. Only
`done` counts as already done; a failed, cancelled or tempfail entry is run
again. Orphans are reaped first, so a dead holder's row is not joined for
long: the joiner reaps it and resolves again. An orphaned ticket becomes
`failed` with no exit status, and an orphaned running job goes back to
`queued` for the worker.
"""

# doc-group: queue

from __future__ import annotations

import contextlib
import itertools
import json
import sqlite3
import time
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import core.proc
import core.timeouts
import otto_queue.lease
import otto_queue.paths
from otto_queue.model import LIVE_STATES, Entry, EntryClass, EntryKind, EntryState, NewEntry, state_for_exit

# Finished rows `entries()` returns beside the live ones: enough for `status` to
# show what just ended without reading the whole history.
RECENT_TERMINAL = 20

MIGRATIONS: tuple[tuple[str, ...], ...] = (
    (
        "CREATE TABLE IF NOT EXISTS entries ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " kind TEXT NOT NULL,"
        " entry_class TEXT NOT NULL,"
        " idem_key TEXT,"
        " argv TEXT NOT NULL,"
        " cwd TEXT NOT NULL,"
        " env_allow TEXT NOT NULL DEFAULT '[]',"
        " state TEXT NOT NULL,"
        " attempts INTEGER NOT NULL DEFAULT 0,"
        " exit_status INTEGER,"
        " log_path TEXT,"
        " result TEXT,"
        " pid INTEGER,"
        " created_at REAL NOT NULL,"
        " started_at REAL,"
        " finished_at REAL)",
        "CREATE INDEX IF NOT EXISTS entries_by_key ON entries (idem_key, state)",
        "CREATE INDEX IF NOT EXISTS entries_by_state ON entries (state, entry_class)",
    ),
)

_VERSION_KEY = "schema_version"

# The live states as SQL parameters, and the placeholder list that takes them.
_LIVE = tuple(sorted(state.value for state in LIVE_STATES))
_LIVE_IN = "(" + ", ".join("?" for _ in _LIVE) + ")"


class LedgerError(RuntimeError):
    """Raised by any ledger operation (open or later): unwritable, not
    SQLite, or a version that is not a number."""


class LedgerBusy(LedgerError):
    """Raised by any ledger operation (open or later) that stayed
    SQLITE_BUSY past the deadline."""


# sqlite3 busy-handler wait on each Ledger.open attempt. Zero so the overall
# LOCAL deadline, not each attempt, bounds the wait.
_OPEN_BUSY_TIMEOUT = 0.0

# Pause between SQLITE_BUSY retries while opening.
_OPEN_BUSY_POLL_S = 0.05


def _connect(root: Path, *, timeout: float) -> sqlite3.Connection:
    conn = sqlite3.connect(str(otto_queue.paths.db_path(root)),
                           timeout=timeout, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


def _is_busy(exc: BaseException) -> bool:
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    code = getattr(exc, "sqlite_errorcode", None)
    if code is not None:
        # Extended codes (BUSY_RECOVERY, BUSY_SNAPSHOT, BUSY_TIMEOUT) share
        # SQLITE_BUSY as the primary byte; WAL open races raise those too.
        return (code & 0xFF) == sqlite3.SQLITE_BUSY
    name = getattr(exc, "sqlite_errorname", None) or ""
    return name == "SQLITE_BUSY" or name.startswith("SQLITE_BUSY_")


def _translate(root: Path, exc: sqlite3.Error) -> LedgerError:
    path = otto_queue.paths.db_path(root)
    if _is_busy(exc):
        return LedgerBusy(f"cannot use {path}: queue busy")
    return LedgerError(f"cannot use {path}: {exc}")


@contextlib.contextmanager
def _transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def _stored_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT value FROM meta WHERE name = ?", (_VERSION_KEY,)).fetchone()
    if row is None:
        return 0
    try:
        return int(row["value"])
    except ValueError as exc:
        raise LedgerError(f"{_VERSION_KEY} is not a number: {row['value']!r}") from exc


def _apply_migrations(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS meta (name TEXT PRIMARY KEY, value TEXT NOT NULL)")
    stored = _stored_version(conn)
    for statement in itertools.chain.from_iterable(MIGRATIONS[stored:]):
        conn.execute(statement)
    if stored < len(MIGRATIONS):
        conn.execute("INSERT INTO meta (name, value) VALUES (?, ?) "
                     "ON CONFLICT(name) DO UPDATE SET value = excluded.value",
                     (_VERSION_KEY, str(len(MIGRATIONS))))


def _open_schema(conn: sqlite3.Connection) -> None:
    # WAL persists in the file. Setting it again is the open that races under
    # SQLITE_BUSY; skip it when a previous open already left the file in WAL.
    # A filesystem that refuses WAL answers its old mode, which still works,
    # only with readers blocking a writer.
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    if str(mode).lower() != "wal":
        conn.execute("PRAGMA journal_mode=WAL")
    with _transaction(conn):
        _apply_migrations(conn)


def _try_open(root: Path) -> sqlite3.OperationalError | None:
    """Open and migrate; return a SQLITE_BUSY error to retry, or None if ready."""
    try:
        with contextlib.closing(_connect(root, timeout=_OPEN_BUSY_TIMEOUT)) as conn:
            _open_schema(conn)
    except sqlite3.OperationalError as exc:
        if _is_busy(exc):
            return exc
        raise
    return None


def _wait_open(root: Path) -> None:
    deadline = time.monotonic() + core.timeouts.LOCAL
    while True:
        last = _try_open(root)
        if last is None:
            return
        if time.monotonic() >= deadline:
            path = otto_queue.paths.db_path(root)
            raise LedgerBusy(f"cannot open {path}: queue busy") from last
        time.sleep(_OPEN_BUSY_POLL_S)


def _row(conn: sqlite3.Connection, entry_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone()


def _entry(row: sqlite3.Row | None) -> Entry | None:
    """*row* as an `Entry`; None for no row, or for a kind, class or state newer code wrote."""
    if row is None:
        return None
    try:
        kind = EntryKind(row["kind"])
        entry_class = EntryClass(row["entry_class"])
        state = EntryState(row["state"])
    except ValueError:
        return None
    return Entry(id=row["id"], kind=kind, entry_class=entry_class, idem_key=row["idem_key"],
                 argv=tuple(json.loads(row["argv"])), cwd=row["cwd"],
                 env_allow=tuple(json.loads(row["env_allow"])), state=state,
                 attempts=row["attempts"], exit_status=row["exit_status"],
                 log_path=row["log_path"], result=row["result"], pid=row["pid"],
                 created_at=row["created_at"], started_at=row["started_at"],
                 finished_at=row["finished_at"])


class Outcome(StrEnum):
    """How `Ledger.resolve` answered a request."""

    NEW = "new"
    JOINED = "joined"
    REUSED = "reused"


@dataclass(frozen=True)
class Resolution:
    """`resolve`'s answer: the entry, and for a NEW ticket the lease it now holds."""

    outcome: Outcome
    entry: Entry
    lease: otto_queue.lease.Lease | None = None


def _match(conn: sqlite3.Connection, key: str) -> Resolution | None:
    done = conn.execute("SELECT * FROM entries WHERE idem_key = ? AND state = ? "
                        "ORDER BY id DESC LIMIT 1", (key, EntryState.DONE.value)).fetchone()
    if (entry := _entry(done)) is not None:
        return Resolution(Outcome.REUSED, entry)
    live = conn.execute(f"SELECT * FROM entries WHERE idem_key = ? AND state IN {_LIVE_IN} "
                        "ORDER BY id DESC LIMIT 1", (key, *_LIVE)).fetchone()
    if (entry := _entry(live)) is not None:
        return Resolution(Outcome.JOINED, entry)
    return None


def _insert(conn: sqlite3.Connection, new: NewEntry, pid: int | None) -> int:
    cur = conn.execute(
        "INSERT INTO entries (kind, entry_class, idem_key, argv, cwd, env_allow, state, pid, "
        "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (new.kind.value, new.entry_class.value, new.idem_key, json.dumps(list(new.argv)),
         new.cwd, json.dumps(list(new.env_allow)), EntryState.QUEUED.value, pid, time.time()))
    return cur.lastrowid


def _end_orphan(conn: sqlite3.Connection, entry: Entry) -> None:
    if entry.kind is EntryKind.JOB:
        conn.execute("UPDATE entries SET state = ?, pid = NULL WHERE id = ?",
                     (EntryState.QUEUED.value, entry.id))
        return
    conn.execute("UPDATE entries SET state = ?, finished_at = ? WHERE id = ?",
                 (EntryState.FAILED.value, time.time(), entry.id))


def _resolve_tx(
    root: Path,
    conn: sqlite3.Connection,
    new: NewEntry,
    force: bool,
    pid: int | None,
    taken: list[otto_queue.lease.Lease],
) -> Resolution:
    match = None if force or not new.idem_key else _match(conn, new.idem_key)
    if match is not None:
        return match
    entry_id = _insert(conn, new, pid)
    lease = (otto_queue.lease.take(root, entry_id)
             if new.kind is EntryKind.TICKET else None)
    if lease is not None:
        taken.append(lease)
    return Resolution(Outcome.NEW, _entry(_row(conn, entry_id)), lease)


class Ledger:
    """The ledger under `root` — `otto_queue.paths.queue_root()` unless a caller names one."""

    def __init__(self, root: Path):
        self.root = root

    @classmethod
    def open(cls, root: Path | None = None) -> Ledger:
        """Create the ledger or bring it forward; `LedgerError` when that cannot be done.

        Raises `LedgerBusy` when the ledger stays busy past `core.timeouts.LOCAL`.
        """
        root = root or otto_queue.paths.queue_root()
        try:
            root.mkdir(parents=True, exist_ok=True)
            _wait_open(root)
        except (OSError, sqlite3.Error) as exc:
            raise LedgerError(f"cannot open {otto_queue.paths.db_path(root)}: {exc}") from exc
        return cls(root)

    @contextlib.contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        try:
            with contextlib.closing(_connect(self.root, timeout=core.timeouts.LOCAL)) as conn:
                yield conn
        except sqlite3.Error as exc:
            raise _translate(self.root, exc) from exc

    @contextlib.contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        with self._conn() as conn, _transaction(conn):
            yield conn

    def schema_version(self) -> int:
        with self._conn() as conn:
            return _stored_version(conn)

    def get(self, entry_id: int) -> Entry | None:
        with self._conn() as conn:
            return _entry(_row(conn, entry_id))

    def entries(self, *, recent: int = RECENT_TERMINAL) -> list[Entry]:
        """Every live entry and the *recent* newest finished ones, oldest first."""
        with self._conn() as conn:
            live = conn.execute(f"SELECT * FROM entries WHERE state IN {_LIVE_IN}", _LIVE).fetchall()
            ended = conn.execute(f"SELECT * FROM entries WHERE state NOT IN {_LIVE_IN} "
                                 "ORDER BY id DESC LIMIT ?", (*_LIVE, recent)).fetchall()
        rows = sorted([*live, *ended], key=lambda r: r["id"])
        return [e for e in map(_entry, rows) if e is not None]

    def resolve(self, new: NewEntry, *, force: bool, pid: int | None) -> Resolution:
        """Accept *new*, or answer it from an entry with the same key; see the module docstring."""
        self.reap_orphans()
        # A ticket lease is taken inside the transaction. If anything after that
        # raises — including COMMIT when this `with` exits — the row rolls back
        # and sqlite reuses the id; the flock must not stay held.
        taken: list[otto_queue.lease.Lease] = []
        try:
            with self._write() as conn:
                return _resolve_tx(self.root, conn, new, force, pid, taken)
        except BaseException:
            for lease in taken:
                lease.release()
            raise

    def mark_running(self, entry_id: int, pid: int) -> bool:
        """Move a queued entry to running under *pid*; False if it is no longer queued."""
        with self._write() as conn:
            cur = conn.execute(
                "UPDATE entries SET state = ?, attempts = attempts + 1, started_at = ?, pid = ? "
                "WHERE id = ? AND state = ?",
                (EntryState.RUNNING.value, time.time(), pid, entry_id, EntryState.QUEUED.value))
            return cur.rowcount == 1

    def finish(self, entry_id: int, exit_status: int) -> EntryState | None:
        """Record *exit_status* and the state it implies; a cancelled entry stays `cancelled`."""
        with self._write() as conn:
            cur = conn.execute(
                f"UPDATE entries SET state = ?, exit_status = ?, finished_at = ? "
                f"WHERE id = ? AND state IN {_LIVE_IN}",
                (state_for_exit(exit_status).value, exit_status, time.time(), entry_id, *_LIVE))
            if cur.rowcount == 0:
                conn.execute("UPDATE entries SET exit_status = ? WHERE id = ? AND exit_status IS NULL",
                             (exit_status, entry_id))
            entry = _entry(_row(conn, entry_id))
        return entry.state if entry is not None else None

    def fail(self, entry_id: int) -> None:
        """Mark a live entry `failed` with no exit status; a terminal row is left as it is."""
        with self._write() as conn:
            conn.execute(
                "UPDATE entries SET state = ?, finished_at = ? "
                f"WHERE id = ? AND state IN {_LIVE_IN}",
                (EntryState.FAILED.value, time.time(), entry_id, *_LIVE))

    def cancel(self, entry_id: int) -> Entry | None:
        """Mark a live entry `cancelled`; return it as it was before, or None if there is none.

        A queued entry gets the interrupt status (130) now, since nothing will run
        to report one. A running entry's status arrives when its process exits.
        """
        with self._write() as conn:
            before = _entry(_row(conn, entry_id))
            if before is None or not before.state.live:
                return before
            status = core.proc.INTERRUPT_RETURNCODE if before.state is EntryState.QUEUED else None
            conn.execute("UPDATE entries SET state = ?, exit_status = ?, finished_at = ? WHERE id = ?",
                         (EntryState.CANCELLED.value, status, time.time(), entry_id))
            return before

    def reap_orphans(self) -> list[int]:
        """End every live entry whose lease nothing holds, and return their ids."""
        with self._write() as conn:
            rows = conn.execute(f"SELECT * FROM entries WHERE state IN {_LIVE_IN}", _LIVE).fetchall()
            orphans = [e for e in map(_entry, rows) if e is not None and e.holds_lease
                       and otto_queue.lease.orphaned(self.root, e.id)]
            for entry in orphans:
                _end_orphan(conn, entry)
        # A job's lease file stays: the worker restarts that id and takes it again.
        for entry in orphans:
            if entry.kind is EntryKind.TICKET:
                otto_queue.lease.discard(self.root, entry.id)
        return [e.id for e in orphans]

    def first_waiting(self, entry_class: EntryClass) -> int | None:
        """The oldest ticket of *entry_class* still waiting for a slot, or None."""
        # ceiling: tickets only, since a queued job has no worker to start it yet and
        # would hold every ticket behind it forever. Upgrade trigger: once the worker
        # claims slots for jobs, order jobs and tickets in one queue.
        with self._conn() as conn:
            row = conn.execute(
                "SELECT MIN(id) AS id FROM entries WHERE kind = ? AND state = ? AND entry_class = ?",
                (EntryKind.TICKET.value, EntryState.QUEUED.value, entry_class.value)).fetchone()
        return row["id"]
