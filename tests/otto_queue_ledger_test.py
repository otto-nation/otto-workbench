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
