"""apply_one, settle_idle, Inbox, and deliver."""

import contextlib
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.inbox  # noqa: E402
import batch.model  # noqa: E402
import batch.store  # noqa: E402
import core.run_lock  # noqa: E402
from batch.inbox import ApplyContext, Inbox  # noqa: E402
from batch.model import (  # noqa: E402
    Decision, DecisionKind, Item, ItemStatus, Run, RunStatus, Step, StepRecord,
)
from batch.publish import TreeState  # noqa: E402
from batch.requests import Cancel, Envelope, Malformed, Outcome, Release  # noqa: E402
from batch.store import Role  # noqa: E402
from batch_session_support import holding, open_session, seed  # noqa: E402
from pr.comments_fix import CloseoutDebt  # noqa: E402
from rebase.types import RefDivergence  # noqa: E402


def _run(*decisions, status=RunStatus.WAITING):
    item = Item(
        key="o/r#1", repo="o/r", repo_dir="/r", pr=1, branch="b", head_sha="s",
        worktree="/wt", status=ItemStatus.AWAITING_DECISION,
        steps=[StepRecord(s) for s in (Step.REBASE, Step.REVIEW)])
    return Run(
        id="r1", started_at="t", steps=[Step.REBASE, Step.REVIEW], pool=1,
        auto_publish=[], status=status, items=[item], decisions=list(decisions))


def _d(kind, step, payload=None, id="d1"):
    return Decision(id=id, item="o/r#1", step=step, kind=kind, payload=payload or {})


class Recorder:
    def __init__(self, code=0):
        self.calls, self.code = [], code

    def __call__(self, argv, log_path=None):
        self.calls.append(argv)
        return self.code


def test_apply_one_cancel_records_run_cancel():
    run = _run()
    out = batch.inbox.apply_one(run, Cancel(), ApplyContext())
    assert out.ok
    assert run.cancel is not None
    assert run.cancel.kill is False
    assert run.cancel.requested_at


def test_apply_one_cancel_kill_only_upgrades(monkeypatch):
    times = iter(["t1", "t2"])
    monkeypatch.setattr(batch.store, "now_iso", lambda: next(times))
    run = _run()
    ctx = ApplyContext()
    batch.inbox.apply_one(run, Cancel(), ctx)
    assert run.cancel.requested_at == "t1"
    assert run.cancel.kill is False
    batch.inbox.apply_one(run, Cancel(kill=True), ctx)
    assert run.cancel.kill is True
    assert run.cancel.requested_at == "t1"
    batch.inbox.apply_one(run, Cancel(), ctx)
    assert run.cancel.kill is True
    assert run.cancel.requested_at == "t1"


def test_apply_one_replay_of_the_same_cancel_leaves_cancel_unchanged(monkeypatch):
    times = iter(["t1", "t2"])
    monkeypatch.setattr(batch.store, "now_iso", lambda: next(times))
    run = _run()
    ctx = ApplyContext()
    first = batch.inbox.apply_one(run, Cancel(), ctx, name="c.json")
    recorded = run.cancel
    second = batch.inbox.apply_one(run, Cancel(), ctx, name="c.json")
    assert first.ok and second.ok and not second.error
    assert run.cancel == recorded
    assert run.cancel.requested_at == "t1"
    assert run.cancel.kill is False


def test_apply_one_cancel_on_a_done_run_is_an_error():
    run = _run(status=RunStatus.DONE)
    out = batch.inbox.apply_one(run, Cancel(kill=True), ApplyContext())
    assert not out.ok
    assert out.error == "run r1 is done"
    assert run.cancel is None


def test_apply_one_cancel_on_a_cancelled_run_is_ok_and_changes_nothing():
    run = _run(status=RunStatus.CANCELLED)
    run.cancel = batch.model.CancelState(kill=False, requested_at="t0")
    out = batch.inbox.apply_one(run, Cancel(kill=True), ApplyContext())
    assert out.ok
    assert run.cancel == batch.model.CancelState(kill=False, requested_at="t0")


def test_apply_one_release_on_a_done_run_is_an_error():
    run = _run(status=RunStatus.DONE)
    out = batch.inbox.apply_one(run, Release("o/r#1"), ApplyContext())
    assert not out.ok
    assert out.error == "run r1 is done"
    assert run.items[0].status is ItemStatus.AWAITING_DECISION


def test_apply_one_release_unknown_item_is_an_error():
    run = _run()
    out = batch.inbox.apply_one(run, Release("o/r#99"), ApplyContext())
    assert not out.ok
    assert out.error == "run r1 has no item o/r#99"


def test_apply_one_release_of_a_terminal_item_is_an_error():
    run = _run()
    run.items[0].status = ItemStatus.DROPPED
    out = batch.inbox.apply_one(run, Release("o/r#1"), ApplyContext())
    assert not out.ok
    assert out.error == "o/r#1 is already dropped"


