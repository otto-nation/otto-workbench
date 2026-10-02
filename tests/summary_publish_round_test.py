"""pr.summary_publish: what each round's comment carries, edits and reposts."""

import dataclasses
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
    HAND_EDITED_ROW, ROUND_ONE_ROW, _GENERATED_ACTION_CELL, _HAND_WRITTEN_ACTION_CELL,
    _SIBLING_ITEMS, _fix, _lookup_returns, _make_state, _no_published_summary, _published,
    _published_summary, content,
)
import core.log
from git.land import CommitStatus
import pr.comments
import pr.attribution
import pr.summary_model
import pr.summary_publish
import pr.summary_render
import pr.summary_scope
import pr.summary_row
from pr.fix import FixOutcome, ItemOutcome
from pr.thread_models import CommentItem, PRReport, ReportThread


_ROUND_ONE_ITEM_CELL = "Fixed in [`aaaaaaa`](https://github.com/owner/repo/commit/aaaaaaa)"


def _sibling_rows(hand_written: str = "") -> list[str]:
    """The three item rows as an earlier round published them.

    ``hand_written`` names the item whose Action cell a person rewrote after
    that round posted.
    """
    return [
        pr.summary_row.render_row(pr.summary_row.row_cells_for(
            item,
            _HAND_WRITTEN_ACTION_CELL if item.id == hand_written
            else _ROUND_ONE_ITEM_CELL,
            {}, "owner/repo", 1, "aaaaaaa",
        ))
        for item in _SIBLING_ITEMS
    ]


# ── reposting a summary the PR has moved past ──────────────────────────────


_SUMMARY_POSTED_AT = "2026-01-02T00:00:00Z"


_AFTER_THE_SUMMARY = "2026-01-03T00:00:00Z"


_BEFORE_THE_SUMMARY = "2026-01-01T00:00:00Z"


_SUMMARY_EDITED_AT = "2026-01-04T00:00:00Z"


_AFTER_THE_EDIT = "2026-01-05T00:00:00Z"


_ROUND_ONE_URL = "https://github.com/owner/repo/pull/1#issuecomment-11"


def _round_one_marker(*rows: str, **overrides):
    """The summary a first round published, spoken over since it went up."""
    import pr.comments
    defaults = dict(
        found=True, comment_id=11, body=_published_summary(*rows),
        created_at=_SUMMARY_POSTED_AT, newest_other_at=_AFTER_THE_SUMMARY,
        url=_ROUND_ONE_URL,
    )
    defaults.update(overrides)
    return pr.comments.MarkerComment(**defaults)


_ROUND_TWO_OUTCOME = ItemOutcome(
    id="t2", summary="round two work", file="new.go", line=1,
    outcome=FixOutcome.FIXED,
)


# What round one recorded, as a state file that still carries it into round two.
_ROUND_ONE_OUTCOME = ItemOutcome(
    id="t1", summary="drop the guard", file="old.go", line=4,
    outcome=FixOutcome.FIXED,
)


def _repost_over(*rows: str, outcomes=(), threads=None, report=None,
                 marker=None):
    """Render a second round's summary over a first round that was answered.

    Returns the body posted. `rows` is what the first round published, and
    `outcomes` what local state still holds beside round two's own fixed `t2`.
    `marker` overrides fields on that first round's comment.
    """
    items = [_ROUND_TWO_OUTCOME, *outcomes]
    state = _make_state(_fix(
        items=items, reviewers={o.id: "kgn" for o in items},
        commit_status="no_changes", summary_deferred=True,
    ))
    with _lookup_returns(_round_one_marker(*rows, **(marker or {}))), \
            patch("pr.comments.post_issue_comment", return_value="https://url") as post:
        pr.summary_publish.render_deferred_summary(
            state, report or PRReport(), "owner/repo", 1, threads or {})
    assert "marker" not in post.call_args.kwargs
    return post.call_args[0][2]


def _reviewed_thread(created_at, login="kgn", thread_id="t1"):
    """A review thread whose only comment `login` left at `created_at`."""
    return {thread_id: ReportThread(
        id=thread_id, my_login="me",
        comments=[{"databaseId": 111, "author": {"login": login},
                   "createdAt": created_at}],
    )}


