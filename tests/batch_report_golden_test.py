"""One golden markdown report for a mixed v1 run.

Worktrees are real directories under `tmp_path` (a session names a PR worktree
only when it exists); the rendered text maps that root to a fixed prefix so the
fixture does not depend on the machine. Rewrite it with
`REGEN_GOLDENS=1 pytest -k regenerate`.
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

REPO = "/no/such/repo"
FIXED_ROOT = "/no/such/wt"


def _mixed_markdown(root: Path) -> str:
    wts = []
    for n in (1, 2, 3):
        wt = root / f"o-r-{n}"
        wt.mkdir()
        wts.append(str(wt))
    base = make_item(1, worktree=wts[0], repo_dir=REPO, branch="feat-base",
                     status=ItemStatus.AWAITING_DECISION, is_draft=False)
    child = make_item(2, worktree=wts[1], repo_dir=REPO, branch="feat-child",
                      status=ItemStatus.AWAITING_DECISION, stacked_on=base.key)
    refused = make_item(3, worktree=wts[2], repo_dir=REPO, branch="feat-moved",
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
    return batch.report_md.render_markdown(doc).replace(str(root), FIXED_ROOT)


@pytest.mark.skipif(os.environ.get("REGEN_GOLDENS") != "1",
                    reason="set REGEN_GOLDENS=1 to rewrite fixtures")
def test_regenerate_golden(tmp_path):
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(_mixed_markdown(tmp_path))


def test_mixed_run_markdown(tmp_path):
    assert _mixed_markdown(tmp_path) == FIXTURE.read_text()
