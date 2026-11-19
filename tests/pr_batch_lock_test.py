"""`pr batch run` does not hold item targets; two runs over one repo proceed side by side.
`--continue` submits on an existing run."""

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
import batch.orchestrate  # noqa: E402
import batch.plan  # noqa: E402
import batch.scheduler  # noqa: E402
import batch.store  # noqa: E402
import cli.pr  # noqa: E402
import core.run_lock  # noqa: E402
import pr.target  # noqa: E402
from batch.plan import Plan, PlanRow, StepNeed  # noqa: E402
from batch.requests import Release  # noqa: E402
from batch_session_support import seed  # noqa: E402
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


def _saved_run(rows=None):
    rows = rows or [PlanRow("o/r", "/r", 1, "t", "b", "h", False,
                            {batch.model.Step.REBASE: StepNeed(True, "x")},
                            repo_key="acme-widget-testhash")]
    run = batch.scheduler.new_run(rows, steps=[batch.model.Step.REBASE], selected=None,
                                  pool=1)
    run.status = batch.model.RunStatus.WAITING
    return seed(run)


# ── two real processes ────────────────────────────────────────────────────────

# One `pr batch run` per process. Role "a" parks inside planning until the test
# writes `release`; role "b" plans at once. Each touches `<role>-planning` when
# it gets that far, which is what tells the test "b" was let in while "a" was
# still live over the same repo.
_HARNESS = textwrap.dedent("""\
    import pathlib, sys, time
    lib, signals, role, bin_dir, repo = sys.argv[1:6]
    sys.path.insert(0, lib)
    import batch.plan
    import cli.pr
    signals = pathlib.Path(signals)

    def build_run_plan(dirs, prs=None):
        (signals / f"{role}-planning").touch()
        deadline = time.monotonic() + 30
        while role == "a" and not (signals / "release").exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        return batch.plan.Plan("me", [])

    batch.plan.build_run_plan = build_run_plan
    sys.exit(cli.pr.main(["batch", "run", "--checkout", repo, "--steps", "rebase"],
                         bin_dir=pathlib.Path(bin_dir)))
""")


def _harness(signals, role, repo):
    return [sys.executable, "-c", _HARNESS, str(LIB_DIR), str(signals), role, str(BIN_DIR),
            str(repo)]


def _wait_for(path, proc):
    """Poll for *path* while *proc* is alive; fail naming what *proc* printed if it never appears."""
    deadline = time.monotonic() + _PROC_TIMEOUT
    while not path.exists() and proc.poll() is None and time.monotonic() < deadline:
        time.sleep(_POLL_S)
    assert path.exists(), f"never saw {path.name}: {proc.communicate(timeout=_PROC_TIMEOUT)}"