_OPEN_OUTCOME = dataclasses.replace(
    _ROUND_ONE_OUTCOME, outcome=FixOutcome.NEEDS_HUMAN, reason="conflicting")


def _published_open_row() -> str:
    """`ROUND_ONE_ROW` as the round that left the thread open published it.

    Built from `HumanReason` rather than transcribed, so the cell this round
    renders and the one the record holds cannot drift apart — the whole of what
    tells "still open, and quiet" from "re-classified this round".
    """
    return ROUND_ONE_ROW.replace(
        _GENERATED_ACTION_CELL, pr.summary_model.HumanReason.prose_for(_OPEN_OUTCOME.reason))


def _edit_over_chain(earlier_rows, target_rows, outcomes=(), threads=None):
    """Edit the newest of two summary comments, with `earlier_rows` below it.

    The target postdates the first comment and nothing has been said under it,
    so the round edits in place — the path where dropping a row the target
    alone holds would delete it from the record rather than defer to a link.
    """
    import pr.comments
    earlier = _round_one_marker(*earlier_rows, newest_other_at=_BEFORE_THE_SUMMARY)
    target = pr.comments.MarkerComment(
        True, 12, _published_summary(*target_rows),
        created_at=_AFTER_THE_SUMMARY, newest_other_at=_BEFORE_THE_SUMMARY,
        url="https://github.com/owner/repo/pull/1#issuecomment-12",
    )
    state = _make_state(_fix(
        items=[*outcomes], commit_status="no_changes", summary_deferred=True))
    with _lookup_returns(earlier, target), \
            patch("pr.comments.post_issue_comment", return_value="https://url") as post:
        pr.summary_publish.render_deferred_summary(state, PRReport(), "owner/repo", 1, threads or {})
    assert post.call_args.kwargs["marker"] == pr.summary_render.SUMMARY_MARKER
    return post.call_args[0][2]


# ── summary upsert ─────────────────────────────────────────────────────────


class TestSummaryMarker:
    """Each review round must edit one summary comment, not append a new one."""

    def test_body_carries_marker(self, content):
        cp = pr.attribution.CommitPushResult(None, "no_changes", "")
        body = pr.summary_render.build_summary_body(
            content(fixed=[CommentItem(id="t1", summary="fix", file="a.py", line=1)]),
            cp, "owner/repo", 1, {},
        )
        assert body.startswith(pr.summary_render.SUMMARY_MARKER)

    def test_post_fix_summary_passes_marker(self, content):
        cp = pr.attribution.CommitPushResult("abc1234", "pushed", "")
        with patch("pr.comments.post_issue_comment", return_value="https://url") as mock_post:
            pr.summary_publish.post_fix_summary(
                content(fixed=[
                    CommentItem(id="t1", summary="fix", file="a.py", line=1),
                ]),
                cp, "owner/repo", 1, {},
            )
        assert mock_post.call_args.kwargs["marker"] == pr.summary_render.SUMMARY_MARKER

    def test_deferred_summary_passes_marker(self):
        fix = _fix(
            items=[ItemOutcome(id="t1", summary="fix", file="a.py", line=1,
                                   outcome=FixOutcome.FIXED)],
            commit_status="no_changes", summary_deferred=True,
        )
        state = _make_state(fix)
        with patch("pr.comments.post_issue_comment", return_value="https://url") as mock_post:
            pr.summary_publish.render_deferred_summary(state, PRReport(), "owner/repo", 1, {})
        assert mock_post.call_args.kwargs["marker"] == pr.summary_render.SUMMARY_MARKER


