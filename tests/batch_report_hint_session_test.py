"""A finished run with needs_session items does not say nothing is left.

Revert: `_hint` returning "nothing is left to answer" whenever GitHub does
not owe work.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.report  # noqa: E402
from batch.model import (STEP_ORDER, Finding, FindingReason, Item,  # noqa: E402
                         ItemStatus, Run, RunStatus, StepRecord)


def _item(n=1, status=ItemStatus.NEEDS_SESSION, **kw):
    return Item(key=f"o/r#{n}", repo="o/r", repo_dir="/r", pr=n, branch=f"b{n}",
                head_sha="s", worktree=f"/wt/b{n}", status=status,
                steps=[StepRecord(s) for s in STEP_ORDER], **kw)


def _run(items, status=RunStatus.DONE):
    return Run(id="r1", started_at="t", steps=list(STEP_ORDER), pool=1,
               auto_publish=[], status=status, items=list(items))


def test_needs_session_items_are_named_in_the_hint():
    # Revert: drop the needs_session count in _hint.
    hint = batch.report.build(_run([_item(1), _item(2)]), active=False)["run"]["hint"]
    assert "2 PRs need a session" in hint
    assert "pr batch report" in hint
    assert "nothing is left" not in hint


def test_act_findings_on_a_done_item_are_named_in_the_hint():
    # Revert: drop the findings check in _hint.
    item = _item(1, status=ItemStatus.DONE)
    item.findings = [Finding(FindingReason.READY_TO_PUSH, "publish")]
    hint = batch.report.build(_run([item]), active=False)["run"]["hint"]
    assert "findings remain" in hint
    assert "pr batch report" in hint
