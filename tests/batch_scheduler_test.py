import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.admission as adm  # noqa: E402
import batch.events as events  # noqa: E402
import batch.model as m  # noqa: E402
import batch.outcomes as outcomes  # noqa: E402
import batch.scheduler as sch  # noqa: E402
import batch.store as store  # noqa: E402
from batch.plan import PlanRow, StepNeed  # noqa: E402
from batch.steps import WorktreeResult  # noqa: E402
from config.workbench_config import BatchConfig  # noqa: E402

GiB = 1024 ** 3
NEED = StepNeed(True, "x")
NO = StepNeed(False, "y")
ALL = {m.Step.REBASE: NEED, m.Step.COMMENTS: NEED, m.Step.REVIEW: NEED}
HEALTHY = adm.HostSample(8 * GiB, 1.0, 0.0)


def row(n, needs=ALL):
    return PlanRow("o/r", "/r", n, f"t{n}", f"b{n}", "h", False, dict(needs))


@pytest.fixture(autouse=True)
def _quiet_outcomes(monkeypatch):
    monkeypatch.setattr(outcomes, "comment_items", lambda item: [])
    monkeypatch.setattr(outcomes, "open_findings", lambda item: [])


class Harness:
    def __init__(self, rows, *, codes=None, auto_publish=(), pool=2, host=HEALTHY,
                 replan=None, worktrees=None, heads=None, selected=None, cfg=None):
        self.codes = codes or {}
        self.spawned, self.events, self.live, self.max_live = [], [], 0, 0
        self.run = sch.new_run(rows, steps=list(m.STEP_ORDER), selected=selected, pool=pool,
                               auto_publish=list(auto_publish))
        self.heads = heads or {}
        self.sched = sch.Scheduler(
            self.run, pr_bin="pr", cfg=cfg or BatchConfig(pool_max=4),
            host=lambda: host, spawn=self._spawn,
            replan=replan or (lambda r: r),
            worktrees=worktrees or (lambda d, b: WorktreeResult(f"/wt/{b}", False, "")),
            head=lambda wt: self.heads.get(wt, "h0"), rss=lambda pid: 0,
            emit=lambda kind, **f: self.events.append((kind, f)), sleep=lambda s: None,
            estimates=adm.Estimates({}))

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
    assert h.sched.run_until_blocked() is m.RunStatus.WAITING
    assert [a[1] for a in h.spawned if a[-1] == "/wt/b1"] == ["rebase", "comments", "review"]
    assert "--no-push" in h.spawned[0]
    pubs = [d for d in h.run.decisions if d.kind is m.DecisionKind.PUBLISH]
    assert sorted(d.item for d in pubs) == ["o/r#1", "o/r#2"]
    assert all(i.status is m.ItemStatus.READY_TO_PUBLISH for i in h.run.items)


def test_auto_publish_run_finishes_done():
    h = Harness([row(1)], auto_publish=m.STEP_ORDER)
    assert h.sched.run_until_blocked() is m.RunStatus.DONE
    assert h.run.items[0].status is m.ItemStatus.DONE
    assert "run_finished" in h.kinds()


def test_pool_limit_is_never_exceeded():
    h = Harness([row(n) for n in range(1, 6)], pool=2)
    h.sched.run_until_blocked()
    assert h.max_live == 2


def test_blocked_pr_frees_its_slot_for_the_others():
    h = Harness([row(1), row(2), row(3)], pool=1, codes={("rebase", "/wt/b1"): 3})
    assert h.sched.run_until_blocked() is m.RunStatus.WAITING
    assert h.run.item("o/r#1").status is m.ItemStatus.AWAITING_DECISION
    assert any(a[1] == "review" and a[-1] == "/wt/b3" for a in h.spawned)


def test_local_head_move_reenables_review():
    rows = [row(1, {m.Step.REBASE: NO, m.Step.COMMENTS: NEED, m.Step.REVIEW: NO})]
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
    assert h.sched.run_until_blocked() is m.RunStatus.DONE
    assert h.run.items[0].status is m.ItemStatus.SKIPPED_CLOSED
    assert h.spawned == []


