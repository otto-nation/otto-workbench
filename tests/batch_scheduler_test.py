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
import batch.outcomes  # noqa: E402
import batch.resolve  # noqa: E402
import batch.scheduler  # noqa: E402
import batch.store  # noqa: E402
from batch.plan import PlanError, PlanRow, StepNeed  # noqa: E402
from batch.steps import WorktreeResult  # noqa: E402
from config.workbench_config import BatchConfig  # noqa: E402

GiB = 1024 ** 3
NEED = StepNeed(True, "x")
NO = StepNeed(False, "y")
ALL = {batch.model.Step.REBASE: NEED, batch.model.Step.COMMENTS: NEED, batch.model.Step.REVIEW: NEED}
HEALTHY = batch.admission.HostSample(8 * GiB, 1.0, 0.0)


def row(n, needs=ALL):
    return PlanRow("o/r", "/r", n, f"t{n}", f"b{n}", "h", False, dict(needs))


@pytest.fixture(autouse=True)
def _quiet_outcomes(monkeypatch):
    monkeypatch.setattr(batch.outcomes, "comment_items", lambda item: [])
    monkeypatch.setattr(batch.outcomes, "open_findings", lambda item: [])


class Harness:
    def __init__(self, rows, *, codes=None, auto_publish=(), pool=2, host=HEALTHY,
                 replan=None, worktrees=None, heads=None, selected=None, cfg=None):
        self.codes = codes or {}
        self.spawned, self.events, self.live, self.max_live = [], [], 0, 0
        self.run = batch.scheduler.new_run(rows, steps=list(batch.model.STEP_ORDER), selected=selected, pool=pool,
                               auto_publish=list(auto_publish))
        self.heads = heads or {}
        self.sched = batch.scheduler.Scheduler(
            self.run, pr_bin="pr", cfg=cfg or BatchConfig(pool_max=4),
            host=lambda: host, spawn=self._spawn,
            replan=replan or (lambda r: r),
            worktrees=worktrees or (lambda d, b: WorktreeResult(f"/wt/{b}", False, "")),
            head=lambda wt: self.heads.get(wt, "h0"), rss=lambda pid: 0,
            emit=lambda kind, **f: self.events.append((kind, f)), sleep=lambda s: None,
            estimates=batch.admission.Estimates({}))

    def _spawn(self, argv, *, log_path, trail_root):
        h = self

        class Proc:
            pid = 1
            polls = 0

            def poll(self):
                self.polls += 1
                if self.polls < 2:
                    return None
                if not getattr(self, "done", False):
                    self.done = True
                    h.live -= 1
                return h.codes.get((argv[1], argv[-1]), 0)

            def drain_lines(self):
                return []

            def stdout(self):
                return "{}"

            def kill(self):
                pass

        self.live += 1
        self.max_live = max(self.max_live, self.live)
        self.spawned.append(argv)
        return Proc()

    def kinds(self):
        return [k for k, _ in self.events]


def test_draft_run_ends_waiting_with_one_publish_decision_per_pr():
    h = Harness([row(1), row(2)])
    assert h.sched.run_until_blocked() is batch.model.RunStatus.WAITING
    assert [a[1] for a in h.spawned if a[-1] == "/wt/b1"] == ["rebase", "comments", "review"]
    assert "--no-push" in h.spawned[0]
    pubs = [d for d in h.run.decisions if d.kind is batch.model.DecisionKind.PUBLISH]
    assert sorted(d.item for d in pubs) == ["o/r#1", "o/r#2"]
    assert all(i.status is batch.model.ItemStatus.READY_TO_PUBLISH for i in h.run.items)


def test_auto_publish_run_finishes_done():
    h = Harness([row(1)], auto_publish=batch.model.STEP_ORDER)
    assert h.sched.run_until_blocked() is batch.model.RunStatus.DONE
    assert h.run.items[0].status is batch.model.ItemStatus.DONE
    assert "run_finished" in h.kinds()


def test_auto_publish_skips_when_an_earlier_step_is_drafted():
    h = Harness([row(1)], auto_publish=[batch.model.Step.COMMENTS, batch.model.Step.REVIEW])
    h.sched.run_until_blocked()
    comments = next(a for a in h.spawned if a[1] == "comments")
    review = next(a for a in h.spawned if a[1] == "review")
    assert "--finish" not in comments and "--post" not in comments
    assert "--push" not in review
    assert h.run.items[0].step(batch.model.Step.REBASE).drafted
    assert any(d.kind is batch.model.DecisionKind.PUBLISH for d in h.run.decisions)


