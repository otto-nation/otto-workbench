"""pr.thread_models: triage results, entries and tracking merges."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from review_threads_support import _no_published_summary  # noqa: E402
from pr.fix import FixOutcome, ItemOutcome, SettledBy
from pr.thread_models import CommentItem, TrackingResult, TriageStats, triage_result_from_dict


# ── triage_result_from_dict ──────────────────────────────────────────────

class TestTriageResultFromDict:
    """The AI is the input class that is malformed occasionally by nature —
    this must degrade a wrong-shaped field rather than crash the whole
    triage pass, the same as it did before `serde.from_dict` started
    rejecting non-dict input outright.
    """

    def test_a_non_dict_stats_value_degrades_to_default_stats(self):
        result = triage_result_from_dict({
            "threads": [{"id": "t1", "summary": "ok"}],
            "stats": [],
        })
        assert result.stats == TriageStats()
        assert result.threads == [CommentItem(id="t1", summary="ok")]

    def test_a_non_dict_thread_entry_degrades_to_a_default_item(self):
        result = triage_result_from_dict({
            "threads": ["not-a-dict", {"id": "t2", "summary": "real"}],
        })
        assert result.threads == [CommentItem(), CommentItem(id="t2", summary="real")]

    def test_a_non_dict_comment_item_degrades_to_a_default_item(self):
        result = triage_result_from_dict({"comment_items": [0]})
        assert result.comment_items == [CommentItem()]

    def test_an_explicit_null_list_degrades_to_no_entries(self):
        """`d.get(key, [])` only defaults on an absent key, not a null one."""
        result = triage_result_from_dict({"threads": None, "comment_items": None})
        assert result.threads == []
        assert result.comment_items == []

    def test_a_scalar_where_a_list_belongs_degrades_to_no_entries(self):
        result = triage_result_from_dict({"threads": "t1", "comment_items": 7})
        assert result.threads == []
        assert result.comment_items == []

    def test_a_null_stats_value_degrades_to_default_stats(self):
        assert triage_result_from_dict({"stats": None}).stats == TriageStats()

    def test_well_formed_input_is_unaffected(self):
        result = triage_result_from_dict({
            "threads": [{"id": "t1"}],
            "comment_items": [{"id": "c1"}],
            "stats": {"total": 3, "actionable": 2},
        })
        assert result.threads == [CommentItem(id="t1")]
        assert result.comment_items == [CommentItem(id="c1")]
        assert result.stats.total == 3
        assert result.stats.actionable == 2

    def test_an_invented_enum_value_degrades_to_a_default_item(self):
        """The entry's own fields are enums now, and serde raises on a bad one.

        Neither key is in the triage schema, so a model emitting one has made
        it up — and one invented field must not take the whole batch down.
        """
        result = triage_result_from_dict({
            "threads": [{"id": "t1", "settled_by": "the-reviewer"},
                        {"id": "t2", "summary": "real"}],
        })
        assert result.threads == [CommentItem(), CommentItem(id="t2", summary="real")]


class TestAnEntryAndAnOutcomeAreInverses:
    """What goes out through `to_outcome` comes back through `from_outcome`.

    The verdict and its provenance are the pair a renderer reads before it
    credits the running pass's commit for a row, so a drain that dropped either
    would leave the reply and the summary reasoning from different records.
    """

    def test_the_verdict_and_its_provenance_survive_the_round_trip(self):
        outcome = ItemOutcome(
            id="t1", file="a.py", line=7, summary="fix it",
            outcome=FixOutcome.SETTLED_ELSEWHERE,
            settled_by=SettledBy.RECONCILIATION,
            reason="reconciled: handled outside the fix pass",
            commit_sha="abc1234", read_sha="def5678",
        )
        assert CommentItem.from_outcome(outcome).to_outcome() == outcome

    def test_whether_the_fix_was_exercised_survives_the_round_trip(self):
        """An unverified fix that replays as verified is the claim the gate exists to stop.

        `--finish` renders replies out of state rather than out of the pass that
        wrote them, so a field the drain drops is a field the published reply
        has to guess at — and the confident reading is the wrong one.
        """
        outcome = ItemOutcome(
            id="t1", summary="fix it", outcome=FixOutcome.FIXED,
            verified=True, verify_detail="suite green",
        )
        assert CommentItem.from_outcome(outcome).to_outcome() == outcome

    def test_an_entry_no_gate_saw_claims_nothing_either_way(self):
        """None, not False: "nobody asked" is not "nobody could tell".

        Recording the second for the first would hedge every row a gate-less
        pass renders, which is every row on every PR until the gate is turned
        on for that pass.
        """
        recorded = CommentItem(id="t1", summary="fix it").to_outcome()
        assert recorded.verified is None
        assert recorded.verify_detail == ""

    def test_the_gate_having_run_and_failed_to_tell_survives_the_round_trip(self):
        outcome = ItemOutcome(
            id="t1", summary="fix it", outcome=FixOutcome.FIXED,
            verified=False, verify_detail="no runnable check",
        )
        assert CommentItem.from_outcome(outcome).to_outcome() == outcome

    def test_an_entry_nobody_decided_records_as_still_owed(self):
        """An entry triage just built carries no verdict, and defaults to owed."""
        recorded = CommentItem(id="t1", summary="fix it").to_outcome()
        assert recorded.outcome is FixOutcome.DEFERRED
        assert recorded.settled_by is SettledBy.PASS

    def test_an_explicit_verdict_outranks_the_one_the_entry_carries(self):
        entry = CommentItem.from_outcome(
            ItemOutcome(id="t1", outcome=FixOutcome.DEFERRED),
        )
        assert entry.to_outcome(FixOutcome.FIXED).outcome is FixOutcome.FIXED


class TestMergeTracking:
    def test_batch_results_accumulate(self):
        total = TrackingResult(
            threads={FixOutcome.FIXED: ["a"]}, items={FixOutcome.DEFERRED: ["z"]},
        )
        total.merge(TrackingResult(
            threads={FixOutcome.FIXED: ["b"], FixOutcome.DEFERRED: ["c"]},
        ))
        assert total.bucket(FixOutcome.FIXED) == ["a", "b"]
        assert total.bucket(FixOutcome.DEFERRED) == ["c"]
        assert total.bucket(FixOutcome.DEFERRED, item=True) == ["z"]

    def test_a_merge_does_not_alias_the_source_s_lists(self):
        """A batch merged into an empty total must not hand over its own list."""
        batch = TrackingResult(threads={FixOutcome.FIXED: ["a"]})
        total = TrackingResult()
        total.merge(batch)
        total.add(FixOutcome.FIXED, "b")
        assert batch.bucket(FixOutcome.FIXED) == ["a"]

    def test_dropping_an_outcome_forgets_threads_and_items_alike(self):
        total = TrackingResult(
            threads={FixOutcome.DEFERRED: ["a"], FixOutcome.FIXED: ["k"]},
            items={FixOutcome.DEFERRED: ["z"]},
        )
        total.drop(FixOutcome.DEFERRED)
        assert total.both(FixOutcome.DEFERRED) == []
        assert total.bucket(FixOutcome.FIXED) == ["k"]
