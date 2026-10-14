"""`pr batch run` and `resume` hold the machine-wide batch lock for their whole life,
and every item's target for as long as the run lasts."""

import json
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
BIN_DIR = REPO_ROOT / "ai" / "bin"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.model  # noqa: E402
import batch.plan  # noqa: E402
import batch.scheduler  # noqa: E402
import batch.store  # noqa: E402
import cli.pr  # noqa: E402
import core.run_lock  # noqa: E402
import pr.target  # noqa: E402
from batch.plan import Plan, PlanRow, StepNeed  # noqa: E402
from batch_session_support import seed, stops_at  # noqa: E402
from conftest import make_ctx  # noqa: E402
from run_lock_support import held_elsewhere, live_pid, repo_with_origin  # noqa: E402

# Bounds every process a test here starts; generous, because a loaded runner
# schedules a fresh interpreter slowly and a flake here reads as a lock bug.
_PROC_TIMEOUT = 60
_POLL_S = 0.05


def _main(argv):
    try:
        return cli.pr.main(list(argv), bin_dir=BIN_DIR)
    except SystemExit as exc:
        return exc.code


def _batch_held():
    return core.run_lock.is_global_held(batch.store.BATCH_LOCK)


def _saved_run(rows=None):
    rows = rows or [PlanRow("o/r", "/r", 1, "t", "b", "h", False,
                            {batch.model.Step.REVIEW: StepNeed(True, "x")})]
    run = batch.scheduler.new_run(rows, steps=[batch.model.Step.REVIEW], selected=None,
                                  pool=1, auto_publish=[])
    run.status = batch.model.RunStatus.WAITING
    return seed(run)


def test_run_plans_under_the_batch_lock_and_releases_it(monkeypatch):
    seen = {}

    def build_plan(dirs):
        seen["held"] = _batch_held()
        return Plan("me", [])

    monkeypatch.setattr(batch.plan, "build_plan", build_plan)
    assert _main(["batch", "run", "--checkout", "/r"]) == 0
    assert seen["held"] is True
    assert not _batch_held()


def test_run_is_refused_while_another_run_holds_the_batch_lock(monkeypatch, capsys):
    planned = []
    monkeypatch.setattr(batch.plan, "build_plan",
                        lambda dirs: planned.append(dirs) or Plan("me", []))
    with live_pid() as pid, held_elsewhere(
            core.run_lock.global_lock_path("batch"),
            {"pid": pid, "command": "pr batch run --checkout /x", "started": "t0",
             "run": "20261013T000000-abcd"}):
        seen = pid
        code = _main(["batch", "run", "--checkout", "/r"])
    assert code == 1
    assert planned == []
    err = capsys.readouterr().err
    assert "owns this global batch" in err
    assert "run 20261013T000000-abcd" in err
    assert f"pid {seen}" in err


def test_resume_is_refused_while_another_run_holds_the_batch_lock(monkeypatch, capsys):
    run = _saved_run()
    driven = []
    monkeypatch.setattr(batch.scheduler.Scheduler, "run_until_blocked",
                        lambda self: driven.append(1) or batch.model.RunStatus.WAITING)
    with live_pid() as pid, held_elsewhere(core.run_lock.global_lock_path("batch"),
                                           {"pid": pid, "command": "pr batch run"}):
        code = _main(["batch", "resume", run.id])
    assert code == 1
    assert driven == []
    assert "owns this global batch" in capsys.readouterr().err


def test_a_run_names_itself_on_the_batch_lock(monkeypatch):
    monkeypatch.setattr(batch.plan, "build_plan", lambda dirs: Plan("me", []))
    seen = {}

    def fake_run(self):
        seen["record"] = json.loads(core.run_lock.global_lock_path("batch").read_text())
        return batch.model.RunStatus.DONE

    monkeypatch.setattr(batch.scheduler.Scheduler, "run_until_blocked", fake_run)
    assert _main(["batch", "run", "--checkout", "/r"]) == 0
    assert seen["record"]["run"] == batch.store.latest_run_id()


def test_resume_holds_the_batch_lock_and_names_the_run(monkeypatch):
    run = _saved_run()
    seen = {}

    def fake_run(self):
        seen["held"] = _batch_held()
        seen["record"] = json.loads(core.run_lock.global_lock_path("batch").read_text())
        return batch.model.RunStatus.WAITING

    monkeypatch.setattr(batch.scheduler.Scheduler, "run_until_blocked", fake_run)
    assert _main(["batch", "resume", run.id]) == 10
    assert seen["held"] is True
    assert seen["record"]["run"] == run.id
    assert not _batch_held()


