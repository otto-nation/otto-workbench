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
    def test_empty_markers_excluded_from_merge(self, tmp_path):
        g1 = tmp_path / "group-1.md"
        g1.write_text(
            "## File Triage\n"
            "- `file.go` — reviewed\n"
            "## Must fix\n"
            "_None._\n"
            "## Should fix\n"
            "_None._\n"
            "## Nit\n"
            "- **[N1]** **`file.go:10`** — style issue\n"
            "## Idioms\n"
            "_(none)_\n"
        )
        result = review.merge.merge_reviews([str(g1)])
        assert "_None._" not in result
        assert "_(none)_" not in result
        assert "## Must fix" not in result
        assert "## Nit" in result
        assert "[N1]" in result

    def test_separators_excluded_from_merge(self, tmp_path):
        g1 = tmp_path / "group-1.md"
        g1.write_text(
            "## File Triage\n"
            "- `file.go` — reviewed\n"
            "## Must fix\n"
            "---\n"
            "_None._\n"
            "---\n"
            "## Nit\n"
            "---\n"
            "- **[N1]** **`file.go:10`** — finding\n"
            "---\n"
        )
        result = review.merge.merge_reviews([str(g1)])
        assert "---" not in result


# ── 7. merge_reviews ────────────────────────────────────────────────────────


