"""Tests for `pr.thread_models` — the constructors, against the type itself.

`TrackingResult.from_outcomes` is the join between what the agent recorded and
what the pass handed it: the file gives an id and a verdict, and the reviewer,
the summary and the conversation behind that id live on the entry. Its only
coverage was through the round trip in `test_review_threads.py`, which writes a
real tracking file and reads it back — good evidence about the format, and none
at all about the join, since every entry there is well-formed by construction.

What is covered here is the join's own rules: which side an id resolves to, what
happens to an id neither side knows, and which verdicts get a reason invented
for them when the agent gave none.
"""

import sys

from conftest import REPO_ROOT

LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pytest  # noqa: E402

from pr.fix import FixOutcome, ItemOutcome  # noqa: E402
from pr.comments_state import ThreadState  # noqa: E402
from pr.thread_models import (  # noqa: E402
    CommentItem, ReplyOutcome, TrackingResult,
)


def _entry(eid, **kw):
    return CommentItem(id=eid, file="f.go", line=3, reviewer="kgn",
                       summary=f"{eid} summary", **kw)


def _recorded(eid, outcome, reason=""):
    return ItemOutcome(id=eid, outcome=outcome, reason=reason)


class TestFromOutcomesResolvesTheEntry:
    """An outcome names an id; the entry behind it is what goes in the bucket."""

    def test_a_thread_lands_on_the_thread_side(self):
        result = TrackingResult.from_outcomes(
            [_recorded("t1", FixOutcome.FIXED)], [_entry("t1")])
        assert [e.id for e in result.bucket(FixOutcome.FIXED)] == ["t1"]
        assert result.bucket(FixOutcome.FIXED, item=True) == []

    def test_a_comment_item_lands_on_the_item_side(self):
        """Only a thread has somewhere to reply, so the sides never merge."""
        result = TrackingResult.from_outcomes(
            [_recorded("c1", FixOutcome.FIXED)], [], fixable_items=[_entry("c1")])
        assert result.bucket(FixOutcome.FIXED) == []
        assert [e.id for e in result.bucket(FixOutcome.FIXED, item=True)] == ["c1"]

    def test_the_entry_carries_its_own_fields_through(self):
        """The outcome has an id and a verdict; everything else is the entry's."""
        result = TrackingResult.from_outcomes(
            [_recorded("t1", FixOutcome.FIXED)], [_entry("t1")])
        entry = result.bucket(FixOutcome.FIXED)[0]
        assert entry.reviewer == "kgn"
        assert entry.summary == "t1 summary"
        assert entry.file == "f.go"

    def test_an_id_neither_side_knows_is_dropped(self):
        """The file is agent-editable — an invented id names nobody to reply to."""
        result = TrackingResult.from_outcomes(
            [_recorded("invented", FixOutcome.FIXED)], [_entry("t1")])
        assert result.both(FixOutcome.FIXED) == []

    def test_a_thread_wins_an_id_collision(self):
        """Both sides claiming one id resolves to the thread, which can be replied to."""
        result = TrackingResult.from_outcomes(
            [_recorded("x1", FixOutcome.FIXED)], [_entry("x1")],
            fixable_items=[_entry("x1")])
        assert [e.id for e in result.bucket(FixOutcome.FIXED)] == ["x1"]

    def test_no_outcomes_is_an_empty_result(self):
        """The empty pass: nothing recorded, nothing to sort, no buckets."""
        result = TrackingResult.from_outcomes([], [_entry("t1")])
        assert result.threads == {}
        assert result.items == {}


class TestFromOutcomesFillsTheUnstatedReason:
    """A ticked box with no words still has to say something to a reviewer."""

    @pytest.mark.parametrize("outcome,expected", [
        (FixOutcome.DEFERRED, "agent could not auto-fix"),
        (FixOutcome.NEEDS_HUMAN, "agent could not auto-fix"),
        (FixOutcome.DECLINED, "agent declined without giving a reason"),
    ])
    def test_a_silent_verdict_gets_a_reason(self, outcome, expected):
        result = TrackingResult.from_outcomes(
            [_recorded("t1", outcome)], [_entry("t1")])
        assert result.bucket(outcome)[0].reason == expected

    def test_fixed_invents_nothing(self):
        """The change itself is the reason; a fixed entry needs no explanation."""
        result = TrackingResult.from_outcomes(
            [_recorded("t1", FixOutcome.FIXED)], [_entry("t1")])
        assert result.bucket(FixOutcome.FIXED)[0].reason == ""

    def test_the_agent_s_own_words_win(self):
        result = TrackingResult.from_outcomes(
            [_recorded("t1", FixOutcome.DECLINED, "the helper it names is gone")],
            [_entry("t1")])
        assert result.bucket(FixOutcome.DECLINED)[0].reason == (
            "the helper it names is gone")

    def test_triage_s_reason_survives_a_silent_verdict(self):
        """An entry that arrived with a reason keeps it rather than being overwritten."""
        result = TrackingResult.from_outcomes(
            [_recorded("t1", FixOutcome.FIXED)],
            [_entry("t1", reason="contested")])
        assert result.bucket(FixOutcome.FIXED)[0].reason == "contested"

    def test_the_source_entry_is_not_mutated(self):
        """The reason is written onto a copy — the pass's own list is untouched."""
        source = _entry("t1")
        TrackingResult.from_outcomes([_recorded("t1", FixOutcome.DEFERRED)], [source])
        assert source.reason == ""


class TestReplyOutcomeAccumulates:
    """The two halves of a round's replies reach one save as one value.

    Triage replies to the dismissed and the already-addressed before the agent
    runs; the pass replies to what it fixed after. Both halves have to arrive
    at the same `persist` call, because the comment tally on disk was
    snapshotted before either ran and learns of the resolutions only from the
    delta. Carried as two loose locals, a phase that forgot to add one of them
    in silently dropped its resolutions from `pr status`.
    """

    def test_an_empty_outcome_says_nothing_happened(self):
        empty = ReplyOutcome()
        assert empty.posted == 0
        assert empty.resolved == ()

    def test_the_counts_add(self):
        total = ReplyOutcome(posted=2).plus(ReplyOutcome(posted=3))
        assert total.posted == 5

    def test_the_resolutions_concatenate_in_order(self):
        """The bucket each thread came from, first phase's before the second's."""
        total = ReplyOutcome(resolved=(ThreadState.NEW,)).plus(
            ReplyOutcome(resolved=(ThreadState.ADDRESSED, ThreadState.NEW)))
        assert total.resolved == (
            ThreadState.NEW, ThreadState.ADDRESSED, ThreadState.NEW)

    def test_adding_an_empty_outcome_changes_nothing(self):
        """The phase that replied to nothing still adds its outcome in."""
        one = ReplyOutcome(posted=2, resolved=(ThreadState.NEW,))
        assert one.plus(ReplyOutcome()) == one

    def test_neither_side_is_mutated(self):
        """Frozen, so a phase cannot lose its own half to the sum."""
        first = ReplyOutcome(posted=1, resolved=(ThreadState.NEW,))
        second = ReplyOutcome(posted=1, resolved=(ThreadState.ADDRESSED,))
        first.plus(second)
        assert first.posted == 1 and first.resolved == (ThreadState.NEW,)
        assert second.posted == 1 and second.resolved == (ThreadState.ADDRESSED,)
