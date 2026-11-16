"""Tests for otto_queue.ledger: classifying and retrying transient SQLite errors."""

import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from otto_queue.ledger import LedgerBusy, LedgerError, _translate  # noqa: E402


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
