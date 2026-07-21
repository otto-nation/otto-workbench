import json
import re
import subprocess
import sys
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

    def test_parenthesized_route_group(self, rp):
        path, line, end = rp._extract_path(
            "**`ui-consumer/src/app/(authenticated)/rewards/page.tsx:186`**"
        )
        assert (path, line) == ("ui-consumer/src/app/(authenticated)/rewards/page.tsx", 186)

    def test_nested_parenthesized_groups(self, rp):
        path, line, end = rp._extract_path(
            "**`app/(auth)/(dashboard)/page.tsx`**"
        )
        assert path == "app/(auth)/(dashboard)/page.tsx"


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

    def test_pathless_finding_uses_full_text_as_body(self, rp):
        f = rp._parse_finding_line(
            '- **[I1]** The `_comment` field convention is good practice and should be retained.'
        )
        assert f.path == ""
        assert "The `_comment` field convention" in f.body
        assert "should be retained" in f.body

    def test_pathless_finding_with_em_dash_keeps_full_text(self, rp):
        f = rp._parse_finding_line(
            '- **[I1]** Some pattern across files — this is good practice.'
        )
        assert f.path == ""
        assert "Some pattern across files" in f.body
        assert "good practice" in f.body

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

    def test_stray_text_between_sections_does_not_corrupt_previous_finding(self, rp):
        text = (
            "## Should fix\n"
            "- **[S1]** **`a.go:10`** — Real should-fix body\n\n"
            "## Nit\n"
            "_None in this file group._\n"
            "- **[N1]** **`b.go:20`** — Real nit body\n"
        )
        findings = rp.parse_findings(text)
        assert len(findings) == 2
        s1 = next(f for f in findings if f.id == "S1")
        n1 = next(f for f in findings if f.id == "N1")
        assert s1.body == "Real should-fix body"
        assert n1.body == "Real nit body"

    def test_empty_section_markers_do_not_corrupt_adjacent_findings(self, rp):
        text = (
            "## Must fix\n"
            "_None in this file group._\n"
            "## Should fix\n"
            "- **[S1]** **`a.go:1`** — Should body\n"
            "- **[S2]** **`b.go:2`** — Last should body\n\n"
            "## Nit\n"
            "_None in this file group._\n"
            "- **[N1]** **`c.go:3`** — First nit\n\n"
            "## Idioms\n"
            "_None in this file group._\n"
            "- **[I1]** **`d.go:4`** — First idiom\n"
        )
        findings = rp.parse_findings(text)
        assert len(findings) == 4
        by_id = {f.id: f for f in findings}
        assert by_id["S1"].body == "Should body"
        assert by_id["S2"].body == "Last should body"
        assert by_id["N1"].body == "First nit"
        assert by_id["I1"].body == "First idiom"


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

    def test_body_only_severity_forced_to_file_level(self, rp):
        """Nit findings are routed to body even when their line is in a diff hunk."""
        f = rp.Finding(id="N1", severity="N", seq=1, path="file.go", line=5, end_line=None, body="style issue")
        inline, fl, skipped = rp.classify_findings([f], self.DIFF_INLINE)
        assert (len(inline), len(fl), len(skipped)) == (0, 1, 0)
        assert fl[0].classification == "file_level"
        assert "body-only" in fl[0].skip_reason

    def test_body_only_severity_resolves_full_path(self, rp):
        """Body-only findings still get full_path resolved for by_file grouping."""
        diff = (
            "diff --git a/pkg/handler.go b/pkg/handler.go\n"
            "--- a/pkg/handler.go\n"
            "+++ b/pkg/handler.go\n"
            "@@ -1,3 +1,10 @@\n"
            "+line\n"
        )
        f = rp.Finding(id="I1", severity="I", seq=1, path="handler.go", line=5, end_line=None, body="use pattern")
        inline, fl, skipped = rp.classify_findings([f], diff)
        assert len(fl) == 1
        assert fl[0].full_path == "pkg/handler.go"

    def test_idiom_with_no_path_still_file_level(self, rp):
        """Body-only findings without a path go to file_level with general finding reason."""
        f = rp.Finding(id="I1", severity="I", seq=1, path="", line=None, end_line=None, body="good pattern")
        inline, fl, skipped = rp.classify_findings([f], self.DIFF_INLINE)
        assert len(fl) == 1
        assert "general finding" in fl[0].skip_reason

    def test_inline_severity_still_inline(self, rp):
        """Must-fix findings with lines in diff are still inline."""
        f = rp.Finding(id="M1", severity="M", seq=1, path="file.go", line=5, end_line=None, body="bug")
        inline, fl, skipped = rp.classify_findings([f], self.DIFF_INLINE)
        assert len(inline) == 1


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

    def test_pathless_finding_omits_backticks(self, rp):
        f = rp.Finding(
            id="I1", severity="I", seq=1, path="", line=None,
            end_line=None, body="Good pattern across files.", posted_id="I1",
        )
        result = rp.format_body_text([f], has_inline=True, severity_filter={"I"})
        assert "``" not in result
        assert "**[I1]** Good pattern" in result
        assert "<details open>" in result

    def test_mixed_severities_grouped_with_headers(self, rp):
        findings = [
            rp.Finding(id="S1", severity="S", seq=1, path="a.go", line=10,
                       end_line=None, body="Should fix", posted_id="S1"),
            rp.Finding(id="N1", severity="N", seq=1, path="b.go", line=20,
                       end_line=None, body="Nit issue", posted_id="N1"),
            rp.Finding(id="S2", severity="S", seq=2, path="c.go", line=30,
                       end_line=None, body="Another should", posted_id="S2"),
        ]
        result = rp.format_body_text(findings, has_inline=True, severity_filter={"S", "N"})
        assert "### Should fix" in result
        assert "<summary>Nit (1)</summary>" in result
        s_idx = result.index("### Should fix")
        n_idx = result.index("<details open>")
        assert s_idx < n_idx
        assert result.index("S1") < result.index("N1")
        assert result.index("S2") < result.index("N1")

    def test_single_severity_nits_in_details(self, rp):
        findings = [
            rp.Finding(id="N1", severity="N", seq=1, path="a.go", line=10,
                       end_line=None, body="Nit one", posted_id="N1"),
            rp.Finding(id="N2", severity="N", seq=2, path="b.go", line=20,
                       end_line=None, body="Nit two", posted_id="N2"),
        ]
        result = rp.format_body_text(findings, has_inline=True, severity_filter={"N"})
        assert "<details open>" in result
        assert "<summary>Nit (2)</summary>" in result
        assert "</details>" in result
        assert "N1" in result
        assert "N2" in result

    def test_summary_and_verdict_prepended(self, rp):
        result = rp.format_body_text(
            [], has_inline=True, severity_filter={"M"},
            summary="Clean refactor.", verdict="Approve.",
        )
        assert result.startswith("## Summary")
        assert "Clean refactor." in result
        assert "### Verdict" in result
        assert "Approve." in result
        assert "---" in result
        assert result.index("## Summary") < result.index("---")
        assert result.index("---") < result.index("Have some comments")

    def test_verdict_action_prefix_stripped(self, rp):
        result = rp.format_body_text(
            [], has_inline=True, severity_filter={"M"},
            summary="Summary text.",
            verdict="Request changes — M1 and M2 are blockers.",
        )
        assert "### Verdict" in result
        assert "M1 and M2 are blockers." in result
        assert "Request changes" not in result

    def test_verdict_approve_prefix_stripped(self, rp):
        result = rp.format_body_text(
            [], has_inline=True, severity_filter={"M"},
            summary="Summary text.",
            verdict="Approve — clean code.",
        )
        assert "### Verdict" in result
        assert "clean code." in result
        assert "Approve" not in result

    def test_verdict_action_prefix_stripped_plain_hyphen(self, rp):
        result = rp.format_body_text(
            [], has_inline=True, severity_filter={"M"},
            summary="Summary text.",
            verdict="Needs discussion - looks good.",
        )
        assert "### Verdict" in result
        assert "looks good." in result
        assert "Needs discussion" not in result

    def test_verdict_bold_action_prefix_stripped(self, rp):
        result = rp.format_body_text(
            [], has_inline=True, severity_filter={"M"},
            summary="Summary text.",
            verdict="**Request changes** — M1 and M2 are blockers.",
        )
        assert "### Verdict" in result
        assert "M1 and M2 are blockers." in result
        assert "Request changes" not in result

    def test_verdict_without_action_prefix_unchanged(self, rp):
        result = rp.format_body_text(
            [], has_inline=True, severity_filter={"M"},
            summary="Summary text.", verdict="Looks good overall.",
        )
        assert "### Verdict" in result
        assert "Looks good overall." in result

    def test_summary_without_verdict(self, rp):
        result = rp.format_body_text(
            [], has_inline=True, severity_filter={"M"},
            summary="Clean refactor.",
        )
        assert result.startswith("## Summary")
        assert "Clean refactor." in result
        assert "### Verdict" not in result
        assert "---" in result
        assert result.index("## Summary") < result.index("---")

    def test_empty_summary_omitted(self, rp):
        result = rp.format_body_text(
            [], has_inline=True, severity_filter={"M"},
            summary="", verdict="",
        )
        assert "## Summary" not in result
        assert "### Verdict" not in result
        assert "---" not in result

    def test_outside_diff_header_only_for_demoted_findings(self, rp):
        findings = [
            rp.Finding(id="S1", severity="S", seq=1, path="a.go", line=10,
                       end_line=None, body="Should fix", posted_id="S1"),
        ]
        result = rp.format_body_text(findings, has_inline=True, severity_filter={"S"})
        assert "**Findings outside the diff:**" in result
        assert "Findings not in the diff" not in result

    def test_no_outside_diff_header_for_nits_only(self, rp):
        findings = [
            rp.Finding(id="N1", severity="N", seq=1, path="a.go", line=10,
                       end_line=None, body="Nit issue", posted_id="N1"),
        ]
        result = rp.format_body_text(findings, has_inline=True, severity_filter={"N"})
        assert "Findings outside the diff" not in result
        assert "<details open>" in result

    def test_nits_drop_severity_label(self, rp):
        f = rp.Finding(
            id="N1", severity="N", seq=1, path="a.go", line=10,
            end_line=None, body="Style issue", posted_id="N1",
        )
        result = rp.format_body_text([f], has_inline=True, severity_filter={"N"})
        assert "**[N1]**" in result
        assert "[nit]" not in result


