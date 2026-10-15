"""`pr batch` resume, resolve and cancel as run sessions: the races a bare run lock left open."""

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
BIN_DIR = REPO_ROOT / "ai" / "bin"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.events  # noqa: E402
import batch.plan  # noqa: E402
import batch.scheduler  # noqa: E402
import batch.store  # noqa: E402
import cli.pr  # noqa: E402
import cli.pr_batch  # noqa: E402
import core.run_lock  # noqa: E402
import pr.target  # noqa: E402
from batch.model import (Decision, DecisionKind, ItemStatus, RunStatus, Step,  # noqa: E402
                         StepStatus)
from batch.plan import Plan, PlanRow, StepNeed  # noqa: E402
from batch.requests import Cancel  # noqa: E402
from batch_session_support import holding, seed, stops_at  # noqa: E402
from run_lock_support import held_elsewhere, repo_with_origin  # noqa: E402


def _main(argv):
    try:
        return cli.pr.main(list(argv), bin_dir=BIN_DIR)
    except SystemExit as exc:
        return exc.code


def _run():
    """A WAITING run, not yet saved, whose one item awaits FAILED decision `d1`.

    `drop-pr` answers `d1` without running any command.
    """
    row = PlanRow("o/r", "/r", 1, "t", "b", "h", False, {Step.REVIEW: StepNeed(True, "x")})
    run = batch.scheduler.new_run([row], steps=[Step.REVIEW], selected=None, pool=1,
                                  auto_publish=[])
    item = run.items[0]
    item.worktree = "/wt"
    item.step(Step.REVIEW).status = StepStatus.NEEDS_DECISION
    item.status = ItemStatus.AWAITING_DECISION
    run.decisions.append(Decision(id="d1", item=item.key, step="review",
                                  kind=DecisionKind.FAILED))
    run.status = RunStatus.WAITING
    return run


def _requests(run_id):
    reqs = batch.store.run_dir(run_id) / "requests"
    return sorted(p.name for p in reqs.iterdir()) if reqs.is_dir() else []


def _events(out):
    return [json.loads(line) for line in out.splitlines() if line.strip()]


# ── R1/R4: resume loads only under the run lock ──────────────────────────────

def test_resume_drives_only_a_run_it_loaded_under_the_run_lock(monkeypatch):
    run = seed(_run())
    lock_file = batch.store.run_lock(run.id).path
    loads = []
    real = batch.store.load

    def spy(run_id):
        loaded = real(run_id)
        loads.append((core.run_lock.holds(lock_file), loaded))
        return loaded

    driven = {}

    def fake_run(self):
        driven["run"] = self.run
        self.run.status = RunStatus.WAITING
        return RunStatus.WAITING

    monkeypatch.setattr(batch.store, "load", spy)
    monkeypatch.setattr(batch.scheduler.Scheduler, "run_until_blocked", fake_run)
    assert _main(["batch", "resume", run.id]) == 10
    assert loads[0][0] is True
    assert [held for held, loaded in loads if loaded is driven["run"]] == [True]


# ── R2: a cancel queued while an applier holds the run is applied ────────────

def test_a_cancel_queued_during_an_applier_hold_is_applied_by_the_next_session(capsys):
    run = seed(_run())
    with holding(run.id, "applier"):
        assert _main(["batch", "cancel", run.id]) == 0
    assert json.loads(capsys.readouterr().out) == {"cancelled": True, "run": run.id,
                                                    "queued": True}
    assert batch.store.load(run.id).status is RunStatus.WAITING
    assert batch.store.pending_requests(run.id) == 1
    assert _main(["batch", "resolve", run.id, "d1", "--action", "drop-pr"]) == 0
    saved = batch.store.load(run.id)
    assert saved.status is RunStatus.CANCELLED
    assert saved.cancel is not None
    assert batch.store.pending_requests(run.id) == 0


# ── R5: a busy resume does nothing before it is refused ──────────────────────

def test_a_busy_resume_emits_nothing_and_deletes_nothing(monkeypatch, capsys):
    run = _run()
    run.items[0].step(Step.REVIEW).status = StepStatus.RUNNING
    run.status = RunStatus.INTERRUPTED
    seed(run)
    batch.store.write_request(run.id, Cancel())
    queued = _requests(run.id)
    emitted = []
    monkeypatch.setattr(batch.events, "emit", lambda kind, **fields: emitted.append(kind))
    with holding(run.id, "applier"):
        code = _main(["batch", "resume", run.id])
    assert code == 1
    assert emitted == []
    assert _requests(run.id) == queued
    assert batch.store.load(run.id).status is RunStatus.INTERRUPTED
    assert (f"run {run.id} is busy: applying a decision (pr batch resolve, "
            f"pid {os.getppid()}, started t0)") in capsys.readouterr().err


# ── R6: a new run is locked before it exists, and saved only once its items are held ─

@dataclass(frozen=True)
class _Seen:
    locked: bool
    saved: bool


