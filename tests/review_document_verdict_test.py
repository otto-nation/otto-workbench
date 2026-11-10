"""Tests for `review.verdict` — the open-finding tally, the call a review
reaches, and the body a review has when no agent wrote one.

Absent and empty are the distinction these turn on — a review nobody wrote
reaches no verdict, while one written with nothing in it approves.
"""

from collections import Counter
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
from review.types import PriorDisposition, ReviewMeta, SEVERITIES
from review.verdict import (
    CLEAN_VERDICT, MECHANICAL_NOTE,
    counts_prose, mechanical_verdict,
    open_counts, resolve_review_verdict, states_verdict, verdict_from_counts,
)

from review_document_support import _write
from review_record_support import finding, record

_PR_DONE = ReviewMeta(mode=Mode.PR, status=ReviewStatus.COMPLETED)
_PR_PARTIAL = ReviewMeta(mode=Mode.PR, status=ReviewStatus.PARTIAL)


def _doc(*findings, status=None, **fields) -> ReviewDocument:
    header = ReviewHeader(status=status) if status is not None else ReviewHeader()
    return ReviewDocument(header=header, record=record(*findings, **fields))


def _resolved(severity: str, disposition: Disposition) -> ReviewVerdict:
    if severity in ("N", "I") or disposition in (Disposition.FIXED, Disposition.FALSIFIED):
        return ReviewVerdict.APPROVE
    if severity == "M":
        return ReviewVerdict.CHANGES_REQUESTED
    return ReviewVerdict.NEEDS_DISCUSSION


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
    @pytest.mark.parametrize(
        "severity,disposition,expected",
        [
            (sev, disposition, _resolved(sev, disposition))
            for sev in ("M", "S", "N", "I")
            for disposition in Disposition
        ],
    )
    def test_completed_pr_verdict_follows_open_must_and_should(
        self, severity, disposition, expected,
    ):
        rec = record(finding(
            f"{severity}1", "x", path="",
            disposition=disposition,
        ))
        assert resolve_review_verdict(rec, _PR_DONE) is expected

    @pytest.mark.parametrize("findings,expected", [
        ((finding("M1", "bug", path=""),), ReviewVerdict.CHANGES_REQUESTED),
        ((finding("S1", "smell", path=""),), ReviewVerdict.NEEDS_DISCUSSION),
        ((finding("N1", "nit", path=""), finding("I1", "idiom", path="")), None),
        ((finding("M1", "bug", path="", disposition=Disposition.FIXED),), None),
        ((), None),
    ])
    def test_a_partial_run_states_only_a_blocking_verdict(self, findings, expected):
        assert resolve_review_verdict(record(*findings), _PR_PARTIAL) is expected

    def test_error_and_self_reviews_state_none(self):
        blocker = record(finding("M1", "bug", path=""))
        assert resolve_review_verdict(
            blocker, ReviewMeta(mode=Mode.PR, status=ReviewStatus.ERROR),
        ) is None
        assert resolve_review_verdict(
            blocker, ReviewMeta(mode=Mode.SELF, status=ReviewStatus.COMPLETED),
        ) is None
        assert resolve_review_verdict(
            record(), ReviewMeta(mode=Mode.PR, status=ReviewStatus.ERROR),
        ) is None
        assert resolve_review_verdict(
            blocker, ReviewMeta(mode=None, status=None),
        ) is ReviewVerdict.CHANGES_REQUESTED

    def test_the_agent_s_prose_is_not_read(self):
        assert resolve_review_verdict(
            record(
                finding("S1", "smell", path=""),
                verdict="Approve. Every remaining finding is minor.",
            ),
            _PR_DONE,
        ) is ReviewVerdict.NEEDS_DISCUSSION
        assert resolve_review_verdict(
            record(finding("N1", "nit", path=""), verdict="Request changes"),
            _PR_DONE,
        ) is ReviewVerdict.APPROVE

    def test_counts_and_verdict_read_one_predicate(self):
        rec = record(
            finding("M1", "open", path=""),
            finding("M2", "fixed", path="", disposition=Disposition.FIXED),
            finding("S1", "declined", path="", disposition=Disposition.DECLINED),
            finding("S2", "skipped", path="", disposition=Disposition.SKIPPED),
            finding("N1", "unreached", path="", disposition=Disposition.UNREACHED),
            finding("I1", "falsified", path="", disposition=Disposition.FALSIFIED),
        )
        tally = Counter(f.severity for f in rec.unfixed)
        assert rec.open_counts == {s.key: tally[s.key] for s in SEVERITIES}
        assert rec.unfixed == (
            rec.findings[0], rec.findings[2], rec.findings[3], rec.findings[4],
        )


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
