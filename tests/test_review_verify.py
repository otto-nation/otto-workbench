"""Tests for the two gates that check a review against the tree it reviewed.

Evidence verification is tested from both ends: the pieces — pulling the quote
off a finding, normalizing each side of the comparison, reading the finding
lines the check walks — and the whole, which is what `verify_findings` reports
and what `_remove_dropped_findings` leaves behind.

The comment-stripping cases carry the most weight, because the regression they
guard is one only the whole comparison shows: stripping the quote and not the
file leaves the file holding text the quote no longer has, and a quote copied
verbatim out of the file then fails to match it.

The disprove gate is the second half — reading an agent's verdicts back and
applying them. What is not here is the reconciliation that makes a review's
Summary and Verdict account for what either gate removed: nothing calls it
directly, so it is exercised through `post_process_findings` alongside the
rest of the post-processing pipeline in `test_review_orchestrate.py`.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import review_verify
from review_verify import DisproveResult, apply_disprove_results, parse_disprove_output


def _verifies(path: str, evidence: str | None, wt_path: str) -> bool:
    """Whether a finding's evidence matches the file it was quoted from."""
    return review_verify._match_evidence(path, evidence, wt_path)["match_result"]


# ── Evidence verification ────────────────────────────────────────────────────


class TestExtractEvidence:
    def test_extracts_from_blockquoted_fenced_code(self):
        body = (
            "missing error check on `db.Query()`\n"
            "  > ```go\n"
            "  > result := db.Query(query)\n"
            "  > ```"
        )
        assert review_verify._extract_evidence(body) == "result := db.Query(query)"

    def test_multiline_snippet(self):
        body = (
            "description\n"
            "  > ```python\n"
            "  > x = 1\n"
            "  > y = 2\n"
            "  > ```"
        )
        assert review_verify._extract_evidence(body) == "x = 1\ny = 2"

    def test_returns_none_when_no_evidence(self):
        assert review_verify._extract_evidence("just a description, no code block") is None

    def test_handles_no_language_tag(self):
        body = (
            "desc\n"
            "  > ```\n"
            "  > some_code()\n"
            "  > ```"
        )
        assert review_verify._extract_evidence(body) == "some_code()"


class TestNormalizeCode:
    def test_strips_whitespace_and_blank_lines(self):
        assert review_verify._normalize_code("  x = 1  \n\n  y = 2  ") == "x = 1\ny = 2"

    def test_empty_string(self):
        assert review_verify._normalize_code("") == ""

    def test_preserves_content(self):
        assert review_verify._normalize_code("result := db.Query(q)") == "result := db.Query(q)"