class TestPublishedRowsSurviveTheEdit:
    """State is per-worktree; the comment is the record of rounds it never saw."""

    def _state_fix(self, **overrides):
        defaults = dict(
            items=[ItemOutcome(id="t2", summary="round two work", file="new.go",
                               line=1, outcome=FixOutcome.FIXED)],
            commit_status="no_changes", summary_deferred=True,
        )
        defaults.update(overrides)
        return _fix(**defaults)

    def _render(self, published):
        state = _make_state(self._state_fix())
        with _published(published), \
                patch("pr.comments.post_issue_comment", return_value="https://url") as post:
            pr.summary_publish.render_deferred_summary(state, PRReport(), "owner/repo", 1, {})
        return post.call_args[0][2]

    def test_the_earlier_round_survives_finish(self):
        body = self._render(_published_summary(ROUND_ONE_ROW))
        assert "drop the guard" in body
        assert "round two work" in body

    def test_the_carried_row_is_counted_and_explained(self):
        body = self._render(_published_summary(ROUND_ONE_ROW))
        assert "1 carried over" in body
        assert "state file does not cover" in body

    def test_carrying_forward_is_idempotent(self):
        once = self._render(_published_summary(ROUND_ONE_ROW))
        twice = self._render(once)
        assert twice == once

    def test_a_run_that_warns_says_how_many(self):
        with patch.object(core.log, "warn") as warn:
            self._render(_published_summary(ROUND_ONE_ROW))
        assert "1 row(s)" in warn.call_args[0][0]

    def test_a_failed_lookup_invents_no_rows(self):
        """An unreadable listing must not be read as an empty published comment."""
        import pr.comments
        state = _make_state(self._state_fix())
        with patch.object(pr.comments, "find_marker_comments",
                          return_value=pr.comments.MarkerHistory(found=False)), \
                patch("pr.comments.post_issue_comment", return_value="https://url") as post:
            pr.summary_publish.render_deferred_summary(state, PRReport(), "owner/repo", 1, {})
        assert "carried over" not in post.call_args[0][2]

    def test_the_fix_pass_upsert_carries_too(self, content):
        """--fix edits the same comment, so it can shrink it the same way."""
        cp = pr.attribution.CommitPushResult("bbbbbbb", "pushed", "")
        with _published(_published_summary(ROUND_ONE_ROW)), \
                patch("pr.comments.post_issue_comment", return_value="https://url") as post:
            pr.summary_publish.post_fix_summary(
                content(fixed=[
                    CommentItem(id="t2", summary="round two work", file="new.go",
                                line=1),
                ]),
                cp, "owner/repo", 1, {},
            )
        body = post.call_args[0][2]
        assert "drop the guard" in body
        assert "1 carried over" in body

    def test_the_lookup_is_not_repeated_for_the_write(self):
        import pr.comments
        state = _make_state(self._state_fix())
        with _published(_published_summary(ROUND_ONE_ROW)) as find, \
                patch("pr.comments.post_issue_comment", return_value="https://url") as post:
            pr.summary_publish.render_deferred_summary(state, PRReport(), "owner/repo", 1, {})
        find.assert_called_once()
        assert post.call_args.kwargs["existing"] == pr.comments.MarkerComment(
            True, 11, _published_summary(ROUND_ONE_ROW),
            url="https://github.com/owner/repo/pull/1#issuecomment-11")


