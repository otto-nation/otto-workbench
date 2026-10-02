"""Tests for the findings `ReviewDocument` reads out of a review's text — every
reader takes them from `ReviewDocument.parse(text).findings`.
"""

import sys
from pathlib import Path

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)
from review.document import ReviewDocument
from review.types import Finding


def _findings(text: str) -> list[Finding]:
    """The findings a review's text declares, read the way every reader does."""
    return ReviewDocument.parse(text).findings


class TestFindings:
    def test_single_finding_single_section(self):
        findings = _findings("## Must fix\n\n- **[M1]** **`file.go:10`** — Fix the bug\n")
        assert len(findings) == 1
        assert findings[0].body == "Fix the bug"

    def test_multiple_findings_one_section(self):
        findings = _findings(
            "## Must fix\n\n"
            "- **[M1]** **`a.go:1`** — First finding\n"
            "- **[M2]** **`b.go:2`** — Second finding\n"
        )
        assert [(f.id, f.body) for f in findings] == [
            ("M1", "First finding"), ("M2", "Second finding"),
        ]

    def test_findings_across_multiple_sections(self):
        findings = _findings(
            "## Must fix\n\n- **[M1]** **`a.go:1`** — Must body\n\n"
            "## Should fix\n\n- **[S1]** **`b.go:2`** — Should body\n\n"
            "## Nit\n\n- **[N1]** **`c.go:3`** — Nit body\n"
        )
        assert [(f.id, f.body) for f in findings] == [
            ("M1", "Must body"), ("S1", "Should body"), ("N1", "Nit body"),
        ]

    def test_last_finding_in_section_preserves_body(self):
        findings = _findings(
            "## Must fix\n\n"
            "- **[M1]** **`a.go:1`** — First must-fix\n"
            "- **[M2]** **`b.go:2`** — Last must-fix body\n\n"
            "## Should fix\n\n"
            "- **[S1]** **`c.go:3`** — First should-fix\n\n"
            "## Nit\n\n"
            "- **[N1]** **`d.go:4`** — The only nit\n\n"
            "## Verdict\n\nAll done.\n"
        )
        by_id = {f.id: f for f in findings}
        assert by_id["M2"].body == "Last must-fix body"
        assert by_id["S1"].body == "First should-fix"
        assert by_id["N1"].body == "The only nit"
        assert all(f.body for f in findings)

    def test_multi_line_continuation(self):
        findings = _findings(
            "## Must fix\n\n"
            "- **[M1]** **`file.go:10`** — Main body text\n"
            "  More details here.\n"
            "  And another line.\n"
        )
        body = findings[0].body
        assert "Main body text" in body
        assert "More details here." in body
        assert "And another line." in body

    def test_multi_line_with_code_block(self):
        findings = _findings(
            "## Must fix\n\n"
            "- **[M1]** **`workflow.yml:10`** — Add permissions block:\n"
            "  ```yaml\n"
            "  permissions:\n"
            "    contents: read\n"
            "  ```\n"
            "  Note about the fix.\n"
        )
        body = findings[0].body
        assert "Add permissions block:" in body
        assert "permissions:" in body
        assert "contents: read" in body
        assert "Note about the fix." in body

    def test_multi_line_last_in_section_regression(self):
        findings = _findings(
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
        m2 = next(f for f in findings if f.id == "M2")
        assert "Complex finding" in m2.body

    def test_code_block_indentation_preserved(self):
        findings = _findings(
            "## Must fix\n\n"
            "- **[M1]** **`file.py:10`** — Add this code:\n"
            "  ```python\n"
            "  def foo():\n"
            "      return bar\n"
            "  ```\n"
        )
        body = findings[0].body
        assert "    return bar" in body or "      return bar" in body

    def test_nested_indentation_preserved(self):
        findings = _findings(
            "## Should fix\n\n"
            "- **[S1]** **`config.yml:5`** — Use this structure:\n"
            "  ```yaml\n"
            "  jobs:\n"
            "    build:\n"
            "      runs-on: ubuntu\n"
            "  ```\n"
        )
        yaml_lines = [l for l in findings[0].body.split("\n") if "runs-on" in l]
        assert yaml_lines
        assert yaml_lines[0] != yaml_lines[0].lstrip()

    def test_strikethrough_skipped(self):
        findings = _findings(
            "## Must fix\n\n"
            "- ~~**[M1]** **`file.go:10`** — Resolved~~\n"
            "- **[M2]** **`file.go:20`** — Still open\n"
        )
        assert [f.id for f in findings] == ["M2"]

    def test_sub_headers_handled(self):
        findings = _findings(
            "## Must fix\n\n"
            "### Group A\n\n"
            "- **[M1]** **`a.go:1`** — First\n\n"
            "### Group B\n\n"
            "- **[M2]** **`b.go:2`** — Second\n"
        )
        assert [f.body for f in findings] == ["First", "Second"]

    def test_h3_severity_under_findings_parent(self):
        findings = _findings(
            "## Findings\n\n"
            "### Should-fix\n\n"
            "- **[S1]** **`file.go:10`** — Issue found\n\n"
            "### Nit\n\n"
            "- **[N1]** **`file.go:20`** — Style issue\n"
        )
        assert [f.severity for f in findings] == ["S", "N"]

    def test_non_severity_section_stops_parsing(self):
        findings = _findings(
            "## Must fix\n\n"
            "- **[M1]** **`a.go:1`** — A finding\n\n"
            "## Verdict\n\n"
            "This is not a finding: - **[M2]** **`b.go:2`** — Not parsed\n"
        )
        assert len(findings) == 1

    def test_empty_input(self):
        assert _findings("") == []

    def test_a_document_declaring_nothing_declares_nothing(self):
        assert ReviewDocument().findings == []

    def test_no_severity_sections(self):
        assert _findings("# Review\n\nSome preamble.\n\n## Summary\n\nNothing here.\n") == []

    def test_the_metadata_header_declares_no_findings(self):
        """The frame is read off before the body, so a header can never be
        mistaken for a declaration."""
        findings = _findings(
            "# Code Review\n\n"
            "<!-- head_sha: abc123 -->\n\n"
            "## Must fix\n\n- **[M1]** **`a.go:1`** — A finding\n"
        )
        assert [f.id for f in findings] == ["M1"]

    def test_finding_with_no_body(self):
        assert _findings("## Nit\n\n- **[N1]** **`file.go:10`**\n")[0].body == ""

    def test_case_insensitive_section_headers(self):
        findings = _findings(
            "## Must Fix\n\n"
            "- **[M1]** **`file.go:10`** — Bug found\n\n"
            "## SHOULD FIX\n\n"
            "- **[S1]** **`file.go:20`** — Cleanup needed\n\n"
            "## nit\n\n"
            "- **[N1]** **`file.go:30`** — Style issue\n"
        )
        assert [(f.severity, f.body) for f in findings] == [
            ("M", "Bug found"), ("S", "Cleanup needed"), ("N", "Style issue"),
        ]

    def test_stray_text_between_sections_does_not_corrupt_previous_finding(self):
        findings = _findings(
            "## Should fix\n"
            "- **[S1]** **`a.go:10`** — Real should-fix body\n\n"
            "## Nit\n"
            "_None in this file group._\n"
            "- **[N1]** **`b.go:20`** — Real nit body\n"
        )
        by_id = {f.id: f for f in findings}
        assert len(findings) == 2
        assert by_id["S1"].body == "Real should-fix body"
        assert by_id["N1"].body == "Real nit body"

    def test_empty_section_markers_do_not_corrupt_adjacent_findings(self):
        findings = _findings(
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
        by_id = {f.id: f for f in findings}
        assert len(findings) == 4
        assert by_id["S1"].body == "Should body"
        assert by_id["S2"].body == "Last should body"
        assert by_id["N1"].body == "First nit"
        assert by_id["I1"].body == "First idiom"

    def test_finding_with_stable_id_comment(self):
        findings = _findings(
            "## Must fix\n\n"
            "- **[M1]** <!-- sid:abc12345 --> **`file.go:10`** — body text\n"
        )
        assert len(findings) == 1
        assert (findings[0].id, findings[0].path, findings[0].line) == ("M1", "file.go", 10)

    def test_idioms_section(self):
        findings = _findings("## Idioms\n\n- **[I1]** **`config.go:5`** — Good pattern\n")
        assert (findings[0].severity, findings[0].body) == ("I", "Good pattern")

    def test_a_whole_review_keeps_every_body(self):
        findings = _findings(
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
        by_id = {f.id: f for f in findings}
        assert all(f.body for f in findings)
        assert "Missing permissions block." in by_id["M1"].body
        assert "Race condition in handler." in by_id["M2"].body
        assert "Hardcoded prefix." in by_id["S1"].body
        assert "Tests not in CI." in by_id["N1"].body

    def test_every_last_in_section_retains_body(self):
        findings = _findings(
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
        by_id = {f.id: f for f in findings}
        assert all(f.body for f in findings)
        assert by_id["M2"].body == "Must-fix beta (last in section)"
        assert by_id["S1"].body == "Should-fix only (last in section)"
        assert by_id["N2"].body == "Nit beta (last in section)"

    def test_a_checked_finding_is_declared_though_open_counts_omits_it(self):
        """The two readings differ deliberately: `open_counts` reports what is
        still outstanding, `findings` reports every declaration."""
        document = ReviewDocument.parse(
            "## Must fix\n"
            "- [x] **[M1]** **`a.go:1`** — done\n"
            "- [ ] **[M2]** **`b.go:2`** — open\n"
        )
        assert [f.id for f in document.findings] == ["M1", "M2"]
        assert document.open_counts["M"] == 1
