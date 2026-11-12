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
import batch.plan  # noqa: E402
import batch.publish  # noqa: E402
import batch.scheduler  # noqa: E402
import batch.store  # noqa: E402
import cli.pr  # noqa: E402
from batch.plan import Plan, PlanRow, StepNeed  # noqa: E402
from batch_report_support import make_item, make_run  # noqa: E402
from batch.store import Role  # noqa: E402
from batch_session_support import holding, seed_raw  # noqa: E402

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


def test_active_run_report_writes_no_files(capsys):
    item = make_item(1)
    run = make_run([item], status=batch.model.RunStatus.RUNNING)
    seed_raw(run)
    with holding(run.id, Role.DRIVER):
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


def test_run_summary_sets_report_path_as_last_line(monkeypatch, capsys):
    row = PlanRow("o/r", "/r", 1, "t", "b", "h", False,
                  {batch.model.Step.REVIEW: StepNeed(True, "x")})
    monkeypatch.setattr(batch.plan, "build_run_plan", lambda dirs, prs=None: Plan("me", [row]))

    def fake_run(self):
        self.run.status = batch.model.RunStatus.DONE
        self.run.items[0].status = batch.model.ItemStatus.DONE
        return batch.model.RunStatus.DONE

    monkeypatch.setattr(batch.scheduler.Scheduler, "run_until_blocked", fake_run)
    assert _main(["batch", "run", "--checkout", "/r", "--steps", "review",
                  "--with-review"]) == 0
    lines = capsys.readouterr().out.splitlines()
    last = json.loads(lines[-1])
    assert last["kind"] == "run_summary"
    assert last["report_path"]
    assert Path(last["report_path"]).is_absolute()
    assert Path(last["report_path"]).name == "report.md"


