"""A rebase that left generated files stale is held for a person, never accepted."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.model  # noqa: E402
import batch.outcomes  # noqa: E402
import batch.session_report  # noqa: E402
from batch.model import DecisionKind, Finding, FindingReason, Step, StepStatus  # noqa: E402
from batch_report_support import make_item, make_run, no_debt  # noqa: E402

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


def test_stale_session_says_undo_then_regenerate(tmp_path):
    item = make_item(
        1, worktree=str(tmp_path), pre_rebase_head="p0",
        status=batch.model.ItemStatus.NEEDS_SESSION,
        findings=[Finding(reason=FindingReason.STALE_GENERATED, step="rebase",
                          files=["a.pb.go"])])
    entries = batch.session_report.sessions(make_run([item]), closeout=no_debt, tree=None)
    assert entries and entries[0].findings[0].files == ["a.pb.go"]
    why = " ".join(c.why for c in entries[0].commands)
    assert "undo" in why and "regenerate" in why
