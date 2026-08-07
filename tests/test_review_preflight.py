"""Tests for review_preflight._collect_delta edge cases."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import replace
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"

if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import review_preflight as rp


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


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


class TestCollectDeltaMode:
    """The delta surface follows the same rule as the full diff: self mode
    reaches into the working tree, PR mode stops at HEAD."""

    @staticmethod
    def _repo_with_prior_commit(tmp_path: Path) -> tuple[Path, str]:
        repo = tmp_path / "repo"
        repo.mkdir()
        _git(repo, "init", "-b", "main", "-q")
        _git(repo, "config", "user.email", "test@test.com")
        _git(repo, "config", "user.name", "Test")
        _git(repo, "config", "commit.gpgsign", "false")
        (repo / "reviewed.go").write_text("package main\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "--no-verify", "-m", "reviewed")
        prior_sha = _git(repo, "rev-parse", "HEAD")
        (repo / "committed.go").write_text("package main\nfunc committed() {}\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-q", "--no-verify", "-m", "since review")
        (repo / "reviewed.go").write_text("package main\nfunc uncommitted() {}\n")
        (repo / "untracked.go").write_text("package main\nfunc untracked() {}\n")
        return repo, prior_sha

    def _job(self, tmp_path: Path, mode: str) -> rp.ReviewJob:
        repo, prior_sha = self._repo_with_prior_commit(tmp_path)
        job = _make_job(
            head_sha=_git(repo, "rev-parse", "HEAD"),
            prior_review=f"<!-- head_sha: {prior_sha} -->\nprior",
        )
        return replace(job, wt_path=str(repo), mode=mode)

    def test_self_mode_delta_includes_worktree_changes(self, tmp_path, capsys):
        delta_diff, _, delta_files, _ = rp._collect_delta(self._job(tmp_path, "self"))
        capsys.readouterr()
        assert "func committed" in delta_diff
        assert "func uncommitted" in delta_diff
        assert "func untracked" in delta_diff
        assert sorted(delta_files) == ["committed.go", "reviewed.go", "untracked.go"]

    def test_pr_mode_delta_stops_at_head(self, tmp_path, capsys):
        delta_diff, _, delta_files, _ = rp._collect_delta(self._job(tmp_path, "pr"))
        capsys.readouterr()
        assert "func committed" in delta_diff
        assert "func uncommitted" not in delta_diff
        assert delta_files == ["committed.go"]


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
