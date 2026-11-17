"""QueueEntries accepts, reads, and cancels tickets against a real ledger."""

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.queue_entries  # noqa: E402
import core.log  # noqa: E402
import otto_queue.lease  # noqa: E402
import otto_queue.paths  # noqa: E402
from otto_queue.ledger import Ledger  # noqa: E402
from otto_queue.model import EntryKind, EntryState  # noqa: E402


def _accept(root, **kw):
    q = batch.queue_entries.QueueEntries(root)
    accepted = q.accept(argv=("true",), cwd="/", label="L",
                        subject="o/r#1", action="batch:review", **kw)
    return q, accepted


def test_accept_creates_a_queued_ticket_holding_its_lease(tmp_path):
    q, accepted = _accept(tmp_path)
    assert accepted is not None
    try:
        entry = Ledger.open(tmp_path).get(accepted.entry_id)
        assert entry is not None
        assert entry.kind is EntryKind.TICKET
        assert entry.state is EntryState.QUEUED
        assert entry.pid == os.getpid()
        assert entry.subject == "o/r#1"
        assert entry.action == "batch:review"
        path = otto_queue.paths.lease_path(tmp_path, accepted.entry_id)
        held, on_disk = os.fstat(accepted.fd), os.stat(path)
        assert (held.st_ino, held.st_dev) == (on_disk.st_ino, on_disk.st_dev)
        assert not otto_queue.lease.orphaned(tmp_path, accepted.entry_id)
        assert q.states([accepted.entry_id, 99]) == {accepted.entry_id: EntryState.QUEUED}
    finally:
        os.close(accepted.fd)


def test_cancel_moves_a_queued_entry_to_cancelled_and_leaves_a_running_one(tmp_path):
    q, queued = _accept(tmp_path)
    running = q.accept(argv=("true",), cwd="/", label="R",
                       subject="o/r#2", action="batch:review")
    assert queued is not None and running is not None
    try:
        ledger = Ledger.open(tmp_path)
        ledger.mark_running(running.entry_id, os.getpid())
        q.cancel(queued.entry_id)
        assert q.states([queued.entry_id, running.entry_id]) == {
            queued.entry_id: EntryState.CANCELLED,
            running.entry_id: EntryState.RUNNING,
        }
    finally:
        os.close(queued.fd)
        os.close(running.fd)


def test_an_unopenable_ledger_gives_none_empty_and_a_noop_cancel(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(core.log, "warn", lambda msg: seen.append(msg))
    blocked = tmp_path / "not-a-dir"
    blocked.write_text("x")
    q = batch.queue_entries.QueueEntries(blocked)
    assert q.accept(argv=("true",), cwd="/", label="L",
                    subject="s", action="a") is None
    assert q.states([1]) == {}
    q.cancel(1)
    assert len(seen) == 1
    assert q.accept(argv=("true",), cwd="/", label="L",
                    subject="s", action="a") is None
    assert q.states([1]) == {}
    assert len(seen) == 1
