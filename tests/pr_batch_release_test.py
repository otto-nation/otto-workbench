"""`pr batch release`: CLI JSON, PR resolution, and apply-through-inbox."""

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
BIN_DIR = REPO_ROOT / "ai" / "bin"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.model  # noqa: E402
import batch.scheduler  # noqa: E402
import batch.store  # noqa: E402
import cli.pr  # noqa: E402
from batch.model import ItemStatus, RunStatus, Step, StepStatus  # noqa: E402
from batch.plan import PlanRow, StepNeed  # noqa: E402
from batch_session_support import open_session, seed  # noqa: E402


def _main(argv):
    try:
        return cli.pr.main(list(argv), bin_dir=BIN_DIR)
    except SystemExit as exc:
        return exc.code


def _waiting(*, extra=(), drafted=True):
    rows = [PlanRow("o/r", "/r", 1, "t", "b", "h", False,
                    {Step.REVIEW: StepNeed(True, "x")}), *extra]
    run = batch.scheduler.new_run(rows, steps=[Step.REVIEW], selected=None,
                                  pool=1, auto_publish=[])
    item = run.items[0]
    item.worktree = "/wt"
    rec = item.step(Step.REVIEW)
    rec.status = StepStatus.NEEDS_DECISION
    rec.drafted = drafted
    rec.commits = ["abc"] if drafted else []
    run.decisions.append(batch.model.Decision(
        id="d1", item=item.key, step="review",
        kind=batch.model.DecisionKind.OPEN_FINDINGS))
    item.status = ItemStatus.AWAITING_DECISION
    run.status = RunStatus.WAITING
    return seed(run)


def test_release_applies_on_a_waiting_run_and_prints_json(capsys):
    run = _waiting()
    assert _main(["batch", "release", run.id, "o/r#1"]) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload == {
        "released": True, "run": run.id, "item": "o/r#1", "queued": False,
        "worktree": "/wt",
        "drafted": [{"step": "review", "commits": ["abc"]}],
        "closeout": "",
    }
    saved = batch.store.load(run.id)
    assert saved.item("o/r#1").status is ItemStatus.DROPPED
    assert saved.decision("d1").resolution == "released"


def test_release_bare_number_matches_the_only_pr(capsys):
    run = _waiting()
    assert _main(["batch", "release", run.id, "1"]) == 0
    assert json.loads(capsys.readouterr().out)["item"] == "o/r#1"


def test_release_bare_number_refuses_when_ambiguous(capsys):
    extra = PlanRow("other/r", "/r", 1, "t", "c", "h", False,
                    {Step.REVIEW: StepNeed(True, "x")})
    run = _waiting(extra=[extra])
    assert _main(["batch", "release", run.id, "1"]) == 1
    err = capsys.readouterr().err
    assert "more than one item" in err
    assert "o/r#1" in err and "other/r#1" in err


def test_release_unknown_pr_exits_1(capsys):
    run = _waiting()
    assert _main(["batch", "release", run.id, "o/r#99"]) == 1
    assert "no item o/r#99" in capsys.readouterr().err


def test_release_unknown_run_exits_1(capsys):
    missing = "20990101T000000-dead"
    assert _main(["batch", "release", missing, "o/r#1"]) == 1
    assert missing in capsys.readouterr().err


def test_release_of_a_terminal_item_exits_1_with_json(capsys):
    extra = PlanRow("o/r", "/r", 2, "t", "c", "h", False,
                    {Step.REVIEW: StepNeed(True, "x")})
    run = _waiting(extra=[extra])
    with open_session(run.id) as session:
        session.run.item("o/r#1").status = ItemStatus.DROPPED
        session.commit()
    assert _main(["batch", "release", run.id, "o/r#1"]) == 1
    captured = capsys.readouterr()
    assert "already dropped" in captured.err
    payload = json.loads(captured.out)
    assert payload["released"] is False
    assert payload["run"] == run.id
