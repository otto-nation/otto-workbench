"""apply_one, settle_idle, Inbox, and deliver."""

import contextlib
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.inbox  # noqa: E402
import batch.model  # noqa: E402
import batch.resolve  # noqa: E402
import batch.store  # noqa: E402
from batch.inbox import ApplyContext, Inbox  # noqa: E402
from batch.model import (  # noqa: E402
    Decision, DecisionKind, Item, ItemStatus, Run, RunStatus, Step, StepRecord,
    StepStatus,
)
from batch.requests import Cancel, Decide, Envelope, Malformed, Outcome  # noqa: E402
import core.run_lock  # noqa: E402
from batch.store import Role  # noqa: E402
from batch_session_support import seed  # noqa: E402
from run_lock_support import held_elsewhere  # noqa: E402
from batch.publish import TreeState  # noqa: E402
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


def test_apply_one_decide_runs_resolve():
    # abort is an action whose command_for builds argv; accept builds none.
    run = _run(_d(DecisionKind.REBASE_CONFLICT, "rebase"))
    rec = Recorder()
    out = batch.inbox.apply_one(
        run, Decide("d1", "abort"), ApplyContext(pr_bin="pr", runner=rec),
        name="a.json")
    assert out.ok and out.name == "a.json"
    assert rec.calls
    assert run.decision("d1").resolution == "abort"
    assert out.created == ()


def test_apply_one_decide_lists_the_item_when_it_becomes_terminal():
    run = _run(_d(DecisionKind.DIRTY_WORKTREE, "worktree"))
    out = batch.inbox.apply_one(
        run, Decide("d1", "drop-pr"), ApplyContext(pr_bin="pr", runner=Recorder()))
    assert run.items[0].status is ItemStatus.DROPPED
    assert out.finished == ("o/r#1",)


def test_apply_one_replay_of_the_same_action_does_not_rerun_the_runner():
    run = _run(_d(DecisionKind.REBASE_CONFLICT, "rebase"))
    rec = Recorder()
    ctx = ApplyContext(pr_bin="pr", runner=rec)
    first = batch.inbox.apply_one(run, Decide("d1", "abort"), ctx)
    n = len(rec.calls)
    replay = batch.inbox.apply_one(run, Decide("d1", "abort"), ctx)
    assert first.ok and replay.ok and not replay.error
    assert n == 1
    assert len(rec.calls) == n


def test_apply_one_replay_of_an_adopted_publish_does_not_rerun_the_runner():
    # Real `_publish` / `_adopt_own_push`: first tree is even (nothing to push),
    # closeout still runs `--finish`, second tree has remote == local != lease.
    run = _run(_d(DecisionKind.PUBLISH, "publish", {"drafted": [], "track": []}))
    rec = Recorder()
    item = run.items[0]
    planned = item.remote_sha
    reads = iter([
        TreeState(local=planned, remote=planned,
                  divergence=RefDivergence(ahead=0, behind=0, comparable=True)),
        TreeState(local="held", remote="held",
                  divergence=RefDivergence(ahead=0, behind=0, comparable=True)),
    ])
    ctx = ApplyContext(
        pr_bin="pr", runner=rec, tree=lambda i: next(reads),
        closeout=lambda d, b: CloseoutDebt(summary=True))
    first = batch.inbox.apply_one(run, Decide("d1", "publish"), ctx, name="p.json")
    assert first.ok and not first.error
    assert planned != "held"
    assert (item.remote_sha, item.published_sha) == ("held", "held")
    assert rec.calls
    n = len(rec.calls)
    replay = batch.inbox.apply_one(run, Decide("d1", "publish"), ctx, name="p.json")
    assert replay.ok and not replay.error
    assert len(rec.calls) == n
    assert run.decision("d1").resolution == "publish"


def test_apply_one_unknown_decision_is_an_error_outcome():
    run = _run()
    out = batch.inbox.apply_one(
        run, Decide("nope", "retry"), ApplyContext(pr_bin="pr", runner=Recorder()))
    assert not out.ok
    assert "nope" in out.error


def test_apply_one_cancel_records_run_cancel():
    run = _run()
    out = batch.inbox.apply_one(run, Cancel(), ApplyContext(pr_bin="pr"))
    assert out.ok
    assert run.cancel is not None
    assert run.cancel.kill is False
    assert run.cancel.requested_at


def test_apply_one_cancel_kill_only_upgrades(monkeypatch):
    times = iter(["t1", "t2"])
    monkeypatch.setattr(batch.store, "now_iso", lambda: next(times))
    run = _run()
    ctx = ApplyContext(pr_bin="pr")
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
    ctx = ApplyContext(pr_bin="pr")
    first = batch.inbox.apply_one(run, Cancel(), ctx, name="c.json")
    recorded = run.cancel
    second = batch.inbox.apply_one(run, Cancel(), ctx, name="c.json")
    assert first.ok and second.ok and not second.error
    assert run.cancel == recorded
    assert run.cancel.requested_at == "t1"
    assert run.cancel.kill is False


