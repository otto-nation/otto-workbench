"""Tests for review_preflight's pinned metadata fetching."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"

if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import review_preflight as rp
from review_types import ReviewJob


def _make_job(head_sha: str, prior_review: str = "") -> ReviewJob:
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
    return ReviewJob(
        repo="owner/repo",
        pr_number="1",
        pr=pr,
        ctx=ctx,
        wt_path="/tmp/fake",
        review_file="/tmp/review.md",
        session_log="/tmp/session.log",
        prior_review=prior_review,
    )


class TestArtifactDir:
    """Where a review's files live is derived from the review file, not passed in."""

    def test_is_the_review_file_directory(self):
        job = _make_job(head_sha="abc123", prior_review="")
        job.review_file = "/tmp/reviews/owner-repo-42/review.md"
        assert job.artifact_dir == "/tmp/reviews/owner-repo-42"

    def test_is_not_the_reviews_root(self):
        job = _make_job(head_sha="abc123", prior_review="")
        job.review_file = "/tmp/reviews/owner-repo-42/review.md"
        assert job.artifact_dir != "/tmp/reviews"


PR_PAYLOAD = {
    "title": "feat: something",
    "body": "",
    "headRefName": "feature",
    "baseRefName": "main",
    "headRefOid": "newsha0",
    "additions": 90,
    "deletions": 9,
    "changedFiles": 3,
    "files": [
        {"path": "new.py", "additions": 90, "deletions": 9},
    ],
    "isDraft": False,
    "labels": [],
    "author": {"login": "someone"},
}

PINNED_NUMSTAT = "10\t2\told.py\n5\t0\tshared.py\n"


def _stub_run(monkeypatch, calls: list):
    def fake_pr_view(pr, *fields, repo="", cwd=None):
        calls.append((["gh", "pr", "view", pr, "--repo", repo], cwd))
        return dict(PR_PAYLOAD)

    def fake_git_out(*args, cwd=None, default="", config=None):
        calls.append((["git", *args], cwd))
        if args == ("diff", "--numstat", "origin/main...HEAD"):
            return PINNED_NUMSTAT
        raise AssertionError(f"unexpected git command: {args}")

    monkeypatch.setattr(rp.gh_client, "pr_view", fake_pr_view)
    monkeypatch.setattr(rp.git_client, "out", fake_git_out)


class TestFetchPRMetadataPinned:
    def test_pin_sha_rebuilds_changeset_from_worktree(self, monkeypatch):
        calls: list = []
        _stub_run(monkeypatch, calls)

        pr = rp.fetch_pr_metadata(
            "owner/repo", "1", pin_sha="oldsha0", wt_path="/tmp/pinned",
        )

        assert pr.head_sha == "oldsha0"
        assert pr.files == [
            {"path": "old.py", "additions": 10, "deletions": 2},
            {"path": "shared.py", "additions": 5, "deletions": 0},
        ]
        assert (pr.additions, pr.deletions, pr.changed_files) == (15, 2, 2)
        assert pr.title == "feat: something"

        git_cmd, cwd = calls[-1]
        assert git_cmd == ["git", "diff", "--numstat", "origin/main...HEAD"]
        assert cwd == "/tmp/pinned"

    def test_pin_sha_matching_head_uses_gh_payload(self, monkeypatch):
        calls: list = []
        _stub_run(monkeypatch, calls)

        pr = rp.fetch_pr_metadata(
            "owner/repo", "1", pin_sha="newsha0", wt_path="/tmp/pinned",
        )

        assert pr.head_sha == "newsha0"
        assert [f["path"] for f in pr.files] == ["new.py"]
        assert pr.changed_files == 3
        assert len(calls) == 1

    def test_no_pin_sha_uses_gh_payload(self, monkeypatch):
        calls: list = []
        _stub_run(monkeypatch, calls)

        pr = rp.fetch_pr_metadata("owner/repo", "1")

        assert pr.head_sha == "newsha0"
        assert pr.changed_files == 3
        assert len(calls) == 1


def _make_meta(total_lines: int) -> rp.PRMetadata:
    return rp.PRMetadata(
        title="test",
        body="",
        head="feature",
        base="main",
        head_sha="abc1234",
        additions=total_lines,
        deletions=0,
        changed_files=1,
        files=[{"path": "a.py", "additions": total_lines, "deletions": 0}],
    )


class TestFileStatsThreshold:
    """The effort preset owns the threshold; file_stats must not re-derive it."""

    def test_low_effort_uses_wider_threshold(self):
        from agent_types import EFFORT_PRESETS, Effort

        low = EFFORT_PRESETS[Effort.LOW].multi_phase_line_threshold
        assert _make_meta(750).file_stats(low) == ""

    def test_medium_effort_uses_narrower_threshold(self):
        from agent_types import EFFORT_PRESETS, Effort

        medium = EFFORT_PRESETS[Effort.MEDIUM].multi_phase_line_threshold
        assert "a.py" in _make_meta(750).file_stats(medium)

    def test_the_module_constant_is_gone(self):
        """A second owner is what this change removes; it must not come back."""
        assert not hasattr(rp, "MULTI_PHASE_LINE_THRESHOLD")
