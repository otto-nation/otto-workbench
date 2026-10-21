"""Tests for `review.merge` — merging group reviews into one document.

Folding the group reviews together, cleaning section text, and reading a review
still being written. Stable IDs and renumbering are in `review_merge_ids_test.py`,
deduplication in `review_merge_dedup_test.py`. Reconciling this review against
the prior one is `review.reconcile`'s job, tested in `review_reconcile_test.py`.
"""

import sys
from pathlib import Path

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import review.merge
from review.document import SECTION_PRIOR_FINDINGS
from review.merge import merge_records
from review.record import LedgerLine, TriageEntry
from review.render import render_body
from review.types import PriorDisposition

from review_record_support import finding, record


def _merged_text(*records):
    return render_body(merge_records(list(records)), sids=True, evidence=True)


class TestCleanSectionText:
    def test_strips_none_markers(self):
        assert review.merge._clean_section_text("_None._") == ""
        assert review.merge._clean_section_text("_(none)_") == ""

    def test_strips_horizontal_rules(self):
        assert review.merge._clean_section_text("---") == ""

    def test_case_insensitive(self):
        assert review.merge._clean_section_text("_NONE._") == ""
        assert review.merge._clean_section_text("_None._") == ""

    def test_preserves_findings(self):
        text = "- **[M1]** **`file.go:42`** — finding"
        assert review.merge._clean_section_text(text) == text

    def test_strips_markers_around_findings(self):
        text = "_None._\n---\n- **[M1]** **`file.go:42`** — finding\n---\n_None._"
        result = review.merge._clean_section_text(text)
        assert result == "- **[M1]** **`file.go:42`** — finding"

    def test_empty_input(self):
        assert review.merge._clean_section_text("") == ""

    def test_only_markers_returns_empty(self):
        assert review.merge._clean_section_text("_None._\n---\n_(none)_") == ""

    def test_strips_none_in_file_group(self):
        assert review.merge._clean_section_text("_None in this file group._") == ""

    def test_strips_none_in_file_group_mixed_case(self):
        assert review.merge._clean_section_text("_NONE IN THIS FILE GROUP._") == ""

    def test_preserves_findings_around_file_group_marker(self):
        text = "_None in this file group._\n- **[M1]** **`file.go:42`** — finding"
        result = review.merge._clean_section_text(text)
        assert result == "- **[M1]** **`file.go:42`** — finding"


class TestMergeReviewsCleanup:
    def test_empty_markers_excluded_from_merge(self):
        result = _merged_text(record(
            finding("N1", "style issue", path="file.go", line=10),
            triage=(TriageEntry("file.go", "reviewed"),),
        ))
        assert "_None._" not in result
        assert "_(none)_" not in result
        assert "## Must fix" not in result
        assert "## Nit" in result
        assert "[N1]" in result

    def test_separators_excluded_from_merge(self):
        result = _merged_text(record(
            finding("N1", "finding", path="file.go", line=10),
            triage=(TriageEntry("file.go", "reviewed"),),
        ))
        assert "---" not in result


# ── 7. merge_records ────────────────────────────────────────────────────────


def _group(*, files: dict[str, str], findings=(), ledger=()):
    """One group's record: triage for `files` and the given findings/ledger."""
    return record(
        *findings,
        triage=tuple(TriageEntry(path, note) for path, note in files.items()),
        ledger=ledger,
    )


