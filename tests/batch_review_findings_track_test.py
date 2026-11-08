"""A review step with mixed evidence yields review_findings and a --track command.

Revert: `_step_review` mapping mixed open_findings + checks_unverified to
failed_unknown.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.outcomes  # noqa: E402
import batch.session_report  # noqa: E402
import review.finding_issue  # noqa: E402
from batch.model import FindingReason, Step  # noqa: E402
from batch.steps import WorktreeResult  # noqa: E402
from batch_report_support import no_debt  # noqa: E402
from batch_scheduler_support import (  # noqa: F401,E402
    NEED, NO, Harness, _close_open_sessions, _quiet_outcomes, row,
)


ONLY_REVIEW = {Step.REBASE: NO, Step.COMMENTS: NO, Step.REVIEW: NEED}


def test_review_run_yields_review_findings_and_track_command(tmp_path, monkeypatch):
    # Revert: kinds == {open_findings} exact match in findings._step_review.
    monkeypatch.setattr(
        batch.outcomes, "open_findings",
        lambda item: [{"severity": "must-fix", "title": "x", "declined": False}])
    monkeypatch.setattr(
        batch.outcomes, "fix_checks",
        lambda wt, hb: [{"commit": "c1", "status": "red"}])
    monkeypatch.setattr(
        review.finding_issue, "filable",
        lambda d: [SimpleNamespace(id="M1")])
    wt = tmp_path / "wt"
    wt.mkdir()
    h = Harness(
        [row(1, ONLY_REVIEW)], with_ai=[Step.REVIEW],
        worktrees=lambda d, b: WorktreeResult(str(wt), False, ""))
    h.sched.run_until_blocked()
    item = h.run.items[0]
    assert any(f.reason is FindingReason.REVIEW_FINDINGS for f in item.findings)
    assert item.findings[0].data.get("checks_unverified")
    entries = batch.session_report.sessions(h.run, closeout=no_debt, tree=None)
    filing = next(c for e in entries for c in e.commands if c.files_issue)
    assert "--track" in filing.argv
    assert "M1" in filing.argv
