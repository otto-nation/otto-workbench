"""Tests for otto_queue.ledger: classifying and retrying transient SQLite errors."""

import contextlib
import os
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import otto_queue.ledger  # noqa: E402
import otto_queue.paths  # noqa: E402
from otto_queue.ledger import Ledger, LedgerBusy, LedgerError, _translate  # noqa: E402
from otto_queue.model import EntryState, NewEntry  # noqa: E402


def _operr(msg, *, code=None, name=None):
    exc = sqlite3.OperationalError(msg)
    if code is not None:
        exc.sqlite_errorcode = code
    if name is not None:
        exc.sqlite_errorname = name
    return exc


def test_translate_sqlite_busy_is_ledger_busy(tmp_path):
    err = _translate(tmp_path, _operr("database is locked", code=sqlite3.SQLITE_BUSY,
                                     name="SQLITE_BUSY"))
    assert type(err) is LedgerBusy


def test_translate_busy_snapshot_is_ledger_busy(tmp_path):
    err = _translate(tmp_path, _operr("database is locked",
                                     code=sqlite3.SQLITE_BUSY_SNAPSHOT,
                                     name="SQLITE_BUSY_SNAPSHOT"))
    assert type(err) is LedgerBusy


def test_translate_no_such_table_is_ledger_error(tmp_path):
    err = _translate(tmp_path, _operr("no such table: entries", code=sqlite3.SQLITE_ERROR,
                                     name="SQLITE_ERROR"))
    assert type(err) is LedgerError


def test_translate_protocol_is_ledger_busy(tmp_path):
    err = _translate(tmp_path, _operr("locking protocol",
                                     code=sqlite3.SQLITE_PROTOCOL,
                                     name="SQLITE_PROTOCOL"))
    assert type(err) is LedgerBusy


def test_translate_locked_is_ledger_busy(tmp_path):
    err = _translate(tmp_path, _operr("database table is locked",
                                     code=sqlite3.SQLITE_LOCKED,
                                     name="SQLITE_LOCKED"))
    assert type(err) is LedgerBusy


def test_translate_locking_protocol_message_without_code_is_ledger_busy(tmp_path):
    err = _translate(tmp_path, _operr("locking protocol"))
    assert type(err) is LedgerBusy


def _protocol():
    return _operr("locking protocol", code=sqlite3.SQLITE_PROTOCOL, name="SQLITE_PROTOCOL")


def _sql(root):
    return sqlite3.connect(str(otto_queue.paths.db_path(root)), isolation_level=None)


def _row(root, *, state="queued"):
    conn = _sql(root)
    try:
        cur = conn.execute(
            "INSERT INTO entries (kind, entry_class, argv, cwd, state, created_at) "
            "VALUES ('ticket', 'heavy', ?, '/', ?, ?)",
            ('["true"]', state, 0.0))
        return cur.lastrowid
    finally:
        conn.close()


def _patch_connect(monkeypatch, remaining):
    real = otto_queue.ledger._connect

    def flaky(root, *, timeout):
        if remaining[0] > 0:
            remaining[0] -= 1
            raise _protocol()
        return real(root, timeout=timeout)

    monkeypatch.setattr(otto_queue.ledger, "_connect", flaky)
    monkeypatch.setattr(otto_queue.ledger, "_sleep", lambda _s: None, raising=False)


def test_get_survives_locking_protocol_then_succeeds(tmp_path, monkeypatch):
    ledger = Ledger.open(tmp_path)
    entry_id = _row(tmp_path)
    _patch_connect(monkeypatch, [3])
    entry = ledger.get(entry_id)
    assert entry is not None and entry.state is EntryState.QUEUED


def test_a_write_survives_locking_protocol_then_succeeds(tmp_path, monkeypatch):
    ledger = Ledger.open(tmp_path)
    entry_id = _row(tmp_path)
    _patch_connect(monkeypatch, [3])
    ledger.fail(entry_id)
    assert ledger.get(entry_id).state is EntryState.FAILED


def test_get_raises_ledger_busy_after_the_retry_bound(tmp_path, monkeypatch):
    ledger = Ledger.open(tmp_path)
    entry_id = _row(tmp_path)
    monkeypatch.setattr(otto_queue.ledger, "TRANSIENT_RETRY_S", 0.0, raising=False)
    _patch_connect(monkeypatch, [99])
    with pytest.raises(LedgerBusy):
        ledger.get(entry_id)


def test_idle_reap_orphans_takes_no_write_transaction(tmp_path, monkeypatch):
    ledger = Ledger.open(tmp_path)
    held = ledger.resolve(NewEntry(argv=("true",), cwd="/"), force=False, pid=os.getpid())
    writes = []
    real = otto_queue.ledger._transaction

    @contextlib.contextmanager
    def spy(conn):
        writes.append(1)
        with real(conn) as inner:
            yield inner

    monkeypatch.setattr(otto_queue.ledger, "_transaction", spy)
    try:
        assert ledger.reap_orphans() == []
        assert writes == []
    finally:
        held.lease.release()
