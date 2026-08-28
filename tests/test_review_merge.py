"""Tests for `review_merge` — what happens to findings across reviews.

Merging a review's groups into one document, the stable IDs that give a finding
an identity later reviews can recognise, and reading the prior review's ledger.
Reconciling this review against the prior one is the same module's other half
and lives in `test_review_reconcile.py`.
"""

import sys
from pathlib import Path

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import review_merge
from review_document import SECTION_PRIOR_FINDINGS
from review_types import PriorDisposition


class TestStripStableIds:
    def test_removes_sid_comments(self):
        text = '- **[M1]** <!-- sid:abc12345 --> **`file.go:42`** — desc\n'
        result = review_merge.strip_stable_ids(text)
        assert "<!-- sid:" not in result
        assert "**[M1]** **`file.go:42`**" in result

    def test_no_sids_unchanged(self):
        content = "- **[M1]** **`file.go:42`** — desc\n"
        assert review_merge.strip_stable_ids(content) == content


class TestFindingPathReCheckbox:
    def test_matches_checkbox_format(self):
        line = '- [ ] **[M1]** **`handler.go:42`** — desc'
        m = review_merge._FINDING_PATH_RE.match(line)
        assert m is not None
        path = (m.group(1) or m.group(2) or "")
        assert "handler.go" in path

    def test_matches_with_stable_id(self):
        line = '- **[M1]** <!-- sid:abc --> **`handler.go:42`** — desc'
        m = review_merge._FINDING_PATH_RE.match(line)
        assert m is not None


class TestComputeStableId:
    def test_deterministic(self):
        a = review_merge.compute_stable_id("pkg/handler.go", "missing error check on db.Query()")
        b = review_merge.compute_stable_id("pkg/handler.go", "missing error check on db.Query()")
        assert a == b

    def test_eight_hex_chars(self):
        sid = review_merge.compute_stable_id("file.go", "desc")
        assert len(sid) == 8
        assert all(c in "0123456789abcdef" for c in sid)

    def test_case_insensitive_path(self):
        a = review_merge.compute_stable_id("Pkg/Handler.go", "desc")
        b = review_merge.compute_stable_id("pkg/handler.go", "desc")
        assert a == b

    def test_different_descriptions_differ(self):
        a = review_merge.compute_stable_id("file.go", "missing error check")
        b = review_merge.compute_stable_id("file.go", "unused import")
        assert a != b

    def test_truncates_description_at_80(self):
        desc_80 = "x" * 80
        desc_100 = desc_80 + "y" * 20
        assert review_merge.compute_stable_id("f.go", desc_80) == review_merge.compute_stable_id("f.go", desc_100)


class TestAnnotatePriorWithStableIds:
    def test_inserts_sid_comment(self):
        text = '- **[M1]** **`handler.go:42`** — missing error check\n'
        result = review_merge.annotate_prior_with_stable_ids(text)
        assert "<!-- sid:" in result
        assert "**[M1]**" in result
        assert "handler.go:42" in result

    def test_checkbox_format(self):
        text = '- [ ] **[S1]** `handler.go:42` — missing check\n'
        result = review_merge.annotate_prior_with_stable_ids(text)
        assert "<!-- sid:" in result

    def test_non_finding_lines_unchanged(self):
        text = "## Summary\nThis is a summary.\n"
        result = review_merge.annotate_prior_with_stable_ids(text)
        assert result == text

    def test_deterministic_ids(self):
        text = '- **[M1]** **`handler.go:42`** — missing error check\n'
        a = review_merge.annotate_prior_with_stable_ids(text)
        b = review_merge.annotate_prior_with_stable_ids(text)
        assert a == b


