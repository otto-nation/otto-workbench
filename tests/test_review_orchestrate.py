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
