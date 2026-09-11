"""Tests for `summary_rounds` — the arithmetic that lets a round omit a row.

`RoundScope.covers` is exercised end to end through `test_review_threads.py`,
but `round_scope` itself — the function that builds the scope out of the
comments actually on the PR — had no direct test, and neither did the two
timestamp readers it is paired with. That is the wrong way round: the scope is
what decides whether a published row is restated or silently dropped, and a
mistake in building it reads downstream as a summary quietly losing a round.

Covered here directly so a change to the rule fails on the rule.
"""

import sys

from conftest import REPO_ROOT

LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pytest  # noqa: E402

from pr import summary_rounds  # noqa: E402
from pr.comments import MarkerComment, MarkerHistory  # noqa: E402
from pr.comments_state import ThreadState  # noqa: E402
from pr.fix import FixOutcome  # noqa: E402
from pr.summary_model import TABLE_DIVIDER, TABLE_HEADER  # noqa: E402
from pr.thread_models import CommentItem, ReportThread  # noqa: E402

T1 = "#discussion_r111"
T2 = "#discussion_r222"


def _row(anchor: str, action: str = "Deferred", reviewer: str = "@kgn") -> str:
    return (f"| [a point](https://github.com/o/r/pull/1{anchor}) "
            f"| {reviewer} | `a.py:2` | {action} |")


def _body(*rows: str) -> str:
    return "\n".join(["<!-- pr-comments:summary -->", "", TABLE_HEADER,
                      TABLE_DIVIDER, *rows])


def _comment(body: str, cid: int = 1, created: str = "2026-01-01T00:00:00Z",
             updated: str = "") -> MarkerComment:
    return MarkerComment(
        found=True, comment_id=cid, body=body,
        url=f"https://github.com/o/r/pull/1#issuecomment-{cid}",
        created_at=created, updated_at=updated,
    )


def _history(*comments: MarkerComment) -> MarkerHistory:
    return MarkerHistory(found=True, comments=tuple(comments))


class TestWhatTheRecordAlreadyHolds:
    """`published_keys` is every row on every summary comment, not just the target."""

    def test_a_row_on_the_target_is_published(self):
        scope = summary_rounds.round_scope(_history(_comment(_body(_row(T1)))), False)
        assert T1 in scope.published_keys

    def test_a_row_on_an_earlier_comment_is_published_too(self):
        scope = summary_rounds.round_scope(
            _history(_comment(_body(_row(T1)), cid=1),
                     _comment(_body(_row(T2)), cid=2)), False)
        assert {T1, T2} <= scope.published_keys

    def test_a_row_nothing_holds_is_always_written(self):
        """The guarantee underneath the whole scoping rule."""
        scope = summary_rounds.round_scope(_history(_comment(_body(_row(T1)))), False)
        assert scope.covers("#discussion_r999", activity_at="")


class TestTheEditedCommentIsNotAllowedToShrink:
    """An in-place edit rewrites its target wholesale, so its rows stay in scope."""

    def test_a_row_only_the_target_holds_is_re_rendered(self):
        scope = summary_rounds.round_scope(_history(_comment(_body(_row(T1)))), False)
        assert T1 in scope.target_keys
        assert scope.covers(T1, activity_at="")

    def test_a_fresh_post_protects_nothing(self):
        """Answered: the earlier comments stay where they are, so none is at risk."""
        scope = summary_rounds.round_scope(
            _history(_comment(_body(_row(T1)))), True)
        assert scope.target_keys == frozenset()

    def test_a_row_an_earlier_comment_also_holds_is_not_protected(self):
        """It is one link back, so dropping it from this body loses nothing."""
        scope = summary_rounds.round_scope(
            _history(_comment(_body(_row(T1)), cid=1),
                     _comment(_body(_row(T1)), cid=2)), False)
        assert T1 not in scope.target_keys
        assert T1 in scope.elsewhere_keys


class TestTheNewestWordOnARowWins:
    """Outcomes are read oldest comment first, so the newest overwrites."""

    def test_the_later_comment_supplies_the_outcome(self):
        scope = summary_rounds.round_scope(
            _history(_comment(_body(_row(T1, "Deferred")), cid=1),
                     _comment(_body(_row(T1, "Already addressed")), cid=2)), False)
        assert scope.published_outcomes[T1] is FixOutcome.ALREADY_ADDRESSED

    def test_a_hand_written_cell_states_no_outcome(self):
        """It must not fall back to the generated cell an earlier round wrote."""
        scope = summary_rounds.round_scope(
            _history(_comment(_body(_row(T1, "Deferred")), cid=1),
                     _comment(_body(_row(T1, "I disagree, leaving open")), cid=2)),
            False)
        assert T1 not in scope.published_outcomes

    def test_a_changed_outcome_is_written_whoever_holds_the_row(self):
        scope = summary_rounds.round_scope(
            _history(_comment(_body(_row(T1, "Deferred")), cid=1),
                     _comment(_body(_row(T1, "Deferred")), cid=2)), False)
        assert scope.covers(T1, activity_at="", outcome=FixOutcome.FIXED)

    def test_an_unchanged_outcome_is_left_where_it_was_published(self):
        scope = summary_rounds.round_scope(
            _history(_comment(_body(_row(T1, "Deferred")), cid=1),
                     _comment(_body(_row(T1, "Deferred")), cid=2)), False)
        assert not scope.covers(T1, activity_at="", outcome=FixOutcome.DEFERRED)


