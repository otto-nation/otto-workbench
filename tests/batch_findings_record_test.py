"""findings_for classes and record() status.

Revert: delete the classes branch; make record set the status unconditionally.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.findings  # noqa: E402
from batch.model import DecisionKind, FindingReason, ItemStatus  # noqa: E402
from batch_report_support import make_decision, make_item, make_run  # noqa: E402


def test_aborted_all_generated_is_conflict_generated():
    d = make_decision(DecisionKind.REBASE_CONFLICT, "rebase", {
        "aborted": True, "classes": {"a.go": "accept_theirs", "b.sum": "regenerate"},
        "files": ["a.go", "b.sum"],
    })
    found = batch.findings.findings_for(d)
    assert [f.reason for f in found] == [FindingReason.CONFLICT_GENERATED]


def test_one_ai_merge_is_conflict_handwritten():
    d = make_decision(DecisionKind.REBASE_CONFLICT, "rebase", {
        "aborted": True, "classes": {"a.go": "ai_merge", "b.sum": "regenerate"},
    })
    found = batch.findings.findings_for(d)
    assert [f.reason for f in found] == [FindingReason.CONFLICT_HANDWRITTEN]


def test_ci_failing_only_maps_to_ci_failing():
    d = make_decision(DecisionKind.STEP_REVIEW, "ci", {
        "evidence": [{"kind": "ci_failing", "jobs": ["t", "lint"], "failures": 2}],
    })
    found = batch.findings.findings_for(d)
    assert found[0].reason is FindingReason.CI_FAILING
    assert found[0].data["jobs"] == ["lint", "t"]


def test_ci_failing_mixed_with_checks_is_ci_failing():
    d = make_decision(DecisionKind.STEP_REVIEW, "ci", {
        "evidence": [
            {"kind": "ci_failing", "jobs": ["t"]},
            {"kind": "checks_unverified", "commit": "abc", "status": "red"},
        ],
    })
    found = batch.findings.findings_for(d)
    assert found[0].reason is FindingReason.CI_FAILING
    assert found[0].data["jobs"] == ["t"]
    assert found[0].data["checks_unverified"][0]["commit"] == "abc"


def test_record_sets_needs_session_only_for_block_or_wait():
    events = []
    item = make_item(1, status=ItemStatus.QUEUED)
    run = make_run([item])
    batch.findings.record(
        run, item, "ci", DecisionKind.STEP_REVIEW,
        {"evidence": [{"kind": "ci_failing", "jobs": ["t"]}]},
        emit=lambda kind, **fields: events.append((kind, fields)))
    assert item.status is ItemStatus.QUEUED
    assert item.findings[0].reason is FindingReason.CI_FAILING
    batch.findings.record(
        run, item, "rebase", DecisionKind.REBASE_CONFLICT,
        {"aborted": True, "classes": {"a.go": "ai_merge"}, "files": ["a.go"]},
        emit=lambda kind, **fields: events.append((kind, fields)))
    assert item.status is ItemStatus.NEEDS_SESSION
    assert any(k == "finding_created" for k, _ in events)
