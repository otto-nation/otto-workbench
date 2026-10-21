"""Tests for `review.verdict` — the open-finding tally, the call a review
reaches, and the body a review has when no agent wrote one.

Absent and empty are the distinction these turn on — a review nobody wrote
reaches no verdict, while one written with nothing in it approves.
"""

import sys
from pathlib import Path

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)
import pytest
from core.phases import Mode
from pr.domains import ReviewStatus, ReviewVerdict
from review.document import ReviewDocument, ReviewHeader
from review.record import Disposition, LedgerLine
from review.types import PriorDisposition
from review.verdict import (
    CLEAN_VERDICT, MECHANICAL_NOTE,
    counts_prose, mechanical_verdict,
    open_counts, resolve_review_verdict, states_verdict, verdict_from_counts,
)

from review_document_support import _write
from review_record_support import finding, record, store_record


def _doc(*findings, status=None, **fields) -> ReviewDocument:
    header = ReviewHeader(status=status) if status is not None else ReviewHeader()
    return ReviewDocument(header=header, record=record(*findings, **fields))


class TestOpenCounts:
    def test_every_severity_is_counted(self):
        document = _doc(
            finding("M1", "description", path=""),
            finding("M2", "description", path=""),
            finding("S1", "description", path=""),
        )
        assert document.open_counts == {"M": 2, "S": 1, "N": 0, "I": 0}

    def test_a_resolved_finding_is_no_longer_counted(self):
        document = _doc(
            finding("M1", "active", path=""),
            finding("M2", "resolved", path="", disposition=Disposition.FALSIFIED),
        )
        assert document.open_counts["M"] == 1

    def test_the_fix_passes_checkbox_does_not_hide_a_finding(self):
        document = _doc(
            finding("M1", "with checkbox", path="", checkbox=True),
            finding("M2", "without checkbox", path=""),
        )
        assert document.open_counts["M"] == 2

    def test_a_finding_the_fix_pass_ticked_off_is_not_open(self):
        document = _doc(
            finding("M1", "fixed", path="", checkbox=True, disposition=Disposition.FIXED),
            finding("M2", "still open", path="", checkbox=True),
        )
        assert [f.id for f in document.open_findings] == ["M2"]
        assert document.open_counts["M"] == 1

    def test_a_declined_finding_is_still_open(self):
        """Wider than the fix pass's predicate: the review judged this one, so
        it is not work, but nothing fixed it either."""
        document = _doc(finding(
            "M1", "", path="",
            disposition=Disposition.DECLINED, reason="by design",
        ))
        assert [f.id for f in document.open_findings] == ["M1"]
        assert document.open_counts["M"] == 1

    def test_a_finding_line_outside_a_severity_section_is_not_counted(self):
        """The prior-findings ledger declares nothing — it reports on the last
        review, and counting it inflated every tally taken over the whole body."""
        document = _doc(
            finding("M1", "bug", path=""),
            ledger=(
                LedgerLine("M1", PriorDisposition.FIXED, "old.go"),
                LedgerLine("S1", PriorDisposition.FIXED, "old.go"),
            ),
        )
        assert document.open_counts == {"M": 1, "S": 0, "N": 0, "I": 0}

    def test_two_findings_sharing_an_id_are_two_findings(self):
        """A tally over the parse counts declarations, not distinct IDs: a
        duplicate is a merge bug to see, not one to hide."""
        document = _doc(
            finding("M1", "one", path="a.py", line=1),
            finding("M1", "two", path="b.py", line=2),
        )
        assert document.open_counts["M"] == 2

    def test_a_document_declaring_nothing_is_zeroed_not_empty(self):
        """Callers index the result directly, so every key must be present."""
        assert ReviewDocument().open_counts == {"M": 0, "S": 0, "N": 0, "I": 0}

    def test_a_review_that_was_never_written_counts_as_one_that_found_nothing(self):
        """The reader that has no separate answer for absent, unlike the
        verdict below."""
        assert open_counts(None) == {"M": 0, "S": 0, "N": 0, "I": 0}
        document = _doc(finding("M1", "bug", path="a.py", line=1))
        assert open_counts(document) == document.open_counts


class TestVerdict:
    @pytest.mark.parametrize("prose,expected", [
        ("Approve", ReviewVerdict.APPROVE),
        ("**Needs discussion**", ReviewVerdict.NEEDS_DISCUSSION),
        ("Request changes", ReviewVerdict.CHANGES_REQUESTED),
        ("Disapprove", ReviewVerdict.DISAPPROVE),
        ("disapprove", ReviewVerdict.DISAPPROVE),
    ])
    def test_the_verdict_section_is_read_however_it_is_worded(self, prose, expected):
        document = _doc(verdict=f"{prose} — rationale.")
        assert document.verdict is expected

    def test_wording_that_states_no_verdict_states_none(self):
        assert _doc(verdict="Looks fine to me.").verdict is None

    def test_a_document_with_no_verdict_section_states_none(self):
        assert _doc(finding("M1", "bug", path=""), summary="Some findings.").verdict is None

    def test_a_verdict_word_outside_the_section_is_not_the_verdict(self):
        """Prose elsewhere describes the review; only the Verdict section
        declares one."""
        assert _doc(summary="I would approve this once the tests land.").verdict is None


class TestStatesVerdict:
    """The one reading of which runs state a verdict at all.

    Both the resolver and the mechanically merged body ask this rather than
    testing the mode for themselves, so a self-review cannot state a verdict
    in one place and withhold it in the other.
    """

    def test_a_pr_review_states_one(self):
        assert states_verdict(Mode.PR)

    def test_a_self_review_does_not(self):
        assert not states_verdict(Mode.SELF)

    def test_a_review_whose_metadata_named_no_mode_still_states_one(self):
        """A missing mode is not a claim that the review had no PR."""
        assert states_verdict(None)