def test_apply_one_release_of_a_running_item_without_kill_is_an_error():
    run = _run()
    run.items[0].status = ItemStatus.RUNNING
    out = batch.inbox.apply_one(run, Release("o/r#1"), ApplyContext())
    assert not out.ok
    assert "pass --kill" in out.error
    assert run.items[0].status is ItemStatus.RUNNING


def test_apply_one_release_drops_the_item_and_resolves_only_its_open_decisions(monkeypatch):
    other = Item(
        key="o/r#2", repo="o/r", repo_dir="/r", pr=2, branch="c", head_sha="s",
        worktree="/wt2", status=ItemStatus.AWAITING_DECISION)
    mine = _d(DecisionKind.DIRTY_WORKTREE, "worktree")
    theirs = _d(DecisionKind.FAILED, "review", id="d2")
    theirs.item = "o/r#2"
    closed = _d(DecisionKind.INTERRUPTED, "rebase", id="d0")
    closed.resolution = "retry"
    closed.resolved_at = "t0"
    run = _run(mine, closed, theirs)
    run.items.append(other)
    monkeypatch.setattr(batch.store, "now_iso", lambda: "t-rel")
    out = batch.inbox.apply_one(run, Release("o/r#1"), ApplyContext())
    assert out.ok and out.finished == ("o/r#1",)
    assert run.items[0].status is ItemStatus.DROPPED
    assert run.items[0].wait_reason == ""
    assert run.decision("d1").resolution == "released"
    assert run.decision("d1").resolved_at == "t-rel"
    assert run.decision("d0").resolution == "retry"
    assert run.decision("d2").open
    assert run.items[1].status is ItemStatus.AWAITING_DECISION


def test_apply_one_release_of_a_running_item_with_kill_drops_it():
    run = _run(_d(DecisionKind.FAILED, "review"))
    run.items[0].status = ItemStatus.RUNNING
    out = batch.inbox.apply_one(
        run, Release("o/r#1", kill=True), ApplyContext())
    assert out.ok and out.finished == ("o/r#1",)
    assert run.items[0].status is ItemStatus.DROPPED
    assert run.decision("d1").resolution == "released"


def test_settle_idle_cancel_wins_over_every_item_terminal():
    run = _run()
    run.cancel = batch.model.CancelState()
    run.items[0].status = ItemStatus.DONE
    assert batch.inbox.settle_idle(run) is True
    assert run.status is RunStatus.CANCELLED


def test_settle_idle_promotes_waiting_to_done_when_every_item_is_terminal():
    run = _run()
    run.items[0].status = ItemStatus.DONE
    assert batch.inbox.settle_idle(run) is True
    assert run.status is RunStatus.DONE


def test_settle_idle_does_not_promote_cancelled_to_done():
    run = _run(status=RunStatus.CANCELLED)
    run.cancel = batch.model.CancelState()
    run.items[0].status = ItemStatus.DONE
    assert batch.inbox.settle_idle(run) is False
    assert run.status is RunStatus.CANCELLED


def test_settle_idle_leaves_waiting_when_an_item_is_open():
    run = _run()
    assert batch.inbox.settle_idle(run) is False
    assert run.status is RunStatus.WAITING


def test_inbox_settle_delegates_to_settle_idle():
    run = _run()
    run.items[0].status = ItemStatus.DROPPED
    assert Inbox(ApplyContext()).settle(run) is True
    assert run.status is RunStatus.DONE


def test_inbox_report_delegates_to_context_with_the_run():
    seen = []
    inbox = Inbox(ApplyContext(report=lambda run, o: seen.append((run, o))))
    run = _run()
    outcome = Outcome(name="n", request=Cancel())
    inbox.report(run, outcome)
    assert seen == [(run, outcome)]


def test_inbox_apply_turns_a_malformed_request_into_its_error():
    out = Inbox(ApplyContext()).apply(_run(), Envelope("m.json", Malformed("bad")))
    assert out == Outcome(name="m.json", request=Malformed("bad"), error="bad")


# ── deliver ───────────────────────────────────────────────────────────────────

def _ctx():
    return ApplyContext()


def test_deliver_queues_for_a_live_holder_that_drains():
    run = seed(_run(_d(DecisionKind.DIRTY_WORKTREE, "worktree")))
    with holding(run.id, Role.DRIVER):
        delivery = batch.inbox.deliver(run.id, Cancel(), _ctx(),
                                       command="pr batch cancel")
    assert delivery.queued is True and delivery.own is None and delivery.others == ()
    assert delivery.holder.role is Role.DRIVER
    assert [e.name for e in batch.store.peek_requests(run.id)] == [delivery.name]
    assert batch.store.load(run.id).decision("d1").open


