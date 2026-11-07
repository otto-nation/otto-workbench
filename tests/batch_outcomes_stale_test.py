"""A rebase that left generated files stale is held for a person, never accepted."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.model  # noqa: E402
import batch.outcomes  # noqa: E402
import batch.report  # noqa: E402
from batch.model import Decision, DecisionKind, Step, StepStatus  # noqa: E402

ITEM = batch.model.Item(key="o/r#1", repo="o/r", repo_dir="/r", pr=1, branch="b", head_sha="s",
                        worktree="/wt")


def test_rebase_with_files_stale_opens_step_review():
    r = batch.outcomes.classify(
        Step.REBASE, 0, '{"files_stale":["a.pb.go"],"pre_rebase_head":"p0"}',
        item=ITEM, log_tail=[])
    assert r.status is StepStatus.NEEDS_DECISION
    assert len(r.decisions) == 1
    draft = r.decisions[0]
    assert draft.kind is DecisionKind.STEP_REVIEW
    assert draft.payload["evidence"] == [{"kind": "stale", "files": ["a.pb.go"]}]
    assert r.pre_rebase_head == "p0"


# passes-at-base: asserts an empty stale list still completes the rebase
def test_rebase_without_stale_is_done():
    r = batch.outcomes.classify(
        Step.REBASE, 0, '{"files_stale":[]}', item=ITEM, log_tail=[])
    assert r.status is StepStatus.DONE


def test_prep_for_stale_says_regenerate_or_undo_never_accept():
    text = batch.report.prep(
        Decision(id="d", item="o/r#1", step="rebase", kind=DecisionKind.STEP_REVIEW,
                 payload={"evidence": [{"kind": "stale", "files": ["a.pb.go"]}]}),
        ITEM)
    assert "a.pb.go" in text
    assert "`undo`" in text
    assert "never" in text
