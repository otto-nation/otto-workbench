"""`pr batch` cancel: queue jobs and lock races."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
BIN_DIR = REPO_ROOT / "ai" / "bin"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.scheduler  # noqa: E402
import batch.store  # noqa: E402
import cli.pr  # noqa: E402
import otto_queue.ticket  # noqa: E402
from batch.model import (Decision, DecisionKind, ItemStatus, Job, RunStatus,  # noqa: E402
                         Step, StepStatus)
from batch.plan import PlanRow, StepNeed  # noqa: E402
from batch_session_support import holding, seed  # noqa: E402


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
    run = batch.scheduler.new_run([row], steps=[Step.REVIEW], selected=None, pool=1)
    item = run.items[0]
    item.worktree = "/wt"
    item.step(Step.REVIEW).status = StepStatus.NEEDS_DECISION
    item.status = ItemStatus.AWAITING_DECISION
    run.decisions.append(Decision(id="d1", item=item.key, step="review",
                                  kind=DecisionKind.FAILED))
    run.status = RunStatus.WAITING
    return run


def test_cancel_cancels_each_live_job_and_marks_the_run_cancelled(monkeypatch):
    run = _run()
    run.items[0].jobs = [Job(step=Step.REVIEW, entry_id=8)]
    seed(run)
    calls = []

    def fake_cancel(ledger, entry_id):
        calls.append((ledger, entry_id))

    monkeypatch.setattr(otto_queue.ticket, "cancel_entry", fake_cancel)
    assert _main(["batch", "cancel", run.id]) == 0
    assert [entry_id for _, entry_id in calls] == [8]
    assert batch.store.load(run.id).status is RunStatus.CANCELLED


# ── R2: a cancel queued while an applier holds the run is applied ────────────

def test_cancel_is_refused_while_another_session_holds_the_run(capsys):
    run = seed(_run())
    with holding(run.id, "applier"):
        assert _main(["batch", "cancel", run.id]) == 1
    err = capsys.readouterr().err
    assert "busy" in err
    assert "inbox" not in err
    assert batch.store.load(run.id).status is RunStatus.WAITING
    assert batch.store.pending_requests(run.id) == 0


def test_cancel_fails_when_queue_ledger_is_busy(monkeypatch, capsys):
    import otto_queue.ledger
    run = _run()
    run.items[0].jobs = [Job(step=Step.REVIEW, entry_id=8)]
    seed(run)
    def boom(*_a, **_k):
        raise otto_queue.ledger.LedgerBusy("busy")
    monkeypatch.setattr(otto_queue.ledger.Ledger, "open", boom)
    assert _main(["batch", "cancel", run.id]) == 1
    assert "busy" in capsys.readouterr().err
    assert batch.store.load(run.id).status is RunStatus.WAITING
