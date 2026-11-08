import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.live  # noqa: E402
import batch.model  # noqa: E402
import batch.outcomes  # noqa: E402
import batch.publish  # noqa: E402
import batch.scheduler  # noqa: E402
import batch.store  # noqa: E402
from batch.requests import Cancel  # noqa: E402
from batch.publish import TreeState  # noqa: E402
from pr.comments_fix import CloseoutDebt  # noqa: E402
from rebase.types import RefDivergence  # noqa: E402
import cli.pr  # noqa: E402
import core.tool_parser  # noqa: E402
import cli.pr_batch  # noqa: E402
import pr.target  # noqa: E402
from batch.plan import Plan, PlanRow, StepNeed  # noqa: E402
from batch_session_support import holding, open_session, seed, stops_at  # noqa: E402
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


def test_run_honours_select_and_prs(monkeypatch):
    rows = [PlanRow("o/r", "/r", n, "t", f"b{n}", "h", False,
                    {s: StepNeed(False, "x") for s in batch.model.STEP_ORDER}) for n in (1, 2)]
    seen = {}
    monkeypatch.setattr(batch.plan, "build_run_plan", lambda dirs: Plan("me", rows))

    def fake_run(self):
        seen["items"] = [(i.key, [s.step.value for s in i.steps
                                  if s.status is batch.model.StepStatus.PENDING]) for i in self.run.items]
        self.run.status = batch.model.RunStatus.DONE
        return batch.model.RunStatus.DONE

    monkeypatch.setattr(batch.scheduler.Scheduler, "run_until_blocked", fake_run)
    assert _main(["batch", "run", "--checkout", "/r", "--prs", "o/r#2",
                  "--select", "o/r#2=review"]) == 0
    assert seen["items"] == [("o/r#2", ["review"])]


def _run_with_decisions(n=1, *, kind=batch.model.DecisionKind.OPEN_FINDINGS):
    """A WAITING run, not yet saved, whose n items each await one decision `d<i>` of *kind*."""
    rows = [PlanRow("o/r", "/r", i, "t", "b", "h", False,
                    {batch.model.Step.REVIEW: StepNeed(True, "x")}) for i in range(1, n + 1)]
    run = batch.scheduler.new_run(rows, steps=[batch.model.Step.REVIEW], selected=None,
                                  pool=1)
    for i, item in enumerate(run.items, 1):
        item.worktree = "/wt"
        item.step(batch.model.Step.REVIEW).status = batch.model.StepStatus.NEEDS_DECISION
        run.decisions.append(batch.model.Decision(id=f"d{i}", item=item.key, step="review",
                                                  kind=kind))
        item.status = batch.model.ItemStatus.AWAITING_DECISION
    run.status = batch.model.RunStatus.WAITING
    return run


def _saved_run_with_decisions(n=1, *, kind=batch.model.DecisionKind.OPEN_FINDINGS):
    return seed(_run_with_decisions(n, kind=kind))


def _saved_run_with_decision():
    return _saved_run_with_decisions(1)


def _saved_v2_waiting():
    """A schema-2 WAITING run with one queued item and no decisions."""
    rows = [PlanRow("o/r", "/r", 1, "t", "b", "h", False,
                    {batch.model.Step.REVIEW: StepNeed(True, "x")})]
    run = batch.scheduler.new_run(rows, steps=[batch.model.Step.REVIEW], selected=None,
                                  pool=1)
    run.items[0].worktree = "/wt"
    run.status = batch.model.RunStatus.WAITING
    return seed(run)


def _origin(monkeypatch, label):
    monkeypatch.setattr(
        pr.target, "repo_identity_from_origin",
        lambda cwd=None: pr.target.RepoIdentity(label=label, key="r", host="github.com"))


def _seeded(run_id, *, repo="o/r", status=batch.model.RunStatus.WAITING):
    run = _run_with_decisions(1)
    run.id = run_id
    run.items[0].repo = repo
    run.items[0].key = f"{repo}#1"
    run.status = status
    return seed(run)


