import json
from unittest.mock import MagicMock, patch

import pytest


class TestExtractPath:
    def test_bold_backtick_with_line_number(self, rp):
        path, line, end = rp._extract_path("**`pkg/handler.go:42`**")
        assert (path, line, end) == ("pkg/handler.go", 42, None)

    def test_bold_backtick_with_line_range(self, rp):
        path, line, end = rp._extract_path("**`pkg/handler.go:10-20`**")
        assert (path, line, end) == ("pkg/handler.go", 10, 20)

    def test_bold_only_format(self, rp):
        path, line, end = rp._extract_path("**pkg/handler.go:5**")
        assert (path, line, end) == ("pkg/handler.go", 5, None)

    def test_backtick_only_format(self, rp):
        path, line, end = rp._extract_path("`pkg/handler.go:99`")
        assert (path, line, end) == ("pkg/handler.go", 99, None)

    def test_no_line_number(self, rp):
        path, line, end = rp._extract_path("**`handler.go`**")
        assert (path, line, end) == ("handler.go", None, None)

    def test_no_path_match_returns_empty(self, rp):
        path, line, end = rp._extract_path("some random text")
        assert (path, line, end) == ("", None, None)

    def test_en_dash_range_separator(self, rp):
        path, line, end = rp._extract_path("**`file.go:10–15`**")
        assert (path, line, end) == ("file.go", 10, 15)


class TestExtractBodyText:
    def test_em_dash_separator(self, rp):
        assert rp._extract_body_text("prefix — the body text") == "the body text"

    def test_double_dash_fallback(self, rp):
        assert rp._extract_body_text("prefix -- the body text") == "the body text"

    def test_hyphen_fallback(self, rp):
        assert rp._extract_body_text("prefix - the body text") == "the body text"

    def test_no_separator_returns_empty(self, rp):
        assert rp._extract_body_text("no separator here") == ""

    def test_em_dash_takes_precedence(self, rp):
        assert rp._extract_body_text("a -- b — c") == "c"


class TestParseFindingLine:
    def test_standard_must_fix(self, rp):
        f = rp._parse_finding_line(
            "- **[M1]** **`handler.go:42`** — Fix this bug"
        )
        assert (f.id, f.severity, f.seq, f.path, f.line, f.body) == (
            "M1", "M", 1, "handler.go", 42, "Fix this bug",
        )

    def test_should_fix_with_line_range(self, rp):
        f = rp._parse_finding_line(
            "- **[S3]** **`pkg/auth.go:10-20`** — Refactor"
        )
        assert (f.id, f.severity, f.seq, f.line, f.end_line) == ("S3", "S", 3, 10, 20)

    def test_nit_finding(self, rp):
        f = rp._parse_finding_line("- **[N2]** **`file.go:5`** — Nit text")
        assert (f.severity, f.seq) == ("N", 2)

    def test_idiom_finding(self, rp):
        f = rp._parse_finding_line("- **[I1]** **`file.go:5`** — Use pattern")
        assert (f.severity, f.seq) == ("I", 1)

    def test_checkbox_variant(self, rp):
        f = rp._parse_finding_line(
            "- [ ] **[S1]** **`file.go:5`** — Fix this"
        )
        assert (f.id, f.body) == ("S1", "Fix this")

    def test_non_finding_returns_none(self, rp):
        assert rp._parse_finding_line("just a regular line") is None

    def test_section_header_returns_none(self, rp):
        assert rp._parse_finding_line("## Must fix") is None


