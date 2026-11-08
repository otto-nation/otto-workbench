"""A fresh tree/closeout read drops stale recorded publish findings.

Revert: `_apply_plan` keeping READY_TO_PUSH when the plan is ok with no
pushes; `_session` not dropping CLOSEOUT_OWED when debt is not owed.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.session_report  # noqa: E402
from batch.model import Finding, FindingReason  # noqa: E402
from batch.publish import TreeState  # noqa: E402
from batch_report_support import SHA, make_item, make_run, no_debt  # noqa: E402
from rebase.types import RefDivergence  # noqa: E402


def _even(item):
    return TreeState(local=item.remote_sha, remote=item.remote_sha,
                     divergence=RefDivergence(ahead=0, behind=0, comparable=True),
                     checked_out=item.branch)


def _moved(item):
    return TreeState(local=item.remote_sha, remote="b" * 40,
                     divergence=RefDivergence(ahead=0, behind=1, comparable=True),
                     checked_out=item.branch)


def _reasons(entries):
    return [f.reason for e in entries for f in e.findings]


def test_fresh_even_tree_drops_ready_to_push_and_unowed_closeout(tmp_path):
    # Revert: skip _drop of READY_TO_PUSH when plan.ok and not plan.pushes.
    item = make_item(1, worktree=str(tmp_path), repo_dir=str(tmp_path), branch="feat")
    item.findings = [
        Finding(FindingReason.READY_TO_PUSH, "publish"),
        Finding(FindingReason.CLOSEOUT_OWED, "comments"),
        Finding(FindingReason.CI_FAILING, "ci"),
    ]
    run = make_run([item])
    entries = batch.session_report.sessions(
        run, closeout=no_debt, tree=_even)
    assert FindingReason.READY_TO_PUSH not in _reasons(entries)
    assert FindingReason.CLOSEOUT_OWED not in _reasons(entries)
    assert FindingReason.CI_FAILING in _reasons(entries)


def test_fresh_moved_remote_drops_ready_to_push_for_push_refused(tmp_path):
    # Revert: keep READY_TO_PUSH when the fresh plan refuses.
    item = make_item(1, worktree=str(tmp_path), repo_dir=str(tmp_path), branch="feat",
                     remote_sha=SHA)
    item.findings = [Finding(FindingReason.READY_TO_PUSH, "publish")]
    run = make_run([item])
    entries = batch.session_report.sessions(
        run, closeout=no_debt, tree=_moved)
    assert FindingReason.READY_TO_PUSH not in _reasons(entries)
    assert FindingReason.PUSH_REFUSED in _reasons(entries)
