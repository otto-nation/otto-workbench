"""Tests for `pr.triage_round` — the dispositions, the holds, and the round.

The classification rules and the two publishing holds used to live in
`ai/bin/review-threads` and are tested here now that they have a module. The
cases themselves are the ones that were there, repointed; what is new is
coverage of `TriagedRound` and `triage_the_round`, which had none because
neither existed.

Three things are worth testing at this level rather than through the fix pass.
The classifier's clause order is a rule — a contested thread is a person's
whatever the model called it, and a valid suggestion the model called complex
does not reach the agent. The holds decide what a round is allowed to assert
outward. And the round's own constructor is what keeps the holds ahead of
anything that reads `publishing.enabled()`, which was previously kept only by
two calls sitting near each other.
"""

import dataclasses
import sys
from unittest.mock import MagicMock, patch

from conftest import (
    REPO_ROOT, make_ctx, supersession_context, supersession_evidence,
    supersession_verdict,
)

LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pytest  # noqa: E402

from pr import triage_round  # noqa: E402
from pr.comments_state import ThreadState  # noqa: E402
from pr.domains import SupersessionKind  # noqa: E402
from pr.thread_models import (  # noqa: E402
    ClassificationResult, CommentItem, PRReport, ReplyOutcome, ReportThread,
    TriageResult,
)


def _fixable_count(entries):
    """What the trail reports fixable — the classifier's own answer."""
    return len(triage_round.classify_entries(entries).fixable)


class TestClassifyTriageComplexity:
    def test_high_complexity_goes_to_needs_human(self):
        entries = [CommentItem(
            id="t1", file="f.go", line=10, reviewer="alice",
            summary="refactor", classification="actionable_suggestion",
            verification="valid", complexity="high", state=ThreadState.NEW,
        )]
        result = triage_round.classify_entries(entries)
        assert len(result.fixable) == 0
        assert len(result.needs_human) == 1
        assert result.needs_human[0].reason == "complex"

    def test_low_complexity_stays_fixable(self):
        entries = [CommentItem(
            id="t1", file="f.go", line=10, reviewer="alice",
            summary="rename", classification="actionable_suggestion",
            verification="valid", complexity="low", state=ThreadState.NEW,
        )]
        result = triage_round.classify_entries(entries)
        assert len(result.fixable) == 1
        assert len(result.needs_human) == 0

    def test_medium_complexity_stays_fixable(self):
        entries = [CommentItem(
            id="t1", file="f.go", line=10, reviewer="alice",
            summary="add guard", classification="actionable_suggestion",
            verification="valid", complexity="medium", state=ThreadState.NEW,
        )]
        result = triage_round.classify_entries(entries)
        assert len(result.fixable) == 1
        assert len(result.needs_human) == 0

    def test_no_complexity_field_stays_fixable(self):
        entries = [CommentItem(
            id="t1", file="f.go", line=10, reviewer="alice",
            summary="fix", classification="actionable_suggestion",
            verification="valid", state=ThreadState.NEW,
        )]
        result = triage_round.classify_entries(entries)
        assert len(result.fixable) == 1
        assert len(result.needs_human) == 0


class TestFixableCountMatchesTheClassifier:
    """What the trail reports fixable is what the fix agent is handed.

    The count used to be a predicate written out beside the trail call —
    classification and verification, and nothing about complexity. The
    classifier routes a valid actionable suggestion of high complexity to a
    human, so that round was reported as having a fixable thread the pass never
    attempted, and the fix pass's own count disagreed with the trail's for the
    whole round.
    """

    def _entry(self, complexity, tid="t1"):
        return CommentItem(
            id=tid, file="f.go", line=10, reviewer="alice",
            summary="refactor", classification="actionable_suggestion",
            verification="valid", complexity=complexity, state=ThreadState.NEW,
        )

    def test_high_complexity_is_not_counted_fixable(self):
        assert _fixable_count([self._entry("high")]) == 0

    def test_low_complexity_is_counted(self):
        assert _fixable_count([self._entry("low")]) == 1

    def test_the_count_is_the_classifier_s_own(self):
        """Every shape at once, against the buckets the pass will actually use."""
        entries = [
            self._entry("high", tid="t1"),
            self._entry("low", tid="t2"),
            self._entry("medium", tid="t3"),
        ]
        assert _fixable_count(entries) == len(
            triage_round.classify_entries(entries).fixable)
        assert _fixable_count(entries) == 2

    def test_a_question_is_not_counted(self):
        entry = dataclasses.replace(self._entry("low"), classification="question")
        assert _fixable_count([entry]) == 0

    def test_a_contested_thread_is_not_counted(self):
        entry = dataclasses.replace(self._entry("low"), state=ThreadState.CONTESTED)
        assert _fixable_count([entry]) == 0