class TestParseFindings:
    def test_single_finding_single_section(self, rp):
        text = "## Must fix\n\n- **[M1]** **`file.go:10`** — Fix the bug\n"
        findings = rp.parse_findings(text)
        assert len(findings) == 1
        assert findings[0].body == "Fix the bug"

    def test_multiple_findings_one_section(self, rp):
        text = (
            "## Must fix\n\n"
            "- **[M1]** **`a.go:1`** — First finding\n"
            "- **[M2]** **`b.go:2`** — Second finding\n"
        )
        findings = rp.parse_findings(text)
        assert len(findings) == 2
        assert (findings[0].id, findings[0].body) == ("M1", "First finding")
        assert (findings[1].id, findings[1].body) == ("M2", "Second finding")

    def test_findings_across_multiple_sections(self, rp):
        text = (
            "## Must fix\n\n- **[M1]** **`a.go:1`** — Must body\n\n"
            "## Should fix\n\n- **[S1]** **`b.go:2`** — Should body\n\n"
            "## Nit\n\n- **[N1]** **`c.go:3`** — Nit body\n"
        )
        findings = rp.parse_findings(text)
        assert len(findings) == 3
        assert [(f.id, f.body) for f in findings] == [
            ("M1", "Must body"),
            ("S1", "Should body"),
            ("N1", "Nit body"),
        ]

    def test_last_finding_in_section_preserves_body(self, rp):
        text = (
            "## Must fix\n\n"
            "- **[M1]** **`a.go:1`** — First must-fix\n"
            "- **[M2]** **`b.go:2`** — Last must-fix body\n\n"
            "## Should fix\n\n"
            "- **[S1]** **`c.go:3`** — First should-fix\n\n"
            "## Nit\n\n"
            "- **[N1]** **`d.go:4`** — The only nit\n\n"
            "## Verdict\n\nAll done.\n"
        )
        findings = rp.parse_findings(text)
        by_id = {f.id: f for f in findings}
        assert by_id["M2"].body == "Last must-fix body"
        assert by_id["S1"].body == "First should-fix"
        assert by_id["N1"].body == "The only nit"
        assert all(f.body for f in findings)

    def test_multi_line_continuation(self, rp):
        text = (
            "## Must fix\n\n"
            "- **[M1]** **`file.go:10`** — Main body text\n"
            "  More details here.\n"
            "  And another line.\n"
        )
        findings = rp.parse_findings(text)
        body = findings[0].body
        assert "Main body text" in body
        assert "More details here." in body
        assert "And another line." in body

    def test_multi_line_with_code_block(self, rp):
        text = (
            "## Must fix\n\n"
            "- **[M1]** **`workflow.yml:10`** — Add permissions block:\n"
            "  ```yaml\n"
            "  permissions:\n"
            "    contents: read\n"
            "  ```\n"
            "  Note about the fix.\n"
        )
        findings = rp.parse_findings(text)
        body = findings[0].body
        assert "Add permissions block:" in body
        assert "permissions:" in body
        assert "contents: read" in body
        assert "Note about the fix." in body

    def test_multi_line_last_in_section_regression(self, rp):
        text = (
            "## Must fix\n\n"
            "- **[M1]** **`a.go:1`** — Simple finding\n\n"
            "- **[M2]** **`workflow.yml:50-60`** — Complex finding.\n"
            "  Code block follows:\n"
            "  ```yaml\n"
            "  jobs:\n"
            "    build:\n"
            "      runs-on: ubuntu\n"
            "  ```\n"
            "  *(Note referencing other comments.)*\n\n"
            "## Should fix\n\n"
            "- **[S1]** **`b.go:5`** — Should fix body\n"
        )
        findings = rp.parse_findings(text)
        m2 = next(f for f in findings if f.id == "M2")
        assert m2.body
        assert "Complex finding" in m2.body

    def test_strikethrough_skipped(self, rp):
        text = (
            "## Must fix\n\n"
            "- ~~**[M1]** **`file.go:10`** — Resolved~~\n"
            "- **[M2]** **`file.go:20`** — Still open\n"
        )
        findings = rp.parse_findings(text)
        assert len(findings) == 1
        assert findings[0].id == "M2"

    def test_sub_headers_handled(self, rp):
        text = (
            "## Must fix\n\n"
            "### Group A\n\n"
            "- **[M1]** **`a.go:1`** — First\n\n"
            "### Group B\n\n"
            "- **[M2]** **`b.go:2`** — Second\n"
        )
        findings = rp.parse_findings(text)
        assert len(findings) == 2
        assert findings[0].body == "First"
        assert findings[1].body == "Second"

    def test_non_severity_section_stops_parsing(self, rp):
        text = (
            "## Must fix\n\n"
            "- **[M1]** **`a.go:1`** — A finding\n\n"
            "## Verdict\n\n"
            "This is not a finding: - **[M2]** **`b.go:2`** — Not parsed\n"
        )
        findings = rp.parse_findings(text)
        assert len(findings) == 1

    def test_empty_input(self, rp):
        assert len(rp.parse_findings("")) == 0

    def test_no_severity_sections(self, rp):
        text = "# Review\n\nSome preamble.\n\n## Summary\n\nNothing here.\n"
        assert len(rp.parse_findings(text)) == 0

    def test_finding_with_no_body(self, rp):
        text = "## Nit\n\n- **[N1]** **`file.go:10`**\n"
        findings = rp.parse_findings(text)
        assert findings[0].body == ""

    def test_case_insensitive_section_headers(self, rp):
        text = (
            "## Must Fix\n\n"
            "- **[M1]** **`file.go:10`** — Bug found\n\n"
            "## SHOULD FIX\n\n"
            "- **[S1]** **`file.go:20`** — Cleanup needed\n\n"
            "## nit\n\n"
            "- **[N1]** **`file.go:30`** — Style issue\n"
        )
        findings = rp.parse_findings(text)
        assert len(findings) == 3
        assert [(f.severity, f.body) for f in findings] == [
            ("M", "Bug found"),
            ("S", "Cleanup needed"),
            ("N", "Style issue"),
        ]


