"""pr.thread_replies: reply bodies, evidence and the upsert."""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

# `_no_published_summary` is autouse: imported so pytest applies it here,
# never referenced by name.
from review_threads_support import _no_published_summary, _standing_reply_thread  # noqa: E402
from pr.comments_state import ThreadState
import git.client
from git.land import CommitStatus
import pr.thread_replies
import pr.attribution
import pr.permalinks
from pr.thread_models import CommentItem, ReportThread


def _dismissed(**overrides):
    """A dismissed-verdict CommentItem for the reply-upsert tests below."""
    fields = {"id": "t1", "summary": "not applicable", "reasoning": "reason"}
    fields.update(overrides)
    return CommentItem(**fields)


# ── _post_already_addressed_replies ───────────────────────────────────────


class TestPostAlreadyAddressedReplies:

    def test_posts_replies_with_commit_ref(self, tmp_path):
        fixed = [CommentItem(id="t1", summary="use helper", file="src/app.py")]
        threads_by_id = {"t1": ReportThread(id="t1", comments=[{"databaseId": 111}])}
        with (
            patch("pr.comments.post_thread_reply", return_value=True) as mock_reply,
            patch.object(pr.attribution, "find_addressing_commit", return_value="abc1234def5678"),
        ):
            count = pr.thread_replies.post_already_addressed_replies(
                fixed, threads_by_id, "owner/repo", 42, tmp_path,
            )
        assert count == 1
        body = mock_reply.call_args[0][3]
        assert "Already addressed" in body
        assert "use helper" in body
        assert "abc1234" in body
        assert "owner/repo/commit/abc1234def5678" in body

    def test_fallback_when_no_commit_found(self, tmp_path):
        fixed = [CommentItem(id="t1", summary="use helper", file="src/app.py")]
        threads_by_id = {"t1": ReportThread(id="t1", comments=[{"databaseId": 111}])}
        with (
            patch("pr.comments.post_thread_reply", return_value=True) as mock_reply,
            patch.object(pr.attribution, "find_addressing_commit", return_value=None),
        ):
            count = pr.thread_replies.post_already_addressed_replies(
                fixed, threads_by_id, "owner/repo", 42, tmp_path,
            )
        assert count == 1
        body = mock_reply.call_args[0][3]
        assert "Already addressed" in body
        assert "commit" not in body

    def test_no_comments_skips(self, tmp_path):
        fixed = [CommentItem(id="t1", summary="use helper", file="src/app.py")]
        with patch("pr.comments.post_thread_reply") as mock_reply:
            count = pr.thread_replies.post_already_addressed_replies(
                fixed, {}, "owner/repo", 42, tmp_path,
            )
        assert count == 0
        mock_reply.assert_not_called()

    def test_an_acted_reply_carries_the_unverified_hedge(self, tmp_path):
        """The unattributed half of `reply_to_fixed` claims a fix and owes the
        same caveat the attributed half carries.

        `acted` is what says the caller landed the change itself. Without the
        hedge here, a fix that could be neither verified nor attributed reaches
        the reviewer as a plain claim.
        """
        fixed = [CommentItem(id="t1", summary="use helper", file="src/app.py",
                             verified=False, verify_detail="no runnable check")]
        threads_by_id = {"t1": ReportThread(id="t1", comments=[{"databaseId": 111}])}
        with (
            patch("pr.comments.post_thread_reply", return_value=True) as mock_reply,
            patch.object(pr.attribution, "find_addressing_commit", return_value=None),
        ):
            pr.thread_replies.post_already_addressed_replies(
                fixed, threads_by_id, "owner/repo", 42, tmp_path, acted=True,
            )
        body = mock_reply.call_args[0][3]
        assert pr.thread_replies.UNVERIFIED_REPLY_NOTE in body
        assert "no runnable check" in body

    def _satisfied_body(self, tmp_path, **entry_kw):
        fixed = [CommentItem(id="t1", summary="use helper", file="src/app.py",
                             **entry_kw)]
        threads_by_id = {"t1": ReportThread(id="t1", comments=[{"databaseId": 111}])}
        with (
            patch("pr.comments.post_thread_reply", return_value=True) as mock_reply,
            patch.object(pr.attribution, "find_addressing_commit", return_value=None),
        ):
            pr.thread_replies.post_already_addressed_replies(
                fixed, threads_by_id, "owner/repo", 42, tmp_path,
            )
        return mock_reply.call_args[0][3]

    def test_a_satisfied_reply_the_gate_never_saw_carries_no_hedge(self, tmp_path):
        """Not acted, and no gate ran (`--no-verify`): the reply reads as it always did.

        Hedging here would caveat a claim the reviewer can check at the link in
        the same reply, and would put the note on rows that never earned one.
        """
        body = self._satisfied_body(tmp_path)
        assert pr.thread_replies.UNVERIFIED_REPLY_NOTE not in body

    def test_a_satisfied_reply_the_gate_could_not_settle_is_hedged(self, tmp_path):
        """Not acted, but checked: the verify gate now sees every such verdict
        before its reply goes out, and one it could not settle says so.
        """
        body = self._satisfied_body(
            tmp_path, verified=False, verify_detail="no runnable check")
        assert pr.thread_replies.UNVERIFIED_REPLY_NOTE in body
        assert "no runnable check" in body