# ── already_addressed verification ─────────────────────────────────────────


class TestHoldIfSuperseded:
    """What the preflight's findings are allowed to do to this run.

    A hold, not the refusal `pr review` answers with: by the time this runs the
    triage pass is already paid for, so stopping saves nothing — what must not
    happen is asserting outward that superseded code was fixed. Detection
    itself is `supersession`'s, and tested there.
    """

    def test_evidence_shuts_the_gate(self, publishing_on):
        from core import publishing
        triage_round.hold_if_superseded(supersession_verdict(supersession_evidence()))
        assert publishing.enabled() is False
        assert "supersession signal" in publishing.held()

    def test_context_alone_leaves_it_open(self, publishing_on):
        """A rebase is how the problem becomes visible, not the problem."""
        from core import publishing
        triage_round.hold_if_superseded(supersession_verdict(supersession_context()))
        assert publishing.enabled() is True

    def test_nothing_found_says_nothing(self, publishing_on, capsys):
        triage_round.hold_if_superseded(supersession_verdict())
        assert capsys.readouterr().err == ""

    def test_the_output_names_the_signal_that_fired(self, publishing_on, capsys):
        triage_round.hold_if_superseded(supersession_verdict(
            supersession_context("replayed onto a moved base"),
            supersession_evidence("`foo` is gone from origin/main"),
        ))
        err = capsys.readouterr().err
        assert "[rebase_skew] replayed onto a moved base" in err
        assert "[readds_removed_symbol] `foo` is gone from origin/main" in err

    def test_the_hold_is_recorded_on_the_trail(self, publishing_on):
        trail = MagicMock()
        triage_round.hold_if_superseded(supersession_verdict(supersession_evidence()), trail)
        data = trail.decision.call_args.kwargs["data"]
        assert data["signals"] == [SupersessionKind.READDS_REMOVED_SYMBOL]


class TestHoldWhileContested:
    """Real fixes must not reach a branch a reviewer said should not land."""

    @staticmethod
    def _entry(reason, id="t1"):
        return CommentItem(id=id, file="f.go", line=10, reviewer="kgn",
                           summary="the root cause does not exist", reason=reason)

    def test_an_open_thread_shuts_the_gate(self, publishing_on):
        from core import publishing
        triage_round.hold_while_contested([self._entry("needs_discussion")])
        assert publishing.enabled() is False
        assert "1 thread(s)" in publishing.held()

    def test_nothing_contested_leaves_the_gate_alone(self, publishing_on):
        from core import publishing
        triage_round.hold_while_contested([])
        assert publishing.enabled() is True
        assert publishing.held() == ""

    def test_every_needs_human_reason_holds(self, publishing_on):
        """Contested, conflicting, question, complex — all route to needs_human.

        The halt is on the bucket, not the reason: distinguishing a
        premise-invalidating question from a bikeshed is the problem this
        deliberately does not try to solve.
        """
        from core import publishing
        triage_round.hold_while_contested([self._entry("complex")])
        assert publishing.enabled() is False

    def test_the_hold_is_recorded_on_the_trail(self, publishing_on):
        trail = MagicMock()
        triage_round.hold_while_contested(
            [self._entry("needs_discussion"), self._entry("question", id="t2")],
            trail,
        )
        trail.decision.assert_called_once()
        data = trail.decision.call_args.kwargs["data"]
        assert data["reasons"] == ["needs_discussion", "question"]


