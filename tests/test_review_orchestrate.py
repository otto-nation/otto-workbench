import contextlib
import io
import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from conftest import synthetic_review


class TestExtractEvidence:
    def test_extracts_from_blockquoted_fenced_code(self, ro):
        body = (
            "missing error check on `db.Query()`\n"
            "  > ```go\n"
            "  > result := db.Query(query)\n"
            "  > ```"
        )
        assert ro._extract_evidence(body) == "result := db.Query(query)"

    def test_multiline_snippet(self, ro):
        body = (
            "description\n"
            "  > ```python\n"
            "  > x = 1\n"
            "  > y = 2\n"
            "  > ```"
        )
        assert ro._extract_evidence(body) == "x = 1\ny = 2"

    def test_returns_none_when_no_evidence(self, ro):
        assert ro._extract_evidence("just a description, no code block") is None

    def test_handles_no_language_tag(self, ro):
        body = (
            "desc\n"
            "  > ```\n"
            "  > some_code()\n"
            "  > ```"
        )
        assert ro._extract_evidence(body) == "some_code()"


class TestNormalizeCode:
    def test_strips_whitespace_and_blank_lines(self, ro):
        assert ro._normalize_code("  x = 1  \n\n  y = 2  ") == "x = 1\ny = 2"

    def test_empty_string(self, ro):
        assert ro._normalize_code("") == ""

    def test_preserves_content(self, ro):
        assert ro._normalize_code("result := db.Query(q)") == "result := db.Query(q)"


class TestVerifyFinding:
    def test_valid_evidence_passes(self, ro, tmp_path):
        src = tmp_path / "handler.go"
        src.write_text("package main\n\nfunc foo() {\n\tresult := db.Query(q)\n}\n")
        assert ro._verify_finding("handler.go", "result := db.Query(q)", str(tmp_path)) is True

    def test_evidence_not_in_file_fails(self, ro, tmp_path):
        src = tmp_path / "handler.go"
        src.write_text("package main\n\nfunc foo() {\n\tx := 1\n}\n")
        assert ro._verify_finding("handler.go", "result := db.Query(q)", str(tmp_path)) is False

    def test_file_not_found_fails(self, ro, tmp_path):
        assert ro._verify_finding("missing.go", "any code", str(tmp_path)) is False

    def test_none_evidence_file_exists_passes(self, ro, tmp_path):
        src = tmp_path / "handler.go"
        src.write_text("package main\n")
        assert ro._verify_finding("handler.go", None, str(tmp_path)) is True

    def test_none_evidence_file_missing_fails(self, ro, tmp_path):
        assert ro._verify_finding("missing.go", None, str(tmp_path)) is False

    def test_indentation_mismatch_still_passes(self, ro, tmp_path):
        src = tmp_path / "handler.go"
        src.write_text("func foo() {\n\t\tresult := db.Query(q)\n}\n")
        assert ro._verify_finding("handler.go", "result := db.Query(q)", str(tmp_path)) is True

    def test_trailing_go_comment_stripped(self, ro, tmp_path):
        src = tmp_path / "handler.go"
        src.write_text("data.Completed[n] = completed\ndata.Posted[n] = posted\n")
        evidence = "data.Completed[n] = completed   // never read in template\ndata.Posted[n] = posted"
        assert ro._verify_finding("handler.go", evidence, str(tmp_path)) is True

    def test_trailing_template_comment_stripped(self, ro, tmp_path):
        src = tmp_path / "page.html"
        src.write_text("{{ end }}\n")
        evidence = "{{ end }}\n{{/* no else — renders nothing */}}"
        assert ro._verify_finding("page.html", evidence, str(tmp_path)) is True

    def test_ellipsis_fragments_verified(self, ro, tmp_path):
        src = tmp_path / "handler.go"
        src.write_text("func foo() {\n\tresult := db.Query(q)\n\tif err != nil {\n\t\treturn err\n\t}\n\tuse(result)\n}\n")
        evidence = "result := db.Query(q)\n...\nuse(result)"
        assert ro._verify_finding("handler.go", evidence, str(tmp_path)) is True

    def test_ellipsis_fragment_not_in_file_fails(self, ro, tmp_path):
        src = tmp_path / "handler.go"
        src.write_text("func foo() {\n\tresult := db.Query(q)\n\tuse(result)\n}\n")
        evidence = "result := db.Query(q)\n...\nnonexistent_call()"
        assert ro._verify_finding("handler.go", evidence, str(tmp_path)) is False

    def test_comment_inside_string_not_stripped(self, ro, tmp_path):
        src = tmp_path / "handler.go"
        src.write_text('msg := "value // not a comment"\n')
        evidence = 'msg := "value // not a comment"'
        assert ro._verify_finding("handler.go", evidence, str(tmp_path)) is True

    @pytest.mark.parametrize("lines", [(0, 6), (1, 5), (2, 6), (4, 6)])
    def test_verbatim_quote_of_any_span_passes(self, ro, tmp_path, lines):
        """Evidence copied out of the file verifies, whatever it spans.

        The regression for #697: comment stripping ran on the quote only, so
        the file kept text the quote no longer had. Every span here — over a
        whole-line comment, over a trailing comment that is not on the last
        line, or both — failed to match the file it was copied from.
        """
        source = (
            "def run(action):\n"
            "    if not publishing.enabled():\n"
            "        # The closing line is the one read as the outcome, so it\n"
            "        # carries the label the body was printed under.\n"
            "        publishing.draft(action)  # not a post\n"
            "        return 0\n"
        )
        src = tmp_path / "publish.py"
        src.write_text(source)
        start, end = lines
        evidence = "\n".join(source.split("\n")[start:end])
        assert ro._verify_finding("publish.py", evidence, str(tmp_path)) is True

    def test_reviewer_annotation_comment_passes(self, ro, tmp_path):
        # Reviewers annotate evidence with lines the file does not contain.
        src = tmp_path / "handler.go"
        src.write_text("func foo() {\n\tresult := db.Query(q)\n}\n")
        evidence = "result := db.Query(q)\n// err is never checked"
        assert ro._verify_finding("handler.go", evidence, str(tmp_path)) is True

    def test_wrong_code_beside_a_comment_still_fails(self, ro, tmp_path):
        src = tmp_path / "handler.go"
        src.write_text("func foo() {\n\t// query the db\n\tresult := db.Query(q)\n}\n")
        evidence = "// query the db\nresult := db.Exec(q)"
        assert ro._verify_finding("handler.go", evidence, str(tmp_path)) is False


class TestStripComments:
    def test_strips_go_comment(self, ro):
        assert ro._strip_comments("x = 1 // explanation") == "x = 1"

    def test_strips_python_comment(self, ro):
        assert ro._strip_comments("x = 1 # explanation") == "x = 1"

    def test_strips_template_comment(self, ro):
        assert ro._strip_comments("{{ end }}{{/* note */}}") == "{{ end }}"

    def test_preserves_comment_inside_string(self, ro):
        line = 'msg := "value // not a comment"'
        assert ro._strip_comments(line) == line

    def test_no_comment_unchanged(self, ro):
        assert ro._strip_comments("x = 1") == "x = 1"

    def test_drops_indented_whole_line_comment(self, ro):
        assert ro._strip_comments("    # why this matters\n    x = 1") == "\n    x = 1"

    def test_drops_column_zero_whole_line_comment(self, ro):
        assert ro._strip_comments("// why this matters\nx = 1") == "\nx = 1"

    def test_keeps_directive_with_no_space_after_marker(self, ro):
        # Not prose: dropping these would erase code from the comparison.
        assert ro._strip_comments("#!/usr/bin/env bash") == "#!/usr/bin/env bash"
        assert ro._strip_comments("#include <stdio.h>") == "#include <stdio.h>"
        assert ro._strip_comments("\t//nolint:errcheck") == "\t//nolint:errcheck"


class TestVerificationDetail:
    def test_match_records_pass(self, ro, tmp_path):
        src = tmp_path / "handler.go"
        src.write_text("func foo() {\n\tresult := db.Query(q)\n}\n")
        finding = {"id": "S1", "severity": "S", "path": "handler.go", "body": ""}
        detail = ro._verification_detail(finding, "result := db.Query(q)", str(tmp_path))
        assert detail["match_result"] is True
        assert detail["file_exists"] is True

    def test_mismatch_records_failure(self, ro, tmp_path):
        src = tmp_path / "handler.go"
        src.write_text("func foo() {\n\tx := 1\n}\n")
        finding = {"id": "S1", "severity": "S", "path": "handler.go", "body": ""}
        detail = ro._verification_detail(finding, "result := db.Query(q)", str(tmp_path))
        assert detail["match_result"] is False
        assert "longest_match_prefix" in detail
        assert "first_mismatch" in detail


class TestVerifyFindings:
    def test_returns_verification_summary(self, ro, tmp_path):
        src = tmp_path / "handler.go"
        src.write_text("package main\n\nfunc foo() {\n\tresult := db.Query(q)\n}\n")
        text = (
            "## Should fix\n"
            "- [ ] **[S1]** **`handler.go:42`** — desc\n"
            "  > ```go\n"
            "  > result := db.Query(q)\n"
            "  > ```\n"
        )
        out_text, result = ro.verify_findings(text, str(tmp_path))
        assert result["dropped"] == []
        assert result["findings_checked"] == 1
        assert result["findings_passed"] == 1
        assert result["findings_dropped"] == 0
        assert out_text == text

    def test_drops_unverified_and_returns_details(self, ro, tmp_path):
        src = tmp_path / "handler.go"
        src.write_text("package main\n\nfunc foo() {\n\tx := 1\n}\n")
        text = (
            "## Should fix\n"
            "- [ ] **[S1]** **`handler.go:42`** — desc\n"
            "  > ```go\n"
            "  > result := db.Query(q)\n"
            "  > ```\n"
        )
        out_text, result = ro.verify_findings(text, str(tmp_path))
        assert result["dropped"] == ["S1"]
        assert result["findings_dropped"] == 1
        assert result["details"][0]["match_result"] is False
        assert "S1" not in out_text


class TestParseVerificationStripsLine:
    def test_path_excludes_line_number(self, ro):
        text = '- **[M1]** **`pkg/handler.go:42`** — missing error check\n'
        findings = ro._parse_findings_for_verification(text)
        assert findings[0]["path"] == "pkg/handler.go"

    def test_path_excludes_line_range(self, ro):
        text = '- **[S1]** **`pkg/handler.go:10-20`** — issue\n'
        findings = ro._parse_findings_for_verification(text)
        assert findings[0]["path"] == "pkg/handler.go"

    def test_checkbox_path_excludes_line(self, ro):
        text = '- [ ] **[M1]** `handler.go:42` — desc\n'
        findings = ro._parse_findings_for_verification(text)
        assert findings[0]["path"] == "handler.go"


class TestStripEvidenceBlocks:
    def test_strips_evidence_preserves_finding(self, ro):
        text = (
            "## Must fix\n"
            "- **[M1]** **`file.go:42`** — missing error check\n"
            "  > ```go\n"
            "  > result := db.Query(q)\n"
            "  > ```\n"
            "## Nit\n"
            "- **[N1]** **`file.go:10`** — rename var\n"
        )
        result = ro.strip_evidence_blocks(text)
        assert "```go" not in result
        assert "result := db.Query" not in result
        assert "**[M1]**" in result
        assert "missing error check" in result
        assert "**[N1]**" in result

    def test_no_evidence_blocks_unchanged(self, ro):
        content = "## Must fix\n- **[M1]** **`file.go:42`** — finding\n"
        assert ro.strip_evidence_blocks(content) == content

    def test_top_level_blockquote_preserved(self, ro):
        content = (
            "## Summary\n"
            "> ```go\n"
            "> example code\n"
            "> ```\n"
            "## Must fix\n"
            "- **[M1]** **`file.go:42`** — finding\n"
        )
        result = ro.strip_evidence_blocks(content)
        assert "> ```go" in result

    def test_strips_unfenced_blockquote_evidence(self, ro):
        text = (
            "## Should fix\n"
            "- **[S1]** **`docs/overview.md:511`** — stale config section\n"
            "  > # Team roster\n"
            "  > team:\n"
            "  >   - first_name: David\n"
            "  >\n"
            "  > docker run maximus daemon\n"
            "## Nit\n"
            "- **[N1]** **`file.go:10`** — rename var\n"
        )
        result = ro.strip_evidence_blocks(text)
        assert "Team roster" not in result
        assert "docker run" not in result
        assert "**[S1]**" in result
        assert "stale config section" in result
        assert "**[N1]**" in result


class TestStripStableIds:
    def test_removes_sid_comments(self, ro):
        text = '- **[M1]** <!-- sid:abc12345 --> **`file.go:42`** — desc\n'
        result = ro.strip_stable_ids(text)
        assert "<!-- sid:" not in result
        assert "**[M1]** **`file.go:42`**" in result

    def test_no_sids_unchanged(self, ro):
        content = "- **[M1]** **`file.go:42`** — desc\n"
        assert ro.strip_stable_ids(content) == content


class TestFindingPathReCheckbox:
    def test_matches_checkbox_format(self, ro):
        line = '- [ ] **[M1]** **`handler.go:42`** — desc'
        m = ro._FINDING_PATH_RE.match(line)
        assert m is not None
        path = (m.group(1) or m.group(2) or "")
        assert "handler.go" in path

    def test_matches_with_stable_id(self, ro):
        line = '- **[M1]** <!-- sid:abc --> **`handler.go:42`** — desc'
        m = ro._FINDING_PATH_RE.match(line)
        assert m is not None


class TestComputeStableId:
    def test_deterministic(self, ro):
        a = ro.compute_stable_id("pkg/handler.go", "missing error check on db.Query()")
        b = ro.compute_stable_id("pkg/handler.go", "missing error check on db.Query()")
        assert a == b

    def test_eight_hex_chars(self, ro):
        sid = ro.compute_stable_id("file.go", "desc")
        assert len(sid) == 8
        assert all(c in "0123456789abcdef" for c in sid)

    def test_case_insensitive_path(self, ro):
        a = ro.compute_stable_id("Pkg/Handler.go", "desc")
        b = ro.compute_stable_id("pkg/handler.go", "desc")
        assert a == b

    def test_different_descriptions_differ(self, ro):
        a = ro.compute_stable_id("file.go", "missing error check")
        b = ro.compute_stable_id("file.go", "unused import")
        assert a != b

    def test_truncates_description_at_80(self, ro):
        desc_80 = "x" * 80
        desc_100 = desc_80 + "y" * 20
        assert ro.compute_stable_id("f.go", desc_80) == ro.compute_stable_id("f.go", desc_100)


class TestAnnotatePriorWithStableIds:
    def test_inserts_sid_comment(self, ro):
        text = '- **[M1]** **`handler.go:42`** — missing error check\n'
        result = ro.annotate_prior_with_stable_ids(text)
        assert "<!-- sid:" in result
        assert "**[M1]**" in result
        assert "handler.go:42" in result

    def test_checkbox_format(self, ro):
        text = '- [ ] **[S1]** `handler.go:42` — missing check\n'
        result = ro.annotate_prior_with_stable_ids(text)
        assert "<!-- sid:" in result

    def test_non_finding_lines_unchanged(self, ro):
        text = "## Summary\nThis is a summary.\n"
        result = ro.annotate_prior_with_stable_ids(text)
        assert result == text

    def test_deterministic_ids(self, ro):
        text = '- **[M1]** **`handler.go:42`** — missing error check\n'
        a = ro.annotate_prior_with_stable_ids(text)
        b = ro.annotate_prior_with_stable_ids(text)
        assert a == b


class TestCleanSectionText:
    def test_strips_none_markers(self, ro):
        assert ro._clean_section_text("_None._") == ""
        assert ro._clean_section_text("_(none)_") == ""

    def test_strips_horizontal_rules(self, ro):
        assert ro._clean_section_text("---") == ""

    def test_case_insensitive(self, ro):
        assert ro._clean_section_text("_NONE._") == ""
        assert ro._clean_section_text("_None._") == ""

    def test_preserves_findings(self, ro):
        text = "- **[M1]** **`file.go:42`** — finding"
        assert ro._clean_section_text(text) == text

    def test_strips_markers_around_findings(self, ro):
        text = "_None._\n---\n- **[M1]** **`file.go:42`** — finding\n---\n_None._"
        result = ro._clean_section_text(text)
        assert result == "- **[M1]** **`file.go:42`** — finding"

    def test_empty_input(self, ro):
        assert ro._clean_section_text("") == ""

    def test_only_markers_returns_empty(self, ro):
        assert ro._clean_section_text("_None._\n---\n_(none)_") == ""

    def test_strips_none_in_file_group(self, ro):
        assert ro._clean_section_text("_None in this file group._") == ""

    def test_strips_none_in_file_group_mixed_case(self, ro):
        assert ro._clean_section_text("_NONE IN THIS FILE GROUP._") == ""

    def test_preserves_findings_around_file_group_marker(self, ro):
        text = "_None in this file group._\n- **[M1]** **`file.go:42`** — finding"
        result = ro._clean_section_text(text)
        assert result == "- **[M1]** **`file.go:42`** — finding"


class TestCountFindings:
    def test_counts_unique_ids(self, ro):
        text = "- **[M1]** finding\n- **[M2]** another"
        counts = ro._count_findings(text)
        assert counts["M"] == 2

    def test_no_findings(self, ro):
        counts = ro._count_findings("no findings here")
        assert all(v == 0 for v in counts.values())

    def test_mixed_severities(self, ro):
        text = "- **[M1]** must\n- **[S1]** should\n- **[N1]** nit\n- **[I1]** idiom"
        counts = ro._count_findings(text)
        assert counts["M"] == 1
        assert counts["S"] == 1
        assert counts["N"] == 1
        assert counts["I"] == 1

    def test_cross_references_not_counted(self, ro):
        text = (
            "## Nit\n"
            "- **[N1]** **`file.go:10`** — resolving [M1], see also S2\n"
            "- **[N2]** **`file.go:20`** — once [M1] is resolved, update this\n"
        )
        counts = ro._count_findings(text)
        assert counts["M"] == 0
        assert counts["N"] == 2

    def test_checkbox_findings_counted(self, ro):
        text = "- [ ] **[S1]** **`file.go:10`** — open finding"
        counts = ro._count_findings(text)
        assert counts["S"] == 1


class TestMergeReviewsCleanup:
    def test_empty_markers_excluded_from_merge(self, ro, tmp_path):
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
        result = ro.merge_reviews([str(g1)])
        assert "_None._" not in result
        assert "_(none)_" not in result
        assert "## Must fix" not in result
        assert "## Nit" in result
        assert "[N1]" in result

    def test_separators_excluded_from_merge(self, ro, tmp_path):
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
        result = ro.merge_reviews([str(g1)])
        assert "---" not in result


PRIOR_ONE_FINDING = (
    "## Must fix\n"
    "- **[M1]** **`handler.go:42`** — missing error check\n"
)


class TestPriorDisposition:
    def test_parses_the_word_the_prompt_asks_for(self, ro):
        assert ro.PriorDisposition.parse("Fixed") is ro.PriorDisposition.FIXED
        assert (
            ro.PriorDisposition.parse("Still open — see below")
            is ro.PriorDisposition.STILL_OPEN
        )

    def test_unrecognised_wording_has_no_disposition(self, ro):
        assert ro.PriorDisposition.parse("moved to a follow-up") is None

    def test_a_qualified_verdict_is_not_read_as_its_optimistic_half(self, ro):
        assert ro.PriorDisposition.parse("Fixed, but only on the happy path") is None
        assert ro.PriorDisposition.parse("Fixed in a follow-up branch") is None


class TestUnaccountedPriorFindings:
    def test_empty_prior(self, ro):
        assert ro.unaccounted_prior_findings("", "<!-- sid:abc -->") == []

    def test_sid_marker_carries_finding_forward(self, ro):
        review = (
            "## Must fix\n"
            "- **[M1]** <!-- sid:deadbeef --> **`other.go:1`** — reworded\n"
        )
        sid = ro.compute_stable_id("handler.go", "missing error check")
        review = review.replace("deadbeef", sid)
        assert ro.unaccounted_prior_findings(PRIOR_ONE_FINDING, review) == []

    def test_verbatim_carry_forward_without_marker(self, ro):
        # Synthesis retypes carried findings and drops the marker; the ID the
        # line hashes to is what survives.
        review = (
            "## Must fix\n"
            "- **[M2]** **`handler.go:42`** — missing error check\n"
        )
        assert ro.unaccounted_prior_findings(PRIOR_ONE_FINDING, review) == []

    def test_ledger_entry_accounts_for_fixed_finding(self, ro):
        review = (
            "## Summary\nAll prior findings addressed.\n"
            f"## {ro.SECTION_PRIOR_FINDINGS}\n"
            "- **[M1]** `handler.go` — Fixed\n"
        )
        assert ro.unaccounted_prior_findings(PRIOR_ONE_FINDING, review) == []

    def test_ledger_matches_reworded_finding_by_path(self, ro):
        review = (
            "## Must fix\n"
            "- **[M4]** **`handler.go:42`** — db.Query() error is discarded\n"
            f"## {ro.SECTION_PRIOR_FINDINGS}\n"
            "- **[M1]** `handler.go` — Still open\n"
        )
        assert ro.unaccounted_prior_findings(PRIOR_ONE_FINDING, review) == []

    def test_reports_finding_the_review_dropped(self, ro):
        review = "## Must fix\n- **[M1]** **`other.go:7`** — unrelated issue\n"
        assert ro.unaccounted_prior_findings(PRIOR_ONE_FINDING, review) == [
            "M1 `handler.go`",
        ]

    def test_ledger_entry_may_carry_a_line_number(self, ro):
        review = (
            f"## {ro.SECTION_PRIOR_FINDINGS}\n"
            "- **[M1]** `handler.go:42` — Fixed\n"
        )
        assert ro.unaccounted_prior_findings(PRIOR_ONE_FINDING, review) == []

    def test_one_ledger_entry_does_not_cover_a_sibling_in_the_same_file(self, ro):
        prior = (
            PRIOR_ONE_FINDING
            + "- **[M2]** **`handler.go:88`** — unchecked type assertion\n"
        )
        review = (
            f"## {ro.SECTION_PRIOR_FINDINGS}\n"
            "- **[M1]** `handler.go` — Fixed\n"
        )
        assert ro.unaccounted_prior_findings(prior, review) == ["M2 `handler.go`"]

    def test_reports_only_the_unaccounted_one(self, ro):
        prior = PRIOR_ONE_FINDING + "- **[M2]** **`cache.go:9`** — stale entry\n"
        review = (
            f"## {ro.SECTION_PRIOR_FINDINGS}\n"
            "- **[M1]** `handler.go` — Fixed\n"
        )
        assert ro.unaccounted_prior_findings(prior, review) == ["M2 `cache.go`"]