def test_two_concurrent_runs_over_one_repo_both_proceed(tmp_path):
    repo = repo_with_origin(tmp_path / "repo")
    first = subprocess.Popen(_harness(tmp_path, "a", repo), stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
    try:
        _wait_for(tmp_path / "a-planning", first)
        second = subprocess.run(_harness(tmp_path, "b", repo), capture_output=True, text=True,
                                timeout=_PROC_TIMEOUT)
    finally:
        (tmp_path / "release").touch()
        _, first_err = first.communicate(timeout=_PROC_TIMEOUT)
    assert second.returncode == 0, second.stderr
    assert (tmp_path / "b-planning").exists()
    assert first.returncode == 0, first_err


# ── item targets are not held by run / --continue ────────────────────────────


def _row(repo, n, branch):
    return PlanRow("acme/widget", str(repo), n, "t", branch, "h", False,
                   {batch.model.Step.REBASE: StepNeed(True, "x")})


def _target(repo, branch):
    return pr.target.target_dir(pr.target.repo_key_from_origin(str(repo)), branch)


def test_run_does_not_hold_item_targets(monkeypatch, tmp_path):
    repo = repo_with_origin(tmp_path / "repo")
    monkeypatch.setattr(batch.plan, "build_run_plan",
                        lambda dirs, prs=None: Plan("me", [_row(repo, 1, "feat/a"), _row(repo, 2, "feat/b")]))
    seen = {}

    def fake_submit(run, **kwargs):
        seen["held"] = [core.run_lock.is_held(_target(repo, b)) for b in ("feat/a", "feat/b")]
        return 0

    monkeypatch.setattr(batch.orchestrate, "submit_ready", fake_submit)
    assert _main(["batch", "run", "--checkout", str(repo), "--steps", "rebase"]) == 0
    assert seen["held"] == [False, False]
    assert not core.run_lock.is_held(_target(repo, "feat/a"))


def test_a_queued_drop_pr_is_applied_when_the_run_session_finishes(
        monkeypatch, tmp_path):
    repo = repo_with_origin(tmp_path / "repo")
    monkeypatch.setattr(batch.plan, "build_run_plan",
                        lambda dirs, prs=None: Plan("me", [_row(repo, 1, "feat/a"), _row(repo, 2, "feat/b")]))
    seen = {}

    def fake_submit(run, **kwargs):
        first = run.items[0]
        batch.store.write_request(run.id, Release(first.key))
        seen["run_id"] = run.id
        seen["first"] = first.key
        return 0

    monkeypatch.setattr(batch.orchestrate, "submit_ready", fake_submit)
    assert _main(["batch", "run", "--checkout", str(repo), "--steps", "rebase"]) == 0
    saved = batch.store.load(seen["run_id"])
    assert saved.item(seen["first"]).status is batch.model.ItemStatus.DROPPED
    assert batch.store.pending_requests(seen["run_id"]) == 0


def test_continue_does_not_take_back_a_dropped_or_done_items_target(monkeypatch, tmp_path):
    repo = repo_with_origin(tmp_path / "repo")
    run = batch.scheduler.new_run(
        [_row(repo, 1, "feat/a"), _row(repo, 2, "feat/b"), _row(repo, 3, "feat/c")],
        steps=[batch.model.Step.REBASE], selected=None, pool=1)
    run.items[0].status = batch.model.ItemStatus.DROPPED
    run.items[1].status = batch.model.ItemStatus.DONE
    run.status = batch.model.RunStatus.WAITING
    seed(run)
    seen = {}

    def fake_submit(loaded, **kwargs):
        seen["held"] = [core.run_lock.is_held(_target(repo, b))
                        for b in ("feat/a", "feat/b", "feat/c")]
        return 0

    monkeypatch.setattr(batch.orchestrate, "submit_ready", fake_submit)
    with live_pid() as pid, held_elsewhere(
            _target(repo, "feat/a") / core.run_lock.LOCK_FILE,
            {"pid": pid, "command": "pr fix", "started": "t0"}):
        code = _main(["batch", "run", "--continue", run.id])
    assert code == 0
    assert seen["held"] == [True, False, False]


def test_run_is_not_refused_when_an_item_target_is_held(monkeypatch, tmp_path):
    repo = repo_with_origin(tmp_path / "repo")
    monkeypatch.setattr(batch.plan, "build_run_plan",
                        lambda dirs, prs=None: Plan("me", [_row(repo, 1, "feat/a"), _row(repo, 2, "feat/b")]))
    submitted = []
    monkeypatch.setattr(batch.orchestrate, "submit_ready",
                        lambda run, **kwargs: submitted.append(run.id) or 0)
    with live_pid() as pid, held_elsewhere(
            _target(repo, "feat/b") / core.run_lock.LOCK_FILE,
            {"pid": pid, "command": "pr fix", "started": "t0"}):
        code = _main(["batch", "run", "--checkout", str(repo), "--steps", "rebase"])
    assert code == 0
    assert submitted
    assert batch.store.latest_run_id() == submitted[0]
    assert not core.run_lock.is_held(_target(repo, "feat/a"))


def test_continue_is_not_refused_when_an_item_is_held(monkeypatch, tmp_path):
    repo = repo_with_origin(tmp_path / "repo")
    run = _saved_run([_row(repo, 1, "feat/a")])
    submitted = []
    monkeypatch.setattr(batch.orchestrate, "submit_ready",
                        lambda loaded, **kwargs: submitted.append(1) or 0)
    with live_pid() as pid, held_elsewhere(
            _target(repo, "feat/a") / core.run_lock.LOCK_FILE,
            {"pid": pid, "command": "pr fix", "started": "t0"}):
        code = _main(["batch", "run", "--continue", run.id])
    assert code == 0
    assert submitted == [1]


def test_continue_of_a_terminal_run_leaves_it_terminal(monkeypatch):
    run = batch.scheduler.new_run(
        [PlanRow("o/r", "/r", 1, "t", "b", "h", False,
                 {batch.model.Step.REBASE: StepNeed(True, "x")})],
        steps=[batch.model.Step.REBASE], selected=None, pool=1)
    item = run.items[0]
    item.status = batch.model.ItemStatus.AWAITING_DECISION
    item.step(batch.model.Step.REBASE).status = batch.model.StepStatus.NEEDS_DECISION
    run.decisions.append(batch.model.Decision(
        id="d1", item=item.key, step="rebase", kind=batch.model.DecisionKind.FAILED))
    run.status = batch.model.RunStatus.CANCELLED
    seed(run)
    monkeypatch.setattr(batch.orchestrate, "submit_ready", lambda loaded, **kwargs: 0)
    code = _main(["batch", "run", "--continue", run.id])
    assert code == 1
    saved = batch.store.load(run.id)
    assert saved.status is batch.model.RunStatus.CANCELLED


def test_an_item_with_no_origin_still_submits(monkeypatch):
    row = PlanRow("o/r", "/r", 1, "t", "b", "h", False,
                  {batch.model.Step.REBASE: StepNeed(True, "x")})
    monkeypatch.setattr(batch.plan, "build_run_plan", lambda dirs, prs=None: Plan("me", [row]))
    monkeypatch.setattr(batch.orchestrate, "submit_ready", lambda run, **kwargs: 0)
    assert _main(["batch", "run", "--checkout", "/r", "--steps", "rebase"]) == 0


# A `pr batch run` over one real PR that parks inside submit_ready until
# `release`. The run lock is held; item targets are not.
_HOLDING_HARNESS = textwrap.dedent("""\
    import pathlib, sys, time
    lib, signals, repo, bin_dir = sys.argv[1:5]
    sys.path.insert(0, lib)
    import batch.model, batch.orchestrate, batch.plan
    import cli.pr
    signals = pathlib.Path(signals)
    row = batch.plan.PlanRow("acme/widget", repo, 1, "t", "feat/x", "h", False,
                             {batch.model.Step.REBASE: batch.plan.StepNeed(True, "x")})
    batch.plan.build_run_plan = lambda dirs, prs=None: batch.plan.Plan("me", [row])

    def submit_ready(run, **kwargs):
        (signals / "holding").touch()
        deadline = time.monotonic() + 30
        while not (signals / "release").exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        return 0

    batch.orchestrate.submit_ready = submit_ready
    sys.exit(cli.pr.main(["batch", "run", "--checkout", repo, "--steps", "rebase"],
                         bin_dir=pathlib.Path(bin_dir)))
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
    assert fix.code == 0
    assert fix.dispatched
    assert run_id is not None
    assert live.returncode == 0, live_err
