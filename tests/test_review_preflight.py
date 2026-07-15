"""Tests for review_preflight._collect_delta edge cases."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "claude" / "lib"

if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import review_preflight as rp


def _make_job(head_sha: str, prior_review: str = "") -> rp.ReviewJob:
    pr = rp.PRMetadata(
        title="test",
        body="",
        head="feature",
        base="main",
        head_sha=head_sha,
        additions=10,
        deletions=5,
        changed_files=1,
        files=[],
    )
    ctx = rp.PRContext()
    return rp.ReviewJob(
        repo="owner/repo",
        pr_number="1",
        pr=pr,
        ctx=ctx,
        wt_path="/tmp/fake",
        review_file="/tmp/review.md",
        session_log="/tmp/session.log",
        reviews_dir="/tmp/reviews",
        prior_review=prior_review,
    )


class TestCollectDeltaSameSha:
    def test_prior_sha_equals_head_sha_returns_empty(self):
        sha = "abc1234def5678901234567890abcdef12345678"
        prior_review = f"<!-- head_sha: {sha} -->\nsome review content"
        job = _make_job(head_sha=sha, prior_review=prior_review)
        result = rp._collect_delta(job)
        assert result == ("", "", [], "")

    def test_no_prior_review_returns_empty(self):
        job = _make_job(head_sha="abc123", prior_review="")
        result = rp._collect_delta(job)
        assert result == ("", "", [], "")

    def test_prior_review_without_sha_returns_empty(self):
        job = _make_job(head_sha="abc123", prior_review="no sha marker here")
        result = rp._collect_delta(job)
        assert result == ("", "", [], "")