# ── 1. _mechanical_verdict ───────────────────────────────────────────────────


class TestMechanicalVerdict:
    def test_must_fix_present(self, ro):
        counts = {"M": 2, "S": 1,
                  "N": 0, "I": 0}
        result = ro.mechanical_verdict(counts)
        assert result.startswith("Request changes")

    def test_should_fix_no_must(self, ro):
        counts = {"M": 0, "S": 3,
                  "N": 1, "I": 0}
        result = ro.mechanical_verdict(counts)
        assert result.startswith("Needs discussion")

    def test_nits_and_idioms_only(self, ro):
        counts = {"M": 0, "S": 0,
                  "N": 2, "I": 1}
        result = ro.mechanical_verdict(counts)
        assert result.startswith("Approve")
        assert "2 nit" in result
        assert "1 idiom" in result

    def test_no_findings(self, ro):
        counts = {"M": 0, "S": 0,
                  "N": 0, "I": 0}
        result = ro.mechanical_verdict(counts)
        assert "no findings" in result

    def test_zero_counts_for_some(self, ro):
        counts = {"M": 1, "S": 0,
                  "N": 0, "I": 0}
        result = ro.mechanical_verdict(counts)
        assert "Request changes" in result
        assert "1 must-fix" in result
        assert "should-fix" not in result


# ── 2. _has_findings ────────────────────────────────────────────────────────


class TestHasFindings:
    def test_with_must_fix(self, ro):
        assert ro._has_findings("found [M1] issue") is True

    def test_with_should_fix(self, ro):
        assert ro._has_findings("found [S3] issue") is True

    def test_with_nit(self, ro):
        assert ro._has_findings("found [N2] issue") is True

    def test_with_idiom(self, ro):
        assert ro._has_findings("found [I1] issue") is True

    def test_no_findings(self, ro):
        assert ro._has_findings("no issues here at all") is False

    def test_cross_references_still_match(self, ro):
        # Simple regex matches any [M\d+] including cross-references
        assert ro._has_findings("see [M1] for context") is True


# ── 3. renumber_section ─────────────────────────────────────────────────────


class TestRenumberSection:
    def test_offset_zero(self, ro):
        text = "- **[S1]** finding\n- **[S2]** another"
        result, count = ro.renumber_section("S", text, 0)
        assert result == text
        assert count == 2

    def test_positive_offset(self, ro):
        text = "- **[S1]** finding\n- **[S2]** another"
        result, count = ro.renumber_section("S", text, 3)
        assert "[S4]" in result
        assert "[S5]" in result
        assert count == 2

    def test_dedup_count(self, ro):
        text = "- **[M1]** finding\n  see [M1] above"
        result, count = ro.renumber_section("M", text, 0)
        assert count == 1

    def test_offset_carries_references(self, ro):
        # The offset is what keeps two groups' IDs apart. A reference left behind
        # would name whatever the earlier group happened to put at that number.
        text = "- **[S1]** first\n- **[S2]** second, see S1 above and [S1] again"
        result, _ = ro.renumber_section("S", text, 2)
        assert "see S3 above and [S3] again" in result

    def test_offset_shifts_ids_it_did_not_expect(self, ro):
        # IDs arrive however the agent wrote them; gaps are closed later, not here.
        result, highest = ro.renumber_section("S", "- **[S1]** first\n- **[S7]** second", 2)
        assert "[S3]" in result
        assert "[S9]" in result
        # What the next group has to clear, not how many findings this one had.
        assert highest == 7

    def test_a_reference_this_group_cannot_resolve_is_left_alone(self, ro):
        # Only the merge-wide pass can tell a dangling reference from one whose
        # finding lives in another group.
        text = "- **[S1]** first, see S4 elsewhere"
        result, _ = ro.renumber_section("S", text, 2)
        assert "see S4 elsewhere" in result

    def test_empty_text(self, ro):
        result, count = ro.renumber_section("M", "", 0)
        assert result == ""
        assert count == 0


# ── 4. _renumber_prefix ─────────────────────────────────────────────────────


def _decl(prefix: str, num: int, body: str = "finding") -> str:
    """A finding declaring its own ID — the only thing renumbering numbers."""
    return f"- **[{prefix}{num}]** **`file.go:{num}`** — {body}"


class TestRenumberPrefix:
    def test_sequential_already(self, ro):
        text = f"{_decl('S', 1, 'first')}\n{_decl('S', 2, 'second')}"
        assert ro._renumber_prefix(text, "S") == text

    def test_with_gaps(self, ro):
        text = f"{_decl('S', 1, 'first')}\n{_decl('S', 3, 'third')}"
        result = ro._renumber_prefix(text, "S")
        assert "[S1]" in result
        assert "[S2]" in result
        assert "[S3]" not in result

    def test_repeated_ids(self, ro):
        text = "\n".join([
            _decl("S", 3, "first"), _decl("S", 1, "second"), _decl("S", 3, "repeat"),
        ])
        result = ro._renumber_prefix(text, "S")
        assert result.count("[S1]") == 2  # S3 appears first -> becomes S1
        assert "[S2]" in result  # S1 appears second -> becomes S2

    def test_unbracketed_cross_refs(self, ro):
        text = f"{_decl('S', 3)}\nsee S3 above"
        result = ro._renumber_prefix(text, "S")
        assert "see S1 above" in result
        assert "S3" not in result

    def test_reference_to_a_dropped_finding_points_nowhere(self, ro):
        # S1 was dropped by verification, so only its reference is left. Closing
        # the gap on S2 frees up the number 1, and the reference must not take it.
        text = f"{_decl('S', 2, 'real problem')}\nblocked on [S1]"
        result = ro._renumber_prefix(text, "S")
        assert "- **[S1]** **`file.go:2`** — real problem" in result
        assert "blocked on [removed]" in result

    def test_bare_reference_to_a_dropped_finding_points_nowhere(self, ro):
        text = f"{_decl('S', 2, 'real problem')}\nblocked on S1"
        assert "blocked on [removed]" in ro._renumber_prefix(text, "S")

    def test_prose_that_merely_looks_like_an_id_is_left_alone(self, ro):
        # S3 the object store, M1 the laptop. Nothing cites them, so nothing
        # may rewrite them — and a review of storage code says "S3" constantly.
        text = "\n".join([
            _decl("S", 3, "uploads to an S3 bucket on every M1 build"),
            _decl("S", 5, "second"),
        ])
        result = ro._renumber_prefix(text, "S")
        assert "uploads to an S3 bucket on every M1 build" in result
        assert "- **[S1]**" in result
        assert "- **[S2]**" in result

    def test_a_cited_bare_reference_is_still_rewritten(self, ro):
        text = f"{_decl('S', 3, 'first')}\n{_decl('S', 5, 'second, duplicate of S3')}"
        assert "duplicate of S1" in ro._renumber_prefix(text, "S")

    def test_references_survive_a_second_pass(self, ro):
        text = f"{_decl('S', 2, 'real problem')}\nblocked on [S1]"
        once = ro._renumber_prefix(text, "S")
        assert ro._renumber_prefix(once, "S") == once

    def test_text_that_declares_nothing_is_left_alone(self, ro):
        # A section can mention IDs it does not own — the triage list, a prior
        # review's ledger. With no declaration there is no map to rewrite through.
        text = "carried over from [S4] and [S7]"
        assert ro._renumber_prefix(text, "S") == text

    def test_checklist_findings_declare_their_ids(self, ro):
        # Self-review writes findings as checkboxes; they are declarations too.
        text = "- [ ] **[S3]** `file.go:1` — finding\nsee [S3]"
        result = ro._renumber_prefix(text, "S")
        assert "- [ ] **[S1]** `file.go:1` — finding" in result
        assert "see [S1]" in result

    def test_empty_text(self, ro):
        assert ro._renumber_prefix("", "S") == ""


# ── 5. renumber_findings ────────────────────────────────────────────────────


class TestRenumberFindings:
    def test_renumbers_gaps(self, ro):
        text = "\n".join([
            _decl("M", 1, "first"), _decl("M", 3, "third"),
            _decl("S", 1, "s1"), _decl("S", 5, "s5"), "",
        ])
        result = ro.renumber_findings(text)
        assert "[M1]" in result
        assert "[M2]" in result
        assert "[M3]" not in result
        assert "[S1]" in result
        assert "[S2]" in result
        assert "[S5]" not in result

    def test_each_severity_is_renumbered_independently(self, ro):
        # A Nit citing a dropped Must-fix loses the citation; its own ID does not
        # move, because the M pass never looks at N numbers.
        text = "\n".join([
            _decl("M", 2, "kept"),
            "- **[N1]** **`file.go:9`** — revisit once [M1] lands",
        ])
        result = ro.renumber_findings(text)
        assert "- **[M1]** **`file.go:2`** — kept" in result
        assert "revisit once [removed] lands" in result

    def test_empty_text(self, ro):
        assert ro.renumber_findings("") == ""

    def test_no_findings_unchanged(self, ro):
        content = "No findings here.\n"
        assert ro.renumber_findings(content) == content


# ── 6. _extract_section ─────────────────────────────────────────────────────


class TestExtractSection:
    def test_middle_section(self, ro):
        content = "## First\nfirst content\n## Second\nsecond content\n## Third\nthird content\n"
        result = ro._extract_section(content, "Second")
        assert result == "second content"

    def test_last_section(self, ro):
        content = "## First\nfirst\n## Last\nlast content\n"
        result = ro._extract_section(content, "Last")
        assert result == "last content"

    def test_missing_section(self, ro):
        content = "## First\nfirst content\n"
        assert ro._extract_section(content, "Missing") == ""

    def test_case_insensitive(self, ro):
        content = "## must fix\nfinding here\n## Other\nother\n"
        result = ro._extract_section(content, "Must fix")
        assert result == "finding here"


# ── 7. merge_reviews ────────────────────────────────────────────────────────


class TestMergeReviews:
    def test_merge_different_sections(self, ro, tmp_path):
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
        result = ro.merge_reviews([str(g1), str(g2)])
        assert "[M1]" in result
        assert "[S1]" in result
        assert "`a.go`" in result
        assert "`b.go`" in result

    def test_merge_duplicate_findings(self, ro, tmp_path):
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
        result = ro.merge_reviews([str(g1), str(g2)])
        # Dedup should remove duplicate finding
        assert result.count("same issue") == 1

    def test_merge_renumbering(self, ro, tmp_path):
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
        result = ro.merge_reviews([str(g1), str(g2)])
        assert "[S1]" in result
        assert "[S2]" in result

    def test_merge_keeps_each_groups_references_inside_that_group(self, ro, tmp_path):
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
        result = ro.merge_reviews([str(g1), str(g2)])

        assert "- **[S2]** **`a.go:2`** — issue b, related to [S1]" in result
        assert "- **[S4]** **`b.go:2`** — issue d, see S3 above" in result

    def test_merge_clears_a_gap_the_first_group_left(self, ro, tmp_path):
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
        result = ro.merge_reviews([str(g1), str(g2)])

        # Offsetting by the count would put issue c on S3, where issue b already
        # sits; the gaps close afterwards, so all three come out distinct.
        assert "- **[S1]** **`a.go:1`** — issue a" in result
        assert "- **[S2]** **`a.go:3`** — issue b" in result
        assert "- **[S3]** **`b.go:1`** — issue c" in result

    def test_merge_unions_prior_findings_ledgers(self, ro, tmp_path):
        g1 = tmp_path / "g1.md"
        g1.write_text(
            "## File Triage\n- `a.go` — reviewed\n"
            "## Must fix\n- **[M1]** **`a.go:1`** — new issue\n"
            f"## {ro.SECTION_PRIOR_FINDINGS}\n- **[M3]** `a.go` — Fixed\n"
        )
        g2 = tmp_path / "g2.md"
        g2.write_text(
            "## File Triage\n- `b.go` — reviewed\n"
            f"## {ro.SECTION_PRIOR_FINDINGS}\n"
            "- **[M3]** `a.go` — Fixed\n"
            "- **[S2]** `b.go` — Still open\n"
        )
        result = ro.merge_reviews([str(g1), str(g2)])
        assert result.count("**[M3]** `a.go` — Fixed") == 1
        assert "**[S2]** `b.go` — Still open" in result
        # Ledger IDs name the prior review, so the merge must not renumber them
        # into the sequence it assigns this review's findings.
        assert "- **[M1]** **`a.go:1`** — new issue" in result

    def test_merge_keeps_the_still_open_verdict_when_groups_disagree(self, ro, tmp_path):
        g1 = tmp_path / "g1.md"
        g1.write_text(
            "## File Triage\n- `a.go` — reviewed\n"
            f"## {ro.SECTION_PRIOR_FINDINGS}\n- **[M3]** `a.go` — Fixed\n"
        )
        g2 = tmp_path / "g2.md"
        g2.write_text(
            "## File Triage\n- `a.go` — reviewed\n"
            f"## {ro.SECTION_PRIOR_FINDINGS}\n- **[M3]** `a.go` — Still open\n"
        )
        result = ro.merge_reviews([str(g1), str(g2)])
        assert "**[M3]** `a.go` — Still open" in result
        assert "Fixed" not in result

    def test_merge_omits_ledger_when_no_group_has_one(self, ro, tmp_path):
        g1 = tmp_path / "g1.md"
        g1.write_text(
            "## File Triage\n- `a.go` — reviewed\n"
            "## Must fix\n- **[M1]** **`a.go:1`** — issue\n"
        )
        assert ro.SECTION_PRIOR_FINDINGS not in ro.merge_reviews([str(g1)])

    def test_missing_file_skipped(self, ro, tmp_path):
        g1 = tmp_path / "g1.md"
        g1.write_text(
            "## File Triage\n- `a.go` — reviewed\n"
            "## Must fix\n- **[M1]** **`a.go:1`** — issue\n"
            "## Should fix\n_None._\n## Nit\n_None._\n## Idioms\n_None._\n"
        )
        result = ro.merge_reviews([str(g1), str(tmp_path / "missing.md")])
        assert "[M1]" in result

    def test_empty_group_file(self, ro, tmp_path):
        g1 = tmp_path / "g1.md"
        g1.write_text("")
        result = ro.merge_reviews([str(g1)])
        assert "## File Triage" in result


# ── 8. _clean_triage ────────────────────────────────────────────────────────


class TestCleanTriage:
    def test_valid_triage_lines(self, ro):
        text = "- `file.go` reviewed\n- `other.go` skimmed"
        result = ro._clean_triage(text)
        assert "file.go" in result
        assert "other.go" in result

    def test_mixed_valid_invalid(self, ro):
        text = "- `file.go` reviewed\nsome other text\n- `b.go` done"
        result = ro._clean_triage(text)
        assert "file.go" in result
        assert "b.go" in result
        assert "some other text" not in result

    def test_empty_input(self, ro):
        assert ro._clean_triage("") == ""


# ── 9. _dedup_triage ────────────────────────────────────────────────────────


class TestDedupTriage:
    def test_with_duplicates(self, ro):
        text = "- `file.go` — reviewed\n- `file.go` — reviewed again"
        result = ro._dedup_triage(text)
        assert result.count("file.go") == 1

    def test_no_duplicates(self, ro):
        text = "- `a.go` — reviewed\n- `b.go` — reviewed"
        result = ro._dedup_triage(text)
        assert "a.go" in result
        assert "b.go" in result

    def test_empty_input(self, ro):
        assert ro._dedup_triage("") == ""


# ── 10. _finding_dedup_key ──────────────────────────────────────────────────


class TestFindingDedupKey:
    def test_standard_finding(self, ro):
        line = "- **[M1]** **`pkg/handler.go:42`** — missing error check"
        result = ro._finding_dedup_key(line)
        assert result is not None
        assert "handler.go" in result.path
        assert "missing error check" in result.desc

    def test_checkbox_finding(self, ro):
        line = "- [ ] **[S1]** **`handler.go:10`** — issue here"
        result = ro._finding_dedup_key(line)
        assert result is not None
        assert "handler.go" in result.path

    def test_stable_id(self, ro):
        line = "- **[M1]** <!-- sid:abc12345 --> **`file.go:1`** — desc"
        result = ro._finding_dedup_key(line)
        assert result is not None
        assert "file.go" in result.path

    def test_non_finding_line(self, ro):
        assert ro._finding_dedup_key("just some text") is None


# ── 11. _dedup_findings ─────────────────────────────────────────────────────


class TestDedupFindings:
    def test_with_duplicates(self, ro):
        text = (
            "- **[M1]** **`file.go:1`** — same issue\n"
            "- **[M2]** **`file.go:1`** — same issue\n"
        )
        result = ro._dedup_findings(text, "M")
        assert result.count("same issue") == 1

    def test_no_duplicates(self, ro):
        text = (
            "- **[M1]** **`a.go:1`** — issue a\n"
            "- **[M2]** **`b.go:2`** — issue b\n"
        )
        result = ro._dedup_findings(text, "M")
        assert "issue a" in result
        assert "issue b" in result

    def test_multiline_continuation(self, ro):
        text = (
            "- **[M1]** **`file.go:1`** — same issue\n"
            "  continuation line\n"
            "- **[M2]** **`file.go:1`** — same issue\n"
            "  another continuation\n"
        )
        result = ro._dedup_findings(text, "M")
        assert result.count("same issue") == 1
        assert "another continuation" not in result

    def test_references_follow_the_surviving_copy(self, ro):
        # M2 is the same finding as M1, so a reference to it is not dangling —
        # it belongs on the copy that stayed.
        text = (
            "- **[M1]** **`file.go:1`** — same issue\n"
            "- **[M2]** **`file.go:1`** — same issue\n"
            "- **[M3]** **`other.go:2`** — see [M2] for context\n"
        )
        result = ro._dedup_findings(text, "M")
        assert "- **[M2]** **`other.go:2`** — see [M1] for context" in result


# ── 12. _parse_numstat ──────────────────────────────────────────────────────


class TestParseNumstat:
    def test_normal_output(self, ro):
        numstat = "10\t5\tpkg/handler.go\n3\t1\tpkg/util.go\n"
        files, total_add, total_del = ro._parse_numstat(numstat)
        assert len(files) == 2
        assert files[0] == {"path": "pkg/handler.go", "additions": 10, "deletions": 5}
        assert total_add == 13
        assert total_del == 6

    def test_binary_files(self, ro):
        numstat = "-\t-\timage.png\n5\t2\tfile.go\n"
        files, total_add, total_del = ro._parse_numstat(numstat)
        assert len(files) == 2
        assert files[0]["additions"] == 0
        assert files[0]["deletions"] == 0
        assert total_add == 5
        assert total_del == 2

    def test_empty_input(self, ro):
        files, total_add, total_del = ro._parse_numstat("")
        assert files == []
        assert total_add == 0
        assert total_del == 0


# ── 13. _scope_diff ─────────────────────────────────────────────────────────


class TestScopeDiff:
    def test_filter_one_of_two(self, ro):
        diff = (
            "diff --git a/file1.go b/file1.go\n"
            "--- a/file1.go\n+++ b/file1.go\n@@ -1 +1 @@\n-old\n+new\n"
            "diff --git a/file2.go b/file2.go\n"
            "--- a/file2.go\n+++ b/file2.go\n@@ -1 +1 @@\n-old2\n+new2\n"
        )
        result = ro._scope_diff(diff, ["file1.go"])
        assert "file1.go" in result
        assert "file2.go" not in result

    def test_filter_two_of_three(self, ro):
        diff = (
            "diff --git a/foo.go b/foo.go\n"
            "--- a/foo.go\n+++ b/foo.go\n@@ -1,3 +1,4 @@\n package main\n+import \"fmt\"\n\n"
            "diff --git a/bar.go b/bar.go\n"
            "--- a/bar.go\n+++ b/bar.go\n@@ -1,3 +1,3 @@\n-package old\n+package bar\n\n"
            "diff --git a/baz.go b/baz.go\n"
            "--- a/baz.go\n+++ b/baz.go\n@@ -1 +1 @@\n-old\n+new\n"
        )
        result = ro._scope_diff(diff, ["foo.go", "baz.go"])
        assert "foo.go" in result
        assert "bar.go" not in result
        assert "baz.go" in result

    def test_filter_no_match(self, ro):
        diff = "diff --git a/file1.go b/file1.go\n--- a/file1.go\n+++ b/file1.go\n"
        result = ro._scope_diff(diff, ["other.go"])
        assert result == ""

    def test_filter_all_files(self, ro):
        diff = (
            "diff --git a/a.go b/a.go\ncontent a\n"
            "diff --git a/b.go b/b.go\ncontent b\n"
        )
        result = ro._scope_diff(diff, ["a.go", "b.go"])
        assert "a.go" in result
        assert "b.go" in result


# ── 14. _truncate_diff ──────────────────────────────────────────────────────


class TestTruncateDiff:
    def test_under_budget(self, ro):
        diff = "diff --git a/f.go b/f.go\nshort\n"
        result, omitted = ro._truncate_diff(diff, 10000)
        assert result == diff
        assert omitted == []

    def test_over_budget(self, ro):
        diff = (
            "diff --git a/a.go b/a.go\n" + "+" * 500 + "\n"
            "diff --git a/b.go b/b.go\n" + "+" * 500 + "\n"
        )
        result, omitted = ro._truncate_diff(diff, 600)
        assert len(omitted) > 0

    def test_single_file_over_budget(self, ro):
        diff = "diff --git a/big.go b/big.go\n" + "x" * 5000 + "\n"
        result, omitted = ro._truncate_diff(diff, 100)
        assert len(result.encode()) < len(diff.encode())

    def test_empty_diff(self, ro):
        result, omitted = ro._truncate_diff("", 1000)
        assert result == ""
        assert omitted == []