class TestCleanSectionText:
    def test_strips_none_markers(self):
        assert review_merge._clean_section_text("_None._") == ""
        assert review_merge._clean_section_text("_(none)_") == ""

    def test_strips_horizontal_rules(self):
        assert review_merge._clean_section_text("---") == ""

    def test_case_insensitive(self):
        assert review_merge._clean_section_text("_NONE._") == ""
        assert review_merge._clean_section_text("_None._") == ""

    def test_preserves_findings(self):
        text = "- **[M1]** **`file.go:42`** — finding"
        assert review_merge._clean_section_text(text) == text

    def test_strips_markers_around_findings(self):
        text = "_None._\n---\n- **[M1]** **`file.go:42`** — finding\n---\n_None._"
        result = review_merge._clean_section_text(text)
        assert result == "- **[M1]** **`file.go:42`** — finding"

    def test_empty_input(self):
        assert review_merge._clean_section_text("") == ""

    def test_only_markers_returns_empty(self):
        assert review_merge._clean_section_text("_None._\n---\n_(none)_") == ""

    def test_strips_none_in_file_group(self):
        assert review_merge._clean_section_text("_None in this file group._") == ""

    def test_strips_none_in_file_group_mixed_case(self):
        assert review_merge._clean_section_text("_NONE IN THIS FILE GROUP._") == ""

    def test_preserves_findings_around_file_group_marker(self):
        text = "_None in this file group._\n- **[M1]** **`file.go:42`** — finding"
        result = review_merge._clean_section_text(text)
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
        result = review_merge.merge_reviews([str(g1)])
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
        result = review_merge.merge_reviews([str(g1)])
        assert "---" not in result


PRIOR_ONE_FINDING = (
    "## Must fix\n"
    "- **[M1]** **`handler.go:42`** — missing error check\n"
)


class TestPriorDisposition:
    def test_parses_the_word_the_prompt_asks_for(self):
        assert PriorDisposition.parse("Fixed") is PriorDisposition.FIXED
        assert (
            PriorDisposition.parse("Still open — see below")
            is PriorDisposition.STILL_OPEN
        )

    def test_unrecognised_wording_has_no_disposition(self):
        assert PriorDisposition.parse("moved to a follow-up") is None

    def test_a_qualified_verdict_is_not_read_as_its_optimistic_half(self):
        assert PriorDisposition.parse("Fixed, but only on the happy path") is None
        assert PriorDisposition.parse("Fixed in a follow-up branch") is None

    def test_a_verdict_ending_a_sentence_is_still_a_verdict(self):
        """The form a review writes when the explanation is prose, not a clause."""
        assert (
            PriorDisposition.parse("Fixed. `check_key` now calls it directly.")
            is PriorDisposition.FIXED
        )
        assert PriorDisposition.parse("Still open.") is PriorDisposition.STILL_OPEN

    def test_parses_a_declined_verdict(self):
        assert PriorDisposition.parse("Declined") is PriorDisposition.DECLINED
        assert (
            PriorDisposition.parse("Declined — documented `ceiling:` tradeoff")
            is PriorDisposition.DECLINED
        )

    def test_the_older_two_verdicts_keep_their_spelling(self):
        """A review file written before Declined existed still has to parse."""
        assert PriorDisposition.FIXED.value == "Fixed"
        assert PriorDisposition.STILL_OPEN.value == "Still open"

    def test_declined_outranks_the_verdicts_it_must_survive(self):
        assert (
            PriorDisposition.DECLINED.precedence
            > PriorDisposition.STILL_OPEN.precedence
            > PriorDisposition.FIXED.precedence
        )


class TestParsePriorFindings:
    """Reconciliation's view of the prior review — see test_review_prior.py."""

    def test_a_findings_quotation_below_its_first_line_travels_with_it(self):
        prior = (
            PRIOR_ONE_FINDING
            + "  The call reads `rows, _ := db.Query(sql)` today.\n"
            + "- **[M2]** **`cache.go:9`** — stale entry\n"
        )
        first, second = review_merge._parse_prior_findings(prior)
        assert "rows, _ := db.Query(sql)" in first.text
        assert "rows, _ := db.Query(sql)" not in second.text

    def test_a_findings_text_stops_at_the_next_section(self):
        prior = PRIOR_ONE_FINDING + "\n## Verdict\nRequest changes.\n"
        first = review_merge._parse_prior_findings(prior)[0]
        assert "Request changes" not in first.text

# ── 3. renumber_section ─────────────────────────────────────────────────────


