"""pr.thread_replies: hand-written replies, --reply, and deferred replies."""

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
from review_threads_support import _no_published_summary, _standing_reply_thread  # noqa: E402
from conftest import make_ctx
from gh.pr_pages import ThreadSet
from pr.comments_state import ThreadState
import core.log
import pr.comments
import pr.thread_replies
import pr.attribution
import pr.permalinks
from pr.thread_models import CommentItem, ReportThread


# ── --reply ──────────────────────────────────────────────────────────────


def _raw_thread(tid, comment_ids, login="reviewer", resolved=False):
    return {
        "id": tid,
        "isResolved": resolved,
        "path": "src/app.py",
        "line": 4,
        "comments": {"nodes": [
            {"databaseId": cid, "body": "point", "author": {"login": login}}
            for cid in comment_ids
        ]},
    }


def _raw_answered_thread(tid="PRRT_abc", resolved=False):
    """A reviewer's thread we have already replied to, as GraphQL returns it."""
    raw = _raw_thread(tid, [111, 222], resolved=resolved)
    raw["comments"]["nodes"][1]["author"] = {"login": "me"}
    return raw


class TestHandWrittenRepliesSurvive:
    """Re-draining the queue must not overwrite replies a human rewrote."""

    def _reply(self, body):
        """Run the fix-reply upsert against a thread whose standing reply is `body`."""
        entry = CommentItem(id="t1", summary="fix it", file="a.py", line=1,
                            commit_sha="abc1234")
        threads_by_id = {"t1": _standing_reply_thread(body=body)}
        with patch("pr.comments.patch_thread_reply", return_value=True) as edit, \
             patch("pr.comments.post_thread_reply", return_value=True) as post:
            count = pr.thread_replies.post_fix_replies(
                [entry], threads_by_id, "owner/repo", 42,
                pr.attribution.CommitPushResult("abc1234", "pushed", ""),
            )
        return count, edit, post

    def test_a_rewritten_reply_is_left_alone(self):
        count, edit, post = self._reply((
            "Applied: fix it\n\n"
            "On reflection we are not doing this — the reviewer's premise "
            "assumes a code path that was removed in #700."
        ))
        assert count == 0
        edit.assert_not_called()
        post.assert_not_called()

    def test_a_reply_that_is_still_the_template_is_refreshed(self):
        """Pairs with the case above — proves that assertion is not vacuous."""
        count, edit, _ = self._reply((
            "Applied: fix it\n\n"
            "Fixed in [`0000000`](https://github.com/owner/repo/commit/0000000)."
        ))
        assert count == 1
        edit.assert_called_once()
        assert "abc1234" in edit.call_args[0][2]

    def test_a_reviewers_own_words_are_never_taken_for_ours(self):
        count, _, _ = self._reply("Thanks, that works for me.")
        assert count == 0

    def _reply_below_a_reviewer_answer(self, body):
        """As `_reply`, but a reviewer has since answered our standing reply."""
        entry = CommentItem(id="t1", summary="fix it", file="a.py", line=1,
                            commit_sha="abc1234")
        thread = _standing_reply_thread(body=body, state=ThreadState.CONTESTED)
        thread.comments.append({
            "databaseId": 333,
            "body": "Agreed — I verified the rewrite at four DOM positions.",
            "author": {"login": "kgn"},
        })
        with patch("pr.comments.patch_thread_reply", return_value=True) as edit, \
             patch("pr.comments.post_thread_reply", return_value=True) as post:
            count = pr.thread_replies.post_fix_replies(
                [entry], {"t1": thread}, "owner/repo", 42,
                pr.attribution.CommitPushResult("abc1234", "pushed", ""),
            )
        return count, edit, post

    def test_a_rewritten_reply_survives_a_reviewer_answering_it(self):
        """The protection used to vanish the moment the thread became a
        conversation: a reviewer's answer retired the standing-reply id the
        hand-written check was gated on, and the round stacked a third comment
        restating a settled position."""
        count, edit, post = self._reply_below_a_reviewer_answer((
            "Applied: fix it\n\n"
            "On reflection we are not doing this — the reviewer's premise "
            "assumes a code path that was removed."
        ))
        assert count == 0
        edit.assert_not_called()
        post.assert_not_called()

    def test_a_template_reply_is_still_reposted_once_answered(self):
        """Pairs with the case above — proves that assertion is not vacuous.

        A generated standing reply is still replaced by a fresh comment under
        the reviewer's answer; only the hand-written case is protected.
        """
        count, edit, post = self._reply_below_a_reviewer_answer((
            "Applied: fix it\n\n"
            "Fixed in [`0000000`](https://github.com/owner/repo/commit/0000000)."
        ))
        assert count == 1
        edit.assert_not_called()
        assert post.call_args[0][2] == 111

    def test_the_newest_reply_of_ours_decides(self):
        """`--reply` after a hand edit is the escape hatch, and it must settle
        the question — the older hand-written reply cannot outvote it."""
        thread = _standing_reply_thread(body="We are not doing this, and here is why.")
        thread.comments.append({
            "databaseId": 333, "body": "no", "author": {"login": "kgn"}})
        thread.comments.append({
            "databaseId": 444,
            "body": "Applied: fix it\n\nFixed in [`0000000`]"
                    "(https://github.com/owner/repo/commit/0000000).",
            "author": {"login": "me"},
        })
        assert pr.thread_replies.has_hand_written_reply(thread) is False

    def test_a_thread_nobody_of_ours_has_touched_is_not_held(self):
        thread = ReportThread(id="t1", my_login="me", comments=[
            {"databaseId": 111, "body": "the point", "author": {"login": "kgn"}},
            {"databaseId": 222, "body": "seconded", "author": {"login": "ana"}},
        ])
        assert pr.thread_replies.has_hand_written_reply(thread) is False

    def test_our_own_review_point_is_not_a_reply(self):
        """On a self-review the root is ours and is hand-written by definition;
        reading it as our standing reply would skip every thread."""
        thread = ReportThread(id="t1", my_login="me", comments=[
            {"databaseId": 111, "body": "this needs a guard", "author": {"login": "me"}},
        ])
        assert pr.thread_replies.has_hand_written_reply(thread) is False

    def test_our_root_is_still_skipped_once_someone_replies(self):
        """The other half of the docstring's root-skip: a self-review root
        authored by us, with a reply since posted. Scanning the root instead
        of skipping it would misread our own review point as a hand-written
        reply and return True here."""
        thread = ReportThread(id="t1", my_login="me", comments=[
            {"databaseId": 111, "body": "this needs a guard", "author": {"login": "me"}},
            {"databaseId": 222, "body": "Agreed, fixed.", "author": {"login": "kgn"}},
        ])
        assert pr.thread_replies.has_hand_written_reply(thread) is False

    @pytest.mark.parametrize("body", [
        "Applied: fix it",
        "Applied: fix it\n\nResult is in [`a.py`](https://github.com/o/r/blob/s/a.py).",
        "Already addressed: fix it\n\nCurrent behaviour is at https://x/#L1.",
        "Already addressed in the current implementation: fix it",
        "Suggestion reviewed and determined to be inapplicable: nope",
        "Suggestion reviewed and determined to be inapplicable.\n\nSee https://x/#L1.",
        "Deferred: fix it\n\nTracked in [ENG-1](https://linear.app/i/ENG-1).",
        "Deferred: fix it\n\nTracked in ENG-1.\n\nUnchanged at https://x/#L1.",
    ])
    def test_every_generated_shape_is_recognised(self, body):
        """A shape this misses is a reply the pass refuses to ever update again."""
        assert pr.thread_replies.is_generated_reply(body) is True

    @pytest.mark.parametrize("body", [
        "",
        "Sounds good to me.",
        "Applied: fix it\n\nBut see the caveat below.",
        "Deferred: fix it\n\nI disagree that this is deferrable.",
    ])
    def test_anything_else_is_treated_as_a_human_reply(self, body):
        assert pr.thread_replies.is_generated_reply(body) is False

    def test_a_body_that_only_reads_like_ours_is_still_theirs(self):
        """The near-miss is the dangerous one: it opens with our prefix and ends
        in a sentence, and only the followup opening tells it from the template."""
        body = (
            "Applied: fix it\n\n"
            "Landed in the follow-up branch rather than here."
        )
        assert pr.thread_replies.is_generated_reply(body) is False
        assert self._reply(body)[0] == 0

    def test_a_hand_sentence_reusing_a_followup_opening_is_missed(self):
        """Documents the ceiling on `_is_generated_reply`, not an endorsement.

        The followup pattern matches any sentence under a known opening, so a
        human who happens to start theirs with one is overwritten. Tightening it
        to demand a link would orphan the linkless generated shapes, which is
        the worse failure — a reply the pass can never update again.
        """
        body = "Applied: fix it\n\nFixed in the follow-up branch rather than here."
        assert pr.thread_replies.is_generated_reply(body) is True

    def test_the_addressed_fallback_body_is_recognised(self, tmp_path):
        """The single-paragraph shapes are built by a different branch of the
        body builders, so assert on what they emit rather than on a transcribed
        copy — a wording change there must not silently orphan the reply."""
        entry = CommentItem(id="t1", summary="use helper", file="src/app.py")
        with patch("pr.comments.post_thread_reply", return_value=True) as post, \
             patch.object(pr.attribution, "find_addressing_commit", return_value=None), \
             patch.object(pr.permalinks, "code_link", return_value=""):
            pr.thread_replies.post_already_addressed_replies(
                [entry], {"t1": ReportThread(id="t1", comments=[{"databaseId": 111}])},
                "owner/repo", 42, tmp_path,
            )
        assert pr.thread_replies.is_generated_reply(post.call_args[0][3]) is True

    def test_the_dismissal_body_with_no_evidence_is_recognised(self, tmp_path):
        entry = CommentItem(id="t1", summary="not applicable", reasoning="premise fails")
        with patch("pr.comments.post_thread_reply", return_value=True) as post, \
             patch.object(pr.permalinks, "evidence_link", return_value=""):
            pr.thread_replies.post_dismissed_replies(
                [entry], {"t1": ReportThread(id="t1", comments=[{"databaseId": 111}])},
                "owner/repo", 42, tmp_path,
            )
        assert pr.thread_replies.is_generated_reply(post.call_args[0][3]) is True

    def test_the_dismissal_body_with_no_reasoning_is_recognised(self, tmp_path):
        entry = CommentItem(id="t1", summary="not applicable")
        with patch("pr.comments.post_thread_reply", return_value=True) as post, \
             patch.object(pr.permalinks, "evidence_link", return_value=""):
            pr.thread_replies.post_dismissed_replies(
                [entry], {"t1": ReportThread(id="t1", comments=[{"databaseId": 111}])},
                "owner/repo", 42, tmp_path,
            )
        assert pr.thread_replies.is_generated_reply(post.call_args[0][3]) is True


