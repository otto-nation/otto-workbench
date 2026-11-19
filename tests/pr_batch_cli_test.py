import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.live  # noqa: E402
import batch.model  # noqa: E402
import batch.orchestrate  # noqa: E402
import batch.outcomes  # noqa: E402
import batch.publish  # noqa: E402
import batch.queue_entries  # noqa: E402
import batch.scheduler  # noqa: E402
import batch.store  # noqa: E402
from batch.publish import TreeState  # noqa: E402
from pr.comments_fix import CloseoutDebt  # noqa: E402
from rebase.types import RefDivergence  # noqa: E402
import cli.pr  # noqa: E402
import core.tool_parser  # noqa: E402
import cli.pr_batch  # noqa: E402
import pr.target  # noqa: E402
from batch.plan import Plan, PlanRow, StepNeed  # noqa: E402
from batch_session_support import seed, seed_raw  # noqa: E402
from otto_queue.model import EntryState  # noqa: E402
from cli.registry import COMMANDS  # noqa: E402
from config.workbench_config import BatchConfig  # noqa: E402

BIN_DIR = REPO_ROOT / "ai" / "bin"
GLOBAL_FLAGS = ("--repo-dir", "--worktree", "--branch", "--pr", "--schema-version", "--debug")


def _main(argv):
    try:
        return cli.pr.main(list(argv), bin_dir=BIN_DIR)
    except SystemExit as exc:
        return exc.code


def _stub_submit_ready(monkeypatch, result=0):
    called = []

    def fake_submit_ready(*args, **kwargs):
        called.append(args[0] if args else kwargs["run"])
        return result

    monkeypatch.setattr(batch.orchestrate, "submit_ready", fake_submit_ready)
    return called