class TestParseDiffHunks:
    def test_single_file_single_hunk(self, rp):
        diff = (
            "diff --git a/file.go b/file.go\n"
            "--- a/file.go\n"
            "+++ b/file.go\n"
            "@@ -10,5 +10,7 @@ func main() {\n"
            "+new line\n"
        )
        hunks = rp.parse_diff_hunks(diff)
        assert "file.go" in hunks
        assert (10, 16) in hunks["file.go"]

    def test_multiple_files(self, rp):
        diff = (
            "diff --git a/a.go b/a.go\n"
            "--- a/a.go\n"
            "+++ b/a.go\n"
            "@@ -1,3 +1,5 @@\n"
            "+line\n"
            "diff --git a/b.go b/b.go\n"
            "--- a/b.go\n"
            "+++ b/b.go\n"
            "@@ -10,2 +10,4 @@\n"
            "+line\n"
        )
        hunks = rp.parse_diff_hunks(diff)
        assert "a.go" in hunks
        assert "b.go" in hunks

    def test_multiple_hunks_per_file(self, rp):
        diff = (
            "diff --git a/file.go b/file.go\n"
            "--- a/file.go\n"
            "+++ b/file.go\n"
            "@@ -1,3 +1,5 @@\n"
            "+line\n"
            "@@ -20,3 +22,5 @@\n"
            "+line\n"
        )
        hunks = rp.parse_diff_hunks(diff)
        assert len(hunks["file.go"]) == 2
        assert (1, 5) in hunks["file.go"]
        assert (22, 26) in hunks["file.go"]

    def test_hunk_with_omitted_count(self, rp):
        diff = (
            "diff --git a/file.go b/file.go\n"
            "--- a/file.go\n"
            "+++ b/file.go\n"
            "@@ -5,3 +5 @@\n"
            "-removed\n"
        )
        hunks = rp.parse_diff_hunks(diff)
        assert (5, 5) in hunks["file.go"]

    def test_new_file(self, rp):
        diff = (
            "diff --git a/new.go b/new.go\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/new.go\n"
            "@@ -0,0 +1,20 @@\n"
            "+package main\n"
        )
        hunks = rp.parse_diff_hunks(diff)
        assert (1, 20) in hunks["new.go"]


class TestResolvePath:
    def test_exact_match(self, rp):
        hunks = {"pkg/handler.go": [(1, 10)]}
        assert rp._resolve_path("pkg/handler.go", hunks) == "pkg/handler.go"

    def test_basename_match_unique(self, rp):
        hunks = {"pkg/handler.go": [(1, 10)]}
        assert rp._resolve_path("handler.go", hunks) == "pkg/handler.go"

    def test_basename_match_ambiguous(self, rp):
        hunks = {"pkg/a/handler.go": [(1, 10)], "pkg/b/handler.go": [(1, 10)]}
        assert rp._resolve_path("handler.go", hunks) is None

    def test_partial_path_match(self, rp):
        hunks = {"src/pkg/service/handler.go": [(1, 10)]}
        assert rp._resolve_path("service/handler.go", hunks) == "src/pkg/service/handler.go"

    def test_no_match(self, rp):
        hunks = {"pkg/handler.go": [(1, 10)]}
        assert rp._resolve_path("other.go", hunks) is None