class TestFindReplyTarget:

    @pytest.mark.parametrize("target", [
        "PRRT_abc",
        "222",
        "#discussion_r222",
        "https://github.com/owner/repo/pull/42#discussion_r222",
    ])
    def test_resolves_every_identifier_a_human_might_paste(self, target):
        raw = [_raw_thread("PRRT_zzz", [999]), _raw_thread("PRRT_abc", [111, 222])]
        thread = pr.thread_replies.find_reply_target(raw, target, "me")
        assert thread is not None
        assert thread.id == "PRRT_abc"

    def test_returns_none_when_nothing_matches(self):
        raw = [_raw_thread("PRRT_abc", [111])]
        assert pr.thread_replies.find_reply_target(raw, "discussion_r404", "me") is None

    def test_carries_the_lifecycle_state_the_upsert_needs(self):
        raw = [_raw_thread("PRRT_abc", [111, 222], login="me")]
        assert pr.thread_replies.find_reply_target(raw, "PRRT_abc", "me").state == ThreadState.ADDRESSED

    def test_carries_the_viewer_login_the_upsert_needs(self):
        """Without it the upsert cannot tell our own reply from a reviewer's."""
        raw = [_raw_answered_thread(resolved=True)]
        thread = pr.thread_replies.find_reply_target(raw, "PRRT_abc", "me")
        assert thread.my_login == "me"
        assert pr.thread_replies.our_last_reply_id(thread) == 222