def test_pool_limit_is_never_exceeded():
    h = Harness([row(n) for n in range(1, 6)], pool=2)
    h.sched.run_until_blocked()
    assert h.max_live == 2


def test_blocked_pr_frees_its_slot_for_the_others():
    h = Harness([row(1), row(2), row(3)], pool=1, codes={("rebase", "/wt/b1"): 3})
    assert h.sched.run_until_blocked() is batch.model.RunStatus.WAITING
    assert h.run.item("o/r#1").status is batch.model.ItemStatus.AWAITING_DECISION
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


def test_closed_pr_is_skipped_without_running_anything():
    h = Harness([row(1)], replan=lambda r: None)
    assert h.sched.run_until_blocked() is batch.model.RunStatus.DONE
    assert h.run.items[0].status is batch.model.ItemStatus.SKIPPED_CLOSED
    assert h.spawned == []


def test_dirty_worktree_becomes_a_decision():
    h = Harness([row(1)], worktrees=lambda d, b: WorktreeResult("/wt/b1", True, ""))
    assert h.sched.run_until_blocked() is batch.model.RunStatus.WAITING
    assert h.run.decisions[0].kind is batch.model.DecisionKind.DIRTY_WORKTREE
    assert h.spawned == []


def test_cancel_before_start_runs_nothing():
    h = Harness([row(1)])
    batch.store.save(h.run)
    batch.store.request_cancel(h.run.id, kill=False)
    assert h.sched.run_until_blocked() is batch.model.RunStatus.CANCELLED
    assert h.spawned == []


def test_admission_wait_is_reported_and_the_floor_still_progresses():
    short = batch.admission.HostSample(2 * GiB, 1.0, 0.0)
    h = Harness([row(1), row(2)], host=short, auto_publish=batch.model.STEP_ORDER)
    assert h.sched.run_until_blocked() is batch.model.RunStatus.DONE
    waits = [f for k, f in h.events if k == "admission_wait"]
    assert waits and waits[0]["reason"].startswith("waiting for memory")
    assert h.max_live == 1


def test_resume_applies_queued_requests():
    h = Harness([row(1, {batch.model.Step.REBASE: NEED, batch.model.Step.COMMENTS: NO, batch.model.Step.REVIEW: NO})],
                codes={("rebase", "/wt/b1"): 3})
    assert h.sched.run_until_blocked() is batch.model.RunStatus.WAITING
    d = h.run.open_decisions()[0]
    batch.store.save(h.run)
    batch.store.write_request(h.run.id, {"decision": d.id, "action": "retry"})
    h.codes.clear()
    h.sched.run_until_blocked()
    assert [a[1] for a in h.spawned] == ["rebase", "rebase"]
    assert ("decision_resolved" in h.kinds())


def test_new_run_selects_needed_steps_and_drops_rows_with_none():
    rows = [row(1, {batch.model.Step.REBASE: NO, batch.model.Step.COMMENTS: NEED, batch.model.Step.REVIEW: NO}),
            row(2, {batch.model.Step.REBASE: NO, batch.model.Step.COMMENTS: NO, batch.model.Step.REVIEW: NO})]
    run = batch.scheduler.new_run(rows, steps=list(batch.model.STEP_ORDER), selected=None, pool=2, auto_publish=[])
    assert [i.key for i in run.items] == ["o/r#1"]
    assert [(s.step, s.status) for s in run.items[0].steps] == [
        (batch.model.Step.REBASE, batch.model.StepStatus.SKIPPED), (batch.model.Step.COMMENTS, batch.model.StepStatus.PENDING),
        (batch.model.Step.REVIEW, batch.model.StepStatus.SKIPPED)]


def test_new_run_honours_explicit_selection():
    run = batch.scheduler.new_run([row(1, {batch.model.Step.REBASE: NO, batch.model.Step.COMMENTS: NO, batch.model.Step.REVIEW: NO})],
                      steps=list(batch.model.STEP_ORDER), selected={"o/r#1": [batch.model.Step.REVIEW]}, pool=1,
                      auto_publish=[])
    assert run.items[0].step(batch.model.Step.REVIEW).status is batch.model.StepStatus.PENDING