def test_ceiling_clamps_to_pool_max_with_a_floor_of_one():
    cfg = BatchConfig(pool_max=2)
    assert (cli.pr_batch.ceiling(5, cfg), cli.pr_batch.ceiling(None, cfg),
            cli.pr_batch.ceiling(0, cfg)) == (2, 2, 1)


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
    monkeypatch.setattr(batch.plan, "build_plan", lambda dirs, steps=None: Plan("me", [row]))
    assert _main(["batch", "plan", "--checkout", "/r"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["schema_version"] == 1
    assert out["rows"][0]["needs"]["rebase"] == {"needed": True, "reason": "behind"}


def test_run_honours_select_and_prs(monkeypatch):
    rows = [PlanRow("o/r", "/r", n, "t", f"b{n}", "h", False,
                    {s: StepNeed(False, "x") for s in batch.model.STEP_ORDER}) for n in (1, 2)]
    monkeypatch.setattr(batch.plan, "build_run_plan", lambda dirs, prs=None: Plan("me", rows))
    called = _stub_submit_ready(monkeypatch)
    assert _main(["batch", "run", "--checkout", "/r", "--prs", "o/r#2",
                  "--select", "o/r#2=rebase"]) == 0
    assert [(i.key, [s.step.value for s in i.steps
                     if s.status is batch.model.StepStatus.PENDING]) for i in called[0].items] == [
        ("o/r#2", ["rebase"])]


def test_run_submits_and_returns(monkeypatch):
    rows = [PlanRow("o/r", "/r", 1, "t", "b1", "h", False,
                    {batch.model.Step.REBASE: StepNeed(True, "x")})]
    monkeypatch.setattr(batch.plan, "build_run_plan", lambda dirs, prs=None: Plan("me", rows))
    called = []

    def fake_submit_ready(*args, **kwargs):
        called.append((args, kwargs))
        return 0

    monkeypatch.setattr(batch.orchestrate, "submit_ready", fake_submit_ready)
    assert _main(["batch", "run", "--checkout", "/r", "--steps", "rebase"]) == 0
    assert len(called) == 1


def test_continue_calls_submit_ready_on_the_existing_run(monkeypatch):
    rows = [PlanRow("o/r", "/r", 1, "t", "b", "h", False,
                    {batch.model.Step.REBASE: StepNeed(True, "x")})]
    run = batch.scheduler.new_run(rows, steps=[batch.model.Step.REBASE], selected=None,
                                  pool=1)
    seed(run)
    called = _stub_submit_ready(monkeypatch)
    assert _main(["batch", "run", "--continue", run.id]) == 0
    assert called[0].id == run.id
    dirs = sorted(p.name for p in batch.store.batch_root().iterdir() if p.is_dir())
    assert dirs == [run.id]


@pytest.mark.parametrize("status", [
    batch.model.RunStatus.DONE, batch.model.RunStatus.CANCELLED,
])
def test_continue_refuses_a_finished_run(monkeypatch, capsys, status):
    rows = [PlanRow("o/r", "/r", 1, "t", "b", "h", False,
                    {batch.model.Step.REBASE: StepNeed(True, "x")})]
    run = batch.scheduler.new_run(rows, steps=[batch.model.Step.REBASE], selected=None,
                                  pool=1)
    run.status = status
    seed_raw(run)
    called = _stub_submit_ready(monkeypatch)
    assert _main(["batch", "run", "--continue", run.id]) == 1
    assert called == []
    assert run.id in capsys.readouterr().err
    assert batch.store.load(run.id).status is status


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


def _origin(monkeypatch, label):
    monkeypatch.setattr(
        pr.target, "repo_identity_from_origin",
        lambda cwd=None: pr.target.RepoIdentity(label=label, key="r", host="github.com"))


def _seeded(run_id, *, repo="o/r", status=batch.model.RunStatus.WAITING, jobs=None):
    run = _run_with_decisions(1)
    run.id = run_id
    run.items[0].repo = repo
    run.items[0].key = f"{repo}#1"
    run.status = status
    if jobs is not None:
        run.items[0].jobs = list(jobs)
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
    other = _seeded(
        "20261002T120000-live", repo="other/r",
        jobs=[batch.model.Job(step=batch.model.Step.REVIEW, entry_id=1, submitted_at="t")])
    _origin(monkeypatch, "o/r")
    monkeypatch.setattr(
        batch.queue_entries.QueueEntries, "states",
        lambda self, ids: {1: EntryState.RUNNING})
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
    assert json.loads(capsys.readouterr().out) == {"cancelled": True, "run": run.id}
    saved = batch.store.load(run.id)
    assert saved.status is batch.model.RunStatus.CANCELLED


def test_cancel_does_not_overwrite_a_terminal_status(capsys):
    run = _run_with_decisions(1)
    run.status = batch.model.RunStatus.DONE
    seed(run)
    assert _main(["batch", "cancel", run.id]) == 1
    assert "done" in capsys.readouterr().err
    assert batch.store.load(run.id).status is batch.model.RunStatus.DONE


def test_a_cancel_that_loses_to_completion_reports_the_real_status(monkeypatch, capsys):
    run = _saved_run_with_decision()
    real = batch.store.session

    @contextmanager
    def finish_first(run_id, role, *, inbox, command):
        # Another session settles DONE between cancel's unlocked check and its lock.
        with real(run_id, batch.store.Role.DRIVER, inbox=inbox,
                  command="test finish") as other:
            other.run.status = batch.model.RunStatus.DONE
            other.commit()
        with real(run_id, role, inbox=inbox, command=command) as session:
            yield session

    monkeypatch.setattr(batch.store, "session", finish_first)
    assert _main(["batch", "cancel", run.id]) == 1
    assert f"run {run.id} is done" in capsys.readouterr().err
    assert batch.store.load(run.id).status is batch.model.RunStatus.DONE


def test_cancel_unknown_run_exits_1_without_creating_a_dir(tmp_path, capsys):
    missing = "20990101T000000-dead"
    assert _main(["batch", "cancel", missing]) == 1
    assert missing in capsys.readouterr().err
    assert not (batch.store.batch_root() / missing).exists()


def test_batch_resume_fails_usage(capsys):
    assert _main(["batch", "resume", "nope"]) == 2
    err = capsys.readouterr().err.lower()
    assert "invalid choice" in err or "unrecognized" in err or "invalid" in err


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
    monkeypatch.setattr(batch.plan, "build_plan", lambda dirs, steps=None: Plan("me", []))
    monkeypatch.setattr(batch.plan, "drop_refs", lambda dirs, ns: dropped.append((dirs, ns)))

    def gone(_payload):
        raise BrokenPipeError

    monkeypatch.setattr(cli.pr_batch.core.report, "emit_json", gone)
    with pytest.raises(BrokenPipeError):
        _main(["batch", "plan", "--checkout", "/r"])
    assert dropped == []


def test_run_drops_the_plans_refs_when_it_fails_before_saving(monkeypatch):
    dropped = []
    monkeypatch.setattr(batch.plan, "build_run_plan", lambda dirs, prs=None: _plan_with_refs())
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
    monkeypatch.setattr(batch.plan, "build_run_plan", lambda dirs, prs=None: Plan("me", rows))
    called = _stub_submit_ready(monkeypatch)
    assert _main(["batch", "run", "--checkout", "/r", "--no-verify"]) == 0
    assert called[0].no_verify is True


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
    monkeypatch.setattr(batch.plan, "build_run_plan", lambda dirs, prs=None: Plan("me", rows))
    called = _stub_submit_ready(monkeypatch)
    assert _main(["batch", "run", "--checkout", "/r", "--focus", "x"]) == 0
    assert list(called[0].focus) == ["x"]


def test_focus_warns_about_items_without_a_review_step(monkeypatch, capsys):
    rows = [
        PlanRow("o/r", "/r", 1, "t", "b1", "h", False,
                {batch.model.Step.REVIEW: StepNeed(True, "x")}),
        PlanRow("o/r", "/r", 2, "t", "b2", "h", False,
                {batch.model.Step.REBASE: StepNeed(True, "behind"),
                 batch.model.Step.REVIEW: StepNeed(False, "current")}),
    ]
    monkeypatch.setattr(batch.plan, "build_run_plan", lambda dirs, prs=None: Plan("me", rows))
    _stub_submit_ready(monkeypatch)
    assert _main(["batch", "run", "--checkout", "/r", "--focus", "x"]) == 0
    warns = [line for line in capsys.readouterr().err.splitlines()
             if "--focus applies only to review steps" in line]
    assert warns, "no --focus warning was issued"
    warn = warns[0]
    assert "o/r#2" in warn and "o/r#1" not in warn
    assert _main(["batch", "run", "--checkout", "/r"]) == 0
    assert "--focus applies only to review steps" not in capsys.readouterr().err


def test_with_review_is_not_in_the_thin_orchestrator(capsys):
    assert _main(["batch", "run", "--checkout", "/r", "--steps", "review",
                  "--with-review"]) == 2
    assert "review is not in the thin orchestrator yet" in capsys.readouterr().err


def test_verbose_run_still_submits(monkeypatch):
    rows = [PlanRow("o/r", "/r", 1, "t", "b1", "h", False,
                    {s: StepNeed(True, "x") for s in batch.model.STEP_ORDER})]
    monkeypatch.setattr(batch.plan, "build_run_plan", lambda dirs, prs=None: Plan("me", rows))
    called = _stub_submit_ready(monkeypatch)
    assert _main(["batch", "run", "--checkout", "/r", "--verbose"]) == 0
    assert len(called) == 1


def test_a_started_run_takes_over_its_plans_namespace_from_the_claim(monkeypatch):
    plan = _plan_with_refs()
    rows = [PlanRow("o/r", "/r", 1, "t", "b1", "h", False,
                    {s: StepNeed(True, "x") for s in batch.model.STEP_ORDER})]
    plan.rows.extend(rows)
    batch.plan.write_claim(plan.ref_namespace, plan.ref_dirs)
    monkeypatch.setattr(batch.plan, "build_run_plan", lambda dirs, prs=None: plan)
    # Leave refs and claims alone so only the hand-over can retire the claim.
    monkeypatch.setattr(batch.plan, "drop_refs", lambda dirs, ns: True)
    seen = {}

    def fake_submit_ready(run, **kwargs):
        seen["claim"] = batch.plan.read_claim(plan.ref_namespace)
        seen["owner"] = run.ref_namespace
        return 0

    monkeypatch.setattr(batch.orchestrate, "submit_ready", fake_submit_ready)
    assert _main(["batch", "run", "--checkout", "/r"]) == 0
    assert seen == {"claim": None, "owner": plan.ref_namespace}