class TestFormatBodyTextDetails:
    def test_nits_sorted_by_path_then_line(self, rp):
        findings = [
            rp.Finding(id="N1", severity="N", seq=1, path="b.go", line=20,
                       end_line=None, body="Naming issue", posted_id="N1"),
            rp.Finding(id="N2", severity="N", seq=2, path="a.go", line=30,
                       end_line=None, body="Another style", posted_id="N2"),
            rp.Finding(id="N3", severity="N", seq=3, path="a.go", line=10,
                       end_line=None, body="Style issue", posted_id="N3"),
        ]
        result = rp.format_body_text(findings, has_inline=True, severity_filter={"N"})
        assert "<summary>Nit (3)</summary>" in result
        assert result.index("Style issue") < result.index("Another style")
        assert result.index("Another style") < result.index("Naming issue")

    def test_nits_within_file_sorted_by_line(self, rp):
        findings = [
            rp.Finding(id="N1", severity="N", seq=1, path="a.go", line=30,
                       end_line=None, body="Later", posted_id="N1"),
            rp.Finding(id="N2", severity="N", seq=2, path="a.go", line=10,
                       end_line=None, body="Earlier", posted_id="N2"),
        ]
        result = rp.format_body_text(findings, has_inline=True, severity_filter={"N"})
        assert result.index("Earlier") < result.index("Later")

    def test_mixed_severity_body_shows_severity_then_details(self, rp):
        findings = [
            rp.Finding(id="S1", severity="S", seq=1, path="a.go", line=10,
                       end_line=None, body="Should fix this", posted_id="S1"),
            rp.Finding(id="N1", severity="N", seq=1, path="b.go", line=20,
                       end_line=None, body="Nit issue", posted_id="N1"),
        ]
        result = rp.format_body_text(findings, has_inline=True, severity_filter={"S", "N"})
        assert "### Should fix" in result
        assert "<summary>Nit (1)</summary>" in result
        assert result.index("### Should fix") < result.index("<details open>")

    def test_nits_and_idioms_in_separate_details(self, rp):
        findings = [
            rp.Finding(id="N1", severity="N", seq=1, path="a.go", line=10,
                       end_line=None, body="Nit", posted_id="N1"),
            rp.Finding(id="I1", severity="I", seq=1, path="a.go", line=20,
                       end_line=None, body="Idiom", posted_id="I1"),
        ]
        result = rp.format_body_text(findings, has_inline=True, severity_filter={"N", "I"})
        assert "<summary>Nit (1)</summary>" in result
        assert "<summary>Idioms (1)</summary>" in result
        assert "Nit" in result
        assert "Idiom" in result

    def test_pathless_body_only_finding_in_details(self, rp):
        findings = [
            rp.Finding(id="N1", severity="N", seq=1, path="a.go", line=10,
                       end_line=None, body="File nit", posted_id="N1"),
            rp.Finding(id="I1", severity="I", seq=1, path="", line=None,
                       end_line=None, body="General pattern", posted_id="I1"),
        ]
        result = rp.format_body_text(findings, has_inline=True, severity_filter={"N", "I"})
        assert "<summary>Nit (1)</summary>" in result
        assert "<summary>Idioms (1)</summary>" in result
        nit_idx = result.index("<summary>Nit")
        idiom_idx = result.index("<summary>Idioms")
        assert nit_idx < idiom_idx


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
    def _review_dir(self, tmp_path):
        """Create and return the folder-layout review directory."""
        d = tmp_path / "test-review"
        d.mkdir()
        return d

    def test_submitted_true(self, rp, tmp_path):
        d = self._review_dir(tmp_path)
        review = str(d / "review.md")
        rp.write_post_tracking(review, 123, "abc", 5, 2, 1, submitted=True)
        data = json.loads((d / "post.jsonl").read_text())
        assert data["submitted"] is True

    def test_submitted_defaults_false(self, rp, tmp_path):
        d = self._review_dir(tmp_path)
        review = str(d / "review.md")
        rp.write_post_tracking(review, 123, "abc", 5, 2, 1)
        data = json.loads((d / "post.jsonl").read_text())
        assert data["submitted"] is False

    def test_single_int_backward_compat(self, rp, tmp_path):
        d = self._review_dir(tmp_path)
        review = str(d / "review.md")
        rp.write_post_tracking(review, 456, "abc", 3, 1, 0)
        data = json.loads((d / "post.jsonl").read_text())
        assert data["review_id"] == 456
        assert data["review_ids"] == [456]
        assert data["chunk_count"] == 1

    def test_list_of_review_ids_with_chunk_count(self, rp, tmp_path):
        d = self._review_dir(tmp_path)
        review = str(d / "review.md")
        rp.write_post_tracking(review, [100, 200, 300], "abc", 90, 5, 2, submitted=True, chunk_count=3)
        data = json.loads((d / "post.jsonl").read_text())
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
        with patch("review_github._gh_api", return_value=(0, reviews)):
            assert rp._check_existing_pending("org/repo", "1") == 12345

    def test_returns_none_when_no_pending(self, rp):
        reviews = json.dumps([{"id": 1, "state": "APPROVED"}])
        with patch("review_github._gh_api", return_value=(0, reviews)):
            assert rp._check_existing_pending("org/repo", "1") is None

    def test_returns_none_for_empty_list(self, rp):
        with patch("review_github._gh_api", return_value=(0, "[]")):
            assert rp._check_existing_pending("org/repo", "1") is None

    def test_returns_none_on_api_failure(self, rp):
        with patch("review_github._gh_api", return_value=(1, "")):
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
            patch("review_github._check_existing_pending", return_value=None),
            patch("review_github._gh_api", return_value=(0, '{"id": 42}')),
        ):
            result = rp.post_review("org/repo", "1", self.PAYLOAD)
            assert result == {"id": 42}

    def test_deletes_existing_pending(self, rp):
        with (
            patch("review_github._check_existing_pending", return_value=999),
            patch("review_github._gh_api", return_value=(0, '{"id": 42}')) as mock_api,
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
            patch("review_github._check_existing_pending", return_value=None),
            patch("review_github._gh_api", return_value=(0, '{"id": 42}')),
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
        with patch("review_github._gh_api", return_value=(0, pr_json)):
            meta = rp._fetch_pr_metadata("org/repo", "1")
            assert meta["head_sha"] == "abc123"
            assert meta["head_ref"] == "feat/branch"
            assert meta["base_ref"] == "main"

    def test_failure_exits(self, rp):
        with (
            patch("review_github._gh_api", return_value=(1, "")),
            pytest.raises(SystemExit),
        ):
            rp._fetch_pr_metadata("org/repo", "1")


class TestGetDiff:
    def test_success(self, rp):
        with patch("review_github._gh_api", return_value=(0, "diff --git a/f b/f\n")):
            assert rp._get_diff("org/repo", "1") == "diff --git a/f b/f\n"

    def test_failure_returns_empty(self, rp):
        with patch("review_github._gh_api", return_value=(1, "")):
            assert rp._get_diff("org/repo", "1") == ""


class TestExtractPathEscapedUnderscores:
    def test_escaped_underscores_in_dunder(self, rp):
        path, line, end = rp._extract_path(r"**`scripts/\_\_main\_\_.py:10`**")
        assert path == "scripts/__main__.py"
        assert line == 10

    def test_escaped_underscores_in_bold(self, rp):
        path, line, end = rp._extract_path(r"**scripts/\_\_main\_\_.py:10**")
        assert path == "scripts/__main__.py"
        assert line == 10

    def test_no_escapes_unchanged(self, rp):
        path, line, end = rp._extract_path("**`scripts/__main__.py:10`**")
        assert path == "scripts/__main__.py"
        assert line == 10

    def test_escaped_underscore_in_middle(self, rp):
        path, line, end = rp._extract_path(r"**`some\_file.py:5`**")
        assert path == "some_file.py"
        assert line == 5


class TestParseFindingsCodeBlockIndentation:
    def test_code_block_indentation_preserved(self, rp):
        text = (
            "## Must fix\n\n"
            "- **[M1]** **`file.py:10`** — Add this code:\n"
            "  ```python\n"
            "  def foo():\n"
            "      return bar\n"
            "  ```\n"
        )
        findings = rp.parse_findings(text)
        body = findings[0].body
        assert "    return bar" in body or "      return bar" in body

    def test_nested_indentation_preserved(self, rp):
        text = (
            "## Should fix\n\n"
            "- **[S1]** **`config.yml:5`** — Use this structure:\n"
            "  ```yaml\n"
            "  jobs:\n"
            "    build:\n"
            "      runs-on: ubuntu\n"
            "  ```\n"
        )
        findings = rp.parse_findings(text)
        body = findings[0].body
        lines = body.split("\n")
        yaml_lines = [l for l in lines if "runs-on" in l]
        assert yaml_lines
        assert yaml_lines[0] != yaml_lines[0].lstrip()


class TestClassifyFindingsEmptyPath:
    DIFF = (
        "diff --git a/file.go b/file.go\n"
        "--- a/file.go\n"
        "+++ b/file.go\n"
        "@@ -1,3 +1,10 @@\n"
        "+line\n"
    )

    def test_empty_path_classified_as_file_level(self, rp):
        f = rp.Finding(id="I1", severity="I", seq=1, path="", line=None, end_line=None, body="Good pattern")
        inline, fl, skipped = rp.classify_findings([f], self.DIFF)
        assert (len(inline), len(fl), len(skipped)) == (0, 1, 0)
        assert fl[0].classification == "file_level"
        assert "general finding" in fl[0].skip_reason

    def test_empty_path_no_warning(self, rp, capsys):
        f = rp.Finding(id="I1", severity="I", seq=1, path="", line=None, end_line=None, body="Good pattern")
        rp.classify_findings([f], self.DIFF)
        captured = capsys.readouterr()
        assert "empty path" not in captured.err

    def test_non_empty_path_not_affected(self, rp):
        f = rp.Finding(id="M1", severity="M", seq=1, path="file.go", line=5, end_line=None, body="x")
        inline, fl, skipped = rp.classify_findings([f], self.DIFF)
        assert len(inline) == 1


class TestWordSet:
    def test_extracts_lowercase_words(self, rp):
        assert rp._word_set("Hello World_Foo 123") == {"hello", "world_foo", "123"}

    def test_empty_string(self, rp):
        assert rp._word_set("") == set()

    def test_strips_punctuation(self, rp):
        assert rp._word_set("error — missing `check`") == {"error", "missing", "check"}


class TestJaccard:
    def test_identical_sets(self, rp):
        assert rp._jaccard({"a", "b"}, {"a", "b"}) == 1.0

    def test_disjoint_sets(self, rp):
        assert rp._jaccard({"a"}, {"b"}) == 0.0

    def test_partial_overlap(self, rp):
        assert rp._jaccard({"a", "b", "c"}, {"b", "c", "d"}) == pytest.approx(0.5)

    def test_both_empty(self, rp):
        assert rp._jaccard(set(), set()) == 1.0

    def test_one_empty(self, rp):
        assert rp._jaccard({"a"}, set()) == 0.0


class TestDedupAgainstPosted:
    def _make_finding(self, rp, id_str, path, body):
        return rp.Finding(
            id=id_str, severity=id_str[0], seq=int(id_str[1:]),
            path=path, line=42, end_line=None, body=body,
        )

    @patch("review_dedup._fetch_bot_comments")
    def test_skips_duplicate(self, mock_fetch, rp):
        mock_fetch.return_value = [
            {"path": "handler.go", "body": "missing error check on db.Query result"},
        ]
        f = self._make_finding(rp, "M1", "handler.go", "missing error check on db.Query result")
        kept, deduped = rp.dedup_against_posted([f], "owner/repo", "123")
        assert len(kept) == 0
        assert len(deduped) == 1
        assert deduped[0].skip_reason == "duplicate of existing comment"

    @patch("review_dedup._fetch_bot_comments")
    def test_keeps_non_duplicate(self, mock_fetch, rp):
        mock_fetch.return_value = [
            {"path": "handler.go", "body": "missing error check on db.Query result"},
        ]
        f = self._make_finding(rp, "S1", "handler.go", "unused import os")
        kept, deduped = rp.dedup_against_posted([f], "owner/repo", "123")
        assert len(kept) == 1
        assert len(deduped) == 0

    @patch("review_dedup._fetch_bot_comments")
    def test_different_file_not_duplicate(self, mock_fetch, rp):
        mock_fetch.return_value = [
            {"path": "handler.go", "body": "missing error check"},
        ]
        f = self._make_finding(rp, "M1", "other.go", "missing error check")
        kept, deduped = rp.dedup_against_posted([f], "owner/repo", "123")
        assert len(kept) == 1

    @patch("review_dedup._fetch_bot_comments")
    def test_no_existing_comments_keeps_all(self, mock_fetch, rp):
        mock_fetch.return_value = []
        f = self._make_finding(rp, "M1", "handler.go", "finding text")
        kept, deduped = rp.dedup_against_posted([f], "owner/repo", "123")
        assert len(kept) == 1
        assert len(deduped) == 0


class TestIsSectionBoundary:
    def test_sub_header_is_boundary(self, rp):
        assert rp._is_section_boundary("### Group A") is True

    def test_strikethrough_finding_is_boundary(self, rp):
        assert rp._is_section_boundary("- ~~**[M1]** **`file.go:10`** — Resolved~~") is True

    def test_non_strikethrough_finding_is_not_boundary(self, rp):
        assert rp._is_section_boundary("- **[M1]** **`file.go:10`** — Open") is False

    def test_regular_text_is_not_boundary(self, rp):
        assert rp._is_section_boundary("Some regular text here") is False

    def test_h2_header_is_not_boundary(self, rp):
        assert rp._is_section_boundary("## Must fix") is False


class TestMatchSeverityHeader:
    def test_h2_must_fix(self, rp):
        assert rp._match_severity_header("## Must fix") == "M"

    def test_h3_should_fix_hyphenated(self, rp):
        assert rp._match_severity_header("### Should-fix") == "S"

    def test_h3_nit(self, rp):
        assert rp._match_severity_header("### Nit") == "N"

    def test_h2_nits_plural(self, rp):
        assert rp._match_severity_header("## Nits") == "N"

    def test_h4_idioms(self, rp):
        assert rp._match_severity_header("#### Idioms") == "I"

    def test_case_insensitive(self, rp):
        assert rp._match_severity_header("## SHOULD FIX") == "S"

    def test_non_severity_header_returns_none(self, rp):
        assert rp._match_severity_header("## Summary") is None

    def test_non_header_returns_none(self, rp):
        assert rp._match_severity_header("Some text") is None

    def test_findings_parent_returns_none(self, rp):
        assert rp._match_severity_header("## Findings") is None


class TestParseFindingsH3Headers:
    def test_h3_severity_under_findings_parent(self, rp):
        text = (
            "## Findings\n\n"
            "### Should-fix\n\n"
            "- **[S1]** **`file.go:10`** — Issue found\n\n"
            "### Nit\n\n"
            "- **[N1]** **`file.go:20`** — Style issue\n"
        )
        findings = rp.parse_findings(text)
        assert len(findings) == 2
        assert findings[0].severity == "S"
        assert findings[1].severity == "N"

    def test_finding_with_optional_bold_close(self, rp):
        line = '- [ ] **[S1] `has_go` summary line dropped** — The step summary lost the output'
        f = rp._parse_finding_line(line)
        assert f is not None
        assert f.severity == "S"
        assert f.seq == 1


class TestFinalizeFinding:
    def test_non_empty_body_lines(self, rp):
        f = rp.Finding(id="M1", severity="M", seq=1, path="file.go", line=10, end_line=None, body="original")
        rp._finalize_finding(f, ["first line", "second line"])
        assert "first line" in f.body
        assert "second line" in f.body

    def test_empty_body_lines_leaves_body_unchanged(self, rp):
        f = rp.Finding(id="M1", severity="M", seq=1, path="file.go", line=10, end_line=None, body="original")
        rp._finalize_finding(f, [])
        assert f.body == "original"

    def test_trailing_sub_header_stripped(self, rp):
        f = rp.Finding(id="M1", severity="M", seq=1, path="file.go", line=10, end_line=None, body="original")
        rp._finalize_finding(f, ["actual content", "### Group B"])
        assert "### Group B" not in f.body
        assert "actual content" in f.body


class TestIsLineResolutionError:
    def test_matching_text(self, rp):
        assert rp._is_line_resolution_error("Line could not be resolved to a position") is True

    def test_non_matching_text(self, rp):
        assert rp._is_line_resolution_error("Something else went wrong") is False

    def test_case_insensitive(self, rp):
        assert rp._is_line_resolution_error("LINE COULD NOT BE RESOLVED") is True


class TestHunkEnd:
    def test_line_inside_hunk(self, rp):
        hunks = [(10, 20), (30, 40)]
        assert rp._hunk_end(15, hunks) == 20

    def test_line_outside_all_hunks(self, rp):
        hunks = [(10, 20), (30, 40)]
        assert rp._hunk_end(25, hunks) is None

    def test_line_at_hunk_start(self, rp):
        hunks = [(10, 20)]
        assert rp._hunk_end(10, hunks) == 20

    def test_line_at_hunk_end(self, rp):
        hunks = [(10, 20)]
        assert rp._hunk_end(20, hunks) == 20

    def test_multiple_hunks_returns_correct_end(self, rp):
        hunks = [(1, 5), (10, 15), (20, 25)]
        assert rp._hunk_end(12, hunks) == 15
        assert rp._hunk_end(3, hunks) == 5
        assert rp._hunk_end(22, hunks) == 25


class TestExtractBodyFindings:
    def test_standard_finding_in_body(self, rp):
        body = "- **[M1]** **`handler.go:42`** — Fix the bug"
        results = rp._extract_body_findings(body)
        assert len(results) == 1
        assert results[0]["path"] == "handler.go"
        assert results[0]["body"] == "Fix the bug"

    def test_multiple_findings(self, rp):
        body = (
            "- **[M1]** **`a.go:10`** — First issue\n"
            "- **[S1]** **`b.go:20`** — Second issue\n"
        )
        results = rp._extract_body_findings(body)
        assert len(results) == 2
        assert results[0]["path"] == "a.go"
        assert results[1]["path"] == "b.go"

    def test_no_findings(self, rp):
        body = "Just some regular text with no findings."
        results = rp._extract_body_findings(body)
        assert len(results) == 0

    def test_path_extraction_with_line_number_suffix(self, rp):
        body = "- **[M1]** **`handler.go:42`** — Fix bug"
        results = rp._extract_body_findings(body)
        assert results[0]["path"] == "handler.go"


class TestFormatFindingLine:
    def test_with_path_and_line(self, rp):
        f = rp.Finding(
            id="M1", severity="M", seq=1, path="file.go", line=42,
            end_line=None, body="Fix bug", posted_id="M1",
        )
        result = rp._format_finding_line(f)
        assert "**[M1] [must-fix]**" in result
        assert "`file.go:42`" in result
        assert "Fix bug" in result
        assert result.startswith("- ")

    def test_with_path_only_no_line(self, rp):
        f = rp.Finding(
            id="S1", severity="S", seq=1, path="file.go", line=None,
            end_line=None, body="Refactor", posted_id="S1",
        )
        result = rp._format_finding_line(f)
        assert "`file.go`" in result
        assert "Refactor" in result

    def test_pathless_finding(self, rp):
        f = rp.Finding(
            id="I1", severity="I", seq=1, path="", line=None,
            end_line=None, body="Good pattern across files.", posted_id="I1",
        )
        result = rp._format_finding_line(f)
        assert "**[I1] [idiom]**" in result
        assert "Good pattern across files." in result
        assert "`" not in result.split("**")[-1]


class TestBuildPermalink:
    def test_with_line_number_only(self, rp):
        m = rp._PERMALINK_REF_RE.search("see file.go:42")
        result = rp._build_permalink("owner/repo", "abc123", m)
        assert "https://github.com/owner/repo/blob/abc123/file.go#L42" in result
        assert "`file.go:42`" in result

    def test_with_line_range(self, rp):
        m = rp._PERMALINK_REF_RE.search("see file.go:10-20")
        result = rp._build_permalink("owner/repo", "def456", m)
        assert "https://github.com/owner/repo/blob/def456/file.go#L10-L20" in result
        assert "`file.go:10-20`" in result

    def test_url_format_correctness(self, rp):
        m = rp._PERMALINK_REF_RE.search("at pkg/handler.go:5")
        result = rp._build_permalink("org/repo", "sha123", m)
        assert result.startswith("[")
        assert "](https://github.com/org/repo/blob/sha123/pkg/handler.go#L5)" in result


class TestResolvePermalinks:
    DIFF = (
        "diff --git a/pkg/handler.go b/pkg/handler.go\n"
        "--- a/pkg/handler.go\n"
        "+++ b/pkg/handler.go\n"
        "@@ -1,3 +1,10 @@\n"
        "+line\n"
    )

    def test_reference_to_file_in_diff_uses_head_ref(self, rp):
        f = rp.Finding(
            id="M1", severity="M", seq=1, path="handler.go", line=5,
            end_line=None, body="see pkg/handler.go:42",
        )
        rp.resolve_permalinks([f], "org/repo", self.DIFF, "head-sha", "base-sha")
        assert "head-sha" in f.body
        assert "base-sha" not in f.body

    def test_reference_to_file_not_in_diff_uses_base_ref(self, rp):
        f = rp.Finding(
            id="M1", severity="M", seq=1, path="handler.go", line=5,
            end_line=None, body="see other.go:10",
        )
        rp.resolve_permalinks([f], "org/repo", self.DIFF, "head-sha", "base-sha")
        assert "base-sha" in f.body
        assert "head-sha" not in f.body

    def test_empty_refs_no_transformation(self, rp):
        f = rp.Finding(
            id="M1", severity="M", seq=1, path="handler.go", line=5,
            end_line=None, body="see other.go:10",
        )
        rp.resolve_permalinks([f], "org/repo", self.DIFF, "", "")
        assert f.body == "see other.go:10"

    def test_no_matching_references(self, rp):
        f = rp.Finding(
            id="M1", severity="M", seq=1, path="handler.go", line=5,
            end_line=None, body="just plain text with no refs",
        )
        rp.resolve_permalinks([f], "org/repo", self.DIFF, "head-sha", "base-sha")
        assert f.body == "just plain text with no refs"


class TestClosePrevious:
    def test_with_findings_and_body_lines(self, rp):
        f = rp.Finding(id="M1", severity="M", seq=1, path="file.go", line=10, end_line=None, body="original")
        body_lines = ["updated body content", "more content"]
        rp._close_previous([f], body_lines)
        assert "updated body content" in f.body
        assert len(body_lines) == 0

    def test_empty_findings_is_noop(self, rp):
        body_lines = ["some text"]
        rp._close_previous([], body_lines)
        assert len(body_lines) == 0

    def test_body_lines_cleared_after_call(self, rp):
        f = rp.Finding(id="M1", severity="M", seq=1, path="file.go", line=10, end_line=None, body="x")
        body_lines = ["line1", "line2"]
        rp._close_previous([f], body_lines)
        assert body_lines == []


class TestParseFindingsEdgeCases:
    def test_finding_with_stable_id_comment(self, rp):
        text = (
            "## Must fix\n\n"
            "- **[M1]** <!-- sid:abc12345 --> **`file.go:10`** — body text\n"
        )
        findings = rp.parse_findings(text)
        assert len(findings) == 1
        assert findings[0].id == "M1"
        assert findings[0].path == "file.go"
        assert findings[0].line == 10

    def test_idioms_section(self, rp):
        text = (
            "## Idioms\n\n"
            "- **[I1]** **`config.go:5`** — Good pattern\n"
        )
        findings = rp.parse_findings(text)
        assert len(findings) == 1
        assert findings[0].severity == "I"
        assert findings[0].body == "Good pattern"


class TestClassifyFindingsEdgeCases:
    DIFF = (
        "diff --git a/file.go b/file.go\n"
        "--- a/file.go\n"
        "+++ b/file.go\n"
        "@@ -1,3 +1,10 @@\n"
        "+line\n"
    )

    def test_end_line_equals_line_single_line(self, rp):
        f = rp.Finding(id="M1", severity="M", seq=1, path="file.go", line=5, end_line=5, body="x")
        inline, fl, skipped = rp.classify_findings([f], self.DIFF)
        assert len(inline) == 1
        comment = rp.format_inline_comment(
            rp.Finding(
                id="M1", severity="M", seq=1, path="file.go", line=5,
                end_line=5, body="x", full_path="file.go", posted_id="M1",
            )
        )
        assert "start_line" not in comment
        assert comment["line"] == 5


class TestRenumberForPostingEdgeCases:
    def test_empty_inline_and_empty_body(self, rp):
        inline, body = rp.renumber_for_posting([], [])
        assert inline == []
        assert body == []

    def test_only_body_findings(self, rp):
        fb1 = rp.Finding(id="S1", severity="S", seq=1, path="a.go", line=None, end_line=None, body="x", full_path="a.go")
        fb2 = rp.Finding(id="N1", severity="N", seq=1, path="b.go", line=None, end_line=None, body="y", full_path="b.go")
        inline, body = rp.renumber_for_posting([], [fb1, fb2])
        assert len(inline) == 0
        assert len(body) == 2
        assert body[0].posted_id == "S1"
        assert body[1].posted_id == "N1"


class TestDedupAgainstPostedEdgeCases:
    def _make_finding(self, rp, id_str, path, body):
        return rp.Finding(
            id=id_str, severity=id_str[0], seq=int(id_str[1:]),
            path=path, line=42, end_line=None, body=body,
        )

    @patch("review_dedup._fetch_bot_comments")
    def test_jaccard_at_threshold_boundary(self, mock_fetch, rp):
        # Build words so Jaccard is exactly 0.6: 3 shared out of 5 total
        # a = {"a", "b", "c"}, b = {"a", "b", "c", "d", "e"} => 3/5 = 0.6
        mock_fetch.return_value = [
            {"path": "file.go", "body": "a b c d e"},
        ]
        f = self._make_finding(rp, "M1", "file.go", "a b c")
        kept, deduped = rp.dedup_against_posted([f], "owner/repo", "123")
        assert len(deduped) == 1
        assert deduped[0].skip_reason == "duplicate of existing comment"

    @patch("review_dedup._fetch_bot_comments")
    def test_empty_path_on_both_sides_not_matched(self, mock_fetch, rp):
        mock_fetch.return_value = [
            {"path": "", "body": "missing error check"},
        ]
        f = self._make_finding(rp, "M1", "", "missing error check")
        # Override to set path="" since _make_finding sets path to the arg
        f.path = ""
        kept, deduped = rp.dedup_against_posted([f], "owner/repo", "123")
        assert len(kept) == 1
        assert len(deduped) == 0


class TestHeadShaRegex:
    def test_standard_sha(self, rp):
        text = "<!-- head_sha: abc123def456 -->"
        m = rp.HEAD_SHA_RE.search(text)
        assert m is not None
        assert m.group(1) == "abc123def456"

    def test_uppercase_hex_does_not_match(self, rp):
        text = "<!-- head_sha: ABC123DEF456 -->"
        m = rp.HEAD_SHA_RE.search(text)
        assert m is None

    def test_short_sha_matches(self, rp):
        text = "<!-- head_sha: abc1234 -->"
        m = rp.HEAD_SHA_RE.search(text)
        assert m is not None
        assert m.group(1) == "abc1234"


class TestFindingIdRegexEdgeCases:
    def test_with_stable_id_comment(self, rp):
        line = "- **[M1]** <!-- sid:abc12345 --> **`file.go:10`** — body"
        m = rp.FINDING_ID_RE.match(line)
        assert m is not None
        assert m.group(2) == "M"
        assert m.group(3) == "1"

    def test_checkbox_and_strikethrough_combined(self, rp):
        line = "- [ ] ~~**[S1]** **`file.go:5`** — Fix~~"
        m = rp.FINDING_ID_RE.match(line)
        assert m is not None
        assert m.group(1) == " "
        assert m.group(2) == "S"
        assert m.group(3) == "1"

    def test_double_digit_seq(self, rp):
        line = "- **[M12]** **`file.go:10`** — body"
        m = rp.FINDING_ID_RE.match(line)
        assert m is not None
        assert m.group(2) == "M"
        assert m.group(3) == "12"

    def test_checked_checkbox(self, rp):
        line = "- [x] **[M1]** **`file.go:10`** — body"
        m = rp.FINDING_ID_RE.match(line)
        assert m is not None
        assert m.group(1) == "x"
        assert m.group(2) == "M"
        assert m.group(3) == "1"

    def test_no_checkbox(self, rp):
        line = "- **[M1]** **`file.go:10`** — body"
        m = rp.FINDING_ID_RE.match(line)
        assert m is not None
        assert m.group(1) is None
        assert m.group(2) == "M"


class TestFirstFileRegexEdgeCases:
    def test_hidden_file_not_matched_at_start(self, rp):
        # Leading dot is not in the regex character class — hidden files
        # are only matched when preceded by a directory component
        m = rp.FIRST_FILE_RE.match(".gitignore")
        assert m is None

    def test_hidden_file_in_directory(self, rp):
        m = rp.FIRST_FILE_RE.match("config/.gitignore")
        assert m is not None
        assert m.group(1) == "config/.gitignore"

    def test_file_with_dots_in_path(self, rp):
        m = rp.FIRST_FILE_RE.match("v1.2.3/file.go")
        assert m is not None
        assert m.group(1) == "v1.2.3/file.go"

    def test_path_with_parentheses(self, rp):
        m = rp.FIRST_FILE_RE.match("(auth)/page.tsx")
        assert m is not None
        assert m.group(1) == "(auth)/page.tsx"


class TestReclassifyAndRetry:
    DIFF_OLD = (
        "diff --git a/file.go b/file.go\n"
        "--- a/file.go\n"
        "+++ b/file.go\n"
        "@@ -1,3 +1,10 @@\n"
        "+line\n"
    )
    DIFF_NEW = (
        "diff --git a/file.go b/file.go\n"
        "--- a/file.go\n"
        "+++ b/file.go\n"
        "@@ -40,3 +40,10 @@\n"
        "+line\n"
    )

    def _make_args(self, repo="org/repo", pr="1"):
        import argparse
        args = argparse.Namespace()
        args.repo = repo
        args.pr = pr
        args.review_file = "/tmp/test-review.md"
        args.submit = False
        return args

    def test_reclassify_recovers_inline_with_fresh_diff(self, rp):
        f = rp.Finding(
            id="M1", severity="M", seq=1, path="file.go", line=45,
            end_line=None, body="Fix", full_path="file.go",
            classification="inline",
        )
        body_f = rp.Finding(
            id="N1", severity="N", seq=1, path="file.go", line=None,
            end_line=None, body="Nit", full_path="file.go",
            classification="file_level", skip_reason="no line number",
        )

        with (
            patch("review_github._get_diff", return_value=self.DIFF_NEW),
            patch("review_posting._post_chunked_review", return_value=[{"id": 42}]),
            patch("review_github._check_existing_pending", return_value=None),
        ):
            inline_comments, inline, body, body_text, results = rp._reclassify_and_retry(
                self._make_args(), [f], [body_f],
                "abc123", 30, {"M", "N"}, False,
            )
            assert len(inline) == 1
            assert inline[0].posted_id == "M1"
            assert results == [{"id": 42}]

    def test_reclassify_demotes_all_when_retry_also_fails(self, rp):
        f = rp.Finding(
            id="M1", severity="M", seq=1, path="file.go", line=45,
            end_line=None, body="Fix", full_path="file.go",
            classification="inline",
        )

        call_count = [0]
        def failing_then_succeeding(*a, **kw):
            call_count[0] += 1
            if call_count[0] == 1:
                raise rp.LineResolutionError("still broken")
            return [{"id": 99}]

        with (
            patch("review_github._get_diff", return_value=self.DIFF_NEW),
            patch("review_posting._post_chunked_review", side_effect=failing_then_succeeding),
            patch("review_github._check_existing_pending", return_value=None),
        ):
            inline_comments, inline, body, body_text, results = rp._reclassify_and_retry(
                self._make_args(), [f], [],
                "abc123", 30, {"M"}, False,
            )
            assert len(inline_comments) == 0
            assert len(inline) == 0
            assert results == [{"id": 99}]

    def test_reclassify_preserves_skipped_findings_in_body(self, rp):
        inline_f = rp.Finding(
            id="M1", severity="M", seq=1, path="file.go", line=5,
            end_line=None, body="Fix", full_path="file.go",
            classification="inline",
        )
        skipped_f = rp.Finding(
            id="I1", severity="I", seq=1, path="", line=None,
            end_line=None, body="Good pattern",
            classification="skipped", skip_reason="general finding",
        )

        with (
            patch("review_github._get_diff", return_value=self.DIFF_NEW),
            patch("review_posting._post_chunked_review", return_value=[{"id": 42}]),
            patch("review_github._check_existing_pending", return_value=None),
        ):
            _, _, body, _, _ = rp._reclassify_and_retry(
                self._make_args(), [inline_f], [skipped_f],
                "abc123", 30, {"M", "I"}, False,
            )
            assert any(f.body == "Good pattern" for f in body)


class TestFormatCommentBody:
    def test_includes_sha_drift_header(self, rp):
        f = rp.Finding(
            id="M1", severity="M", seq=1, path="file.go", line=10,
            end_line=None, body="Fix this", posted_id="M1",
        )
        body = rp._format_comment_body([f], {"M"}, "aaa1111", "bbb2222", 3)
        assert "aaa1111" in body
        assert "bbb2222" in body
        assert "3 new commits" in body
        assert "Fix this" in body

    def test_single_commit_no_plural(self, rp):
        f = rp.Finding(
            id="S1", severity="S", seq=1, path="a.go", line=1,
            end_line=None, body="body", posted_id="S1",
        )
        body = rp._format_comment_body([f], {"S"}, "aaa", "bbb", 1)
        assert "1 new commit)" in body
        assert "commits" not in body

    def test_renumbers_findings(self, rp):
        findings = [
            rp.Finding(id="M1", severity="M", seq=1, path="a.go", line=1,
                       end_line=None, body="first"),
            rp.Finding(id="M2", severity="M", seq=2, path="b.go", line=2,
                       end_line=None, body="second"),
        ]
        body = rp._format_comment_body(findings, {"M"}, "aaa", "bbb", 1)
        assert "[M1]" in body
        assert "[M2]" in body


class TestHandleChunkFailure:
    def test_exits_with_error(self, rp):
        with pytest.raises(SystemExit):
            rp._handle_chunk_failure(2, 3, [])

    def test_logs_partial_post(self, rp, capsys):
        with pytest.raises(SystemExit):
            rp._handle_chunk_failure(2, 3, [{"id": 100}])
        err = capsys.readouterr().err
        assert "Partial post" in err
        assert "100" in err


class TestCountNewCommits:
    def test_finds_review_sha_and_counts_after(self, rp):
        commits = [
            {"sha": "aaa111"},
            {"sha": "bbb222"},
            {"sha": "ccc333"},
        ]
        with patch("review_github._gh_api", return_value=(0, json.dumps(commits))):
            assert rp._count_new_commits("org/repo", "1", "bbb222") == 1

    def test_no_match_returns_total(self, rp):
        commits = [{"sha": "aaa"}, {"sha": "bbb"}]
        with patch("review_github._gh_api", return_value=(0, json.dumps(commits))):
            assert rp._count_new_commits("org/repo", "1", "zzz") == 2

    def test_api_failure_returns_zero(self, rp):
        with patch("review_github._gh_api", return_value=(1, "")):
            assert rp._count_new_commits("org/repo", "1", "aaa") == 0

    def test_prefix_match(self, rp):
        commits = [{"sha": "aabbccdd1234"}, {"sha": "eeff5678"}]
        with patch("review_github._gh_api", return_value=(0, json.dumps(commits))):
            assert rp._count_new_commits("org/repo", "1", "aabbccdd") == 1


class TestCollectInlineComments:
    def test_filters_by_bot_user(self, rp):
        comments = [
            {"path": "a.go", "body": "fix", "user": {"login": "bot"}},
            {"path": "b.go", "body": "nit", "user": {"login": "human"}},
            {"path": "c.go", "body": "issue", "user": {"login": "bot"}},
        ]
        with patch("review_github._fetch_json_list", return_value=comments):
            result = rp._collect_inline_comments("org/repo", "1", "bot")
            assert len(result) == 2
            assert all(r["path"] in ("a.go", "c.go") for r in result)

    def test_empty_comments(self, rp):
        with patch("review_github._fetch_json_list", return_value=[]):
            result = rp._collect_inline_comments("org/repo", "1", "bot")
            assert result == []


class TestCollectReviewFindings:
    def test_extracts_from_bot_review_bodies(self, rp):
        reviews = [
            {"body": "- **[M1]** **`a.go:1`** — issue one", "user": {"login": "bot"}},
            {"body": "no findings", "user": {"login": "human"}},
        ]
        with patch("review_github._fetch_json_list", return_value=reviews):
            result = rp._collect_review_findings("org/repo", "1", "bot")
            assert len(result) == 1
            assert result[0]["path"] == "a.go"

    def test_skips_empty_bodies(self, rp):
        reviews = [
            {"body": "", "user": {"login": "bot"}},
        ]
        with patch("review_github._fetch_json_list", return_value=reviews):
            result = rp._collect_review_findings("org/repo", "1", "bot")
            assert result == []


class TestFetchBotComments:
    def test_combines_inline_and_review_findings(self, rp):
        with (
            patch("review_github._gh_api", return_value=(0, '{"login": "bot"}')),
            patch("review_github._fetch_json_list", side_effect=[
                [{"path": "a.go", "body": "inline", "user": {"login": "bot"}}],
                [{"body": "- **[M1]** **`b.go:1`** — review", "user": {"login": "bot"}}],
            ]),
        ):
            result = rp._fetch_bot_comments("org/repo", "1")
            assert len(result) == 2

    def test_api_failure_returns_empty(self, rp):
        with patch("review_github._gh_api", return_value=(1, "")):
            assert rp._fetch_bot_comments("org/repo", "1") == []

    def test_empty_login_returns_empty(self, rp):
        with patch("review_github._gh_api", return_value=(0, '{"login": ""}')):
            assert rp._fetch_bot_comments("org/repo", "1") == []


class TestDryRunIntegration:
    """Integration tests exercising the review-post --dry-run code path via subprocess."""

    from pathlib import Path as _Path
    _REPO_ROOT = _Path(__file__).resolve().parent.parent
    _REVIEW_POST = _REPO_ROOT / "ai" / "claude" / "bin" / "review-post"

    REVIEW_MD = (
        "# Review: test-org/test-repo#42 — Fix handler\n"
        "<!-- head_sha: abc123def456 -->\n"
        "\n"
        "## File Triage\n"
        "- `handler.go` — **Tier 2** (application logic)\n"
        "\n"
        "## Must fix\n"
        "\n"
        "- **[M1]** **`handler.go:11`** — missing error check\n"
        "\n"
        "## Should fix\n"
        "\n"
        "- **[S1]** **`handler.go:25`** — unclear variable name\n"
        "\n"
        "## Nit\n"
        "\n"
        "- **[N1]** **`config.sh:2`** — trailing whitespace\n"
        "\n"
        "## Verdict\n"
        "\n"
        "Request changes.\n"
    )

    DIFF_TEXT = (
        "diff --git a/handler.go b/handler.go\n"
        "--- a/handler.go\n"
        "+++ b/handler.go\n"
        "@@ -10,3 +10,5 @@ func main() {\n"
        "     existing()\n"
        "+    added()\n"
        "+    alsoAdded()\n"
        "     kept()\n"
        "diff --git a/config.sh b/config.sh\n"
        "--- a/config.sh\n"
        "+++ b/config.sh\n"
        "@@ -1,3 +1,4 @@\n"
        " #!/bin/bash\n"
        "+set -e\n"
        " echo hello\n"
    )

    def _setup_review(self, tmp_path):
        """Write the review markdown and sidecar meta.json into tmp_path."""
        review_dir = tmp_path / "test-review"
        review_dir.mkdir()
        review_file = review_dir / "review.md"
        review_file.write_text(self.REVIEW_MD)
        meta = {"repo": "test/repo", "head_sha": "abc123def456", "diff": self.DIFF_TEXT}
        (review_dir / "meta.json").write_text(json.dumps(meta))
        return review_file

    def _run_dry_run(self, review_file, extra_args=None):
        """Run review-post --dry-run and return the CompletedProcess."""
        cmd = [
            sys.executable, str(self._REVIEW_POST),
            "--pr", "42",
            "--review-file", str(review_file),
            "--dry-run",
        ]
        if extra_args:
            cmd.extend(extra_args)
        import os
        env = {**os.environ, "NO_COLOR": "1", "TERM": "dumb"}
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=30, env=env,
        )

    @staticmethod
    def _extract_json(stdout):
        """Extract the JSON object from stdout which may contain log lines."""
        # The JSON payload is printed between blank lines. Use greedy match
        # to find the outermost { ... } — lazy .*? would stop at the first
        # } at column 0, truncating nested objects.
        match = re.search(r"^\{.*^\}", stdout, re.MULTILINE | re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise ValueError(f"No JSON payload found in stdout: {stdout!r}")

    def test_dry_run_exits_0(self, tmp_path):
        review_file = self._setup_review(tmp_path)
        result = self._run_dry_run(review_file)
        assert result.returncode == 0, f"stderr: {result.stderr}"

    def test_dry_run_outputs_json_payload(self, tmp_path):
        review_file = self._setup_review(tmp_path)
        result = self._run_dry_run(review_file)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        payload = self._extract_json(result.stdout)
        assert "body" in payload or "comments" in payload

    def test_dry_run_with_severity_filter(self, tmp_path):
        review_file = self._setup_review(tmp_path)
        result = self._run_dry_run(review_file, extra_args=["--severity", "M"])
        assert result.returncode == 0, f"stderr: {result.stderr}"
        payload = self._extract_json(result.stdout)
        # With only M severity, the body/comments should reference M1 but not S1 or N1
        payload_text = json.dumps(payload)
        assert "M1" in payload_text
        assert "S1" not in payload_text
        assert "N1" not in payload_text

    def test_dry_run_with_missing_review_file_exits_nonzero(self, tmp_path):
        nonexistent = tmp_path / "does-not-exist.md"
        result = self._run_dry_run(nonexistent)
        assert result.returncode != 0

    def test_dry_run_without_repo_in_meta_exits_nonzero(self, tmp_path):
        review_dir = tmp_path / "no-repo-meta"
        review_dir.mkdir()
        review_file = review_dir / "review.md"
        review_file.write_text(self.REVIEW_MD)
        # meta.json present but missing the 'repo' field
        (review_dir / "meta.json").write_text(json.dumps({"head_sha": "abc123"}))
        result = self._run_dry_run(review_file)
        assert result.returncode != 0
        assert "repo" in result.stderr.lower() or "meta" in result.stderr.lower()


class TestCheckReviewAlreadyPosted:
    def test_no_reviews_returns_empty(self, rp):
        assert rp.check_review_already_posted([], "some body") == []

    def test_match_returns_ids(self, rp):
        bot_reviews = [
            {"id": 42, "body": "some body text here", "state": "COMMENTED"},
        ]
        assert rp.check_review_already_posted(bot_reviews, "some body text here") == [42]

    def test_zero_similarity_not_matched(self, rp):
        bot_reviews = [
            {"id": 42, "body": "completely different content", "state": "COMMENTED"},
        ]
        assert rp.check_review_already_posted(bot_reviews, "unrelated words here") == []

    def test_partial_overlap_below_threshold(self, rp):
        shared = "alpha bravo charlie delta echo foxtrot golf hotel"
        different = "india juliet kilo lima mike november oscar papa quebec romeo sierra tango"
        bot_reviews = [
            {"id": 42, "body": f"{shared} {different}", "state": "COMMENTED"},
        ]
        assert rp.check_review_already_posted(bot_reviews, f"{shared} unique words not in review") == []


class TestFetchBotReviews:
    def test_returns_bot_reviews(self, rp, monkeypatch):
        monkeypatch.setattr(rp.review_github, "_gh_api", lambda *a, **k: (0, '{"login":"bot"}'))
        monkeypatch.setattr(rp.review_github, "_fetch_json_list", lambda *a: [
            {"id": 1, "user": {"login": "bot"}, "state": "COMMENTED", "body": "review text"},
            {"id": 2, "user": {"login": "human"}, "state": "COMMENTED", "body": "human review"},
            {"id": 3, "user": {"login": "bot"}, "state": "PENDING", "body": "pending"},
        ])
        result = rp.fetch_bot_reviews("org/repo", "1")
        assert len(result) == 1
        assert result[0]["id"] == 1

    def test_ignores_pending(self, rp, monkeypatch):
        monkeypatch.setattr(rp.review_github, "_gh_api", lambda *a, **k: (0, '{"login":"bot"}'))
        monkeypatch.setattr(rp.review_github, "_fetch_json_list", lambda *a: [
            {"id": 42, "body": "some body text here", "state": "PENDING", "user": {"login": "bot"}},
        ])
        assert rp.fetch_bot_reviews("org/repo", "1") == []

    def test_ignores_dismissed(self, rp, monkeypatch):
        monkeypatch.setattr(rp.review_github, "_gh_api", lambda *a, **k: (0, '{"login":"bot"}'))
        monkeypatch.setattr(rp.review_github, "_fetch_json_list", lambda *a: [
            {"id": 42, "body": "some body text here", "state": "DISMISSED", "user": {"login": "bot"}},
        ])
        assert rp.fetch_bot_reviews("org/repo", "1") == []

    def test_ignores_other_users(self, rp, monkeypatch):
        monkeypatch.setattr(rp.review_github, "_gh_api", lambda *a, **k: (0, '{"login":"bot"}'))
        monkeypatch.setattr(rp.review_github, "_fetch_json_list", lambda *a: [
            {"id": 42, "body": "some body text here", "state": "COMMENTED", "user": {"login": "alice"}},
        ])
        assert rp.fetch_bot_reviews("org/repo", "1") == []

    def test_api_failure_returns_empty(self, rp, monkeypatch):
        monkeypatch.setattr(rp.review_github, "_gh_api", lambda *a, **k: (1, ""))
        assert rp.fetch_bot_reviews("org/repo", "1") == []
