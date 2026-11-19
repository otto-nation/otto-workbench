"""CLI for `pr batch report` and the conclude-time report files.

Revert the offline guard in write_report (`tree=None` / `live.skipped`) and
`--offline` must start calling peek_tree or live.view.
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.live  # noqa: E402
import batch.model  # noqa: E402
import batch.orchestrate  # noqa: E402
import batch.plan  # noqa: E402
import batch.publish  # noqa: E402
import batch.queue_entries  # noqa: E402
import batch.store  # noqa: E402
import cli.pr  # noqa: E402
from batch.plan import Plan, PlanRow, StepNeed  # noqa: E402
from batch_report_support import make_item, make_run  # noqa: E402
from batch_session_support import seed_raw  # noqa: E402
from otto_queue.model import EntryState  # noqa: E402

BIN_DIR = REPO_ROOT / "ai" / "bin"


def _main(argv):
    try:
        return cli.pr.main(list(argv), bin_dir=BIN_DIR)
    except SystemExit as exc:
        return exc.code


def _boom(*args, **kwargs):
    raise AssertionError("online read")


def test_report_writes_files_and_prints_path(capsys):
    item = make_item(1)
    run = make_run([item])
    seed_raw(run)
    assert _main(["batch", "report", run.id, "--offline"]) == 0
    directory = batch.store.run_dir(run.id)
    md = directory / "report.md"
    js = directory / "report.json"
    assert md.is_file() and js.is_file()
    out = capsys.readouterr().out.strip().splitlines()
    assert out[-1] == f"report: {md}"
    assert Path(out[-1].split(" ", 1)[1]).is_absolute()


def test_active_run_report_writes_no_files(monkeypatch, capsys):
    item = make_item(1, jobs=[batch.model.Job(
        step=batch.model.Step.REVIEW, entry_id=1, submitted_at="t")])
    run = make_run([item], status=batch.model.RunStatus.RUNNING)
    seed_raw(run)
    monkeypatch.setattr(
        batch.queue_entries.QueueEntries, "states",
        lambda self, ids: {1: EntryState.RUNNING})
    assert _main(["batch", "report", run.id, "--offline", "--format", "json"]) == 0
    directory = batch.store.run_dir(run.id)
    assert not (directory / "report.md").exists()
    assert not (directory / "report.json").exists()
    capsys.readouterr()


def test_offline_calls_neither_github_nor_peek_tree(monkeypatch, capsys):
    # Revert: drop `offline` in write_report (always peek / live.view).
    item = make_item(1)
    run = make_run([item])
    seed_raw(run)
    monkeypatch.setattr(batch.live, "view", _boom)
    monkeypatch.setattr(batch.publish, "peek_tree", _boom)
    assert _main(["batch", "report", run.id, "--offline"]) == 0
    assert "report:" in capsys.readouterr().out


def test_run_emits_run_started_as_first_event(monkeypatch, capsys):
    row = PlanRow("o/r", "/r", 1, "t", "b", "h", False,
                  {batch.model.Step.REBASE: StepNeed(True, "x")})
    monkeypatch.setattr(batch.plan, "build_run_plan", lambda dirs, prs=None: Plan("me", [row]))
    monkeypatch.setattr(batch.orchestrate, "submit_ready", lambda *a, **k: 0)
    assert _main(["batch", "run", "--checkout", "/r", "--steps", "rebase"]) == 0
    kinds = [json.loads(line)["kind"] for line in capsys.readouterr().out.splitlines()
             if line.startswith("{")]
    assert kinds[0] == "run_started"


