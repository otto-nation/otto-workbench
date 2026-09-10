"""Tests for `pr.thread_replies` — the reply paths nothing exercised directly.

The four builders and the two predicates are covered heavily through
`test_review_threads.py`, which drives them end to end. What had no test at all
is the machinery underneath: the upsert's three branches, and the two log lines
the driver emits about what it edited and what it refused to touch.
"""

import sys
from unittest.mock import patch

from conftest import REPO_ROOT

LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pytest  # noqa: E402

from pr import thread_replies  # noqa: E402
from pr.thread_models import CommentItem, ReportThread  # noqa: E402

_REPO = "owner/repo"
_PR = 42


def _thread(*, db_id=111, comments=None, my_login="me"):
    if comments is None:
        comments = [{"databaseId": db_id, "author": {"login": "kgn"}}]
    return ReportThread(id="t1", my_login=my_login, comments=comments)


class TestUpsertThreadReply:
    """Three branches, none of which had a test naming this function."""

    def test_an_existing_reply_is_edited_in_place(self):
        with patch.object(thread_replies.pc, "patch_thread_reply",
                          return_value=True) as edit, \
             patch.object(thread_replies.pc, "post_thread_reply") as post:
            assert thread_replies.upsert_thread_reply(
                _thread(), _REPO, _PR, "body", 999) is True

        edit.assert_called_once_with(_REPO, 999, "body")
        post.assert_not_called()

    def test_no_existing_reply_posts_under_the_thread_root(self):
        with patch.object(thread_replies.pc, "post_thread_reply",
                          return_value=True) as post, \
             patch.object(thread_replies.pc, "patch_thread_reply") as edit:
            assert thread_replies.upsert_thread_reply(
                _thread(db_id=222), _REPO, _PR, "body", None) is True

        post.assert_called_once_with(_REPO, _PR, 222, "body")
        edit.assert_not_called()

    def test_a_thread_whose_root_has_no_id_is_left_alone(self):
        """The one branch no end-to-end test reaches: nothing to reply under."""
        with patch.object(thread_replies.pc, "post_thread_reply") as post, \
             patch.object(thread_replies.pc, "patch_thread_reply") as edit:
            assert thread_replies.upsert_thread_reply(
                _thread(comments=[{"author": {"login": "kgn"}}]),
                _REPO, _PR, "body", None) is False

        post.assert_not_called()
        edit.assert_not_called()


class TestWhatTheDriverReports:
    """The two log lines, which no test asserted.

    They are the operator's only signal that a reply was edited rather than
    posted, and that one was deliberately not written at all.
    """

    @pytest.fixture
    def entry(self):
        return CommentItem(id="t1", summary="s", reviewer="kgn")

    def _run(self, entries, threads):
        return thread_replies._post_thread_replies(
            entries, threads, _REPO, _PR, lambda e: "generated body")

    def test_editing_one_reply_reads_as_singular(self, entry, caplog):
        thread = _thread(comments=[
            {"databaseId": 111, "author": {"login": "kgn"}},
            {"databaseId": 222, "author": {"login": "me"},
             "body": "Applied: earlier"},
        ])
        with patch.object(thread_replies.pc, "patch_thread_reply",
                          return_value=True), \
             patch.object(thread_replies.pc, "last_comment_is_mine",
                          return_value=True), \
             patch.object(thread_replies.log, "info") as info:
            assert self._run([entry], {"t1": thread}) == 1

        assert any("Edited 1 standing reply in place" in c.args[0]
                   for c in info.call_args_list)

    def test_a_hand_written_reply_is_reported_with_the_way_out(self, entry):
        """Refusing to write is only defensible if the operator is told how."""
        thread = _thread(comments=[
            {"databaseId": 111, "author": {"login": "kgn"}},
            {"databaseId": 222, "author": {"login": "me"},
             "body": "I disagree, and here is why"},
        ])
        with patch.object(thread_replies.pc, "patch_thread_reply") as edit, \
             patch.object(thread_replies.pc, "post_thread_reply") as post, \
             patch.object(thread_replies.log, "info") as info:
            assert self._run([entry], {"t1": thread}) == 0

        edit.assert_not_called()
        post.assert_not_called()
        message = " ".join(c.args[0] for c in info.call_args_list)
        assert "Left 1 hand-written reply untouched" in message
        assert "--reply <id> --body-file <f>" in message

    def test_a_thread_the_report_does_not_carry_is_skipped(self, entry):
        with patch.object(thread_replies.pc, "post_thread_reply") as post:
            assert self._run([entry], {}) == 0
        post.assert_not_called()

    def test_a_thread_with_no_comments_is_skipped(self, entry):
        with patch.object(thread_replies.pc, "post_thread_reply") as post:
            assert self._run([entry], {"t1": _thread(comments=[])}) == 0
        post.assert_not_called()

    def test_a_failed_post_does_not_count_as_replied(self, entry):
        with patch.object(thread_replies.pc, "post_thread_reply",
                          return_value=False):
            assert self._run([entry], {"t1": _thread()}) == 0
