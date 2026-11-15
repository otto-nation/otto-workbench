"""The next command a session shows for the three findings `pr.needs_person` owns.

Characterization: these argv and `why` strings were written by the report itself before
they moved to pr.needs_person, so a failure here means the move changed what an operator
is told to run. The original code passes this file unchanged.
"""

import shlex
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.session_report  # noqa: E402
from batch.model import Finding, FindingReason, ItemStatus  # noqa: E402
from batch_report_support import make_item, make_run, no_debt  # noqa: E402
from pr.comments_fix import CLOSEOUT_COMMAND, CloseoutDebt  # noqa: E402


def _commands(reason, wt, data=None):
    item = make_item(1, worktree=wt, repo_dir=wt, status=ItemStatus.NEEDS_SESSION)
    item.findings = [Finding(reason, "x", data=data or {})]
    entries = batch.session_report.sessions(
        make_run([item]), closeout=no_debt, tree=None)
    return [c for e in entries for c in e.commands if not c.files_issue]


def test_ci_failing_argv_and_why(tmp_path):
    wt = str(tmp_path)
    cmds = _commands(FindingReason.CI_FAILING, wt, {"jobs": ["lint", "unit"]})
    assert len(cmds) == 1
    assert cmds[0].argv == ("pr", "ci", "--fix", "--repo-dir", wt)
    assert cmds[0].why == "fix failing CI (lint, unit)"
    assert cmds[0].action == "ci"
    cmds = _commands(FindingReason.CI_FAILING, wt)
    assert cmds[0].why == "fix failing CI"
    assert cmds[0].action == "ci"


def test_threads_need_human_argv_and_why(tmp_path):
    wt = str(tmp_path)
    cmds = _commands(FindingReason.THREADS_NEED_HUMAN, wt)
    assert len(cmds) == 1
    assert cmds[0].argv == ("pr", "comments", "--fix", "--repo-dir", wt)
    assert cmds[0].why == "address review comments"
    assert cmds[0].action == "comments"


def test_closeout_owed_argv_and_why(tmp_path):
    wt = str(tmp_path)
    item = make_item(1, worktree=wt, repo_dir=wt, status=ItemStatus.NEEDS_SESSION)
    debt = CloseoutDebt(summary=True)
    entries = batch.session_report.sessions(
        make_run([item]), closeout=lambda d, b: debt, tree=None)
    cmds = [c for e in entries for c in e.commands if not c.files_issue]
    assert len(cmds) == 1
    assert cmds[0].argv == (*shlex.split(CLOSEOUT_COMMAND), "--repo-dir", wt)
    assert cmds[0].why == "closeout owed per pr state"
    assert cmds[0].action == "closeout"
