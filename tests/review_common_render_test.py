"""Tests for review_common.render_status."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "claude" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pr_state
from review_common import render_status


def test_render_status_not_run():
    rev = pr_state.ReviewSummary()
    assert render_status(rev) == ["**Review**: not run yet"]


def test_render_status_error():
    rev = pr_state.ReviewSummary(
        review_type="full", verdict=pr_state.ReviewVerdict.APPROVE.value,
        status=pr_state.ReviewStatus.ERROR.value, updated_at="t",
    )
    lines = render_status(rev)
    assert "[ERROR]" in lines[0]


def test_render_status_completed():
    rev = pr_state.ReviewSummary(
        review_type="full", verdict=pr_state.ReviewVerdict.APPROVE.value,
        status=pr_state.ReviewStatus.COMPLETED.value, updated_at="t",
    )
    lines = render_status(rev)
    assert "[ERROR]" not in lines[0]


def test_render_status_empty_status():
    rev = pr_state.ReviewSummary(
        review_type="full", verdict=pr_state.ReviewVerdict.APPROVE.value, updated_at="t",
    )
    lines = render_status(rev)
    assert "[ERROR]" not in lines[0]


def test_render_status_disapprove():
    rev = pr_state.ReviewSummary(
        review_type="full", verdict=pr_state.ReviewVerdict.DISAPPROVE.value,
        updated_at="t",
    )
    lines = render_status(rev)
    assert "[DISAPPROVED]" in lines[0]


def test_render_status_disapprove_and_error():
    rev = pr_state.ReviewSummary(
        review_type="full", verdict=pr_state.ReviewVerdict.DISAPPROVE.value,
        status=pr_state.ReviewStatus.ERROR.value, updated_at="t",
    )
    lines = render_status(rev)
    assert "[ERROR]" in lines[0]
    assert "[DISAPPROVED]" in lines[0]


def test_render_status_with_findings():
    rev = pr_state.ReviewSummary(
        review_type="pr", finding_counts={"M": 2, "S": 1},
        verdict=pr_state.ReviewVerdict.CHANGES_REQUESTED.value, updated_at="t",
    )
    lines = render_status(rev)
    assert any("findings:" in l for l in lines)
    assert any("M: 2" in l for l in lines)


def test_render_status_with_cost():
    rev = pr_state.ReviewSummary(
        review_type="pr", cost_usd=1.23, updated_at="t",
    )
    lines = render_status(rev)
    assert any("$1.23" in l for l in lines)


def test_render_status_partial_with_failure_detail():
    rev = pr_state.ReviewSummary(
        review_type="pr", verdict=pr_state.ReviewVerdict.CHANGES_REQUESTED.value,
        status=pr_state.ReviewStatus.PARTIAL.value,
        failure_detail="2/8 groups failed: quota exhausted (429), agent hit max turns (5)",
        finding_counts={"M": 3, "S": 2}, cost_usd=4.50, updated_at="t",
    )
    lines = render_status(rev)
    assert any("PARTIAL" in line for line in lines)
    assert any("2/8 groups failed" in line for line in lines)
    assert any("recover" in line for line in lines)


def test_render_status_error_with_failure_detail():
    rev = pr_state.ReviewSummary(
        review_type="pr", status=pr_state.ReviewStatus.ERROR.value,
        failure_detail="all groups failed: quota exhausted (429)",
        cost_usd=2.10, updated_at="t",
    )
    lines = render_status(rev)
    assert any("ERROR" in line for line in lines)
    assert any("all groups failed" in line for line in lines)
    assert any("recover" in line for line in lines)


def test_render_status_complete_no_recover_hint():
    rev = pr_state.ReviewSummary(
        review_type="pr", verdict=pr_state.ReviewVerdict.APPROVE.value,
        status=pr_state.ReviewStatus.COMPLETED.value,
        finding_counts={}, cost_usd=3.00, updated_at="t",
    )
    lines = render_status(rev)
    assert not any("recover" in line for line in lines)
    assert not any("PARTIAL" in line or "ERROR" in line for line in lines)