def test_status_full_prints_the_raw_run_and_whether_it_is_active(capsys):
    run = _saved_run_with_decision()
    assert _main(["batch", "status", run.id, "--full"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["run"]["id"] == run.id and out["active"] is False
    assert out["run"]["decisions"][0]["id"] == "d1"


def test_status_full_names_the_run_directory(capsys):
    run = _saved_run_with_decision()
    assert _main(["batch", "status", run.id, "--full"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["run_dir"] == str(batch.store.run_dir(run.id))


def test_status_prints_the_compact_report_by_default(capsys):
    run = _saved_run_with_decisions(1, kind=batch.model.DecisionKind.FAILED)
    assert _main(["batch", "status", run.id, "--offline"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert set(out) == {"schema_version", "run", "counts", "items", "github"}
    assert out["schema_version"] == 2
    assert (out["run"]["id"], out["run"]["active"], out["run"]["exit_hint"]) == (run.id, False, 1)
    assert "next" not in out
    assert "open_decisions" not in out["counts"]


@pytest.mark.parametrize("command", [["status"], ["report", "--format", "json"]])
def test_status_and_report_ask_github_about_the_runs_repos(monkeypatch, capsys, command):
    run = _saved_run_with_decision()
    seen = {}

    def fake_view(r, *, cwd):
        seen["run"], seen["cwd"] = r.id, cwd
        return {"checked": True, "owed": [], "untracked": [], "moved": []}

    monkeypatch.setattr(batch.live, "view", fake_view)
    assert _main(["batch", *command, run.id]) == 0
    assert json.loads(capsys.readouterr().out)["github"]["checked"] is True
    assert seen == {"run": run.id, "cwd": os.getcwd()}


@pytest.mark.parametrize("command", [["status"], ["report", "--format", "json"]])
def test_offline_skips_github(monkeypatch, capsys, command):
    run = _saved_run_with_decision()

    def no_github(*a, **kw):
        raise AssertionError("asked GitHub under --offline")

    monkeypatch.setattr(batch.live, "view", no_github)
    assert _main(["batch", *command, run.id, "--offline"]) == 0
    assert json.loads(capsys.readouterr().out)["github"] == {"checked": False, "skipped": True}


def test_status_defaults_to_the_latest_run(capsys):
    run = _saved_run_with_decision()
    assert _main(["batch", "status"]) == 0
    assert json.loads(capsys.readouterr().out)["run"]["id"] == run.id


def test_status_defaults_to_this_repos_waiting_run(monkeypatch, capsys):
    waiting = _seeded("20261001T120000-wait", repo="o/r")
    _seeded("20261002T120000-done", repo="other/r", status=batch.model.RunStatus.DONE)
    _origin(monkeypatch, "o/r")
    assert _main(["batch", "status", "--offline"]) == 0
    assert json.loads(capsys.readouterr().out)["run"]["id"] == waiting.id


def test_status_names_other_active_runs_on_stderr(monkeypatch, capsys):
    waiting = _seeded("20261001T120000-wait", repo="o/r")
    other = _seeded("20261002T120000-live", repo="other/r")
    _origin(monkeypatch, "o/r")
    with holding(other.id, "driver", "pr batch resume"):
        assert _main(["batch", "status", "--offline"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["run"]["id"] == waiting.id
    assert f"other active run {other.id}" in captured.err


def test_cancel_without_id_refuses_two_unfinished_matching_runs(monkeypatch, capsys):
    older = _seeded("20261001T120000-aaaa", repo="o/r")
    newer = _seeded("20261002T120000-bbbb", repo="o/r")
    _origin(monkeypatch, "o/r")
    assert _main(["batch", "cancel"]) == 1
    err = capsys.readouterr().err
    assert older.id in err and newer.id in err
    assert batch.store.load(older.id).status is batch.model.RunStatus.WAITING
    assert batch.store.load(newer.id).status is batch.model.RunStatus.WAITING


def test_cancel_without_id_picks_the_unfinished_matching_run(monkeypatch, capsys):
    waiting = _seeded("20261001T120000-wait", repo="o/r")
    done = _seeded("20261002T120000-done", repo="o/r", status=batch.model.RunStatus.DONE)
    _origin(monkeypatch, "o/r")
    assert _main(["batch", "cancel"]) == 0
    assert json.loads(capsys.readouterr().out)["run"] == waiting.id
    assert batch.store.load(waiting.id).status is batch.model.RunStatus.CANCELLED
    assert batch.store.load(done.id).status is batch.model.RunStatus.DONE


def test_cancel_an_idle_run_marks_it_cancelled(capsys):
    run = _saved_run_with_decision()
    assert _main(["batch", "cancel", run.id]) == 0
    assert json.loads(capsys.readouterr().out) == {"cancelled": True, "run": run.id,
                                                    "queued": False}
    saved = batch.store.load(run.id)
    assert saved.status is batch.model.RunStatus.CANCELLED
    assert saved.cancel is not None and saved.cancel.kill is False


def test_cancel_does_not_overwrite_a_terminal_status(capsys):
    run = _run_with_decisions(1)
    run.status = batch.model.RunStatus.DONE
    seed(run)
    assert _main(["batch", "cancel", run.id]) == 1
    assert "done" in capsys.readouterr().err
    assert batch.store.load(run.id).status is batch.model.RunStatus.DONE


def test_a_cancel_that_loses_to_completion_reports_the_real_status(monkeypatch, capsys):
    run = _saved_run_with_decision()
    real = batch.store.write_request

    def finish_first(run_id, request):
        # The scheduler settles DONE between cancel's check and its request.
        with open_session(run_id) as session:
            session.run.status = batch.model.RunStatus.DONE
        return real(run_id, request)

    monkeypatch.setattr(batch.store, "write_request", finish_first)
    assert _main(["batch", "cancel", run.id]) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"cancelled": False, "run": run.id, "queued": False,
                                        "status": "done"}
    assert f"run {run.id} is done" in captured.err
    assert batch.store.load(run.id).status is batch.model.RunStatus.DONE


def test_cancel_unknown_run_exits_1_without_creating_a_dir(tmp_path, capsys):
    missing = "20990101T000000-dead"
    assert _main(["batch", "cancel", missing]) == 1
    assert missing in capsys.readouterr().err
    assert not (batch.store.batch_root() / missing).exists()


def test_a_refused_resume_of_a_done_run_does_not_touch_state_mtime(monkeypatch, capsys):
    run = _run_with_decisions(1)
    run.status = batch.model.RunStatus.DONE
    seed(run)
    path = batch.store.run_dir(run.id) / batch.store.STATE_FILE
    before = path.stat()
    monkeypatch.setattr(batch.scheduler.Scheduler, "run_until_blocked",
                        lambda self: batch.model.RunStatus.DONE)
    assert _main(["batch", "resume", run.id]) == 1
    assert f"run {run.id} is done" in capsys.readouterr().err
    after = path.stat()
    assert batch.store.state_mtime(run.id) == before.st_mtime
    assert after.st_mtime_ns == before.st_mtime_ns
    assert after.st_ino == before.st_ino


def test_resume_after_a_queued_cancel_refuses_and_the_run_is_cancelled(monkeypatch, capsys):
    run = _saved_run_with_decision()
    batch.store.write_request(run.id, Cancel())
    driven = []
    monkeypatch.setattr(batch.scheduler.Scheduler, "run_until_blocked",
                        lambda self: driven.append(1) or batch.model.RunStatus.WAITING)
    assert _main(["batch", "resume", run.id]) == 1
    assert f"run {run.id} is cancelled" in capsys.readouterr().err
    assert driven == []
    assert batch.store.load(run.id).status is batch.model.RunStatus.CANCELLED


def test_resume_reports_an_unknown_run(capsys):
    assert _main(["batch", "resume", "nope"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("pr batch:")
    assert "nope" in err


def test_resume_emits_interrupted_for_a_running_step(monkeypatch, capsys):
    rows = [PlanRow("o/r", "/r", 1, "t", "b", "h", False,
                    {batch.model.Step.REVIEW: StepNeed(True, "x")})]
    run = batch.scheduler.new_run(rows, steps=[batch.model.Step.REVIEW], selected=None,
                                  pool=1)
    rec = run.items[0].step(batch.model.Step.REVIEW)
    rec.status = batch.model.StepStatus.RUNNING
    rec.log_path = "/tmp/x.log"
    run.items[0].worktree = "/wt"
    run.status = batch.model.RunStatus.INTERRUPTED
    seed(run)
    monkeypatch.setattr(batch.scheduler.Scheduler, "run_until_blocked",
                        stops_at(batch.model.RunStatus.DONE))
    assert _main(["batch", "resume", run.id]) == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert all(e.get("kind") != "decision_created" for e in lines)
    loaded = batch.store.load(run.id)
    assert loaded.items[0].step(batch.model.Step.REVIEW).status is batch.model.StepStatus.PENDING


def test_batch_subcommand_help_reaches_the_batch_parser(capsys):
    assert _main(["batch", "plan", "-h"]) == 0
    assert "--checkout" in capsys.readouterr().out


# passes-at-base: status already answers -h via argparse because it is scriptless
def test_a_command_that_ignores_argv_answers_help_without_running(monkeypatch, capsys):
    """`status` takes no flags, so add_help=False would run it on `-h`."""
    monkeypatch.setitem(cli.pr._CUSTOM, "status",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran")))
    assert _main(["status", "-h"]) == 0
    assert "usage" in capsys.readouterr().out


def _failing_drop(monkeypatch):
    def drop(dirs, ns):
        raise OSError("cleanup failed")

    monkeypatch.setattr(batch.plan, "drop_refs", drop)


def test_cancel_saves_cancelled_before_dropping_refs(monkeypatch):
    run = _saved_run_with_decision()
    _failing_drop(monkeypatch)
    with pytest.raises(OSError):
        _main(["batch", "cancel", run.id])
    assert batch.store.load(run.id).status is batch.model.RunStatus.CANCELLED


def _plan_with_refs():
    return Plan("me", [], ref_namespace="refs/pr-batch/abcd", ref_dirs=["/r"])


def test_plan_does_not_drop_refs_when_the_reader_has_gone(monkeypatch):
    dropped = []
    monkeypatch.setattr(batch.plan, "build_plan", lambda dirs: Plan("me", []))
    monkeypatch.setattr(batch.plan, "drop_refs", lambda dirs, ns: dropped.append((dirs, ns)))

    def gone(_payload):
        raise BrokenPipeError

    monkeypatch.setattr(cli.pr_batch.core.report, "emit_json", gone)
    with pytest.raises(BrokenPipeError):
        _main(["batch", "plan", "--checkout", "/r"])
    assert dropped == []


def test_run_drops_the_plans_refs_when_it_fails_before_saving(monkeypatch):
    dropped = []
    monkeypatch.setattr(batch.plan, "build_run_plan", lambda dirs: _plan_with_refs())
    monkeypatch.setattr(batch.plan, "drop_refs", lambda dirs, ns: dropped.append((dirs, ns)))

    def broken_config(dirs):
        raise ValueError("bad config")

    monkeypatch.setattr(cli.pr_batch, "_cfg", broken_config)
    with pytest.raises(ValueError):
        _main(["batch", "run", "--checkout", "/r"])
    assert dropped == [(["/r"], "refs/pr-batch/abcd")]


def test_no_verify_reaches_the_run(monkeypatch):
    rows = [PlanRow("o/r", "/r", 1, "t", "b1", "h", False,
                    {s: StepNeed(True, "x") for s in batch.model.STEP_ORDER})]
    seen = {}
    monkeypatch.setattr(batch.plan, "build_run_plan", lambda dirs: Plan("me", rows))

    settle = stops_at(batch.model.RunStatus.DONE)

    def fake_run(self):
        seen["no_verify"] = self.run.no_verify
        return settle(self)

    monkeypatch.setattr(batch.scheduler.Scheduler, "run_until_blocked", fake_run)
    assert _main(["batch", "run", "--checkout", "/r", "--no-verify"]) == 0
    assert seen["no_verify"] is True


def test_an_oversized_focus_file_refuses_the_run_before_planning(tmp_path, monkeypatch, capsys):
    from review.budget import MAX_FOCUS_BYTES
    big = tmp_path / "focus.md"
    big.write_bytes(b"x" * (MAX_FOCUS_BYTES + 1))

    def boom(args):
        raise AssertionError("planned")

    monkeypatch.setattr(cli.pr_batch, "_plan", boom)
    assert _main(["batch", "run", "--checkout", "/r", "--focus-file", str(big)]) == 1
    assert str(MAX_FOCUS_BYTES) in capsys.readouterr().err


def test_an_untitled_focus_text_refuses_the_run_before_planning(monkeypatch, capsys):
    def boom(args):
        raise AssertionError("planned")

    monkeypatch.setattr(cli.pr_batch, "_plan", boom)
    assert _main(["batch", "run", "--checkout", "/r", "--focus", "###"]) == 1
    assert "no title" in capsys.readouterr().err


def test_run_stores_the_focus(monkeypatch):
    rows = [PlanRow("o/r", "/r", 1, "t", "b1", "h", False,
                    {s: StepNeed(True, "x") for s in batch.model.STEP_ORDER})]
    seen = {}
    monkeypatch.setattr(batch.plan, "build_run_plan", lambda dirs: Plan("me", rows))

    settle = stops_at(batch.model.RunStatus.DONE)

    def fake_run(self):
        seen["focus"] = list(self.run.focus)
        return settle(self)

    monkeypatch.setattr(batch.scheduler.Scheduler, "run_until_blocked", fake_run)
    assert _main(["batch", "run", "--checkout", "/r", "--focus", "x"]) == 0
    assert seen["focus"] == ["x"]


def test_focus_warns_about_items_without_a_review_step(monkeypatch, capsys):
    rows = [
        PlanRow("o/r", "/r", 1, "t", "b1", "h", False,
                {batch.model.Step.REVIEW: StepNeed(True, "x")}),
        PlanRow("o/r", "/r", 2, "t", "b2", "h", False,
                {batch.model.Step.REBASE: StepNeed(True, "behind"),
                 batch.model.Step.REVIEW: StepNeed(False, "current")}),
    ]
    monkeypatch.setattr(batch.plan, "build_run_plan", lambda dirs: Plan("me", rows))
    monkeypatch.setattr(batch.scheduler.Scheduler, "run_until_blocked",
                        stops_at(batch.model.RunStatus.DONE))
    assert _main(["batch", "run", "--checkout", "/r", "--focus", "x"]) == 0
    warns = [line for line in capsys.readouterr().err.splitlines()
             if "--focus applies only to review steps" in line]
    assert warns, "no --focus warning was issued"
    warn = warns[0]
    assert "o/r#2" in warn and "o/r#1" not in warn
    assert _main(["batch", "run", "--checkout", "/r"]) == 0
    assert "--focus applies only to review steps" not in capsys.readouterr().err


def test_run_ends_with_a_run_summary_naming_what_needs_action(monkeypatch, capsys):
    # Revert: re-add next= to run_summary.
    row = PlanRow("o/r", "/r", 1, "t", "b", "h", False,
                  {batch.model.Step.REVIEW: StepNeed(True, "x")})
    monkeypatch.setattr(batch.plan, "build_run_plan", lambda dirs: Plan("me", [row]))

    def fake_run(self):
        self.run.status = batch.model.RunStatus.DONE
        return batch.model.RunStatus.DONE

    monkeypatch.setattr(batch.scheduler.Scheduler, "run_until_blocked", fake_run)
    assert _main(["batch", "run", "--checkout", "/r", "--steps", "review",
                  "--with-review"]) == 0
    last = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert last["kind"] == "run_summary"
    assert set(last) >= {"kind", "run", "counts", "report_path"}
    assert "next" not in last
    assert (last["run"]["exit_hint"], last["run"]["active"]) == (0, False)


def test_verbose_reaches_the_scheduler(monkeypatch):
    rows = [PlanRow("o/r", "/r", 1, "t", "b1", "h", False,
                    {s: StepNeed(True, "x") for s in batch.model.STEP_ORDER})]
    seen = {}
    monkeypatch.setattr(batch.plan, "build_run_plan", lambda dirs: Plan("me", rows))

    def fake_run(self):
        seen["verbose"] = self._verbose
        self.run.status = batch.model.RunStatus.DONE
        return batch.model.RunStatus.DONE

    monkeypatch.setattr(batch.scheduler.Scheduler, "run_until_blocked", fake_run)
    assert _main(["batch", "run", "--checkout", "/r", "--verbose"]) == 0
    assert seen["verbose"] is True


def test_resume_verbose_reaches_the_scheduler(monkeypatch):
    run = _saved_v2_waiting()
    seen = {}

    def fake_run(self):
        seen["verbose"] = self._verbose
        return batch.model.RunStatus.WAITING

    monkeypatch.setattr(batch.scheduler.Scheduler, "run_until_blocked", fake_run)
    assert _main(["batch", "resume", run.id, "--verbose"]) == 1
    assert seen["verbose"] is True


def test_resume_ends_with_a_run_summary(monkeypatch, capsys):
    run = _saved_v2_waiting()
    monkeypatch.setattr(batch.scheduler.Scheduler, "run_until_blocked",
                        stops_at(batch.model.RunStatus.DONE))
    assert _main(["batch", "resume", run.id]) == 0
    last = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert last["kind"] == "run_summary"
    assert last["run"]["exit_hint"] == 0
    assert "next" not in last


def test_a_run_summary_after_apply_errors_hints_the_real_exit_code(monkeypatch, capsys):
    run = _saved_v2_waiting()
    reqs = batch.store.run_dir(run.id) / "requests"
    reqs.mkdir(exist_ok=True)
    (reqs / "bad.json").write_text('{"kind": "decide"}', encoding="utf-8")
    monkeypatch.setattr(batch.scheduler.Scheduler, "run_until_blocked",
                        stops_at(batch.model.RunStatus.DONE))
    assert _main(["batch", "resume", run.id]) == 1
    last = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert last["kind"] == "run_summary"
    assert last["run"]["exit_hint"] == 1


def test_a_started_run_takes_over_its_plans_namespace_from_the_claim(monkeypatch):
    plan = _plan_with_refs()
    rows = [PlanRow("o/r", "/r", 1, "t", "b1", "h", False,
                    {s: StepNeed(True, "x") for s in batch.model.STEP_ORDER})]
    plan.rows.extend(rows)
    batch.plan.write_claim(plan.ref_namespace, plan.ref_dirs)
    monkeypatch.setattr(batch.plan, "build_run_plan", lambda dirs: plan)
    # Leave refs and claims alone so only the hand-over can retire the claim.
    monkeypatch.setattr(batch.plan, "drop_refs", lambda dirs, ns: True)
    seen = {}

    def fake_run(self):
        seen["claim"] = batch.plan.read_claim(plan.ref_namespace)
        seen["owner"] = self.run.ref_namespace
        self.run.status = batch.model.RunStatus.DONE
        return batch.model.RunStatus.DONE

    monkeypatch.setattr(batch.scheduler.Scheduler, "run_until_blocked", fake_run)
    assert _main(["batch", "run", "--checkout", "/r"]) == 0
    assert seen == {"claim": None, "owner": plan.ref_namespace}


def test_resume_without_id_refuses_two_unfinished_matching_runs(monkeypatch, capsys):
    older = _seeded("20261001T120000-aaaa", repo="o/r")
    newer = _seeded("20261002T120000-bbbb", repo="o/r")
    _origin(monkeypatch, "o/r")
    assert _main(["batch", "resume"]) == 1
    err = capsys.readouterr().err
    assert older.id in err and newer.id in err
