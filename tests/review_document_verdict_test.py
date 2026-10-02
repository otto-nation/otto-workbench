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
from pr.domains import ReviewVerdict
from review.document import ReviewDocument
from review.verdict import (
    CLEAN_VERDICT, MECHANICAL_NOTE,
    build_mechanical_body, counts_prose, mechanical_verdict,
    open_counts, resolve_review_verdict, states_verdict, verdict_from_counts,
)

from review_document_support import _write


class TestOpenCounts:
    def test_every_severity_is_counted(self):
        document = ReviewDocument.parse(
            "## Must fix\n"
            "- **[M1]** path:1 — description\n"
            "- **[M2]** path:2 — description\n"
            "## Should fix\n"
            "- **[S1]** path:3 — description\n"
        )
        assert document.open_counts == {"M": 2, "S": 1, "N": 0, "I": 0}

    def test_a_resolved_finding_is_no_longer_counted(self):
        document = ReviewDocument.parse(
            "## Must fix\n"
            "- **[M1]** path:1 — active\n"
            "- ~~**[M2]** path:2 — resolved~~\n"
        )
        assert document.open_counts["M"] == 1

    def test_the_fix_passes_checkbox_does_not_hide_a_finding(self):
        document = ReviewDocument.parse(
            "## Must fix\n"
            "- [ ] **[M1]** path:1 — with checkbox\n"
            "- **[M2]** path:2 — without checkbox\n"
        )
        assert document.open_counts["M"] == 2

    def test_a_finding_the_fix_pass_ticked_off_is_not_open(self):
        document = ReviewDocument.parse(
            "## Must fix\n"
            "- [x] **[M1]** path:1 — fixed\n"
            "- [ ] **[M2]** path:2 — still open\n"
        )
        assert [f.id for f in document.open_findings] == ["M2"]
        assert document.open_counts["M"] == 1

    def test_a_declined_finding_is_still_open(self):
        """Wider than the fix pass's predicate: the review judged this one, so
        it is not work, but nothing fixed it either."""
        document = ReviewDocument.parse(
            "## Must fix\n"
            "- **[M1]** path:1 — *(declined — by design)*\n"
        )
        assert [f.id for f in document.open_findings] == ["M1"]
        assert document.open_counts["M"] == 1

    def test_an_indented_finding_is_counted(self):
        document = ReviewDocument.parse(
            "## Must fix\n"
            "  - **[M1]** path:1 — indented under something\n"
        )
        assert document.open_counts["M"] == 1

    def test_a_finding_line_outside_a_severity_section_is_not_counted(self):
        """The prior-findings ledger declares nothing — it reports on the last
        review, and counting it inflated every tally taken over the whole body."""
        document = ReviewDocument.parse(
            "## Must fix\n"
            "- **[M1]** path:1 — bug\n"
            "## Prior findings\n"
            "- **[M1]** `old.go` — Fixed\n"
            "- **[S1]** `old.go` — Fixed\n"
        )
        assert document.open_counts == {"M": 1, "S": 0, "N": 0, "I": 0}

    def test_two_findings_sharing_an_id_are_two_findings(self):
        """A tally over the parse counts declarations, not distinct IDs: a
        duplicate is a merge bug to see, not one to hide."""
        document = ReviewDocument.parse(
            "## Must fix\n"
            "- **[M1]** a.py:1 — one\n"
            "- **[M1]** b.py:2 — two\n"
        )
        assert document.open_counts["M"] == 2

    def test_a_document_declaring_nothing_is_zeroed_not_empty(self):
        """Callers index the result directly, so every key must be present."""
        assert ReviewDocument().open_counts == {"M": 0, "S": 0, "N": 0, "I": 0}

    def test_a_review_that_was_never_written_counts_as_one_that_found_nothing(self):
        """The reader that has no separate answer for absent, unlike the
        verdict below."""
        assert open_counts(None) == {"M": 0, "S": 0, "N": 0, "I": 0}
        document = ReviewDocument.parse("## Must fix\n- **[M1]** a.py:1 — bug\n")
        assert open_counts(document) == document.open_counts

    def test_a_reference_to_a_finding_is_not_a_second_finding(self):
        document = ReviewDocument.parse(
            "## Must fix\n"
            "- **[M1]** path:1 — bug\n"
            "  - see [M1] above\n"
        )
        assert document.open_counts["M"] == 1


