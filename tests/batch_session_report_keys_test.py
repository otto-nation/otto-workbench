"""Every finding a session shows is one `pr batch note` accepts.

Revert: drop the DERIVED_STEPS union from finding_keys.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.session_report  # noqa: E402
from batch.model import DecisionKind, Finding, FindingReason  # noqa: E402
from batch.publish import TreeState  # noqa: E402
from batch_report_support import SHA, make_decision, make_item, make_run  # noqa: E402
from pr.comments_fix import CloseoutDebt  # noqa: E402
from rebase.types import RefDivergence  # noqa: E402


def test_keys_cover_stored_and_v1_findings():
    item = make_item(1, findings=[Finding(FindingReason.CI_FAILING, "ci")])
    run = make_run([item], decisions=[make_decision(DecisionKind.COMMENT_ITEM, "comments")])
    keys = batch.session_report.finding_keys(run, item)
    assert {"ci_failing:ci", "threads_need_human:comments"} <= keys


def test_every_finding_a_session_shows_is_a_key():
    item = make_item(1)
    run = make_run([item])
    moved = TreeState(local=SHA, remote="b" * 40,
                      divergence=RefDivergence(ahead=1, behind=0, comparable=True),
                      checked_out=item.branch)
    entries = batch.session_report.sessions(
        run, closeout=lambda d, b: CloseoutDebt(summary=True), tree=lambda i: moved)
    shown = {batch.session_report.finding_key(f) for f in entries[0].findings}
    assert {"closeout_owed:comments", "push_refused:publish"} <= shown
    assert shown <= batch.session_report.finding_keys(run, item)