class TestTheRoundMergesItsTwoSides:
    """Threads and comment items stay apart where it matters and merge where it does not.

    Only a thread has somewhere to reply, so the two classifications are held
    separately and joined by the properties. Nothing here re-derives what the
    two `ClassificationResult`s already hold — a round that recomputed a bucket
    could disagree with the side it came from.
    """

    def _round(self, **kw):
        return triage_round.TriagedRound(**kw)

    def test_needs_human_is_threads_then_items(self):
        round_ = self._round(
            threads=ClassificationResult(needs_human=[CommentItem(id="t1")]),
            items=ClassificationResult(needs_human=[CommentItem(id="ic-1")]),
        )
        assert [e.id for e in round_.needs_human] == ["t1", "ic-1"]

    def test_dismissed_and_addressed_merge_the_same_way(self):
        round_ = self._round(
            threads=ClassificationResult(
                dismissed=[CommentItem(id="t1")],
                already_addressed=[CommentItem(id="t2")]),
            items=ClassificationResult(
                dismissed=[CommentItem(id="ic-1")],
                already_addressed=[CommentItem(id="ic-2")]),
        )
        assert [e.id for e in round_.dismissed] == ["t1", "ic-1"]
        assert [e.id for e in round_.already_addressed] == ["t2", "ic-2"]

    def test_the_fixable_sides_do_not_merge(self):
        """The agent is handed threads and items separately; the pass replies to one."""
        round_ = self._round(
            threads=ClassificationResult(fixable=[CommentItem(id="t1")]),
            items=ClassificationResult(fixable=[CommentItem(id="ic-1")]),
        )
        assert [e.id for e in round_.fixable] == ["t1"]
        assert [e.id for e in round_.fixable_items] == ["ic-1"]

    def test_an_empty_round_says_so(self):
        round_ = self._round()
        assert round_.has_fixables is False
        assert round_.has_items is False
        assert round_.needs_human == []
        assert round_.replies == ReplyOutcome()


class TestHasItemsCountsEveryBucket:
    """`has_comment_items` is persisted and read back on `--finish`.

    It decides whether the deferred render appends the raw comment sections: an
    item that is already a table row must not have its body repeated below the
    table. Every bucket counts, not only the fixable one — an item the round
    dismissed is a row like any other, and a rule naming one bucket is how a
    dismissed-only round came to render its comment twice.
    """

    @pytest.mark.parametrize("bucket", [
        "fixable", "needs_human", "dismissed", "already_addressed",
    ])
    def test_an_item_in_any_bucket_counts(self, bucket):
        round_ = triage_round.TriagedRound(
            items=ClassificationResult(**{bucket: [CommentItem(id="ic-1")]}))
        assert round_.has_items is True

    def test_threads_alone_do_not_count(self):
        """The flag is about decomposed comments, not about the round having rows."""
        round_ = triage_round.TriagedRound(
            threads=ClassificationResult(fixable=[CommentItem(id="t1")]))
        assert round_.has_items is False


class TestTheRoundIsMeasuredAgainstThePrsOwnThreads:
    """`has_unaccounted` compares the PR's open threads against what was disposed of."""

    def _triaged(self, *, triaged_ids, report_threads):
        entries = [
            CommentItem(id=tid, file="f.go", line=1, summary="s",
                        classification="actionable_suggestion",
                        verification="valid", complexity="low")
            for tid in triaged_ids
        ]
        report = PRReport(repo="owner/repo", pr_number=1, threads=report_threads)
        ctx = make_ctx(repo="owner/repo", pr_number=1)
        with patch.object(triage_round.supersession, "detect_cached",
                          return_value=supersession_verdict()), \
             patch.object(triage_round.git_topology, "default_branch_cached",
                          return_value="main"):
            return triage_round.triage_the_round(
                TriageResult(threads=entries), report, "/tmp/wt", ctx)

    def test_every_open_thread_disposed_of_is_accounted(self, publishing_on):
        round_ = self._triaged(
            triaged_ids=["t1"],
            report_threads=[ReportThread(id="t1", state=ThreadState.NEW)])
        assert round_.has_unaccounted is False

    def test_a_thread_the_round_never_saw_is_unaccounted(self, publishing_on):
        round_ = self._triaged(
            triaged_ids=["t1"],
            report_threads=[ReportThread(id="t1", state=ThreadState.NEW),
                            ReportThread(id="t2", state=ThreadState.NEW)])
        assert round_.has_unaccounted is True

    def test_a_resolved_thread_is_not_owed_a_disposition(self, publishing_on):
        """Already settled on GitHub — this round has nothing to say about it."""
        round_ = self._triaged(
            triaged_ids=["t1"],
            report_threads=[ReportThread(id="t1", state=ThreadState.NEW),
                            ReportThread(id="t2", state=ThreadState.RESOLVED)])
        assert round_.has_unaccounted is False

    def test_a_dropped_classification_leaves_the_thread_unaccounted(self, publishing_on):
        """An approval reaches no bucket, so the thread it was on stays open.

        The classifier drops anything that is not one of the four it routes,
        which is correct — there is nothing to do about "lgtm" — but the thread
        is still on the PR, and the summary this round publishes is partial
        until someone says otherwise.
        """
        entry = CommentItem(id="t1", classification="approval",
                            file="f.go", line=1, summary="lgtm")
        report = PRReport(repo="owner/repo", pr_number=1,
                          threads=[ReportThread(id="t1", state=ThreadState.NEW)])
        ctx = make_ctx(repo="owner/repo", pr_number=1)
        with patch.object(triage_round.supersession, "detect_cached",
                          return_value=supersession_verdict()), \
             patch.object(triage_round.git_topology, "default_branch_cached",
                          return_value="main"):
            round_ = triage_round.triage_the_round(
                TriageResult(threads=[entry]), report, "/tmp/wt", ctx)
        assert round_.has_unaccounted is True