class TestVerdict:
    @pytest.mark.parametrize("prose,expected", [
        ("Approve", ReviewVerdict.APPROVE),
        ("**Needs discussion**", ReviewVerdict.NEEDS_DISCUSSION),
        ("Request changes", ReviewVerdict.CHANGES_REQUESTED),
        ("Disapprove", ReviewVerdict.DISAPPROVE),
        ("disapprove", ReviewVerdict.DISAPPROVE),
    ])
    def test_the_verdict_section_is_read_however_it_is_worded(self, prose, expected):
        document = ReviewDocument.parse(f"## Verdict\n{prose} — rationale.\n")
        assert document.verdict is expected

    def test_wording_that_states_no_verdict_states_none(self):
        assert ReviewDocument.parse("## Verdict\nLooks fine to me.\n").verdict is None

    def test_a_document_with_no_verdict_section_states_none(self):
        text = "## Summary\nSome findings.\n## Must fix\n- **[M1]** a:1 — bug\n"
        assert ReviewDocument.parse(text).verdict is None

    def test_a_verdict_word_outside_the_section_is_not_the_verdict(self):
        """Prose elsewhere describes the review; only the Verdict section
        declares one."""
        text = "## Summary\nI would approve this once the tests land.\n"
        assert ReviewDocument.parse(text).verdict is None


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
        document = ReviewDocument.parse("## Should fix\n- **[S1]** a.py:1 — improvement\n")
        assert resolve_review_verdict(document) is ReviewVerdict.NEEDS_DISCUSSION

    def test_prose_cannot_under_report_blocking_findings(self):
        document = ReviewDocument.parse(
            "## Must fix\n- **[M1]** a.py:1 — bug\n\n## Verdict\nApprove — looks fine.\n",
        )
        assert resolve_review_verdict(document) is ReviewVerdict.CHANGES_REQUESTED

    def test_counts_cannot_discard_a_stronger_call(self):
        document = ReviewDocument.parse(
            "## Nit\n- **[N1]** a.py:1 — style\n\n## Verdict\nRequest changes — rework it.\n",
        )
        assert resolve_review_verdict(document) is ReviewVerdict.CHANGES_REQUESTED

    def test_disapprove_survives_any_counts(self):
        document = ReviewDocument.parse("## Verdict\nDisapprove — wrong approach.\n")
        assert resolve_review_verdict(document) is ReviewVerdict.DISAPPROVE

    def test_a_self_review_is_advisory(self):
        document = ReviewDocument.parse("## Must fix\n- **[M1]** a.py:1 — bug\n")
        assert resolve_review_verdict(document, mode=Mode.SELF) is None

    def test_a_self_review_still_reports_disapprove(self):
        """Disapprove judges the approach, which holds with or without a PR."""
        document = ReviewDocument.parse("## Verdict\nDisapprove — wrong approach.\n")
        assert resolve_review_verdict(document, mode=Mode.SELF) is ReviewVerdict.DISAPPROVE

    def test_a_review_that_was_never_written_reaches_no_verdict(self):
        assert resolve_review_verdict(None) is None

    def test_a_review_that_found_nothing_approves(self):
        """The other half of the distinction above: an empty document is a
        review that ran and had nothing to say."""
        assert resolve_review_verdict(ReviewDocument()) is ReviewVerdict.APPROVE

    def test_the_counts_come_from_the_document_it_was_handed(self, tmp_path):
        """One document, read once — the verdict cannot be resolved against
        counts from a file the caller re-read in between."""
        document = ReviewDocument.read(
            _write(tmp_path, "## Should fix\n- **[S1]** a.py:1 — improvement\n\n## Verdict\nApprove — fine.\n"),
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


class TestBuildMechanicalBody:
    @staticmethod
    def _must_fix_content(count=1):
        lines = ["## Must fix"]
        for i in range(1, count + 1):
            lines.append(f"- **[M{i}]** **`f{i}.py`** — bug {i}")
        return "\n".join(lines) + "\n"

    def test_opens_with_the_summary_it_generates(self):
        """The body starts at `## Summary` — the title and metadata header above
        it belong to the document, not to the merge."""
        result = build_mechanical_body(
            self._must_fix_content(), group_count=2, summary_note="Test note.",
        )
        assert result.startswith("## Summary")
        assert "1 finding" in result
        assert "2 groups" in result
        assert "Test note." in result

    def test_includes_verdict_by_default(self):
        result = build_mechanical_body(
            self._must_fix_content(), group_count=1, summary_note="note",
        )
        assert "## Verdict" in result
        assert "Request changes" in result

    def test_excludes_verdict_when_disabled(self):
        result = build_mechanical_body(
            self._must_fix_content(), group_count=1, summary_note="note",
            include_verdict=False,
        )
        assert "## Verdict" not in result

    def test_a_stated_verdict_replaces_the_derived_one(self):
        """A path that reached the review file without an agent but knows what
        the call is says so — the clean run does, and its verdict carries no
        mechanical note because nothing stood in for a synthesis."""
        result = build_mechanical_body(
            "## File Triage\n- `a.py` — tier 2\n", group_count=1,
            summary_note="note", verdict=CLEAN_VERDICT,
        )
        assert "Approve — clean review." in result
        assert MECHANICAL_NOTE not in result

    def test_no_findings_verdict(self):
        result = build_mechanical_body(
            "## Must fix\n_none._\n", group_count=1, summary_note="note",
        )
        assert "No findings" in result
        assert "Approve" in result

    def test_file_count_in_scope(self):
        result = build_mechanical_body(
            self._must_fix_content(), group_count=2, summary_note="note", file_count=3,
        )
        assert "across 3 files in 2 groups" in result

    def test_a_run_where_every_group_failed_does_not_claim_no_findings(self):
        """Nothing reported is not nothing found, and the tally cannot tell.

        Both are zero, so a summary derived from the tally alone says "No
        findings" over source no agent opened — the one sentence a reader acts
        on before reaching the failures table underneath it. Observed: a review
        reporting "No findings across 13 files in 3 groups" for a run whose
        every group hit its turn cap without writing.
        """
        result = build_mechanical_body(
            "", group_count=3, summary_note="Synthesis failed.",
            file_count=13, groups_reported=0,
        )
        assert "No findings" not in result
        assert "No group reported" in result
        assert "0 of 3 groups" in result

    # passes-at-base: the partial case already worked; this bounds the new branch
    def test_a_partly_failed_run_still_reports_its_findings(self):
        """Some groups reported, so the findings are real — the scope narrows."""
        result = build_mechanical_body(
            self._must_fix_content(), group_count=3, summary_note="note",
            file_count=13, groups_reported=2,
        )
        assert "1 finding across 13 files in 2 of 3 groups" in result

    # passes-at-base: the full-coverage case, which this change preserves
    def test_a_clean_run_still_reads_as_clean(self):
        """The honest empty tally must keep its plain sentence."""
        result = build_mechanical_body(
            "## Must fix\n_none._\n", group_count=3, summary_note="note",
            file_count=13,
        )
        assert "No findings across 13 files in 3 groups" in result

    def test_the_prior_findings_ledger_does_not_inflate_the_count(self):
        """The ledger reports the last review, so its lines are not findings
        this one declares — counting them said `2 findings` where there is one."""
        result = build_mechanical_body(
            self._must_fix_content() + "## Prior findings\n- **[M1]** `old.go` — Fixed\n",
            group_count=1, summary_note="note",
        )
        assert "1 finding across" in result
