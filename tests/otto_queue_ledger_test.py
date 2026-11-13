"""Tests for otto_queue.ledger: the schema, its version check, and reading rows."""

import itertools
import json
import re
import sqlite3
import sys
import threading
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import core.timeouts  # noqa: E402
import core.workbench_paths  # noqa: E402
import otto_queue.ledger  # noqa: E402
import otto_queue.paths  # noqa: E402
from otto_queue.ledger import Ledger, LedgerBusy, LedgerError  # noqa: E402
from otto_queue.model import EntryState  # noqa: E402
from otto_queue_support import set_columns # noqa: E402

ADDITIVE = re.compile(r"^(CREATE (TABLE|INDEX) IF NOT EXISTS|ALTER TABLE \w+ ADD COLUMN) ")


def _sql(root):
    return sqlite3.connect(str(otto_queue.paths.db_path(root)), isolation_level=None)


def _row(root, *, state="queued"):
    conn = _sql(root)
    try:
        cur = conn.execute(
            "INSERT INTO entries (kind, entry_class, argv, cwd, state, created_at) "
            "VALUES ('ticket', 'heavy', ?, '/', ?, ?)", (json.dumps(["true"]), state, time.time()))
        return cur.lastrowid
    finally:
        conn.close()


def test_open_creates_a_wal_ledger_at_the_current_version(tmp_path):
    ledger = Ledger.open(tmp_path)
    assert ledger.schema_version() == len(otto_queue.ledger.MIGRATIONS)
    assert _sql(tmp_path).execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_the_default_ledger_lives_under_the_state_root():
    ledger = Ledger.open()
    expected = core.workbench_paths.state_dir() / "queue" / "queue.db"
    assert otto_queue.paths.db_path(ledger.root) == expected
    assert expected.is_file()


def test_reopening_keeps_the_rows(tmp_path):
    Ledger.open(tmp_path)
    entry_id = _row(tmp_path)
    assert Ledger.open(tmp_path).get(entry_id).state is EntryState.QUEUED


def test_a_newer_additive_schema_opens_under_the_old_code(tmp_path, monkeypatch):
    current = otto_queue.ledger.MIGRATIONS
    newer = (*current, ("ALTER TABLE entries ADD COLUMN note TEXT",))
    monkeypatch.setattr(otto_queue.ledger, "MIGRATIONS", newer)
    assert Ledger.open(tmp_path).schema_version() == len(newer)
    columns = [r[1] for r in _sql(tmp_path).execute("PRAGMA table_info(entries)")]
    assert "note" in columns

    monkeypatch.setattr(otto_queue.ledger, "MIGRATIONS", current)
    older = Ledger.open(tmp_path)
    entry_id = _row(tmp_path)
    assert older.get(entry_id).state is EntryState.QUEUED
    assert older.schema_version() == len(newer)


def test_a_version_that_is_not_a_number_is_refused(tmp_path):
    Ledger.open(tmp_path)
    _sql(tmp_path).execute("UPDATE meta SET value = 'two' WHERE name = 'schema_version'")
    with pytest.raises(LedgerError, match="not a number"):
        Ledger.open(tmp_path)


def test_a_file_that_is_not_sqlite_is_refused(tmp_path):
    otto_queue.paths.db_path(tmp_path).write_text("not a database")
    with pytest.raises(LedgerError):
        Ledger.open(tmp_path)


def test_rows_written_by_newer_code_are_skipped(tmp_path):
    ledger = Ledger.open(tmp_path)
    known = _row(tmp_path)
    _row(tmp_path, state="paused")
    assert [e.id for e in ledger.entries()] == [known]


def test_entries_are_every_live_row_and_the_recent_finished_ones(tmp_path):
    ledger = Ledger.open(tmp_path)
    finished = [_row(tmp_path, state="done") for _ in range(3)]
    live = _row(tmp_path, state="running")
    assert [e.id for e in ledger.entries(recent=2)] == [finished[1], finished[2], live]