class TestClassifyFindings:
    DIFF_INLINE = (
        "diff --git a/file.go b/file.go\n"
        "--- a/file.go\n"
        "+++ b/file.go\n"
        "@@ -1,3 +1,10 @@\n"
        "+line\n"
    )

    def test_inline_when_in_hunk(self, rp):
        f = rp.Finding(id="M1", severity="M", seq=1, path="file.go", line=5, end_line=None, body="x")
        inline, fl, skipped = rp.classify_findings([f], self.DIFF_INLINE)
        assert (len(inline), len(fl), len(skipped)) == (1, 0, 0)
        assert inline[0].classification == "inline"

    def test_file_level_when_line_not_in_hunk(self, rp):
        f = rp.Finding(id="M1", severity="M", seq=1, path="file.go", line=50, end_line=None, body="x")
        inline, fl, skipped = rp.classify_findings([f], self.DIFF_INLINE)
        assert (len(inline), len(fl), len(skipped)) == (0, 1, 0)
        assert fl[0].classification == "file_level"
        assert "not in any diff hunk" in fl[0].skip_reason

    def test_file_level_when_no_line_number(self, rp):
        f = rp.Finding(id="M1", severity="M", seq=1, path="file.go", line=None, end_line=None, body="x")
        inline, fl, skipped = rp.classify_findings([f], self.DIFF_INLINE)
        assert (len(inline), len(fl), len(skipped)) == (0, 1, 0)
        assert "no line number" in fl[0].skip_reason

    def test_skipped_when_path_not_in_diff(self, rp):
        f = rp.Finding(id="M1", severity="M", seq=1, path="other.go", line=5, end_line=None, body="x")
        inline, fl, skipped = rp.classify_findings([f], self.DIFF_INLINE)
        assert (len(inline), len(fl), len(skipped)) == (0, 0, 1)
        assert skipped[0].classification == "skipped"

    def test_resolves_bare_filename(self, rp):
        diff = (
            "diff --git a/pkg/handler.go b/pkg/handler.go\n"
            "--- a/pkg/handler.go\n"
            "+++ b/pkg/handler.go\n"
            "@@ -1,3 +1,10 @@\n"
            "+line\n"
        )
        f = rp.Finding(id="M1", severity="M", seq=1, path="handler.go", line=5, end_line=None, body="x")
        inline, _, _ = rp.classify_findings([f], diff)
        assert len(inline) == 1
        assert inline[0].full_path == "pkg/handler.go"

    def test_end_line_snapped_to_hunk_boundary(self, rp):
        """end_line beyond the hunk gets snapped to hunk end."""
        f = rp.Finding(id="M1", severity="M", seq=1, path="file.go", line=5, end_line=50, body="x")
        inline, fl, skipped = rp.classify_findings([f], self.DIFF_INLINE)
        assert (len(inline), len(fl), len(skipped)) == (1, 0, 0)
        assert inline[0].end_line == 10

    def test_end_line_within_hunk_unchanged(self, rp):
        """end_line inside the hunk stays as-is."""
        f = rp.Finding(id="M1", severity="M", seq=1, path="file.go", line=3, end_line=8, body="x")
        inline, fl, skipped = rp.classify_findings([f], self.DIFF_INLINE)
        assert (len(inline), len(fl), len(skipped)) == (1, 0, 0)
        assert inline[0].end_line == 8


class TestRenumberForPosting:
    def test_inline_sorted_by_path_then_line(self, rp):
        f1 = rp.Finding(id="M1", severity="M", seq=1, path="b.go", line=10, end_line=None, body="x", full_path="b.go")
        f2 = rp.Finding(id="M2", severity="M", seq=2, path="a.go", line=5, end_line=None, body="y", full_path="a.go")
        inline, _ = rp.renumber_for_posting([f1, f2], [])
        assert (inline[0].posted_id, inline[0].full_path) == ("M1", "a.go")
        assert (inline[1].posted_id, inline[1].full_path) == ("M2", "b.go")

    def test_body_continues_after_inline(self, rp):
        fi = rp.Finding(id="S1", severity="S", seq=1, path="a.go", line=5, end_line=None, body="x", full_path="a.go")
        fb = rp.Finding(id="S2", severity="S", seq=2, path="b.go", line=None, end_line=None, body="y", full_path="b.go")
        inline, body = rp.renumber_for_posting([fi], [fb])
        assert inline[0].posted_id == "S1"
        assert body[0].posted_id == "S2"

    def test_independent_counters_per_severity(self, rp):
        f1 = rp.Finding(id="M1", severity="M", seq=1, path="a.go", line=1, end_line=None, body="x", full_path="a.go")
        f2 = rp.Finding(id="S1", severity="S", seq=1, path="a.go", line=2, end_line=None, body="y", full_path="a.go")
        f3 = rp.Finding(id="M2", severity="M", seq=2, path="a.go", line=3, end_line=None, body="z", full_path="a.go")
        inline, _ = rp.renumber_for_posting([f1, f2, f3], [])
        assert [f.posted_id for f in inline] == ["M1", "S1", "M2"]

    def test_same_file_sorted_by_line(self, rp):
        f1 = rp.Finding(id="M1", severity="M", seq=1, path="a.go", line=30, end_line=None, body="x", full_path="a.go")
        f2 = rp.Finding(id="M2", severity="M", seq=2, path="a.go", line=5, end_line=None, body="y", full_path="a.go")
        inline, _ = rp.renumber_for_posting([f1, f2], [])
        assert inline[0].line == 5
        assert inline[1].line == 30


