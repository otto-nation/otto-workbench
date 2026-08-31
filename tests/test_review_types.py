"""Tests for the records a review is described with."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"

if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import review_types as rt
from review_types import PRContext, PRMetadata, ReviewJob


def _make_job(head_sha: str, prior_review: str = "") -> ReviewJob:
    pr = PRMetadata(
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
    return ReviewJob(
        repo="owner/repo",
        pr_number="1",
        pr=pr,
        ctx=PRContext(),
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


def _make_meta(total_lines: int) -> PRMetadata:
    return PRMetadata(
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
        """A second owner is what this change removed; it must not come back."""
        assert not hasattr(rt, "MULTI_PHASE_LINE_THRESHOLD")


class TestReplyStateIsAStringOnTheWire:
    """`ReplyState` keys thread dicts and their per-state tally.

    Both are read back with plain strings by callers that never import the
    enum, and `fetch_reply_threads`' answer is serialised as-is.
    """

    def test_members_are_their_own_string_values(self):
        assert rt.ReplyState.RESOLVED == "resolved"
        assert {rt.ReplyState.RESOLVED: 1}["resolved"] == 1

    def test_no_member_collides_with_a_thread_state(self):
        # `pr_comments_state.ThreadState` answers the same question from the
        # author's side. The two vocabularies overlap and must not be swapped
        # for one another, so a shared member has to mean the same thing.
        from pr_comments_state import ThreadState

        shared = {s.value for s in rt.ReplyState} & {s.value for s in ThreadState}
        assert shared == {"contested", "resolved"}
