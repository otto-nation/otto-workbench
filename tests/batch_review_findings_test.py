"""review_findings mapping and the on-request filing command.

Revert: constant severity; emit --track-all; drop the worktree guard.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.findings  # noqa: E402
import batch.session_report  # noqa: E402
import review.finding_issue  # noqa: E402
from batch.model import DecisionKind, Finding, FindingReason, ItemStatus, Severity  # noqa: E402
from batch_report_support import make_decision, make_item, make_run, no_debt  # noqa: E402
from review.types import SEVERITY_MUST, severity_by_key  # noqa: E402

MUST = severity_by_key(SEVERITY_MUST).label


def _open(findings):
    return make_decision(DecisionKind.STEP_REVIEW, "review", {
        "evidence": [{"kind": "open_findings", "findings": findings}],
    })


def test_open_must_fix_is_review_findings_block():
    found = batch.findings.findings_for(_open([
        {"severity": MUST, "title": "x", "declined": False},
    ]))
    assert found[0].reason is FindingReason.REVIEW_FINDINGS
    assert found[0].data["severity"] == Severity.BLOCK.value
    assert found[0].data["counts"][MUST] == 1


def test_declined_must_fix_or_should_fix_only_is_act():
    declined = batch.findings.findings_for(_open([
        {"severity": MUST, "title": "x", "declined": True},
    ]))
    assert declined[0].data["severity"] == Severity.ACT.value
    should = batch.findings.findings_for(_open([
        {"severity": "should-fix", "title": "y", "declined": False},
    ]))
    assert should[0].reason is FindingReason.REVIEW_FINDINGS
    assert should[0].data["severity"] == Severity.ACT.value


def test_mixed_open_findings_and_checks_is_review_findings():
    d = make_decision(DecisionKind.STEP_REVIEW, "review", {
        "evidence": [
            {"kind": "open_findings", "findings": [{"severity": MUST}]},
            {"kind": "checks_unverified", "commit": "abc", "status": "red"},
        ],
    })
    found = batch.findings.findings_for(d)
    assert found[0].reason is FindingReason.REVIEW_FINDINGS
    assert found[0].data["checks_unverified"][0]["commit"] == "abc"


def test_filing_command_tracks_ids_on_a_live_worktree(tmp_path, monkeypatch):
    monkeypatch.setattr(review.finding_issue, "filable",
                        lambda d: [SimpleNamespace(id="M1"), SimpleNamespace(id="S2")])
    wt = tmp_path / "wt"
    wt.mkdir()
    item = make_item(1, worktree=str(wt), repo_dir=str(tmp_path),
                     status=ItemStatus.DONE)
    item.findings = [Finding(FindingReason.REVIEW_FINDINGS, "review",
                             data={"severity": Severity.BLOCK.value})]
    run = make_run([item])
    entries = batch.session_report.sessions(run, closeout=no_debt, tree=None)
    assert len(entries) == 1
    cmds = entries[0].commands
    filing = next(c for c in cmds if c.files_issue)
    assert cmds[0] is not filing
    assert filing.argv == ("pr", "review", "--self", "--track", "M1", "--track", "S2",
                           "--post", "--repo-dir", str(wt))
    assert "--track-all" not in filing.argv


def test_filing_command_absent_when_worktree_gone_or_filable_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(review.finding_issue, "filable",
                        lambda d: [SimpleNamespace(id="M1")])
    item = make_item(1, worktree="/missing/wt", repo_dir=str(tmp_path),
                     status=ItemStatus.DONE)
    item.findings = [Finding(FindingReason.REVIEW_FINDINGS, "review",
                             data={"severity": Severity.ACT.value})]
    run = make_run([item])
    entries = batch.session_report.sessions(run, closeout=no_debt, tree=None)
    assert all(not c.files_issue or "review" not in c.argv for e in entries for c in e.commands)

    monkeypatch.setattr(review.finding_issue, "filable", lambda d: [])
    wt = tmp_path / "wt"
    wt.mkdir()
    item.worktree = str(wt)
    entries = batch.session_report.sessions(run, closeout=no_debt, tree=None)
    assert not any(c.files_issue and "--track" in c.argv for e in entries for c in e.commands)