class TestHandEditedCellsSurviveTheRender:
    """State regaining coverage of a thread used to be what destroyed the edit."""

    def _threads(self):
        return {"t1": ReportThread(id="t1", comments=[{"databaseId": 111}])}

    def _state_fix(self, **overrides):
        defaults = dict(
            items=[ItemOutcome(
                id="t1", summary="drop the guard", file="old.go", line=4,
                outcome=FixOutcome.NEEDS_HUMAN, reason="conflicting")],
            commit_status="no_changes", summary_deferred=True,
        )
        defaults.update(overrides)
        return _fix(**defaults)

    def _render(self, published):
        state = _make_state(self._state_fix())
        with _published(published), \
                patch("pr.comments.post_issue_comment", return_value="https://url") as post:
            pr.summary_publish.render_deferred_summary(state, PRReport(), "owner/repo", 1, self._threads())
        return post.call_args[0][2]

    def test_the_hand_written_cell_is_republished(self):
        body = self._render(_published_summary(HAND_EDITED_ROW))
        assert _HAND_WRITTEN_ACTION_CELL in body
        assert "Conflicting reviewer feedback" not in body

    def test_the_row_is_not_duplicated(self):
        body = self._render(_published_summary(HAND_EDITED_ROW))
        assert body.count("drop the guard") == 1

    def test_the_header_count_follows_the_cell(self):
        """A row reading `Superseded` under a header reading `1 need discussion`
        reopens the question the hand edit closed."""
        body = self._render(_published_summary(HAND_EDITED_ROW))
        assert "need discussion" not in body
        assert "1 hand-written" in body

    def test_the_reader_is_told_why_the_row_was_not_re_rendered(self):
        body = self._render(_published_summary(HAND_EDITED_ROW))
        assert "written by hand" in body

    def test_holding_a_row_is_idempotent(self):
        once = self._render(_published_summary(HAND_EDITED_ROW))
        assert self._render(once) == once

    def test_the_run_names_the_row_and_what_it_would_have_said(self):
        """An overwritten hand edit was silent — the warning listed only the
        rows the run kept, never the one it replaced."""
        with patch.object(core.log, "warn") as warn:
            self._render(_published_summary(HAND_EDITED_ROW))
        held = next(c[0][0] for c in warn.call_args_list if "hand-written" in c[0][0])
        assert "#discussion_r111" in held
        assert _HAND_WRITTEN_ACTION_CELL in held
        assert "Conflicting reviewer feedback" in held

    def test_a_generated_cell_is_still_re_rendered(self):
        """Pairs with the cases above — proves those assertions are not vacuous."""
        body = self._render(_published_summary(ROUND_ONE_ROW))
        assert "Conflicting reviewer feedback" in body
        assert "1 need discussion" in body
        assert "hand-written" not in body

    def test_the_fix_pass_upsert_holds_the_cell_too(self, content):
        """--fix edits the same comment, so it can destroy the edit the same way."""
        cp = pr.attribution.CommitPushResult("bbbbbbb", CommitStatus.PUSHED, "")
        with _published(_published_summary(HAND_EDITED_ROW)), \
                patch("pr.comments.post_issue_comment", return_value="https://url") as post:
            pr.summary_publish.post_fix_summary(
                content(fixed=[
                    CommentItem(id="t1", summary="drop the guard", file="old.go",
                                line=4),
                ]),
                cp, "owner/repo", 1, self._threads(),
            )
        body = post.call_args[0][2]
        assert _HAND_WRITTEN_ACTION_CELL in body
        assert "1 hand-written" in body
        assert "1 fixed" not in body


class TestEveryItemReachesTheTable:
    """A held row used to stand in for its siblings, which then vanished."""

    def _render(self, content, published=""):
        cp = pr.attribution.CommitPushResult("bbbbbbb", CommitStatus.PUSHED, "")
        with _published(published), \
                patch("pr.comments.post_issue_comment", return_value="https://url") as post:
            pr.summary_publish.post_fix_summary(
                content(fixed=list(_SIBLING_ITEMS)), cp, "owner/repo", 1, {})
        return post.call_args[0][2]

    def test_three_items_render_three_rows(self, content):
        body = self._render(content, _published_summary(*_sibling_rows()))
        assert len(pr.summary_scope.table_rows(body)) == len(_SIBLING_ITEMS)

    def test_a_held_row_stands_in_for_its_own_row_only(self, content):
        published = _published_summary(*_sibling_rows(hand_written="ic-900-1"))
        body = self._render(content, published)
        assert body.count(_HAND_WRITTEN_ACTION_CELL) == 1
        for item in _SIBLING_ITEMS:
            assert body.count(f"[{item.summary}]") == 1

    def test_the_counts_match_the_rows(self, content):
        published = _published_summary(*_sibling_rows(hand_written="ic-900-1"))
        body = self._render(content, published)
        assert len(pr.summary_scope.table_rows(body)) == len(_SIBLING_ITEMS)
        assert f"**{len(_SIBLING_ITEMS) - 1} fixed**" in body
        assert "1 hand-written" in body

    def test_nothing_published_counts_every_row_as_fixed(self, content):
        body = self._render(content)
        assert len(pr.summary_scope.table_rows(body)) == len(_SIBLING_ITEMS)
        assert f"**{len(_SIBLING_ITEMS)} fixed**" in body
        assert "hand-written" not in body

    def test_holding_one_sibling_is_idempotent(self, content):
        published = _published_summary(*_sibling_rows(hand_written="ic-900-1"))
        once = self._render(content, published)
        assert self._render(content, once) == once

    def test_a_sibling_state_lost_is_carried_rather_than_dropped(self, content):
        """One sibling in the fresh render used to account for all of them."""
        published = _published_summary(*_sibling_rows())
        cp = pr.attribution.CommitPushResult("bbbbbbb", CommitStatus.PUSHED, "")
        with _published(published), \
                patch("pr.comments.post_issue_comment", return_value="https://url") as post:
            pr.summary_publish.post_fix_summary(
                content(fixed=[_SIBLING_ITEMS[0]]), cp, "owner/repo", 1, {})
        body = post.call_args[0][2]
        assert len(pr.summary_scope.table_rows(body)) == 3
        assert "2 carried over" in body