def _seen(run_id):
    """Whether this process holds *run_id*'s lock, and whether its state is on disk."""
    return _Seen(core.run_lock.holds(batch.store.run_lock(run_id).path),
                 (batch.store.run_dir(run_id) / batch.store.STATE_FILE).exists())


def _row(repo, n, branch):
    return PlanRow("acme/widget", str(repo), n, "t", branch, "h", False,
                   {Step.REVIEW: StepNeed(True, "x")})


def _target(repo, branch):
    return pr.target.target_dir(pr.target.repo_key_from_origin(str(repo)), branch)


def test_a_new_run_is_locked_before_its_items_and_saved_only_after_them(monkeypatch, tmp_path):
    repo = repo_with_origin(tmp_path / "repo")
    monkeypatch.setattr(batch.plan, "build_plan",
                        lambda dirs: Plan("me", [_row(repo, 1, "feat/a")]))
    monkeypatch.setattr(batch.scheduler.Scheduler, "run_until_blocked",
                        stops_at(RunStatus.DONE))
    seen = []
    real = cli.pr_batch._hold_items

    def spy(held, run, verb):
        seen.append(_seen(run.id))
        real(held, run, verb)
        seen.append(_seen(run.id))

    monkeypatch.setattr(cli.pr_batch, "_hold_items", spy)
    assert _main(["batch", "run", "--checkout", str(repo)]) == 0
    assert seen == [_Seen(locked=True, saved=False), _Seen(locked=True, saved=False)]


# passes-at-base: the old order also refused before its first save; this pins that create() keeps it
def test_a_run_refused_an_item_leaves_no_run_directory(monkeypatch, tmp_path):
    repo = repo_with_origin(tmp_path / "repo")
    monkeypatch.setattr(batch.plan, "build_plan",
                        lambda dirs: Plan("me", [_row(repo, 1, "feat/a")]))
    record = {"pid": os.getppid(), "command": "pr fix", "started": "t0"}
    with held_elsewhere(_target(repo, "feat/a") / core.run_lock.LOCK_FILE, record):
        assert _main(["batch", "run", "--checkout", str(repo)]) == 1
    root = batch.store.batch_root()
    assert not root.exists() or list(root.iterdir()) == []


# ── a cancel the driver's own finish applies ─────────────────────────────────

def test_a_cancel_queued_during_the_drive_ends_the_run_cancelled(monkeypatch, capsys):
    run = seed(_run())

    def fake_run(self):
        # A submitter's cancel, refused by this driver's lock and queued for it.
        batch.store.write_request(self.run.id, Cancel())
        self.run.status = RunStatus.WAITING
        return RunStatus.WAITING

    monkeypatch.setattr(batch.scheduler.Scheduler, "run_until_blocked", fake_run)
    assert _main(["batch", "resume", run.id]) == 0
    lines = _events(capsys.readouterr().out)
    assert [e["status"] for e in lines if e["kind"] == "run_finished"] == ["cancelled"]
    assert lines[-1]["kind"] == "run_summary"
    assert (lines[-1]["run"]["status"], lines[-1]["run"]["exit_hint"]) == ("cancelled", 0)
    assert batch.store.load(run.id).status is RunStatus.CANCELLED


# ── what status says about holders and the inbox ─────────────────────────────

def test_an_applier_hold_is_not_a_live_scheduler_and_its_queue_is_reported(capsys):
    run = seed(_run())
    with holding(run.id, "applier"):
        assert _main(["batch", "resolve", run.id, "d1", "--action", "drop-pr"]) == 0
        capsys.readouterr()
        assert _main(["batch", "status", run.id]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["run"]["active"] is False
    assert report["run"]["pending_requests"] == 1
    assert [e["decision"] for e in report["next"]] == ["d1"]


def test_a_rejected_request_is_listed_by_next(capsys):
    run = seed(_run())
    assert _main(["batch", "resolve", run.id, "nope", "--action", "drop-pr"]) == 1
    capsys.readouterr()
    assert _main(["batch", "next", run.id]) == 0
    view = json.loads(capsys.readouterr().out)
    [failed] = view["run"]["failed_requests"]
    assert "nope" in failed["error"]
    assert view["run"]["pending_requests"] == 0


def test_a_resolve_that_cannot_reach_the_run_says_where_the_request_is(monkeypatch, capsys):
    run = seed(_run())
    monkeypatch.setattr(core.run_lock, "retry_delays", lambda: iter([0.0]))
    record = {"pid": os.getppid(), "command": "pr batch resolve", "started": "t0",
              "released": "t1"}
    with held_elsewhere(batch.store.run_lock(run.id).path, record):
        code = _main(["batch", "resolve", run.id, "d1", "--action", "drop-pr"])
    assert code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert (f"the request is in its inbox and will be applied by the next "
            f"session to open it") in captured.err
    assert batch.store.pending_requests(run.id) == 1