class TestTheHoldsArePlacedBeforeTheRoundExists:
    """The ordering that used to be two calls sitting near each other.

    `publishing.hold()` flips `publishing.enabled()`, and the fix pass reads
    that flag afterwards to decide whether the replies it rendered are still
    owed. Placed after that read, the queue says a drafted reply went out.
    There is no `TriagedRound` that predates its own holds, so the ordering is
    the constructor's rather than a caller's to remember.
    """

    def _triage(self, entries, *, verdict=None):
        report = PRReport(repo="owner/repo", pr_number=1)
        ctx = make_ctx(repo="owner/repo", pr_number=1)
        with patch.object(triage_round.supersession, "detect_cached",
                          return_value=verdict or supersession_verdict()), \
             patch.object(triage_round.git_topology, "default_branch_cached",
                          return_value="main"):
            return triage_round.triage_the_round(
                TriageResult(threads=entries), report, "/tmp/wt", ctx)

    def test_a_contested_entry_holds_before_the_round_is_returned(self, publishing_on):
        from core import publishing
        self._triage([CommentItem(id="t1", state=ThreadState.CONTESTED)])
        assert publishing.enabled() is False

    def test_supersession_evidence_holds_too(self, publishing_on):
        from core import publishing
        self._triage([], verdict=supersession_verdict(supersession_evidence()))
        assert publishing.enabled() is False

    def test_a_clean_round_leaves_the_gate_open(self, publishing_on):
        """Pairs with the two above: proves those assertions are not vacuous."""
        from core import publishing
        entry = CommentItem(id="t1", classification="actionable_suggestion",
                            verification="valid", complexity="low",
                            file="f.go", line=1, summary="s")
        round_ = self._triage([entry])
        assert publishing.enabled() is True
        assert round_.has_fixables is True

    def test_an_item_side_contest_holds_as_well(self, publishing_on):
        """Both sides' needs-human feed the hold — an item can contest too."""
        from core import publishing
        report = PRReport(repo="owner/repo", pr_number=1)
        ctx = make_ctx(repo="owner/repo", pr_number=1)
        item = CommentItem(id="ic-1", classification="question",
                           file="f.go", line=1, summary="why?")
        with patch.object(triage_round.supersession, "detect_cached",
                          return_value=supersession_verdict()), \
             patch.object(triage_round.git_topology, "default_branch_cached",
                          return_value="main"):
            triage_round.triage_the_round(
                TriageResult(comment_items=[item]), report, "/tmp/wt", ctx)
        assert publishing.enabled() is False


class TestEveryDispositionCountsAsAccounted:
    """All four buckets answer for their thread, not just the ones with work in them.

    A thread the round dismissed, or found the code already satisfies, has been
    accounted for as squarely as one the agent will fix — the round has
    something to say about it either way. A rule that named only the buckets
    carrying pending work would leave a settled round reporting itself as
    partial, and `summary_still_owed` would keep re-rendering a table that is
    already complete.
    """

    @pytest.mark.parametrize("bucket", [
        "fixable", "needs_human", "dismissed", "already_addressed",
    ])
    def test_an_entry_in_any_bucket_is_accounted(self, bucket):
        result = ClassificationResult(**{bucket: [CommentItem(id="t1")]})
        assert result.ids() == {"t1"}

    def test_an_empty_classification_accounts_for_nothing(self):
        assert ClassificationResult().ids() == set()

    def test_both_sides_ids_answer_for_the_round(self):
        """A comment item's disposition accounts for it as a thread's does."""
        threads = ClassificationResult(dismissed=[CommentItem(id="t1")])
        items = ClassificationResult(already_addressed=[CommentItem(id="ic-1")])
        assert threads.ids() | items.ids() == {"t1", "ic-1"}
