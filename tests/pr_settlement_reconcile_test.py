"""pr.settlement: reconciling the fix snapshot and comment sources."""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from review_threads_support import (  # noqa: E402
    _fetches, _fix, _make_state, _no_published_summary, _our_reply, content,
)
from pr.comments_state import ThreadState
from git.land import CommitStatus
import pr.summary_publish
import pr.settlement
from pr.fix import FixOutcome, ItemOutcome, SettledBy
from pr.thread_models import ReportThread


class TestReconcileFixSnapshot:
    """Evidence on GitHub outranks a stale snapshot."""

    def _state(self):
        return _make_state(_fix(head_sha="aaaaaaa", items=[
            ItemOutcome(id="t1", file="a.go", line=1,
                        summary="one", outcome=FixOutcome.DEFERRED,
                        reason="agent could not auto-fix"),
        ], reviewers={"t1": "kgn"}))

    def _thread(self, comments, **kw):
        kw.setdefault("state", ThreadState.NEW)
        kw.setdefault("is_resolved", False)
        return ReportThread(id="t1", comments=comments, **kw)

    def test_a_resolved_thread_is_settled_but_not_claimed_as_fixed(self):
        """The resolve button is not evidence of a fix.

        It is pressed for a thread that was answered, deferred by agreement, or
        withdrawn by the reviewer just as readily as one whose fix landed. The
        row leaves the deferred bucket — nobody owes it — under a verdict that
        claims no more than the evidence does.
        """
        state = self._state()
        threads = {"t1": self._thread([{"body": "x"}],
                                      state=ThreadState.RESOLVED, is_resolved=True)}
        assert pr.settlement.reconcile_fix_snapshot(state, threads) == 1
        assert state.fix.fix.items[0].outcome == FixOutcome.SETTLED_ELSEWHERE

    def test_a_reconciled_row_records_who_settled_it(self):
        """Provenance, not the wording of `reason`, is what a renderer reads."""
        state = self._state()
        threads = {"t1": self._thread([{"body": "x"}],
                                      state=ThreadState.RESOLVED, is_resolved=True)}
        pr.settlement.reconcile_fix_snapshot(state, threads)
        assert state.fix.fix.items[0].settled_by is SettledBy.RECONCILIATION

    def test_an_addressed_thread_is_settled_but_not_claimed_as_fixed(self):
        """Lifecycle state alone says as little as the resolve button does."""
        state = self._state()
        threads = {"t1": self._thread([{"body": "x"}], state=ThreadState.ADDRESSED)}
        assert pr.settlement.reconcile_fix_snapshot(state, threads) == 1
        assert state.fix.fix.items[0].outcome == FixOutcome.SETTLED_ELSEWHERE

    def test_thread_with_a_fix_reply_is_reclaimed_even_if_unresolved(self):
        """The 13 contradicted threads on the incident PR all looked like this."""
        state = self._state()
        threads = {"t1": self._thread([
            {"body": "please rename this"},
            {"body": "Applied: renamed the guard\n\nFixed in `abc1234`."},
        ])}
        assert pr.settlement.reconcile_fix_snapshot(state, threads) == 1
        assert state.fix.fix.items[0].outcome == FixOutcome.FIXED

    def test_genuinely_open_thread_stays_deferred(self):
        state = self._state()
        threads = {"t1": self._thread([{"body": "please rename this"}])}
        assert pr.settlement.reconcile_fix_snapshot(state, threads) == 0
        assert state.fix.fix.items[0].outcome == FixOutcome.DEFERRED

    def test_a_deferred_reply_is_not_evidence_of_a_fix(self):
        """Our own prior Deferred: reply must not reclaim the thread."""
        state = self._state()
        threads = {"t1": self._thread([
            {"body": "please rename this"},
            {"body": "Deferred: rename the guard\n\nTracked in ENG-3021."},
        ])}
        assert pr.settlement.reconcile_fix_snapshot(state, threads) == 0
        assert state.fix.fix.items[0].outcome == FixOutcome.DEFERRED

    def test_a_thread_absent_from_github_stays_deferred(self):
        """An id nothing on GitHub knows anything about settles nothing.

        Still the right answer for a genuinely unknown thread id. A comment item
        is no longer the same case: it is absent from this map by construction,
        and TestCommentItemsSettleThroughTheirSource covers what does settle it.
        """
        state = self._state()
        assert pr.settlement.reconcile_fix_snapshot(state, {}, {"77": FixOutcome.FIXED}) == 0
        assert state.fix.fix.items[0].outcome == FixOutcome.DEFERRED

    def test_a_needs_human_thread_settled_by_hand_is_reclaimed(self):
        """The pass handed it to the operator; the operator answering it is the ending.

        Answering is exactly what the resolve button most often means on a
        NEEDS_HUMAN thread, which is why the verdict it lands on says settled
        rather than fixed.
        """
        state = _make_state(_fix(head_sha="aaaaaaa", items=[
            ItemOutcome(id="t1", outcome=FixOutcome.NEEDS_HUMAN, reason="contested"),
        ]))
        threads = {"t1": self._thread([{"body": "x"}],
                                      state=ThreadState.RESOLVED, is_resolved=True)}
        assert pr.settlement.reconcile_fix_snapshot(state, threads) == 1
        assert state.fix.fix.items[0].outcome == FixOutcome.SETTLED_ELSEWHERE

    def test_a_needs_human_thread_still_open_is_left_alone(self):
        state = _make_state(_fix(head_sha="aaaaaaa", items=[
            ItemOutcome(id="t1", outcome=FixOutcome.NEEDS_HUMAN, reason="contested"),
        ]))
        threads = {"t1": self._thread([{"body": "why not do it the other way?"}])}
        assert pr.settlement.reconcile_fix_snapshot(state, threads) == 0
        assert state.fix.fix.items[0].outcome == FixOutcome.NEEDS_HUMAN

    def test_a_declined_thread_settled_by_hand_is_reclaimed(self):
        """The agent refused it; the operator doing it anyway outranks that refusal.

        Without this the thread republishes as declined on every later run, so the
        reviewer keeps reading a verdict the tree stopped supporting.
        """
        state = _make_state(_fix(head_sha="aaaaaaa", items=[
            ItemOutcome(id="t1", outcome=FixOutcome.DECLINED,
                          reason="the premise does not hold"),
        ]))
        threads = {"t1": self._thread([{"body": "x"}],
                                      state=ThreadState.RESOLVED, is_resolved=True)}
        assert pr.settlement.reconcile_fix_snapshot(state, threads) == 1
        assert state.fix.fix.items[0].outcome == FixOutcome.SETTLED_ELSEWHERE
        assert "reconciled" in state.fix.fix.items[0].reason

    def test_a_declined_thread_still_open_is_left_alone(self):
        state = _make_state(_fix(head_sha="aaaaaaa", items=[
            ItemOutcome(id="t1", outcome=FixOutcome.DECLINED,
                          reason="the premise does not hold"),
        ]))
        threads = {"t1": self._thread([{"body": "why not do it the other way?"}])}
        assert pr.settlement.reconcile_fix_snapshot(state, threads) == 0
        assert state.fix.fix.items[0].outcome == FixOutcome.DECLINED

    def test_settled_outcomes_are_left_alone(self):
        """Only the open actions are reconcilable — the rest are already decided.

        SETTLED_ELSEWHERE is among them: it is what a previous reconciliation
        wrote, and asking the same question of the same resolved thread again
        would re-flip a row nobody owes on every `--finish`.
        """
        settled = (FixOutcome.FIXED, FixOutcome.DISMISSED,
                   FixOutcome.ALREADY_ADDRESSED, FixOutcome.SETTLED_ELSEWHERE)
        state = _make_state(_fix(head_sha="aaaaaaa", items=[
            ItemOutcome(id=f"t{i}", outcome=o)
            for i, o in enumerate(settled)
        ]))
        threads = {
            f"t{i}": ReportThread(id=f"t{i}", comments=[{"body": "x"}],
                                  state=ThreadState.RESOLVED, is_resolved=True)
            for i in range(len(settled))
        }
        assert pr.settlement.reconcile_fix_snapshot(state, threads) == 0
        assert [t.outcome for t in state.fix.fix.items] == list(settled)

    def test_the_reason_records_why_it_flipped(self):
        state = self._state()
        threads = {"t1": self._thread([{"body": "x"}],
                                      state=ThreadState.RESOLVED, is_resolved=True)}
        pr.settlement.reconcile_fix_snapshot(state, threads)
        assert "reconciled" in state.fix.fix.items[0].reason


