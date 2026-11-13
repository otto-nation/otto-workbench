"""A run records the checkouts it was started over, absolute; older state loads with none.

Revert: drop `checkouts=checkouts` from the new_run call in cli.pr_batch._start_run.
"""

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
BIN_DIR = REPO_ROOT / "ai" / "bin"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.model  # noqa: E402
import batch.plan  # noqa: E402
import batch.scheduler  # noqa: E402
import cli.pr  # noqa: E402
import core.serde  # noqa: E402
from batch.model import Run, RunStatus, Step  # noqa: E402
from batch.plan import Plan, PlanRow, StepNeed  # noqa: E402
from batch_report_support import make_item, make_run  # noqa: E402
from batch_session_support import stops_at  # noqa: E402


def _main(argv):
    try:
        return cli.pr.main(list(argv), bin_dir=BIN_DIR)
    except SystemExit as exc:
        return exc.code


def test_new_run_keeps_the_checkouts_it_is_given():
    run = batch.scheduler.new_run([], steps=[Step.REBASE], selected=None, pool=1,
                                  checkouts=["/a"])
    assert run.checkouts == ["/a"]


def test_a_run_saved_before_the_field_loads_with_no_checkouts():
    data = core.serde.to_dict(make_run([make_item(1)]))
    data.pop("checkouts")
    assert core.serde.from_dict(Run, data).checkouts == []


def test_run_records_the_absolute_checkout(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    expected = os.path.join(os.getcwd(), "r")
    rows = [PlanRow("o/r", expected, 1, "t", "b1", "h", False,
                    {s: StepNeed(True, "x") for s in batch.model.STEP_ORDER})]
    monkeypatch.setattr(batch.plan, "build_run_plan", lambda dirs, prs=None: Plan("me", rows))
    seen = {}
    settle = stops_at(RunStatus.DONE)

    def fake_run(self):
        seen["checkouts"] = list(self.run.checkouts)
        return settle(self)

    monkeypatch.setattr(batch.scheduler.Scheduler, "run_until_blocked", fake_run)
    assert _main(["batch", "run", "--checkout", "r"]) == 0
    assert seen["checkouts"] == [expected]


def test_checkouts_fall_back_to_the_plans_repo_dirs_when_every_row_is_filtered_out():
    import cli.pr_batch
    assert cli.pr_batch._checkouts([], [], ["/r/../r"]) == ["/r"]