class TestAnsweredSummariesArePostedAgain:
    """An edit notifies nobody, so a summary spoken over is reposted, not patched."""

    def _marker(self, **overrides):
        import pr.comments
        defaults = dict(found=True, comment_id=11, body="",
                        created_at=_SUMMARY_POSTED_AT)
        defaults.update(overrides)
        return pr.comments.MarkerComment(**defaults)

    def _publish(self, marker, activity_at=""):
        with _lookup_returns(marker), \
                patch("pr.comments.post_issue_comment", return_value="https://url") as post:
            pr.summary_publish.publish_summary("owner/repo", 1,
                                lambda carried_over, scope, chain: "body",
                                activity_at=activity_at)
        return post.call_args

    def test_a_round_that_still_has_the_last_word_edits_in_place(self):
        call = self._publish(self._marker())
        assert call.kwargs["marker"] == pr.summary_render.SUMMARY_MARKER

    def test_a_later_issue_comment_forces_a_fresh_one(self):
        call = self._publish(self._marker(newest_other_at=_AFTER_THE_SUMMARY))
        assert "marker" not in call.kwargs

    def test_a_later_review_forces_a_fresh_one(self):
        call = self._publish(self._marker(), activity_at=_AFTER_THE_SUMMARY)
        assert "marker" not in call.kwargs

    def test_activity_from_before_the_summary_changes_nothing(self):
        call = self._publish(
            self._marker(newest_other_at=_BEFORE_THE_SUMMARY),
            activity_at=_BEFORE_THE_SUMMARY,
        )
        assert call.kwargs["marker"] == pr.summary_render.SUMMARY_MARKER

    def test_a_target_with_no_timestamp_is_still_edited(self):
        """Guessing "buried" here would append a duplicate summary every round."""
        call = self._publish(self._marker(created_at=""),
                             activity_at=_AFTER_THE_SUMMARY)
        assert call.kwargs["marker"] == pr.summary_render.SUMMARY_MARKER

    def test_the_fresh_comment_describes_its_own_round(self):
        """The earlier round stays where it was posted, and is linked, not restated."""
        body = _repost_over(ROUND_ONE_ROW)
        assert pr.summary_render.SUMMARY_MARKER in body
        assert "round two work" in body
        assert "drop the guard" not in body
        assert f"**Earlier rounds:** [1]({_ROUND_ONE_URL})" in body