class TestMatchEvidence:
    def test_valid_evidence_passes(self, tmp_path):
        src = tmp_path / "handler.go"
        src.write_text("package main\n\nfunc foo() {\n\tresult := db.Query(q)\n}\n")
        assert _verifies("handler.go", "result := db.Query(q)", str(tmp_path)) is True

    def test_evidence_not_in_file_fails(self, tmp_path):
        src = tmp_path / "handler.go"
        src.write_text("package main\n\nfunc foo() {\n\tx := 1\n}\n")
        assert _verifies("handler.go", "result := db.Query(q)", str(tmp_path)) is False

    def test_file_not_found_fails(self, tmp_path):
        assert _verifies("missing.go", "any code", str(tmp_path)) is False

    def test_none_evidence_file_exists_passes(self, tmp_path):
        src = tmp_path / "handler.go"
        src.write_text("package main\n")
        assert _verifies("handler.go", None, str(tmp_path)) is True

    def test_none_evidence_file_missing_fails(self, tmp_path):
        assert _verifies("missing.go", None, str(tmp_path)) is False

    def test_indentation_mismatch_still_passes(self, tmp_path):
        src = tmp_path / "handler.go"
        src.write_text("func foo() {\n\t\tresult := db.Query(q)\n}\n")
        assert _verifies("handler.go", "result := db.Query(q)", str(tmp_path)) is True

    def test_trailing_go_comment_stripped(self, tmp_path):
        src = tmp_path / "handler.go"
        src.write_text("data.Completed[n] = completed\ndata.Posted[n] = posted\n")
        evidence = "data.Completed[n] = completed   // never read in template\ndata.Posted[n] = posted"
        assert _verifies("handler.go", evidence, str(tmp_path)) is True

    def test_trailing_template_comment_stripped(self, tmp_path):
        src = tmp_path / "page.html"
        src.write_text("{{ end }}\n")
        evidence = "{{ end }}\n{{/* no else — renders nothing */}}"
        assert _verifies("page.html", evidence, str(tmp_path)) is True

    def test_ellipsis_fragments_verified(self, tmp_path):
        src = tmp_path / "handler.go"
        src.write_text("func foo() {\n\tresult := db.Query(q)\n\tif err != nil {\n\t\treturn err\n\t}\n\tuse(result)\n}\n")
        evidence = "result := db.Query(q)\n...\nuse(result)"
        assert _verifies("handler.go", evidence, str(tmp_path)) is True

    def test_ellipsis_fragment_not_in_file_fails(self, tmp_path):
        src = tmp_path / "handler.go"
        src.write_text("func foo() {\n\tresult := db.Query(q)\n\tuse(result)\n}\n")
        evidence = "result := db.Query(q)\n...\nnonexistent_call()"
        assert _verifies("handler.go", evidence, str(tmp_path)) is False

    def test_comment_inside_string_not_stripped(self, tmp_path):
        src = tmp_path / "handler.go"
        src.write_text('msg := "value // not a comment"\n')
        evidence = 'msg := "value // not a comment"'
        assert _verifies("handler.go", evidence, str(tmp_path)) is True

    @pytest.mark.parametrize("lines", [(0, 6), (1, 5), (2, 6), (4, 6)])
    def test_verbatim_quote_of_any_span_passes(self, tmp_path, lines):
        """Evidence copied out of the file verifies, whatever it spans.

        The regression: comment stripping ran on the quote only, so the file
        kept text the quote no longer had. Every span here — over a
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
        assert _verifies("publish.py", evidence, str(tmp_path)) is True

    def test_reviewer_annotation_comment_passes(self, tmp_path):
        # Reviewers annotate evidence with lines the file does not contain.
        src = tmp_path / "handler.go"
        src.write_text("func foo() {\n\tresult := db.Query(q)\n}\n")
        evidence = "result := db.Query(q)\n// err is never checked"
        assert _verifies("handler.go", evidence, str(tmp_path)) is True

    def test_wrong_code_beside_a_comment_still_fails(self, tmp_path):
        src = tmp_path / "handler.go"
        src.write_text("func foo() {\n\t// query the db\n\tresult := db.Query(q)\n}\n")
        evidence = "// query the db\nresult := db.Exec(q)"
        assert _verifies("handler.go", evidence, str(tmp_path)) is False


class TestStripComments:
    def test_strips_go_comment(self):
        assert review_verify._strip_comments("x = 1 // explanation") == "x = 1"

    def test_strips_python_comment(self):
        assert review_verify._strip_comments("x = 1 # explanation") == "x = 1"

    def test_strips_template_comment(self):
        assert review_verify._strip_comments("{{ end }}{{/* note */}}") == "{{ end }}"

    def test_preserves_comment_inside_string(self):
        line = 'msg := "value // not a comment"'
        assert review_verify._strip_comments(line) == line

    def test_no_comment_unchanged(self):
        assert review_verify._strip_comments("x = 1") == "x = 1"

    def test_drops_indented_whole_line_comment(self):
        assert review_verify._strip_comments("    # why this matters\n    x = 1") == "\n    x = 1"

    def test_drops_column_zero_whole_line_comment(self):
        assert review_verify._strip_comments("// why this matters\nx = 1") == "\nx = 1"

    def test_keeps_directive_with_no_space_after_marker(self):
        # Not prose: dropping these would erase code from the comparison.
        assert review_verify._strip_comments("#!/usr/bin/env bash") == "#!/usr/bin/env bash"
        assert review_verify._strip_comments("#include <stdio.h>") == "#include <stdio.h>"
        assert review_verify._strip_comments("\t//nolint:errcheck") == "\t//nolint:errcheck"


class TestVerificationDetail:
    def test_match_records_pass(self, tmp_path):
        src = tmp_path / "handler.go"
        src.write_text("func foo() {\n\tresult := db.Query(q)\n}\n")
        finding = {"id": "S1", "severity": "S", "path": "handler.go", "body": ""}
        detail = review_verify._verification_detail(finding, "result := db.Query(q)", str(tmp_path))
        assert detail["match_result"] is True
        assert detail["file_exists"] is True

    def test_mismatch_records_failure(self, tmp_path):
        src = tmp_path / "handler.go"
        src.write_text("func foo() {\n\tx := 1\n}\n")
        finding = {"id": "S1", "severity": "S", "path": "handler.go", "body": ""}
        detail = review_verify._verification_detail(finding, "result := db.Query(q)", str(tmp_path))
        assert detail["match_result"] is False
        assert "longest_match_prefix" in detail
        assert "first_mismatch" in detail


class TestVerifyFindings:
    def test_returns_verification_summary(self, tmp_path):
        src = tmp_path / "handler.go"
        src.write_text("package main\n\nfunc foo() {\n\tresult := db.Query(q)\n}\n")
        text = (
            "## Should fix\n"
            "- [ ] **[S1]** **`handler.go:42`** — desc\n"
            "  > ```go\n"
            "  > result := db.Query(q)\n"
            "  > ```\n"
        )
        out_text, result = review_verify.verify_findings(text, str(tmp_path))
        assert result["dropped"] == []
        assert result["findings_checked"] == 1
        assert result["findings_passed"] == 1
        assert result["findings_dropped"] == 0
        assert out_text == text

    def test_drops_unverified_and_returns_details(self, tmp_path):
        src = tmp_path / "handler.go"
        src.write_text("package main\n\nfunc foo() {\n\tx := 1\n}\n")
        text = (
            "## Should fix\n"
            "- [ ] **[S1]** **`handler.go:42`** — desc\n"
            "  > ```go\n"
            "  > result := db.Query(q)\n"
            "  > ```\n"
        )
        out_text, result = review_verify.verify_findings(text, str(tmp_path))
        assert result["dropped"] == ["S1"]
        assert result["findings_dropped"] == 1
        assert result["details"][0]["match_result"] is False
        assert "S1" not in out_text


class TestParseVerificationStripsLine:
    def test_path_excludes_line_number(self):
        text = '- **[M1]** **`pkg/handler.go:42`** — missing error check\n'
        findings = review_verify._parse_findings_for_verification(text)
        assert findings[0]["path"] == "pkg/handler.go"

    def test_path_excludes_line_range(self):
        text = '- **[S1]** **`pkg/handler.go:10-20`** — issue\n'
        findings = review_verify._parse_findings_for_verification(text)
        assert findings[0]["path"] == "pkg/handler.go"

    def test_checkbox_path_excludes_line(self):
        text = '- [ ] **[M1]** `handler.go:42` — desc\n'
        findings = review_verify._parse_findings_for_verification(text)
        assert findings[0]["path"] == "handler.go"


class TestParseVerificationSpacedPaths:
    def test_spaced_path_finding_is_not_swallowed_by_the_previous_one(self):
        text = (
            "## Must fix\n"
            "- [ ] **[M1]** **`pkg/a.go:1`** — First finding\n"
            "- [ ] **[M2]** **`src/my notes.py:2`** — Second finding, spaced path\n"
            "- [ ] **[M3]** **`pkg/c.go:3`** — Third finding\n"
        )
        findings = review_verify._parse_findings_for_verification(text)
        assert [f["id"] for f in findings] == ["M1", "M2", "M3"]
        assert findings[0]["body"] == "First finding"
        assert findings[1]["path"] == "src/my notes.py"

    def test_spaced_path_with_line_range(self):
        text = '- **[S1]** **`src/my notes.py:12-18`** — issue\n'
        findings = review_verify._parse_findings_for_verification(text)
        assert findings[0]["path"] == "src/my notes.py"

    def test_non_ascii_spaced_path(self):
        text = '- [ ] **[M1]** **`src/café brûlé.py:42`** — desc\n'
        findings = review_verify._parse_findings_for_verification(text)
        assert findings[0]["path"] == "src/café brûlé.py"

    def test_spaced_path_in_a_bare_code_span(self):
        text = '- **[N1]** `docs/release notes.md:3` — stale\n'
        findings = review_verify._parse_findings_for_verification(text)
        assert findings[0]["path"] == "docs/release notes.md"

    def test_unchecked_and_plain_forms_both_parse(self):
        text = (
            "- [ ] **[M1]** **`pkg/a.go:1`** — checkbox form\n"
            "- **[M2]** **`pkg/b.go:2`** — plain form\n"
        )
        findings = review_verify._parse_findings_for_verification(text)
        assert [f["id"] for f in findings] == ["M1", "M2"]
        assert [f["body"] for f in findings] == ["checkbox form", "plain form"]

    def test_checked_finding_is_not_returned(self):
        text = '- [x] **[M1]** **`src/my notes.py:2`** — done\n'
        assert review_verify._parse_findings_for_verification(text) == []


class TestStripEvidenceBlocks:
    def test_strips_evidence_preserves_finding(self):
        text = (
            "## Must fix\n"
            "- **[M1]** **`file.go:42`** — missing error check\n"
            "  > ```go\n"
            "  > result := db.Query(q)\n"
            "  > ```\n"
            "## Nit\n"
            "- **[N1]** **`file.go:10`** — rename var\n"
        )
        result = review_verify.strip_evidence_blocks(text)
        assert "```go" not in result
        assert "result := db.Query" not in result
        assert "**[M1]**" in result
        assert "missing error check" in result
        assert "**[N1]**" in result

    def test_no_evidence_blocks_unchanged(self):
        content = "## Must fix\n- **[M1]** **`file.go:42`** — finding\n"
        assert review_verify.strip_evidence_blocks(content) == content

    def test_top_level_blockquote_preserved(self):
        content = (
            "## Summary\n"
            "> ```go\n"
            "> example code\n"
            "> ```\n"
            "## Must fix\n"
            "- **[M1]** **`file.go:42`** — finding\n"
        )
        result = review_verify.strip_evidence_blocks(content)
        assert "> ```go" in result

    def test_strips_unfenced_blockquote_evidence(self):
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
        result = review_verify.strip_evidence_blocks(text)
        assert "Team roster" not in result
        assert "docker run" not in result
        assert "**[S1]**" in result
        assert "stale config section" in result
        assert "**[N1]**" in result


class TestRemoveDroppedFindings:
    def test_remove_single(self):
        text = (
            "## Must fix\n"
            "- **[M1]** **`file.go:1`** — issue one\n"
            "- **[M2]** **`file.go:5`** — issue two\n"
        )
        result = review_verify._remove_dropped_findings(text, ["M1"])
        assert "issue one" not in result
        assert "issue two" in result

    def test_remove_with_continuation(self):
        text = (
            "- **[M1]** **`file.go:1`** — issue one\n"
            "  continuation line\n"
            "  more detail\n"
            "- **[M2]** **`file.go:5`** — issue two\n"
        )
        result = review_verify._remove_dropped_findings(text, ["M1"])
        assert "issue one" not in result
        assert "continuation line" not in result
        assert "issue two" in result

    def test_empty_dropped_list(self):
        text = "- **[M1]** **`file.go:1`** — issue\n"
        assert review_verify._remove_dropped_findings(text, []) == text

    def test_remove_last_finding(self):
        text = (
            "## Must fix\n"
            "- **[M1]** **`file.go:1`** — only finding\n"
        )
        result = review_verify._remove_dropped_findings(text, ["M1"])
        assert "only finding" not in result
        assert "## Must fix" in result


# ── Disprove-it gate ─────────────────────────────────────────────────────────


DISPROVE_OUTPUT = """\
## Disprove Results

