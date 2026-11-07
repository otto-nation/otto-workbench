import contextlib
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.admission  # noqa: E402
import batch.events  # noqa: E402
import batch.model  # noqa: E402
import batch.scheduler  # noqa: E402
import batch.store  # noqa: E402
import batch.targets  # noqa: E402
from batch.requests import Cancel, Envelope, Malformed, Release  # noqa: E402
from batch.plan import PlanError  # noqa: E402
from batch.steps import WorktreeResult  # noqa: E402
from batch_scheduler_support import (ALL, GiB, HEALTHY, NEED, NO, Harness,  # noqa: F401,E402
                                     _close_open_sessions, _even, _quiet_outcomes, row)
from pr.comments_fix import CloseoutDebt  # noqa: E402


def test_pool_limit_is_never_exceeded():
    h = Harness([row(n) for n in range(1, 6)], pool=2)
    h.sched.run_until_blocked()
    assert h.max_live == 2


def test_blocked_pr_frees_its_slot_for_the_others():
    h = Harness([row(1), row(2), row(3)], pool=1, codes={("rebase", "/wt/b1"): 3})
    assert h.sched.run_until_blocked() is batch.model.RunStatus.DONE
    assert h.run.item("o/r#1").status is batch.model.ItemStatus.NEEDS_SESSION
    assert any(a[1] == "review" and a[-1] == "/wt/b3" for a in h.spawned)


def test_local_head_move_reenables_review():
    rows = [row(1, {batch.model.Step.REBASE: NO, batch.model.Step.COMMENTS: NEED, batch.model.Step.REVIEW: NO})]
    h = Harness(rows)
    real_spawn = h._spawn

    def spawn(argv, **kw):
        if argv[1] == "comments":
            h.heads["/wt/b1"] = "h1"
        return real_spawn(argv, **kw)

    h.sched._spawn = spawn
    h.sched.run_until_blocked()
    assert [a[1] for a in h.spawned] == ["comments", "review"]
    assert h.run.items[0].head_moved


def test_confirm_hands_replan_the_worktree_head():
    seen = []

    def replan(r):
        seen.append(r.local_head)
        return r

    h = Harness([row(1)], replan=replan, heads={"/wt/b1": "local1"})
    h.sched.run_until_blocked()
    assert seen and set(seen) == {"local1"}


def test_explicit_selection_runs_even_when_replan_says_not_needed():
    done = {batch.model.Step.REBASE: NO, batch.model.Step.COMMENTS: NO, batch.model.Step.REVIEW: NO}
    h = Harness([row(1, done)], selected={"o/r#1": [batch.model.Step.REVIEW]},
                replan=lambda r: row(1, done))
    h.sched.run_until_blocked()
    assert [a[1] for a in h.spawned] == ["review"]


def test_unselected_step_is_still_skipped_when_replan_says_not_needed():
    done = {batch.model.Step.REBASE: NO, batch.model.Step.COMMENTS: NO, batch.model.Step.REVIEW: NO}
    h = Harness([row(1, ALL)], replan=lambda r: row(1, done))
    h.sched.run_until_blocked()
    assert h.spawned == []


def test_explicit_selection_still_skips_a_closed_pr():
    h = Harness([row(1)], selected={"o/r#1": [batch.model.Step.REVIEW]}, replan=lambda r: None)
    h.sched.run_until_blocked()
    assert h.run.items[0].status is batch.model.ItemStatus.SKIPPED_CLOSED
    assert h.spawned == []


def test_closed_pr_is_skipped_without_running_anything():
    h = Harness([row(1)], replan=lambda r: None)
    assert h.sched.run_until_blocked() is batch.model.RunStatus.DONE
    assert h.run.items[0].status is batch.model.ItemStatus.SKIPPED_CLOSED
    assert h.spawned == []


def test_dirty_worktree_becomes_a_decision():
    h = Harness([row(1)], worktrees=lambda d, b: WorktreeResult("/wt/b1", True, ""))
    assert h.sched.run_until_blocked() is batch.model.RunStatus.DONE
    assert h.run.items[0].status is batch.model.ItemStatus.NEEDS_SESSION
    assert h.run.items[0].findings[0].reason is batch.model.FindingReason.DIRTY_WORKTREE
    assert h.spawned == []