class TestTheScopeDatesTheBodyNotTheComment:
    """`since` has to describe the moment the keys beside it were written."""

    def test_the_edit_time_wins_when_there_is_one(self):
        scope = summary_rounds.round_scope(
            _history(_comment(_body(_row(T1)), created="2026-01-01T00:00:00Z",
                              updated="2026-06-01T00:00:00Z")), False)
        assert scope.since == "2026-06-01T00:00:00Z"

    def test_it_falls_back_to_the_post_time(self):
        scope = summary_rounds.round_scope(
            _history(_comment(_body(_row(T1)), created="2026-01-01T00:00:00Z")), False)
        assert scope.since == "2026-01-01T00:00:00Z"


class TestDatingAnEntry:
    """`entry_activity_at` is what tells a quiet row from one spoken on since."""

    def _thread(self, comments):
        return ReportThread(
            id="t1", state=ThreadState.NEW, is_resolved=False, reviewer="kgn",
            file="a.py", line=2, comments=comments, my_login="me",
        )

    def test_the_newest_reviewer_comment_dates_the_thread(self):
        thread = self._thread([
            {"createdAt": "2026-01-01T00:00:00Z", "author": {"login": "kgn"}},
            {"createdAt": "2026-03-01T00:00:00Z", "author": {"login": "kgn"}},
        ])
        entry = CommentItem(id="t1", summary="s", file="a.py", line=2)
        assert summary_rounds.entry_activity_at(
            entry, {"t1": thread}, {}) == "2026-03-01T00:00:00Z"

    def test_our_own_reply_does_not_date_the_thread(self):
        """A pass replies before it publishes; counting that restates everything."""
        thread = self._thread([
            {"createdAt": "2026-01-01T00:00:00Z", "author": {"login": "kgn"}},
            {"createdAt": "2026-09-01T00:00:00Z", "author": {"login": "me"}},
        ])
        entry = CommentItem(id="t1", summary="s", file="a.py", line=2)
        assert summary_rounds.entry_activity_at(
            entry, {"t1": thread}, {}) == "2026-01-01T00:00:00Z"

    def test_an_entry_with_no_thread_reads_its_source_comment(self):
        entry = CommentItem(id="ic-500-0", summary="s", file="a.py", line=2)
        assert summary_rounds.entry_activity_at(
            entry, {}, {"500": "2026-04-01T00:00:00Z"}) == "2026-04-01T00:00:00Z"

    def test_an_undatable_entry_returns_empty(self):
        entry = CommentItem(id="ic-999-0", summary="s", file="a.py", line=2)
        assert summary_rounds.entry_activity_at(entry, {}, {}) == ""


class TestCommentTimestamps:
    """Both listings, keyed the way an entry's source id is spelled."""

    def test_both_kinds_are_keyed_as_strings(self):
        stamps = summary_rounds.comment_timestamps(
            [{"id": 500, "created_at": "2026-01-01T00:00:00Z"}],
            [{"id": 600, "submitted_at": "2026-02-01T00:00:00Z"}],
        )
        assert stamps == {"500": "2026-01-01T00:00:00Z",
                          "600": "2026-02-01T00:00:00Z"}

    def test_a_review_body_uses_its_submission_time(self):
        stamps = summary_rounds.comment_timestamps(
            [], [{"id": 1, "submitted_at": "2026-02-01T00:00:00Z"}])
        assert stamps["1"] == "2026-02-01T00:00:00Z"


class TestAQuietRowIsLeftWhereItWasPublished:
    """The activity test, which is the last of the three questions `covers` asks."""

    @pytest.fixture
    def scope(self):
        return summary_rounds.round_scope(
            _history(_comment(_body(_row(T1)), cid=1, created="2026-05-01T00:00:00Z"),
                     _comment(_body(_row(T1)), cid=2, created="2026-05-01T00:00:00Z")),
            False)

    def test_a_row_spoken_on_since_comes_back(self, scope):
        assert scope.covers(T1, activity_at="2026-06-01T00:00:00Z")

    def test_a_row_quiet_since_is_left_alone(self, scope):
        assert not scope.covers(T1, activity_at="2026-04-01T00:00:00Z")

    def test_an_undatable_entry_reads_as_quiet(self, scope):
        """A settled thread stops being fetched, which is the ordinary shape here."""
        assert not scope.covers(T1, activity_at="")