class TestRunReply:

    def _ctx(self, tmp_path):
        return make_ctx(branch="b", worktree_root=tmp_path, head_sha="abc1234",
                        target_dir=tmp_path / "target")

    def _patches(self, raw, login="reviewer"):
        return (
            patch.object(pr.thread_replies, "fetch_pr_data",
                         return_value=SimpleNamespace(viewer_login=login)),
            patch.object(pr.comments, "fetch_threads", return_value=ThreadSet(raw)),
        )

    def test_posts_the_body_from_the_file(self, tmp_path):
        body = tmp_path / "reply.md"
        body.write_text("See https://github.com/owner/repo/blob/abc/src/app.py#L4.")
        fetch_pr, fetch_threads = self._patches([_raw_thread("PRRT_abc", [111])])
        with fetch_pr, fetch_threads, \
             patch("pr.comments.post_thread_reply", return_value=True) as post:
            code = pr.thread_replies.run_reply(self._ctx(tmp_path), "PRRT_abc", str(body))
        assert code == 0
        assert post.call_args[0][2] == 111

    def test_edits_rather_than_stacking(self, tmp_path):
        body = tmp_path / "reply.md"
        body.write_text("Revised. https://github.com/owner/repo/blob/abc/src/app.py#L4")
        raw = [_raw_thread("PRRT_abc", [111, 222], login="me")]
        fetch_pr, fetch_threads = self._patches(raw, login="me")
        with fetch_pr, fetch_threads, \
             patch("pr.comments.post_thread_reply") as post, \
             patch("pr.comments.patch_thread_reply", return_value=True) as edit:
            code = pr.thread_replies.run_reply(self._ctx(tmp_path), "discussion_r222", str(body))
        assert code == 0
        post.assert_not_called()
        assert edit.call_args[0][1] == 222

    def test_warns_when_the_body_cites_no_permalink(self, tmp_path):
        body = tmp_path / "reply.md"
        body.write_text("Trust me, the code already does this.")
        fetch_pr, fetch_threads = self._patches([_raw_thread("PRRT_abc", [111])])
        with fetch_pr, fetch_threads, \
             patch("pr.comments.post_thread_reply", return_value=True), \
             patch.object(core.log, "warn") as warn:
            assert pr.thread_replies.run_reply(self._ctx(tmp_path), "PRRT_abc", str(body)) == 0
        warn.assert_called_once()

    def test_errors_on_an_unknown_thread(self, tmp_path):
        body = tmp_path / "reply.md"
        body.write_text("something")
        fetch_pr, fetch_threads = self._patches([_raw_thread("PRRT_abc", [111])])
        with fetch_pr, fetch_threads, \
             patch("pr.comments.post_thread_reply") as post:
            assert pr.thread_replies.run_reply(self._ctx(tmp_path), "discussion_r404", str(body)) == 1
        post.assert_not_called()

    def test_errors_when_the_reply_call_fails(self, tmp_path, publishing_on):
        body = tmp_path / "reply.md"
        body.write_text("See https://github.com/owner/repo/blob/abc/src/app.py#L4.")
        fetch_pr, fetch_threads = self._patches([_raw_thread("PRRT_abc", [111])])
        with fetch_pr, fetch_threads, \
             patch("pr.comments.post_thread_reply", return_value=False), \
             patch.object(core.log, "error") as err:
            assert pr.thread_replies.run_reply(self._ctx(tmp_path), "PRRT_abc", str(body)) == 1
        err.assert_called_once()

    def test_a_drafted_reply_is_not_a_failure(self, tmp_path):
        """_gh_post reports failure whenever the publishing gate is closed."""
        body = tmp_path / "reply.md"
        body.write_text("See https://github.com/owner/repo/blob/abc/src/app.py#L4.")
        fetch_pr, fetch_threads = self._patches([_raw_thread("PRRT_abc", [111])])
        with fetch_pr, fetch_threads, \
             patch("pr.comments.post_thread_reply", return_value=False), \
             patch.object(core.log, "error") as err:
            assert pr.thread_replies.run_reply(self._ctx(tmp_path), "PRRT_abc", str(body)) == 0
        err.assert_not_called()

    def test_a_drafted_reply_says_draft_and_sends_nothing(self, tmp_path):
        """The closing line must not claim a post no draft run ever made."""
        body = tmp_path / "reply.md"
        body.write_text("See https://github.com/owner/repo/blob/abc/src/app.py#L4.")
        fetch_pr, fetch_threads = self._patches([_raw_thread("PRRT_abc", [111])])
        with fetch_pr, fetch_threads, \
             patch("core.proc.subprocess.run") as run, \
             patch.object(core.log, "info") as info:
            assert pr.thread_replies.run_reply(self._ctx(tmp_path), "PRRT_abc", str(body)) == 0
        run.assert_not_called()
        lines = [c[0][0] for c in info.call_args_list]
        assert any("DRAFT (not published)" in line for line in lines)
        assert not any("Posted" in line or "Edited" in line for line in lines)

    @pytest.mark.parametrize("raw,call,verb", [
        ([_raw_thread("PRRT_abc", [111])], "post_thread_reply", "Posted"),
        ([_raw_answered_thread()], "patch_thread_reply", "Edited"),
    ])
    def test_a_published_reply_reports_what_it_did(
        self, tmp_path, publishing_on, raw, call, verb,
    ):
        body = tmp_path / "reply.md"
        body.write_text("See https://github.com/owner/repo/blob/abc/src/app.py#L4.")
        fetch_pr, fetch_threads = self._patches(raw, login="me")
        with fetch_pr, fetch_threads, \
             patch(f"pr.comments.{call}", return_value=True), \
             patch.object(core.log, "info") as info:
            assert pr.thread_replies.run_reply(self._ctx(tmp_path), "PRRT_abc", str(body)) == 0
        assert f"{verb} reply on PRRT_abc" in [c[0][0] for c in info.call_args_list]

    def test_edits_the_standing_reply_on_a_resolved_thread(
        self, tmp_path, publishing_on,
    ):
        """End to end: --finish --post resolves the threads it answers."""
        body = tmp_path / "reply.md"
        body.write_text("Revised. https://github.com/owner/repo/blob/abc/src/app.py#L4")
        fetch_pr, fetch_threads = self._patches(
            [_raw_answered_thread(resolved=True)], login="me",
        )
        with fetch_pr, fetch_threads, \
             patch("pr.comments.post_thread_reply") as post, \
             patch("pr.comments.patch_thread_reply", return_value=True) as edit:
            assert pr.thread_replies.run_reply(self._ctx(tmp_path), "PRRT_abc", str(body)) == 0
        post.assert_not_called()
        assert edit.call_args[0][1] == 222

    def test_distinguishes_a_missing_body_file_from_an_empty_one(self, tmp_path):
        empty = tmp_path / "empty.md"
        empty.write_text("   ")
        with patch.object(pr.thread_replies, "fetch_pr_data") as fetch_pr, \
             patch.object(core.log, "error") as err:
            assert pr.thread_replies.run_reply(self._ctx(tmp_path), "PRRT_abc", None) == 1
            assert pr.thread_replies.run_reply(self._ctx(tmp_path), "PRRT_abc", "/nope.md") == 1
            assert pr.thread_replies.run_reply(self._ctx(tmp_path), "PRRT_abc", str(empty)) == 1
        fetch_pr.assert_not_called()
        messages = [c[0][0] for c in err.call_args_list]
        assert "not found" in messages[1]
        assert "empty" in messages[2]


# ── _post_deferred_replies ────────────────────────────────────────────────


class TestPostDeferredReplies:

    def test_posts_replies_with_issue_link(self):
        deferred = [
            CommentItem(id="t1", summary="fix it"),
        ]
        threads_by_id = {
            "t1": ReportThread(id="t1", comments=[{"databaseId": 111}]),
        }
        with patch("pr.comments.post_thread_reply", return_value=True) as mock_reply:
            count = pr.thread_replies.post_deferred_replies(
                deferred, threads_by_id, "owner/repo", 42,
                "ENG-456", "https://linear.app/team/issue/ENG-456",
            )
        assert count == 1
        body = mock_reply.call_args[0][3]
        assert "ENG-456" in body
        assert "linear.app" in body
        assert "Deferred" in body

    def test_no_comments_skips(self):
        deferred = [CommentItem(id="t1", summary="fix it")]
        with patch("pr.comments.post_thread_reply") as mock_reply:
            count = pr.thread_replies.post_deferred_replies(
                deferred, {}, "owner/repo", 42, "ENG-456", "",
            )
        assert count == 0
        mock_reply.assert_not_called()