class TestFormatInlineComment:
    def test_single_line(self, rp):
        f = rp.Finding(
            id="M1", severity="M", seq=1, path="file.go", line=42,
            end_line=None, body="Fix bug", full_path="pkg/file.go", posted_id="M1",
        )
        c = rp.format_inline_comment(f)
        assert c["path"] == "pkg/file.go"
        assert c["line"] == 42
        assert "**[M1] [must-fix]** Fix bug" in c["body"]
        assert c["side"] == "RIGHT"
        assert "start_line" not in c

    def test_omits_subject_type(self, rp):
        f = rp.Finding(
            id="S1", severity="S", seq=1, path="file.go", line=10,
            end_line=None, body="Fix", full_path="pkg/file.go", posted_id="S1",
        )
        c = rp.format_inline_comment(f)
        assert "subject_type" not in c

    def test_multi_line_range(self, rp):
        f = rp.Finding(
            id="S1", severity="S", seq=1, path="file.go", line=10,
            end_line=20, body="Refactor", full_path="pkg/file.go", posted_id="S1",
        )
        c = rp.format_inline_comment(f)
        assert c["start_line"] == 10
        assert c["line"] == 20
        assert c.get("start_side") == "RIGHT"

    @pytest.mark.parametrize(
        "sev_id,sev,label",
        [("M1", "M", "must-fix"), ("S1", "S", "should-fix"), ("N1", "N", "nit"), ("I1", "I", "idiom")],
    )
    def test_severity_labels(self, rp, sev_id, sev, label):
        f = rp.Finding(
            id=sev_id, severity=sev, seq=1, path="f.go", line=1,
            end_line=None, body="text", full_path="f.go", posted_id=sev_id,
        )
        body = rp.format_inline_comment(f)["body"]
        assert f"[{label}]" in body


class TestFormatBodyText:
    def test_with_inline_shows_have_some_comments(self, rp):
        result = rp.format_body_text([], has_inline=True, severity_filter={"M", "S", "N"})
        assert "Have some comments" in result

    def test_without_inline_shows_review_findings(self, rp):
        result = rp.format_body_text([], has_inline=False, severity_filter={"M", "S", "N"})
        assert "Review findings" in result

    def test_body_findings_listed_with_path_refs(self, rp):
        f = rp.Finding(
            id="M1", severity="M", seq=1, path="handler.go", line=42,
            end_line=None, body="Fix it", full_path="pkg/handler.go", posted_id="M1",
        )
        result = rp.format_body_text([f], has_inline=True, severity_filter={"M"})
        assert "**[M1] [must-fix]**" in result
        assert "`handler.go:42`" in result
        assert "Fix it" in result

    def test_no_body_findings_single_line(self, rp):
        text = rp.format_body_text([], has_inline=True, severity_filter={"M", "S"})
        assert len(text.strip().split("\n")) == 1


class TestFormatPathRef:
    def test_path_with_line(self, rp):
        f = rp.Finding(id="M1", severity="M", seq=1, path="file.go", line=42, end_line=None, body="")
        assert rp._format_path_ref(f) == "`file.go:42`"

    def test_path_with_line_range(self, rp):
        f = rp.Finding(id="M1", severity="M", seq=1, path="file.go", line=10, end_line=20, body="")
        assert rp._format_path_ref(f) == "`file.go:10-20`"

    def test_path_only(self, rp):
        f = rp.Finding(id="M1", severity="M", seq=1, path="file.go", line=None, end_line=None, body="")
        assert rp._format_path_ref(f) == "`file.go`"


class TestEndToEnd:
    def test_real_world_review(self, rp):
        text = (
            "# Review: org/repo#42\n\n"
            "## Must fix\n\n"
            "- **[M1]** **`workflow.yml:10-20`** — Missing permissions block.\n"
            "  ```yaml\n"
            "  permissions:\n"
            "    contents: read\n"
            "  ```\n\n"
            "- **[M2]** **`handler.go:50`** — Race condition in handler.\n\n"
            "## Should fix\n\n"
            "- **[S1]** **`config.sh:30`** — Hardcoded prefix.\n\n"
            "## Nit\n\n"
            "- **[N1]** **`test.sh:5`** — Tests not in CI.\n\n"
            "## Verdict\n\nRequest changes.\n"
        )
        findings = rp.parse_findings(text)
        by_id = {f.id: f for f in findings}
        assert all(f.body for f in findings)
        assert "Missing permissions block." in by_id["M1"].body
        assert "Race condition in handler." in by_id["M2"].body
        assert "Hardcoded prefix." in by_id["S1"].body
        assert "Tests not in CI." in by_id["N1"].body

    def test_every_last_in_section_retains_body(self, rp):
        text = (
            "## Must fix\n\n"
            "- **[M1]** **`a.go:1`** — Must-fix alpha\n"
            "- **[M2]** **`b.go:2`** — Must-fix beta (last in section)\n\n"
            "## Should fix\n\n"
            "- **[S1]** **`c.go:3`** — Should-fix only (last in section)\n\n"
            "## Nit\n\n"
            "- **[N1]** **`d.go:4`** — Nit alpha\n"
            "- **[N2]** **`e.go:5`** — Nit beta (last in section)\n\n"
            "## Verdict\n\nDone.\n"
        )
        findings = rp.parse_findings(text)
        assert all(f.body for f in findings)
        by_id = {f.id: f for f in findings}
        assert by_id["M2"].body == "Must-fix beta (last in section)"
        assert by_id["S1"].body == "Should-fix only (last in section)"
        assert by_id["N2"].body == "Nit beta (last in section)"


