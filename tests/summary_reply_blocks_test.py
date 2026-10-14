"""pr.summary_replies: review-body reply blocks, rendered, scoped and published.

A reply to a review body is published inside the summary comment, because
GitHub has no reply API for one. These cover the section the renderer writes,
and what `publish_summary` keeps of the blocks already published: the same
protections a row gets — an edit never drops a block it alone holds, a hand edit
survives, a block another checkout wrote is carried forward.
"""

import functools
import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

# `_no_published_summary` is autouse: imported so pytest applies it here,
# never referenced by name.
from review_threads_support import (  # noqa: E402
    _fix, _lookup_returns, _make_state, _no_published_summary, _published_summary, content,
)
import core.log
from git.land import CommitStatus
import pr.attribution
import pr.comments
import pr.published_record
import pr.summary_publish
import pr.summary_render
import pr.summary_replies
import pr.summary_rounds
from pr.fix import FixOutcome, ItemOutcome
from pr.thread_models import CommentItem, PRReport

_REPO = "owner/repo"
_LINK = "https://github.com/owner/repo/pull/1#pullrequestreview-"
_REVIEWS = [
    {"id": 88, "user": "kgn", "submitted_at": "2026-01-02T00:00:00Z"},
    {"id": 90, "user": "amp", "submitted_at": "2026-01-01T00:00:00Z"},
]
_CP = pr.attribution.CommitPushResult(sha="", status=CommitStatus.NO_CHANGES, error="")


def _text(review_id, user, reply):
    head = f"**@{user}** · " if user else ""
    return f"{head}[review]({_LINK}{review_id})\n\n{reply}"


def _block(review_id="88", user="kgn", reply="Done."):
    return pr.published_record.mark_reply_block(review_id, _text(review_id, user, reply))


def _render(round_content, **kw):
    return pr.summary_render.build_summary_body(
        round_content, _CP, _REPO, 1, {}, has_comment_items=True, wt_path=None, **kw)


def _marker(body, cid=11, newest_other_at=""):
    return pr.comments.MarkerComment(
        found=True, comment_id=cid, body=body, created_at="2026-01-03T00:00:00Z",
        url=f"https://github.com/owner/repo/pull/1#issuecomment-{cid}",
        newest_other_at=newest_other_at)


def _publish(round_content, *markers, force=frozenset()):
    """Publish `round_content` over `markers`; returns the body that was posted."""
    with _lookup_returns(*markers), \
            patch.object(pr.comments, "post_issue_comment", return_value="url") as post:
        pr.summary_publish.publish_summary(
            _REPO, 1, functools.partial(
                pr.summary_render.build_summary_body, round_content, _CP, _REPO, 1, {},
                has_comment_items=True, wt_path=None),
            reply_keys=pr.summary_replies.reply_block_keys(round_content),
            force_reply_keys=force)
    return post.call_args[0][2]


class TestTheRepliesSection:
    """Where the blocks go, what each says, and when the section is absent."""

    def test_a_reply_renders_as_a_marked_block_under_its_heading(self, content):
        body = _render(content(source_replies={"rb-88": "Done."},
                               review_body_comments=_REVIEWS))
        assert f"### Replies to review comments\n\n{_block()}\n" in body

    def test_the_section_sits_above_the_round_chain(self, content):
        body = _render(content(source_replies={"rb-88": "Done."}),
                       chain=[pr.summary_rounds.SummaryRound(1, "https://u/1")])
        assert body.index("### Replies to review comments") < body.index("**Earlier rounds:**")

    def test_no_reply_renders_no_section(self, content):
        assert "Replies to review comments" not in _render(
            content(review_body_comments=_REVIEWS))

    def test_blocks_follow_the_order_the_reviews_were_submitted(self, content):
        body = _render(content(source_replies={"rb-88": "A.", "rb-90": "B.", "rb-7": "C."},
                               review_body_comments=_REVIEWS))
        assert body.index("k=90 ") < body.index("k=88 ") < body.index("k=7 ")

    def test_a_review_outside_the_listing_names_the_reviewer_its_items_carry(self, content):
        item = CommentItem(id="rb-77-1", summary="x", reviewer="zed")
        body = _render(content(source_replies={"rb-77": "Done."}, dismissed=[item]))
        assert _block("77", "zed") in body

    def test_an_unknown_reviewer_is_left_out_rather_than_guessed(self, content):
        body = _render(content(source_replies={"rb-77": "Done."}))
        assert _block("77", "") in body
        assert "**@" not in body

    def test_a_replies_only_summary_carries_no_empty_table_heading(self, content):
        """`--reply` before any fix pass: no row, so no heading over nothing."""
        body = _render(content(source_replies={"rb-88": "Done."}))
        assert "## Review Comments Addressed" not in body
        assert body.startswith(
            f"{pr.summary_render.SUMMARY_MARKER}\n{pr.published_record.FORMAT_MARKER}\n")

    def test_a_round_with_rows_keeps_its_heading_beside_the_replies(self, content):
        item = CommentItem(id="rb-88-1", summary="x", reviewer="kgn")
        body = _render(content(source_replies={"rb-88": "Done."}, dismissed=[item]))
        assert body.index("## Review Comments Addressed") < body.index(
            "### Replies to review comments")

    def test_a_kept_block_is_re_emitted_verbatim(self, content):
        kept = _block(reply="Done, by hand.").replace("d=", "d=0")
        body = _render(content(source_replies={"rb-88": "Done."},
                               review_body_comments=_REVIEWS), kept_replies={"88": kept})
        assert kept in body
        assert _block() not in body