# ── the host reaches the reply bodies ────────────────────────────────────


class TestReplyBodiesRenderTheEnterpriseHost:
    """Every reply body that cites a link, rendered against a GHES host.

    The linkers taking a `host` buys nothing if the reply path drops it, and a
    dropped argument is invisible at the unit level: each of these functions
    renders a URL that resolves either way. Only the host in the rendered body
    tells a threaded call from an untaught one.

    Public github.com is asserted absent rather than the host asserted present,
    because the failure being tested is a link on the *wrong* forge.
    """

    HOST = "ghe.acme.com"

    def test_a_fixed_reply_cites_the_enterprise_commit_and_blob(self, tmp_path):
        fixed = [CommentItem(id="t1", summary="use helper", file="src/app.py",
                             commit_sha="abc1234")]
        threads_by_id = {"t1": ReportThread(id="t1", comments=[{"databaseId": 111}])}
        cp = pr.attribution.CommitPushResult("abc1234", CommitStatus.PUSHED, "")
        with patch("pr.comments.post_thread_reply", return_value=True) as mock_reply:
            pr.thread_replies.post_fix_replies(
                fixed, threads_by_id, "owner/repo", 42, cp, host=self.HOST,
            )
        body = mock_reply.call_args[0][3]
        assert f"https://{self.HOST}/owner/repo/commit/abc1234" in body
        assert f"https://{self.HOST}/owner/repo/blob/abc1234/src/app.py" in body
        assert "github.com" not in body

    def test_an_already_addressed_reply_cites_the_enterprise_commit(self, tmp_path):
        fixed = [CommentItem(id="t1", summary="use helper", file="src/app.py")]
        threads_by_id = {"t1": ReportThread(id="t1", comments=[{"databaseId": 111}])}
        with (
            patch("pr.comments.post_thread_reply", return_value=True) as mock_reply,
            patch.object(pr.attribution, "find_addressing_commit",
                         return_value="abc1234def5678"),
        ):
            pr.thread_replies.post_already_addressed_replies(
                fixed, threads_by_id, "owner/repo", 42, tmp_path, host=self.HOST,
            )
        body = mock_reply.call_args[0][3]
        assert f"https://{self.HOST}/owner/repo/commit/abc1234def5678" in body
        assert "github.com" not in body

    def test_a_dismissed_reply_cites_the_enterprise_evidence(self, tmp_path):
        """The reply that most needs a line to point at, per the module docstring."""
        (tmp_path / "cited.py").write_text("a\nb\n")
        dismissed = [CommentItem(
            id="t1", summary="not applicable", reasoning="reason",
            evidence_file="cited.py", evidence_line=2, read_sha="head123")]
        threads_by_id = {"t1": ReportThread(id="t1", comments=[{"databaseId": 111}])}
        with (
            patch("pr.comments.post_thread_reply", return_value=True) as mock_reply,
            patch.object(git.client, "head_sha", return_value="head123"),
        ):
            pr.thread_replies.post_dismissed_replies(
                dismissed, threads_by_id, "owner/repo", 42, tmp_path, self.HOST,
            )
        body = mock_reply.call_args[0][3]
        assert f"https://{self.HOST}/owner/repo/blob/head123/cited.py#L2" in body
        assert "github.com" not in body

    def test_a_deferred_reply_cites_the_enterprise_code(self, tmp_path):
        deferred = [CommentItem(id="t1", summary="fix it", file="src/app.py",
                                line=3, read_sha="head123")]
        threads_by_id = {"t1": ReportThread(id="t1", comments=[{"databaseId": 111}])}
        with (
            patch("pr.comments.post_thread_reply", return_value=True) as mock_reply,
            patch.object(git.client, "head_sha", return_value="head123"),
        ):
            pr.thread_replies.post_deferred_replies(
                deferred, threads_by_id, "owner/repo", 42,
                "ENG-456", "https://linear.app/team/issue/ENG-456",
                tmp_path, self.HOST,
            )
        body = mock_reply.call_args[0][3]
        assert f"https://{self.HOST}/owner/repo/blob/head123/src/app.py#L3" in body

    def test_an_unattributed_fix_keeps_the_host_through_the_split(self, tmp_path):
        """`reply_to_fixed` routes uncitable rows to a second function.

        The host has to survive the hand-off, which is the one place in this
        path where a caller passes it on rather than using it.
        """
        # No `commit_sha` and a failed push: nothing to attribute, so the split
        # routes this row to the already-addressed body under `acted=True`.
        # That path names no commit by design — a commit predating the comment
        # is not the one that carried the fix — so the blob link is what the
        # host has to reach.
        fixed = [CommentItem(id="t1", summary="use helper", file="src/app.py",
                             line=3, read_sha="head123")]
        threads_by_id = {"t1": ReportThread(id="t1", comments=[{"databaseId": 111}])}
        cp = pr.attribution.CommitPushResult("", CommitStatus.PUSH_FAILED, "")
        with (
            patch("pr.comments.post_thread_reply", return_value=True) as mock_reply,
            patch.object(git.client, "head_sha", return_value="head123"),
            patch.object(pr.attribution, "find_addressing_commit", return_value=None),
        ):
            pr.thread_replies.reply_to_fixed(
                fixed, threads_by_id, "owner/repo", 42, cp, tmp_path, self.HOST,
            )
        body = mock_reply.call_args[0][3]
        assert f"https://{self.HOST}/owner/repo/blob/head123/src/app.py#L3" in body
        assert "github.com" not in body


