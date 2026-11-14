"""QueueWaits maps queued ticket pids to entry ids against a real ledger."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.queue_wait  # noqa: E402
import core.log  # noqa: E402
from otto_queue.ledger import Ledger  # noqa: E402
from otto_queue.model import EntryKind, NewEntry  # noqa: E402


def _ticket(ledger, pid, *, kind=EntryKind.TICKET):
    return ledger.resolve(
        NewEntry(argv=("true",), cwd="/", kind=kind), force=True, pid=pid)


def test_a_queued_ticket_pid_is_reported(tmp_path):
    ledger = Ledger.open(tmp_path)
    queued = _ticket(ledger, 11)
    running = _ticket(ledger, 12)
    ledger.mark_running(running.entry.id, 12)
    job = _ticket(ledger, 13, kind=EntryKind.JOB)
    waits = batch.queue_wait.QueueWaits(tmp_path)
    assert waits({11, 12, 13, 99}) == {11: queued.entry.id}
    assert job.entry.id not in waits({13}).values()


def test_an_unopenable_ledger_gives_empty_after_one_warning(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(core.log, "warn", lambda msg: seen.append(msg))
    blocked = tmp_path / "not-a-dir"
    blocked.write_text("x")
    waits = batch.queue_wait.QueueWaits(blocked)
    assert waits({1}) == {}
    assert len(seen) == 1
    assert waits({1}) == {}
    assert len(seen) == 1