class TestWhatAnEditKeeps:
    """An in-place edit rewrites its target: no block it alone holds is lost."""

    def test_an_in_place_edit_keeps_the_targets_block(self, content):
        round_content = content(source_replies={"rb-88": "Done."},
                                review_body_comments=_REVIEWS)
        body = _publish(round_content, _marker(_published_summary() + "\n" + _block()))
        assert _block() in body

    def test_a_fresh_comment_leaves_an_unchanged_block_where_it_was(self, content):
        round_content = content(source_replies={"rb-88": "Done."},
                                review_body_comments=_REVIEWS)
        answered = _marker(_published_summary() + "\n" + _block(),
                           newest_other_at="2026-01-04T00:00:00Z")
        assert "pr-comments:review-reply" not in _publish(round_content, answered)

    def test_a_fresh_comment_carries_a_revised_reply(self, content):
        round_content = content(source_replies={"rb-88": "Done, and tested."},
                                review_body_comments=_REVIEWS)
        answered = _marker(_published_summary() + "\n" + _block(),
                           newest_other_at="2026-01-04T00:00:00Z")
        assert _block(reply="Done, and tested.") in _publish(round_content, answered)

    def test_a_hand_edited_block_is_kept_and_named(self, content):
        edited = _block().replace("Done.", "Done — see the follow-up.")
        round_content = content(source_replies={"rb-88": "Done."},
                                review_body_comments=_REVIEWS)
        with patch.object(core.log, "warn") as warn:
            body = _publish(round_content, _marker(_published_summary() + "\n" + edited))
        assert edited in body
        assert _block() not in body
        assert any("@kgn's review" in c[0][0] for c in warn.call_args_list)

    def test_answering_the_review_again_replaces_the_hand_edit(self, content):
        edited = _block().replace("Done.", "Done — see the follow-up.")
        round_content = content(source_replies={"rb-88": "Revised."},
                                review_body_comments=_REVIEWS)
        body = _publish(round_content, _marker(_published_summary() + "\n" + edited),
                        force=frozenset({"88"}))
        assert _block(reply="Revised.") in body
        assert edited not in body

    def test_a_block_another_checkout_wrote_is_carried_forward(self, content):
        theirs = _block("90", "amp", "Answered from the other laptop.")
        round_content = content(source_replies={"rb-88": "Done."},
                                review_body_comments=_REVIEWS)
        with patch.object(core.log, "warn") as warn:
            body = _publish(round_content, _marker(_published_summary() + "\n" + theirs))
        assert theirs in body
        assert _block() in body
        assert any("carrying them forward" in c[0][0] for c in warn.call_args_list)

    def test_a_block_an_earlier_comment_also_holds_is_not_carried(self, content):
        theirs = _block("90", "amp", "Answered from the other laptop.")
        body = _publish(content(), _marker(_published_summary() + "\n" + theirs, cid=10),
                        _marker(_published_summary() + "\n" + theirs, cid=11))
        assert theirs not in body


def _dismissed(item_id, summary, reason, reviewer="kgn"):
    return CommentItem(id=item_id, summary=summary, reason=reason, reviewer=reviewer)


