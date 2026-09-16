"""Tests for the review domain's single writer and the typed summary report."""

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from conftest import make_ctx
from pr import state as pr_state
from pr.domains import ReviewStatus, ReviewVerdict
from pr.review_sync import sync_review_domain
from review.summary import ReviewSummaryReport, build_review_summary, json_summary


def test_sync_review_domain_is_the_writer(tmp_path):
    ctx = make_ctx(
        repo="acme/widget", branch="feat/login", pr_number=7,
        worktree_root=tmp_path / "wt", head_sha="abc",
        target_dir=tmp_path / "pr" / "feat",
    )
    report = ReviewSummaryReport(
        review_file="/r.md",
        review_type="full",
        head_sha="abc",
        findings={
            "must_fix": 1, "should_fix": 0, "nit": 2, "idiom": 0, "total": 3,
        },
        verdict=ReviewVerdict.CHANGES_REQUESTED.value,
        status=ReviewStatus.COMPLETED.value,
        recoverable=False,
        cost_usd=1.5,
        input_tokens=10,
        output_tokens=20,
        cache_read_tokens=5,
        cache_write_tokens=1,
    )

    written = sync_review_domain(ctx, report)

    assert written is not None
    assert written.finding_counts == {"must_fix": 1, "nit": 2}
    assert written.total_tokens == 36
    loaded = pr_state.load_state(ctx.target_dir)
    assert loaded.review.finding_counts == {"must_fix": 1, "nit": 2}
    assert loaded.review.verdict == ReviewVerdict.CHANGES_REQUESTED.value
    assert loaded.review.cost_usd == pytest.approx(1.5)
    assert loaded.review.recoverable is False
    assert loaded.identity.pr_number == 7
    assert loaded.identity.head_sha == "abc"


def test_sync_review_domain_keeps_prior_type_when_the_report_omits_it(tmp_path):
    ctx = make_ctx(target_dir=tmp_path / "pr" / "feat", worktree_root=tmp_path / "wt")
    sync_review_domain(ctx, ReviewSummaryReport(review_type="incremental"))
    sync_review_domain(ctx, ReviewSummaryReport(review_file="later.md"))

    assert pr_state.load_state(ctx.target_dir).review.review_type == "incremental"


def test_sync_review_domain_defaults_missing_type_to_full(tmp_path):
    ctx = make_ctx(target_dir=tmp_path / "pr" / "feat", worktree_root=tmp_path / "wt")
    sync_review_domain(ctx, ReviewSummaryReport())

    assert pr_state.load_state(ctx.target_dir).review.review_type == "full"


def test_sync_review_domain_records_unknown_recoverable(tmp_path):
    ctx = make_ctx(target_dir=tmp_path / "pr" / "feat", worktree_root=tmp_path / "wt")
    sync_review_domain(ctx, ReviewSummaryReport(review_file="r.md"))

    assert pr_state.load_state(ctx.target_dir).review.recoverable is None


def test_sync_review_domain_lands_with_the_pr_not_the_caller(tmp_path):
    caller = tmp_path / "repo-root"
    caller.mkdir()
    target = tmp_path / "pr" / "widget-feat-login"
    ctx = make_ctx(
        repo="acme/widget", branch="feat/login", pr_number=2973,
        worktree_root=caller, head_sha="pr-sha",
        target_dir=target,
    )

    sync_review_domain(ctx, ReviewSummaryReport(
        review_file="r.md", verdict="approve", head_sha="pr-sha",
    ))

    assert (target / pr_state.STATE_FILE).is_file()
    assert not list(caller.rglob(pr_state.STATE_FILE))
    written = pr_state.load_state(target)
    assert written.identity.pr_number == 2973
    assert written.identity.worktree_root == str(caller)


def test_review_summary_report_to_json_matches_the_legacy_dict(tmp_path):
    """to_json() is the dict json_summary used to build inline.

    Key order and values must stay byte-identical to the previous return so
    REVIEW_SUMMARY consumers and the golden json_summary tests keep passing.
    """
    content = "## Nit\n- **[N1]** path:1 — style\n"
    review = tmp_path / "review.md"
    review.write_text(content)

    report = build_review_summary("org/repo", "42", str(review))
    expected = {
        "repo": "org/repo",
        "pr_number": 42,
        "head_sha": None,
        "head_ref": None,
        "base_ref": None,
        "review_type": None,
        "review_file": str(review),
        "review_content": content,
        "findings": {
            "must_fix": 0, "should_fix": 0, "nit": 1, "idiom": 0, "total": 1,
        },
        "verdict": ReviewVerdict.APPROVE.value,
        "status": ReviewStatus.COMPLETED.value,
        "failure_detail": "",
        "recoverable": False,
        "cost_usd": 0.0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "duration_ms": 0,
    }
    assert report.to_json() == expected
    assert list(report.to_json()) == list(expected)
    assert json.dumps(report.to_json()) == json.dumps(expected)
    assert json_summary("org/repo", "42", str(review)) == (
        "REVIEW_SUMMARY:" + json.dumps(expected)
    )