class TestMergeReviews:
    def test_merge_different_sections(self, tmp_path):
        g1 = tmp_path / "g1.md"
        g1.write_text(
            "## File Triage\n- `a.go` — reviewed\n"
            "## Must fix\n- **[M1]** **`a.go:1`** — issue a\n"
            "## Should fix\n_None._\n"
            "## Nit\n_None._\n"
            "## Idioms\n_None._\n"
        )
        g2 = tmp_path / "g2.md"
        g2.write_text(
            "## File Triage\n- `b.go` — reviewed\n"
            "## Must fix\n_None._\n"
            "## Should fix\n- **[S1]** **`b.go:5`** — issue b\n"
            "## Nit\n_None._\n"
            "## Idioms\n_None._\n"
        )
        result = review.merge.merge_reviews([str(g1), str(g2)])
        assert "[M1]" in result
        assert "[S1]" in result
        assert "`a.go`" in result
        assert "`b.go`" in result

    def test_merge_duplicate_findings(self, tmp_path):
        g1 = tmp_path / "g1.md"
        g1.write_text(
            "## File Triage\n- `a.go` — reviewed\n"
            "## Must fix\n- **[M1]** **`a.go:1`** — same issue\n"
            "## Should fix\n_None._\n## Nit\n_None._\n## Idioms\n_None._\n"
        )
        g2 = tmp_path / "g2.md"
        g2.write_text(
            "## File Triage\n- `a.go` — reviewed\n"
            "## Must fix\n- **[M1]** **`a.go:1`** — same issue\n"
            "## Should fix\n_None._\n## Nit\n_None._\n## Idioms\n_None._\n"
        )
        result = review.merge.merge_reviews([str(g1), str(g2)])
        # Dedup should remove duplicate finding
        assert result.count("same issue") == 1

    def test_merge_renumbering(self, tmp_path):
        g1 = tmp_path / "g1.md"
        g1.write_text(
            "## File Triage\n- `a.go` — reviewed\n"
            "## Must fix\n_None._\n"
            "## Should fix\n- **[S1]** **`a.go:1`** — issue a\n"
            "## Nit\n_None._\n## Idioms\n_None._\n"
        )
        g2 = tmp_path / "g2.md"
        g2.write_text(
            "## File Triage\n- `b.go` — reviewed\n"
            "## Must fix\n_None._\n"
            "## Should fix\n- **[S1]** **`b.go:5`** — issue b\n"
            "## Nit\n_None._\n## Idioms\n_None._\n"
        )
        result = review.merge.merge_reviews([str(g1), str(g2)])
        assert "[S1]" in result
        assert "[S2]" in result

    def test_merge_keeps_each_groups_references_inside_that_group(self, tmp_path):
        # Both groups number from S1, so the second group's IDs get offset past
        # the first's. A reference that did not move with them would name the
        # first group's finding — a different file, a different problem.
        g1 = tmp_path / "g1.md"
        g1.write_text(
            "## File Triage\n- `a.go` — reviewed\n"
            "## Should fix\n"
            "- **[S1]** **`a.go:1`** — issue a\n"
            "- **[S2]** **`a.go:2`** — issue b, related to [S1]\n"
            "## Must fix\n_None._\n## Nit\n_None._\n## Idioms\n_None._\n"
        )
        g2 = tmp_path / "g2.md"
        g2.write_text(
            "## File Triage\n- `b.go` — reviewed\n"
            "## Should fix\n"
            "- **[S1]** **`b.go:1`** — issue c\n"
            "- **[S2]** **`b.go:2`** — issue d, see S1 above\n"
            "## Must fix\n_None._\n## Nit\n_None._\n## Idioms\n_None._\n"
        )
        result = review.merge.merge_reviews([str(g1), str(g2)])

        assert "- **[S2]** **`a.go:2`** — issue b, related to [S1]" in result
        assert "- **[S4]** **`b.go:2`** — issue d, see S3 above" in result

    def test_merge_carries_a_citation_across_severities(self, tmp_path):
        # Both groups number their Should-fix findings from S1, so the second
        # group's are offset past the first's. The second group's Must-fix
        # finding cites its own group's S1 — left behind, it would name the
        # first group's finding, in a different file about a different problem.
        g1 = tmp_path / "g1.md"
        g1.write_text(
            "## File Triage\n- `a.go` — reviewed\n"
            "## Must fix\n- **[M1]** **`a.go:1`** — issue a\n"
            "## Should fix\n"
            "- **[S1]** **`a.go:2`** — issue b\n"
            "- **[S2]** **`a.go:3`** — issue c\n"
            "## Nit\n_None._\n## Idioms\n_None._\n"
        )
        g2 = tmp_path / "g2.md"
        g2.write_text(
            "## File Triage\n- `b.go` — reviewed\n"
            "## Must fix\n- **[M1]** **`b.go:1`** — issue d, blocked on [S1]\n"
            "## Should fix\n- **[S1]** **`b.go:2`** — issue e\n"
            "## Nit\n- **[N1]** **`b.go:3`** — issue f, see S1 above\n"
            "## Idioms\n_None._\n"
        )
        result = review.merge.merge_reviews([str(g1), str(g2)])

        assert "- **[S3]** **`b.go:2`** — issue e" in result
        assert "- **[M2]** **`b.go:1`** — issue d, blocked on [S3]" in result
        assert "- **[N1]** **`b.go:3`** — issue f, see S3 above" in result

    def _misdirected_pair(self, tmp_path, *, citing_first: bool) -> str:
        """One group citing a Should-fix ID it never declared, and one declaring one."""
        citing = tmp_path / "citing.md"
        citing.write_text(
            "## File Triage\n- `a.go` — reviewed\n"
            "## Must fix\n- **[M1]** **`a.go:1`** — issue a, blocked on [S1]\n"
            "## Should fix\n_None._\n## Nit\n_None._\n## Idioms\n_None._\n"
        )
        declaring = tmp_path / "declaring.md"
        declaring.write_text(
            "## File Triage\n- `b.go` — reviewed\n"
            "## Must fix\n_None._\n"
            "## Should fix\n- **[S1]** **`b.go:2`** — unrelated issue in group two\n"
            "## Nit\n_None._\n## Idioms\n_None._\n"
        )
        order = (citing, declaring) if citing_first else (declaring, citing)
        return review.merge.merge_reviews([str(path) for path in order])

    def test_merge_does_not_point_a_dangling_reference_at_another_group(self, tmp_path):
        # No group can name another group's finding — every group numbers from
        # [S1] over its own files alone. Resolving this citation through the
        # pooled map lands the reader on a real finding about a different file.
        result = self._misdirected_pair(tmp_path, citing_first=True)
        assert "- **[M1]** **`a.go:1`** — issue a, blocked on [removed]" in result
        assert "- **[S1]** **`b.go:2`** — unrelated issue in group two" in result

    def test_a_dangling_reference_is_marked_whichever_group_merges_first(self, tmp_path):
        result = self._misdirected_pair(tmp_path, citing_first=False)
        assert "- **[M1]** **`a.go:1`** — issue a, blocked on [removed]" in result
        assert "- **[S1]** **`b.go:2`** — unrelated issue in group two" in result

    def test_merge_clears_a_gap_the_first_group_left(self, tmp_path):
        # Nothing closes a group's gaps before the merge, so offsetting by the
        # number of findings would drop the second group's S1 onto the first
        # group's S3 — two findings, one ID, and dedup keeps both.
        g1 = tmp_path / "g1.md"
        g1.write_text(
            "## File Triage\n- `a.go` — reviewed\n"
            "## Should fix\n"
            "- **[S1]** **`a.go:1`** — issue a\n"
            "- **[S3]** **`a.go:3`** — issue b\n"
            "## Must fix\n_None._\n## Nit\n_None._\n## Idioms\n_None._\n"
        )
        g2 = tmp_path / "g2.md"
        g2.write_text(
            "## File Triage\n- `b.go` — reviewed\n"
            "## Should fix\n- **[S1]** **`b.go:1`** — issue c\n"
            "## Must fix\n_None._\n## Nit\n_None._\n## Idioms\n_None._\n"
        )
        result = review.merge.merge_reviews([str(g1), str(g2)])

        # Offsetting by the count would put issue c on S3, where issue b already
        # sits; the gaps close afterwards, so all three come out distinct.
        assert "- **[S1]** **`a.go:1`** — issue a" in result
        assert "- **[S2]** **`a.go:3`** — issue b" in result
        assert "- **[S3]** **`b.go:1`** — issue c" in result

    def test_merge_unions_prior_findings_ledgers(self, tmp_path):
        g1 = tmp_path / "g1.md"
        g1.write_text(
            "## File Triage\n- `a.go` — reviewed\n"
            "## Must fix\n- **[M1]** **`a.go:1`** — new issue\n"
            f"## {SECTION_PRIOR_FINDINGS}\n- **[M3]** `a.go` — Fixed\n"
        )
        g2 = tmp_path / "g2.md"
        g2.write_text(
            "## File Triage\n- `b.go` — reviewed\n"
            f"## {SECTION_PRIOR_FINDINGS}\n"
            "- **[M3]** `a.go` — Fixed\n"
            "- **[S2]** `b.go` — Still open\n"
        )
        result = review.merge.merge_reviews([str(g1), str(g2)])
        assert result.count("**[M3]** `a.go` — Fixed") == 1
        assert "**[S2]** `b.go` — Still open" in result
        # Ledger IDs name the prior review, so the merge must not renumber them
        # into the sequence it assigns this review's findings.
        assert "- **[M1]** **`a.go:1`** — new issue" in result

    def test_merge_keeps_the_still_open_verdict_when_groups_disagree(self, tmp_path):
        g1 = tmp_path / "g1.md"
        g1.write_text(
            "## File Triage\n- `a.go` — reviewed\n"
            f"## {SECTION_PRIOR_FINDINGS}\n- **[M3]** `a.go` — Fixed\n"
        )
        g2 = tmp_path / "g2.md"
        g2.write_text(
            "## File Triage\n- `a.go` — reviewed\n"
            f"## {SECTION_PRIOR_FINDINGS}\n- **[M3]** `a.go` — Still open\n"
        )
        result = review.merge.merge_reviews([str(g1), str(g2)])
        assert "**[M3]** `a.go` — Still open" in result
        assert "Fixed" not in result

    def _ledger_pair(self, tmp_path, first: str, second: str) -> str:
        """Two groups dispositioning the same prior finding, merged."""
        paths = []
        for name, verdict in (("g1", first), ("g2", second)):
            path = tmp_path / f"{name}.md"
            path.write_text(
                "## File Triage\n- `a.go` — reviewed\n"
                f"## {SECTION_PRIOR_FINDINGS}\n- **[M3]** `a.go` — {verdict}\n"
            )
            paths.append(str(path))
        return review.merge.merge_reviews(paths)

    def test_merge_does_not_reopen_a_declined_finding(self, tmp_path):
        """Still-open used to overwrite whatever was kept, declined included."""
        result = self._ledger_pair(tmp_path, "Declined", "Still open")
        assert "**[M3]** `a.go` — Declined" in result
        assert "Still open" not in result

    def test_merge_lets_a_decline_settle_a_finding_another_group_still_sees(
        self, tmp_path,
    ):
        result = self._ledger_pair(tmp_path, "Still open", "Declined")
        assert "**[M3]** `a.go` — Declined" in result
        assert "Still open" not in result

    def test_merge_omits_ledger_when_no_group_has_one(self, tmp_path):
        g1 = tmp_path / "g1.md"
        g1.write_text(
            "## File Triage\n- `a.go` — reviewed\n"
            "## Must fix\n- **[M1]** **`a.go:1`** — issue\n"
        )
        assert SECTION_PRIOR_FINDINGS not in review.merge.merge_reviews([str(g1)])

    def test_missing_file_skipped(self, tmp_path):
        g1 = tmp_path / "g1.md"
        g1.write_text(
            "## File Triage\n- `a.go` — reviewed\n"
            "## Must fix\n- **[M1]** **`a.go:1`** — issue\n"
            "## Should fix\n_None._\n## Nit\n_None._\n## Idioms\n_None._\n"
        )
        result = review.merge.merge_reviews([str(g1), str(tmp_path / "missing.md")])
        assert "[M1]" in result

    def test_empty_group_file(self, tmp_path):
        g1 = tmp_path / "g1.md"
        g1.write_text("")
        result = review.merge.merge_reviews([str(g1)])
        assert "## File Triage" in result

    def test_merge_strips_narrative_from_triage(self, tmp_path):
        # An agent that writes prose under File Triage instead of entries: the
        # entries survive the merge, the essay around them does not.
        g1 = tmp_path / "g1.md"
        g1.write_text(
            "## File Triage\n"
            "- `a.go` — Tier 2\n"
            "\n"
            "Both files are straightforward. No issues found.\n"
            "\n"
            "### a.go\n"
            "\n"
            "This file has a simple handler implementation.\n"
        )
        result = review.merge.merge_reviews([str(g1)])
        assert "`a.go`" in result
        assert "Both files" not in result
        assert "### a.go" not in result

    def test_merge_dedups_triage_across_groups(self, tmp_path):
        # Two groups can share a file, and each triages every file it was given.
        g1 = tmp_path / "g1.md"
        g1.write_text(
            "## File Triage\n- `shared.go` — Tier 1\n- `a.go` — Tier 2\n"
        )
        g2 = tmp_path / "g2.md"
        g2.write_text(
            "## File Triage\n- `shared.go` — Tier 1\n- `b.go` — Tier 2\n"
        )
        result = review.merge.merge_reviews([str(g1), str(g2)])
        assert result.count("`shared.go`") == 1

    def test_merge_strips_separators_from_triage(self, tmp_path):
        g1 = tmp_path / "g1.md"
        g1.write_text("## File Triage\n- `a.go` — Tier 2\n")
        g2 = tmp_path / "g2.md"
        g2.write_text(
            "## File Triage\n- `b.go` — Tier 2\n"
            "\n---\n\nSome paragraph about the files above.\n"
        )
        assert "---" not in review.merge.merge_reviews([str(g1), str(g2)])

    def test_merge_closes_the_gap_a_cross_group_duplicate_leaves(self, tmp_path):
        # Four declarations, one of them a duplicate of another group's. The
        # survivor keeps its place and the numbers close up behind the copy.
        g1 = tmp_path / "g1.md"
        g1.write_text(
            "## File Triage\n- `a.go` — Tier 2\n"
            "## Must fix\n"
            "- **[M1]** **`a.go:10`** — First unique finding\n"
            "- **[M2]** **`a.go:20`** — Duplicate finding across groups\n"
        )
        g2 = tmp_path / "g2.md"
        g2.write_text(
            "## File Triage\n- `b.go` — Tier 2\n"
            "## Must fix\n"
            "- **[M1]** **`a.go:20`** — Duplicate finding across groups\n"
            "- **[M2]** **`b.go:5`** — Second unique finding\n"
        )
        result = review.merge.merge_reviews([str(g1), str(g2)])
        assert result.count("Duplicate finding") == 1
        assert "[M1]" in result
        assert "[M2]" in result
        assert "[M3]" in result
        assert "[M4]" not in result

    def test_merge_reads_section_headers_case_insensitively(self, tmp_path):
        # Agents write the headings the prompt names, in whatever case they
        # felt like typing; the section a heading opens is the same either way.
        g1 = tmp_path / "g1.md"
        g1.write_text(
            "## Must Fix\n- **[M1]** **`a.go:10`** — bug\n"
            "## NIT\n- **[N1]** **`a.go:20`** — style\n"
        )
        result = review.merge.merge_reviews([str(g1)])
        assert "[M1]" in result
        assert "[N1]" in result