# ── two real processes ────────────────────────────────────────────────────────

# One `pr batch run` per process. Role "a" parks inside planning until the test
# writes `release`; role "b" plans at once. Each touches `<role>-planning` when
# it gets that far, which is what tells the test whether "b" was let in.
_HARNESS = textwrap.dedent("""\
    import pathlib, sys, time
    lib, signals, role, bin_dir = sys.argv[1:5]
    sys.path.insert(0, lib)
    import batch.plan
    import cli.pr
    signals = pathlib.Path(signals)

    def build_plan(dirs):
        (signals / f"{role}-planning").touch()
        deadline = time.monotonic() + 30
        while role == "a" and not (signals / "release").exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        return batch.plan.Plan("me", [])

    batch.plan.build_plan = build_plan
    sys.exit(cli.pr.main(["batch", "run", "--checkout", str(signals)],
                         bin_dir=pathlib.Path(bin_dir)))
""")


def _harness(signals, role):
    return [sys.executable, "-c", _HARNESS, str(LIB_DIR), str(signals), role, str(BIN_DIR)]


def _wait_for(path, proc):
    """Poll for *path* while *proc* is alive; fail naming what *proc* printed if it never appears."""
    deadline = time.monotonic() + _PROC_TIMEOUT
    while not path.exists() and proc.poll() is None and time.monotonic() < deadline:
        time.sleep(_POLL_S)
    assert path.exists(), f"never saw {path.name}: {proc.communicate(timeout=_PROC_TIMEOUT)}"


