"""Request-outcome events: cancel and release."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.events  # noqa: E402
import batch.model  # noqa: E402
import batch.scheduler  # noqa: E402
from batch.requests import Cancel, Outcome, Release  # noqa: E402
from batch_scheduler_support import row  # noqa: E402


def _new_run(*rows):
    return batch.scheduler.new_run(list(rows), steps=list(batch.model.STEP_ORDER),
                                   selected=None, pool=1)


def _recorded(run, outcome):
    events = []
    batch.scheduler.emit_outcome(run, outcome,
                                 emit=lambda kind, **fields: events.append((kind, fields)))
    return events


def test_cancel_requested_is_a_known_event_kind(capsys):
    batch.events.emit("cancel_requested", run="r1", kill=True)
    line = capsys.readouterr().out
    assert "cancel_requested" in line


def test_emit_outcome_reports_a_cancel_with_its_kill_flag():
    run = _new_run(row(1))
    assert _recorded(run, Outcome(name="c.json", request=Cancel(kill=True))) == [
        ("cancel_requested", {"run": run.id, "kill": True})]


def test_emit_outcome_reports_a_release_as_item_finished():
    run = _new_run(row(1))
    run.items[0].status = batch.model.ItemStatus.DROPPED
    assert _recorded(run, Outcome(name="r.json", request=Release("o/r#1"),
                                  finished=("o/r#1",))) == [
        ("item_finished", {"run": run.id, "item": "o/r#1", "status": "dropped"})]