class TestReaderToleranceForIncrementalWrites:
    """A6b's net: merge_reviews already accepts a short complete doc, an
    empty doc, and a rewrite of the file between calls. Do not tighten this.
    """

    _SHORT = (
        "## File Triage\n- `a.py` — Tier 2\n"
        "## Must fix\n- **[M1]** **`a.py:1`** — issue\n"
    )

    # passes-at-base: merge_reviews already section-merges a short complete doc
    def test_a_short_complete_group_doc_merges(self, tmp_path):
        g = tmp_path / "group-1.md"
        g.write_text(self._SHORT)
        result = review.merge.merge_reviews([str(g)])
        assert "## File Triage" in result
        assert "[M1]" in result

    # passes-at-base: an empty group file already contributes no sections
    def test_an_empty_group_doc_merges(self, tmp_path):
        g = tmp_path / "group-1.md"
        g.write_text("")
        result = review.merge.merge_reviews([str(g)])
        assert "## File Triage" in result
        assert "[M1]" not in result

    # passes-at-base: merge_reviews reads the path at call time, so a rewrite is a later read
    def test_a_doc_rewritten_mid_run_merges_the_current_bytes(self, tmp_path):
        g = tmp_path / "group-1.md"
        g.write_text(self._SHORT)
        first = review.merge.merge_reviews([str(g)])
        g.write_text(
            self._SHORT
            + "## Should fix\n- **[S1]** **`a.py:2`** — later finding\n"
        )
        second = review.merge.merge_reviews([str(g)])
        assert "[M1]" in first
        assert "[S1]" not in first
        assert "[M1]" in second
        assert "[S1]" in second
