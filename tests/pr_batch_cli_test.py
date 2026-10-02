import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.model  # noqa: E402
import batch.scheduler  # noqa: E402
import batch.store  # noqa: E402
import cli.pr  # noqa: E402
import core.run_lock  # noqa: E402
import core.tool_parser  # noqa: E402
import cli.pr_batch  # noqa: E402
from batch.plan import Plan, PlanRow, StepNeed  # noqa: E402
from cli.registry import COMMANDS  # noqa: E402

BIN_DIR = REPO_ROOT / "ai" / "bin"
GLOBAL_FLAGS = ("--repo-dir", "--worktree", "--branch", "--pr", "--schema-version", "--debug")


def _main(argv):
    try:
        return cli.pr.main(list(argv), bin_dir=BIN_DIR)
    except SystemExit as exc:
        return exc.code


def test_batch_is_registered_and_takes_no_target():
    spec = COMMANDS["batch"]
    assert spec.takes_target is False


def test_no_batch_flag_is_a_prefix_of_a_global_flag():
    # core.tool_parser.subparsers() is the documented, repo-wide way to read a
    # built parser's subcommands back off of it (see its docstring); walking
    # each subparser's own `_actions` matches the precedent `value_taking_options`
    # sets in the same module, rather than reaching further into argparse's
    # private layout (`_subparsers._group_actions[0]`) the way this test used to.
    parser = cli.pr_batch.build_parser()
    flags = {opt for sub in core.tool_parser.subparsers(parser).values()
             for a in sub._actions for opt in a.option_strings if opt.startswith("--")}
    clashes = {f for f in flags for g in GLOBAL_FLAGS if g.startswith(f)}
    assert clashes == set()


