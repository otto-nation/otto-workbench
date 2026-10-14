"""`pr batch run` and `resume` hold the machine-wide batch lock from before they look for an active run."""

import json
import subprocess
import sys
import textwrap
import time
from pathlib import Path

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
from batch.plan import Plan, PlanRow, StepNeed  # noqa: E402
from run_lock_support import held_elsewhere  # noqa: E402

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
    batch.store.save(run)
    return run


def test_run_asks_for_an_active_run_only_under_the_batch_lock(monkeypatch):
    """The reported race: the question and the run it guards were seconds apart."""
    seen = {}

    def active():
        seen["held"] = _batch_held()
        return "20261001T000000-aaaa"

    monkeypatch.setattr(batch.store, "active_run_id", active)
    assert _main(["batch", "run", "--checkout", "/r"]) == 1
    assert seen["held"] is True


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
    record = {"pid": 4242, "command": "pr batch run --checkout /x", "started": "t0",
              "run": "20261013T000000-abcd"}
    with held_elsewhere(core.run_lock.global_lock_path("batch"), record):
        code = _main(["batch", "run", "--checkout", "/r"])
    assert code == 1
    assert planned == []
    err = capsys.readouterr().err
    assert "owns this global batch" in err
    assert "run 20261013T000000-abcd" in err


def test_resume_is_refused_while_another_run_holds_the_batch_lock(monkeypatch, capsys):
    run = _saved_run()
    driven = []
    monkeypatch.setattr(batch.scheduler.Scheduler, "run_until_blocked",
                        lambda self: driven.append(1) or batch.model.RunStatus.WAITING)
    with held_elsewhere(core.run_lock.global_lock_path("batch"),
                        {"pid": 4242, "command": "pr batch run"}):
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