class TestWritePostTracking:
    def test_submitted_true(self, rp, tmp_path):
        review = str(tmp_path / "test-review.md")
        rp.write_post_tracking(review, 123, "abc", 5, 2, 1, submitted=True)
        data = json.loads((tmp_path / "test-review.post.jsonl").read_text())
        assert data["submitted"] is True

    def test_submitted_defaults_false(self, rp, tmp_path):
        review = str(tmp_path / "test-review.md")
        rp.write_post_tracking(review, 123, "abc", 5, 2, 1)
        data = json.loads((tmp_path / "test-review.post.jsonl").read_text())
        assert data["submitted"] is False

    def test_single_int_backward_compat(self, rp, tmp_path):
        review = str(tmp_path / "test-review.md")
        rp.write_post_tracking(review, 456, "abc", 3, 1, 0)
        data = json.loads((tmp_path / "test-review.post.jsonl").read_text())
        assert data["review_id"] == 456
        assert data["review_ids"] == [456]
        assert data["chunk_count"] == 1

    def test_list_of_review_ids_with_chunk_count(self, rp, tmp_path):
        review = str(tmp_path / "test-review.md")
        rp.write_post_tracking(review, [100, 200, 300], "abc", 90, 5, 2, submitted=True, chunk_count=3)
        data = json.loads((tmp_path / "test-review.post.jsonl").read_text())
        assert data["review_id"] == 100
        assert data["review_ids"] == [100, 200, 300]
        assert data["chunk_count"] == 3
        assert data["submitted"] is True


class TestFormatSubmitCommand:
    def test_produces_correct_command(self, rp):
        result = rp._format_submit_command("org/repo", "42", 12345)
        assert result == "gh api repos/org/repo/pulls/42/reviews/12345/events --method POST -f event=COMMENT"


class TestChunkComments:
    def test_no_chunking_under_threshold(self, rp):
        comments = [{"body": f"c{i}"} for i in range(10)]
        chunks = rp._chunk_comments(comments, 30)
        assert len(chunks) == 1
        assert len(chunks[0]) == 10

    def test_exact_boundary_not_chunked(self, rp):
        comments = [{"body": f"c{i}"} for i in range(30)]
        chunks = rp._chunk_comments(comments, 30)
        assert len(chunks) == 1
        assert len(chunks[0]) == 30

    def test_splits_above_threshold(self, rp):
        comments = [{"body": f"c{i}"} for i in range(82)]
        chunks = rp._chunk_comments(comments, 30)
        assert len(chunks) == 3
        assert [len(c) for c in chunks] == [30, 30, 22]

    def test_empty_input(self, rp):
        chunks = rp._chunk_comments([], 30)
        assert len(chunks) == 1
        assert len(chunks[0]) == 0


class TestIsRateLimited:
    def test_secondary_rate_limit(self, rp):
        assert rp._is_rate_limited('{"message": "You have exceeded a secondary rate limit"}') is True

    def test_abuse_detection(self, rp):
        assert rp._is_rate_limited('{"message": "abuse detection triggered"}') is True

    def test_retry_later(self, rp):
        assert rp._is_rate_limited('{"message": "Please retry later"}') is True

    def test_normal_error_not_rate_limited(self, rp):
        assert rp._is_rate_limited('{"message": "Not Found"}') is False


class TestGhApi:
    def test_get_request(self, rp):
        mock_result = MagicMock(returncode=0, stdout='{"ok": true}')
        with patch.object(rp.subprocess, "run", return_value=mock_result) as mock_run:
            rc, stdout = rp._gh_api("repos/org/repo/pulls/1")
            mock_run.assert_called_once()
            cmd = mock_run.call_args[0][0]
            assert cmd == ["gh", "api", "repos/org/repo/pulls/1"]
            assert rc == 0

    def test_post_request(self, rp):
        mock_result = MagicMock(returncode=0, stdout="{}")
        with patch.object(rp.subprocess, "run", return_value=mock_result) as mock_run:
            rp._gh_api("repos/org/repo/pulls/1/reviews", method="POST")
            cmd = mock_run.call_args[0][0]
            assert "--method" in cmd
            assert "POST" in cmd

    def test_input_file(self, rp):
        mock_result = MagicMock(returncode=0, stdout="{}")
        with patch.object(rp.subprocess, "run", return_value=mock_result) as mock_run:
            rp._gh_api("endpoint", method="POST", input_file="/tmp/payload.json")
            cmd = mock_run.call_args[0][0]
            assert "--input" in cmd
            assert "/tmp/payload.json" in cmd

    def test_returns_tuple(self, rp):
        mock_result = MagicMock(returncode=1, stdout="error msg")
        with patch.object(rp.subprocess, "run", return_value=mock_result):
            rc, stdout = rp._gh_api("endpoint")
            assert rc == 1
            assert stdout == "error msg"

    def test_headers(self, rp):
        mock_result = MagicMock(returncode=0, stdout="diff text")
        with patch.object(rp.subprocess, "run", return_value=mock_result) as mock_run:
            rp._gh_api("endpoint", headers={"Accept": "application/vnd.github.v3.diff"})
            cmd = mock_run.call_args[0][0]
            assert "--header" in cmd
            idx = cmd.index("--header")
            assert cmd[idx + 1] == "Accept: application/vnd.github.v3.diff"