class TestOurLastReplyId:
    """Edit-vs-post turns on who spoke last, not on the thread's lifecycle."""

    def test_our_unanswered_reply_is_editable(self):
        assert pr.thread_replies.our_last_reply_id(_standing_reply_thread()) == 222

    def test_resolution_does_not_retire_our_standing_reply(self):
        """--finish --post resolves what it replies to, and RESOLVED outranks
        ADDRESSED — reading the state the other way stacks a second comment."""
        thread = _standing_reply_thread(state=ThreadState.RESOLVED, is_resolved=True)
        assert pr.thread_replies.our_last_reply_id(thread) == 222

    def test_none_once_a_reviewer_has_answered(self):
        thread = _standing_reply_thread(state=ThreadState.CONTESTED)
        thread.comments.append(
            {"databaseId": 333, "body": "not what I meant", "author": {"login": "kgn"}},
        )
        assert pr.thread_replies.our_last_reply_id(thread) is None

    def test_none_for_a_lone_root_comment(self):
        thread = ReportThread(id="t1", my_login="me", state=ThreadState.ADDRESSED,
                              comments=[{"databaseId": 111, "author": {"login": "me"}}])
        assert pr.thread_replies.our_last_reply_id(thread) is None

    def test_none_without_a_viewer_login(self):
        """An unknown viewer cannot claim authorship of anything."""
        thread = _standing_reply_thread(my_login="")
        assert pr.thread_replies.our_last_reply_id(thread) is None

    def test_none_for_a_thread_that_is_not_there(self):
        assert pr.thread_replies.our_last_reply_id(None) is None