def test_open_succeeds_once_an_exclusive_lock_is_released(tmp_path):
    Ledger.open(tmp_path)
    holder = sqlite3.connect(str(otto_queue.paths.db_path(tmp_path)), isolation_level=None,
                             timeout=0, check_same_thread=False)
    try:
        holder.execute("BEGIN EXCLUSIVE")
        threading.Timer(0.3, holder.execute, ("COMMIT",)).start()
        ledger = Ledger.open(tmp_path)
        assert ledger.schema_version() == len(otto_queue.ledger.MIGRATIONS)
    finally:
        holder.close()


def test_a_write_after_open_raises_ledger_busy(tmp_path, monkeypatch):
    ledger = Ledger.open(tmp_path)
    entry_id = _row(tmp_path)
    holder = sqlite3.connect(str(otto_queue.paths.db_path(tmp_path)), isolation_level=None,
                             timeout=0)
    try:
        holder.execute("BEGIN EXCLUSIVE")
        monkeypatch.setattr(core.timeouts, "LOCAL", 0.2)
        with pytest.raises(LedgerBusy):
            ledger.fail(entry_id)
    finally:
        holder.close()


def test_every_migration_statement_is_additive():
    statements = list(itertools.chain.from_iterable(otto_queue.ledger.MIGRATIONS))
    assert all(ADDITIVE.match(s) for s in statements), statements
    added = [s for s in statements if s.startswith("ALTER")]
    assert all("NOT NULL" not in s or "DEFAULT" in s for s in added), added


DAY = 86400.0


def _finished(root, state, *, age_days, key=None):
    entry_id = _row(root, state=state)
    set_columns(root, entry_id, finished_at=time.time() - age_days * DAY, idem_key=key)
    return entry_id


def test_compaction_prunes_old_finished_rows_and_keeps_the_rest(tmp_path):
    ledger = Ledger.open(tmp_path)
    old_failed = _finished(tmp_path, "failed", age_days=30)
    young_done = _finished(tmp_path, "done", age_days=1)
    live = _row(tmp_path, state="queued")
    pruned = ledger.compact(retention_s=14 * DAY, now=time.time())
    assert pruned == [old_failed]
    assert ledger.get(old_failed) is None
    assert ledger.get(young_done) is not None and ledger.get(live) is not None


def test_compaction_keeps_the_newest_done_entry_of_each_key(tmp_path):
    ledger = Ledger.open(tmp_path)
    older = _finished(tmp_path, "done", age_days=60, key="review:abc")
    newest = _finished(tmp_path, "done", age_days=40, key="review:abc")
    assert ledger.compact(retention_s=14 * DAY, now=time.time()) == [older]
    assert ledger.get(newest).state is EntryState.DONE


def test_compaction_never_prunes_a_state_this_code_does_not_know(tmp_path):
    ledger = Ledger.open(tmp_path)
    future = _finished(tmp_path, "paused", age_days=60)
    assert ledger.compact(retention_s=14 * DAY, now=time.time()) == []
    assert _sql(tmp_path).execute("SELECT COUNT(*) FROM entries WHERE id = ?", (future,)).fetchone()[0] == 1


def test_compaction_removes_the_pruned_entries_files_and_records_when(tmp_path):
    ledger = Ledger.open(tmp_path)
    old = _finished(tmp_path, "done", age_days=30)
    for path in (otto_queue.paths.log_path(tmp_path, old), otto_queue.paths.lease_path(tmp_path, old)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
    moment = time.time()
    ledger.compact(retention_s=14 * DAY, now=moment)
    assert not otto_queue.paths.log_path(tmp_path, old).exists()
    assert not otto_queue.paths.lease_path(tmp_path, old).exists()
    assert ledger.compacted_at() == moment


def test_compaction_truncates_the_wal(tmp_path):
    ledger = Ledger.open(tmp_path)
    # An idle connection keeps the WAL file in place; without one SQLite deletes
    # it on the last close, and there would be nothing to measure.
    keeper = _sql(tmp_path)
    try:
        # sqlite3.connect is lazy; a statement is what actually holds the file.
        keeper.execute("SELECT 1")
        for _ in range(50):
            _row(tmp_path, state="done")
        wal = otto_queue.paths.db_path(tmp_path).with_name(otto_queue.paths.DB_NAME + "-wal")
        assert wal.stat().st_size > 0
        ledger.compact(retention_s=14 * DAY, now=time.time())
        assert wal.stat().st_size == 0
    finally:
        keeper.close()
