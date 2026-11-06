"""v1 resolve actions for an aborted (tree: clean) rebase_conflict."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.model  # noqa: E402
import batch.requests  # noqa: E402
import batch.resolve  # noqa: E402


def _run(*decisions):
    item = batch.model.Item(
        key="o/r#1", repo="o/r", repo_dir="/r", pr=1, branch="b", head_sha="s",
        worktree="/wt", status=batch.model.ItemStatus.AWAITING_DECISION,
        steps=[batch.model.StepRecord(s) for s in batch.model.STEP_ORDER],
    )
    return batch.model.Run(
        id="r1", started_at="t", steps=list(batch.model.STEP_ORDER), pool=1,
        auto_publish=[], items=[item], decisions=list(decisions),
    )


def _d(kind, step, payload=None, id="d1"):
    return batch.model.Decision(
        id=id, item="o/r#1", step=step, kind=kind, payload=payload or {},
    )


class Recorder:
    def __init__(self, code=0):
        self.calls, self.code = [], code

    def __call__(self, argv, log_path=None):
        self.calls.append(argv)
        return self.code


def test_retry_on_clean_tree_repends_rebase_without_continue():
    run = _run(_d(batch.model.DecisionKind.REBASE_CONFLICT, "rebase", {"tree": "clean"}))
    run.items[0].step(batch.model.Step.REBASE).status = batch.model.StepStatus.NEEDS_DECISION
    rec = Recorder()
    batch.resolve.apply(run, batch.requests.Decide("d1", "retry"), pr_bin="pr", runner=rec)
    assert rec.calls == []
    assert run.items[0].step(batch.model.Step.REBASE).status is batch.model.StepStatus.PENDING


def test_abort_on_clean_tree_runs_no_command_and_skips():
    run = _run(_d(batch.model.DecisionKind.REBASE_CONFLICT, "rebase", {"tree": "clean"}))
    run.items[0].step(batch.model.Step.REBASE).status = batch.model.StepStatus.NEEDS_DECISION
    rec = Recorder()
    batch.resolve.apply(run, batch.requests.Decide("d1", "abort"), pr_bin="pr", runner=rec)
    assert rec.calls == []
    assert run.items[0].step(batch.model.Step.REBASE).status is batch.model.StepStatus.SKIPPED