class TestReplyUpsert:

    def test_edits_our_standing_reply(self, tmp_path):
        dismissed = [_dismissed()]
        threads_by_id = {"t1": _standing_reply_thread(
            body="Suggestion reviewed and determined to be inapplicable: old reason",
        )}
        with patch("pr.comments.post_thread_reply") as post, \
             patch("pr.comments.patch_thread_reply", return_value=True) as edit:
            count = pr.thread_replies.post_dismissed_replies(
                dismissed, threads_by_id, "owner/repo", 42, tmp_path,
            )
        assert count == 1
        post.assert_not_called()
        assert edit.call_args[0][1] == 222
        assert "reason" in edit.call_args[0][2]

    def test_posts_when_reviewer_replied_after_us(self, tmp_path):
        """Editing under a reviewer's reply would rewrite what they answered."""
        dismissed = [_dismissed()]
        threads_by_id = {
            "t1": ReportThread(id="t1", my_login="me",
                               state=ThreadState.CONTESTED, comments=[
                {"databaseId": 111, "body": "reviewer's point",
                 "author": {"login": "kgn"}},
                {"databaseId": 222, "body": "Applied: old take",
                 "author": {"login": "me"}},
                {"databaseId": 333, "body": "that is not what I meant",
                 "author": {"login": "kgn"}},
            ]),
        }
        with patch("pr.comments.post_thread_reply", return_value=True) as post, \
             patch("pr.comments.patch_thread_reply") as edit:
            count = pr.thread_replies.post_dismissed_replies(
                dismissed, threads_by_id, "owner/repo", 42, tmp_path,
            )
        assert count == 1
        edit.assert_not_called()
        assert post.call_args[0][2] == 111

    def test_posts_when_we_never_replied(self, tmp_path):
        dismissed = [_dismissed()]
        threads_by_id = {
            "t1": ReportThread(id="t1", state=ThreadState.NEW,
                               comments=[{"databaseId": 111}]),
        }
        with patch("pr.comments.post_thread_reply", return_value=True) as post, \
             patch("pr.comments.patch_thread_reply") as edit:
            count = pr.thread_replies.post_dismissed_replies(
                dismissed, threads_by_id, "owner/repo", 42, tmp_path,
            )
        assert count == 1
        edit.assert_not_called()
        assert post.call_args[0][2] == 111

    def test_never_edits_a_lone_root_comment(self, tmp_path):
        """On a self-review the root is ours; editing it rewrites the review point."""
        dismissed = [_dismissed()]
        threads_by_id = {
            "t1": ReportThread(id="t1", state=ThreadState.ADDRESSED, my_login="me",
                               comments=[{"databaseId": 111, "body": "my own note",
                                          "author": {"login": "me"}}]),
        }
        with patch("pr.comments.post_thread_reply", return_value=True) as post, \
             patch("pr.comments.patch_thread_reply") as edit:
            count = pr.thread_replies.post_dismissed_replies(
                dismissed, threads_by_id, "owner/repo", 42, tmp_path,
            )
        assert count == 1
        edit.assert_not_called()
        assert post.call_args[0][2] == 111

    @pytest.mark.parametrize("standing,failing", [
        (True, "patch_thread_reply"),
        (False, "post_thread_reply"),
    ])
    def test_a_failed_call_is_not_counted(self, tmp_path, standing, failing):
        """replies_posted feeds the run summary, so a silent failure would inflate it."""
        dismissed = [_dismissed()]
        thread = _standing_reply_thread() if standing else ReportThread(
            id="t1", my_login="me",
            comments=[{"databaseId": 111, "author": {"login": "kgn"}}],
        )
        with patch("pr.comments.post_thread_reply", return_value=True), \
             patch("pr.comments.patch_thread_reply", return_value=True), \
             patch(f"pr.comments.{failing}", return_value=False):
            count = pr.thread_replies.post_dismissed_replies(
                dismissed, {"t1": thread}, "owner/repo", 42, tmp_path,
            )
        assert count == 0

    def test_a_fix_replaces_an_earlier_dismissal(self):
        """Round one dismissed the thread, round two fixed it.

        Guarding per verdict left both replies standing, telling the reviewer
        their point did not apply and that we had acted on it.
        """
        fixed = [CommentItem(id="t1", summary="fix it", file="src/app.py",
                             commit_sha="def5678")]
        threads_by_id = {"t1": _standing_reply_thread(
            body="Suggestion reviewed and determined to be inapplicable: old reason",
        )}
        with patch("pr.comments.post_thread_reply") as post, \
             patch("pr.comments.patch_thread_reply", return_value=True) as edit:
            count = pr.thread_replies.post_fix_replies(
                fixed, threads_by_id, "owner/repo", 42,
                pr.attribution.CommitPushResult("def5678", "pushed", ""),
            )
        assert count == 1
        post.assert_not_called()
        assert edit.call_args[0][1] == 222
        assert edit.call_args[0][2].startswith("Applied: fix it")

    def test_mixed_edit_and_post(self, tmp_path):
        dismissed = [
            CommentItem(id="t1", summary="revised take", reasoning="new reason"),
            CommentItem(id="t2", summary="new one", reasoning="reason"),
        ]
        threads_by_id = {
            "t1": _standing_reply_thread(),
            "t2": ReportThread(id="t2", state=ThreadState.NEW,
                               comments=[{"databaseId": 333}]),
        }
        with patch("pr.comments.post_thread_reply", return_value=True) as post, \
             patch("pr.comments.patch_thread_reply", return_value=True) as edit:
            count = pr.thread_replies.post_dismissed_replies(
                dismissed, threads_by_id, "owner/repo", 42, tmp_path,
            )
        assert count == 2
        assert edit.call_args[0][1] == 222
        assert post.call_args[0][2] == 333