def test_mark_interrupted_turns_running_steps_into_decisions():
    run = batch.scheduler.new_run([row(1)], steps=list(batch.model.STEP_ORDER), selected=None, pool=1, auto_publish=[])
    run.items[0].step(batch.model.Step.REBASE).status = batch.model.StepStatus.RUNNING
    assert batch.scheduler.mark_interrupted(run) is True
    assert run.items[0].step(batch.model.Step.REBASE).status is batch.model.StepStatus.INTERRUPTED
    assert run.decisions[0].kind is batch.model.DecisionKind.INTERRUPTED


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
    assert h.sched.run_until_blocked() is batch.model.RunStatus.WAITING
    failed = [d for d in h.run.decisions if d.kind is batch.model.DecisionKind.FAILED]
    assert len(failed) == 1
    assert failed[0].payload["reason"] == "github"
    assert failed[0].payload["detail"] == "graphql 502"
    assert h.run.item("o/r#1").status is batch.model.ItemStatus.AWAITING_DECISION
    assert any(a[-1] == "/wt/b2" for a in h.spawned)
    assert not any(a[-1] == "/wt/b1" for a in h.spawned)


def test_malformed_request_is_reported_and_the_rest_apply():
    h = Harness([row(1, {batch.model.Step.REBASE: NEED, batch.model.Step.COMMENTS: NO, batch.model.Step.REVIEW: NO})],
                codes={("rebase", "/wt/b1"): 3})
    assert h.sched.run_until_blocked() is batch.model.RunStatus.WAITING
    d = h.run.open_decisions()[0]
    batch.store.save(h.run)
    batch.store.write_request(h.run.id, {"decision": d.id})
    batch.store.write_request(h.run.id, {"decision": d.id, "action": "retry"})
    h.codes.clear()
    h.sched.run_until_blocked()
    assert [a[1] for a in h.spawned] == ["rebase", "rebase"]
    errors = [f for k, f in h.events if k == "decision_resolved" and "error" in f]
    assert errors


def test_decision_created_carries_decision_kind():
    h = Harness([row(1)], codes={("rebase", "/wt/b1"): 3})
    h.sched.run_until_blocked()
    created = [f for k, f in h.events if k == "decision_created"]
    assert created
    f = created[0]
    assert f["decision_kind"] and f["decision"] and f["item"] and f["step"] and "payload" in f


def test_publish_and_dirty_worktree_emit_decision_created():
    dirty = Harness([row(1)], worktrees=lambda d, b: WorktreeResult("/wt/b1", True, ""))
    dirty.sched.run_until_blocked()
    kinds = [f["decision_kind"] for k, f in dirty.events if k == "decision_created"]
    assert "dirty_worktree" in kinds

    h = Harness([row(1)])
    h.sched.run_until_blocked()
    kinds = [f["decision_kind"] for k, f in h.events if k == "decision_created"]
    assert "publish" in kinds


def test_failed_publish_via_request_emits_decision_created(monkeypatch):
    h = Harness([row(1)])
    h.sched.run_until_blocked()
    d = next(x for x in h.run.open_decisions() if x.kind is batch.model.DecisionKind.PUBLISH)
    batch.store.save(h.run)
    batch.store.write_request(h.run.id, {"decision": d.id, "action": "publish"})
    monkeypatch.setattr(batch.resolve, "default_runner", lambda argv: 1)
    h.sched.run_until_blocked()
    created = [f for k, f in h.events if k == "decision_created"]
    assert any(f["decision_kind"] == "failed" and f["step"] == "publish" for f in created)


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
    assert killed
    assert h.run.status is batch.model.RunStatus.INTERRUPTED
    saved = batch.store.load(h.run.id)
    assert saved.status is batch.model.RunStatus.INTERRUPTED
    finished = [f for k, f in h.events if k == "run_finished"]
    assert finished and finished[-1]["status"] == "interrupted"
    assert any(d.kind is batch.model.DecisionKind.INTERRUPTED for d in h.run.decisions)


def test_does_not_settle_while_requests_are_pending(monkeypatch):
    h = Harness([row(1)], worktrees=lambda d, b: WorktreeResult("/wt/b1", True, ""))
    checks = {"n": 0}

    def has(run_id):
        checks["n"] += 1
        return checks["n"] == 1

    monkeypatch.setattr(batch.store, "has_requests", has)
    assert h.sched.run_until_blocked() is batch.model.RunStatus.WAITING
    assert checks["n"] >= 2
