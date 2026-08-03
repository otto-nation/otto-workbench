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