# ── 15. _read_file_safe ─────────────────────────────────────────────────────


class TestReadFileSafe:
    def test_normal_read(self, ro, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        assert ro._read_file_safe(f) == "hello world"

    def test_binary_file(self, ro, tmp_path):
        f = tmp_path / "binary.bin"
        f.write_bytes(b"\x80\x81\x82\xff\xfe")
        result = ro._read_file_safe(f)
        assert "binary file" in result

    def test_file_not_found(self, ro, tmp_path):
        result = ro._read_file_safe(tmp_path / "missing.txt")
        assert result == "<file deleted>"

    def test_large_file_truncation(self, ro, tmp_path):
        f = tmp_path / "large.txt"
        # MAX_FILE_BYTES is 100_000, so write more than that
        f.write_text("x" * 200_000)
        result = ro._read_file_safe(f)
        assert "truncated" in result

    def test_permission_denied(self, ro, tmp_path):
        import os
        f = tmp_path / "noperm.txt"
        f.write_text("secret")
        os.chmod(str(f), 0o000)
        try:
            result = ro._read_file_safe(f)
            assert result == "<permission denied>"
        finally:
            os.chmod(str(f), 0o644)


# ── 16. _remove_dropped_findings ────────────────────────────────────────────


class TestRemoveDroppedFindings:
    def test_remove_single(self, ro):
        text = (
            "## Must fix\n"
            "- **[M1]** **`file.go:1`** — issue one\n"
            "- **[M2]** **`file.go:5`** — issue two\n"
        )
        result = ro._remove_dropped_findings(text, ["M1"])
        assert "issue one" not in result
        assert "issue two" in result

    def test_remove_with_continuation(self, ro):
        text = (
            "- **[M1]** **`file.go:1`** — issue one\n"
            "  continuation line\n"
            "  more detail\n"
            "- **[M2]** **`file.go:5`** — issue two\n"
        )
        result = ro._remove_dropped_findings(text, ["M1"])
        assert "issue one" not in result
        assert "continuation line" not in result
        assert "issue two" in result

    def test_empty_dropped_list(self, ro):
        text = "- **[M1]** **`file.go:1`** — issue\n"
        assert ro._remove_dropped_findings(text, []) == text

    def test_remove_last_finding(self, ro):
        text = (
            "## Must fix\n"
            "- **[M1]** **`file.go:1`** — only finding\n"
        )
        result = ro._remove_dropped_findings(text, ["M1"])
        assert "only finding" not in result
        assert "## Must fix" in result


# ── 17. _is_next_finding_or_section ─────────────────────────────────────────


class TestIsNextFindingOrSection:
    def test_finding_line(self, ro):
        assert ro._is_next_finding_or_section('- **[M1]** desc') is True

    def test_checkbox_finding(self, ro):
        assert ro._is_next_finding_or_section('- [ ] **[S1]** desc') is True

    def test_section_header(self, ro):
        assert ro._is_next_finding_or_section('## Must fix') is True

    def test_plain_text(self, ro):
        assert ro._is_next_finding_or_section('just plain text') is False

    def test_continuation_line(self, ro):
        assert ro._is_next_finding_or_section('  continuation') is False

    def test_bullet_no_finding(self, ro):
        assert ro._is_next_finding_or_section('- regular bullet') is False


# ── 18. _check_serial_abort ─────────────────────────────────────────────────


class TestCheckSerialAbort:
    def _diagnosis(self, ro, detail: str):
        return ro.Diagnosis(ro.DiagnosisKind.AGENT_ERROR, detail=detail)

    def test_model_error(self, ro, tmp_path):
        log = tmp_path / "session.jsonl"
        log.write_text(json.dumps({
            "type": "result", "api_error_status": 404,
        }) + "\n")
        msg, consec, last = ro._check_serial_abort(
            1, 5, self._diagnosis(ro, "error"), str(log), 0, None,
        )
        assert "Model not available" in msg
        assert "4" in msg  # 5 - 1 = 4 remaining

    def test_consecutive_threshold(self, ro, tmp_path):
        log = tmp_path / "session.jsonl"
        log.write_text(json.dumps({"type": "result", "subtype": "max_turns"}) + "\n")
        same = self._diagnosis(ro, "same_reason")
        msg, consec, last = ro._check_serial_abort(
            3, 10, same, str(log), ro.CONSECUTIVE_FAIL_THRESHOLD - 1, same,
        )
        assert "consecutive failures" in msg
        assert same.message in msg

    def test_equal_diagnoses_count_together_regardless_of_identity(self, ro, tmp_path):
        """Two runs failing the same way are separate objects with equal value."""
        log = tmp_path / "session.jsonl"
        log.write_text(json.dumps({"type": "result", "subtype": "max_turns"}) + "\n")
        msg, consec, last = ro._check_serial_abort(
            2, 10, self._diagnosis(ro, "boom"), str(log), 1,
            self._diagnosis(ro, "boom"),
        )
        assert msg == ""
        assert consec == 2

    def test_a_differing_turn_count_resets_the_streak(self, ro, tmp_path):
        """The turn count is part of the diagnosis, so it parts the streak.

        Unchanged from the string comparison this replaced, where the count was
        embedded in the rendered reason. It holds together in practice only
        because every group runs on the same turn budget and so exhausts at the
        same count.
        """
        log = tmp_path / "session.jsonl"
        log.write_text(json.dumps({"type": "result", "subtype": "max_turns"}) + "\n")
        msg, consec, last = ro._check_serial_abort(
            3, 10, ro.Diagnosis(ro.DiagnosisKind.MAX_TURNS, num_turns=30), str(log),
            ro.CONSECUTIVE_FAIL_THRESHOLD - 1,
            ro.Diagnosis(ro.DiagnosisKind.MAX_TURNS, num_turns=15),
        )
        assert msg == ""
        assert consec == 1

    def test_different_reason_resets(self, ro, tmp_path):
        log = tmp_path / "session.jsonl"
        log.write_text(json.dumps({"type": "result", "subtype": "max_turns"}) + "\n")
        new = self._diagnosis(ro, "new_reason")
        msg, consec, last = ro._check_serial_abort(
            2, 10, new, str(log), 2, self._diagnosis(ro, "old_reason"),
        )
        assert msg == ""
        assert consec == 1
        assert last == new

    def test_below_threshold(self, ro, tmp_path):
        log = tmp_path / "session.jsonl"
        log.write_text(json.dumps({"type": "result", "subtype": "max_turns"}) + "\n")
        msg, consec, last = ro._check_serial_abort(
            1, 10, self._diagnosis(ro, "some_reason"), str(log), 0, None,
        )
        assert msg == ""
        assert consec == 1


# ── 19. _resolve_model ──────────────────────────────────────────────────────


@pytest.fixture
def no_model_env(monkeypatch):
    """A clean slate — the developer's own shell usually has these set."""
    for key in ("CLAUDE_REVIEW_MODEL", "UNUSED_KEY", "MY_MODEL_KEY",
                "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL",
                "ANTHROPIC_DEFAULT_HAIKU_MODEL"):
        monkeypatch.delenv(key, raising=False)


class TestResolveModel:
    def _clear_alias_envs(self, ro, monkeypatch):
        for alias in ro.ModelAlias:
            monkeypatch.delenv(alias.env_key, raising=False)

    def test_alias_env_keys_follow_convention(self, ro):
        assert ro.ModelAlias.SONNET.env_key == "ANTHROPIC_DEFAULT_SONNET_MODEL"
        assert ro.ModelAlias.OPUS.env_key == "ANTHROPIC_DEFAULT_OPUS_MODEL"
        assert ro.ModelAlias.HAIKU.env_key == "ANTHROPIC_DEFAULT_HAIKU_MODEL"

    def test_parse_rejects_concrete_model_id(self, ro):
        assert ro.ModelAlias.parse("claude-sonnet-5") is None
        assert ro.ModelAlias.parse("sonnet") is ro.ModelAlias.SONNET

    def test_explicit(self, ro, no_model_env):
        assert ro._resolve_model("opus", "SOME_KEY", "sonnet") == "opus"

    def test_env_key(self, ro, no_model_env, monkeypatch):
        monkeypatch.setenv("MY_MODEL_KEY", "haiku")

        assert ro._resolve_model("", "MY_MODEL_KEY", "sonnet") == "haiku"

    def test_global_env(self, ro, no_model_env, monkeypatch):
        monkeypatch.setenv("CLAUDE_REVIEW_MODEL", "opus")
        self._clear_alias_envs(ro, monkeypatch)
        assert ro._resolve_model("", "UNUSED_KEY", "sonnet") == "opus"

    def test_global_env_alias_resolved(self, ro, no_model_env, monkeypatch):
        monkeypatch.setenv("CLAUDE_REVIEW_MODEL", "opus")
        monkeypatch.setenv("ANTHROPIC_DEFAULT_OPUS_MODEL", "claude-opus-4-6")
        assert ro._resolve_model("", "UNUSED_KEY", "sonnet") == "claude-opus-4-6"

    def test_default_fallback(self, ro, no_model_env):
        assert ro._resolve_model("", "UNUSED_KEY", "sonnet") == "sonnet"

    def test_alias_resolved_via_env(self, ro, monkeypatch):
        monkeypatch.delenv("CLAUDE_REVIEW_MODEL", raising=False)
        self._clear_alias_envs(ro, monkeypatch)
        monkeypatch.setenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "claude-sonnet-5")
        assert ro._resolve_model("", "UNUSED_KEY", "sonnet") == "claude-sonnet-5"

    def test_explicit_alias_resolved(self, ro, monkeypatch):
        monkeypatch.delenv("CLAUDE_REVIEW_MODEL", raising=False)
        self._clear_alias_envs(ro, monkeypatch)
        monkeypatch.setenv("ANTHROPIC_DEFAULT_OPUS_MODEL", "claude-opus-4-6")
        assert ro._resolve_model("opus", "SOME_KEY", "sonnet") == "claude-opus-4-6"

    def test_env_key_alias_resolved(self, ro, monkeypatch):
        monkeypatch.setenv("MY_MODEL_KEY", "haiku")
        monkeypatch.delenv("CLAUDE_REVIEW_MODEL", raising=False)
        self._clear_alias_envs(ro, monkeypatch)
        monkeypatch.setenv("ANTHROPIC_DEFAULT_HAIKU_MODEL", "claude-haiku-4-5@20251001")
        assert ro._resolve_model("", "MY_MODEL_KEY", "sonnet") == "claude-haiku-4-5@20251001"

    def test_empty_alias_env_falls_back_to_alias(self, ro, monkeypatch):
        monkeypatch.delenv("CLAUDE_REVIEW_MODEL", raising=False)
        self._clear_alias_envs(ro, monkeypatch)
        monkeypatch.setenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "")
        assert ro._resolve_model("sonnet", "SOME_KEY", "opus") == "sonnet"

    def test_full_model_id_not_resolved(self, ro, monkeypatch):
        monkeypatch.delenv("CLAUDE_REVIEW_MODEL", raising=False)
        self._clear_alias_envs(ro, monkeypatch)
        assert ro._resolve_model("claude-sonnet-5", "SOME_KEY", "sonnet") == "claude-sonnet-5"


# ── 19b. phase_model / collect_phase_models ─────────────────────────────────


class TestPhaseModel:
    def _clean_env(self, ro, monkeypatch):
        monkeypatch.delenv("CLAUDE_REVIEW_MODEL", raising=False)
        for phase in ro.Phase:
            monkeypatch.delenv(phase.model_env_key, raising=False)
        for alias in ro.ModelAlias:
            monkeypatch.delenv(alias.env_key, raising=False)

    def test_phase_env_keys_follow_convention(self, ro):
        assert ro.Phase.SCOUT.model_env_key == "CLAUDE_REVIEW_SCOUT_MODEL"
        assert ro.Phase.SCOUT.thinking_env_key == "CLAUDE_REVIEW_SCOUT_THINKING"

    def test_default(self, ro, monkeypatch):
        self._clean_env(ro, monkeypatch)
        assert ro.phase_model("scout", "") == "sonnet"

    def test_env_key_derived_from_phase_name(self, ro, monkeypatch):
        self._clean_env(ro, monkeypatch)
        monkeypatch.setenv("CLAUDE_REVIEW_SCOUT_MODEL", "claude-haiku-4-5")
        assert ro.phase_model("scout", "") == "claude-haiku-4-5"
        assert ro.phase_model("group", "") == "sonnet"

    def test_explicit_overrides_env(self, ro, monkeypatch):
        self._clean_env(ro, monkeypatch)
        monkeypatch.setenv("CLAUDE_REVIEW_SCOUT_MODEL", "claude-haiku-4-5")
        assert ro.phase_model("scout", "claude-opus-5") == "claude-opus-5"

    def test_collect_groups_phases_by_model(self, ro, monkeypatch):
        self._clean_env(ro, monkeypatch)
        monkeypatch.setenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "claude-sonnet-5")
        monkeypatch.setenv("CLAUDE_REVIEW_SCOUT_MODEL", "claude-haiku-4-5")
        models = ro.collect_phase_models("")
        assert models["claude-haiku-4-5"] == ["scout"]
        assert set(models["claude-sonnet-5"]) == set(ro.PHASES) - {"scout"}

    def test_collect_covers_every_phase(self, ro, monkeypatch):
        self._clean_env(ro, monkeypatch)
        models = ro.collect_phase_models("")
        phases = [p for group in models.values() for p in group]
        assert sorted(phases) == sorted(ro.PHASES)


# ── 19c. enum_arg ───────────────────────────────────────────────────────────


class TestEnumArg:
    def test_converts_to_enum_member(self, ro):
        assert ro.enum_arg(ro.Mode)("self") is ro.Mode.SELF

    def test_error_lists_valid_choices(self, ro):
        import argparse

        with pytest.raises(argparse.ArgumentTypeError) as exc:
            ro.enum_arg(ro.Effort)("bogus")
        assert str(exc.value) == "invalid choice: 'bogus' (choose from 'low', 'medium', 'high')"


# ── 20. _extract_heredoc ────────────────────────────────────────────────────


class TestExtractHeredoc:
    def test_with_eof(self, ro):
        cmd = "cat << EOF\nhello world\nline two\nEOF"
        assert ro._extract_heredoc(cmd) == "hello world\nline two"

    def test_with_review_eof(self, ro):
        cmd = "cat << REVIEW_EOF\ncontent here\nREVIEW_EOF"
        assert ro._extract_heredoc(cmd) == "content here"

    def test_no_heredoc(self, ro):
        assert ro._extract_heredoc("echo hello") == ""

    def test_multiline_content(self, ro):
        cmd = "cat << EOF\nline 1\nline 2\nline 3\nEOF"
        result = ro._extract_heredoc(cmd)
        assert "line 1" in result
        assert "line 2" in result
        assert "line 3" in result


# ── 21. _extract_denied_content ─────────────────────────────────────────────


class TestExtractDeniedContent:
    def test_content_in_tool_input(self, ro):
        denial = {"tool_input": {"content": "## Must fix\nfinding"}}
        assert ro._extract_denied_content(denial) == "## Must fix\nfinding"

    def test_bash_command_with_heredoc(self, ro):
        denial = {"tool_input": {"command": "cat << EOF\n## Must fix\nfinding\nEOF"}}
        result = ro._extract_denied_content(denial)
        assert "## Must fix" in result

    def test_bash_command_without_heredoc(self, ro):
        denial = {"tool_input": {"command": "echo hello"}}
        assert ro._extract_denied_content(denial) == ""


# ── 22. try_recover_output ─────────────────────────────────────────────────


class TestTryRecoverOutput:
    def test_recover_from_denied_write(self, ro, tmp_path):
        log = tmp_path / "session.jsonl"
        output = tmp_path / "output.md"
        log.write_text(json.dumps({
            "type": "result",
            "permission_denials": [{
                "tool_input": {"content": "## Must fix\n- **[M1]** finding\n"}
            }],
        }) + "\n")
        assert ro.try_recover_output(str(log), str(output)) is True
        assert output.exists()
        assert "## Must fix" in output.read_text()

    def test_recover_from_bash_heredoc(self, ro, tmp_path):
        log = tmp_path / "session.jsonl"
        output = tmp_path / "output.md"
        log.write_text(json.dumps({
            "type": "result",
            "permission_denials": [{
                "tool_input": {"command": "cat << EOF\n## Should fix\ncontent\nEOF"}
            }],
        }) + "\n")
        assert ro.try_recover_output(str(log), str(output)) is True
        assert "## Should fix" in output.read_text()

    def test_no_denials(self, ro, tmp_path):
        log = tmp_path / "session.jsonl"
        output = tmp_path / "output.md"
        log.write_text(json.dumps({"type": "result", "permission_denials": []}) + "\n")
        assert ro.try_recover_output(str(log), str(output)) is False

    def test_denial_no_section_headers(self, ro, tmp_path):
        log = tmp_path / "session.jsonl"
        output = tmp_path / "output.md"
        log.write_text(json.dumps({
            "type": "result",
            "permission_denials": [{
                "tool_input": {"content": "just text no sections"}
            }],
        }) + "\n")
        assert ro.try_recover_output(str(log), str(output)) is False

    def test_missing_log_file(self, ro, tmp_path):
        assert ro.try_recover_output(
            str(tmp_path / "missing.jsonl"),
            str(tmp_path / "output.md"),
        ) is False


# ── 23. _diagnose_result_type ───────────────────────────────────────────────


class TestDiagnoseResultType:
    def test_max_turns(self, ro):
        result = {"subtype": "max_turns", "num_turns": 15}
        diag = ro._diagnose_result_type(result)
        assert diag == ro.Diagnosis(ro.DiagnosisKind.MAX_TURNS, num_turns=15)

    def test_error(self, ro):
        result = {"is_error": True, "errors": ["timeout"]}
        diag = ro._diagnose_result_type(result)
        assert diag == ro.Diagnosis(ro.DiagnosisKind.AGENT_ERROR, detail="timeout")

    def test_completed_no_output(self, ro):
        result = {"subtype": "completed"}
        diag = ro._diagnose_result_type(result)
        assert diag == ro.Diagnosis(ro.DiagnosisKind.COMPLETED, detail="completed")


# ── 24. diagnose_missing_output ────────────────────────────────────────────


class TestDiagnoseMissingOutput:
    def test_max_turns_result(self, ro, tmp_path):
        log = tmp_path / "session.jsonl"
        log.write_text(json.dumps({
            "type": "result", "subtype": "max_turns", "num_turns": 10,
        }) + "\n")
        result = ro.diagnose_missing_output(str(log))
        assert result.kind is ro.DiagnosisKind.MAX_TURNS

    def test_no_log_file(self, ro, tmp_path):
        result = ro.diagnose_missing_output(str(tmp_path / "missing.jsonl"))
        assert result.kind is ro.DiagnosisKind.NO_SESSION_LOG

    def test_empty_log(self, ro, tmp_path):
        log = tmp_path / "session.jsonl"
        log.write_text("")
        result = ro.diagnose_missing_output(str(log))
        assert result.kind is ro.DiagnosisKind.NO_RESULT_RECORD

    def test_no_result_records(self, ro, tmp_path):
        log = tmp_path / "session.jsonl"
        log.write_text(json.dumps({"type": "assistant", "message": "hi"}) + "\n")
        result = ro.diagnose_missing_output(str(log))
        assert result.kind is ro.DiagnosisKind.NO_RESULT_RECORD

    def test_quota_exhausted_no_result(self, ro, tmp_path):
        log = tmp_path / "session.jsonl"
        log.write_text(
            json.dumps({"type": "system", "subtype": "init"}) + "\n"
            + json.dumps({"type": "system", "subtype": "api_retry", "error_status": 429}) + "\n"
        )
        result = ro.diagnose_missing_output(str(log))
        assert result.kind is ro.DiagnosisKind.QUOTA_EXHAUSTED


# ── 25. _is_model_error ─────────────────────────────────────────────────────


class TestIsModelError:
    def test_404_error(self, ro, tmp_path):
        log = tmp_path / "session.jsonl"
        log.write_text(json.dumps({
            "type": "result", "api_error_status": 404,
        }) + "\n")
        assert ro._is_model_error(str(log)) is True

    def test_not_available(self, ro, tmp_path):
        log = tmp_path / "session.jsonl"
        log.write_text(json.dumps({
            "type": "result", "result": "The model is Not Available right now",
        }) + "\n")
        assert ro._is_model_error(str(log)) is True

    def test_normal_completion(self, ro, tmp_path):
        log = tmp_path / "session.jsonl"
        log.write_text(json.dumps({
            "type": "result", "subtype": "completed", "result": "done",
        }) + "\n")
        assert ro._is_model_error(str(log)) is False

    def test_missing_log(self, ro, tmp_path):
        assert ro._is_model_error(str(tmp_path / "missing.jsonl")) is False


# ── 26. classify_tier ────────────────────────────────────────────────────────


class TestClassifyTier:
    def test_tier1_migrations(self, ro):
        assert ro.classify_tier("db/migrations/001_init.sql") == 1

    def test_tier1_proto(self, ro):
        assert ro.classify_tier("api/service.proto") == 1

    def test_tier1_claude_md(self, ro):
        assert ro.classify_tier("CLAUDE.md") == 1

    def test_tier1_auth_path(self, ro):
        assert ro.classify_tier("pkg/auth/handler.go") == 1

    def test_tier2_normal(self, ro):
        assert ro.classify_tier("pkg/service/handler.go") == 2

    def test_tier3_pb_go(self, ro):
        assert ro.classify_tier("api/service.pb.go") == 3

    def test_tier3_go_sum(self, ro):
        assert ro.classify_tier("go.sum") == 3

    def test_tier3_gen_path(self, ro):
        assert ro.classify_tier("gen/api/types.go") == 3


# ── 27. _split_large_dir ────────────────────────────────────────────────────