- [M1] SURVIVES — confirmed: no nil check before deref at handler.go:42
- [M2] FALSIFIED — the error is handled in the caller at service.go:88
- [S1] SURVIVES — timeout not set, could hang indefinitely
- [S2] FALSIFIED — deprecated API was replaced in the same PR, see diff line 204
"""


class TestParseDisproveOutput:
    def test_parses_all_results(self):
        results = parse_disprove_output(DISPROVE_OUTPUT)
        assert len(results) == 4

    def test_survives_verdict(self):
        results = parse_disprove_output(DISPROVE_OUTPUT)
        assert results[0].finding_id == "M1"
        assert results[0].verdict == "SURVIVES"
        assert "nil check" in results[0].reason

    def test_falsified_verdict(self):
        results = parse_disprove_output(DISPROVE_OUTPUT)
        assert results[1].finding_id == "M2"
        assert results[1].verdict == "FALSIFIED"
        assert "caller" in results[1].reason

    def test_empty_input(self):
        assert parse_disprove_output("") == []

    def test_no_matching_lines(self):
        assert parse_disprove_output("some random text\nno findings here\n") == []

    def test_double_dash_separator(self):
        text = "- [M1] SURVIVES -- reason here\n"
        results = parse_disprove_output(text)
        assert len(results) == 1
        assert results[0].reason == "reason here"


# ── apply_disprove_results ───────────────────────────────────────────────────


REVIEW_TEXT = """\
## Must fix

