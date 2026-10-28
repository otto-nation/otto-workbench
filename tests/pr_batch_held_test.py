"""A new `pr batch run` skips PRs an unfinished run still holds."""

import json
import sys
from datetime import datetime, timedelta, timezone
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
import cli.pr_batch  # noqa: E402
from batch.model import TERMINAL_ITEM, ItemStatus, RunStatus, Step  # noqa: E402
from batch.plan import Plan, PlanRow, StepNeed  # noqa: E402
from batch_session_support import seed, seed_raw  # noqa: E402


def _main(argv):
    try:
        return cli.pr.main(list(argv), bin_dir=BIN_DIR)
    except SystemExit as exc:
        return exc.code


def _row(pr, *, repo="o/r", repo_key="hold-key-aaaaaaaa", repo_dir="/r"):
    return PlanRow(repo, repo_dir, pr, "t", f"b{pr}", "h", False,
                   {Step.REVIEW: StepNeed(True, "x")}, repo_key=repo_key)


def _seed_holder(row, *, status=RunStatus.WAITING, item_status=ItemStatus.QUEUED,
                 run_id="20261020T164736-afec", started_at=""):
    run = batch.scheduler.new_run(
        [row], steps=[Step.REVIEW], selected=None, pool=1, auto_publish=[])
    run.id = run_id
    run.status = status
    run.items[0].status = item_status
    if started_at:
        run.started_at = started_at
    if status in batch.model.FINISHED_RUN or item_status in TERMINAL_ITEM:
        return seed_raw(run)
    return seed(run)


def test_started_ago_formats_buckets():
    now = datetime.now(timezone.utc)
    assert batch.store.started_ago("nope") == "at an unknown time"
    assert batch.store.started_ago("") == "at an unknown time"
    assert batch.store.started_ago((now - timedelta(seconds=10)).isoformat()) == "<1m ago"
    assert batch.store.started_ago((now - timedelta(minutes=5)).isoformat()) == "5m ago"
    assert batch.store.started_ago((now - timedelta(hours=3, minutes=10)).isoformat()) == "3h ago"
    assert batch.store.started_ago((now - timedelta(days=2, hours=1)).isoformat()) == "2d ago"


def test_holds_item_matches_by_repo_key_or_label():
    held = batch.store.HeldItem(
        run_id="r", started_at="t", item_key="o/r#1", repo_key="abc", repo="o/r", pr=1)
    assert batch.store.holds_item(held, 1, "other/x", "abc")
    assert not batch.store.holds_item(held, 1, "o/r", "zzz")
    unlabeled = batch.store.HeldItem(
        run_id="r", started_at="t", item_key="o/r#1", repo_key="", repo="o/r", pr=1)
    assert batch.store.holds_item(unlabeled, 1, "O/R", "")
    assert not batch.store.holds_item(unlabeled, 2, "o/r", "")


def _no_locks(monkeypatch):
    monkeypatch.setattr(cli.pr_batch, "_require_batch_locks", lambda *a, **k: None)


def test_run_skips_a_pr_held_by_repo_key(monkeypatch, capsys):
    started = (datetime.now(timezone.utc) - timedelta(hours=3, minutes=10)).isoformat(
        timespec="seconds")
    _seed_holder(_row(1), started_at=started)
    planned = [_row(1), _row(2)]
    _no_locks(monkeypatch)
    monkeypatch.setattr(batch.plan, "build_run_plan", lambda dirs: Plan("me", planned))
    seen = {}

    def fake_run(self):
        seen["items"] = [i.key for i in self.run.items]
        self.run.status = RunStatus.DONE
        return RunStatus.DONE

    monkeypatch.setattr(batch.scheduler.Scheduler, "run_until_blocked", fake_run)
    assert _main(["batch", "run", "--checkout", "/r"]) == 0
    captured = capsys.readouterr()
    assert seen["items"] == ["o/r#2"]
    err = captured.err
    assert "skipping o/r#1" in err
    assert "run 20261020T164736-afec" in err
    assert "started 3h ago" in err
    assert "`pr batch release 20261020T164736-afec o/r#1`" in err
    assert "`pr batch cancel 20261020T164736-afec`" in err
    events = [json.loads(line) for line in captured.out.splitlines() if line.startswith("{")]
    skipped = [e for e in events if e.get("kind") == "item_skipped_held"]
    assert skipped and skipped[0]["item"] == "o/r#1"
    assert skipped[0]["holder"] == "20261020T164736-afec"
    assert "run" not in skipped[0]


def test_run_skips_a_pr_held_by_label_when_the_seeded_item_has_no_key(
        monkeypatch, capsys):
    _seed_holder(_row(1, repo="o/r", repo_key="", repo_dir="/missing"))
    planned = [_row(1, repo="O/R", repo_key="")]
    _no_locks(monkeypatch)
    monkeypatch.setattr(batch.plan, "build_run_plan", lambda dirs: Plan("me", planned))
    seen = {}

    def fake_run(self):
        seen["items"] = [i.key for i in self.run.items]
        self.run.status = RunStatus.DONE
        return RunStatus.DONE

    monkeypatch.setattr(batch.scheduler.Scheduler, "run_until_blocked", fake_run)
    assert _main(["batch", "run", "--checkout", "/r"]) == 0
    assert seen["items"] == []
    assert "skipping O/R#1" in capsys.readouterr().err


def test_run_does_not_skip_a_pr_held_only_by_a_finished_run(monkeypatch):
    _seed_holder(_row(1), status=RunStatus.DONE, item_status=ItemStatus.QUEUED)
    planned = [_row(1)]
    _no_locks(monkeypatch)
    monkeypatch.setattr(batch.plan, "build_run_plan", lambda dirs: Plan("me", planned))
    seen = {}

    def fake_run(self):
        seen["items"] = [i.key for i in self.run.items]
        self.run.status = RunStatus.DONE
        return RunStatus.DONE

    monkeypatch.setattr(batch.scheduler.Scheduler, "run_until_blocked", fake_run)
    assert _main(["batch", "run", "--checkout", "/r"]) == 0
    assert seen["items"] == ["o/r#1"]


def test_run_does_not_skip_a_pr_whose_item_is_already_terminal(monkeypatch):
    _seed_holder(_row(1), item_status=ItemStatus.DROPPED)
    planned = [_row(1)]
    _no_locks(monkeypatch)
    monkeypatch.setattr(batch.plan, "build_run_plan", lambda dirs: Plan("me", planned))
    seen = {}

    def fake_run(self):
        seen["items"] = [i.key for i in self.run.items]
        self.run.status = RunStatus.DONE
        return RunStatus.DONE

    monkeypatch.setattr(batch.scheduler.Scheduler, "run_until_blocked", fake_run)
    assert _main(["batch", "run", "--checkout", "/r"]) == 0
    assert seen["items"] == ["o/r#1"]