class TestSplitLargeDir:
    def test_fits_in_one_group(self, ro):
        files = ["a.go", "b.go"]
        file_lines = {"a.go": 100, "b.go": 100}
        groups = ro._split_large_dir("pkg", files, file_lines)
        assert len(groups) == 1
        assert groups[0].name == "pkg-1"
        assert groups[0].files == files

    def test_requires_split(self, ro):
        files = ["a.go", "b.go", "c.go"]
        file_lines = {"a.go": 500, "b.go": 500, "c.go": 500}
        groups = ro._split_large_dir("pkg", files, file_lines)
        assert len(groups) > 1
        all_files = [f for g in groups for f in g.files]
        assert set(all_files) == set(files)

    def test_splits_on_file_count(self, ro):
        files = [f"f{i}.py" for i in range(20)]
        file_lines = {f: 10 for f in files}
        groups = ro._split_large_dir("pkg", files, file_lines)
        assert len(groups) > 1
        assert all(len(g.files) <= ro.MAX_GROUP_FILES for g in groups)
        all_files = [f for g in groups for f in g.files]
        assert set(all_files) == set(files)


# ── 28. group_files ─────────────────────────────────────────────────────────


class TestGroupFiles:
    def test_mix_of_tiers(self, ro):
        pr = ro.PRMetadata(
            title="test", body="", head="feat", base="main", head_sha="abc",
            additions=100, deletions=50, changed_files=3,
            files=[
                {"path": "CLAUDE.md", "additions": 10, "deletions": 5},
                {"path": "pkg/handler.go", "additions": 50, "deletions": 20},
                {"path": "go.sum", "additions": 40, "deletions": 25},
            ],
        )
        groups = ro.group_files(pr)
        names = [g.name for g in groups]
        assert ro.GROUP_TIER1 in names
        assert ro.GROUP_TIER3 in names

    def test_large_dir_split(self, ro):
        files = [
            {"path": f"pkg/file{i}.go", "additions": 500, "deletions": 0}
            for i in range(5)
        ]
        pr = ro.PRMetadata(
            title="test", body="", head="feat", base="main", head_sha="abc",
            additions=2500, deletions=0, changed_files=5, files=files,
        )
        groups = ro.group_files(pr)
        # pkg dir has 2500 lines, should be split
        pkg_groups = [g for g in groups if g.name.startswith("pkg")]
        assert len(pkg_groups) > 1

    def test_many_files_split(self, ro):
        files = [
            {"path": f"pkg/file{i}.go", "additions": 10, "deletions": 5}
            for i in range(20)
        ]
        pr = ro.PRMetadata(
            title="test", body="", head="feat", base="main", head_sha="abc",
            additions=200, deletions=100, changed_files=20, files=files,
        )
        groups = ro.group_files(pr)
        pkg_groups = [g for g in groups if g.name.startswith("pkg")]
        assert len(pkg_groups) > 1
        assert all(len(g.files) <= ro.MAX_GROUP_FILES for g in pkg_groups)
        all_files = [f for g in pkg_groups for f in g.files]
        assert set(all_files) == {f"pkg/file{i}.go" for i in range(20)}

    def test_exactly_max_files_no_split(self, ro):
        # group_files uses > MAX_GROUP_FILES, so exactly MAX_GROUP_FILES files
        # must not trigger a split — verifies the threshold is exclusive
        files = [
            {"path": f"pkg/file{i}.go", "additions": 10, "deletions": 0}
            for i in range(ro.MAX_GROUP_FILES)
        ]
        pr = ro.PRMetadata(
            title="test", body="", head="feat", base="main", head_sha="abc",
            additions=ro.MAX_GROUP_FILES * 10, deletions=0,
            changed_files=ro.MAX_GROUP_FILES, files=files,
        )
        groups = ro.group_files(pr)
        pkg_groups = [g for g in groups if g.name.startswith("pkg")]
        assert len(pkg_groups) == 1


# ── 29. _merge_smallest_groups ──────────────────────────────────────────────


class TestMergeSmallestGroups:
    def test_under_limit(self, ro):
        groups = [
            ro.Group("a", ["f1"], 10),
            ro.Group("b", ["f2"], 20),
        ]
        result = ro._merge_smallest_groups(groups, 5)
        assert len(result) == 2

    def test_over_limit(self, ro):
        groups = [
            ro.Group("a", ["f1"], 10),
            ro.Group("b", ["f2"], 20),
            ro.Group("c", ["f3"], 30),
        ]
        result = ro._merge_smallest_groups(groups, 2)
        assert len(result) == 2

    def test_merged_name_and_files(self, ro):
        groups = [
            ro.Group("a", ["f1"], 10),
            ro.Group("b", ["f2"], 20),
        ]
        result = ro._merge_smallest_groups(groups, 1)
        assert len(result) == 1
        assert "a" in result[0].name
        assert "b" in result[0].name
        assert set(result[0].files) == {"f1", "f2"}

    def test_prefers_shared_directory_prefix(self, ro):
        groups = [
            ro.Group("src/api", ["api.go"], 100),
            ro.Group("src/auth", ["auth.go"], 100),
            ro.Group("tests/unit", ["test.go"], 50),
        ]
        result = ro._merge_smallest_groups(groups, 2)
        assert len(result) == 2
        merged = [g for g in result if "+" in g.name][0]
        assert "src/api" in merged.name
        assert "src/auth" in merged.name

    def test_falls_back_to_size_without_shared_prefix(self, ro):
        groups = [
            ro.Group("alpha", ["a.go"], 500),
            ro.Group("beta", ["b.go"], 10),
            ro.Group("gamma", ["c.go"], 20),
        ]
        result = ro._merge_smallest_groups(groups, 2)
        assert len(result) == 2
        merged = [g for g in result if "+" in g.name][0]
        assert "beta" in merged.name
        assert "gamma" in merged.name


# ── 30. _validate_group_output ──────────────────────────────────────────────


class TestValidateGroupOutput:
    def test_valid_sections(self, ro, tmp_path):
        f = tmp_path / "group.md"
        f.write_text("## Must fix\n- **[M1]** finding\n## Nit\n- **[N1]** nit\n")
        assert ro._validate_group_output(str(f), "test") is True

    def test_no_recognized_sections(self, ro, tmp_path):
        f = tmp_path / "group.md"
        f.write_text("## Random Header\nsome content\n")
        assert ro._validate_group_output(str(f), "test") is False

    def test_empty_file(self, ro, tmp_path):
        f = tmp_path / "group.md"
        f.write_text("")
        assert ro._validate_group_output(str(f), "test") is True


# ── 31. _build_meta_header ──────────────────────────────────────────────────


class TestBuildMetaHeader:
    def test_contains_date_and_sha(self, ro, tmp_path):
        from datetime import date
        job = ro.ReviewJob(
            repo="org/repo", pr_number="42",
            pr=ro.PRMetadata(title="test", body="", head="feat", base="main",
                             head_sha="abc123", additions=10, deletions=5,
                             changed_files=2, files=[]),
            ctx=ro.PRContext(),
            wt_path="/tmp/wt", review_file=str(tmp_path / "review.md"),
            session_log=str(tmp_path / "session.jsonl"),
            generator_version="1.0.0",
        )
        result = ro._build_meta_header(job)
        assert date.today().isoformat() in result
        assert "abc123" in result
        assert "1.0.0" in result


# ── 32. _build_mechanical_fallback ──────────────────────────────────────────


class TestBuildMechanicalFallback:
    def test_pr_mode(self, ro, tmp_path):
        job = ro.ReviewJob(
            repo="org/repo", pr_number="42",
            pr=ro.PRMetadata(title="test PR", body="", head="feat", base="main",
                             head_sha="abc", additions=10, deletions=5,
                             changed_files=2, files=[]),
            ctx=ro.PRContext(),
            wt_path="/tmp/wt", review_file=str(tmp_path / "review.md"),
            session_log=str(tmp_path / "session.jsonl"),
            mode=ro.Mode.PR,
        )
        merged = "## Must fix\n- **[M1]** **`file.go:1`** — issue\n"
        result = ro._build_mechanical_fallback(job, 3, merged)
        assert "# Review:" in result
        assert "Verdict" in result
        assert "1 finding" in result

    def test_self_review_mode(self, ro, tmp_path):
        job = ro.ReviewJob(
            repo="org/repo", pr_number="",
            pr=ro.PRMetadata(title="test", body="", head="my-branch", base="main",
                             head_sha="abc", additions=10, deletions=5,
                             changed_files=2, files=[]),
            ctx=ro.PRContext(),
            wt_path="/tmp/wt", review_file=str(tmp_path / "review.md"),
            session_log=str(tmp_path / "session.jsonl"),
            mode=ro.Mode.SELF,
        )
        merged = "## Nit\n- **[N1]** **`file.go:1`** — style\n"
        result = ro._build_mechanical_fallback(job, 2, merged)
        assert "# Self-Review:" in result
        assert "Verdict" not in result

    def test_counts_correct(self, ro, tmp_path):
        job = ro.ReviewJob(
            repo="org/repo", pr_number="42",
            pr=ro.PRMetadata(title="test", body="", head="feat", base="main",
                             head_sha="abc", additions=10, deletions=5,
                             changed_files=2, files=[]),
            ctx=ro.PRContext(),
            wt_path="/tmp/wt", review_file=str(tmp_path / "review.md"),
            session_log=str(tmp_path / "session.jsonl"),
            mode=ro.Mode.PR,
        )
        merged = (
            "## Must fix\n- **[M1]** **`a.go:1`** — issue\n"
            "## Nit\n- **[N1]** **`b.go:2`** — style\n- **[N2]** **`c.go:3`** — naming\n"
        )
        result = ro._build_mechanical_fallback(job, 3, merged)
        assert "3 findings" in result


# ── 33. _write_clean_review ─────────────────────────────────────────────────


class TestWriteCleanReview:
    def test_pr_mode(self, ro, tmp_path):
        review_file = tmp_path / "review.md"
        job = ro.ReviewJob(
            repo="org/repo", pr_number="42",
            pr=ro.PRMetadata(title="clean PR", body="", head="feat", base="main",
                             head_sha="abc", additions=10, deletions=5,
                             changed_files=3, files=[]),
            ctx=ro.PRContext(),
            wt_path="/tmp/wt", review_file=str(review_file),
            session_log=str(tmp_path / "session.jsonl"),
            mode=ro.Mode.PR,
        )
        ro._write_clean_review(job, 2)
        content = review_file.read_text()
        assert "# Review:" in content
        assert "No issues found" in content
        assert "Approve" in content

    def test_self_review_mode(self, ro, tmp_path):
        review_file = tmp_path / "review.md"
        job = ro.ReviewJob(
            repo="org/repo", pr_number="",
            pr=ro.PRMetadata(title="self", body="", head="my-branch", base="main",
                             head_sha="abc", additions=10, deletions=5,
                             changed_files=3, files=[]),
            ctx=ro.PRContext(),
            wt_path="/tmp/wt", review_file=str(review_file),
            session_log=str(tmp_path / "session.jsonl"),
            mode=ro.Mode.SELF,
        )
        ro._write_clean_review(job, 2)
        content = review_file.read_text()
        assert "# Self-Review:" in content
        assert "No issues found" in content
        assert "Verdict" not in content


# ── 34. _parse_session_cost ─────────────────────────────────────────────────


class TestParseSessionCost:
    def test_valid_log(self, ro, tmp_path):
        log = tmp_path / "session.jsonl"
        log.write_text(
            json.dumps({"type": "result", "total_cost_usd": 1.5}) + "\n"
            + json.dumps({"type": "result", "total_cost_usd": 0.5}) + "\n"
        )
        assert ro._parse_session_cost(str(log)) == 2.0

    def test_missing_file(self, ro, tmp_path):
        assert ro._parse_session_cost(str(tmp_path / "missing.jsonl")) == 0.0

    def test_no_result_records(self, ro, tmp_path):
        log = tmp_path / "session.jsonl"
        log.write_text(json.dumps({"type": "assistant", "message": "hi"}) + "\n")
        assert ro._parse_session_cost(str(log)) == 0.0


# ── 35b. preserve_log / restore_preserved ──────────────────────────────────


