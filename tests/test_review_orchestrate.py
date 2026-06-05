import pytest


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
    def test_strips_evidence_preserves_finding(self, ro, tmp_path):
        review = tmp_path / "review.md"
        review.write_text(
            "## Must fix\n"
            "- **[M1]** **`file.go:42`** — missing error check\n"
            "  > ```go\n"
            "  > result := db.Query(q)\n"
            "  > ```\n"
            "## Nit\n"
            "- **[N1]** **`file.go:10`** — rename var\n"
        )
        ro.strip_evidence_blocks(str(review))
        result = review.read_text()
        assert "```go" not in result
        assert "result := db.Query" not in result
        assert "**[M1]**" in result
        assert "missing error check" in result
        assert "**[N1]**" in result

    def test_no_evidence_blocks_unchanged(self, ro, tmp_path):
        review = tmp_path / "review.md"
        content = "## Must fix\n- **[M1]** **`file.go:42`** — finding\n"
        review.write_text(content)
        ro.strip_evidence_blocks(str(review))
        assert review.read_text() == content

    def test_top_level_blockquote_preserved(self, ro, tmp_path):
        review = tmp_path / "review.md"
        content = (
            "## Summary\n"
            "> ```go\n"
            "> example code\n"
            "> ```\n"
            "## Must fix\n"
            "- **[M1]** **`file.go:42`** — finding\n"
        )
        review.write_text(content)
        ro.strip_evidence_blocks(str(review))
        assert "> ```go" in review.read_text()


class TestStripStableIds:
    def test_removes_sid_comments(self, ro, tmp_path):
        review = tmp_path / "review.md"
        review.write_text(
            '- **[M1]** <!-- sid:abc12345 --> **`file.go:42`** — desc\n'
        )
        ro.strip_stable_ids(str(review))
        result = review.read_text()
        assert "<!-- sid:" not in result
        assert "**[M1]** **`file.go:42`**" in result

    def test_no_sids_unchanged(self, ro, tmp_path):
        review = tmp_path / "review.md"
        content = "- **[M1]** **`file.go:42`** — desc\n"
        review.write_text(content)
        ro.strip_stable_ids(str(review))
        assert review.read_text() == content


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


class TestCountFindings:
    def test_counts_unique_ids(self, ro):
        text = "- **[M1]** finding\nsee [M1] above\n- **[M2]** another"
        counts = ro._count_findings(text)
        assert counts[ro.FINDING_PREFIX_MUST] == 2

    def test_no_findings(self, ro):
        counts = ro._count_findings("no findings here")
        assert all(v == 0 for v in counts.values())

    def test_mixed_severities(self, ro):
        text = "- **[M1]** must\n- **[S1]** should\n- **[N1]** nit\n- **[I1]** idiom"
        counts = ro._count_findings(text)
        assert counts[ro.FINDING_PREFIX_MUST] == 1
        assert counts[ro.FINDING_PREFIX_SHOULD] == 1
        assert counts[ro.FINDING_PREFIX_NIT] == 1
        assert counts[ro.FINDING_PREFIX_IDIOMS] == 1


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


class TestCheckOrphanedPriorIds:
    def test_no_orphans(self, ro):
        prior = "<!-- sid:aabb1122 --> finding 1"
        review = "<!-- sid:aabb1122 --> carried forward"
        assert ro._check_orphaned_prior_ids(prior, review) == []

    def test_detects_orphans(self, ro):
        prior = "<!-- sid:aabb1122 --> finding 1\n<!-- sid:ccdd3344 --> finding 2"
        review = "<!-- sid:aabb1122 --> carried forward"
        assert ro._check_orphaned_prior_ids(prior, review) == ["ccdd3344"]

    def test_empty_prior(self, ro):
        assert ro._check_orphaned_prior_ids("", "<!-- sid:abc -->") == []