class TestASummaryDescribesItsOwnRound:
    """A repost restating every round the PR ever had is complete and unreadable."""

    def test_a_settled_quiet_thread_is_left_where_it_was_published(self):
        body = _repost_over(
            ROUND_ONE_ROW, outcomes=[_ROUND_ONE_OUTCOME],
            threads=_reviewed_thread(_BEFORE_THE_SUMMARY),
        )
        assert "drop the guard" not in body
        assert "1 thread settled in an earlier round" in body
        assert "**1 fixed**" in body

    def test_a_thread_spoken_on_since_comes_back(self):
        """The point of the scoping is the round's own activity, not silence."""
        body = _repost_over(
            ROUND_ONE_ROW, outcomes=[_ROUND_ONE_OUTCOME],
            threads=_reviewed_thread(_AFTER_THE_SUMMARY),
        )
        assert "drop the guard" in body
        assert "settled in an earlier round" not in body

    def test_a_thread_the_summary_absorbed_by_edit_is_left_where_it_is(self):
        """A marker comment is edited in place round after round, so its body
        carries rows for threads opened long after it was posted. Dating it by
        `created_at` calls every one of them newer than the summary already
        holding it, and the repost becomes the whole edited history again."""
        body = _repost_over(
            ROUND_ONE_ROW, outcomes=[_ROUND_ONE_OUTCOME],
            threads=_reviewed_thread(_AFTER_THE_SUMMARY),
            marker={"updated_at": _SUMMARY_EDITED_AT},
        )
        assert "drop the guard" not in body
        assert "1 thread settled in an earlier round" in body

    def test_a_thread_spoken_on_after_the_edit_still_comes_back(self):
        """The later timestamp narrows the window; it does not close it."""
        body = _repost_over(
            ROUND_ONE_ROW, outcomes=[_ROUND_ONE_OUTCOME],
            threads=_reviewed_thread(_AFTER_THE_EDIT),
            marker={"updated_at": _SUMMARY_EDITED_AT},
        )
        assert "drop the guard" in body
        assert "settled in an earlier round" not in body

    def test_our_own_reply_is_not_activity(self):
        """The fix pass replies before it publishes — counting those never settles."""
        body = _repost_over(
            ROUND_ONE_ROW, outcomes=[_ROUND_ONE_OUTCOME],
            threads=_reviewed_thread(_AFTER_THE_SUMMARY, login="me"),
        )
        assert "drop the guard" not in body

    def test_an_open_thread_quiet_since_is_left_where_it_was_published(self):
        """#1017 — #714 exempted every open thread from the scoping, which at
        forty-three of them rebuilds the document the scoping exists to prevent.
        One row among forty-three is no easier to find than one round back."""
        body = _repost_over(
            _published_open_row(), outcomes=[_OPEN_OUTCOME],
            threads=_reviewed_thread(_BEFORE_THE_SUMMARY),
        )
        assert "drop the guard" not in body
        assert "1 thread still open" in body
        assert "settled in an earlier round" not in body

    def test_an_open_thread_spoken_on_since_comes_back(self):
        """The rule is the round's own activity — open threads get no exemption
        from it, and no different treatment under it."""
        body = _repost_over(
            _published_open_row(), outcomes=[_OPEN_OUTCOME],
            threads=_reviewed_thread(_AFTER_THE_SUMMARY),
        )
        assert "drop the guard" in body
        assert "1 need discussion" in body
        assert "still open" not in body

    def test_a_newly_open_thread_is_written_whatever_else_is_dropped(self):
        """#712 outranks the scoping for a row no comment holds, and an open
        question reaching a reader for the first time is that row."""
        fresh = dataclasses.replace(
            _OPEN_OUTCOME, id="t9", summary="never published")
        body = _repost_over(
            _published_open_row(), outcomes=[_OPEN_OUTCOME, fresh],
            threads=_reviewed_thread(_BEFORE_THE_SUMMARY),
        )
        assert "never published" in body
        assert "drop the guard" not in body
        assert "1 thread still open" in body

    def test_an_entry_the_run_cannot_date_reads_as_quiet(self):
        """An item keeps its source anchor whether or not the report still
        carries the comment, so it is the entry this reading decides. A settled
        thread stops being fetched, so undatable is the ordinary shape of the
        row being scoped out, not a signal that it is new."""
        item = ItemOutcome(id="ic-900-0", summary="drop the guard",
                           file="old.go", line=4, outcome=FixOutcome.FIXED)
        body = _repost_over(*_sibling_rows(), outcomes=[item])
        assert "drop the guard" not in body
        assert "1 thread settled in an earlier round" in body

    def test_an_undatable_entry_no_comment_holds_is_still_written(self):
        """#712 outranks that reading: absent from the record, so never dropped."""
        item = ItemOutcome(id="ic-901-0", summary="never published",
                           file="old.go", line=4, outcome=FixOutcome.FIXED)
        body = _repost_over(*_sibling_rows(), outcomes=[item])
        assert "never published" in body

    def test_a_row_no_summary_comment_holds_is_written(self):
        """#712 read against the set: absent from the record, so never dropped."""
        body = _repost_over(
            ROUND_ONE_ROW,
            outcomes=[dataclasses.replace(_ROUND_ONE_OUTCOME, id="t9",
                                          summary="never published")],
            threads=_reviewed_thread(_BEFORE_THE_SUMMARY),
        )
        assert "never published" in body

    def test_the_footer_links_every_earlier_summary(self):
        import pr.comments
        second = pr.comments.MarkerComment(
            True, 12, _published_summary(ROUND_ONE_ROW),
            created_at=_SUMMARY_POSTED_AT, newest_other_at=_AFTER_THE_SUMMARY,
            url="https://github.com/owner/repo/pull/1#issuecomment-12",
        )
        state = _make_state(_fix(
            items=[_ROUND_TWO_OUTCOME], commit_status="no_changes",
            summary_deferred=True))
        with _lookup_returns(_round_one_marker(), second), \
                patch("pr.comments.post_issue_comment", return_value="https://url") as post:
            pr.summary_publish.render_deferred_summary(state, PRReport(), "owner/repo", 1, {})
        assert (f"**Earlier rounds:** [1]({_ROUND_ONE_URL}) · "
                f"[2]({second.url})") in post.call_args[0][2]

    def test_a_first_summary_has_no_footer(self, content):
        cp = pr.attribution.CommitPushResult("bbbbbbb", CommitStatus.PUSHED, "")
        with patch("pr.comments.post_issue_comment", return_value="https://url") as post:
            pr.summary_publish.post_fix_summary(
                content(fixed=[
                    CommentItem(id="t2", summary="round two work", file="new.go",
                                line=1),
                ]),
                cp, "owner/repo", 1, {},
            )
        assert "Earlier rounds" not in post.call_args[0][2]