class TestAnsweredCommentSources:
    """A comment item has no thread, so the evidence is on the comment itself."""

    def _outcomes(self, outcome=FixOutcome.NEEDS_HUMAN, iid="ic-77-0"):
        return [ItemOutcome(id=iid, outcome=outcome, reason="contested")]

    def test_our_handled_reply_marks_its_source_answered(self):
        with _fetches([_our_reply("#issuecomment-77")]):
            answered = pr.settlement.answered_comment_sources(
                self._outcomes(), "owner/repo", 42, "me")
        assert answered == {"77": FixOutcome.FIXED}

    def test_the_listing_is_asked_to_keep_our_own_comments(self):
        """The reply being looked for is ours, so the self filter has to be off."""
        with _fetches([_our_reply("#issuecomment-77")]) as fetch:
            pr.settlement.answered_comment_sources(self._outcomes(), "owner/repo", 42, "me")
        assert fetch.call_args.kwargs["include_self"] is True

    def test_a_review_body_is_answered_through_its_own_anchor(self):
        with _fetches([_our_reply("#pullrequestreview-88")]):
            answered = pr.settlement.answered_comment_sources(
                self._outcomes(iid="rb-88-1"), "owner/repo", 42, "me")
        assert answered == {"88": FixOutcome.FIXED}

    def test_the_login_match_ignores_case(self):
        with _fetches([_our_reply("#issuecomment-77", user="Me")]):
            answered = pr.settlement.answered_comment_sources(
                self._outcomes(), "owner/repo", 42, "me")
        assert answered == {"77": FixOutcome.FIXED}

    def test_the_reviewer_restating_their_point_is_not_an_answer(self):
        with _fetches([_our_reply("#issuecomment-77", user="kgn")]):
            answered = pr.settlement.answered_comment_sources(
                self._outcomes(), "owner/repo", 42, "me")
        assert answered == {}

    def test_a_deferred_reply_says_the_opposite(self):
        """Same carve-out the thread evidence makes — it is not a settlement."""
        with _fetches([_our_reply("#issuecomment-77", prefix="Deferred:")]):
            answered = pr.settlement.answered_comment_sources(
                self._outcomes(), "owner/repo", 42, "me")
        assert answered == {}

    def test_a_reply_that_cites_nothing_settles_nothing(self):
        with _fetches([{"user": "me", "body": "Applied: drop the retry"}]):
            answered = pr.settlement.answered_comment_sources(
                self._outcomes(), "owner/repo", 42, "me")
        assert answered == {}

    def test_a_non_comment_item_is_not_worth_a_listing(self):
        """`t1` is open, but a thread-shaped id has no source comment to read."""
        with _fetches([]) as fetch:
            answered = pr.settlement.answered_comment_sources(
                [ItemOutcome(id="t1", outcome=FixOutcome.DEFERRED)],
                "owner/repo", 42, "me")
        assert answered == {}
        fetch.assert_not_called()

    def test_a_settled_item_is_not_worth_a_listing_either(self):
        with _fetches([]) as fetch:
            pr.settlement.answered_comment_sources(
                self._outcomes(outcome=FixOutcome.FIXED), "owner/repo", 42, "me")
        fetch.assert_not_called()

    def test_without_our_login_no_reply_can_be_called_ours(self):
        with _fetches([_our_reply("#issuecomment-77")]) as fetch:
            answered = pr.settlement.answered_comment_sources(
                self._outcomes(), "owner/repo", 42, "")
        assert answered == {}
        fetch.assert_not_called()

    def test_a_hand_written_verdict_answers_its_source_too(self):
        """The same widening the thread evidence got, on the only surface a
        decomposed item has. A reply naming the verdict in a person's own words
        is the same evidence as one that came out of a template.
        """
        with _fetches([_our_reply("#issuecomment-77", prefix="Fixed —")]):
            answered = pr.settlement.answered_comment_sources(
                self._outcomes(), "owner/repo", 42, "me")
        assert answered == {"77": FixOutcome.FIXED}

    def test_a_hand_written_dismissal_answers_as_dismissed_not_fixed(self):
        """The same grading the thread evidence got: naming a verdict is not
        the same as naming FIXED specifically.
        """
        with _fetches([_our_reply("#issuecomment-77", prefix="Dismissed —")]):
            answered = pr.settlement.answered_comment_sources(
                self._outcomes(), "owner/repo", 42, "me")
        assert answered == {"77": FixOutcome.DISMISSED}

    def test_the_reviewer_typing_the_same_verdict_answers_nothing(self):
        """The negative the widening is bought with — the login test is what
        stops their words settling the item they themselves raised.
        """
        with _fetches([_our_reply("#issuecomment-77", prefix="Fixed —", user="kgn")]):
            answered = pr.settlement.answered_comment_sources(
                self._outcomes(), "owner/repo", 42, "me")
        assert answered == {}

    def test_an_acknowledgement_of_ours_answers_nothing(self):
        """Being heard is not being handled."""
        with _fetches([_our_reply("#issuecomment-77", prefix="Good catch —")]):
            answered = pr.settlement.answered_comment_sources(
                self._outcomes(), "owner/repo", 42, "me")
        assert answered == {}