class TestHandleApiAttempt:
    def test_success_returns_parsed_json(self, rp):
        result = rp._handle_api_attempt(0, 0, '{"id": 42}')
        assert result == {"id": 42}

    def test_invalid_json_returns_none(self, rp):
        result = rp._handle_api_attempt(0, 0, "not json")
        assert result is None

    def test_rate_limited_sleeps_and_returns_none(self, rp):
        with patch.object(rp.time, "sleep") as mock_sleep:
            result = rp._handle_api_attempt(0, 1, '{"message": "secondary rate limit"}')
            assert result is None
            mock_sleep.assert_called_once()
            wait = mock_sleep.call_args[0][0]
            assert wait == rp.RATE_LIMIT_WAIT

    def test_rate_limited_exponential_backoff(self, rp):
        with patch.object(rp.time, "sleep") as mock_sleep:
            rp._handle_api_attempt(2, 1, '{"message": "secondary rate limit"}')
            wait = mock_sleep.call_args[0][0]
            expected = int(min(rp.RATE_LIMIT_WAIT * (rp.RATE_LIMIT_BACKOFF ** 2), rp.RATE_LIMIT_MAX_WAIT))
            assert wait == expected

    def test_non_rate_error_sleeps_if_not_last(self, rp):
        with patch.object(rp.time, "sleep") as mock_sleep:
            result = rp._handle_api_attempt(0, 1, '{"message": "Not Found"}')
            assert result is None
            mock_sleep.assert_called_once_with(rp.NON_RATE_LIMIT_DELAY)

    def test_non_rate_error_no_sleep_on_last_attempt(self, rp):
        with patch.object(rp.time, "sleep") as mock_sleep:
            result = rp._handle_api_attempt(rp.MAX_RETRIES - 1, 1, '{"message": "error"}')
            assert result is None
            mock_sleep.assert_not_called()

    def test_line_resolution_error_takes_priority(self, rp):
        stdout = '{"message": "Unprocessable Entity", "errors": ["Line could not be resolved"]}'
        with pytest.raises(rp.LineResolutionError):
            rp._handle_api_attempt(0, 1, stdout)

    def test_errors_array_logged_for_non_line_errors(self, rp, capsys):
        stdout = '{"message": "Unprocessable Entity", "errors": ["Something else"]}'
        with patch.object(rp.time, "sleep"):
            rp._handle_api_attempt(0, 1, stdout)
        captured = capsys.readouterr()
        assert "Something else" in captured.err


class TestCheckExistingPending:
    def test_returns_review_id(self, rp):
        reviews = json.dumps([{"id": 12345, "state": "PENDING"}])
        with patch.object(rp, "_gh_api", return_value=(0, reviews)):
            assert rp._check_existing_pending("org/repo", "1") == 12345

    def test_returns_none_when_no_pending(self, rp):
        reviews = json.dumps([{"id": 1, "state": "APPROVED"}])
        with patch.object(rp, "_gh_api", return_value=(0, reviews)):
            assert rp._check_existing_pending("org/repo", "1") is None

    def test_returns_none_for_empty_list(self, rp):
        with patch.object(rp, "_gh_api", return_value=(0, "[]")):
            assert rp._check_existing_pending("org/repo", "1") is None

    def test_returns_none_on_api_failure(self, rp):
        with patch.object(rp, "_gh_api", return_value=(1, "")):
            assert rp._check_existing_pending("org/repo", "1") is None