class TestResolveVerdict:
    def test_the_counts_speak_when_the_prose_does_not(self):
        document = _doc(finding("S1", "improvement", path="a.py", line=1))
        assert resolve_review_verdict(document) is ReviewVerdict.NEEDS_DISCUSSION

    def test_prose_cannot_under_report_blocking_findings(self):
        document = _doc(
            finding("M1", "bug", path="a.py", line=1),
            verdict="Approve — looks fine.",
        )
        assert resolve_review_verdict(document) is ReviewVerdict.CHANGES_REQUESTED

    def test_counts_cannot_discard_a_stronger_call(self):
        document = _doc(
            finding("N1", "style", path="a.py", line=1),
            verdict="Request changes — rework it.",
        )
        assert resolve_review_verdict(document) is ReviewVerdict.CHANGES_REQUESTED

    def test_disapprove_survives_any_counts(self):
        document = _doc(verdict="Disapprove — wrong approach.")
        assert resolve_review_verdict(document) is ReviewVerdict.DISAPPROVE

    def test_a_self_review_is_advisory(self):
        document = _doc(finding("M1", "bug", path="a.py", line=1))
        assert resolve_review_verdict(document, mode=Mode.SELF) is None

    def test_a_self_review_still_reports_disapprove(self):
        """Disapprove judges the approach, which holds with or without a PR."""
        document = _doc(verdict="Disapprove — wrong approach.")
        assert resolve_review_verdict(document, mode=Mode.SELF) is ReviewVerdict.DISAPPROVE

    def test_a_review_that_was_never_written_reaches_no_verdict(self):
        assert resolve_review_verdict(None) is None

    def test_a_review_that_found_nothing_approves(self):
        """The other half of the distinction above: an empty document is a
        review that ran and had nothing to say."""
        assert resolve_review_verdict(ReviewDocument()) is ReviewVerdict.APPROVE

    def test_a_review_where_nothing_ran_reaches_no_verdict(self):
        """An empty tally under `status: error` is no agent reading anything.

        Counted as a review that found nothing, it was reported as `approve`
        by `--json-summary` and the listing alongside `status: error`.
        """
        document = _doc(summary="No group reported.", status=ReviewStatus.ERROR)
        assert resolve_review_verdict(document) is None

    def test_a_partial_review_still_reaches_one(self):
        """The findings the surviving groups reported are real and still count."""
        document = _doc(
            finding("M1", "bug", path="a.py", line=1),
            status=ReviewStatus.PARTIAL,
        )
        assert resolve_review_verdict(document) is ReviewVerdict.CHANGES_REQUESTED

    def test_the_counts_come_from_the_document_it_was_handed(self, tmp_path):
        """One document, read once — the verdict cannot be resolved against
        counts from a file the caller re-read in between."""
        document = ReviewDocument.read(
            store_record(
                tmp_path / "review.md",
                record(
                    finding("S1", "improvement", path="a.py", line=1),
                    verdict="Approve — fine.",
                ),
            ),
        )
        assert resolve_review_verdict(document) is ReviewVerdict.NEEDS_DISCUSSION


class TestVerdictFromCounts:
    def test_a_must_fix_blocks(self):
        assert verdict_from_counts({"M": 1, "S": 0}) is ReviewVerdict.CHANGES_REQUESTED

    def test_a_should_fix_opens_a_discussion(self):
        assert verdict_from_counts({"M": 0, "S": 2}) is ReviewVerdict.NEEDS_DISCUSSION

    def test_nothing_blocking_approves(self):
        assert verdict_from_counts({"N": 3}) is ReviewVerdict.APPROVE

    def test_a_severity_the_tally_omits_counts_as_none(self):
        """A partial tally is read rather than refused — the mechanical paths
        hand this whatever counts they have."""
        assert verdict_from_counts({}) is ReviewVerdict.APPROVE


class TestCountsProse:
    def test_severities_read_out_in_order(self):
        assert counts_prose({"M": 2, "S": 1, "N": 1}) == "2 must-fix, 1 should-fix, 1 nit"

    def test_a_severity_with_none_is_left_out(self):
        assert counts_prose({"M": 0, "S": 1}) == "1 should-fix"

    def test_an_empty_tally_reads_as_nothing(self):
        assert counts_prose({"M": 0, "S": 0, "N": 0, "I": 0}) == ""


# ── The body a review has when no agent wrote one ───────────────────────────


class TestMechanicalVerdict:
    def test_must_fix_present(self):
        assert mechanical_verdict({"M": 2, "S": 1, "N": 0, "I": 0}).startswith(
            "Request changes"
        )

    def test_should_fix_no_must(self):
        assert mechanical_verdict({"M": 0, "S": 3, "N": 1, "I": 0}).startswith(
            "Needs discussion"
        )

    def test_nits_and_idioms_only(self):
        result = mechanical_verdict({"M": 0, "S": 0, "N": 2, "I": 1})
        assert result.startswith("Approve")
        assert "2 nit" in result
        assert "1 idiom" in result

    def test_no_findings(self):
        assert "no findings" in mechanical_verdict({"M": 0, "S": 0, "N": 0, "I": 0})

    def test_zero_counts_for_some(self):
        result = mechanical_verdict({"M": 1, "S": 0, "N": 0, "I": 0})
        assert "Request changes" in result
        assert "1 must-fix" in result
        assert "should-fix" not in result

    def test_the_note_says_no_agent_reached_this(self):
        """The pipeline reads it back to record that synthesis did not run."""
        assert MECHANICAL_NOTE in mechanical_verdict({"M": 1})
        assert MECHANICAL_NOTE in mechanical_verdict({})