class TestCommentItemsSettleThroughTheirSource:
    """The outcome the fix pass handed to the operator has to be clearable."""

    def _state(self, outcome=FixOutcome.NEEDS_HUMAN, iid="ic-77-0"):
        return _make_state(_fix(head_sha="aaaaaaa", items=[
            ItemOutcome(id=iid, file="a.go", line=7,
                        summary="drop the retry", outcome=outcome,
                        reason="contested"),
        ], reviewers={iid: "kgn"}))

    def test_an_answered_item_reconciles_to_fixed(self):
        state = self._state()
        assert pr.settlement.reconcile_fix_snapshot(state, {}, {"77": FixOutcome.FIXED}) == 1
        assert state.fix.fix.items[0].outcome == FixOutcome.FIXED
        assert "reconciled" in state.fix.fix.items[0].reason

    def test_a_deferred_item_reconciles_the_same_way(self):
        state = self._state(outcome=FixOutcome.DEFERRED)
        assert pr.settlement.reconcile_fix_snapshot(state, {}, {"77": FixOutcome.FIXED}) == 1
        assert state.fix.fix.items[0].outcome == FixOutcome.FIXED

    def test_a_review_body_item_reconciles_through_its_review(self):
        state = self._state(iid="rb-88-1")
        assert pr.settlement.reconcile_fix_snapshot(state, {}, {"88": FixOutcome.FIXED}) == 1
        assert state.fix.fix.items[0].outcome == FixOutcome.FIXED

    def test_an_answer_to_another_comment_is_not_this_items_answer(self):
        state = self._state()
        assert pr.settlement.reconcile_fix_snapshot(state, {}, {"99": FixOutcome.FIXED}) == 0
        assert state.fix.fix.items[0].outcome == FixOutcome.NEEDS_HUMAN

    def test_an_unanswered_item_still_holds_the_summary_back(self, content):
        state = self._state()
        assert pr.settlement.reconcile_fix_snapshot(state, {}, {}) == 0
        needs_human = [t for t in state.fix.fix.items
                       if t.outcome == FixOutcome.NEEDS_HUMAN]
        assert needs_human
        assert pr.summary_publish.summary_still_owed(
            content(needs_human=needs_human), CommitStatus.PUSHED, False) is True

    def test_an_item_restating_a_settled_thread_settles_with_it(self):
        """The duplicate is one finding; one of its two copies being closed closes it.

        It inherits the thread's grade with it: the copy cannot be better
        evidence than the thread it is a copy of, and that thread has only its
        resolve button to show.
        """
        state = self._state()
        threads = {"t1": ReportThread(
            id="t1", file="a.go", line=7, reviewer="kgn",
            state=ThreadState.RESOLVED, is_resolved=True, comments=[{"body": "x"}],
        )}
        assert pr.settlement.reconcile_fix_snapshot(state, threads) == 1
        assert state.fix.fix.items[0].outcome == FixOutcome.SETTLED_ELSEWHERE

    def test_an_item_restating_a_thread_we_replied_to_inherits_the_fix(self):
        """A standing reply of ours names the verdict, and the copy gets it."""
        state = self._state()
        threads = {"t1": ReportThread(
            id="t1", file="a.go", line=7, reviewer="kgn",
            state=ThreadState.NEW, is_resolved=False,
            comments=[{"body": "Applied: dropped the retry\n\nFixed in `abc1234`."}],
        )}
        assert pr.settlement.reconcile_fix_snapshot(state, threads) == 1
        assert state.fix.fix.items[0].outcome == FixOutcome.FIXED

    @pytest.mark.parametrize("resolved_first", [True, False])
    def test_the_stronger_evidence_at_a_location_wins(self, resolved_first):
        """Two threads on one line, one merely resolved and one we answered.

        The location carries one verdict, so the grades have to be ordered
        rather than left to whichever thread the map happened to visit last.
        Both insertion orders run: a last-write-wins fold passes one of them.
        """
        state = self._state()
        resolved = ReportThread(
            id="t1", file="a.go", line=7, reviewer="kgn",
            state=ThreadState.RESOLVED, is_resolved=True,
            comments=[{"body": "x"}],
        )
        answered = ReportThread(
            id="t2", file="a.go", line=7, reviewer="kgn",
            state=ThreadState.NEW, is_resolved=False,
            comments=[{"body": "Applied: dropped the retry\n\nFixed in `abc`."}],
        )
        pair = [resolved, answered] if resolved_first else [answered, resolved]
        threads = {t.id: t for t in pair}
        assert pr.settlement.reconcile_fix_snapshot(state, threads) == 1
        assert state.fix.fix.items[0].outcome == FixOutcome.FIXED

    def test_an_item_restating_an_open_thread_stays_open(self):
        state = self._state()
        threads = {"t1": ReportThread(
            id="t1", file="a.go", line=7, reviewer="kgn",
            state=ThreadState.NEW, is_resolved=False,
            comments=[{"body": "why not the other way?"}],
        )}
        assert pr.settlement.reconcile_fix_snapshot(state, threads) == 0
        assert state.fix.fix.items[0].outcome == FixOutcome.NEEDS_HUMAN

    def test_a_settled_thread_elsewhere_settles_nothing_here(self):
        state = self._state()
        threads = {"t1": ReportThread(
            id="t1", file="b.go", line=3, reviewer="kgn",
            state=ThreadState.RESOLVED, is_resolved=True, comments=[{"body": "x"}],
        )}
        assert pr.settlement.reconcile_fix_snapshot(state, threads) == 0
        assert state.fix.fix.items[0].outcome == FixOutcome.NEEDS_HUMAN
