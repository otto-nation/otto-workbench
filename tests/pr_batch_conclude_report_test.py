"""A run still emits `run_summary` when the report files cannot be written.

Revert: remove the try/except around write_report in `_conclude`.
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.model  # noqa: E402
import batch.plan  # noqa: E402
import batch.scheduler  # noqa: E402
import cli.pr  # noqa: E402
import cli.pr_batch_views  # noqa: E402
from batch.plan import Plan, PlanRow, StepNeed  # noqa: E402

BIN_DIR = REPO_ROOT / "ai" / "bin"


def _main(argv):
    try:
        return cli.pr.main(list(argv), bin_dir=BIN_DIR)
    except SystemExit as exc:
        return exc.code


def test_run_summary_survives_a_report_write_failure(monkeypatch, capsys):
    # Revert: remove the try/except around write_report in _conclude.
    row = PlanRow("o/r", "/r", 1, "t", "b", "h", False,
                  {batch.model.Step.REVIEW: StepNeed(True, "x")})
    monkeypatch.setattr(batch.plan, "build_run_plan", lambda dirs: Plan("me", [row]))

    def fake_run(self):
        self.run.status = batch.model.RunStatus.DONE
        self.run.items[0].status = batch.model.ItemStatus.DONE
        return batch.model.RunStatus.DONE

    def boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(batch.scheduler.Scheduler, "run_until_blocked", fake_run)
    monkeypatch.setattr(cli.pr_batch_views, "write_report", boom)
    assert _main(["batch", "run", "--checkout", "/r", "--steps", "review"]) == 0
    lines = capsys.readouterr().out.splitlines()
    last = json.loads(lines[-1])
    assert last["kind"] == "run_summary"
    assert "report_path" not in last