class TestARoundWritesWhatItChanged:
    """A row this round re-classified is this round's business, however quiet.

    `covers` reads reviewer activity, and a round changing a row's outcome is
    not that: nobody has to speak for a deferred thread to become a fixed one.
    Left to the activity test alone, the new outcome reaches no summary at all
    and the record's newest word on the row is the outcome it has replaced.
    """

    def test_a_reclassified_row_is_written_though_nobody_spoke(self):
        body = _repost_over(
            _published_open_row(), outcomes=[_ROUND_ONE_OUTCOME],
            threads=_reviewed_thread(_BEFORE_THE_SUMMARY),
        )
        assert "drop the guard" in body
        assert "**2 fixed**" in body

    def test_an_unchanged_row_is_still_left_where_it_was_published(self):
        """The guard is the outcome, not the round: one wording per outcome is
        not something the renderer promises, so a re-worded cell is not news."""
        body = _repost_over(
            ROUND_ONE_ROW, outcomes=[_ROUND_ONE_OUTCOME],
            threads=_reviewed_thread(_BEFORE_THE_SUMMARY),
        )
        assert "drop the guard" not in body
        assert "1 thread settled in an earlier round" in body

    def test_a_hand_written_cell_is_not_a_reclassification(self):
        """A person's wording states no outcome, so it cannot differ from one.
        Reading it as a change would restate the row every round — the ratchet
        this issue removes, rebuilt on the one path a human controls."""
        body = _repost_over(
            HAND_EDITED_ROW, outcomes=[_ROUND_ONE_OUTCOME],
            threads=_reviewed_thread(_BEFORE_THE_SUMMARY),
        )
        assert "drop the guard" not in body
        assert _HAND_WRITTEN_ACTION_CELL not in body


class TestAnEditKeepsOnlyWhatItAloneHolds:
    """`target_keys` protected every row on the edited comment, which made it a
    ratchet: once a row reached a summary, every later edit of that comment
    re-rendered it whatever else carried it. Dropping a row an earlier comment
    also holds deletes nothing — the reader still finds it one link back."""

    def test_a_row_an_earlier_comment_also_holds_is_dropped(self):
        body = _edit_over_chain(
            [ROUND_ONE_ROW], [ROUND_ONE_ROW], outcomes=[_ROUND_ONE_OUTCOME],
            threads=_reviewed_thread(_BEFORE_THE_SUMMARY),
        )
        assert "drop the guard" not in body
        assert "1 thread settled in an earlier round" in body

    def test_the_dropped_row_is_not_handed_back_by_the_carry_forward(self):
        """The two gates ask one question. Scoping a row out of the body while
        carry-forward reads it as a round local state lost puts it straight
        back, verbatim, and reports it as carried."""
        body = _edit_over_chain(
            [ROUND_ONE_ROW], [ROUND_ONE_ROW], outcomes=[_ROUND_ONE_OUTCOME],
            threads=_reviewed_thread(_BEFORE_THE_SUMMARY),
        )
        assert "carried over" not in body

    def test_a_row_the_target_alone_holds_is_still_re_rendered(self):
        """Dropping it here deletes it: an edit rewrites the body wholesale and
        no earlier comment carries it."""
        body = _edit_over_chain(
            [], [ROUND_ONE_ROW], outcomes=[_ROUND_ONE_OUTCOME],
            threads=_reviewed_thread(_BEFORE_THE_SUMMARY),
        )
        assert "drop the guard" in body
        assert "settled in an earlier round" not in body