- [ ] **[M1]** Nil pointer dereference at handler.go:42
  The handler does not check for nil before calling `.Process()`.

  **Evidence:**
  ```go
  func Handle(r *Request) { r.Process() }
  ```

- [ ] **[M2]** Error ignored in database query
  The return value of `db.Query()` is discarded.

## Should fix

- [ ] **[S1]** No timeout on HTTP client
  The default client has no timeout, which could cause hangs.

- [ ] **[S2]** Using deprecated API
  `OldMethod()` is marked deprecated since v2.0.

## Nit

- [ ] **[N1]** Variable naming: `x` should be `count`
"""


class TestApplyDisproveResults:
    def test_removes_falsified_findings(self):
        results = [
            DisproveResult("M2", "FALSIFIED", "handled in caller"),
            DisproveResult("S2", "FALSIFIED", "replaced in same PR"),
        ]
        new_text, stats = apply_disprove_results(REVIEW_TEXT, results)
        assert "**[M1]**" in new_text
        assert "**[M2]**" not in new_text
        assert "**[S1]**" in new_text
        assert "**[S2]**" not in new_text
        assert "**[N1]**" in new_text

    def test_removes_multiline_body(self):
        results = [DisproveResult("M1", "FALSIFIED", "not real")]
        new_text, _ = apply_disprove_results(REVIEW_TEXT, results)
        assert "**[M1]**" not in new_text
        assert "r.Process()" not in new_text
        assert "**[M2]**" in new_text

    def test_all_survive(self):
        results = [
            DisproveResult("M1", "SURVIVES", "confirmed"),
            DisproveResult("M2", "SURVIVES", "confirmed"),
        ]
        new_text, stats = apply_disprove_results(REVIEW_TEXT, results)
        assert new_text == REVIEW_TEXT
        assert stats["survived"] == 2
        assert stats["falsified"] == 0

    def test_all_falsified(self):
        results = [
            DisproveResult("M1", "FALSIFIED", "r1"),
            DisproveResult("M2", "FALSIFIED", "r2"),
            DisproveResult("S1", "FALSIFIED", "r3"),
            DisproveResult("S2", "FALSIFIED", "r4"),
        ]
        new_text, stats = apply_disprove_results(REVIEW_TEXT, results)
        assert "**[M1]**" not in new_text
        assert "**[M2]**" not in new_text
        assert "**[S1]**" not in new_text
        assert "**[S2]**" not in new_text
        assert "**[N1]**" in new_text
        assert stats["falsified"] == 4

    def test_empty_results(self):
        new_text, stats = apply_disprove_results(REVIEW_TEXT, [])
        assert new_text == REVIEW_TEXT
        assert stats["total_challenged"] == 0

    def test_stats_structure(self):
        results = [
            DisproveResult("M1", "SURVIVES", "confirmed"),
            DisproveResult("M2", "FALSIFIED", "not real"),
            DisproveResult("S1", "SURVIVES", "confirmed"),
        ]
        _, stats = apply_disprove_results(REVIEW_TEXT, results)
        assert stats["total_challenged"] == 3
        assert stats["survived"] == 2
        assert stats["falsified"] == 1
        assert "M2" in stats["falsified_ids"]
        assert stats["reasons"]["M2"] == "not real"

    def test_section_headers_preserved(self):
        results = [DisproveResult("M1", "FALSIFIED", "x")]
        new_text, _ = apply_disprove_results(REVIEW_TEXT, results)
        assert "## Must fix" in new_text
        assert "## Should fix" in new_text
        assert "## Nit" in new_text

    def test_finding_without_checkbox(self):
        review = "## Must fix\n\n- **[M1]** Simple finding\n  Detail line.\n"
        results = [DisproveResult("M1", "FALSIFIED", "not real")]
        new_text, stats = apply_disprove_results(review, results)
        assert "**[M1]**" not in new_text
        assert stats["falsified"] == 1
