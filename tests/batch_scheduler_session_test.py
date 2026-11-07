"""Scheduler-on-session contract: drain, commit, cancel, interrupt, events."""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.events  # noqa: E402
import batch.model  # noqa: E402
import batch.plan  # noqa: E402
import batch.scheduler  # noqa: E402
import batch.store  # noqa: E402
from batch.model import CancelState, DecisionKind, RunStatus, Step  # noqa: E402
from batch.requests import Cancel, Malformed, Outcome, Release  # noqa: E402
from batch_scheduler_support import (  # noqa: E402,F401
    NEED, NO, Harness, _close_open_sessions, _quiet_outcomes, row,
)

ONLY_REVIEW = {Step.REBASE: NO, Step.COMMENTS: NO, Step.REVIEW: NEED}
ONLY_REBASE = {Step.REBASE: NEED, Step.COMMENTS: NO, Step.REVIEW: NO}


def _new_run(*rows):
    return batch.scheduler.new_run(list(rows), steps=list(batch.model.STEP_ORDER),
                                   selected=None, pool=1)


def _recorded(run, outcome):
    events = []
    batch.scheduler.emit_outcome(run, outcome,
                                 emit=lambda kind, **fields: events.append((kind, fields)))
    return events


def test_interrupt_with_a_recorded_cancel_ends_cancelled():
    h = Harness([row(1)])

    def interrupt(_seconds):
        # A step is live after the first tick; the cancel lands, then Ctrl-C.
        h.run.cancel = CancelState(kill=False, requested_at="t0")
        raise KeyboardInterrupt()

    h.sched._sleep = interrupt
    with pytest.raises(KeyboardInterrupt):
        h.sched.run_until_blocked()
    assert h.run.status is RunStatus.CANCELLED
    assert batch.store.load(h.run.id).status is RunStatus.CANCELLED
    finished = [f for k, f in h.events if k == "run_finished"]
    assert finished and finished[-1]["status"] == "cancelled"


def test_settle_does_not_drop_refs_itself_and_each_terminal_commit_drops_once(
        monkeypatch):
    h = Harness([row(1)], replan=lambda r: None)
    dropped = []
    monkeypatch.setattr(batch.plan, "drop_refs",
                        lambda dirs, ns: dropped.append((dirs, ns)))
    commits = []
    real = h.session.commit

    def wrapped():
        before = len(dropped)
        real()
        commits.append((h.run.status, len(dropped) - before))

    h.session.commit = wrapped
    assert h.sched.run_until_blocked() is RunStatus.DONE
    terminal = [n for status, n in commits
                if status in (RunStatus.DONE, RunStatus.CANCELLED)]
    assert terminal and all(n == 1 for n in terminal)


def test_a_cancel_drained_mid_loop_emits_cancel_requested():
    h = Harness([row(1, {Step.REBASE: NEED, Step.COMMENTS: NO, Step.REVIEW: NO})])
    batch.store.write_request(h.run.id, Cancel())
    assert h.sched.run_until_blocked() is RunStatus.CANCELLED
    requested = [f for k, f in h.events if k == "cancel_requested"]
    assert requested and requested[-1]["run"] == h.run.id
    assert requested[-1]["kill"] is False


def test_the_scheduler_never_calls_store_save(monkeypatch):
    h = Harness([row(1)], replan=lambda r: None)

    def boom(run):
        raise AssertionError("scheduler called batch.store.save")

    # raising=False: store.save may no longer exist, and a reintroduced call must
    # still land here rather than be an AttributeError somewhere else.
    monkeypatch.setattr(batch.store, "save", boom, raising=False)
    assert h.sched.run_until_blocked() is RunStatus.DONE


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