def test_dirty_worktree_becomes_a_decision():
    h = Harness([row(1)], worktrees=lambda d, b: WorktreeResult("/wt/b1", True, ""))
    assert h.sched.run_until_blocked() is m.RunStatus.WAITING
    assert h.run.decisions[0].kind is m.DecisionKind.DIRTY_WORKTREE
    assert h.spawned == []


def test_cancel_before_start_runs_nothing():
    h = Harness([row(1)])
    store.save(h.run)
    store.request_cancel(h.run.id, kill=False)
    assert h.sched.run_until_blocked() is m.RunStatus.CANCELLED
    assert h.spawned == []


def test_admission_wait_is_reported_and_the_floor_still_progresses():
    short = adm.HostSample(2 * GiB, 1.0, 0.0)
    h = Harness([row(1), row(2)], host=short, auto_publish=m.STEP_ORDER)
    assert h.sched.run_until_blocked() is m.RunStatus.DONE
    waits = [f for k, f in h.events if k == "admission_wait"]
    assert waits and waits[0]["reason"].startswith("waiting for memory")
    assert h.max_live == 1


def test_resume_applies_queued_requests():
    h = Harness([row(1, {m.Step.REBASE: NEED, m.Step.COMMENTS: NO, m.Step.REVIEW: NO})],
                codes={("rebase", "/wt/b1"): 3})
    assert h.sched.run_until_blocked() is m.RunStatus.WAITING
    d = h.run.open_decisions()[0]
    store.save(h.run)
    store.write_request(h.run.id, {"decision": d.id, "action": "retry"})
    h.codes.clear()
    h.sched.run_until_blocked()
    assert [a[1] for a in h.spawned] == ["rebase", "rebase"]
    assert ("decision_resolved" in h.kinds())


def test_new_run_selects_needed_steps_and_drops_rows_with_none():
    rows = [row(1, {m.Step.REBASE: NO, m.Step.COMMENTS: NEED, m.Step.REVIEW: NO}),
            row(2, {m.Step.REBASE: NO, m.Step.COMMENTS: NO, m.Step.REVIEW: NO})]
    run = sch.new_run(rows, steps=list(m.STEP_ORDER), selected=None, pool=2, auto_publish=[])
    assert [i.key for i in run.items] == ["o/r#1"]
    assert [(s.step, s.status) for s in run.items[0].steps] == [
        (m.Step.REBASE, m.StepStatus.SKIPPED), (m.Step.COMMENTS, m.StepStatus.PENDING),
        (m.Step.REVIEW, m.StepStatus.SKIPPED)]


def test_new_run_honours_explicit_selection():
    run = sch.new_run([row(1, {m.Step.REBASE: NO, m.Step.COMMENTS: NO, m.Step.REVIEW: NO})],
                      steps=list(m.STEP_ORDER), selected={"o/r#1": [m.Step.REVIEW]}, pool=1,
                      auto_publish=[])
    assert run.items[0].step(m.Step.REVIEW).status is m.StepStatus.PENDING


def test_mark_interrupted_turns_running_steps_into_decisions():
    run = sch.new_run([row(1)], steps=list(m.STEP_ORDER), selected=None, pool=1, auto_publish=[])
    run.items[0].step(m.Step.REBASE).status = m.StepStatus.RUNNING
    assert sch.mark_interrupted(run) is True
    assert run.items[0].step(m.Step.REBASE).status is m.StepStatus.INTERRUPTED
    assert run.decisions[0].kind is m.DecisionKind.INTERRUPTED


def test_events_are_ndjson_with_schema_version(capsys):
    events.emit("run_started", run="r1")
    line = json.loads(capsys.readouterr().out.strip())
    assert line["schema_version"] == 1 and line["kind"] == "run_started" and line["run"] == "r1"


def test_unknown_event_kind_is_refused():
    with pytest.raises(ValueError):
        events.emit("nope")