class TestRenumberSection:
    def test_offset_zero(self):
        text = "- **[S1]** finding\n- **[S2]** another"
        result, count = review_merge.renumber_section("S", text, 0)
        assert result == text
        assert count == 2

    def test_positive_offset(self):
        text = "- **[S1]** finding\n- **[S2]** another"
        result, count = review_merge.renumber_section("S", text, 3)
        assert "[S4]" in result
        assert "[S5]" in result
        assert count == 2

    def test_dedup_count(self):
        text = "- **[M1]** finding\n  see [M1] above"
        result, count = review_merge.renumber_section("M", text, 0)
        assert count == 1

    def test_offset_carries_references(self):
        # The offset is what keeps two groups' IDs apart. A reference left behind
        # would name whatever the earlier group happened to put at that number.
        text = "- **[S1]** first\n- **[S2]** second, see S1 above and [S1] again"
        result, _ = review_merge.renumber_section("S", text, 2)
        assert "see S3 above and [S3] again" in result

    def test_offset_shifts_ids_it_did_not_expect(self):
        # IDs arrive however the agent wrote them; gaps are closed later, not here.
        result, highest = review_merge.renumber_section("S", "- **[S1]** first\n- **[S7]** second", 2)
        assert "[S3]" in result
        assert "[S9]" in result
        # What the next group has to clear, not how many findings this one had.
        assert highest == 7

    def test_a_reference_this_group_cannot_resolve_is_left_alone(self):
        # Only the merge-wide pass can tell a dangling reference from one whose
        # finding lives in another group.
        text = "- **[S1]** first, see S4 elsewhere"
        result, _ = review_merge.renumber_section("S", text, 2)
        assert "see S4 elsewhere" in result

    def test_empty_text(self):
        result, count = review_merge.renumber_section("M", "", 0)
        assert result == ""
        assert count == 0


# ── 4. _renumber_prefix ─────────────────────────────────────────────────────


def _decl(prefix: str, num: int, body: str = "finding") -> str:
    """A finding declaring its own ID — the only thing renumbering numbers."""
    return f"- **[{prefix}{num}]** **`file.go:{num}`** — {body}"


class TestRenumberPrefix:
    def test_sequential_already(self):
        text = f"{_decl('S', 1, 'first')}\n{_decl('S', 2, 'second')}"
        assert review_merge._renumber_prefix(text, "S") == text

    def test_with_gaps(self):
        text = f"{_decl('S', 1, 'first')}\n{_decl('S', 3, 'third')}"
        result = review_merge._renumber_prefix(text, "S")
        assert "[S1]" in result
        assert "[S2]" in result
        assert "[S3]" not in result

    def test_repeated_ids(self):
        text = "\n".join([
            _decl("S", 3, "first"), _decl("S", 1, "second"), _decl("S", 3, "repeat"),
        ])
        result = review_merge._renumber_prefix(text, "S")
        assert result.count("[S1]") == 2  # S3 appears first -> becomes S1
        assert "[S2]" in result  # S1 appears second -> becomes S2

    def test_unbracketed_cross_refs(self):
        text = f"{_decl('S', 3)}\nsee S3 above"
        result = review_merge._renumber_prefix(text, "S")
        assert "see S1 above" in result
        assert "S3" not in result

    def test_reference_to_a_dropped_finding_points_nowhere(self):
        # S1 was dropped by verification, so only its reference is left. Closing
        # the gap on S2 frees up the number 1, and the reference must not take it.
        text = f"{_decl('S', 2, 'real problem')}\nblocked on [S1]"
        result = review_merge._renumber_prefix(text, "S")
        assert "- **[S1]** **`file.go:2`** — real problem" in result
        assert "blocked on [removed]" in result

    def test_bare_reference_to_a_dropped_finding_points_nowhere(self):
        text = f"{_decl('S', 2, 'real problem')}\nblocked on S1"
        assert "blocked on [removed]" in review_merge._renumber_prefix(text, "S")

    def test_prose_that_merely_looks_like_an_id_is_left_alone(self):
        # S3 the object store, M1 the laptop. Nothing cites them, so nothing
        # may rewrite them — and a review of storage code says "S3" constantly.
        text = "\n".join([
            _decl("S", 3, "uploads to an S3 bucket on every M1 build"),
            _decl("S", 5, "second"),
        ])
        result = review_merge._renumber_prefix(text, "S")
        assert "uploads to an S3 bucket on every M1 build" in result
        assert "- **[S1]**" in result
        assert "- **[S2]**" in result

    def test_a_cited_bare_reference_is_still_rewritten(self):
        text = f"{_decl('S', 3, 'first')}\n{_decl('S', 5, 'second, duplicate of S3')}"
        assert "duplicate of S1" in review_merge._renumber_prefix(text, "S")

    def test_references_survive_a_second_pass(self):
        text = f"{_decl('S', 2, 'real problem')}\nblocked on [S1]"
        once = review_merge._renumber_prefix(text, "S")
        assert review_merge._renumber_prefix(once, "S") == once

    def test_text_that_declares_nothing_is_left_alone(self):
        # A section can mention IDs it does not own — the triage list, a prior
        # review's ledger. With no declaration there is no map to rewrite through.
        text = "carried over from [S4] and [S7]"
        assert review_merge._renumber_prefix(text, "S") == text

    def test_checklist_findings_declare_their_ids(self):
        # Self-review writes findings as checkboxes; they are declarations too.
        text = "- [ ] **[S3]** `file.go:1` — finding\nsee [S3]"
        result = review_merge._renumber_prefix(text, "S")
        assert "- [ ] **[S1]** `file.go:1` — finding" in result
        assert "see [S1]" in result

    def test_empty_text(self):
        assert review_merge._renumber_prefix("", "S") == ""