def test_apply_one_cancel_on_a_done_run_is_an_error():
    run = _run(status=RunStatus.DONE)
    out = batch.inbox.apply_one(run, Cancel(kill=True), ApplyContext(pr_bin="pr"))
    assert not out.ok
    assert out.error == "run r1 is done"
    assert run.cancel is None


def test_apply_one_cancel_on_a_cancelled_run_is_ok_and_changes_nothing():
    run = _run(status=RunStatus.CANCELLED)
    run.cancel = batch.model.CancelState(kill=False, requested_at="t0")
    out = batch.inbox.apply_one(run, Cancel(kill=True), ApplyContext(pr_bin="pr"))
    assert out.ok
    assert run.cancel == batch.model.CancelState(kill=False, requested_at="t0")


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


def test_inbox_apply_delegates_to_apply_one():
    run = _run(_d(DecisionKind.DIRTY_WORKTREE, "worktree"))
    inbox = Inbox(ApplyContext(pr_bin="pr", runner=Recorder()))
    out = inbox.apply(run, Envelope("x.json", Decide("d1", "drop-pr")))
    assert out.ok and out.name == "x.json"
    assert run.items[0].status is ItemStatus.DROPPED


def test_inbox_settle_delegates_to_settle_idle():
    run = _run()
    run.items[0].status = ItemStatus.DROPPED
    assert Inbox(ApplyContext(pr_bin="pr")).settle(run) is True
    assert run.status is RunStatus.DONE


def test_inbox_report_delegates_to_context_with_the_run():
    seen = []
    inbox = Inbox(ApplyContext(pr_bin="pr", report=lambda run, o: seen.append((run, o))))
    run = _run()
    outcome = Outcome(name="n", request=Cancel())
    inbox.report(run, outcome)
    assert seen == [(run, outcome)]


def test_inbox_apply_turns_a_malformed_request_into_its_error():
    out = Inbox(ApplyContext(pr_bin="pr")).apply(_run(), Envelope("m.json", Malformed("bad")))
    assert out == Outcome(name="m.json", request=Malformed("bad"), error="bad")


# ── deliver ───────────────────────────────────────────────────────────────────

def _ctx():
    return ApplyContext(pr_bin="pr", runner=Recorder())


def _holding(run_id, role):
    """Hold *run_id*'s lock on a descriptor of our own, as a live *role* in another process."""
    record = {"pid": os.getppid(), "command": "pr batch x", "started": "t0",
              "role": role.value, "run": run_id}
    return held_elsewhere(batch.store.run_lock(run_id).path, record)


def test_deliver_applies_its_own_request_and_returns_the_earlier_ones_apart():
    run = seed(_run(_d(DecisionKind.DIRTY_WORKTREE, "worktree")))
    earlier = batch.store.write_request(run.id, Decide("nope", "retry"))
    delivery = batch.inbox.deliver(run.id, Decide("d1", "drop-pr"), _ctx(),
                                   command="pr batch resolve")
    assert delivery.queued is False and delivery.holder is None
    assert delivery.own.name == delivery.name and delivery.own.ok
    assert [o.name for o in delivery.others] == [earlier]
    assert "nope" in delivery.others[0].error
    assert batch.store.load(run.id).items[0].status is ItemStatus.DROPPED


def test_deliver_queues_for_a_live_holder_that_drains():
    run = seed(_run(_d(DecisionKind.DIRTY_WORKTREE, "worktree")))
    with _holding(run.id, Role.DRIVER):
        delivery = batch.inbox.deliver(run.id, Decide("d1", "drop-pr"), _ctx(),
                                       command="pr batch resolve")
    assert delivery.queued is True and delivery.own is None and delivery.others == ()
    assert delivery.holder.role is Role.DRIVER
    assert [e.name for e in batch.store.peek_requests(run.id)] == [delivery.name]
    assert batch.store.load(run.id).decision("d1").open


def test_deliver_waits_out_a_gc_holder_and_then_applies(monkeypatch):
    run = seed(_run(_d(DecisionKind.DIRTY_WORKTREE, "worktree")))
    monkeypatch.setattr(core.run_lock, "retry_delays", lambda: iter([0.0, 0.0, 0.0]))
    paused = []
    with contextlib.ExitStack() as gc:
        gc.enter_context(_holding(run.id, Role.GC))

        def pause(seconds):
            paused.append(seconds)
            gc.close()

        monkeypatch.setattr(batch.inbox.time, "sleep", pause)
        delivery = batch.inbox.deliver(run.id, Decide("d1", "drop-pr"), _ctx(),
                                       command="pr batch resolve")
    assert paused == [0.0]
    assert delivery.queued is False and delivery.own.ok


def test_deliver_gives_up_on_a_gc_holder_that_stays(monkeypatch):
    run = seed(_run(_d(DecisionKind.DIRTY_WORKTREE, "worktree")))
    monkeypatch.setattr(core.run_lock, "retry_delays", lambda: iter([0.0, 0.0]))
    paused = []
    monkeypatch.setattr(batch.inbox.time, "sleep", paused.append)
    with _holding(run.id, Role.GC), pytest.raises(batch.store.RunUnreachable):
        batch.inbox.deliver(run.id, Cancel(), _ctx(), command="pr batch cancel")
    assert paused == [0.0, 0.0]
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