class TestDismissalReasons:
    """A dismissed review-body item's reason reaches the reviewer in its block."""

    def test_each_reason_renders_under_the_reply_in_item_order(self, content):
        items = [_dismissed("rb-88-2", "Rename the flag\nbecause...", "It is public API"),
                 _dismissed("rb-88-1", "Drop the cache", "The cache is the fix")]
        body = _render(content(source_replies={"rb-88": "Done."},
                               review_body_comments=_REVIEWS, dismissed=items))
        reply = ("Done.\n\nDismissed: Drop the cache — The cache is the fix\n"
                 "Dismissed: Rename the flag — It is public API")
        assert _block(reply=reply) in body

    def test_a_dismissal_without_a_reason_adds_no_line(self, content):
        body = _render(content(source_replies={"rb-88": "Done."},
                               review_body_comments=_REVIEWS,
                               dismissed=[_dismissed("rb-88-1", "Drop the cache", "")]))
        assert _block() in body
        assert "Dismissed:" not in body

    def test_only_a_review_body_items_reason_reaches_a_block(self, content):
        items = [_dismissed("PRRT_1", "Inline nit", "Not ours"),
                 _dismissed("ic-5-1", "Issue comment", "Not ours"),
                 _dismissed("rb-90-1", "Other review", "Out of scope", "amp")]
        body = _render(content(source_replies={"rb-88": "Done."},
                               review_body_comments=_REVIEWS, dismissed=items))
        assert _block() in body
        assert _block("90", "amp", "Dismissed: Other review — Out of scope") in body
        assert "Dismissed: Inline nit" not in body
        assert "Dismissed: Issue comment" not in body

    def test_a_dismissal_alone_renders_a_block_with_no_reply(self, content):
        round_content = content(review_body_comments=_REVIEWS, dismissed=[
            _dismissed("rb-88-1", "Drop the cache", "The cache is the fix")])
        alone = _block(reply="Dismissed: Drop the cache — The cache is the fix")
        assert pr.summary_replies.reply_block_keys(round_content) == {"88"}
        assert alone in _render(round_content)

    def test_an_in_place_edit_does_not_carry_a_dismissal_block_twice(self, content):
        """Guards duplication only: the block is written once, not also carried.

        Rendering itself is `test_a_dismissal_alone_renders_a_block_with_no_reply`'s.
        """
        round_content = content(review_body_comments=_REVIEWS, dismissed=[
            _dismissed("rb-88-1", "Drop the cache", "The cache is the fix")])
        alone = _block(reply="Dismissed: Drop the cache — The cache is the fix")
        body = _publish(round_content, _marker(_published_summary() + "\n" + alone))
        assert body.count("pr-comments:review-reply k=88 ") == 1
        assert alone in body

    def test_a_live_edit_writes_an_earlier_rounds_block_rather_than_carrying_it(
        self, content,
    ):
        """The record, not this round's buckets, holds the dismissal: still ours."""
        round_content = content(review_body_comments=_REVIEWS, recorded=[ItemOutcome(
            id="rb-88-1", summary="Drop the cache", outcome=FixOutcome.DISMISSED,
            reason="The cache is the fix")])
        alone = _block(reply="Dismissed: Drop the cache — The cache is the fix")
        with patch.object(core.log, "warn") as warn:
            body = _publish(round_content, _marker(_published_summary() + "\n" + alone))
        assert body.count("pr-comments:review-reply k=88 ") == 1
        assert alone in body
        assert not any("carrying them forward" in c[0][0] for c in warn.call_args_list)

    def test_a_new_dismissal_is_restated_on_a_fresh_comment(self, content):
        round_content = content(source_replies={"rb-88": "Done."},
                                review_body_comments=_REVIEWS, dismissed=[
                                    _dismissed("rb-88-1", "Drop the cache", "Needed")])
        answered = _marker(_published_summary() + "\n" + _block(),
                           newest_other_at="2026-01-04T00:00:00Z")
        restated = _block(reply="Done.\n\nDismissed: Drop the cache — Needed")
        assert restated in _publish(round_content, answered)

    def test_a_triage_dismissal_renders_its_reasoning_live_and_on_replay(self, content):
        """Triage writes its justification to `reasoning`; `reason` stays empty.

        The record persists it as the reason (`to_outcome`), so the live round
        and a replay of it must render the same block, not one each.
        """
        live = CommentItem(id="rb-88-1", summary="Drop the cache", reviewer="kgn",
                           reasoning="The cache is the fix")
        replayed = CommentItem.from_outcome(live.to_outcome(FixOutcome.DISMISSED), "kgn")
        texts = [pr.summary_replies.reply_block_text(
            content(review_body_comments=_REVIEWS, dismissed=[entry]), "88", _REPO, 1)
            for entry in (live, replayed)]
        assert texts[0] == texts[1]
        alone = _block(reply="Dismissed: Drop the cache — The cache is the fix")
        assert alone in _render(content(review_body_comments=_REVIEWS, dismissed=[live]))


class TestAWithdrawnDismissal:
    """A dismissal re-settled away is dropped from the comment that published it."""

    def _replay(self, *markers):
        fix = _fix(items=[ItemOutcome(id="rb-88-1", summary="Drop the cache",
                                      outcome=FixOutcome.FIXED)],
                   commit_status="no_changes", summary_deferred=True)
        with _lookup_returns(*markers), \
                patch.object(pr.comments, "post_issue_comment", return_value="url") as post:
            pr.summary_publish.render_deferred_summary(
                _make_state(fix), PRReport(review_body_comments=_REVIEWS), _REPO, 1, {})
        return post.call_args[0][2]

    def test_an_in_place_finish_edit_drops_the_withdrawn_block_and_says_so(self):
        stale = _block(reply="Dismissed: Drop the cache — The cache is the fix")
        with patch.object(core.log, "info") as info:
            body = self._replay(_marker(_published_summary() + "\n" + stale))
        assert "pr-comments:review-reply k=88 " not in body
        assert any("withdrawn" in c[0][0] and "@kgn's review" in c[0][0]
                   for c in info.call_args_list)

    def test_a_reply_another_checkout_wrote_to_a_known_review_is_carried(self):
        """`source_replies` never shrinks, so reply text this one lacks is not ours."""
        theirs = _block(reply="Answered from the other laptop.")
        body = self._replay(_marker(_published_summary() + "\n" + theirs))
        assert theirs in body

    def test_a_block_for_a_review_this_checkout_does_not_know_is_still_carried(self):
        theirs = _block("90", "amp", "Answered from the other laptop.")
        body = self._replay(_marker(_published_summary() + "\n" + theirs))
        assert theirs in body