# ── 5. renumber_findings ────────────────────────────────────────────────────


class TestRenumberFindings:
    def test_renumbers_gaps(self):
        text = "\n".join([
            _decl("M", 1, "first"), _decl("M", 3, "third"),
            _decl("S", 1, "s1"), _decl("S", 5, "s5"), "",
        ])
        result = review_merge.renumber_findings(text)
        assert "[M1]" in result
        assert "[M2]" in result
        assert "[M3]" not in result
        assert "[S1]" in result
        assert "[S2]" in result
        assert "[S5]" not in result

    def test_each_severity_is_renumbered_independently(self):
        # A Nit citing a dropped Must-fix loses the citation; its own ID does not
        # move, because the M pass never looks at N numbers.
        text = "\n".join([
            _decl("M", 2, "kept"),
            "- **[N1]** **`file.go:9`** — revisit once [M1] lands",
        ])
        result = review_merge.renumber_findings(text)
        assert "- **[M1]** **`file.go:2`** — kept" in result
        assert "revisit once [removed] lands" in result

    def test_empty_text(self):
        assert review_merge.renumber_findings("") == ""

    def test_no_findings_unchanged(self):
        content = "No findings here.\n"
        assert review_merge.renumber_findings(content) == content


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
        result = review_merge.merge_reviews([str(g1), str(g2)])
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
        result = review_merge.merge_reviews([str(g1), str(g2)])
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
        result = review_merge.merge_reviews([str(g1), str(g2)])
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
        result = review_merge.merge_reviews([str(g1), str(g2)])

        assert "- **[S2]** **`a.go:2`** — issue b, related to [S1]" in result
        assert "- **[S4]** **`b.go:2`** — issue d, see S3 above" in result

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
        result = review_merge.merge_reviews([str(g1), str(g2)])

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
        result = review_merge.merge_reviews([str(g1), str(g2)])
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
        result = review_merge.merge_reviews([str(g1), str(g2)])
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
        return review_merge.merge_reviews(paths)

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
        assert SECTION_PRIOR_FINDINGS not in review_merge.merge_reviews([str(g1)])

    def test_missing_file_skipped(self, tmp_path):
        g1 = tmp_path / "g1.md"
        g1.write_text(
            "## File Triage\n- `a.go` — reviewed\n"
            "## Must fix\n- **[M1]** **`a.go:1`** — issue\n"
            "## Should fix\n_None._\n## Nit\n_None._\n## Idioms\n_None._\n"
        )
        result = review_merge.merge_reviews([str(g1), str(tmp_path / "missing.md")])
        assert "[M1]" in result

    def test_empty_group_file(self, tmp_path):
        g1 = tmp_path / "g1.md"
        g1.write_text("")
        result = review_merge.merge_reviews([str(g1)])
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
        result = review_merge.merge_reviews([str(g1)])
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
        result = review_merge.merge_reviews([str(g1), str(g2)])
        assert result.count("`shared.go`") == 1

    def test_merge_strips_separators_from_triage(self, tmp_path):
        g1 = tmp_path / "g1.md"
        g1.write_text("## File Triage\n- `a.go` — Tier 2\n")
        g2 = tmp_path / "g2.md"
        g2.write_text(
            "## File Triage\n- `b.go` — Tier 2\n"
            "\n---\n\nSome paragraph about the files above.\n"
        )
        assert "---" not in review_merge.merge_reviews([str(g1), str(g2)])

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
        result = review_merge.merge_reviews([str(g1), str(g2)])
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
        result = review_merge.merge_reviews([str(g1)])
        assert "[M1]" in result
        assert "[N1]" in result


