"""`pr batch note`: validate, append one decision, change nothing else.

Revert: in cmd_note, return 0 without calling batch.run_session.note.
"""

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
BIN_DIR = REPO_ROOT / "ai" / "bin"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.events  # noqa: E402
import batch.scheduler  # noqa: E402
import batch.store  # noqa: E402
import cli.pr  # noqa: E402
import core.run_events  # noqa: E402
from batch.model import Finding, FindingReason, ItemStatus, Step  # noqa: E402
from batch.plan import PlanRow, StepNeed  # noqa: E402
from batch_session_support import seed  # noqa: E402


def _main(argv):
    try:
        return cli.pr.main(list(argv), bin_dir=BIN_DIR)
    except SystemExit as exc:
        return exc.code


def _seeded():
    rows = [PlanRow("o/r", "/r", 1, "t", "b1", "h", False,
                    {Step.REBASE: StepNeed(True, "x")})]
    run = batch.scheduler.new_run(rows, steps=[Step.REBASE], selected=None, pool=1,
                                  checkouts=["/r"])
    run.items[0].findings.append(Finding(FindingReason.CI_FAILING, "ci"))
    run.items[0].status = ItemStatus.NEEDS_SESSION
    return seed(run)


def _note(run_id, *extra, pr="o/r#1", finding="ci_failing:ci", verdict="deferred",
          reason="flaky runner"):
    return _main(["batch", "note", run_id, pr, "--finding", finding, "--verdict", verdict,
                  "--reason", reason, *extra])


def _history(run_id):
    return core.run_events.read(batch.store.run_dir(run_id)).events


def test_note_appends_a_decision_prints_it_and_leaves_state_alone(capsys):
    run = _seeded()
    state = batch.store.run_dir(run.id) / batch.store.STATE_FILE
    before = (state.read_bytes(), state.stat().st_mtime_ns)
    assert _note(run.id, "--command", "pr ci --fix", "--command", "pr ci --post") == 0
    printed = json.loads(capsys.readouterr().out)
    assert _history(run.id) == [printed]
    assert printed["kind"] == "decision"
    assert printed["commands"] == ["pr ci --fix", "pr ci --post"]
    assert (state.read_bytes(), state.stat().st_mtime_ns) == before


def test_a_bare_pr_number_names_the_item(capsys):
    run = _seeded()
    assert _note(run.id, pr="1") == 0
    assert json.loads(capsys.readouterr().out)["item"] == "o/r#1"


def test_the_latest_note_per_finding_wins(capsys):
    run = _seeded()
    assert _note(run.id, verdict="deferred") == 0
    assert _note(run.id, verdict="fixed", reason="re-ran green") == 0
    latest = batch.events.latest_decisions(_history(run.id))
    assert latest[("o/r#1", "ci_failing:ci")]["verdict"] == "fixed"


@pytest.mark.parametrize("override, named", [
    ({"pr": "o/r#9"}, "o/r#9"),
    ({"finding": "review_findings:review"}, "review_findings:review"),
    ({"reason": "   "}, "reason"),
])
def test_a_bad_value_is_exit_2_naming_it_and_appends_nothing(capsys, override, named):
    run = _seeded()
    assert _note(run.id, **override) == 2
    assert named in capsys.readouterr().err
    assert _history(run.id) == []


def test_an_unknown_run_is_exit_2(capsys):
    assert _note("20990101T000000-0000") == 2
    assert "no run 20990101T000000-0000" in capsys.readouterr().err


@pytest.mark.parametrize("override", [{"verdict": "resolved"}, {"finding": "ci_failing"}])
def test_argparse_refuses_a_bad_verdict_or_finding_shape(capsys, override):
    run = _seeded()
    assert _note(run.id, **override) == 2
    assert _history(run.id) == []
