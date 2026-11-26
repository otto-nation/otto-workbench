"""pr.summary_render: already-addressed framing (Fixed vs Addressed)."""

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from review_threads_support import (  # noqa: E402
    _AFTER_THE_REVIEW, _BEFORE_THE_REVIEW, _THE_REVIEW_COMMENT, content,
)
from conftest import git_out, run_checked
import git.topology
from git.land import CommitStatus
import pr.thread_replies
import pr.attribution
import pr.summary_render
from pr.thread_models import CommentItem, ReportThread


class TestAddressedInResponseFraming:
    """A reviewer we acted for must not be told their comment needed no action.

    Triage reads current HEAD, which holds the fixes an earlier round of the
    same cycle landed, so a thread the pass fixed comes back `already_addressed`
    on the next run. The verdict answers "does the code do this now?" correctly;
    the flat "Already addressed" answers "was your comment moot?" wrongly.
    """

    @staticmethod
    def _git(wt, *args, when=""):
        env = dict(os.environ)
        if when:
            env["GIT_AUTHOR_DATE"] = when
            env["GIT_COMMITTER_DATE"] = when
        run_checked(["git", "-C", str(wt), *args], env=env)

    def _sha(self, wt, rev):
        return git_out(wt, "rev-parse", rev).strip()

    @pytest.fixture
    def branch(self, worktree):
        """A branch with one commit dated before the review and one after it."""
        hooks = worktree / ".git" / "empty-hooks"
        hooks.mkdir()
        self._git(worktree, "config", "user.email", "test@example.com")
        self._git(worktree, "config", "user.name", "Test")
        self._git(worktree, "config", "commit.gpgsign", "false")
        self._git(worktree, "config", "core.hooksPath", str(hooks))
        (worktree / "a.py").write_text("one\ntwo\n")
        self._git(worktree, "add", "-A")
        self._git(worktree, "commit", "-qm", "base")
        self._git(worktree, "update-ref", "refs/remotes/origin/main", "HEAD")
        (worktree / "a.py").write_text("ONE\ntwo\n")
        self._git(worktree, "commit", "-qam", "line one, long before the review",
                  when=_BEFORE_THE_REVIEW)
        before = self._sha(worktree, "HEAD")
        (worktree / "a.py").write_text("ONE\nTWO\n")
        self._git(worktree, "commit", "-qam", "line two, in response to the review",
                  when=_AFTER_THE_REVIEW)
        return SimpleNamespace(path=worktree, before=before,
                               after=self._sha(worktree, "HEAD"))

    @staticmethod
    def _thread(tid, database_id):
        return ReportThread(id=tid, comments=[
            {"databaseId": database_id, "createdAt": _THE_REVIEW_COMMENT},
        ])

    def _reply_body(self, entry, thread, wt_path, **kwargs):
        with patch.object(git.topology, "default_branch_cached", return_value="main"), \
             patch("pr.comments.post_thread_reply", return_value=True) as post, \
             patch("git.push.holds", return_value=True):
            pr.thread_replies.post_already_addressed_replies(
                [entry], {entry.id: thread}, "owner/repo", 42, wt_path, **kwargs,
            )
        return post.call_args[0][3]

    def test_a_commit_after_the_review_reads_as_addressed_in_response(self, branch):
        body = self._reply_body(
            CommentItem(id="t2", summary="rename it", file="a.py", line=2),
            self._thread("t2", 222), branch.path,
        )
        assert body.startswith("Applied: rename it")
        assert "Already addressed" not in body
        assert "Fixed in" not in body
        assert "Current behaviour is at" in body

    def test_code_predating_the_comment_cites_the_tree(self, branch):
        """The genuine case: the reviewer's point was true before they made it."""
        body = self._reply_body(
            CommentItem(id="t1", summary="use the helper", file="a.py", line=1),
            self._thread("t1", 111), branch.path,
        )
        assert body.startswith("Addressed in")
        assert branch.after[:7] in body
        assert "use the helper" in body
        assert "Applied:" not in body
        assert "Already addressed" not in body

    def test_an_undated_thread_reads_as_a_fix(self, branch):
        """Addressed is the claim that needs dates. Missing them is not proof."""
        body = self._reply_body(
            CommentItem(id="t2", summary="rename it", file="a.py", line=2),
            ReportThread(id="t2", comments=[{"databaseId": 222}]), branch.path,
        )
        assert body.startswith("Applied: rename it")
        assert "Addressed in" not in body

    def test_a_fix_the_resolver_cannot_cite_still_reads_as_a_fix(self, branch):
        """#827's caller: a FIXED entry whose commit a hook rejected lands here.

        The pass acted on the thread — that is what put the entry in `fixed` —
        so the reply says so even though no commit can be named for it. The one
        commit the branch offers for that line predates the comment and cannot
        be what carried a fix made after it, so nothing is cited.
        """
        entry = CommentItem(id="t1", summary="use the helper", file="a.py", line=1)
        cp = pr.attribution.CommitPushResult(None, CommitStatus.NO_CHANGES, "")
        with patch.object(git.topology, "default_branch_cached", return_value="main"), \
             patch("pr.comments.post_thread_reply", return_value=True) as post:
            pr.thread_replies.reply_to_fixed(
                [entry], {"t1": self._thread("t1", 111)}, "owner/repo", 42,
                cp, branch.path,
            )
        body = post.call_args[0][3]
        assert body.startswith("Applied: use the helper")
        assert "Already addressed" not in body
        assert branch.before[:7] not in body

    def _summary(self, content, entry, thread, wt_path):
        cp = pr.attribution.CommitPushResult(None, CommitStatus.NO_CHANGES, "")
        with patch.object(git.topology, "default_branch_cached", return_value="main"):
            return pr.summary_render.build_summary_body(
                content(already_addressed=[entry]),
                cp, "owner/repo", 42, {entry.id: thread}, wt_path=wt_path,
            )

    def test_the_summary_row_reports_a_responsive_fix_as_fixed(self, content, branch):
        body = self._summary(
            content, CommentItem(id="t2", summary="rename it", file="a.py", line=2),
            self._thread("t2", 222), branch.path,
        )
        assert "Fixed in" in body or "Fix applied (commit not recorded)" in body
        assert "Already addressed" not in body
        assert "**1 fixed**" in body

    def test_the_summary_row_keeps_already_addressed_for_older_code(
        self, content, branch,
    ):
        body = self._summary(
            content,
            CommentItem(id="t1", summary="use the helper", file="a.py", line=1),
            self._thread("t1", 111), branch.path,
        )
        assert "Addressed in" in body
        assert "1 addressed" in body
        assert "fixed" not in body

    def test_an_undated_summary_row_reads_as_fixed(self, content, branch):
        body = self._summary(
            content,
            CommentItem(id="t2", summary="rename it", file="a.py", line=2),
            ReportThread(id="t2", comments=[{"databaseId": 222}]),
            branch.path,
        )
        assert "Fixed in" in body or "Fix applied (commit not recorded)" in body
        assert "Addressed in" not in body