class TestAnEditKeepsItsTargetWhole:
    """An edit replaces the comment, so scoping it would delete the round."""

    def _edit(self, *rows, outcomes=(), threads=None):
        state = _make_state(_fix(
            items=[*outcomes], commit_status="no_changes", summary_deferred=True))
        marker = _round_one_marker(*rows, newest_other_at=_BEFORE_THE_SUMMARY)
        with _lookup_returns(marker), \
                patch("pr.comments.post_issue_comment", return_value="https://url") as post:
            pr.summary_publish.render_deferred_summary(
                state, PRReport(), "owner/repo", 1, threads or {})
        assert post.call_args.kwargs["marker"] == pr.summary_render.SUMMARY_MARKER
        return post.call_args[0][2]

    def test_a_quiet_row_the_target_holds_is_re_rendered(self):
        """The --finish pass updating a --fix round's own status is this case."""
        body = self._edit(
            ROUND_ONE_ROW, outcomes=[_ROUND_ONE_OUTCOME],
            threads=_reviewed_thread(_BEFORE_THE_SUMMARY),
        )
        assert "drop the guard" in body
        assert "settled in an earlier round" not in body
        assert "carried over" not in body

    def test_the_edited_comment_does_not_link_itself(self):
        assert "Earlier rounds" not in self._edit(ROUND_ONE_ROW)


class TestNewestReviewerActivity:
    """What counts as somebody else having spoken since the summary went up."""

    def _report(self, **overrides):
        defaults = dict(my_login="me")
        defaults.update(overrides)
        return PRReport(**defaults)

    def _thread(self, login, created_at):
        return ReportThread(
            id="t1", my_login="me",
            comments=[{"author": {"login": login}, "createdAt": created_at}],
        )

    def test_a_reviewer_reply_counts(self):
        report = self._report(threads=[self._thread("kgn", _AFTER_THE_SUMMARY)])
        assert pr.summary_publish.newest_reviewer_activity(report) == _AFTER_THE_SUMMARY

    def test_our_own_replies_do_not(self):
        """The fix pass replies before it publishes — counting those never settles."""
        report = self._report(threads=[self._thread("me", _AFTER_THE_SUMMARY)])
        assert pr.summary_publish.newest_reviewer_activity(report) == ""

    def test_a_verdict_with_no_body_counts(self):
        report = self._report(verdicts=[
            {"user": "kgn", "state": "APPROVED", "submitted_at": _AFTER_THE_SUMMARY},
        ])
        assert pr.summary_publish.newest_reviewer_activity(report) == _AFTER_THE_SUMMARY

    def test_our_own_verdict_does_not(self):
        report = self._report(verdicts=[
            {"user": "Me", "state": "COMMENTED", "submitted_at": _AFTER_THE_SUMMARY},
        ])
        assert pr.summary_publish.newest_reviewer_activity(report) == ""

    def test_the_newest_of_several_wins(self):
        report = self._report(
            threads=[self._thread("kgn", _BEFORE_THE_SUMMARY)],
            verdicts=[{"user": "kgn", "state": "APPROVED",
                       "submitted_at": _AFTER_THE_SUMMARY}],
        )
        assert pr.summary_publish.newest_reviewer_activity(report) == _AFTER_THE_SUMMARY

    def test_an_unknown_author_counts_as_somebody_else(self):
        """An author this cannot identify is not evidence the comment is ours."""
        report = self._report(threads=[
            ReportThread(id="t1", comments=[{"createdAt": _AFTER_THE_SUMMARY}]),
        ])
        assert pr.summary_publish.newest_reviewer_activity(report) == _AFTER_THE_SUMMARY

    def test_an_unresolved_identity_counts_everything_as_somebody_else(self):
        """An empty `my_login` must not make an equally-empty author match it."""
        report = self._report(
            my_login="",
            threads=[ReportThread(id="t1", comments=[
                {"createdAt": _AFTER_THE_SUMMARY},
            ])],
            verdicts=[{"state": "COMMENTED", "submitted_at": _BEFORE_THE_SUMMARY}],
        )
        assert pr.summary_publish.newest_reviewer_activity(report) == _AFTER_THE_SUMMARY

    def test_a_quiet_pr_reports_nothing(self):
        assert pr.summary_publish.newest_reviewer_activity(self._report()) == ""
