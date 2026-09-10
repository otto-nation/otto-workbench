"""Tests for `pr.thread_replies` — the reply paths nothing exercised directly.

The four builders and the two predicates are covered heavily through
`test_review_threads.py`, which drives them end to end. What had no test at all
is the machinery underneath: the upsert's three branches, and the two log lines
the driver emits about what it edited and what it refused to touch.
"""

import sys
from unittest.mock import patch

from conftest import REPO_ROOT, git_in, run_checked

LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pytest  # noqa: E402

from git.land import CommitStatus  # noqa: E402
from pr import attribution  # noqa: E402
from pr import thread_replies  # noqa: E402
from pr.thread_models import CommentItem, ReportThread  # noqa: E402

_REPO = "owner/repo"
_PR = 42
_SHA = "abc1234"


def _thread(*, db_id=111, comments=None, my_login="me"):
    if comments is None:
        comments = [{"databaseId": db_id, "author": {"login": "kgn"}}]
    return ReportThread(id="t1", my_login=my_login, comments=comments)


class TestHandledPrefixesTrackTheGeneratedSet:
    """The handled subset is derived, so a fifth opening cannot miss it.

    Reconciliation reads a thread as handled when our standing reply opens with
    one of these. That set was listed by hand in `review-threads`, which meant a
    new generated opening added here was recognised as ours everywhere except
    reconciliation — where the thread would then stay open for the life of the
    PR. Deriving it makes the omission impossible; these tests pin the one
    exclusion that is deliberate.
    """

    def test_the_deferred_opening_is_the_only_one_excluded(self):
        assert set(thread_replies.GENERATED_REPLY_PREFIXES) - set(
            thread_replies.HANDLED_REPLY_PREFIXES,
        ) == {thread_replies.DEFERRED_REPLY_PREFIX}

    def test_every_handled_opening_is_a_generated_one(self):
        assert set(thread_replies.HANDLED_REPLY_PREFIXES) <= set(
            thread_replies.GENERATED_REPLY_PREFIXES,
        )

    def test_deferring_is_not_handling(self):
        assert thread_replies.DEFERRED_REPLY_PREFIX not in (
            thread_replies.HANDLED_REPLY_PREFIXES
        )


class TestTheFollowupPatternKnowsEveryLead:
    """The pattern must recognise every body a builder actually writes.

    `is_generated_reply` accepts a body only when every trailing paragraph
    matches `_GENERATED_FOLLOWUP_RE`, so a builder that gains a sentence the
    pattern does not know makes its own reply unrecognisable — and an
    unrecognised reply reads as hand-written and is never updated again for the
    life of the PR. The comment above the pattern asks a human to keep the two
    in step; this asks the suite instead.

    Driven through the real builders rather than a transcribed list of bodies,
    following `TestGeneratedActionCell` on the cell side: a copy of the wording
    would keep passing after the wording changed, which is the failure being
    guarded against.

    Retired by #1219, which marks generated bodies rather than recognising them
    by their prose. Delete this with the pattern.
    """

    @pytest.fixture
    def captured(self):
        """Every body the builder under test hands to the upsert."""
        bodies = []

        def record(thread, repo, pr_number, body, existing_id):
            bodies.append(body)
            return True

        with patch.object(thread_replies, "upsert_thread_reply", record):
            yield bodies

    @pytest.fixture
    def entry(self):
        return CommentItem(
            id="t1", summary="drop the guard", reviewer="kgn",
            reasoning="the premise does not hold", file="f.py", line=2,
            read_sha=_SHA, commit_sha=_SHA,
        )

    @pytest.fixture
    def threads(self):
        return {"t1": _thread()}

    @pytest.fixture
    def tree(self, tmp_path):
        """A real repo holding the cited file.

        Both halves are needed or the trailing sentence never renders and the
        case proves nothing: `code_link` returns "" for a file the tree does
        not have, and it also returns "" when `head_sha` is empty — which is
        what `git_client.head_sha` answers for a directory that is not a repo.
        """
        run_checked(["git", "init", "-q", "-b", "main", str(tmp_path)])
        git_in(tmp_path, "config", "user.email", "t@example.com")
        git_in(tmp_path, "config", "user.name", "Test")
        (tmp_path / "f.py").write_text("one\ntwo\nthree\n")
        git_in(tmp_path, "add", "-A")
        git_in(tmp_path, "commit", "-q", "--no-verify", "-m", "base")
        return tmp_path

    def _assert_all_ours(self, bodies):
        assert bodies, "the builder wrote nothing — the case proves nothing"
        for body in bodies:
            assert thread_replies.is_generated_reply(body) is True, body

    def test_a_fixed_reply_is_recognised(self, captured, entry, threads):
        thread_replies.post_fix_replies(
            [entry], threads, _REPO, _PR,
            attribution.CommitPushResult(_SHA, CommitStatus.PUSHED, ""))
        self._assert_all_ours(captured)
        assert "Result is in" in captured[0]

    def test_a_fixed_reply_with_no_file_is_recognised(self, captured, threads):
        bare = CommentItem(id="t1", summary="s", reviewer="kgn", commit_sha=_SHA)
        thread_replies.post_fix_replies(
            [bare], threads, _REPO, _PR,
            attribution.CommitPushResult(_SHA, CommitStatus.PUSHED, ""))
        self._assert_all_ours(captured)

    @pytest.mark.parametrize("acted", [True, False])
    def test_an_addressed_reply_is_recognised(
            self, captured, entry, threads, tree, acted):
        thread_replies.post_already_addressed_replies(
            [entry], threads, _REPO, _PR, tree, acted=acted)
        self._assert_all_ours(captured)
        assert "Current behaviour is at" in captured[0]

    @pytest.mark.parametrize("reasoning", ["the premise does not hold", ""])
    def test_a_dismissal_is_recognised(
            self, captured, threads, tree, reasoning):
        item = CommentItem(id="t1", summary="s", reviewer="kgn",
                           reasoning=reasoning, file="f.py", line=2,
                           evidence_file="f.py", evidence_line=2,
                           read_sha=_SHA)
        thread_replies.post_dismissed_replies(
            [item], threads, _REPO, _PR, tree)
        self._assert_all_ours(captured)
        assert "See " in captured[0]

    @pytest.mark.parametrize("issue_url", ["https://linear.app/i/ENG-1", ""])
    def test_a_deferral_is_recognised(
            self, captured, entry, threads, tree, issue_url):
        thread_replies.post_deferred_replies(
            [entry], threads, _REPO, _PR, "ENG-1", issue_url, tree)
        self._assert_all_ours(captured)
        assert "Unchanged at" in captured[0]

    def test_a_lead_the_pattern_does_not_know_is_not_ours(self):
        """The failure this guards, shown once.

        A builder gaining "Landed in ..." without the pattern gaining it too
        writes a reply the next round refuses to touch.
        """
        assert thread_replies.is_generated_reply(
            "Applied: x\n\nLanded in [`abc1234`](u).") is False


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