class TestPostWithRetries:
    def test_success_on_first_attempt(self, rp):
        mock_result = MagicMock(returncode=0, stdout='{"id": 42}')
        with patch.object(rp.subprocess, "run", return_value=mock_result):
            result = rp._post_with_retries("repos/org/repo/pulls/1/reviews", "/tmp/payload.json")
            assert result == {"id": 42}

    def test_success_after_retry(self, rp):
        fail = MagicMock(returncode=1, stdout='{"message": "error"}')
        success = MagicMock(returncode=0, stdout='{"id": 99}')
        with (
            patch.object(rp.subprocess, "run", side_effect=[fail, success]),
            patch.object(rp.time, "sleep"),
        ):
            result = rp._post_with_retries("endpoint", "/tmp/p.json")
            assert result == {"id": 99}

    def test_all_attempts_fail_returns_none(self, rp):
        fail = MagicMock(returncode=1, stdout='{"message": "error"}')
        with (
            patch.object(rp.subprocess, "run", return_value=fail),
            patch.object(rp.time, "sleep"),
        ):
            result = rp._post_with_retries("endpoint", "/tmp/p.json")
            assert result is None

    def test_rate_limited_then_success(self, rp):
        rate_limited = MagicMock(returncode=1, stdout='{"message": "secondary rate limit"}')
        success = MagicMock(returncode=0, stdout='{"id": 1}')
        with (
            patch.object(rp.subprocess, "run", side_effect=[rate_limited, success]),
            patch.object(rp.time, "sleep") as mock_sleep,
        ):
            result = rp._post_with_retries("endpoint", "/tmp/p.json")
            assert result == {"id": 1}
            mock_sleep.assert_called_once()


class TestPostReview:
    PAYLOAD = {"body": "test", "commit_id": "abc123"}

    def test_no_existing_pending(self, rp):
        with (
            patch.object(rp, "_check_existing_pending", return_value=None),
            patch.object(rp, "_gh_api", return_value=(0, '{"id": 42}')),
        ):
            result = rp.post_review("org/repo", "1", self.PAYLOAD)
            assert result == {"id": 42}

    def test_deletes_existing_pending(self, rp):
        with (
            patch.object(rp, "_check_existing_pending", return_value=999),
            patch.object(rp, "_gh_api", return_value=(0, '{"id": 42}')) as mock_api,
        ):
            result = rp.post_review("org/repo", "1", self.PAYLOAD)
            assert result == {"id": 42}
            delete_calls = [c for c in mock_api.call_args_list if c.kwargs.get("method") == "DELETE"
                            or (len(c.args) > 1 and c.args[1] == "DELETE")]
            assert len(delete_calls) == 1

    def test_with_submit(self, rp):
        dumped_payloads = []
        orig_dump = rp.json.dump

        def capture_dump(obj, fp, **kw):
            dumped_payloads.append(obj)
            return orig_dump(obj, fp, **kw)

        with (
            patch.object(rp, "_check_existing_pending", return_value=None),
            patch.object(rp, "_gh_api", return_value=(0, '{"id": 42}')),
            patch.object(rp.json, "dump", side_effect=capture_dump),
        ):
            result = rp.post_review("org/repo", "1", self.PAYLOAD, submit=True)
            assert result == {"id": 42}
            assert any(p.get("event") == "COMMENT" for p in dumped_payloads)


class TestSubmitReview:
    def test_success(self, rp):
        mock_result = MagicMock(returncode=0, stdout='{"ok": true}')
        with patch.object(rp.subprocess, "run", return_value=mock_result) as mock_run:
            assert rp._submit_review("org/repo", "1", 42) is True
            cmd = mock_run.call_args[0][0]
            assert "repos/org/repo/pulls/1/reviews/42/events" in " ".join(cmd)

    def test_failure_warns(self, rp, capsys):
        fail = MagicMock(returncode=1, stdout='{"message": "bad request"}')
        with (
            patch.object(rp.subprocess, "run", return_value=fail),
            patch.object(rp.time, "sleep"),
        ):
            assert rp._submit_review("org/repo", "1", 42) is False
        captured = capsys.readouterr()
        assert "Failed to submit" in captured.err


class TestFetchPrMetadata:
    def test_success(self, rp):
        pr_json = json.dumps({
            "head": {"sha": "abc123", "ref": "feat/branch"},
            "base": {"ref": "main"},
        })
        with patch.object(rp, "_gh_api", return_value=(0, pr_json)):
            meta = rp._fetch_pr_metadata("org/repo", "1")
            assert meta["head_sha"] == "abc123"
            assert meta["head_ref"] == "feat/branch"
            assert meta["base_ref"] == "main"

    def test_failure_exits(self, rp):
        with (
            patch.object(rp, "_gh_api", return_value=(1, "")),
            pytest.raises(SystemExit),
        ):
            rp._fetch_pr_metadata("org/repo", "1")


class TestGetDiff:
    def test_success(self, rp):
        with patch.object(rp, "_gh_api", return_value=(0, "diff --git a/f b/f\n")):
            assert rp._get_diff("org/repo", "1") == "diff --git a/f b/f\n"

    def test_failure_exits(self, rp):
        with (
            patch.object(rp, "_gh_api", return_value=(1, "")),
            pytest.raises(SystemExit),
        ):
            rp._get_diff("org/repo", "1")
