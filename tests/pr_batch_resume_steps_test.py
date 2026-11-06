"""`pr batch resume --steps` narrows a run; a later commit does not re-queue review."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

# NEED, _close_open_sessions, _quiet_outcomes are re-exported so pytest
# picks up their fixtures/autouse hooks from the support module.
import batch.model  # noqa: E402
import batch.scheduler  # noqa: E402
import batch.store  # noqa: E402
import cli.pr  # noqa: E402
from batch.plan import PlanRow, StepNeed  # noqa: E402
from batch_scheduler_support import (NEED, NO, Harness,  # noqa: F401,E402
                                     _close_open_sessions, _quiet_outcomes, row)
from batch_session_support import seed, stops_at  # noqa: E402

BIN_DIR = REPO_ROOT / "ai" / "bin"
Step = batch.model.Step
StepStatus = batch.model.StepStatus
NEED_ALL = {s: StepNeed(True, "x") for s in batch.model.STEP_ORDER}


def _main(argv):
    try:
        return cli.pr.main(list(argv), bin_dir=BIN_DIR)
    except SystemExit as exc:
        return exc.code


def _interrupted_run(*, steps=None, needs=None, review_status=StepStatus.PENDING,
                     persist=True):
    chosen = list(steps) if steps is not None else list(batch.model.STEP_ORDER)
    rows = [PlanRow("o/r", "/r", 1, "t", "b", "h", False, needs or NEED_ALL)]
    run = batch.scheduler.new_run(rows, steps=chosen, selected=None, pool=1, auto_publish=[])
    run.items[0].worktree = "/wt"
    if run.items[0].has(Step.REVIEW):
        run.items[0].step(Step.REVIEW).status = review_status
    run.status = batch.model.RunStatus.INTERRUPTED
    return seed(run) if persist else run


def test_resume_steps_skips_pending_review_and_narrows_run_steps(monkeypatch):
    run = _interrupted_run()
    monkeypatch.setattr(batch.scheduler.Scheduler, "run_until_blocked",
                        stops_at(batch.model.RunStatus.WAITING))
    assert _main(["batch", "resume", run.id, "--steps", "rebase,ci,comments"]) == 10
    loaded = batch.store.load(run.id)
    assert loaded.steps == [Step.REBASE, Step.CI, Step.COMMENTS]
    assert loaded.items[0].step(Step.REVIEW).status is StepStatus.SKIPPED
    assert loaded.items[0].step(Step.REBASE).status is StepStatus.PENDING


def test_resume_steps_leaves_an_open_review_decision_alone(monkeypatch):
    run = _interrupted_run(review_status=StepStatus.NEEDS_DECISION, persist=False)
    run.items[0].status = batch.model.ItemStatus.AWAITING_DECISION
    run.decisions.append(batch.model.Decision(
        id="d1", item=run.items[0].key, step="review",
        kind=batch.model.DecisionKind.STEP_REVIEW))
    seed(run)
    monkeypatch.setattr(batch.scheduler.Scheduler, "run_until_blocked",
                        stops_at(batch.model.RunStatus.WAITING))
    assert _main(["batch", "resume", run.id, "--steps", "rebase,ci,comments"]) == 10
    loaded = batch.store.load(run.id)
    assert loaded.items[0].step(Step.REVIEW).status is StepStatus.NEEDS_DECISION
    assert loaded.decision("d1").open


def test_resume_refuses_to_widen_steps(monkeypatch, capsys):
    run = _interrupted_run(steps=[Step.REBASE, Step.CI],
                           needs={Step.REBASE: StepNeed(True, "x"),
                                  Step.CI: StepNeed(True, "x")})
    monkeypatch.setattr(batch.scheduler.Scheduler, "run_until_blocked",
                        stops_at(batch.model.RunStatus.WAITING))
    assert _main(["batch", "resume", run.id, "--steps", "rebase,ci,comments"]) == 2
    err = capsys.readouterr().err
    assert "resume can only narrow steps; start a new run to add one" in err
    loaded = batch.store.load(run.id)
    assert loaded.steps == [Step.REBASE, Step.CI]


def test_head_move_does_not_repend_review_when_review_not_in_run_steps():
    rows = [row(1, {Step.REBASE: NO, Step.COMMENTS: NEED, Step.REVIEW: NO})]
    h = Harness(rows)
    h.run.steps = [s for s in h.run.steps if s is not Step.REVIEW]
    real_spawn = h._spawn

    def spawn(argv, **kw):
        if argv[1] == "comments":
            h.heads["/wt/b1"] = "h1"
        return real_spawn(argv, **kw)

    h.sched._spawn = spawn
    h.sched.run_until_blocked()
    assert [a[1] for a in h.spawned] == ["comments"]
    assert h.run.items[0].step(Step.REVIEW).status is StepStatus.SKIPPED