class TestMergeReviews:
    def test_merge_different_sections(self):
        result = _merged_text(
            _group(files={"a.go": "reviewed"}, findings=(finding("M1", "issue a", path="a.go"),)),
            _group(
                files={"b.go": "reviewed"},
                findings=(finding("S1", "issue b", path="b.go", line=5),),
            ),
        )
        assert "[M1]" in result
        assert "[S1]" in result
        assert "`a.go`" in result
        assert "`b.go`" in result

    def test_merge_duplicate_findings(self):
        g = _group(
            files={"a.go": "reviewed"},
            findings=(finding("M1", "same issue", path="a.go"),),
        )
        result = _merged_text(g, g)
        # Dedup should remove duplicate finding
        assert result.count("same issue") == 1

    def test_merge_renumbering(self):
        result = _merged_text(
            _group(files={"a.go": "reviewed"}, findings=(finding("S1", "issue a", path="a.go"),)),
            _group(
                files={"b.go": "reviewed"},
                findings=(finding("S1", "issue b", path="b.go", line=5),),
            ),
        )
        assert "[S1]" in result
        assert "[S2]" in result

    def test_merge_keeps_each_groups_references_inside_that_group(self):
        # Both groups number from S1, so the second group's IDs get offset past
        # the first's. A reference that did not move with them would name the
        # first group's finding — a different file, a different problem.
        result = _merged_text(
            _group(files={"a.go": "reviewed"}, findings=(
                finding("S1", "issue a", path="a.go"),
                finding("S2", "issue b, related to [S1]", path="a.go", line=2),
            )),
            _group(files={"b.go": "reviewed"}, findings=(
                finding("S1", "issue c", path="b.go"),
                finding("S2", "issue d, see S1 above", path="b.go", line=2),
            )),
        )

        assert "- **[S2]** `a.go:2` — issue b, related to [S1]" in result
        assert "- **[S4]** `b.go:2` — issue d, see S3 above" in result

    def test_merge_carries_a_citation_across_severities(self):
        # Both groups number their Should-fix findings from S1, so the second
        # group's are offset past the first's. The second group's Must-fix
        # finding cites its own group's S1 — left behind, it would name the
        # first group's finding, in a different file about a different problem.
        result = _merged_text(
            _group(files={"a.go": "reviewed"}, findings=(
                finding("M1", "issue a", path="a.go"),
                finding("S1", "issue b", path="a.go", line=2),
                finding("S2", "issue c", path="a.go", line=3),
            )),
            _group(files={"b.go": "reviewed"}, findings=(
                finding("M1", "issue d, blocked on [S1]", path="b.go"),
                finding("S1", "issue e", path="b.go", line=2),
                finding("N1", "issue f, see S1 above", path="b.go", line=3),
            )),
        )

        assert "- **[S3]** `b.go:2` — issue e" in result
        assert "- **[M2]** `b.go:1` — issue d, blocked on [S3]" in result
        assert "- **[N1]** `b.go:3` — issue f, see S3 above" in result

    def _misdirected_pair(self, *, citing_first: bool) -> str:
        """One group citing a Should-fix ID it never declared, and one declaring one."""
        citing = _group(
            files={"a.go": "reviewed"},
            findings=(finding("M1", "issue a, blocked on [S1]", path="a.go"),),
        )
        declaring = _group(
            files={"b.go": "reviewed"},
            findings=(finding("S1", "unrelated issue in group two", path="b.go", line=2),),
        )
        order = (citing, declaring) if citing_first else (declaring, citing)
        return _merged_text(*order)

    def test_merge_does_not_point_a_dangling_reference_at_another_group(self):
        # No group can name another group's finding — every group numbers from
        # [S1] over its own files alone. Resolving this citation through the
        # pooled map lands the reader on a real finding about a different file.
        result = self._misdirected_pair(citing_first=True)
        assert "- **[M1]** `a.go:1` — issue a, blocked on [removed]" in result
        assert "- **[S1]** `b.go:2` — unrelated issue in group two" in result

    def test_a_dangling_reference_is_marked_whichever_group_merges_first(self):
        result = self._misdirected_pair(citing_first=False)
        assert "- **[M1]** `a.go:1` — issue a, blocked on [removed]" in result
        assert "- **[S1]** `b.go:2` — unrelated issue in group two" in result

    def test_merge_clears_a_gap_the_first_group_left(self):
        # Nothing closes a group's gaps before the merge, so offsetting by the
        # number of findings would drop the second group's S1 onto the first
        # group's S3 — two findings, one ID, and dedup keeps both.
        result = _merged_text(
            _group(files={"a.go": "reviewed"}, findings=(
                finding("S1", "issue a", path="a.go"),
                finding("S3", "issue b", path="a.go", line=3),
            )),
            _group(
                files={"b.go": "reviewed"},
                findings=(finding("S1", "issue c", path="b.go"),),
            ),
        )

        # Offsetting by the count would put issue c on S3, where issue b already
        # sits; the gaps close afterwards, so all three come out distinct.
        assert "- **[S1]** `a.go:1` — issue a" in result
        assert "- **[S2]** `a.go:3` — issue b" in result
        assert "- **[S3]** `b.go:1` — issue c" in result

    def test_merge_unions_prior_findings_ledgers(self):
        result = _merged_text(
            _group(
                files={"a.go": "reviewed"},
                findings=(finding("M1", "new issue", path="a.go"),),
                ledger=(LedgerLine("M3", PriorDisposition.FIXED, "a.go"),),
            ),
            _group(
                files={"b.go": "reviewed"},
                ledger=(
                    LedgerLine("M3", PriorDisposition.FIXED, "a.go"),
                    LedgerLine("S2", PriorDisposition.STILL_OPEN, "b.go"),
                ),
            ),
        )
        assert result.count("**[M3]** `a.go` — Fixed") == 1
        assert "**[S2]** `b.go` — Still open" in result
        # Ledger IDs name the prior review, so the merge must not renumber them
        # into the sequence it assigns this review's findings.
        assert "- **[M1]** `a.go:1` — new issue" in result

    def test_merge_keeps_the_still_open_verdict_when_groups_disagree(self):
        result = _merged_text(
            _group(
                files={"a.go": "reviewed"},
                ledger=(LedgerLine("M3", PriorDisposition.FIXED, "a.go"),),
            ),
            _group(
                files={"a.go": "reviewed"},
                ledger=(LedgerLine("M3", PriorDisposition.STILL_OPEN, "a.go"),),
            ),
        )
        assert "**[M3]** `a.go` — Still open" in result
        assert "Fixed" not in result

    def _ledger_pair(self, first: PriorDisposition, second: PriorDisposition) -> str:
        """Two groups dispositioning the same prior finding, merged."""
        return _merged_text(*(
            _group(
                files={"a.go": "reviewed"},
                ledger=(LedgerLine("M3", verdict, "a.go"),),
            )
            for verdict in (first, second)
        ))

    def test_merge_does_not_reopen_a_declined_finding(self):
        """Still-open used to overwrite whatever was kept, declined included."""
        result = self._ledger_pair(PriorDisposition.DECLINED, PriorDisposition.STILL_OPEN)
        assert "**[M3]** `a.go` — Declined" in result
        assert "Still open" not in result

    def test_merge_lets_a_decline_settle_a_finding_another_group_still_sees(
        self,
    ):
        result = self._ledger_pair(PriorDisposition.STILL_OPEN, PriorDisposition.DECLINED)
        assert "**[M3]** `a.go` — Declined" in result
        assert "Still open" not in result

    def test_merge_omits_ledger_when_no_group_has_one(self):
        result = _merged_text(_group(
            files={"a.go": "reviewed"},
            findings=(finding("M1", "issue", path="a.go"),),
        ))
        assert SECTION_PRIOR_FINDINGS not in result

    def test_empty_group_file(self):
        result = _merged_text(record())
        assert "## File Triage" in result

    def test_merge_strips_narrative_from_triage(self):
        # An agent that writes prose under File Triage instead of entries: the
        # entries survive the merge, the essay around them does not.
        result = _merged_text(record(triage=(
            TriageEntry("a.go", "Tier 2"),
            TriageEntry("", "Both files are straightforward. No issues found."),
            TriageEntry("", "### a.go"),
            TriageEntry("", "This file has a simple handler implementation."),
        )))
        assert "`a.go`" in result
        assert "Both files" not in result
        assert "### a.go" not in result

    def test_merge_dedups_triage_across_groups(self):
        # Two groups can share a file, and each triages every file it was given.
        result = _merged_text(
            record(triage=(TriageEntry("shared.go", "Tier 1"), TriageEntry("a.go", "Tier 2"))),
            record(triage=(TriageEntry("shared.go", "Tier 1"), TriageEntry("b.go", "Tier 2"))),
        )
        assert result.count("`shared.go`") == 1

    def test_merge_strips_separators_from_triage(self):
        result = _merged_text(
            record(triage=(TriageEntry("a.go", "Tier 2"),)),
            record(triage=(
                TriageEntry("b.go", "Tier 2"),
                TriageEntry("", "---"),
                TriageEntry("", "Some paragraph about the files above."),
            )),
        )
        assert "---" not in result

    def test_merge_closes_the_gap_a_cross_group_duplicate_leaves(self):
        # Four declarations, one of them a duplicate of another group's. The
        # survivor keeps its place and the numbers close up behind the copy.
        result = _merged_text(
            _group(files={"a.go": "Tier 2"}, findings=(
                finding("M1", "First unique finding", path="a.go", line=10),
                finding("M2", "Duplicate finding across groups", path="a.go", line=20),
            )),
            _group(files={"b.go": "Tier 2"}, findings=(
                finding("M1", "Duplicate finding across groups", path="a.go", line=20),
                finding("M2", "Second unique finding", path="b.go", line=5),
            )),
        )
        assert result.count("Duplicate finding") == 1
        assert "[M1]" in result
        assert "[M2]" in result
        assert "[M3]" in result
        assert "[M4]" not in result


class TestReaderToleranceForIncrementalWrites:
    """A6b's net: merge_records already accepts a short complete doc and an
    empty doc. File reads live on `_phase_merge`; do not tighten this.
    """

    _SHORT = record(
        finding("M1", "issue"),
        triage=(TriageEntry("a.py", "Tier 2"),),
    )

    # passes-at-base: merge_records already section-merges a short complete doc
    def test_a_short_complete_group_doc_merges(self):
        result = _merged_text(self._SHORT)
        assert "## File Triage" in result
        assert "[M1]" in result

    # passes-at-base: an empty group file already contributes no sections
    def test_an_empty_group_doc_merges(self):
        result = _merged_text(record())
        assert "## File Triage" in result
        assert "[M1]" not in result
