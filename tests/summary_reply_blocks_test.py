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
    _lookup_returns, _no_published_summary, _published_summary, content,
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
from pr.thread_models import CommentItem

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
