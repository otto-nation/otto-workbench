"""Live review entries from the otto-queue ledger, as board facts."""

import json
import sqlite3
import time
from pathlib import Path

import board.queue
import otto_queue.ledger
import otto_queue.paths
from board.document import Checkout, build
from board.github import PullList
from board.model import FEATURES, Phase, PullFacts, QueueWork
from otto_queue.ledger import Ledger
from otto_queue.model import EntryState


def _sql(root: Path):
    return sqlite3.connect(str(otto_queue.paths.db_path(root)), isolation_level=None)


def _insert(root: Path, *, state="queued", subject="acme/box#7",
            action="review", terminal=None):
    Ledger.open(root)
    conn = _sql(root)
    try:
        cur = conn.execute(
            "INSERT INTO entries (kind, entry_class, argv, cwd, state, created_at, "
            "subject, action, terminal) VALUES ('ticket', 'heavy', ?, '/', ?, ?, ?, ?, ?)",
            (json.dumps(["true"]), state, time.time(), subject, action, terminal),
        )
        return cur.lastrowid
    finally:
        conn.close()


def test_missing_ledger_is_empty(tmp_path):
    assert board.queue.review_work(tmp_path) == {}


def test_old_schema_ledger_is_empty(tmp_path, monkeypatch):
    current = otto_queue.ledger.MIGRATIONS
    monkeypatch.setattr(otto_queue.ledger, "MIGRATIONS", current[:-1])
    Ledger.open(tmp_path)
    monkeypatch.setattr(otto_queue.ledger, "MIGRATIONS", current)
    assert board.queue.review_work(tmp_path) == {}


def test_busy_ledger_is_empty_without_retry(tmp_path, monkeypatch):
    Ledger.open(tmp_path)
    busy = sqlite3.OperationalError("database is locked")
    busy.sqlite_errorcode = sqlite3.SQLITE_BUSY

    def boom(*_a, **_k):
        raise busy

    monkeypatch.setattr(board.queue.sqlite3, "connect", boom)
    started = time.monotonic()
    assert board.queue.review_work(tmp_path) == {}
    assert time.monotonic() - started < 1


def test_corrupt_ledger_is_empty(tmp_path):
    otto_queue.paths.db_path(tmp_path).write_text("not sqlite", encoding="utf-8")
    assert board.queue.review_work(tmp_path) == {}


def test_queued_review_is_keyed_by_subject(tmp_path):
    entry_id = _insert(tmp_path, terminal="term-1")
    assert board.queue.review_work(tmp_path) == {
        "acme/box#7": QueueWork(
            state=EntryState.QUEUED, entry=entry_id, terminal="term-1",
        ),
    }


def test_running_review_and_non_review_actions(tmp_path):
    running = _insert(tmp_path, state="running", subject="acme/box#8",
                      action="review", terminal="t")
    _insert(tmp_path, subject="acme/box#9", action="comments")
    _insert(tmp_path, state="done", subject="acme/box#10", action="review")
    _insert(tmp_path, subject="acme/box#11", action="batch:review")
    _insert(tmp_path, subject="acme/box#12", action="pr review")
    got = board.queue.review_work(tmp_path)
    assert got["acme/box#8"] == QueueWork(
        state=EntryState.RUNNING, entry=running, terminal="t",
    )
    assert "acme/box#9" not in got
    assert "acme/box#10" not in got
    assert "acme/box#11" in got
    assert "acme/box#12" in got


def test_running_wins_when_the_same_pr_has_two_live_entries(tmp_path):
    _insert(tmp_path, state="queued", subject="acme/box#7")
    running = _insert(tmp_path, state="running", subject="acme/box#7",
                      terminal="live")
    got = board.queue.review_work(tmp_path)["acme/box#7"]
    assert got.state is EntryState.RUNNING
    assert got.entry == running
    assert got.terminal == "live"


def test_document_row_carries_queue_and_queue_lanes_feature(
        tmp_path, monkeypatch,
):
    monkeypatch.setenv("WORKBENCH_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path / "state"))
    found = Checkout(str(tmp_path / "co"), "acme/box", "github.com", "k")
    monkeypatch.setattr("gh.client.login", lambda: "me")
    monkeypatch.setattr("board.document.review.listing.rows", lambda *a, **k: [])
    monkeypatch.setattr("board.rows.batch.store.busy_prs", lambda ref: frozenset())
    monkeypatch.setattr("board.document._resolve", lambda path: found)
    monkeypatch.setattr(
        "board.github.list_pulls",
        lambda slug: PullList(pulls=[PullFacts(
            repo="acme/box", number=7, title="T", author="me", head_sha="aaa",
        )]),
    )
    root = otto_queue.paths.queue_root()
    entry_id = _insert(root, terminal="pane-3")
    doc = build(Phase.LISTING, [found.path])
    assert "queue-lanes" in doc["features"]
    assert list(FEATURES)[-1] == "queue-lanes"
    row = next(r for r in doc["rows"] if r["number"] == 7)
    assert row["queue"] == {
        "state": "queued", "entry": entry_id, "terminal": "pane-3",
    }
    assert row["lanes"]["team"] == "in-queue"
    assert any(c["key"] == "in-progress" for c in doc["columns"] if c["board"] == "team")
