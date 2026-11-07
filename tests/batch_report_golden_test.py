"""One golden markdown report for a mixed v1 run.

Worktree paths are non-existent and fixed so the fixture does not depend on
the machine. Rewrite it with `REGEN_GOLDENS=1 pytest -k regenerate`.
"""

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.report  # noqa: E402
import batch.report_md  # noqa: E402
import batch.session_report  # noqa: E402
from batch.model import DecisionKind, ItemStatus, RunStatus  # noqa: E402
from batch.publish import TreeState  # noqa: E402
from batch_report_support import SHA, make_decision, make_item, make_run  # noqa: E402
from pr.comments_fix import CloseoutDebt  # noqa: E402
from rebase.types import RefDivergence  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "batch_report" / "mixed.md"

WT1 = "/no/such/wt/o-r-1"
WT2 = "/no/such/wt/o-r-2"
WT3 = "/no/such/wt/o-r-3"
REPO = "/no/such/repo"


def _mixed_markdown() -> str:
    base = make_item(1, worktree=WT1, repo_dir=REPO, branch="feat-base",
                     status=ItemStatus.AWAITING_DECISION, is_draft=False)
    child = make_item(2, worktree=WT2, repo_dir=REPO, branch="feat-child",
                      status=ItemStatus.AWAITING_DECISION, stacked_on=base.key)
    refused = make_item(3, worktree=WT3, repo_dir=REPO, branch="feat-moved",
                        status=ItemStatus.DONE, is_draft=False)
    run = make_run(
        [child, refused, base],
        [
            make_decision(DecisionKind.REBASE_CONFLICT, "rebase",
                          {"files": ["a.go"]}, id="d1", item=base.key),
            make_decision(DecisionKind.COMMENT_ITEM, "comments",
                          {"id": "T1"}, id="d2", item=child.key),
        ],
        status=RunStatus.INTERRUPTED,
    )

    def closeout(repo_dir, branch):
        return CloseoutDebt(summary=True) if branch == "feat-moved" else CloseoutDebt()

    def tree(item):
        if item.key != refused.key:
            return None
        return TreeState(local=SHA, remote="b" * 40,
                         divergence=RefDivergence(ahead=1, behind=0, comparable=True),
                         checked_out=item.branch)

    sessions = [e.to_dict() for e in batch.session_report.sessions(
        run, closeout=closeout, tree=tree)]
    doc = batch.report.build(run, active=False, sessions=sessions)
    return batch.report_md.render_markdown(doc)


@pytest.mark.skipif(os.environ.get("REGEN_GOLDENS") != "1",
                    reason="set REGEN_GOLDENS=1 to rewrite fixtures")
def test_regenerate_golden():
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(_mixed_markdown())


def test_mixed_run_markdown():
    assert _mixed_markdown() == FIXTURE.read_text()
