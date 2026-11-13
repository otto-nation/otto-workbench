"""Tests for `review.verdict`: open counts, the derived call, and counts prose.

The verdict follows the open findings, the run's status and its mode: a
self-review or errored run states none, a partial run or incomplete coverage
states no approval.
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
from review.types import CoverageGap, PriorDisposition, ReviewMeta, SEVERITIES, review_meta_from_dict
from review.verdict import counts_prose, open_counts, resolve_review_verdict, verdict_line

from review_document_support import _write
from review_record_support import finding, record

_PR_DONE = ReviewMeta(mode=Mode.PR, status=ReviewStatus.COMPLETED)
_PR_PARTIAL = ReviewMeta(mode=Mode.PR, status=ReviewStatus.PARTIAL)
_GAP = CoverageGap(kind="untriaged", subject="ui-console/CLAUDE.md")
_PR_GAPS = ReviewMeta(mode=Mode.PR, status=ReviewStatus.COMPLETED, coverage_gaps=(_GAP,))


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
            record(finding("S1", "smell", path="")),
            _PR_DONE,
        ) is ReviewVerdict.NEEDS_DISCUSSION
        assert resolve_review_verdict(
            record(finding("N1", "nit", path="")),
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

    def test_incomplete_coverage_withholds_approve(self):
        assert resolve_review_verdict(record(), _PR_GAPS) is None

    def test_incomplete_coverage_keeps_a_blocking_verdict(self):
        assert resolve_review_verdict(
            record(finding("S1", "smell", path="")), _PR_GAPS,
        ) is ReviewVerdict.NEEDS_DISCUSSION

    def test_self_mode_with_gaps_still_states_none(self):
        assert resolve_review_verdict(
            record(),
            ReviewMeta(
                mode=Mode.SELF, status=ReviewStatus.COMPLETED, coverage_gaps=(_GAP,),
            ),
        ) is None

    def test_an_older_sidecar_without_coverage_gaps_still_approves(self):
        meta = review_meta_from_dict({"mode": "pr", "status": "completed"})
        assert meta.coverage_gaps == ()
        assert resolve_review_verdict(record(), meta) is ReviewVerdict.APPROVE


class TestVerdictLine:
    def test_incomplete_coverage_states_approve_withheld(self):
        line = verdict_line(record(), _PR_GAPS)
        assert line.startswith("**No verdict.** Approve withheld — incomplete coverage:")
        assert "(ui-console/CLAUDE.md)" in line
        assert "Verdict: Approve" not in line

    def test_a_blocking_verdict_keeps_its_line_and_names_the_gaps(self):
        line = verdict_line(
            record(finding("S1", "smell", path="")), _PR_GAPS,
        )
        assert line.startswith("**Verdict: Needs discussion.**")
        assert "Coverage incomplete: no triage entry for 1 changed file (ui-console/CLAUDE.md)" in line
        assert "Approve" not in line

    def test_a_partial_clean_run_keeps_its_line_and_names_the_gaps(self):
        line = verdict_line(
            record(),
            ReviewMeta(
                mode=Mode.PR, status=ReviewStatus.PARTIAL, coverage_gaps=(_GAP,),
            ),
        )
        assert line.startswith("**No verdict.**")
        assert "cannot vouch that nothing blocks" in line
        assert "Coverage incomplete:" in line
        assert "Verdict: Approve" not in line

    def test_self_mode_with_gaps_states_no_line(self):
        assert verdict_line(
            record(),
            ReviewMeta(
                mode=Mode.SELF, status=ReviewStatus.COMPLETED, coverage_gaps=(_GAP,),
            ),
        ) == ""


class TestCountsProse:
    def test_severities_read_out_in_order(self):
        assert counts_prose({"M": 2, "S": 1, "N": 1}) == "2 must-fix, 1 should-fix, 1 nit"

    def test_a_severity_with_none_is_left_out(self):
        assert counts_prose({"M": 0, "S": 1}) == "1 should-fix"

    def test_an_empty_tally_reads_as_nothing(self):
        assert counts_prose({"M": 0, "S": 0, "N": 0, "I": 0}) == ""