# ── 8. _clean_triage ────────────────────────────────────────────────────────


class TestCleanTriage:
    def test_valid_triage_lines(self):
        text = "- `file.go` reviewed\n- `other.go` skimmed"
        result = review_merge._clean_triage(text)
        assert "file.go" in result
        assert "other.go" in result

    def test_mixed_valid_invalid(self):
        text = "- `file.go` reviewed\nsome other text\n- `b.go` done"
        result = review_merge._clean_triage(text)
        assert "file.go" in result
        assert "b.go" in result
        assert "some other text" not in result

    def test_empty_input(self):
        assert review_merge._clean_triage("") == ""


# ── 9. _dedup_triage ────────────────────────────────────────────────────────


class TestDedupTriage:
    def test_with_duplicates(self):
        text = "- `file.go` — reviewed\n- `file.go` — reviewed again"
        result = review_merge._dedup_triage(text)
        assert result.count("file.go") == 1

    def test_no_duplicates(self):
        text = "- `a.go` — reviewed\n- `b.go` — reviewed"
        result = review_merge._dedup_triage(text)
        assert "a.go" in result
        assert "b.go" in result

    def test_empty_input(self):
        assert review_merge._dedup_triage("") == ""


# ── 10. _finding_dedup_key ──────────────────────────────────────────────────


class TestFindingDedupKey:
    def test_standard_finding(self):
        line = "- **[M1]** **`pkg/handler.go:42`** — missing error check"
        result = review_merge._finding_dedup_key(line)
        assert result is not None
        assert "handler.go" in result.path
        assert "missing error check" in result.desc

    def test_checkbox_finding(self):
        line = "- [ ] **[S1]** **`handler.go:10`** — issue here"
        result = review_merge._finding_dedup_key(line)
        assert result is not None
        assert "handler.go" in result.path

    def test_stable_id(self):
        line = "- **[M1]** <!-- sid:abc12345 --> **`file.go:1`** — desc"
        result = review_merge._finding_dedup_key(line)
        assert result is not None
        assert "file.go" in result.path

    def test_non_finding_line(self):
        assert review_merge._finding_dedup_key("just some text") is None


# ── 11. _dedup_findings ─────────────────────────────────────────────────────


class TestDedupFindings:
    def test_with_duplicates(self):
        text = (
            "- **[M1]** **`file.go:1`** — same issue\n"
            "- **[M2]** **`file.go:1`** — same issue\n"
        )
        result = review_merge._dedup_findings(text, "M")
        assert result.count("same issue") == 1

    def test_no_duplicates(self):
        text = (
            "- **[M1]** **`a.go:1`** — issue a\n"
            "- **[M2]** **`b.go:2`** — issue b\n"
        )
        result = review_merge._dedup_findings(text, "M")
        assert "issue a" in result
        assert "issue b" in result

    def test_multiline_continuation(self):
        text = (
            "- **[M1]** **`file.go:1`** — same issue\n"
            "  continuation line\n"
            "- **[M2]** **`file.go:1`** — same issue\n"
            "  another continuation\n"
        )
        result = review_merge._dedup_findings(text, "M")
        assert result.count("same issue") == 1
        assert "another continuation" not in result

    def test_references_follow_the_surviving_copy(self):
        # M2 is the same finding as M1, so a reference to it is not dangling —
        # it belongs on the copy that stayed.
        text = (
            "- **[M1]** **`file.go:1`** — same issue\n"
            "- **[M2]** **`file.go:1`** — same issue\n"
            "- **[M3]** **`other.go:2`** — see [M2] for context\n"
        )
        result = review_merge._dedup_findings(text, "M")
        assert "- **[M2]** **`other.go:2`** — see [M1] for context" in result