# ── reply evidence ───────────────────────────────────────────────────────


class TestReplyEvidence:

    def test_fix_reply_links_the_file_at_the_fix_commit(self):
        fixed = [CommentItem(id="t1", summary="fix it", file="src/app.py",
                             commit_sha="def5678")]
        threads_by_id = {"t1": ReportThread(id="t1", comments=[{"databaseId": 111}])}
        with patch("pr.comments.post_thread_reply", return_value=True) as post:
            pr.thread_replies.post_fix_replies(fixed, threads_by_id, "owner/repo", 42,
                                 pr.attribution.CommitPushResult("def5678", "pushed", ""))
        body = post.call_args[0][3]
        assert "owner/repo/blob/def5678/src/app.py" in body
        # No line anchor: the fix just moved the lines around it.
        assert "#L" not in body

    def test_deferred_reply_links_the_unchanged_code(self, tmp_path):
        deferred = [CommentItem(id="t1", summary="fix it", file="src/app.py", line=12,
                                read_sha="cafe123")]
        threads_by_id = {"t1": ReportThread(id="t1", comments=[{"databaseId": 111}])}
        with patch("pr.comments.post_thread_reply", return_value=True) as post, \
             patch.object(git.client, "head_sha", return_value="cafe123"):
            pr.thread_replies.post_deferred_replies(
                deferred, threads_by_id, "owner/repo", 42,
                "ENG-456", "https://linear.app/team/issue/ENG-456", tmp_path,
            )
        body = post.call_args[0][3]
        assert "ENG-456" in body
        assert "owner/repo/blob/cafe123/src/app.py#L12" in body

    def test_code_link_prefers_a_citation_that_resolves(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text("a\nb\nc\n")
        entry = CommentItem(id="t1", file="other.py", line=1,
                            evidence_file="src/app.py", evidence_line=2,
                            read_sha="cafe123")
        link = pr.permalinks.code_link(entry, "owner/repo", "cafe123", tmp_path)
        assert "blob/cafe123/src/app.py#L2" in link

    def test_code_link_falls_back_when_the_citation_is_not_in_the_tree(self, tmp_path):
        entry = CommentItem(id="t1", file="other.py", line=7,
                            evidence_file="src/gone.py", evidence_line=2,
                            read_sha="cafe123")
        link = pr.permalinks.code_link(entry, "owner/repo", "cafe123", tmp_path)
        assert "blob/cafe123/other.py#L7" in link

    def test_code_link_drops_an_anchor_it_cannot_vouch_for(self, tmp_path):
        """A line with no recorded tree is a number, not a location."""
        entry = CommentItem(id="t1", file="other.py", line=7)
        link = pr.permalinks.code_link(entry, "owner/repo", "cafe123", tmp_path)
        assert link == "[`other.py`](https://github.com/owner/repo/blob/cafe123/other.py)"

    def test_code_link_is_empty_with_nothing_to_point_at(self, tmp_path):
        assert pr.permalinks.code_link(CommentItem(id="t1"), "owner/repo", "cafe123", tmp_path) == ""
        assert pr.permalinks.code_link(
            CommentItem(id="t1", file="a.py", line=1), "owner/repo", "", tmp_path,
        ) == ""