def test_cancel_before_start_runs_nothing():
    h = Harness([row(1)])
    batch.store.write_request(h.run.id, Cancel())
    assert h.sched.run_until_blocked() is batch.model.RunStatus.CANCELLED
    assert h.spawned == []


def test_unrealised_headroom_blocks_a_second_admit_in_the_same_tick():
    estimate = batch.admission.DEFAULT_ESTIMATES[batch.model.Step.REBASE]
    host = batch.admission.HostSample(2 * GiB + estimate, 1.0, 0.0)
    h = Harness([row(1), row(2)], host=host, pool=2)
    h.sched._sleep = lambda _: (_ for _ in ()).throw(KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        h.sched.run_until_blocked()
    assert len(h.spawned) == 1


def test_admission_wait_is_reported_and_the_floor_still_progresses():
    short = batch.admission.HostSample(2 * GiB, 1.0, 0.0)
    # Two repos, so the one-fetcher-per-repo rule cannot be what holds row 2 back.
    h = Harness([row(1), row(2, repo="o/s", repo_dir="/s")], host=short)
    assert h.sched.run_until_blocked() is batch.model.RunStatus.DONE
    waits = [f for k, f in h.events if k == "admission_wait"]
    assert waits and waits[0]["reason"].startswith("waiting for memory")
    assert h.max_live == 1


def test_mid_loop_drains_a_queued_request_written_to_disk():
    # Not a process-restart test: `h.sched`/`h.run` stay the same in-memory
    # objects across both run_until_blocked() calls below. What this checks is
    # that a request written to the store mid-run gets picked up and applied
    # without a fresh Scheduler being constructed.
    h = Harness([row(1, {batch.model.Step.REBASE: NEED, batch.model.Step.COMMENTS: NO,
                         batch.model.Step.REVIEW: NO})])
    h.sched._sleep = lambda _: (_ for _ in ()).throw(KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        h.sched.run_until_blocked()
    batch.store.write_request(h.run.id, Cancel())
    assert h.sched.run_until_blocked() is batch.model.RunStatus.CANCELLED
    assert "cancel_requested" in h.kinds()


def test_new_run_selects_needed_steps_and_drops_rows_with_none():
    rows = [row(1, {batch.model.Step.REBASE: NO, batch.model.Step.COMMENTS: NEED, batch.model.Step.REVIEW: NO}),
            row(2, {batch.model.Step.REBASE: NO, batch.model.Step.COMMENTS: NO, batch.model.Step.REVIEW: NO})]
    run = batch.scheduler.new_run(rows, steps=list(batch.model.STEP_ORDER), selected=None, pool=2)
    assert [i.key for i in run.items] == ["o/r#1"]
    assert [(s.step, s.status) for s in run.items[0].steps] == [
        (batch.model.Step.REBASE, batch.model.StepStatus.SKIPPED),
        (batch.model.Step.CI, batch.model.StepStatus.SKIPPED),
        (batch.model.Step.COMMENTS, batch.model.StepStatus.PENDING),
        (batch.model.Step.REVIEW, batch.model.StepStatus.SKIPPED)]


def test_new_run_honours_explicit_selection():
    run = batch.scheduler.new_run([row(1, {batch.model.Step.REBASE: NO, batch.model.Step.COMMENTS: NO, batch.model.Step.REVIEW: NO})],
                      steps=list(batch.model.STEP_ORDER), selected={"o/r#1": [batch.model.Step.REVIEW]}, pool=1)
    assert run.items[0].step(batch.model.Step.REVIEW).status is batch.model.StepStatus.PENDING


def test_new_run_marks_only_selected_steps_explicit():
    run = batch.scheduler.new_run([row(1)], steps=list(batch.model.STEP_ORDER),
                                  selected={"o/r#1": [batch.model.Step.REVIEW]}, pool=1)
    assert [(s.step, s.explicit) for s in run.items[0].steps] == [
        (batch.model.Step.REBASE, False), (batch.model.Step.CI, False),
        (batch.model.Step.COMMENTS, False), (batch.model.Step.REVIEW, True)]
    planned = batch.scheduler.new_run([row(1)], steps=list(batch.model.STEP_ORDER),
                                      selected=None, pool=1)
    assert not any(s.explicit for s in planned.items[0].steps)


def test_events_are_ndjson_with_schema_version(capsys):
    batch.events.emit("run_started", run="r1")
    line = json.loads(capsys.readouterr().out.strip())
    assert line["schema_version"] == 1 and line["kind"] == "run_started" and line["run"] == "r1"


def test_unknown_event_kind_is_refused():
    with pytest.raises(ValueError):
        batch.events.emit("nope")


def test_replan_runs_only_after_admission():
    calls = []

    def replan(r):
        calls.append(r.key)
        return r

    h = Harness([row(1), row(2), row(3)], pool=1, replan=replan)
    h.sched.run_until_blocked()
    assert len(h.spawned) == 9
    assert len(calls) == len(h.spawned)


def test_plan_error_fails_one_item_and_others_progress():
    n = {"n": 0}

    def replan(r):
        n["n"] += 1
        if n["n"] == 1:
            raise PlanError("graphql 502")
        return r

    h = Harness([row(1), row(2)], replan=replan)
    assert h.sched.run_until_blocked() is batch.model.RunStatus.DONE
    failed = [f for f in h.run.item("o/r#1").findings
              if f.reason is batch.model.FindingReason.FAILED_UNKNOWN]
    assert len(failed) == 1
    assert failed[0].data.get("reason") == "github"
    assert "graphql 502" in failed[0].detail
    assert h.run.item("o/r#1").status is batch.model.ItemStatus.NEEDS_SESSION
    assert any(a[-1] == "/wt/b2" for a in h.spawned)
    assert not any(a[-1] == "/wt/b1" for a in h.spawned)


def test_unreadable_request_file_does_not_crash_the_run():
    h = Harness([row(1, {batch.model.Step.REBASE: NEED, batch.model.Step.COMMENTS: NO,
                         batch.model.Step.REVIEW: NO})])
    reqs = batch.store.run_dir(h.run.id) / "requests"
    reqs.mkdir(parents=True, exist_ok=True)
    (reqs / "20261001T000000000000-bad.json").write_text("not json")
    batch.store.write_request(h.run.id, Cancel())
    assert h.sched.run_until_blocked() is batch.model.RunStatus.CANCELLED
    failed = batch.store.failed_requests(h.run.id)
    assert failed and "not an object" in failed[0].error


def test_malformed_request_is_reported_and_the_rest_apply():
    # Revert: emit decision_resolved again.
    h = Harness([row(1, {batch.model.Step.REBASE: NEED, batch.model.Step.COMMENTS: NO,
                         batch.model.Step.REVIEW: NO})])
    reqs = batch.store.run_dir(h.run.id) / "requests"
    reqs.mkdir(parents=True, exist_ok=True)
    (reqs / "20261001T000000000000-decide.json").write_text(
        json.dumps({"kind": "decide", "decision": "d1", "action": "retry"}))
    batch.store.write_request(h.run.id, Cancel())
    assert h.sched.run_until_blocked() is batch.model.RunStatus.CANCELLED
    failed = batch.store.failed_requests(h.run.id)
    assert any("pr batch resolve was removed" in f.error for f in failed)
    assert "decision_resolved" not in h.kinds()
    assert "cancel_requested" in h.kinds()


def _chatty(h):
    """Make every spawned step print one stderr line."""
    inner = h.sched._spawn

    def spawn(argv, **kw):
        proc = inner(argv, **kw)
        lines = ["working"]
        proc.drain_lines = lambda: [lines.pop()] if lines else []
        return proc

    h.sched._spawn = spawn


def test_step_log_lines_stay_off_the_event_stream_unless_verbose():
    quiet = Harness([row(1, {batch.model.Step.REBASE: NEED})], codes={("rebase", "/wt/b1"): 1})
    _chatty(quiet)
    quiet.sched.run_until_blocked()
    assert "step_log" not in quiet.kinds()
    failed = next(f for f in quiet.run.items[0].findings
                  if f.reason is batch.model.FindingReason.FAILED_UNKNOWN)
    assert failed.log_path.endswith("/logs/o__r-1-rebase-0.log")

    loud = Harness([row(1, {batch.model.Step.REBASE: NEED})], verbose=True)
    _chatty(loud)
    loud.sched.run_until_blocked()
    assert [f["line"] for k, f in loud.events if k == "step_log"] == ["working"]


def test_cancel_kill_force_kills_what_the_tracked_step_already_outlived():
    # The tracked step can die to the first kill() call in the same tick that
    # _reap() notices it is gone; once that happens the item leaves self._live
    # and nothing calls kill() on it again. Anything it left running in its
    # process group (a backgrounded child trapping SIGTERM, say) must still get
    # reached, so _reap() owes it one last, unconditional SIGKILL right there.
    h = Harness([row(1)])
    calls = []
    orig = h._spawn

    def spawn(argv, **kw):
        proc = orig(argv, **kw)
        proc.exited = False

        def poll():
            return 0 if proc.exited else None

        def kill(*, force=False):
            calls.append(force)
            proc.exited = True

        proc.poll = poll
        proc.kill = kill
        return proc

    h.sched._spawn = spawn
    h.sched._admit()
    assert h.spawned
    batch.store.write_request(h.run.id, Cancel(kill=True))
    h.sched._once()
    assert calls == [False, True]


def test_release_kill_of_a_live_step_does_not_requeue_the_item():
    """A killed step exits a tick later: its item's locks are given back only then,
    and the step's exit does not requeue the released item."""
    released = []
    h = Harness([row(1)])
    holds = batch.targets.ItemHolds()
    target = Path("/t")
    holds.take(target, ["o/r#1"], contextlib.nullcontext())

    def release(run, live):
        if holds.release_finished(run, live):
            released.append(["o/r#1"])

    h.sched._release = release
    orig = h._spawn

    def spawn(argv, **kw):
        proc = orig(argv, **kw)
        proc.state = "running"

        def poll():
            if proc.state == "killed":
                proc.state = "exited"
                return None
            return 143 if proc.state == "exited" else None

        def kill(*, force=False):
            if proc.state == "running":
                proc.state = "killed"

        proc.poll = poll
        proc.kill = kill
        return proc

    h.sched._spawn = spawn
    h.sched._admit()
    assert h.spawned
    assert h.run.items[0].status is batch.model.ItemStatus.RUNNING
    batch.store.write_request(h.run.id, Release("o/r#1", kill=True))
    h.sched._once()
    item = h.run.items[0]
    assert item.status is batch.model.ItemStatus.DROPPED
    assert not released and holds.held() == [target]
    h.sched._once()
    assert ["o/r#1"] in released and holds.held() == []
    assert item.status is batch.model.ItemStatus.DROPPED
    rec = next(r for r in item.steps if r.status is not batch.model.StepStatus.PENDING
               and r.status is not batch.model.StepStatus.SKIPPED)
    assert rec.status is batch.model.StepStatus.INTERRUPTED
    assert item.findings == []


def test_interrupt_kills_live_children_and_marks_the_run():
    h = Harness([row(1)])
    killed = []
    orig = h._spawn

    def spawn(argv, **kw):
        proc = orig(argv, **kw)
        inner = proc.kill

        def kill():
            killed.append(proc.pid)
            inner()

        proc.kill = kill
        return proc

    h.sched._spawn = spawn
    h.sched._sleep = lambda _: (_ for _ in ()).throw(KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        h.sched.run_until_blocked()
    assert killed == [1]
    assert h.run.status is batch.model.RunStatus.INTERRUPTED
    saved = batch.store.load(h.run.id)
    assert saved.status is batch.model.RunStatus.INTERRUPTED
    finished = [f for k, f in h.events if k == "run_finished"]
    assert finished and finished[-1]["status"] == "interrupted"
    assert h.run.items[0].status is batch.model.ItemStatus.QUEUED
    assert all(r.status is not batch.model.StepStatus.RUNNING for r in h.run.items[0].steps)


def test_interrupt_emit_failure_still_saves_and_reraises_original():
    h = Harness([row(1)])
    real = h.sched._emit

    def boom(kind, **fields):
        if kind == "run_finished" and fields.get("status") == "interrupted":
            raise RuntimeError("emit failed")
        real(kind, **fields)

    h.sched._emit = boom
    h.sched._sleep = lambda _: (_ for _ in ()).throw(KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        h.sched.run_until_blocked()
    saved = batch.store.load(h.run.id)
    assert saved.status is batch.model.RunStatus.INTERRUPTED
    assert saved.items[0].status is batch.model.ItemStatus.QUEUED


def test_settle_emit_failure_does_not_mark_interrupted():
    h = Harness([row(1)], worktrees=lambda d, b: WorktreeResult("/wt/b1", True, ""))
    real = h.sched._emit

    def boom(kind, **fields):
        if kind == "run_finished":
            raise RuntimeError("emit failed")
        real(kind, **fields)

    h.sched._emit = boom
    with pytest.raises(RuntimeError, match="emit failed"):
        h.sched.run_until_blocked()
    saved = batch.store.load(h.run.id)
    assert saved.status is batch.model.RunStatus.DONE
    assert saved.items[0].status is batch.model.ItemStatus.NEEDS_SESSION


def test_non_dict_request_is_reported_and_does_not_crash(monkeypatch):
    h = Harness([row(1)], worktrees=lambda d, b: WorktreeResult("/wt/b1", True, ""))
    h.sched.run_until_blocked()
    monkeypatch.setattr(batch.store, "peek_requests", lambda run_id: [
        Envelope("x.json", Malformed("not an object"))])
    monkeypatch.setattr(batch.store, "pending_requests", lambda run_id: 0)
    assert h.sched.run_until_blocked() is batch.model.RunStatus.DONE
    assert "decision_resolved" not in h.kinds()


def test_does_not_settle_while_requests_are_pending(monkeypatch):
    h = Harness([row(1)], worktrees=lambda d, b: WorktreeResult("/wt/b1", True, ""))
    checks = {"n": 0}

    def pending(run_id):
        checks["n"] += 1
        return 1 if checks["n"] == 1 else 0

    monkeypatch.setattr(batch.store, "pending_requests", pending)
    assert h.sched.run_until_blocked() is batch.model.RunStatus.DONE
    assert checks["n"] >= 2


def test_a_ci_step_runs_against_the_planned_remote_head():
    h = Harness([row(1, {batch.model.Step.CI: NEED})])
    h.sched.run_until_blocked()
    assert h.spawned == [["ci-check", "--fix", "--no-rebase", "--head-sha", "h",
                          "--repo-dir", "/wt/b1"]]


def test_resume_replays_the_run_s_focus():
    h = Harness([row(1)], focus=["a"])
    h.resume()
    h.sched.run_until_blocked()
    argv = next(a for a in h.spawned if a[1] == "review")
    assert "--focus=a" in argv


def test_a_failed_step_records_the_log_it_wrote():
    h = Harness([row(1)], codes={("rebase", "/wt/b1"): 1})
    h.sched.run_until_blocked()
    failed = next(f for f in h.run.items[0].findings
                  if f.reason is batch.model.FindingReason.FAILED_UNKNOWN)
    assert failed.data.get("reason") == "error"
    assert failed.log_path.endswith("/logs/o__r-1-rebase-0.log")


NONE_NEEDED = {batch.model.Step.REBASE: NO, batch.model.Step.COMMENTS: NO,
               batch.model.Step.REVIEW: NO}


def test_new_run_keeps_a_row_whose_only_work_is_an_owed_closeout():
    run = batch.scheduler.new_run([row(1, NONE_NEEDED, closeout="summary"), row(2, NONE_NEEDED)],
                                  steps=list(batch.model.STEP_ORDER), selected=None, pool=1)
    assert [i.key for i in run.items] == ["o/r#1"]
    assert all(r.status is batch.model.StepStatus.SKIPPED for r in run.items[0].steps)


def test_new_run_leaves_out_a_closeout_when_comments_is_not_a_chosen_step():
    run = batch.scheduler.new_run([row(1, NONE_NEEDED, closeout="summary")],
                                  steps=[batch.model.Step.REBASE], selected=None, pool=1)
    assert run.items == []


def test_new_run_leaves_out_a_closeout_when_selection_is_explicit():
    run = batch.scheduler.new_run([row(1, NONE_NEEDED, closeout="summary")],
                                  steps=list(batch.model.STEP_ORDER), selected={}, pool=1)
    assert run.items == []


def _owes_summary(d, b):
    return CloseoutDebt(summary=True)


def _closeout_row():
    return row(1, NONE_NEEDED, closeout="summary")


def test_no_drafted_work_and_no_debt_finishes_done_without_asking():
    h = Harness([row(1, NONE_NEEDED, closeout="")], tree=_even)
    assert h.sched.run_until_blocked() is batch.model.RunStatus.DONE
    assert h.run.decisions == []