class TestPreserveLog:
    def test_preserves_prior_content(self, ro, tmp_path):
        log = tmp_path / "session.jsonl"
        first = json.dumps({
            "type": "result", "total_cost_usd": 1.0,
            "usage": {"input_tokens": 100, "output_tokens": 200,
                      "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
            "duration_ms": 30000,
        }) + "\n"
        log.write_text(first)

        prior = ro.preserve_log(str(log))
        assert prior == first

        second = json.dumps({
            "type": "result", "total_cost_usd": 2.0,
            "usage": {"input_tokens": 300, "output_tokens": 400,
                      "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
            "duration_ms": 60000,
        }) + "\n"
        log.write_text(second)

        ro.restore_preserved(str(log), prior)

        usage = ro.parse_session_log(str(log))
        assert usage.cost == pytest.approx(3.0)
        assert usage.input_tokens == 400
        assert usage.output_tokens == 600
        assert usage.duration_ms == 90000

    def test_preserve_nonexistent_file(self, ro, tmp_path):
        assert ro.preserve_log(str(tmp_path / "missing.jsonl")) == ""

    def test_restore_empty_prior_is_noop(self, ro, tmp_path):
        log = tmp_path / "session.jsonl"
        content = '{"type":"result","total_cost_usd":1.0}\n'
        log.write_text(content)
        ro.restore_preserved(str(log), "")
        assert log.read_text() == content


class TestIsCompleteReview:
    def test_has_summary(self, ro, tmp_path):
        f = tmp_path / "review.md"
        f.write_text("# Review\n\n## Summary\nLooks good.\n")
        assert ro._is_complete_review(str(f)) is True

    def test_has_verdict(self, ro, tmp_path):
        f = tmp_path / "review.md"
        f.write_text("# Review\n\n## Verdict\nApprove.\n")
        assert ro._is_complete_review(str(f)) is True

    def test_missing_headers(self, ro, tmp_path):
        f = tmp_path / "review.md"
        f.write_text("# Review\n\nSome content without required headers.\n")
        assert ro._is_complete_review(str(f)) is False

    def test_file_not_exists(self, ro, tmp_path):
        assert ro._is_complete_review(str(tmp_path / "nonexistent.md")) is False

    def test_empty_file(self, ro, tmp_path):
        f = tmp_path / "review.md"
        f.write_text("")
        assert ro._is_complete_review(str(f)) is False


class TestPhaseSynthesis:
    def _make_job(self, ro, tmp_path, mode="pr"):
        return ro.ReviewJob(
            repo="org/repo", pr_number="42",
            pr=ro.PRMetadata(title="test PR", body="", head="feat", base="main",
                             head_sha="abc123", additions=100, deletions=50,
                             changed_files=10, files=[]),
            ctx=ro.PRContext(),
            wt_path=str(tmp_path / "wt"),
            review_file=str(tmp_path / "review.md"),
            session_log=str(tmp_path / "session.jsonl"),
            mode=ro.Mode(mode),
        )

    @staticmethod
    def _patch_pipeline(monkeypatch, ro, **overrides):
        """Patch the module-level imports `_phase_synthesis` reaches through.

        The synthesis phase spans two modules: it builds its own prompt and
        post-processes its own findings, but invokes the agent through
        `PhaseRunner`, whose bindings live in review_phases. Each name is
        patched on whichever module binds it.
        """
        import review_phases
        import review_pipeline
        defaults = {
            "build_prompt": lambda *a, **kw: "mock prompt",
            "post_process_findings": lambda *a, **kw: None,
        }
        defaults.update(overrides)
        for name, func in defaults.items():
            owner = review_pipeline if hasattr(review_pipeline, name) else review_phases
            monkeypatch.setattr(owner, name, func)

    def test_successful_synthesis(self, ro, tmp_path, monkeypatch):
        job = self._make_job(ro, tmp_path)
        review_content = "# Review: org/repo#42 — test PR\n\n## Summary\nLooks good.\n\n## Verdict\nApprove.\n"

        def mock_invoke(inv, **kwargs):
            from pathlib import Path
            Path(job.review_file).write_text(review_content)
            Path(inv.session_log).write_text("")
            return 0

        self._patch_pipeline(monkeypatch, ro, invoke_agent=mock_invoke)

        ro._phase_synthesis(job, "", 3, "merged content")

        from pathlib import Path
        result = Path(job.review_file).read_text()
        assert "## Summary" in result
        assert ro.FALLBACK_SUMMARY not in result

    def test_cost_comes_from_the_session_log(self, ro, tmp_path, monkeypatch):
        job = self._make_job(ro, tmp_path)
        review_content = "# Review\n\n## Summary\nLooks good.\n\n## Verdict\nApprove.\n"

        def mock_invoke(inv, **kwargs):
            Path(job.review_file).write_text(review_content)
            Path(inv.session_log).write_text(
                '{"type":"result","subtype":"success","total_cost_usd":2.5}\n')
            return 0

        self._patch_pipeline(monkeypatch, ro, invoke_agent=mock_invoke)

        assert ro._phase_synthesis(job, "", 3, "merged content").cost == 2.5

    def test_a_fallen_back_synthesis_is_still_charged(self, ro, tmp_path, monkeypatch):
        """The agent ran and spent; the review file just came from the merge."""
        job = self._make_job(ro, tmp_path)

        def mock_invoke(inv, **kwargs):
            Path(inv.session_log).write_text(
                '{"type":"result","subtype":"success","total_cost_usd":2.5}\n')
            return 1

        self._patch_pipeline(
            monkeypatch, ro,
            invoke_agent=mock_invoke,
            try_recover_output=lambda *a: False,
        )

        merged = "## Must fix\n- **[M1]** **`file.go:1`** — issue\n"
        result = ro._phase_synthesis(job, "", 3, merged)

        assert ro.FALLBACK_SUMMARY in Path(job.review_file).read_text()
        assert result.cost == 2.5

    def test_agent_fails_no_output(self, ro, tmp_path, monkeypatch):
        job = self._make_job(ro, tmp_path)

        def mock_invoke(inv, **kwargs):
            from pathlib import Path
            Path(inv.session_log).write_text("")
            return 1

        self._patch_pipeline(
            monkeypatch, ro,
            invoke_agent=mock_invoke,
            try_recover_output=lambda *a: False,
        )

        merged = "## Must fix\n- **[M1]** **`file.go:1`** — issue\n"
        ro._phase_synthesis(job, "", 3, merged)

        from pathlib import Path
        result = Path(job.review_file).read_text()
        assert ro.FALLBACK_SUMMARY in result

    def test_incomplete_output_falls_back(self, ro, tmp_path, monkeypatch):
        job = self._make_job(ro, tmp_path)

        def mock_invoke(inv, **kwargs):
            from pathlib import Path
            # Write incomplete review (no Summary or Verdict headers)
            Path(job.review_file).write_text("# Review\nSome partial content\n")
            Path(inv.session_log).write_text("")
            return 0

        self._patch_pipeline(monkeypatch, ro, invoke_agent=mock_invoke)

        merged = "## Should fix\n- **[S1]** **`api.go:10`** — cleanup\n"
        ro._phase_synthesis(job, "", 3, merged)

        from pathlib import Path
        result = Path(job.review_file).read_text()
        assert ro.FALLBACK_SUMMARY in result

    def test_transient_error_retries_then_succeeds(self, ro, tmp_path, monkeypatch):
        job = self._make_job(ro, tmp_path)
        review_content = "# Review: org/repo#42 — test PR\n\n## Summary\nLooks good.\n\n## Verdict\nApprove.\n"
        calls = []

        def mock_invoke(inv, **kwargs):
            from pathlib import Path
            calls.append(len(calls))
            if len(calls) == 1:
                Path(inv.session_log).write_text(
                    '{"type":"result","subtype":"success","is_error":true,'
                    '"result":"API Error: Connection to the API was lost (FailedToOpenSocket)."}\n'
                )
                return 1
            Path(job.review_file).write_text(review_content)
            Path(inv.session_log).write_text("")
            return 0

        self._patch_pipeline(monkeypatch, ro, invoke_agent=mock_invoke)

        ro._phase_synthesis(job, "", 3, "merged content")

        from pathlib import Path
        result = Path(job.review_file).read_text()
        assert "## Summary" in result
        assert ro.FALLBACK_SUMMARY not in result
        assert len(calls) == 2

    def test_transient_error_retries_then_falls_back(self, ro, tmp_path, monkeypatch):
        job = self._make_job(ro, tmp_path)
        calls = []

        def mock_invoke(inv, **kwargs):
            from pathlib import Path
            calls.append(len(calls))
            Path(inv.session_log).write_text(
                '{"type":"result","subtype":"success","is_error":true,'
                '"result":"API Error: Connection to the API was lost (FailedToOpenSocket)."}\n'
            )
            return 1

        self._patch_pipeline(
            monkeypatch, ro,
            invoke_agent=mock_invoke,
            try_recover_output=lambda *a: False,
        )

        merged = "## Must fix\n- **[M1]** **`file.go:1`** — issue\n"
        ro._phase_synthesis(job, "", 3, merged)

        from pathlib import Path
        result = Path(job.review_file).read_text()
        assert ro.FALLBACK_SUMMARY in result
        assert len(calls) == 2

    def test_non_transient_error_does_not_retry(self, ro, tmp_path, monkeypatch):
        job = self._make_job(ro, tmp_path)
        calls = []

        def mock_invoke(inv, **kwargs):
            from pathlib import Path
            calls.append(len(calls))
            Path(inv.session_log).write_text(
                '{"type":"result","subtype":"success","is_error":true,'
                '"result":"agent error: something broke"}\n'
            )
            return 1

        self._patch_pipeline(
            monkeypatch, ro,
            invoke_agent=mock_invoke,
            try_recover_output=lambda *a: False,
        )

        merged = "## Must fix\n- **[M1]** **`file.go:1`** — issue\n"
        ro._phase_synthesis(job, "", 3, merged)

        from pathlib import Path
        result = Path(job.review_file).read_text()
        assert ro.FALLBACK_SUMMARY in result
        assert len(calls) == 1


class TestRunSynthesisOrFallback:
    """What the synthesis step records in state, and what it reports spending."""

    def _make_state(self, ro):
        return ro.PipelineState(
            head_sha="abc123",
            group_names=["grp-1"],
            holistic_done=True,
            groups_done=[1],
        )

    def _make_job(self, ro, tmp_path, mode="pr"):
        return ro.ReviewJob(
            repo="org/repo", pr_number="42",
            pr=ro.PRMetadata(title="test PR", body="", head="feat", base="main",
                             head_sha="abc123", additions=100, deletions=50,
                             changed_files=10, files=[]),
            ctx=ro.PRContext(),
            wt_path=str(tmp_path / "wt"),
            review_file=str(tmp_path / "review.md"),
            session_log=str(tmp_path / "session.jsonl"),
            mode=ro.Mode(mode),
        )

    def test_self_review_fallback_detected(self, ro, tmp_path, monkeypatch):
        """Self-reviews without verdict must still detect mechanical fallback."""
        import review_pipeline

        job = self._make_job(ro, tmp_path, mode="self")
        state = self._make_state(ro)

        def mock_synthesis(job, holistic, count, merged, skipped_groups=0):
            from pathlib import Path
            fallback = ro._build_mechanical_fallback(
                job, count, merged, skipped_groups=skipped_groups,
            )
            Path(job.review_file).write_text(fallback)
            return review_pipeline.PhaseResult(str(tmp_path / "synthesis.jsonl"))

        monkeypatch.setattr(review_pipeline, "_phase_synthesis", mock_synthesis)
        monkeypatch.setattr(review_pipeline, "_write_pipeline_state", lambda *a: None)

        merged = "## Should fix\n- **[S1]** **`api.go:10`** — cleanup\n"
        ro._run_synthesis_or_fallback(
            job, state, "", 1, merged, [], 0, 0.0, 20.0,
        )
        assert state.synthesis_failed == ro.Diagnosis(ro.DiagnosisKind.MECHANICAL_FALLBACK)

    def test_synthesis_reports_what_its_log_records(self, ro, tmp_path, monkeypatch):
        import review_pipeline

        job = self._make_job(ro, tmp_path)
        state = self._make_state(ro)

        def mock_synthesis(job, holistic, count, merged, skipped_groups=0):
            from pathlib import Path
            Path(job.review_file).write_text(
                "# Review\n\n## Summary\nok\n\n## Verdict\nApprove\n")
            return review_pipeline.PhaseResult(str(tmp_path / "synthesis.jsonl"), 1.25)

        monkeypatch.setattr(review_pipeline, "_phase_synthesis", mock_synthesis)
        monkeypatch.setattr(review_pipeline, "_write_pipeline_state", lambda *a: None)

        merged = "## Should fix\n- **[S1]** **`api.go:10`** — cleanup\n"
        result = ro._run_synthesis_or_fallback(
            job, state, "", 1, merged, [], 0, 0.0, 20.0,
        )
        assert result.cost == 1.25

    def test_a_clean_review_spends_nothing(self, ro, tmp_path, monkeypatch):
        import review_pipeline

        job = self._make_job(ro, tmp_path)
        state = self._make_state(ro)
        monkeypatch.setattr(review_pipeline, "_write_pipeline_state", lambda *a: None)

        result = ro._run_synthesis_or_fallback(
            job, state, "", 1, "No findings.\n", [], 0, 0.0, 20.0,
        )
        assert result == review_pipeline.PhaseResult()

    def test_a_synthesis_skipped_on_budget_spends_nothing(self, ro, tmp_path, monkeypatch):
        import review_pipeline

        job = self._make_job(ro, tmp_path)
        state = self._make_state(ro)
        monkeypatch.setattr(review_pipeline, "_write_pipeline_state", lambda *a: None)

        merged = "## Should fix\n- **[S1]** **`api.go:10`** — cleanup\n"
        result = ro._run_synthesis_or_fallback(
            job, state, "", 1, merged, [], 0, 25.0, 20.0,
        )
        assert result == review_pipeline.PhaseResult()


class TestIsRetryable:
    def test_max_turns_is_retryable(self, ro):
        assert ro._is_retryable(
            ro.Diagnosis(ro.DiagnosisKind.MAX_TURNS, num_turns=16)) is True

    def test_no_session_log_is_retryable(self, ro):
        assert ro._is_retryable(ro.Diagnosis(ro.DiagnosisKind.NO_SESSION_LOG)) is True

    def test_no_result_record_is_retryable(self, ro):
        assert ro._is_retryable(ro.Diagnosis(ro.DiagnosisKind.NO_RESULT_RECORD)) is True

    def test_model_error_not_retryable(self, ro):
        assert ro._is_retryable(ro.Diagnosis(
            ro.DiagnosisKind.AGENT_ERROR, detail="model not available")) is False

    def test_agent_error_not_retryable(self, ro):
        assert ro._is_retryable(ro.Diagnosis(
            ro.DiagnosisKind.AGENT_ERROR, detail="something broke")) is False

    def test_skipped_not_retryable(self, ro):
        assert ro._is_retryable(ro.Diagnosis(
            ro.DiagnosisKind.SKIPPED,
            detail="3 consecutive failures (agent hit max turns) — aborting")) is False

    def test_transient_is_retryable(self, ro):
        assert ro._is_retryable(ro.Diagnosis(
            ro.DiagnosisKind.TRANSIENT, detail="ETIMEDOUT")) is True


class TestTransientClassification:
    """Which backend errors the classifier files as TRANSIENT rather than fatal.

    Driven through `diagnose_missing_output` because the classifier is only
    reached once a crash is established — a marker in the output of a run that
    ended on its own terms is not an error report.
    """

    def _kind(self, ro, tmp_path, detail: str):
        log = tmp_path / "session.jsonl"
        log.write_text(json.dumps({
            "type": "result", "subtype": "error", "is_error": True,
            "result": detail,
        }) + "\n")
        return ro.diagnose_missing_output(str(log)).kind

    def test_socket_error(self, ro, tmp_path):
        assert self._kind(
            ro, tmp_path,
            "API Error: Connection to the API was lost (FailedToOpenSocket).",
        ) is ro.DiagnosisKind.TRANSIENT

    def test_connection_refused(self, ro, tmp_path):
        assert self._kind(ro, tmp_path, "ConnectionRefused") is ro.DiagnosisKind.TRANSIENT

    def test_connection_reset(self, ro, tmp_path):
        assert self._kind(
            ro, tmp_path, "Connection to the API was lost (ConnectionReset)",
        ) is ro.DiagnosisKind.TRANSIENT

    def test_etimedout(self, ro, tmp_path):
        assert self._kind(ro, tmp_path, "ETIMEDOUT") is ro.DiagnosisKind.TRANSIENT

    def test_model_error_not_transient(self, ro, tmp_path):
        assert self._kind(
            ro, tmp_path, "model not available") is ro.DiagnosisKind.AGENT_ERROR

    def test_generic_agent_error_not_transient(self, ro, tmp_path):
        assert self._kind(
            ro, tmp_path, "something broke") is ro.DiagnosisKind.AGENT_ERROR

    def test_a_marker_in_a_clean_run_is_not_an_error_report(self, ro, tmp_path):
        """A successful run whose output happens to mention a socket fault."""
        log = tmp_path / "session.jsonl"
        log.write_text(json.dumps({
            "type": "result", "subtype": "success", "result": "ECONNRESET",
        }) + "\n")
        assert ro.diagnose_missing_output(str(log)).kind is ro.DiagnosisKind.COMPLETED


class TestRetryTurns:
    def _job_no_omitted(self, ro, tmp_path):
        return ro.ReviewJob(
            repo="org/repo", pr_number="1",
            pr=ro.PRMetadata(title="t", body="", head="f", base="main",
                             head_sha="abc", additions=1, deletions=0,
                             changed_files=1, files=[]),
            ctx=ro.PRContext(),
            wt_path=str(tmp_path), review_file=str(tmp_path / "r.md"),
            session_log=str(tmp_path / "s.jsonl"),
            mode=ro.Mode.PR,
        )

    def test_max_turns_gets_doubled(self, ro, tmp_path):
        job = self._job_no_omitted(ro, tmp_path)
        diagnosis = ro.Diagnosis(ro.DiagnosisKind.MAX_TURNS, num_turns=16)
        assert ro._retry_turns(diagnosis, job) == ro.RETRY_MAX_TURNS_GROUP

    def test_other_reason_gets_default(self, ro, tmp_path):
        job = self._job_no_omitted(ro, tmp_path)
        diagnosis = ro.Diagnosis(ro.DiagnosisKind.NO_SESSION_LOG)
        assert ro._retry_turns(diagnosis, job) == ro.PHASES[ro.Phase.GROUP].max_turns


def _max_turns_16(ro):
    """Turn exhaustion, the retryable failure these tests drive retries with."""
    return ro.Diagnosis(ro.DiagnosisKind.MAX_TURNS, num_turns=16)


class TestRetryFailedGroups:
    def _make_job(self, ro, tmp_path):
        return ro.ReviewJob(
            repo="org/repo", pr_number="42",
            pr=ro.PRMetadata(title="test PR", body="", head="feat", base="main",
                             head_sha="abc123", additions=100, deletions=50,
                             changed_files=5, files=[
                                 {"path": "a.go", "additions": 10, "deletions": 5, "status": "modified"},
                                 {"path": "b.go", "additions": 20, "deletions": 10, "status": "modified"},
                             ]),
            ctx=ro.PRContext(),
            wt_path=str(tmp_path / "wt"),
            review_file=str(tmp_path / "review.md"),
            session_log=str(tmp_path / "session.jsonl"),
            mode=ro.Mode.PR,
        )

    def test_retries_max_turns_failure(self, ro, tmp_path, monkeypatch):
        import review_phases

        job = self._make_job(ro, tmp_path)
        groups = [ro.Group(name="grp-a", files=["a.go"], lines=100)]

        calls = []
        def mock_invoke(inv, **kwargs):
            from pathlib import Path
            calls.append(inv.max_turns)
            output_path = str(tmp_path / "group-1.md")
            Path(output_path).write_text("## Must fix\n- **[M1]** **`a.go:1`** — issue\n")
            Path(inv.session_log).write_text("")
            return 0

        monkeypatch.setattr(review_phases, "invoke_agent", mock_invoke)
        monkeypatch.setattr(review_phases, "build_prompt", lambda *a, **kw: "mock prompt")
        monkeypatch.setattr(review_phases, "_validate_group_output", lambda *a: None)

        failed = [ro.GroupFailure("grp-a", _max_turns_16(ro))]
        result = ro._retry_failed_groups(failed, groups, job, 1, "", None)
        assert result == []
        assert calls[-1] == ro.RETRY_MAX_TURNS_GROUP

    def test_skips_model_errors(self, ro, tmp_path):
        job = self._make_job(ro, tmp_path)
        groups = [ro.Group(name="grp-a", files=["a.go"], lines=100)]

        failure = ro.GroupFailure("grp-a", ro.Diagnosis(
            ro.DiagnosisKind.AGENT_ERROR, detail="model not available"))
        result = ro._retry_failed_groups([failure], groups, job, 1, "", None)
        assert result == [failure]

    def test_non_retryable_preserved(self, ro, tmp_path):
        job = self._make_job(ro, tmp_path)
        groups = [ro.Group(name="grp-a", files=["a.go"], lines=100)]

        failed = [ro.GroupFailure("grp-a", ro.Diagnosis(
            ro.DiagnosisKind.AGENT_ERROR, detail="something broke"))]
        result = ro._retry_failed_groups(failed, groups, job, 1, "", None)
        assert result == failed

    def test_skipped_groups_run_after_retries_succeed(self, ro, tmp_path, monkeypatch):
        import review_phases

        job = self._make_job(ro, tmp_path)
        groups = [
            ro.Group(name="grp-a", files=["a.go"], lines=100),
            ro.Group(name="grp-b", files=["b.go"], lines=100),
        ]

        calls = []
        def mock_invoke(inv, **kwargs):
            from pathlib import Path
            name = inv.label
            calls.append(name)
            if name == "grp-a":
                Path(job.artifact_dir, "group-1.md").write_text("## Must fix\n- **[M1]** **`a.go:1`** — issue\n")
            elif name == "grp-b":
                Path(job.artifact_dir, "group-2.md").write_text("## Must fix\n- **[M1]** **`b.go:1`** — issue\n")
            Path(inv.session_log).write_text("")
            return 0

        monkeypatch.setattr(review_phases, "invoke_agent", mock_invoke)
        monkeypatch.setattr(review_phases, "build_prompt", lambda *a, **kw: "mock prompt")
        monkeypatch.setattr(review_phases, "_validate_group_output", lambda *a: None)

        failed = [
            ro.GroupFailure("grp-a", _max_turns_16(ro)),
            ro.GroupFailure("grp-b", ro.Diagnosis(
                ro.DiagnosisKind.SKIPPED,
                detail="3 consecutive failures (agent hit max turns) — aborting remaining 1 groups")),
        ]
        result = ro._retry_failed_groups(failed, groups, job, 2, "", None)
        assert result == []
        assert "grp-a" in calls
        assert "grp-b" in calls

    def test_skipped_groups_kept_when_retries_fail(self, ro, tmp_path, monkeypatch):
        import review_phases

        job = self._make_job(ro, tmp_path)
        groups = [
            ro.Group(name="grp-a", files=["a.go"], lines=100),
            ro.Group(name="grp-b", files=["b.go"], lines=100),
        ]

        def mock_invoke(inv, **kwargs):
            from pathlib import Path
            Path(inv.session_log).write_text("")
            return 1

        monkeypatch.setattr(review_phases, "invoke_agent", mock_invoke)
        monkeypatch.setattr(review_phases, "build_prompt", lambda *a, **kw: "mock prompt")
        monkeypatch.setattr(
            review_phases, "diagnose_missing_output",
            lambda *a: ro.Diagnosis(ro.DiagnosisKind.MAX_TURNS, num_turns=30),
        )

        failed = [
            ro.GroupFailure("grp-a", _max_turns_16(ro)),
            ro.GroupFailure("grp-b", ro.Diagnosis(
                ro.DiagnosisKind.SKIPPED,
                detail="3 consecutive failures (agent hit max turns) — aborting")),
        ]
        result = ro._retry_failed_groups(failed, groups, job, 2, "", None)
        # grp-a retry failed, so grp-b stays as skipped
        assert len(result) == 2
        result_names = [f.group for f in result]
        assert "grp-a" in result_names
        assert "grp-b" in result_names


# ── Density-based file content skipping ─────────────────────────────────────


class TestDensitySkipping:
    @staticmethod
    def _make_job(ro, tmp_path, files):
        pr = ro.PRMetadata(
            title="Test PR", body="", head="feat/test", base="main",
            head_sha="abc123", additions=0, deletions=0,
            changed_files=len(files), files=files,
        )
        ctx = ro.PRContext()
        return ro.ReviewJob(
            repo="org/repo", pr_number="1", pr=pr, ctx=ctx,
            wt_path=str(tmp_path), review_file=str(tmp_path / "review.md"),
            session_log=str(tmp_path / "session.jsonl"),
        )

    def test_large_file_small_diff_omitted(self, ro, tmp_path, monkeypatch):
        big_file = tmp_path / "big.py"
        big_file.write_text("x = 1\n" * 2000)
        files = [{"path": "big.py", "additions": 2, "deletions": 1}]
        job = self._make_job(ro, tmp_path, files)

        with contextlib.redirect_stdout(io.StringIO()):
            data = ro.collect_preflight_data(job)

        assert "big.py" not in data.file_contents
        assert "big.py" in data.omitted_files

    def test_small_file_always_included(self, ro, tmp_path, monkeypatch):
        small_file = tmp_path / "small.py"
        small_file.write_text("x = 1\n")
        files = [{"path": "small.py", "additions": 1, "deletions": 0}]
        job = self._make_job(ro, tmp_path, files)

        with contextlib.redirect_stdout(io.StringIO()):
            data = ro.collect_preflight_data(job)

        assert "small.py" in data.file_contents

    def test_high_density_file_included(self, ro, tmp_path, monkeypatch):
        file = tmp_path / "refactored.py"
        file.write_text("line\n" * 100)
        files = [{"path": "refactored.py", "additions": 80, "deletions": 70}]
        job = self._make_job(ro, tmp_path, files)

        with contextlib.redirect_stdout(io.StringIO()):
            data = ro.collect_preflight_data(job)

        assert "refactored.py" in data.file_contents


# ── Prompt stats persistence ────────────────────────────────────────────────


class TestPromptStats:
    def test_prompt_stats_written(self, ro, tmp_path):
        pr = ro.PRMetadata(
            title="t", body="", head="feat", base="main",
            head_sha="abc", additions=1, deletions=0,
            changed_files=1, files=[],
        )
        ctx = ro.PRContext()
        review_file = str(tmp_path / "review.md")
        job = ro.ReviewJob(
            repo="r", pr_number="1", pr=pr, ctx=ctx,
            wt_path=str(tmp_path), review_file=review_file,
            session_log=str(tmp_path / "s.jsonl"),
        )

        ro._log_prompt_size("test", "hello world", {"sec": "data"}, job)

        stats_file = tmp_path / ro.FILENAME_PROMPT_STATS
        assert stats_file.exists()
        stats = json.loads(stats_file.read_text())
        assert isinstance(stats, list)
        assert stats[0]["template"] == "test"
        assert stats[0]["prompt_bytes"] == len(b"hello world")
        assert "utilization_pct" in stats[0]
        assert stats[0]["sections"]["sec"] == len(b"data")

    def test_prompt_stats_appends(self, ro, tmp_path):
        pr = ro.PRMetadata(
            title="t", body="", head="feat", base="main",
            head_sha="abc", additions=1, deletions=0,
            changed_files=1, files=[],
        )
        ctx = ro.PRContext()
        review_file = str(tmp_path / "review.md")
        job = ro.ReviewJob(
            repo="r", pr_number="1", pr=pr, ctx=ctx,
            wt_path=str(tmp_path), review_file=review_file,
            session_log=str(tmp_path / "s.jsonl"),
        )

        ro._log_prompt_size("first", "aaa", {}, job)
        ro._log_prompt_size("second", "bbb", {}, job)

        stats = json.loads((tmp_path / ro.FILENAME_PROMPT_STATS).read_text())
        assert len(stats) == 2
        assert stats[0]["template"] == "first"
        assert stats[1]["template"] == "second"

    def test_prompt_stats_survives_corrupt_file(self, ro, tmp_path):
        pr = ro.PRMetadata(
            title="t", body="", head="feat", base="main",
            head_sha="abc", additions=1, deletions=0,
            changed_files=1, files=[],
        )
        ctx = ro.PRContext()
        review_file = str(tmp_path / "review.md")
        job = ro.ReviewJob(
            repo="r", pr_number="1", pr=pr, ctx=ctx,
            wt_path=str(tmp_path), review_file=review_file,
            session_log=str(tmp_path / "s.jsonl"),
        )

        stats_file = tmp_path / ro.FILENAME_PROMPT_STATS
        stats_file.write_text("")

        ro._log_prompt_size("test", "hello", {}, job)

        stats = json.loads(stats_file.read_text())
        assert isinstance(stats, list)
        assert len(stats) == 1
        assert stats[0]["template"] == "test"


# ── post_process_findings ───────────────────────────────────────────────────


class TestPostProcessFindings:
    def test_skips_verify_when_no_wt_path(self, ro, tmp_path):
        review = tmp_path / "review.md"
        must = ro.severity_by_key(ro.SEVERITY_MUST).section
        prefix = ro.SEVERITY_MUST
        review.write_text(
            f"## {must}\n"
            f"- **[{prefix}1]** **`missing.py:10`** — bug\n"
            "  > ```\n"
            "  > old_code()\n"
            "  > ```\n"
        )
        ro.post_process_findings(str(review))
        result = review.read_text()
        assert f"[{prefix}1]" in result

    def test_strips_evidence_blocks(self, ro, tmp_path):
        review = tmp_path / "review.md"
        must = ro.severity_by_key(ro.SEVERITY_MUST).section
        prefix = ro.SEVERITY_MUST
        review.write_text(
            f"## {must}\n"
            f"- **[{prefix}1]** **`foo.py:1`** — issue\n"
            "  > ```python\n"
            "  > x = 1\n"
            "  > ```\n"
        )
        ro.post_process_findings(str(review))
        assert "```python" not in review.read_text()

    def test_strips_stable_ids(self, ro, tmp_path):
        review = tmp_path / "review.md"
        must = ro.severity_by_key(ro.SEVERITY_MUST).section
        prefix = ro.SEVERITY_MUST
        review.write_text(
            f"## {must}\n"
            f"- **[{prefix}1]** <!-- sid:abc12345 --> **`foo.py:1`** — issue\n"
        )
        ro.post_process_findings(str(review))
        assert "sid:" not in review.read_text()

    def test_renumbers_findings(self, ro, tmp_path):
        review = tmp_path / "review.md"
        must = ro.severity_by_key(ro.SEVERITY_MUST).section
        prefix = ro.SEVERITY_MUST
        review.write_text(
            f"## {must}\n"
            f"- **[{prefix}3]** **`foo.py:1`** — first\n"
            f"- **[{prefix}7]** **`bar.py:1`** — second\n"
        )
        ro.post_process_findings(str(review))
        result = review.read_text()
        assert f"[{prefix}1]" in result
        assert f"[{prefix}2]" in result

    def test_strips_prior_findings_ledger(self, ro, tmp_path):
        review = tmp_path / "review.md"
        must = ro.severity_by_key(ro.SEVERITY_MUST).section
        prefix = ro.SEVERITY_MUST
        review.write_text(
            f"## {must}\n"
            f"- **[{prefix}5]** **`foo.py:1`** — still broken\n"
            f"## {ro.SECTION_PRIOR_FINDINGS}\n"
            f"- **[{prefix}2]** `bar.py` — Fixed\n"
        )
        ro.post_process_findings(str(review))
        result = review.read_text()
        assert ro.SECTION_PRIOR_FINDINGS not in result
        assert "bar.py" not in result
        # Renumbering sees only the findings this review actually reports.
        assert f"[{prefix}1]" in result

    def test_a_dropped_findings_citation_does_not_survive_it(self, ro, tmp_path):
        # The whole chain: verification drops M1, renumbering pulls M2 into its
        # place, and the Nit that cited M1 must not end up citing the survivor.
        src = tmp_path / "handler.go"
        src.write_text("package main\n\nfunc foo() {\n\tx := 1\n}\n")
        review = tmp_path / "review.md"
        review.write_text(
            "## Must fix\n"
            "- **[M1]** **`handler.go:42`** — evidence no longer in the file\n"
            "  > ```go\n"
            "  > result := db.Query(q)\n"
            "  > ```\n"
            "- **[M2]** **`handler.go:4`** — real problem\n"
            "  > ```go\n"
            "  > \tx := 1\n"
            "  > ```\n"
            "## Nit\n"
            "- **[N1]** **`handler.go:1`** — revisit once [M1] lands\n"
        )
        ro.post_process_findings(str(review), str(tmp_path))
        result = review.read_text()

        assert "- **[M1]** **`handler.go:4`** — real problem" in result
        assert "revisit once [removed] lands" in result

    def test_ledger_suppresses_the_unaccounted_warning(self, ro, tmp_path, capsys):
        review = tmp_path / "review.md"
        review.write_text(
            "## Summary\nPrior finding fixed.\n"
            f"## {ro.SECTION_PRIOR_FINDINGS}\n"
            "- **[M1]** `handler.go` — Fixed\n"
        )
        ro.post_process_findings(str(review), prior_review=PRIOR_ONE_FINDING)
        assert "not accounted for" not in capsys.readouterr().err


# ── build_mechanical_review ─────────────────────────────────────────────────


class TestBuildMechanicalReview:
    @staticmethod
    def _must_fix_content(ro, count=1):
        must = "Must fix"
        prefix = "M"
        lines = [f"## {must}"]
        for i in range(1, count + 1):
            lines.append(f"- **[{prefix}{i}]** **`f{i}.py`** — bug {i}")
        return "\n".join(lines) + "\n"

    def test_includes_title_and_summary(self, ro):
        result = ro.build_mechanical_review(
            self._must_fix_content(ro),
            title="# Review: org/repo#1 — title",
            meta_header="<!-- date: 2026-01-01 -->\n",
            group_count=2,
            summary_note="Test note.",
        )
        assert result.startswith("# Review: org/repo#1 — title")
        assert "1 finding" in result
        assert "2 groups" in result
        assert "Test note." in result

    def test_includes_verdict_by_default(self, ro):
        result = ro.build_mechanical_review(
            self._must_fix_content(ro),
            title="# Review",
            meta_header="",
            group_count=1,
            summary_note="note",
        )
        assert "## Verdict" in result
        assert "Request changes" in result

    def test_excludes_verdict_when_disabled(self, ro):
        result = ro.build_mechanical_review(
            self._must_fix_content(ro),
            title="# Self-Review",
            meta_header="",
            group_count=1,
            summary_note="note",
            include_verdict=False,
        )
        assert "## Verdict" not in result

    def test_no_findings_verdict(self, ro):
        must = "Must fix"
        result = ro.build_mechanical_review(
            f"## {must}\n_none._\n",
            title="# Review",
            meta_header="",
            group_count=1,
            summary_note="note",
        )
        assert "No findings" in result
        assert "Approve" in result

    def test_file_count_in_scope(self, ro):
        result = ro.build_mechanical_review(
            self._must_fix_content(ro),
            title="# Review",
            meta_header="",
            group_count=2,
            summary_note="note",
            file_count=3,
        )
        assert "across 3 files in 2 groups" in result


# ── _write_review_sidecar enriched meta.json ────────────────────────────────


class TestWriteReviewSidecar:
    @staticmethod
    def _make_job(ro, tmp_path):
        pr = ro.PRMetadata(
            title="Test PR", body="", head="feat/test", base="main",
            head_sha="abc123", additions=10, deletions=5,
            changed_files=3, files=[],
        )
        ctx = ro.PRContext()
        review_file = str(tmp_path / "review.md")
        return ro.ReviewJob(
            repo="org/repo", pr_number="42", pr=pr, ctx=ctx,
            wt_path=str(tmp_path), review_file=review_file,
            session_log=str(tmp_path / "session.jsonl"),
            generator_version="test-v1",
        )

    def test_meta_includes_title_and_changed_files(self, ro, tmp_path):
        job = self._make_job(ro, tmp_path)
        ro._write_review_sidecar(job)
        meta = json.loads((tmp_path / "meta.json").read_text())
        assert meta["title"] == "Test PR"
        assert meta["changed_files"] == 3

    def test_meta_includes_mode(self, ro, tmp_path):
        job = self._make_job(ro, tmp_path)
        ro._write_review_sidecar(job)
        meta = json.loads((tmp_path / "meta.json").read_text())
        assert meta["mode"] == "pr"

    def test_meta_includes_generator_version(self, ro, tmp_path):
        job = self._make_job(ro, tmp_path)
        ro._write_review_sidecar(job)
        meta = json.loads((tmp_path / "meta.json").read_text())
        assert meta["generator_version"] == "test-v1"

    def test_meta_omits_generator_when_empty(self, ro, tmp_path):
        job = self._make_job(ro, tmp_path)
        job.generator_version = ""
        ro._write_review_sidecar(job)
        meta = json.loads((tmp_path / "meta.json").read_text())
        assert "generator_version" not in meta


# ── _is_quota_error ───────────────────────────────────────────────────


class TestIsQuotaError:
    def test_detects_429_retry(self, ro, tmp_path):
        log = tmp_path / "session.jsonl"
        log.write_text(
            json.dumps({"type": "system", "subtype": "init"}) + "\n"
            + json.dumps({"type": "system", "subtype": "api_retry", "error_status": 429}) + "\n"
        )
        assert ro._is_quota_error(str(log)) is True

    def test_ignores_non_429(self, ro, tmp_path):
        log = tmp_path / "session.jsonl"
        log.write_text(
            json.dumps({"type": "system", "subtype": "api_retry", "error_status": 500}) + "\n"
        )
        assert ro._is_quota_error(str(log)) is False

    def test_false_for_normal_session(self, ro, tmp_path):
        log = tmp_path / "session.jsonl"
        log.write_text(
            json.dumps({"type": "system", "subtype": "init"}) + "\n"
            + json.dumps({"type": "result", "subtype": "completed"}) + "\n"
        )
        assert ro._is_quota_error(str(log)) is False

    def test_false_for_missing_log(self, ro, tmp_path):
        assert ro._is_quota_error(str(tmp_path / "missing.jsonl")) is False

    def test_false_for_empty_log(self, ro, tmp_path):
        log = tmp_path / "session.jsonl"
        log.write_text("")
        assert ro._is_quota_error(str(log)) is False


# ── format_preflight_data ──────────────────────────────────────────────


class TestFormatPreflightData:
    def test_includes_all_sections(self, ro):
        data = ro.PreflightData(
            diff="--- a/foo.go\n+++ b/foo.go\n@@ -1 +1 @@\n-old\n+new",
            commit_log="abc123 fix bug",
            file_contents={"foo.go": "package main\n", "bar.go": "package bar\n"},
            file_permissions={"foo.go": "0o644", "bar.go": "0o755"},
            claude_md="# My Project",
            architecture_md="## Known Constraints",
            review_checklists={"security.md": "# Security checks"},
        )
        result = ro.format_preflight_data(data)
        assert "Pre-collected data" in result
        assert "```diff" in result
        assert "foo.go" in result
        assert "bar.go" in result
        assert "# My Project" in result
        assert "Known Constraints" in result
        assert "Security checks" in result
        assert "abc123 fix bug" in result

    def test_file_filter_scopes_file_contents(self, ro):
        data = ro.PreflightData(
            diff="full diff",
            commit_log="log",
            file_contents={"foo.go": "package main", "bar.go": "package bar"},
            file_permissions={"foo.go": "0o644", "bar.go": "0o755"},
            claude_md="",
            architecture_md="",
        )
        result = ro.format_preflight_data(data, file_filter=["foo.go"])
        assert "package main" in result
        assert "package bar" not in result

    def test_file_filter_scopes_diff(self, ro):
        diff_text = (
            "diff --git a/foo.go b/foo.go\n"
            "--- a/foo.go\n"
            "+++ b/foo.go\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
            "\n"
            "diff --git a/bar.go b/bar.go\n"
            "--- a/bar.go\n"
            "+++ b/bar.go\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
        )
        data = ro.PreflightData(
            diff=diff_text,
            commit_log="log",
            file_contents={"foo.go": "package main", "bar.go": "package bar"},
            file_permissions={"foo.go": "0o644", "bar.go": "0o755"},
            claude_md="",
            architecture_md="",
        )
        result = ro.format_preflight_data(data, file_filter=["foo.go"])
        assert "a/foo.go" in result
        assert "a/bar.go" not in result

    def test_empty_commit_log_omits_section(self, ro):
        data = ro.PreflightData(
            diff="--- a/f.go\n+++ b/f.go",
            commit_log="",
            file_contents={"f.go": "code"},
            file_permissions={"f.go": "0o644"},
            claude_md="",
            architecture_md="",
        )
        result = ro.format_preflight_data(data)
        assert "Commit history" not in result

    def test_omitted_files_listed_in_output(self, ro):
        data = ro.PreflightData(
            diff="--- a/a.go\n+++ b/a.go",
            commit_log="log",
            file_contents={"a.go": "code"},
            file_permissions={"a.go": "0o644"},
            claude_md="",
            architecture_md="",
            omitted_files=["big.go", "huge.go"],
        )
        result = ro.format_preflight_data(data)
        assert "Files not pre-collected" in result
        assert "- big.go" in result
        assert "- huge.go" in result
        assert "a.go" in result

    def test_no_omitted_section_when_all_files_included(self, ro):
        data = ro.PreflightData(
            diff="--- a/a.go\n+++ b/a.go",
            commit_log="log",
            file_contents={"a.go": "code"},
            file_permissions={"a.go": "0o644"},
            claude_md="",
            architecture_md="",
        )
        result = ro.format_preflight_data(data)
        assert "Files not pre-collected" not in result

    def test_skip_file_contents_omits_file_contents_and_omitted_list(self, ro):
        data = ro.PreflightData(
            diff="--- a/foo.go\n+++ b/foo.go",
            commit_log="abc123 fix bug",
            file_contents={"foo.go": "package main"},
            file_permissions={"foo.go": "0o644"},
            claude_md="# Project",
            architecture_md="",
            omitted_files=["bar.go"],
        )
        result = ro.format_preflight_data(data, skip_file_contents=True)
        assert "```diff" in result
        assert "abc123 fix bug" in result
        assert "# Project" in result
        assert "package main" not in result
        assert "Changed file contents" not in result
        assert "Files not pre-collected" not in result


# ── _file_permissions ──────────────────────────────────────────────────


class TestFilePermissions:
    def test_returns_octal_for_normal_file(self, ro, tmp_path):
        f = tmp_path / "perm.txt"
        f.write_text("test\n")
        f.chmod(0o644)
        assert ro._file_permissions(f) == "0o644"

    def test_returns_question_mark_for_missing_file(self, ro, tmp_path):
        assert ro._file_permissions(tmp_path / "nonexistent.txt") == "?"

    def test_returns_executable_mode(self, ro, tmp_path):
        f = tmp_path / "exec.sh"
        f.write_text("#!/bin/sh\n")
        f.chmod(0o755)
        assert ro._file_permissions(f) == "0o755"


# ── build_prompt (preflight) ──────────────────────────────────────────


class TestBuildPromptPreflight:
    def test_includes_preflight_data_when_set(self, ro):
        pr = ro.PRMetadata(
            title="Fix", body="desc", head="feat", base="main", head_sha="abc",
            additions=10, deletions=5, changed_files=1,
            files=[{"path": "a.go", "additions": 10, "deletions": 5}],
        )
        ctx = ro.PRContext(
            commits="fix it", reviews="[]",
            review_comments="[]", comments="[]",
        )
        preflight = ro.PreflightData(
            diff="--- a/a.go\n+++ b/a.go",
            commit_log="abc fix",
            file_contents={"a.go": "package main"},
            file_permissions={"a.go": "0o644"},
            claude_md="# Project",
            architecture_md="",
        )
        job = ro.ReviewJob(
            repo="org/repo", pr_number="99", pr=pr, ctx=ctx,
            wt_path="/tmp/wt", review_file="/tmp/review.md",
            session_log="/tmp/session.jsonl",
            preflight=preflight,
        )
        result = ro.build_prompt("single-agent.md", job, max_turns=15)
        assert "Pre-collected data" in result
        assert "package main" in result
        assert "--- a/a.go" in result

    def test_no_preflight_data_when_not_set(self, ro):
        pr = ro.PRMetadata(
            title="Fix", body="desc", head="feat", base="main", head_sha="abc",
            additions=10, deletions=5, changed_files=1,
            files=[{"path": "a.go", "additions": 10, "deletions": 5}],
        )
        ctx = ro.PRContext(
            commits="fix it", reviews="[]",
            review_comments="[]", comments="[]",
        )
        job = ro.ReviewJob(
            repo="org/repo", pr_number="99", pr=pr, ctx=ctx,
            wt_path="/tmp/wt", review_file="/tmp/review.md",
            session_log="/tmp/session.jsonl",
        )
        result = ro.build_prompt("single-agent.md", job, max_turns=15)
        assert "Pre-collected data" not in result

    def test_synthesis_includes_reviews_section(self, ro):
        pr = ro.PRMetadata(
            title="Fix", body="", head="feat", base="main", head_sha="abc",
            additions=10, deletions=5, changed_files=1,
            files=[{"path": "a.go", "additions": 10, "deletions": 5}],
        )
        ctx = ro.PRContext(
            commits="fix",
            reviews='[{"user":"bob","state":"APPROVED"}]',
            review_comments="[]",
            comments="[]",
        )
        job = ro.ReviewJob(
            repo="org/repo", pr_number="1", pr=pr, ctx=ctx,
            wt_path="/tmp/wt", review_file="/tmp/review.md",
            session_log="/tmp/session.jsonl",
        )
        result = ro.build_prompt(
            "synthesis.md", job, max_turns=15,
            holistic_content="assessment",
            group_count=1,
            merged_content="## Must fix\n- [M1] bug",
        )
        assert "bob" in result
        assert "APPROVED" in result

    def test_group_template_gets_scoped_preflight(self, ro):
        pr = ro.PRMetadata(
            title="Fix", body="", head="feat", base="main", head_sha="abc",
            additions=20, deletions=10, changed_files=2,
            files=[
                {"path": "a.go", "additions": 10, "deletions": 5},
                {"path": "b.go", "additions": 10, "deletions": 5},
            ],
        )
        ctx = ro.PRContext()
        preflight = ro.PreflightData(
            diff="full diff here",
            commit_log="commits",
            file_contents={"a.go": "package a", "b.go": "package b"},
            file_permissions={"a.go": "0o644", "b.go": "0o644"},
            claude_md="",
            architecture_md="",
        )
        job = ro.ReviewJob(
            repo="org/repo", pr_number="1", pr=pr, ctx=ctx,
            wt_path="/tmp/wt", review_file="/tmp/review.md",
            session_log="/tmp/session.jsonl",
            preflight=preflight,
        )
        result = ro.build_prompt(
            "group.md", job, max_turns=15,
            group_idx=1, group_count=2, group_name="pkg",
            group_files_formatted="  - a.go (+10 -5)",
            group_output="/tmp/g1.md",
            holistic_content="",
            group_file_paths=["a.go"],
        )
        assert "package a" in result
        assert "package b" not in result

    def test_holistic_template_includes_preflight(self, ro):
        pr = ro.PRMetadata(
            title="Fix", body="", head="feat", base="main", head_sha="abc",
            additions=10, deletions=5, changed_files=1,
            files=[{"path": "a.go", "additions": 10, "deletions": 5}],
        )
        ctx = ro.PRContext(
            commits="fix", reviews="[]",
            review_comments="[]", comments="[]",
        )
        preflight = ro.PreflightData(
            diff="--- a/a.go\n+++ b/a.go",
            commit_log="abc fix",
            file_contents={"a.go": "package main"},
            file_permissions={"a.go": "0o644"},
            claude_md="# Proj",
            architecture_md="",
        )
        job = ro.ReviewJob(
            repo="org/repo", pr_number="1", pr=pr, ctx=ctx,
            wt_path="/tmp/wt", review_file="/tmp/review.md",
            session_log="/tmp/session.jsonl",
            preflight=preflight,
        )
        result = ro.build_prompt(
            "holistic.md", job, max_turns=15,
            holistic_output="/tmp/h.md",
        )
        assert "Pre-collected data" in result
        assert "package main" in result

    def test_self_review_template_includes_preflight(self, ro):
        pr = ro.PRMetadata(
            title="Fix", body="", head="feat", base="main", head_sha="abc",
            additions=10, deletions=5, changed_files=1,
            files=[{"path": "a.go", "additions": 10, "deletions": 5}],
        )
        ctx = ro.PRContext()
        preflight = ro.PreflightData(
            diff="--- a/a.go\n+++ b/a.go",
            commit_log="abc fix",
            file_contents={"a.go": "package main"},
            file_permissions={"a.go": "0o644"},
            claude_md="",
            architecture_md="",
        )
        job = ro.ReviewJob(
            repo="org/repo", pr_number="1", pr=pr, ctx=ctx,
            wt_path="/tmp/wt", review_file="/tmp/review.md",
            session_log="/tmp/session.jsonl",
            preflight=preflight,
        )
        result = ro.build_prompt(
            "self-review.md", job, max_turns=15,
            branch_name="feat",
        )
        assert "Pre-collected data" in result
        assert "package main" in result

    def test_env_section_all_files_pre_collected(self, ro):
        pr = ro.PRMetadata(
            title="Fix", body="", head="feat", base="main", head_sha="abc",
            additions=10, deletions=5, changed_files=1,
            files=[{"path": "a.go", "additions": 10, "deletions": 5}],
        )
        ctx = ro.PRContext(
            commits="fix", reviews="[]",
            review_comments="[]", comments="[]",
        )
        preflight = ro.PreflightData(
            diff="diff", commit_log="log",
            file_contents={"a.go": "pkg"},
            file_permissions={"a.go": "0o644"},
            claude_md="", architecture_md="",
        )
        job = ro.ReviewJob(
            repo="org/repo", pr_number="1", pr=pr, ctx=ctx,
            wt_path="/tmp/wt", review_file="/tmp/review.md",
            session_log="/tmp/session.jsonl",
            preflight=preflight,
        )
        result = ro.build_prompt("single-agent.md", job, max_turns=15)
        assert "NOT in the PR" in result
        assert "Files not pre-collected" not in result
        assert "Read source files directly" not in result

    def test_env_section_partial_preflight_with_omitted_files(self, ro):
        pr = ro.PRMetadata(
            title="Fix", body="", head="feat", base="main", head_sha="abc",
            additions=10, deletions=5, changed_files=2,
            files=[
                {"path": "a.go", "additions": 5, "deletions": 2},
                {"path": "b.go", "additions": 5, "deletions": 3},
            ],
        )
        ctx = ro.PRContext(
            commits="fix", reviews="[]",
            review_comments="[]", comments="[]",
        )
        preflight = ro.PreflightData(
            diff="diff", commit_log="log",
            file_contents={"a.go": "pkg"},
            file_permissions={"a.go": "0o644"},
            claude_md="", architecture_md="",
            omitted_files=["b.go"],
        )
        job = ro.ReviewJob(
            repo="org/repo", pr_number="1", pr=pr, ctx=ctx,
            wt_path="/tmp/wt", review_file="/tmp/review.md",
            session_log="/tmp/session.jsonl",
            preflight=preflight,
        )
        result = ro.build_prompt("single-agent.md", job, max_turns=15)
        assert "NOT in the PR" not in result
        assert "must be read directly" in result
        assert "Read source files directly" not in result

    def test_env_section_no_preflight(self, ro):
        pr = ro.PRMetadata(
            title="Fix", body="", head="feat", base="main", head_sha="abc",
            additions=10, deletions=5, changed_files=1,
            files=[{"path": "a.go", "additions": 10, "deletions": 5}],
        )
        ctx = ro.PRContext(
            commits="fix", reviews="[]",
            review_comments="[]", comments="[]",
        )
        job = ro.ReviewJob(
            repo="org/repo", pr_number="1", pr=pr, ctx=ctx,
            wt_path="/tmp/wt", review_file="/tmp/review.md",
            session_log="/tmp/session.jsonl",
        )
        result = ro.build_prompt("single-agent.md", job, max_turns=15)
        assert "NOT in the PR" not in result
        assert "must be read directly" not in result
        assert "Read source files directly" in result


# ── collect_preflight_data (git repo tests) ───────────────────────────


class TestCollectPreflightData:
    @staticmethod
    def _init_repo(path):
        """Initialize a git repo with standard test config."""
        subprocess.run(
            ["git", "init", "-b", "main", "-q"], cwd=str(path),
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"], cwd=str(path),
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"], cwd=str(path),
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "config", "commit.gpgsign", "false"], cwd=str(path),
            check=True, capture_output=True,
        )

    @staticmethod
    def _add_origin(path):
        """Add the repo itself as origin and fetch main."""
        subprocess.run(
            ["git", "remote", "add", "origin", str(path)], cwd=str(path),
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "fetch", "-q", "origin", "main"], cwd=str(path),
            check=True, capture_output=True,
        )

    @staticmethod
    def _commit_all(path, message):
        """Stage every change in the repo and commit it."""
        subprocess.run(
            ["git", "add", "."], cwd=str(path), check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-q", "--no-verify", "-m", message],
            cwd=str(path), check=True, capture_output=True,
        )

    def test_oversized_file_in_diff_but_omitted_from_contents(self, ro, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        self._init_repo(repo)
        (repo / "big.txt").write_text("x" * 600_000)
        subprocess.run(
            ["git", "add", "."], cwd=str(repo), check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-q", "--no-verify", "-m", "init"],
            cwd=str(repo), check=True, capture_output=True,
        )
        self._add_origin(repo)
        subprocess.run(
            ["git", "checkout", "-b", "feat", "-q"],
            cwd=str(repo), check=True, capture_output=True,
        )
        (repo / "big.txt").write_text("y" * 600_000)
        subprocess.run(
            ["git", "add", "."], cwd=str(repo), check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-q", "--no-verify", "-m", "change"],
            cwd=str(repo), check=True, capture_output=True,
        )

        pr = ro.PRMetadata(
            title="t", body="", head="feat", base="main", head_sha="abc",
            additions=1, deletions=0, changed_files=1,
            files=[{"path": "big.txt", "additions": 1, "deletions": 0}],
        )
        ctx = ro.PRContext()
        job = ro.ReviewJob(
            repo="r", pr_number="1", pr=pr, ctx=ctx,
            wt_path=str(repo), review_file=str(tmp_path / "review.md"),
            session_log=str(tmp_path / "session.jsonl"),
        )
        with contextlib.redirect_stdout(io.StringIO()):
            data = ro.collect_preflight_data(job)
        assert data is not None
        assert len(data.diff) > 0
        assert data.omitted_files == ["big.txt"]
        assert data.file_contents == {}

    def test_large_diff_includes_diff_but_omits_some_files(self, ro, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        self._init_repo(repo)
        for i in range(1, 6):
            content = "".join(
                f"original_line_content_padding_{j}\n" for j in range(10_000)
            )
            (repo / f"file{i}.go").write_text(content)
        subprocess.run(
            ["git", "add", "."], cwd=str(repo), check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-q", "--no-verify", "-m", "init"],
            cwd=str(repo), check=True, capture_output=True,
        )
        self._add_origin(repo)
        subprocess.run(
            ["git", "checkout", "-b", "feat", "-q"],
            cwd=str(repo), check=True, capture_output=True,
        )
        for i in range(1, 6):
            content = "".join(
                f"modified_line_content_padding_{j}\n" for j in range(10_000)
            )
            (repo / f"file{i}.go").write_text(content)
        subprocess.run(
            ["git", "add", "."], cwd=str(repo), check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-q", "--no-verify", "-m", "change"],
            cwd=str(repo), check=True, capture_output=True,
        )

        pr = ro.PRMetadata(
            title="t", body="", head="feat", base="main", head_sha="abc",
            additions=50_000, deletions=50_000, changed_files=5,
            files=[
                {"path": f"file{i}.go", "additions": 10_000, "deletions": 10_000}
                for i in range(1, 6)
            ],
        )
        ctx = ro.PRContext()
        job = ro.ReviewJob(
            repo="r", pr_number="1", pr=pr, ctx=ctx,
            wt_path=str(repo), review_file=str(tmp_path / "review.md"),
            session_log=str(tmp_path / "session.jsonl"),
        )
        with contextlib.redirect_stdout(io.StringIO()):
            data = ro.collect_preflight_data(job)
        assert data is not None
        assert len(data.diff) > 0
        assert len(data.omitted_files) > 0
        assert len(data.file_contents) + len(data.omitted_files) == 5

    def test_success_path_collects_all_data(self, ro, tmp_path):
        repo = tmp_path / "repo"
        (repo / ".claude" / "review").mkdir(parents=True)
        self._init_repo(repo)
        (repo / "main.go").write_text("package main\n")
        (repo / "CLAUDE.md").write_text("# Project\n")
        (repo / ".claude" / "architecture.md").write_text("## Known Constraints\n")
        (repo / ".claude" / "review" / "security.md").write_text("# Security\n")
        subprocess.run(
            ["git", "add", "."], cwd=str(repo), check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-q", "--no-verify", "-m", "init"],
            cwd=str(repo), check=True, capture_output=True,
        )
        self._add_origin(repo)
        subprocess.run(
            ["git", "checkout", "-b", "feat", "-q"],
            cwd=str(repo), check=True, capture_output=True,
        )
        (repo / "main.go").write_text("package main\nfunc hello() {}\n")
        subprocess.run(
            ["git", "add", "."], cwd=str(repo), check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-q", "--no-verify", "-m", "add hello"],
            cwd=str(repo), check=True, capture_output=True,
        )

        pr = ro.PRMetadata(
            title="t", body="", head="feat", base="main", head_sha="abc",
            additions=1, deletions=0, changed_files=1,
            files=[{"path": "main.go", "additions": 1, "deletions": 0}],
        )
        ctx = ro.PRContext()
        job = ro.ReviewJob(
            repo="r", pr_number="1", pr=pr, ctx=ctx,
            wt_path=str(repo), review_file=str(tmp_path / "review.md"),
            session_log=str(tmp_path / "session.jsonl"),
        )
        with contextlib.redirect_stdout(io.StringIO()):
            data = ro.collect_preflight_data(job)
        assert data is not None
        assert "main.go" in data.file_contents
        assert "main.go" in data.file_permissions
        assert data.file_permissions["main.go"] != "?"
        assert "# Project" in data.claude_md
        assert "## Known Constraints" in data.architecture_md
        assert "security.md" in data.review_checklists
        assert len(data.diff) > 0
        assert len(data.commit_log) > 0
        assert data.omitted_files == []

    def test_handles_deleted_files_in_pr(self, ro, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        self._init_repo(repo)
        (repo / "removed.txt").write_text("old content\n")
        (repo / "kept.txt").write_text("keep\n")
        subprocess.run(
            ["git", "add", "."], cwd=str(repo), check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-q", "--no-verify", "-m", "init"],
            cwd=str(repo), check=True, capture_output=True,
        )
        self._add_origin(repo)
        subprocess.run(
            ["git", "checkout", "-b", "feat", "-q"],
            cwd=str(repo), check=True, capture_output=True,
        )
        (repo / "removed.txt").unlink()
        (repo / "kept.txt").write_text("updated\n")
        subprocess.run(
            ["git", "add", "."], cwd=str(repo), check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-q", "--no-verify", "-m", "remove file"],
            cwd=str(repo), check=True, capture_output=True,
        )

        pr = ro.PRMetadata(
            title="t", body="", head="feat", base="main", head_sha="abc",
            additions=1, deletions=1, changed_files=2,
            files=[
                {"path": "removed.txt", "additions": 0, "deletions": 1},
                {"path": "kept.txt", "additions": 1, "deletions": 0},
            ],
        )
        ctx = ro.PRContext()
        job = ro.ReviewJob(
            repo="r", pr_number="1", pr=pr, ctx=ctx,
            wt_path=str(repo), review_file=str(tmp_path / "review.md"),
            session_log=str(tmp_path / "session.jsonl"),
        )
        with contextlib.redirect_stdout(io.StringIO()):
            data = ro.collect_preflight_data(job)
        assert data.file_contents["removed.txt"] == "<file deleted>"
        assert "updated" in data.file_contents["kept.txt"]
        assert len(data.diff) > 0

    def test_captures_uncommitted_diff_when_no_commits_on_branch(self, ro, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        self._init_repo(repo)
        (repo / "main.go").write_text("package main\n")
        subprocess.run(
            ["git", "add", "."], cwd=str(repo), check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-q", "--no-verify", "-m", "init"],
            cwd=str(repo), check=True, capture_output=True,
        )
        self._add_origin(repo)
        (repo / "main.go").write_text("package main\nfunc hello() {}\n")

        pr = ro.fetch_branch_metadata(str(repo))
        ctx = ro.PRContext()
        job = ro.ReviewJob(
            repo="r", pr_number="", pr=pr, ctx=ctx,
            wt_path=str(repo), review_file=str(tmp_path / "review.md"),
            session_log=str(tmp_path / "session.jsonl"),
            mode="self",
        )
        with contextlib.redirect_stdout(io.StringIO()):
            data = ro.collect_preflight_data(job)
        assert "func hello" in data.diff
        assert "main.go" in data.file_contents

    def _repo_with_worktree_changes(self, repo):
        """Branch with one commit, one uncommitted edit and one untracked file."""
        self._init_repo(repo)
        (repo / "main.go").write_text("package main\n")
        (repo / "helper.go").write_text("package main\n")
        self._commit_all(repo, "init")
        self._add_origin(repo)
        subprocess.run(
            ["git", "checkout", "-b", "feat", "-q"],
            cwd=str(repo), check=True, capture_output=True,
        )
        (repo / "main.go").write_text("package main\nfunc committed() {}\n")
        self._commit_all(repo, "add committed")
        (repo / "helper.go").write_text("package main\nfunc uncommitted() {}\n")
        (repo / "extra.go").write_text("package main\nfunc untracked() {}\n")

    def test_self_mode_diff_spans_the_whole_worktree(self, ro, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        self._repo_with_worktree_changes(repo)

        pr = ro.fetch_branch_metadata(str(repo))
        job = ro.ReviewJob(
            repo="r", pr_number="", pr=pr, ctx=ro.PRContext(),
            wt_path=str(repo), review_file=str(tmp_path / "review.md"),
            session_log=str(tmp_path / "session.jsonl"),
            mode="self",
        )
        with contextlib.redirect_stdout(io.StringIO()):
            data = ro.collect_preflight_data(job)
        assert "func committed" in data.diff
        assert "func uncommitted" in data.diff
        assert "func untracked" in data.diff
        assert set(data.file_contents) == {"main.go", "helper.go", "extra.go"}

    def test_pr_mode_diff_stops_at_head(self, ro, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        self._repo_with_worktree_changes(repo)

        pr = ro.PRMetadata(
            title="t", body="", head="feat", base="main", head_sha="abc",
            additions=1, deletions=0, changed_files=1,
            files=[{"path": "main.go", "additions": 1, "deletions": 0}],
        )
        job = ro.ReviewJob(
            repo="r", pr_number="1", pr=pr, ctx=ro.PRContext(),
            wt_path=str(repo), review_file=str(tmp_path / "review.md"),
            session_log=str(tmp_path / "session.jsonl"),
        )
        with contextlib.redirect_stdout(io.StringIO()):
            data = ro.collect_preflight_data(job)
        assert "func committed" in data.diff
        assert "func uncommitted" not in data.diff
        assert "func untracked" not in data.diff

    def test_low_density_large_file_omitted_from_contents(self, ro, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        self._init_repo(repo)
        (repo / "big.py").write_text("x = 1\n" * 2000)
        subprocess.run(
            ["git", "add", "."], cwd=str(repo), check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-q", "--no-verify", "-m", "init"],
            cwd=str(repo), check=True, capture_output=True,
        )
        self._add_origin(repo)
        subprocess.run(
            ["git", "checkout", "-b", "feat", "-q"],
            cwd=str(repo), check=True, capture_output=True,
        )
        with open(str(repo / "big.py"), "a") as f:
            f.write("new_line_1\n")
            f.write("new_line_2\n")
        subprocess.run(
            ["git", "add", "."], cwd=str(repo), check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-q", "--no-verify", "-m", "small change"],
            cwd=str(repo), check=True, capture_output=True,
        )

        pr = ro.PRMetadata(
            title="t", body="", head="feat", base="main", head_sha="abc",
            additions=2, deletions=0, changed_files=1,
            files=[{"path": "big.py", "additions": 2, "deletions": 0}],
        )
        ctx = ro.PRContext()
        job = ro.ReviewJob(
            repo="r", pr_number="1", pr=pr, ctx=ctx,
            wt_path=str(repo), review_file=str(tmp_path / "review.md"),
            session_log=str(tmp_path / "session.jsonl"),
        )
        with contextlib.redirect_stdout(io.StringIO()):
            data = ro.collect_preflight_data(job)
        assert "big.py" not in data.file_contents
        assert "big.py" in data.omitted_files

    def test_tier1_files_prioritized_over_tier2_when_budget_tight(
        self, ro, tmp_path, monkeypatch,
    ):
        repo = tmp_path / "repo"
        repo.mkdir()
        self._init_repo(repo)
        (repo / "CLAUDE.md").write_text("# Rules\n" * 10)
        (repo / "util.go").write_text("package main\n" + "func f() {}\n" * 3000)
        subprocess.run(
            ["git", "add", "."], cwd=str(repo), check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-q", "--no-verify", "-m", "init"],
            cwd=str(repo), check=True, capture_output=True,
        )
        self._add_origin(repo)
        subprocess.run(
            ["git", "checkout", "-b", "feat", "-q"],
            cwd=str(repo), check=True, capture_output=True,
        )
        (repo / "CLAUDE.md").write_text("# Updated rules\n" * 10)
        (repo / "util.go").write_text("package main\n" + "func g() {}\n" * 3000)
        subprocess.run(
            ["git", "add", "."], cwd=str(repo), check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-q", "--no-verify", "-m", "change"],
            cwd=str(repo), check=True, capture_output=True,
        )

        pr = ro.PRMetadata(
            title="t", body="", head="feat", base="main", head_sha="abc",
            additions=2, deletions=2, changed_files=2,
            files=[
                {"path": "util.go", "additions": 3000, "deletions": 3000},
                {"path": "CLAUDE.md", "additions": 10, "deletions": 10},
            ],
        )
        ctx = ro.PRContext()
        job = ro.ReviewJob(
            repo="r", pr_number="1", pr=pr, ctx=ctx,
            wt_path=str(repo), review_file=str(tmp_path / "review.md"),
            session_log=str(tmp_path / "session.jsonl"),
        )
        # Set budget so diff fits but only ~1000 bytes remain for file contents
        diff_size = len(ro._run(
            ["git", "diff", "origin/main...HEAD"], cwd=str(repo),
        ).encode())
        monkeypatch.setattr(ro, "MAX_PROMPT_BYTES", diff_size + ro.TEMPLATE_OVERHEAD_BYTES + 1000)
        with contextlib.redirect_stdout(io.StringIO()):
            data = ro.collect_preflight_data(job)
        assert "CLAUDE.md" in data.file_contents
        assert "util.go" in data.omitted_files


# ── fetch_branch_metadata ─────────────────────────────────────────────


class TestFetchBranchMetadata:
    # Shared with TestCollectPreflightData — single source of truth for repo helpers.
    _init_repo = staticmethod(TestCollectPreflightData._init_repo)
    _add_origin = staticmethod(TestCollectPreflightData._add_origin)
    _commit_all = staticmethod(TestCollectPreflightData._commit_all)

    def test_includes_uncommitted_changes_when_no_commits_on_branch(
        self, ro, tmp_path,
    ):
        repo = tmp_path / "repo"
        repo.mkdir()
        self._init_repo(repo)
        (repo / "main.go").write_text("package main\n")
        subprocess.run(
            ["git", "add", "."], cwd=str(repo), check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-q", "--no-verify", "-m", "init"],
            cwd=str(repo), check=True, capture_output=True,
        )
        self._add_origin(repo)
        # Stay on main but modify a file without committing
        (repo / "main.go").write_text("package main\nfunc hello() {}\n")

        pr = ro.fetch_branch_metadata(str(repo))
        assert pr.changed_files == 1
        assert pr.files[0]["path"] == "main.go"

    def test_includes_staged_changes_when_no_commits_on_branch(
        self, ro, tmp_path,
    ):
        repo = tmp_path / "repo"
        repo.mkdir()
        self._init_repo(repo)
        (repo / "main.go").write_text("package main\n")
        subprocess.run(
            ["git", "add", "."], cwd=str(repo), check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-q", "--no-verify", "-m", "init"],
            cwd=str(repo), check=True, capture_output=True,
        )
        self._add_origin(repo)
        # Stage changes without committing
        (repo / "main.go").write_text("package main\nfunc staged() {}\n")
        subprocess.run(
            ["git", "add", "main.go"], cwd=str(repo),
            check=True, capture_output=True,
        )

        pr = ro.fetch_branch_metadata(str(repo))
        assert pr.changed_files == 1

    def test_committed_uncommitted_and_untracked_changes_all_appear(
        self, ro, tmp_path,
    ):
        repo = tmp_path / "repo"
        repo.mkdir()
        self._init_repo(repo)
        (repo / "main.go").write_text("package main\n")
        (repo / "helper.go").write_text("package main\n")
        (repo / ".gitignore").write_text("secret.txt\n")
        self._commit_all(repo, "init")
        self._add_origin(repo)
        subprocess.run(
            ["git", "checkout", "-b", "feat", "-q"],
            cwd=str(repo), check=True, capture_output=True,
        )
        (repo / "main.go").write_text("package main\nfunc committed() {}\n")
        self._commit_all(repo, "add committed")
        (repo / "helper.go").write_text("package main\nfunc uncommitted() {}\n")
        (repo / "extra.go").write_text("package main\nfunc untracked() {}\n")
        (repo / "secret.txt").write_text("ignored\n")

        pr = ro.fetch_branch_metadata(str(repo))
        paths = sorted(f["path"] for f in pr.files)
        assert paths == ["extra.go", "helper.go", "main.go"]
        assert pr.changed_files == 3
        assert "secret.txt" not in paths

    def test_untracked_files_are_counted_as_whole_file_additions(self, ro, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        self._init_repo(repo)
        (repo / "main.go").write_text("package main\n")
        self._commit_all(repo, "init")
        self._add_origin(repo)
        (repo / "new.go").write_text("one\ntwo\nthree\n")

        pr = ro.fetch_branch_metadata(str(repo))
        assert pr.files == [{"path": "new.go", "additions": 3, "deletions": 0}]
        assert pr.additions == 3
        assert pr.deletions == 0

    def test_commits_on_base_are_not_reported_as_branch_changes(self, ro, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        self._init_repo(repo)
        (repo / "main.go").write_text("package main\n")
        self._commit_all(repo, "init")
        self._add_origin(repo)
        subprocess.run(
            ["git", "checkout", "-b", "feat", "-q"],
            cwd=str(repo), check=True, capture_output=True,
        )
        (repo / "feat.go").write_text("package main\nfunc feat() {}\n")
        self._commit_all(repo, "add feat")
        # Move main forward behind the branch's back, so the branch is stale
        subprocess.run(
            ["git", "checkout", "main", "-q"],
            cwd=str(repo), check=True, capture_output=True,
        )
        (repo / "other.go").write_text("package main\nfunc other() {}\n")
        self._commit_all(repo, "add other")
        subprocess.run(
            ["git", "fetch", "-q", "origin", "main"],
            cwd=str(repo), check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "checkout", "feat", "-q"],
            cwd=str(repo), check=True, capture_output=True,
        )

        pr = ro.fetch_branch_metadata(str(repo))
        paths = [f["path"] for f in pr.files]
        assert paths == ["feat.go"]

    def test_the_base_ref_is_fetched_before_the_range_is_built(self, ro, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        self._init_repo(repo)
        (repo / "main.go").write_text("package main\n")
        self._commit_all(repo, "init")
        # Origin is added but never fetched, so origin/main does not resolve and
        # the fork point would collapse to HEAD — hiding every commit.
        subprocess.run(
            ["git", "remote", "add", "origin", str(repo)], cwd=str(repo),
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "checkout", "-b", "feat", "-q"],
            cwd=str(repo), check=True, capture_output=True,
        )
        (repo / "feat.go").write_text("package main\nfunc feat() {}\n")
        self._commit_all(repo, "add feat")

        pr = ro.fetch_branch_metadata(str(repo))
        assert [f["path"] for f in pr.files] == ["feat.go"]

    def test_base_argument_selects_the_diff_range(self, ro, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        self._init_repo(repo)
        (repo / "main.go").write_text("package main\n")
        self._commit_all(repo, "init")
        subprocess.run(
            ["git", "checkout", "-b", "develop", "-q"],
            cwd=str(repo), check=True, capture_output=True,
        )
        (repo / "dev.go").write_text("package main\nfunc dev() {}\n")
        self._commit_all(repo, "add dev")
        self._add_origin(repo)
        subprocess.run(
            ["git", "fetch", "-q", "origin", "develop"],
            cwd=str(repo), check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "checkout", "-b", "feat", "-q"],
            cwd=str(repo), check=True, capture_output=True,
        )
        (repo / "feat.go").write_text("package main\nfunc feat() {}\n")
        self._commit_all(repo, "add feat")

        pr = ro.fetch_branch_metadata(str(repo), "develop")
        assert [f["path"] for f in pr.files] == ["feat.go"]
        assert pr.base == "develop"


# ── self-review metadata ──────────────────────────────────────────────


def _pr_metadata(ro, **overrides):
    defaults = dict(
        title="feat: thing", body="why", head="feat", base="main",
        head_sha="a" * 40, additions=1, deletions=0, changed_files=1,
        files=[{"path": "pushed.go", "additions": 1, "deletions": 0}],
    )
    return ro.PRMetadata(**{**defaults, **overrides})


class TestWithLocalDiff:
    def test_diff_surface_comes_from_the_worktree(self, ro):
        pr = _pr_metadata(ro)
        local = _pr_metadata(
            ro, head_sha="b" * 40, additions=9, deletions=2, changed_files=2,
            files=[
                {"path": "pushed.go", "additions": 1, "deletions": 0},
                {"path": "unpushed.go", "additions": 8, "deletions": 2},
            ],
        )

        merged = ro._with_local_diff(pr, local)

        assert merged.head_sha == "b" * 40
        assert [f["path"] for f in merged.files] == ["pushed.go", "unpushed.go"]
        assert merged.additions == 9
        assert merged.deletions == 2
        assert merged.changed_files == 2

    def test_branch_name_comes_from_the_worktree(self, ro):
        pr = _pr_metadata(ro, head="feat")
        local = _pr_metadata(ro, head="renamed-locally")

        assert ro._with_local_diff(pr, local).head == "renamed-locally"

    def test_pr_narrative_is_preserved(self, ro):
        pr = _pr_metadata(ro, labels=["review"], author="isaac", is_draft=True)
        local = _pr_metadata(ro, title="add unpushed", body="", head_sha="b" * 40)

        merged = ro._with_local_diff(pr, local)

        assert merged.title == "feat: thing"
        assert merged.body == "why"
        assert merged.labels == ["review"]
        assert merged.author == "isaac"
        assert merged.is_draft is True

    def test_drift_is_reported(self, ro, capsys):
        pr = _pr_metadata(ro)
        local = _pr_metadata(ro, head_sha="b" * 40)

        ro._with_local_diff(pr, local)

        err = capsys.readouterr().err
        assert "b" * 7 in err
        assert "a" * 7 in err

    def test_matching_heads_are_silent(self, ro, capsys):
        pr = _pr_metadata(ro)

        ro._with_local_diff(pr, _pr_metadata(ro))

        assert capsys.readouterr().err == ""


class TestFetchMetadataSelfMode:
    def test_self_review_of_a_pr_sees_unpushed_commits(self, ro, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        repo.mkdir()
        TestFetchBranchMetadata._init_repo(repo)
        (repo / "main.go").write_text("package main\n")
        TestFetchBranchMetadata._commit_all(repo, "init")
        TestFetchBranchMetadata._add_origin(repo)
        subprocess.run(
            ["git", "checkout", "-b", "feat", "-q"],
            cwd=str(repo), check=True, capture_output=True,
        )
        (repo / "unpushed.go").write_text("package main\nfunc unpushed() {}\n")
        TestFetchBranchMetadata._commit_all(repo, "add unpushed")

        monkeypatch.setattr(
            ro._rpl, "fetch_pr_metadata",
            lambda repo_name, pr_number: _pr_metadata(ro),
        )

        pr, ctx, pr_data = ro._fetch_metadata("o/r", "1", ro.Mode.SELF, str(repo))

        assert [f["path"] for f in pr.files] == ["unpushed.go"]
        assert pr.head_sha != "a" * 40
        assert pr.title == "feat: thing"
        assert pr_data is None


class TestStaticAnalysisIntegration:
    def test_static_analysis_injected_into_review(self, ro, tmp_path):
        review_file = tmp_path / "review.md"
        review_file.write_text("## Summary\nLooks good.\n\n## Verdict\nApprove")

        deep_script = tmp_path / "deep.sh"
        deep_script.write_text(
            "#!/bin/bash\n"
            "func() {\n"
            "  if true; then\n"
            "    for x in a; do\n"
            "      while true; do\n"
            "        echo deep\n"
            "      done\n"
            "    done\n"
            "  fi\n"
            "}\n"
        )

        changed_files = [{"path": "deep.sh", "additions": 10, "deletions": 0}]
        ro._inject_static_analysis_section(str(review_file), changed_files, str(tmp_path))

        result = review_file.read_text()
        assert "## Static Analysis" in result
        assert "Nesting depth" in result
        assert result.index("## Static Analysis") < result.index("## Verdict")

    def test_static_analysis_skipped_when_no_applicable_files(self, ro, tmp_path):
        review_file = tmp_path / "review.md"
        original = "## Summary\nLooks good.\n\n## Verdict\nApprove"
        review_file.write_text(original)

        changed_files = [{"path": "README.md", "additions": 5, "deletions": 0}]
        ro._inject_static_analysis_section(str(review_file), changed_files, str(tmp_path))

        assert review_file.read_text() == original

    def test_static_analysis_clean_files(self, ro, tmp_path):
        review_file = tmp_path / "review.md"
        review_file.write_text("## Summary\nLooks good.\n\n## Verdict\nApprove")

        clean_script = tmp_path / "clean.sh"
        clean_script.write_text("#!/bin/bash\necho hello\n")

        changed_files = [{"path": "clean.sh", "additions": 2, "deletions": 0}]
        ro._inject_static_analysis_section(str(review_file), changed_files, str(tmp_path))

        result = review_file.read_text()
        assert "## Static Analysis" in result
        assert "All checks passed" in result


class TestPipelineStateFailureRoundTrip:
    """`groups_failed` survives state.json in both the old and new format."""

    def _job(self, ro, tmp_path):
        job = MagicMock()
        job.review_file = str(tmp_path / "review.md")
        return job

    def _state(self, ro, groups_failed):
        from review_preflight import PipelineState
        return PipelineState(
            head_sha="abc", group_names=["ui"], groups_failed=groups_failed,
        )

    def test_a_diagnosis_survives_write_then_read(self, ro, tmp_path):
        diagnosis = ro.Diagnosis(
            ro.DiagnosisKind.MAX_TURNS, num_turns=12, no_write_tool=True,
        )
        job = self._job(ro, tmp_path)
        ro._write_pipeline_state(job, self._state(ro, {1: diagnosis}))
        assert ro._read_pipeline_state(job).groups_failed == {1: diagnosis}

    def test_a_legacy_string_recovers_the_kind_it_renders_as(self, ro, tmp_path):
        """State written before diagnoses were typed holds a rendered reason.

        Where that reason is one a kind renders verbatim, the kind comes back —
        a recovered run gets the real retry policy rather than UNKNOWN's.
        """
        path = ro._pipeline_state_path(self._job(ro, tmp_path))
        Path(path).write_text(json.dumps({
            "head_sha": "abc", "group_names": ["ui"],
            "groups_failed": {"1": "quota exhausted (429)"},
        }))
        state = ro._read_pipeline_state(self._job(ro, tmp_path))
        assert state.groups_failed == {1: ro.Diagnosis(ro.DiagnosisKind.QUOTA_EXHAUSTED)}

    def test_a_legacy_string_no_kind_renders_stays_unknown(self, ro, tmp_path):
        """An interpolated reason names no kind, so it is kept as written."""
        path = ro._pipeline_state_path(self._job(ro, tmp_path))
        Path(path).write_text(json.dumps({
            "head_sha": "abc", "group_names": ["ui"],
            "groups_failed": {"1": "agent hit max turns (12)"},
        }))
        state = ro._read_pipeline_state(self._job(ro, tmp_path))
        assert state.groups_failed == {
            1: ro.Diagnosis(ro.DiagnosisKind.UNKNOWN, detail="agent hit max turns (12)"),
        }

    def _synthesis_state(self, ro, tmp_path, raw):
        path = ro._pipeline_state_path(self._job(ro, tmp_path))
        Path(path).write_text(json.dumps({
            "head_sha": "abc", "group_names": ["ui"], "synthesis_failed": raw,
        }))
        return ro._read_pipeline_state(self._job(ro, tmp_path))

    @pytest.mark.parametrize("raw,kind", [
        ("all groups failed", "ALL_GROUPS_FAILED"),
        ("mechanical fallback", "MECHANICAL_FALLBACK"),
        ("budget exceeded", "BUDGET_EXCEEDED"),
    ])
    def test_a_legacy_synthesis_sentinel_becomes_its_kind(self, ro, tmp_path, raw, kind):
        """The three strings synthesis used to write are now diagnoses.

        A review directory written before the change is the common case for
        `--recover`, so each sentinel has to read back as the kind it names —
        otherwise the recovered run loses the outcome it recorded.
        """
        state = self._synthesis_state(ro, tmp_path, raw)
        assert state.synthesis_failed == ro.Diagnosis(getattr(ro.DiagnosisKind, kind))

    def test_a_legacy_empty_synthesis_field_is_no_failure_at_all(self, ro, tmp_path):
        """`""` was how the old schema spelled "synthesis did not fail".

        Read as a blank diagnosis it would be truthy, and every reader tests
        truthiness — a clean recovered run would report itself partial and
        grow an Agent Failures section with an empty reason.
        """
        state = self._synthesis_state(ro, tmp_path, "")

        assert state.synthesis_failed is None
        assert ro.build_failures_section(state) == ""

    def test_a_legacy_reason_still_renders_verbatim(self, ro, tmp_path):
        state = self._state(ro, {
            1: ro.Diagnosis(ro.DiagnosisKind.UNKNOWN, detail="quota exhausted (429)"),
        })
        assert "quota exhausted (429)" in ro.build_failures_section(state)


class TestBuildFailuresSection:
    def test_no_failures_returns_empty(self):
        from review_preflight import PipelineState
        from review_state import build_failures_section
        state = PipelineState(
            head_sha="abc", group_names=["ui", "api"],
            groups_done=[1, 2], groups_failed={},
            synthesis_done=True,
        )
        assert build_failures_section(state) == ""

    def test_group_failures_produce_table(self):
        from review_common import Diagnosis, DiagnosisKind
        from review_preflight import PipelineState
        from review_state import build_failures_section
        state = PipelineState(
            head_sha="abc", group_names=["ui-components", "api-routes", "tests"],
            groups_done=[1], groups_failed={
                2: Diagnosis(DiagnosisKind.QUOTA_EXHAUSTED),
                3: Diagnosis(DiagnosisKind.MAX_TURNS, num_turns=5),
            },
            synthesis_done=True,
        )
        result = build_failures_section(state)
        assert "## Agent Failures" in result
        assert "group-2: api-routes" in result
        assert "quota exhausted (429)" in result
        assert "group-3: tests" in result
        assert "agent hit max turns" in result
        assert "failed" in result
        assert "pr review --recover" in result

    def test_synthesis_fallback_in_table(self):
        from review_common import Diagnosis, DiagnosisKind
        from review_preflight import PipelineState
        from review_state import build_failures_section
        state = PipelineState(
            head_sha="abc", group_names=["g1"],
            groups_done=[1], groups_failed={},
            synthesis_done=True,
            synthesis_failed=Diagnosis(DiagnosisKind.MECHANICAL_FALLBACK),
        )
        result = build_failures_section(state)
        assert "synthesis" in result
        assert "fallback" in result

    def test_no_recover_hint_for_permission_errors(self):
        """The reason a denial really produces, not the bare marker.

        The check this replaced compared the whole rendered reason against the
        marker tuple, so it never fired on `agent error: permission denied`.
        """
        from review_common import Diagnosis, DiagnosisKind
        from review_preflight import PipelineState
        from review_state import build_failures_section
        state = PipelineState(
            head_sha="abc", group_names=["g1"],
            groups_done=[],
            groups_failed={
                1: Diagnosis(DiagnosisKind.AGENT_ERROR, detail="permission denied"),
            },
            synthesis_done=True,
        )
        result = build_failures_section(state)
        assert "## Agent Failures" in result
        assert "agent error: permission denied" in result
        assert "pr review --recover" not in result

    def test_recover_hint_survives_one_recoverable_failure(self):
        from review_common import Diagnosis, DiagnosisKind
        from review_preflight import PipelineState
        from review_state import build_failures_section
        state = PipelineState(
            head_sha="abc", group_names=["g1", "g2"],
            groups_done=[],
            groups_failed={
                1: Diagnosis(DiagnosisKind.AGENT_ERROR, detail="permission denied"),
                2: Diagnosis(DiagnosisKind.MAX_TURNS, num_turns=5),
            },
            synthesis_done=True,
        )
        assert "pr review --recover" in build_failures_section(state)

    def test_no_recover_hint_when_every_group_is_unrecoverable(self):
        from review_common import Diagnosis, DiagnosisKind
        from review_preflight import PipelineState
        from review_state import build_failures_section
        denial = Diagnosis(DiagnosisKind.AGENT_ERROR, detail="permission denied")
        state = PipelineState(
            head_sha="abc", group_names=["g1", "g2"],
            groups_done=[], groups_failed={1: denial, 2: denial},
            synthesis_done=True,
        )
        assert "pr review --recover" not in build_failures_section(state)

    def test_recover_hint_offered_for_max_turns(self):
        from review_common import Diagnosis, DiagnosisKind
        from review_preflight import PipelineState
        from review_state import build_failures_section
        state = PipelineState(
            head_sha="abc", group_names=["g1"],
            groups_done=[], groups_failed={1: Diagnosis(DiagnosisKind.MAX_TURNS, num_turns=5)},
            synthesis_done=True,
        )
        assert "pr review --recover" in build_failures_section(state)

    def test_synthesis_failure_alone_stays_recoverable(self):
        from review_common import Diagnosis, DiagnosisKind
        from review_preflight import PipelineState
        from review_state import build_failures_section
        state = PipelineState(
            head_sha="abc", group_names=["g1"],
            groups_done=[1], groups_failed={},
            synthesis_done=True,
            synthesis_failed=Diagnosis(DiagnosisKind.MECHANICAL_FALLBACK),
        )
        assert "pr review --recover" in build_failures_section(state)


def test_meta_status_constant_format():
    from review_common import META_STATUS
    assert META_STATUS.format(status="complete") == "<!-- status: complete -->"
    assert META_STATUS.format(status="partial") == "<!-- status: partial -->"
    assert META_STATUS.format(status="error") == "<!-- status: error -->"


class TestFailuresSectionInReview:
    def test_mechanical_fallback_includes_failures(self, tmp_path):
        """When synthesis falls back, the review includes ## Agent Failures."""
        from review_common import Diagnosis, DiagnosisKind
        from review_preflight import PipelineState
        from review_state import build_failures_section
        state = PipelineState(
            head_sha="abc", group_names=["ui", "api"],
            groups_done=[1], groups_failed={2: Diagnosis(DiagnosisKind.QUOTA_EXHAUSTED)},
            synthesis_done=True,
            synthesis_failed=Diagnosis(DiagnosisKind.MECHANICAL_FALLBACK),
        )
        result = build_failures_section(state)
        assert "group-2: api" in result
        assert "quota exhausted" in result
        assert "synthesis" in result
        assert "fallback" in result


class TestInjectFailuresAndStatus:
    """Tests for _inject_failures_and_status — specifically the always-update status fix."""

    def _make_pipeline_json(self, tmp_path, synthesis_failed="", groups_failed=None):
        import json
        data = {
            "head_sha": "abc123",
            "group_names": ["ui", "api"],
            "groups_done": [1],
            "groups_failed": groups_failed or {},
            "holistic_done": False,
            "synthesis_done": True,
            "synthesis_failed": synthesis_failed,
            "review_type": "full",
            "prior_sha": "",
            "skipped_groups": [],
        }
        (tmp_path / "pipeline.json").write_text(json.dumps(data))

    def test_replaces_existing_status_line(self, tmp_path):
        """I1: status already present as 'completed' is updated to 'partial' on synthesis failure."""
        from review_common import Diagnosis, DiagnosisKind
        from review_preflight import PipelineState
        from review_state import _inject_failures_and_status

        # Write a review file that already has a 'completed' status line
        review_file = tmp_path / "review.md"
        review_file.write_text(
            "<!-- status: completed -->\n"
            "<!-- generator: claude-review v1 -->\n"
            "\n"
            "## Summary\n\nAll good.\n"
        )

        # Pipeline state says synthesis failed — status should be 'partial'
        self._make_pipeline_json(tmp_path, synthesis_failed="budget exceeded")

        state = PipelineState(
            head_sha="abc123", group_names=["ui", "api"],
            groups_done=[1], groups_failed={},
            synthesis_done=True,
            synthesis_failed=Diagnosis(DiagnosisKind.BUDGET_EXCEEDED),
        )
        _inject_failures_and_status(str(review_file), state)

        content = review_file.read_text()
        assert "<!-- status: partial -->" in content
        assert "<!-- status: completed -->" not in content

    def test_inserts_status_when_absent(self, tmp_path):
        """Status line is inserted before the generator line when not already present."""
        from review_common import Diagnosis, DiagnosisKind
        from review_preflight import PipelineState
        from review_state import _inject_failures_and_status

        review_file = tmp_path / "review.md"
        review_file.write_text(
            "<!-- generator: claude-review v1 -->\n"
            "\n"
            "## Summary\n\nAll good.\n"
        )

        self._make_pipeline_json(tmp_path, synthesis_failed="")

        state = PipelineState(
            head_sha="abc123", group_names=["ui", "api"],
            groups_done=[1, 2], groups_failed={},
            synthesis_done=True,
        )
        _inject_failures_and_status(str(review_file), state)

        content = review_file.read_text()
        assert "<!-- status: completed -->" in content

    def test_replaces_status_not_duplicated(self, tmp_path):
        """Replacing an existing status line does not add a second status line."""
        from review_common import Diagnosis, DiagnosisKind
        from review_preflight import PipelineState
        from review_state import _inject_failures_and_status

        review_file = tmp_path / "review.md"
        review_file.write_text(
            "<!-- status: completed -->\n"
            "<!-- generator: claude-review v1 -->\n"
            "\n"
            "## Summary\n\nAll good.\n"
        )

        self._make_pipeline_json(tmp_path, synthesis_failed="mechanical fallback")

        state = PipelineState(
            head_sha="abc123", group_names=["ui", "api"],
            groups_done=[1], groups_failed={},
            synthesis_done=True,
            synthesis_failed=Diagnosis(DiagnosisKind.MECHANICAL_FALLBACK),
        )
        _inject_failures_and_status(str(review_file), state)

        content = review_file.read_text()
        assert content.count("<!-- status:") == 1


class TestCleanupScope:
    """What `_run_orchestrate` leaves in the review directory when it returns.

    Driven through the whole run rather than through the sweep, because the
    leak this covers was in the order the phases run and not in any one of
    them: the pipeline swept as it returned, and the fix pass wrote its log
    afterwards. Each phase is replaced by something that writes the files a
    real one leaves behind, which is all the sweep can see.
    """

    _REVIEW = synthetic_review(meta="status: completed", summary="All good.")

    @staticmethod
    def _args(ro, review_file, repo_dir, **overrides):
        from types import SimpleNamespace

        defaults = {
            "pr": "1", "review_file": str(review_file), "repo_dir": str(repo_dir),
            "mode": ro.Mode.PR, "effort": ro.Effort.MEDIUM,
            "prior_review": "", "issue": "", "issue_context": "",
            "generator_version": "", "model": "", "recover_sha": "",
            "target_dir": str(repo_dir), "max_parallel": 2,
            "max_cost": 20.0, "max_groups": None,
            "no_holistic": False, "no_scout": False, "disprove": False,
            "generated": False, "fix": False,
        }
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def _run(self, ro, monkeypatch, tmp_path, pipeline=None, out=None, **arg_overrides):
        """Drive `_run_orchestrate` over *tmp_path* with every phase faked.

        Pass *out* to keep the result JSON the run prints; the default throws
        it away, since what most of these tests read is the directory.
        """
        review_dir = tmp_path / "review-dir"
        review_dir.mkdir()
        review_file = review_dir / "review.md"

        pr = ro.PRMetadata(
            title="t", body="", head="feat", base="main", head_sha="abc123",
            additions=10, deletions=0, changed_files=1,
            files=[{"path": "a.py", "additions": 10, "deletions": 0}],
        )

        def _pipeline(job, **_kwargs):
            Path(job.review_file).write_text(self._REVIEW)
            Path(job.session_log).write_text("{}\n")
            (review_dir / "meta.json").write_text("{}")
            (review_dir / "disprove.jsonl").write_text("{}\n")
            (review_dir / "prompt-single.md").write_text("PROMPT")

        def _fix(job, **_kwargs):
            (Path(job.artifact_dir) / "fix.jsonl").write_text("{}\n")

        monkeypatch.setattr(ro.ai_backend, "preflight", lambda *a, **k: True)
        monkeypatch.setattr(ro.pr_state, "load_state", lambda *a, **k: None)
        monkeypatch.setattr(
            ro, "_fetch_metadata", lambda *a, **k: (pr, ro.PRContext(), None),
        )
        monkeypatch.setattr(ro, "collect_preflight_data", lambda job: MagicMock())
        monkeypatch.setattr(ro, "run_single_agent", pipeline or _pipeline)
        monkeypatch.setattr(ro, "run_static_analysis", lambda *a, **k: [])
        monkeypatch.setattr(ro, "run_fix_pass", _fix)

        args = self._args(ro, review_file, tmp_path, **arg_overrides)
        with contextlib.redirect_stdout(out if out is not None else io.StringIO()):
            ro._run_orchestrate(MagicMock(), args, "org/repo", str(review_dir / "session.jsonl"))
        return review_dir

    def test_the_fix_pass_log_does_not_outlive_the_run(self, ro, monkeypatch, tmp_path):
        """Regression for #678: the sweep used to run before the fix pass did."""
        review_dir = self._run(ro, monkeypatch, tmp_path, fix=True)

        assert not (review_dir / "fix.jsonl").exists()
        assert sorted(p.name for p in review_dir.iterdir()) == [
            "meta.json", "review.md", "session.jsonl",
        ]

    def test_a_run_without_the_fix_pass_is_swept_too(self, ro, monkeypatch, tmp_path):
        review_dir = self._run(ro, monkeypatch, tmp_path)

        assert not (review_dir / "disprove.jsonl").exists()
        assert not (review_dir / "prompt-single.md").exists()
        assert (review_dir / "review.md").exists()

    def test_a_failed_run_keeps_its_artifacts(self, ro, monkeypatch, tmp_path):
        """A pipeline that exits non-zero leaves everything for diagnosis."""
        def _fails(job, **_kwargs):
            (Path(job.artifact_dir) / "disprove.jsonl").write_text("{}\n")
            (Path(job.artifact_dir) / "prompt-single.md").write_text("PROMPT")
            raise SystemExit(1)

        with pytest.raises(SystemExit) as exc:
            self._run(ro, monkeypatch, tmp_path, pipeline=_fails, fix=True)

        assert exc.value.code == 1
        review_dir = tmp_path / "review-dir"
        assert (review_dir / "disprove.jsonl").exists()
        assert (review_dir / "prompt-single.md").exists()

    def test_a_partial_run_keeps_its_artifacts(self, ro, monkeypatch, tmp_path):
        """Failed groups are not an exception, but they still block the sweep.

        `pr review --recover` resumes from pipeline.json and the outputs of the
        groups that did succeed, so a sweep here would strand the recovery.
        """
        import serde
        from review_common import Diagnosis, DiagnosisKind, FILENAME_PIPELINE_STATE
        from review_preflight import PipelineState

        def _partial(job, **_kwargs):
            review_dir = Path(job.artifact_dir)
            Path(job.review_file).write_text(self._REVIEW)
            (review_dir / "group-1.md").write_text("finding")
            state = PipelineState(
                head_sha="abc123", group_names=["a", "b"], groups_done=[1],
                groups_failed={2: Diagnosis(DiagnosisKind.MAX_TURNS, num_turns=20)},
                synthesis_done=True,
            )
            (review_dir / FILENAME_PIPELINE_STATE).write_text(
                json.dumps(serde.to_dict(state)),
            )

        review_dir = self._run(ro, monkeypatch, tmp_path, pipeline=_partial, fix=True)

        assert (review_dir / "group-1.md").exists()
        assert (review_dir / "pipeline.json").exists()
        assert (review_dir / "fix.jsonl").exists()

    def test_a_failed_sweep_still_reports_the_review(self, ro, monkeypatch, tmp_path):
        """The result JSON is printed after the sweep, so the sweep must not eat it.

        `unlink(missing_ok=True)` only suppresses FileNotFoundError; a
        read-only filesystem raises. claude-review reads the review it just
        paid for out of this JSON, so failing to delete a log cannot be what
        loses it.
        """
        import review_gc

        def _explode(review_dir):
            raise OSError(30, "Read-only file system", str(review_dir / "disprove.jsonl"))

        monkeypatch.setattr(review_gc, "cleanup_intermediates", _explode)

        out = io.StringIO()
        review_dir = self._run(ro, monkeypatch, tmp_path, out=out)

        assert json.loads(out.getvalue()) == {
            "review_file": str(review_dir / "review.md"),
            "session_log": str(review_dir / "session.jsonl"),
            "mode": ro.PIPELINE_SINGLE,
        }
        assert (review_dir / "disprove.jsonl").exists()