def test_plan_prints_the_matrix(monkeypatch, capsys):
    row = PlanRow("o/r", "/r", 1, "t", "b", "h", False, {batch.model.Step.REBASE: StepNeed(True, "behind")})
    monkeypatch.setattr(batch.plan, "build_plan", lambda dirs: Plan("me", [row]))
    assert _main(["batch", "plan", "--checkout", "/r"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["schema_version"] == 1
    assert out["rows"][0]["needs"]["rebase"] == {"needed": True, "reason": "behind"}


def test_run_refuses_while_another_run_is_active(monkeypatch, capsys):
    monkeypatch.setattr(batch.store, "active_run_id", lambda: "20261001T000000-aaaa")
    assert _main(["batch", "run", "--checkout", "/r"]) == 1
    assert "20261001T000000-aaaa" in capsys.readouterr().err


def test_run_exits_10_when_waiting(monkeypatch):
    row = PlanRow("o/r", "/r", 1, "t", "b", "h", False,
                  {s: StepNeed(True, "x") for s in batch.model.STEP_ORDER})
    monkeypatch.setattr(batch.plan, "build_plan", lambda dirs: Plan("me", [row]))
    monkeypatch.setattr(batch.scheduler.Scheduler, "run_until_blocked",
                        lambda self: batch.model.RunStatus.WAITING)
    assert _main(["batch", "run", "--checkout", "/r"]) == 10


def test_run_honours_select_and_prs(monkeypatch):
    rows = [PlanRow("o/r", "/r", n, "t", f"b{n}", "h", False,
                    {s: StepNeed(False, "x") for s in batch.model.STEP_ORDER}) for n in (1, 2)]
    seen = {}
    monkeypatch.setattr(batch.plan, "build_plan", lambda dirs: Plan("me", rows))

    def fake_run(self):
        seen["items"] = [(i.key, [s.step.value for s in i.steps
                                  if s.status is batch.model.StepStatus.PENDING]) for i in self.run.items]
        return batch.model.RunStatus.DONE

    monkeypatch.setattr(batch.scheduler.Scheduler, "run_until_blocked", fake_run)
    assert _main(["batch", "run", "--checkout", "/r", "--prs", "o/r#2",
                  "--select", "o/r#2=review"]) == 0
    assert seen["items"] == [("o/r#2", ["review"])]


def _saved_run_with_decisions(n=1, *, kind=batch.model.DecisionKind.OPEN_FINDINGS):
    rows = [PlanRow("o/r", "/r", i, "t", "b", "h", False,
                    {batch.model.Step.REVIEW: StepNeed(True, "x")}) for i in range(1, n + 1)]
    run = batch.scheduler.new_run(rows, steps=[batch.model.Step.REVIEW], selected=None,
                                  pool=1, auto_publish=[])
    for i, item in enumerate(run.items, 1):
        item.worktree = "/wt"
        item.step(batch.model.Step.REVIEW).status = batch.model.StepStatus.NEEDS_DECISION
        run.decisions.append(batch.model.Decision(id=f"d{i}", item=item.key, step="review",
                                                  kind=kind))
        item.status = batch.model.ItemStatus.AWAITING_DECISION
    run.status = batch.model.RunStatus.WAITING
    batch.store.save(run)
    return run


def _saved_run_with_decision():
    return _saved_run_with_decisions(1)


def test_resolve_applies_directly_when_no_scheduler_holds_the_run(capsys):
    run = _saved_run_with_decision()
    assert _main(["batch", "resolve", run.id, "d1", "--action", "accept"]) == 0
    assert json.loads(capsys.readouterr().out)["applied"] is True
    assert batch.store.load(run.id).decision("d1").resolution == "accept"


def test_resolve_applies_a_queued_request_if_the_lock_drops(monkeypatch, capsys):
    run = _saved_run_with_decision()
    held = {"n": 0}

    def is_held(_path):
        held["n"] += 1
        return held["n"] == 1

    monkeypatch.setattr(core.run_lock, "is_held", is_held)
    assert _main(["batch", "resolve", run.id, "d1", "--action", "accept"]) == 0
    assert batch.store.load(run.id).decision("d1").resolution == "accept"
    assert batch.store.has_requests(run.id) is False


def test_resolve_rejects_a_bad_action(capsys):
    run = _saved_run_with_decision()
    assert _main(["batch", "resolve", run.id, "d1", "--action", "abort"]) == 1
    assert "accept" in capsys.readouterr().err


def test_status_prints_the_run_and_whether_it_is_active(capsys):
    run = _saved_run_with_decision()
    assert _main(["batch", "status", run.id]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["run"]["id"] == run.id and out["active"] is False


def test_status_defaults_to_the_latest_run(capsys):
    run = _saved_run_with_decision()
    assert _main(["batch", "status"]) == 0
    assert json.loads(capsys.readouterr().out)["run"]["id"] == run.id


def test_cancel_an_idle_run_marks_it_cancelled():
    run = _saved_run_with_decision()
    assert _main(["batch", "cancel", run.id]) == 0
    assert batch.store.load(run.id).status is batch.model.RunStatus.CANCELLED


def test_cancel_does_not_overwrite_a_terminal_status(capsys):
    run = _saved_run_with_decision()
    run.status = batch.model.RunStatus.DONE
    batch.store.save(run)
    assert _main(["batch", "cancel", run.id]) == 1
    assert "done" in capsys.readouterr().err
    assert batch.store.load(run.id).status is batch.model.RunStatus.DONE


def test_cancel_does_not_clobber_a_run_that_finished_during_the_request(monkeypatch):
    run = _saved_run_with_decision()

    def is_held(_path):
        # Simulate the scheduler finishing and releasing its lock in the window
        # between the initial load and this check, writing its real outcome.
        finished = batch.store.load(run.id)
        finished.status = batch.model.RunStatus.DONE
        batch.store.save(finished)
        return False

    monkeypatch.setattr(core.run_lock, "is_held", is_held)
    assert _main(["batch", "cancel", run.id]) == 0
    assert batch.store.load(run.id).status is batch.model.RunStatus.DONE


def test_cancel_unknown_run_exits_1_without_creating_a_dir(tmp_path, capsys):
    missing = "20990101T000000-dead"
    assert _main(["batch", "cancel", missing]) == 1
    assert missing in capsys.readouterr().err
    assert not (batch.store.batch_root() / missing).exists()


def test_resolve_unknown_run_exits_1_without_creating_a_dir(capsys):
    missing = "20990101T000000-dead"
    assert _main(["batch", "resolve", missing, "d1", "--action", "accept"]) == 1
    assert missing in capsys.readouterr().err
    assert not (batch.store.batch_root() / missing).exists()


def test_resume_clears_a_stale_cancel_before_driving(monkeypatch):
    run = _saved_run_with_decision()
    batch.store.request_cancel(run.id, kill=False)
    seen = {}

    def fake_run(self):
        seen["cancel"] = batch.store.cancel_requested(self.run.id).requested
        return batch.model.RunStatus.WAITING

    monkeypatch.setattr(batch.scheduler.Scheduler, "run_until_blocked", fake_run)
    assert _main(["batch", "resume", run.id]) == 10
    assert seen["cancel"] is False


def test_resume_reports_an_unknown_run(capsys):
    assert _main(["batch", "resume", "nope"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("pr batch:")
    assert "nope" in err


def test_resume_emits_interrupted_for_a_running_step(monkeypatch, capsys):
    run = _saved_run_with_decision()
    rec = run.items[0].step(batch.model.Step.REVIEW)
    rec.status = batch.model.StepStatus.RUNNING
    rec.log_path = "/tmp/x.log"
    run.status = batch.model.RunStatus.INTERRUPTED
    batch.store.save(run)
    monkeypatch.setattr(batch.scheduler.Scheduler, "run_until_blocked",
                        lambda self: batch.model.RunStatus.WAITING)
    assert _main(["batch", "resume", run.id]) == 10
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    created = [e for e in lines if e.get("kind") == "decision_created"]
    assert any(e.get("decision_kind") == "interrupted" for e in created)


def test_idle_resolve_drains_other_pending_requests():
    run = _saved_run_with_decisions(2)
    batch.store.write_request(run.id, {"decision": "d1", "action": "accept"})
    assert _main(["batch", "resolve", run.id, "d2", "--action", "accept"]) == 0
    saved = batch.store.load(run.id)
    assert saved.decision("d1").resolution == "accept"
    assert saved.decision("d2").resolution == "accept"
    assert batch.store.has_requests(run.id) is False


def test_apply_pending_reports_each_error_and_keeps_earlier_applies(monkeypatch, capsys):
    run = _saved_run_with_decisions(2)
    batch.store.write_request(run.id, {"decision": "d1", "action": "accept"})
    held = {"n": 0}

    def is_held(_path):
        held["n"] += 1
        return held["n"] == 1

    monkeypatch.setattr(core.run_lock, "is_held", is_held)
    assert _main(["batch", "resolve", run.id, "d2", "--action", "abort"]) == 1
    captured = capsys.readouterr()
    err_json = json.loads(captured.out)
    assert err_json["errors"]
    assert "accept" in captured.err
    saved = batch.store.load(run.id)
    assert saved.decision("d1").resolution == "accept"
    assert saved.decision("d2").open


def test_idle_resolve_marks_run_done_when_every_item_is_terminal(capsys):
    run = _saved_run_with_decisions(1, kind=batch.model.DecisionKind.FAILED)
    assert _main(["batch", "resolve", run.id, "d1", "--action", "drop-pr"]) == 0
    saved = batch.store.load(run.id)
    assert saved.items[0].terminal
    assert saved.status is batch.model.RunStatus.DONE
    out = capsys.readouterr().out
    assert "run_finished" not in out


def test_cancelled_run_stays_cancelled_after_resolve_drain():
    run = _saved_run_with_decisions(1, kind=batch.model.DecisionKind.FAILED)
    run.status = batch.model.RunStatus.CANCELLED
    batch.store.save(run)
    assert _main(["batch", "resolve", run.id, "d1", "--action", "drop-pr"]) == 0
    saved = batch.store.load(run.id)
    assert saved.items[0].terminal
    assert saved.status is batch.model.RunStatus.CANCELLED


def test_idle_resolve_leaves_waiting_when_an_item_is_unblocked():
    run = _saved_run_with_decision()
    assert _main(["batch", "resolve", run.id, "d1", "--action", "accept"]) == 0
    saved = batch.store.load(run.id)
    assert saved.items[0].status is batch.model.ItemStatus.QUEUED
    assert not saved.open_decisions()
    assert saved.status is batch.model.RunStatus.WAITING


def test_schema_version_1_is_served_for_batch(monkeypatch):
    monkeypatch.setattr(batch.plan, "build_plan", lambda dirs: Plan("me", []))
    assert _main(["--schema-version", "1", "batch", "plan", "--checkout", "/r"]) == 0
