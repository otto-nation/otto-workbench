"""pr.permalinks: tree-scoped line anchors and evidence links."""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

# `_no_published_summary` is autouse: imported so pytest applies it here,
# never referenced by name.
from review_threads_support import _no_published_summary, content  # noqa: E402
from conftest import git_out
import git.client
import pr.thread_replies
import pr.attribution
import pr.permalinks
import pr.summary_render
from pr.thread_models import CommentItem, ReportThread


class TestLineAnchorsAreTreeScoped:
    """A line read in one tree is not a location in another.

    Every reply pinned its permalink to the tree the fix commit produced while
    numbering it from a line read before that commit, so a reviewer following
    the link landed on whatever code inherited the number rather than on the
    code they had commented on.
    """

    @staticmethod
    def _git(wt, *args):
        git_out(wt, *args)

    def _sha(self, wt, rev):
        return git_out(wt, "rev-parse", rev).strip()

    @pytest.fixture
    def trees(self, worktree):
        """Two commits: the second rewrites `moved.py` and leaves `still.py`."""
        hooks = worktree / ".git" / "empty-hooks"
        hooks.mkdir()
        self._git(worktree, "config", "user.email", "test@example.com")
        self._git(worktree, "config", "user.name", "Test")
        self._git(worktree, "config", "commit.gpgsign", "false")
        self._git(worktree, "config", "core.hooksPath", str(hooks))
        (worktree / "moved.py").write_text("guard\ncheck\n")
        (worktree / "still.py").write_text("one\ntwo\n")
        self._git(worktree, "add", "-A")
        self._git(worktree, "commit", "-qm", "reviewed")
        read = self._sha(worktree, "HEAD")
        (worktree / "moved.py").write_text("import os\nguard\ncheck\n")
        self._git(worktree, "commit", "-qam", "fix pass")
        return SimpleNamespace(path=worktree, read=read,
                               fixed=self._sha(worktree, "HEAD"))

    def test_a_line_in_an_untouched_file_keeps_its_anchor(self, trees):
        entry = CommentItem(id="t1", file="still.py", line=2, read_sha=trees.read)
        assert pr.permalinks.anchored_line(
            entry, "still.py", 2, trees.fixed, trees.path) == 2

    def test_a_line_in_a_rewritten_file_loses_its_anchor(self, trees):
        entry = CommentItem(id="t1", file="moved.py", line=1, read_sha=trees.read)
        assert pr.permalinks.anchored_line(
            entry, "moved.py", 1, trees.fixed, trees.path) == 0

    def test_the_same_tree_needs_no_comparison(self, trees):
        """The triage replies go out before the fix commit, so this is the common case."""
        entry = CommentItem(id="t1", file="moved.py", line=1, read_sha=trees.read)
        assert pr.permalinks.anchored_line(
            entry, "moved.py", 1, trees.read, trees.path) == 1

    def test_an_unrecorded_tree_loses_the_anchor(self, trees):
        entry = CommentItem(id="t1", file="still.py", line=2)
        assert pr.permalinks.anchored_line(
            entry, "still.py", 2, trees.fixed, trees.path) == 0

    def test_a_reply_drafted_after_the_fix_commit_links_the_file(self, trees):
        """End to end: the shape that sent reviewers to unrelated code."""
        entry = CommentItem(id="t1", file="moved.py", line=1, read_sha=trees.read)
        link = pr.permalinks.code_link(entry, "owner/repo", trees.fixed, trees.path)
        assert link.endswith(f"/blob/{trees.fixed}/moved.py)")
        assert "#L" not in link


class TestEvidencePermalinks:
    """Every claim links to the code at a pinned SHA."""

    def test_permalink_pins_the_sha(self):
        assert pr.permalinks.blob_permalink("owner/repo", "abc123", "a/b.py", 7) == (
            "https://github.com/owner/repo/blob/abc123/a/b.py#L7")

    def test_uncited_entry_renders_no_link(self):
        entry = CommentItem(id="t1", summary="s")
        assert pr.permalinks.evidence_link(entry, "owner/repo", "abc123") == ""

    def test_dismissal_carries_the_cited_line(self, tmp_path):
        dismissed = [CommentItem(
            id="t1", summary="s", reasoning="the guard already returns early",
            evidence_file="app.py", evidence_line=12, read_sha="cafe123",
        )]
        threads = {"t1": ReportThread(id="t1", comments=[{"databaseId": 111}])}
        with (
            patch.object(git.client, "head_sha", return_value="cafe123"),
            patch("pr.comments.post_thread_reply", return_value=True) as reply,
        ):
            pr.thread_replies.post_dismissed_replies(dismissed, threads, "owner/repo", 42, tmp_path)
        body = reply.call_args[0][3]
        assert "blob/cafe123/app.py#L12" in body
        assert "the guard already returns early" in body

    def test_already_addressed_links_the_line_at_head(self, tmp_path):
        # `read_sha` here names a tree that was never built — `tmp_path` is not a
        # repo — so the staleness check cannot resolve the coordinate and
        # declines the citation, which is the honest answer for a tree git
        # cannot read. This test's subject is the link, and the addressing
        # commit is already a stub, so the check is stubbed to match rather than
        # the assertion weakened to whatever the unresolvable tree produces.
        addressed = [CommentItem(
            id="t1", summary="use the helper", file="app.py",
            evidence_file="app.py", evidence_line=4, read_sha="cafe123",
        )]
        threads = {"t1": ReportThread(id="t1", comments=[{"databaseId": 111}])}
        with (
            patch.object(git.client, "head_sha", return_value="cafe123"),
            patch.object(pr.attribution, "find_addressing_commit", return_value="dead" * 10),
            patch.object(pr.attribution.AddressingHistory, "_coordinate_went_stale",
                         return_value=False),
            patch("pr.comments.post_thread_reply", return_value=True) as reply,
        ):
            pr.thread_replies.post_already_addressed_replies(
                addressed, threads, "owner/repo", 42, tmp_path)
        body = reply.call_args[0][3]
        assert "blob/cafe123/app.py#L4" in body
        assert "/commit/deaddeaddead" in body

    def test_summary_file_cell_links_at_the_fix_commit(self, content):
        cp = pr.attribution.CommitPushResult("abc1234", "pushed", "")
        body = pr.summary_render.build_summary_body(
            content(fixed=[CommentItem(id="t1", summary="fix", file="a.py", line=9,
                                       read_sha="abc1234")]),
            cp, "owner/repo", 1, {},
        )
        assert "https://github.com/owner/repo/blob/abc1234/a.py#L9" in body

    def test_summary_file_cell_drops_a_line_read_in_another_tree(self, content):
        """The fix commit moved the line, so the cell links the file alone."""
        cp = pr.attribution.CommitPushResult("abc1234", "pushed", "")
        body = pr.summary_render.build_summary_body(
            content(fixed=[CommentItem(id="t1", summary="fix", file="a.py", line=9,
                                       read_sha="0ldc0de")]),
            cp, "owner/repo", 1, {},
        )
        assert "https://github.com/owner/repo/blob/abc1234/a.py)" in body
        assert "#L9" not in body

    def test_summary_file_cell_stays_plain_without_a_sha(self, content):
        cp = pr.attribution.CommitPushResult(None, "no_changes", "")
        body = pr.summary_render.build_summary_body(
            content(fixed=[CommentItem(id="t1", summary="fix", file="a.py", line=9)]),
            cp, "owner/repo", 1, {},
        )
        assert "| `a.py:9` |" in body
        assert "/blob/" not in body