def test_deliver_waits_out_a_gc_holder_and_then_applies(monkeypatch):
    run = seed(_run(_d(DecisionKind.DIRTY_WORKTREE, "worktree")))
    monkeypatch.setattr(core.run_lock, "retry_delays", lambda: iter([0.0, 0.0, 0.0]))
    paused = []
    with contextlib.ExitStack() as gc:
        gc.enter_context(holding(run.id, Role.GC))

        def pause(seconds):
            paused.append(seconds)
            gc.close()

        monkeypatch.setattr(batch.inbox.time, "sleep", pause)
        delivery = batch.inbox.deliver(run.id, Cancel(), _ctx(),
                                       command="pr batch cancel")
    assert paused == [0.0]
    assert delivery.queued is False and delivery.own.ok


def test_deliver_gives_up_on_a_gc_holder_that_stays(monkeypatch):
    run = seed(_run(_d(DecisionKind.DIRTY_WORKTREE, "worktree")))
    monkeypatch.setattr(core.run_lock, "retry_delays", lambda: iter([0.0, 0.0]))
    paused = []
    monkeypatch.setattr(batch.inbox.time, "sleep", paused.append)
    with holding(run.id, Role.GC), pytest.raises(batch.store.RunUnreachable) as exc:
        batch.inbox.deliver(run.id, Cancel(), _ctx(), command="pr batch cancel")
    assert paused == [0.0, 0.0]
    assert "gc holder" in str(exc.value)
    assert batch.store.pending_requests(run.id) == 1


def test_deliver_gives_up_on_an_unreadable_holder_record(monkeypatch):
    run = seed(_run(_d(DecisionKind.DIRTY_WORKTREE, "worktree")))
    monkeypatch.setattr(core.run_lock, "retry_delays", lambda: iter([0.0, 0.0]))
    paused = []
    monkeypatch.setattr(batch.inbox.time, "sleep", paused.append)
    monkeypatch.setattr(batch.store, "_holder_from", lambda record: None)
    with holding(run.id, Role.DRIVER), pytest.raises(batch.store.RunUnreachable) as exc:
        batch.inbox.deliver(run.id, Cancel(), _ctx(), command="pr batch cancel")
    assert paused == [0.0, 0.0]
    assert "unreadable holder record" in str(exc.value)
    assert "gc holder" not in str(exc.value)
    assert batch.store.pending_requests(run.id) == 1


def test_deliver_reports_a_cancel_that_reached_a_finished_run_as_its_error():
    run = seed(_run(status=RunStatus.DONE))
    delivery = batch.inbox.deliver(run.id, Cancel(), _ctx(), command="pr batch cancel")
    assert delivery.own.error == f"run {run.id} is done"
    assert [f.name for f in batch.store.failed_requests(run.id)] == [delivery.name]
    assert batch.store.load(run.id).status is RunStatus.DONE


def test_deliver_to_a_removed_run_is_not_found_and_recreates_nothing():
    missing = "20990101T000000-dead"
    with pytest.raises(batch.store.RunNotFound):
        batch.inbox.deliver(missing, Cancel(), _ctx(), command="pr batch cancel")
    assert not batch.store.run_dir(missing).exists()


def test_deliver_retries_run_not_found_after_a_successful_write(monkeypatch):
    run = seed(_run(_d(DecisionKind.DIRTY_WORKTREE, "worktree")))
    monkeypatch.setattr(core.run_lock, "retry_delays", lambda: iter([0.0]))
    paused = []
    real = batch.store.session
    calls = {"n": 0}

    @contextlib.contextmanager
    def once_missing(run_id, role, *, inbox, command):
        calls["n"] += 1
        if calls["n"] == 1:
            raise batch.store.RunNotFound(run_id)
        with real(run_id, role, inbox=inbox, command=command) as opened:
            yield opened

    monkeypatch.setattr(batch.store, "session", once_missing)
    monkeypatch.setattr(batch.inbox.time, "sleep", paused.append)
    delivery = batch.inbox.deliver(run.id, Cancel(), _ctx(),
                                   command="pr batch cancel")
    assert paused == [0.0]
    assert delivery.queued is False and delivery.own.ok
    assert batch.store.load(run.id).items[0].status is ItemStatus.DROPPED


def test_deliver_exhausted_run_not_found_stays_not_found(monkeypatch):
    run = seed(_run(_d(DecisionKind.DIRTY_WORKTREE, "worktree")))
    monkeypatch.setattr(core.run_lock, "retry_delays", lambda: iter([0.0]))
    monkeypatch.setattr(batch.inbox.time, "sleep", lambda seconds: None)

    @contextlib.contextmanager
    def gone(run_id, role, *, inbox, command):
        raise batch.store.RunNotFound(run_id)
        yield

    monkeypatch.setattr(batch.store, "session", gone)
    with pytest.raises(batch.store.RunNotFound):
        batch.inbox.deliver(run.id, Cancel(), _ctx(),
                            command="pr batch cancel")
    assert batch.store.pending_requests(run.id) == 1