def test_two_concurrent_runs_exactly_one_proceeds(tmp_path):
    first = subprocess.Popen(_harness(tmp_path, "a"), stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
    try:
        _wait_for(tmp_path / "a-planning", first)
        second = subprocess.run(_harness(tmp_path, "b"), capture_output=True, text=True,
                                timeout=_PROC_TIMEOUT)
    finally:
        (tmp_path / "release").touch()
        _, first_err = first.communicate(timeout=_PROC_TIMEOUT)
    assert second.returncode == 1, second.stderr
    assert "owns this global batch" in second.stderr
    assert not (tmp_path / "b-planning").exists()
    assert first.returncode == 0, first_err


# ── every item's target, for the life of the run ─────────────────────────────


def _row(repo, n, branch):
    return PlanRow("acme/widget", str(repo), n, "t", branch, "h", False,
                   {batch.model.Step.REVIEW: StepNeed(True, "x")})


def _target(repo, branch):
    return pr.target.target_dir(pr.target.repo_key_from_origin(str(repo)), branch)


def test_run_holds_every_items_target_until_it_settles(monkeypatch, tmp_path):
    repo = repo_with_origin(tmp_path / "repo")
    monkeypatch.setattr(batch.plan, "build_plan",
                        lambda dirs: Plan("me", [_row(repo, 1, "feat/a"), _row(repo, 2, "feat/b")]))
    seen = {}

    def fake_run(self):
        seen["held"] = [core.run_lock.is_held(_target(repo, b)) for b in ("feat/a", "feat/b")]
        self.run.status = batch.model.RunStatus.DONE
        return batch.model.RunStatus.DONE

    monkeypatch.setattr(batch.scheduler.Scheduler, "run_until_blocked", fake_run)
    assert _main(["batch", "run", "--checkout", str(repo)]) == 0
    assert seen["held"] == [True, True]
    assert not core.run_lock.is_held(_target(repo, "feat/a"))


def test_run_is_refused_whole_when_one_item_is_being_worked_on(monkeypatch, tmp_path, capsys):
    repo = repo_with_origin(tmp_path / "repo")
    monkeypatch.setattr(batch.plan, "build_plan",
                        lambda dirs: Plan("me", [_row(repo, 1, "feat/a"), _row(repo, 2, "feat/b")]))
    driven = []
    monkeypatch.setattr(batch.scheduler.Scheduler, "run_until_blocked",
                        lambda self: driven.append(1) or batch.model.RunStatus.DONE)
    with live_pid() as pid, held_elsewhere(
            _target(repo, "feat/b") / core.run_lock.LOCK_FILE,
            {"pid": pid, "command": "pr fix", "started": "t0"}):
        seen = pid
        code = _main(["batch", "run", "--checkout", str(repo)])
    assert code == 1
    assert driven == []
    assert batch.store.latest_run_id() is None
    assert not core.run_lock.is_held(_target(repo, "feat/a"))
    assert f"acme/widget#2 is held by pr fix (pid {seen}" in capsys.readouterr().err


def test_resume_is_refused_when_an_item_is_held(monkeypatch, tmp_path, capsys):
    repo = repo_with_origin(tmp_path / "repo")
    run = _saved_run([_row(repo, 1, "feat/a")])
    driven = []
    monkeypatch.setattr(batch.scheduler.Scheduler, "run_until_blocked",
                        lambda self: driven.append(1) or batch.model.RunStatus.WAITING)
    with live_pid() as pid, held_elsewhere(
            _target(repo, "feat/a") / core.run_lock.LOCK_FILE,
            {"pid": pid, "command": "pr fix", "started": "t0"}):
        code = _main(["batch", "resume", run.id])
    assert code == 1
    assert driven == []
    assert f"run {run.id} not resumed: acme/widget#1 is held by pr fix" in capsys.readouterr().err


def test_an_item_with_no_origin_is_left_to_lock_itself(monkeypatch, capsys):
    row = PlanRow("o/r", "/r", 1, "t", "b", "h", False,
                  {batch.model.Step.REVIEW: StepNeed(True, "x")})
    monkeypatch.setattr(batch.plan, "build_plan", lambda dirs: Plan("me", [row]))
    monkeypatch.setattr(batch.scheduler.Scheduler, "run_until_blocked",
                        stops_at(batch.model.RunStatus.DONE))
    assert _main(["batch", "run", "--checkout", "/r"]) == 0
    assert "cannot name the pr target of o/r#1" in capsys.readouterr().err


# A `pr batch run` over one real PR whose scheduler parks until `release`, so
# the batch holds that PR's target while the test acts as a second session.
_HOLDING_HARNESS = textwrap.dedent("""\
    import pathlib, sys, time
    lib, signals, repo, bin_dir = sys.argv[1:5]
    sys.path.insert(0, lib)
    import batch.model, batch.plan, batch.scheduler
    import cli.pr
    signals = pathlib.Path(signals)
    row = batch.plan.PlanRow("acme/widget", repo, 1, "t", "feat/x", "h", False,
                             {batch.model.Step.REVIEW: batch.plan.StepNeed(True, "x")})
    batch.plan.build_plan = lambda dirs: batch.plan.Plan("me", [row])

    def run_until_blocked(self):
        (signals / "holding").touch()
        deadline = time.monotonic() + 30
        while not (signals / "release").exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        self.run.status = batch.model.RunStatus.DONE
        return batch.model.RunStatus.DONE

    batch.scheduler.Scheduler.run_until_blocked = run_until_blocked
    sys.exit(cli.pr.main(["batch", "run", "--checkout", repo], bin_dir=pathlib.Path(bin_dir)))
""")


def _manual_fix(target):
    """`pr fix` on *target* in this process, stopped short of any real work."""
    ctx = make_ctx(target_dir=target, worktree_root=None)
    with patch("pr.context.resolve", return_value=ctx), \
         patch("pr.sync.update_to_remote", return_value=ctx) as update, \
         patch("cli.pr._dispatch", return_value=0) as dispatch:
        code = _main(["fix"])
    return SimpleNamespace(code=code, fetched=update.called, dispatched=dispatch.called)


def test_a_manual_pr_fix_on_a_live_batchs_pr_is_refused_naming_the_batch(tmp_path, capsys):
    repo = repo_with_origin(tmp_path / "repo")
    argv = [sys.executable, "-c", _HOLDING_HARNESS, str(LIB_DIR), str(tmp_path), str(repo),
            str(BIN_DIR)]
    live = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        _wait_for(tmp_path / "holding", live)
        run_id = batch.store.latest_run_id()
        fix = _manual_fix(_target(repo, "feat/x"))
    finally:
        (tmp_path / "release").touch()
        _, live_err = live.communicate(timeout=_PROC_TIMEOUT)
    assert fix.code == 1
    assert not fix.fetched and not fix.dispatched
    assert f"pr batch run {run_id}" in capsys.readouterr().err
    assert live.returncode == 0, live_err
