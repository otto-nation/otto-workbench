"""A resumed or re-run reply batch must not POST the same thread twice.

`_post_thread_replies` is the one writer behind triage replies, the fixed
replies `record` sends, and the `--finish` drain. Skip when the last comment
is already ours with the same body; remember a just-posted reply on the
in-memory thread so a kill mid-batch cannot POST the first threads again.
"""

import sys
from collections import Counter
from unittest.mock import patch

import pytest

from conftest import REPO_ROOT

LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from git.land import CommitStatus  # noqa: E402
import git.client  # noqa: E402
import pr.attribution  # noqa: E402
import pr.comments  # noqa: E402
import pr.published_record  # noqa: E402
import pr.settlement  # noqa: E402
import pr.thread_replies  # noqa: E402
from pr.thread_models import CommentItem, ReportThread  # noqa: E402
import fix.comment_replies  # noqa: E402


_REPO = "owner/repo"
_PR = 42


class _Killed(BaseException):
    """The interrupt a mid-batch kill is. BaseException, like KeyboardInterrupt."""


def _thread(tid, root_id, *, my_login="me"):
    return ReportThread(
        id=tid, my_login=my_login,
        comments=[{"databaseId": root_id, "author": {"login": "kgn"}, "body": "point"}],
    )


def _entry(tid, summary="s"):
    return CommentItem(id=tid, summary=summary, reviewer="kgn")


class TestAReplyBatchIsIdempotent:
    """Kill after the first POST, resume, each thread is written once."""

    def _run(self, entries, threads, body_fn=None):
        return pr.thread_replies._post_thread_replies(
            entries, threads, _REPO, _PR, body_fn or (lambda e: f"body-{e.id}"))

    def test_a_kill_mid_batch_then_resume_posts_each_thread_once(self):
        threads = {"t1": _thread("t1", 111), "t2": _thread("t2", 222)}
        entries = [_entry("t1"), _entry("t2")]
        posted = []
        seen_second = False

        def post(repo, pr_number, root_id, body):
            nonlocal seen_second
            if root_id == 222 and not seen_second:
                seen_second = True
                raise _Killed()
            posted.append(root_id)
            return True

        with patch.object(pr.comments, "post_thread_reply", side_effect=post), \
                patch.object(pr.comments, "patch_thread_reply") as edit:
            with pytest.raises(_Killed):
                self._run(entries, threads)
            self._run(entries, threads)

        assert Counter(posted) == {111: 1, 222: 1}
        edit.assert_not_called()

    def test_a_re_run_does_not_resend_an_already_sent_reply(self):
        """record/closeout calling the driver again on the same threads."""
        threads = {"t1": _thread("t1", 111)}
        posted = []

        def post(repo, pr_number, root_id, body):
            posted.append(root_id)
            return True

        with patch.object(pr.comments, "post_thread_reply", side_effect=post), \
                patch.object(pr.comments, "patch_thread_reply") as edit:
            assert self._run([_entry("t1")], threads) == 1
            assert self._run([_entry("t1")], threads) == 0

        assert posted == [111]
        edit.assert_not_called()

    def test_a_refetched_thread_whose_last_comment_is_ours_is_not_reposted(self):
        """Resume after GitHub already has the reply — no extra API round trip."""
        body = pr.published_record.stamp_reply("body-t1")
        thread = ReportThread(
            id="t1", my_login="me", comments=[
                {"databaseId": 111, "author": {"login": "kgn"}, "body": "point"},
                {"databaseId": 222, "author": {"login": "me"}, "body": body},
            ],
        )
        with patch.object(pr.comments, "post_thread_reply") as post, \
                patch.object(pr.comments, "patch_thread_reply") as edit:
            assert self._run([_entry("t1")], {"t1": thread}) == 0
        post.assert_not_called()
        edit.assert_not_called()

    def test_a_different_body_still_edits_our_standing_reply(self):
        """Idempotence is same-body, not a freeze of the standing reply."""
        thread = ReportThread(
            id="t1", my_login="me", comments=[
                {"databaseId": 111, "author": {"login": "kgn"}, "body": "point"},
                {"databaseId": 222, "author": {"login": "me"}, "body": "Applied: earlier"},
            ],
        )
        with patch.object(pr.comments, "post_thread_reply") as post, \
                patch.object(pr.comments, "patch_thread_reply", return_value=True) as edit:
            assert self._run([_entry("t1")], {"t1": thread}) == 1
        post.assert_not_called()
        edit.assert_called_once()


class TestSettleFixedDoesNotResend:
    """`CommentFixAdapter.record` drains through `settle_fixed`."""

    def test_a_second_record_does_not_repost_a_fixed_reply(self, tmp_path):
        item = CommentItem(
            id="t1", summary="use helper", file="src/app.py", commit_sha="abc1234",
        )
        threads = {"t1": _thread("t1", 111)}
        cp = pr.attribution.CommitPushResult("abc1234", CommitStatus.PUSHED, "")
        posted = []

        def post(repo, pr_number, root_id, body):
            posted.append(root_id)
            return True

        with patch.object(pr.comments, "post_thread_reply", side_effect=post), \
                patch.object(pr.comments, "patch_thread_reply") as edit, \
                patch.object(pr.settlement, "resolve_fixed_threads", return_value=()), \
                patch.object(git.client, "head_sha", return_value="abc1234"), \
                patch.object(pr.attribution, "find_addressing_commit",
                             return_value="abc1234"):
            first = fix.comment_replies.settle_fixed(
                [item], threads, _REPO, _PR, cp, tmp_path,
            )
            second = fix.comment_replies.settle_fixed(
                [item], threads, _REPO, _PR, cp, tmp_path,
            )

        assert first.posted == 1
        assert second.posted == 0
        assert posted == [111]
        edit.assert_not_called()
