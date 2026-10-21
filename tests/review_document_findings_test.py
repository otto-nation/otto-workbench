"""Tests for the findings `ReviewDocument` reads off a stored review's record."""

import sys
from pathlib import Path

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)
from review.document import ReviewDocument
from review.record import Disposition
from review.types import Finding

from review_record_support import finding, record


def _doc(*findings, **fields) -> ReviewDocument:
    return ReviewDocument(record=record(*findings, **fields))


def _findings(*findings, **fields) -> list[Finding]:
    """The findings a review's record declares, read the way every reader does."""
    return _doc(*findings, **fields).findings


class TestFindings:
    def test_single_finding_single_section(self):
        findings = _findings(finding("M1", "Fix the bug", path="file.go", line=10))
        assert len(findings) == 1
        assert findings[0].body == "Fix the bug"

    def test_multiple_findings_one_section(self):
        findings = _findings(
            finding("M1", "First finding", path="a.go", line=1),
            finding("M2", "Second finding", path="b.go", line=2),
        )
        assert [(f.id, f.body) for f in findings] == [
            ("M1", "First finding"), ("M2", "Second finding"),
        ]

    def test_findings_across_multiple_sections(self):
        findings = _findings(
            finding("M1", "Must body", path="a.go", line=1),
            finding("S1", "Should body", path="b.go", line=2),
            finding("N1", "Nit body", path="c.go", line=3),
        )
        assert [(f.id, f.body) for f in findings] == [
            ("M1", "Must body"), ("S1", "Should body"), ("N1", "Nit body"),
        ]

    def test_last_finding_in_section_preserves_body(self):
        findings = _findings(
            finding("M1", "First must-fix", path="a.go", line=1),
            finding("M2", "Last must-fix body", path="b.go", line=2),
            finding("S1", "First should-fix", path="c.go", line=3),
            finding("N1", "The only nit", path="d.go", line=4),
            verdict="All done.",
        )
        by_id = {f.id: f for f in findings}
        assert by_id["M2"].body == "Last must-fix body"
        assert by_id["S1"].body == "First should-fix"
        assert by_id["N1"].body == "The only nit"
        assert all(f.body for f in findings)

    def test_multi_line_continuation(self):
        findings = _findings(finding(
            "M1",
            "Main body text\n  More details here.\n  And another line.",
            path="file.go", line=10,
        ))
        body = findings[0].body
        assert "Main body text" in body
        assert "More details here." in body
        assert "And another line." in body

    def test_multi_line_with_code_block(self):
        findings = _findings(finding(
            "M1",
            "Add permissions block:\n"
            "  ```yaml\n"
            "  permissions:\n"
            "    contents: read\n"
            "  ```\n"
            "  Note about the fix.",
            path="workflow.yml", line=10,
        ))
        body = findings[0].body
        assert "Add permissions block:" in body
        assert "permissions:" in body
        assert "contents: read" in body
        assert "Note about the fix." in body

    def test_multi_line_last_in_section_regression(self):
        findings = _findings(
            finding("M1", "Simple finding", path="a.go", line=1),
            finding(
                "M2",
                "Complex finding.\n"
                "  Code block follows:\n"
                "  ```yaml\n"
                "  jobs:\n"
                "    build:\n"
                "      runs-on: ubuntu\n"
                "  ```\n"
                "  *(Note referencing other comments.)*",
                path="workflow.yml", line=50, end_line=60,
            ),
            finding("S1", "Should fix body", path="b.go", line=5),
        )
        m2 = next(f for f in findings if f.id == "M2")
        assert "Complex finding" in m2.body

    def test_code_block_indentation_preserved(self):
        findings = _findings(finding(
            "M1",
            "Add this code:\n"
            "  ```python\n"
            "  def foo():\n"
            "      return bar\n"
            "  ```",
            path="file.py", line=10,
        ))
        body = findings[0].body
        assert "    return bar" in body or "      return bar" in body

    def test_nested_indentation_preserved(self):
        findings = _findings(finding(
            "S1",
            "Use this structure:\n"
            "  ```yaml\n"
            "  jobs:\n"
            "    build:\n"
            "      runs-on: ubuntu\n"
            "  ```",
            path="config.yml", line=5,
        ))
        yaml_lines = [l for l in findings[0].body.split("\n") if "runs-on" in l]
        assert yaml_lines
        assert yaml_lines[0] != yaml_lines[0].lstrip()

    def test_strikethrough_skipped(self):
        findings = _findings(finding("M2", "Still open", path="file.go", line=20))
        assert [f.id for f in findings] == ["M2"]

    def test_sub_headers_handled(self):
        findings = _findings(
            finding("M1", "First", path="a.go", line=1),
            finding("M2", "Second", path="b.go", line=2),
        )
        assert [f.body for f in findings] == ["First", "Second"]

    def test_h3_severity_under_findings_parent(self):
        findings = _findings(
            finding("S1", "Issue found", path="file.go", line=10),
            finding("N1", "Style issue", path="file.go", line=20),
        )
        assert [f.severity for f in findings] == ["S", "N"]

    def test_non_severity_section_stops_parsing(self):
        findings = _findings(
            finding("M1", "A finding", path="a.go", line=1),
            verdict="This is not a finding: - **[M2]** **`b.go:2`** — Not parsed",
        )
        assert len(findings) == 1

    def test_empty_input(self):
        assert _findings() == []

    def test_a_document_declaring_nothing_declares_nothing(self):
        assert ReviewDocument().findings == []

    def test_no_severity_sections(self):
        assert _doc(summary="Nothing here.").findings == []

    def test_the_metadata_header_declares_no_findings(self):
        """The frame is read off before the body, so a header can never be
        mistaken for a declaration."""
        findings = _findings(finding("M1", "A finding", path="a.go", line=1))
        assert [f.id for f in findings] == ["M1"]

    def test_finding_with_no_body(self):
        assert _findings(finding("N1", "", path="file.go", line=10))[0].body == ""

    def test_case_insensitive_section_headers(self):
        findings = _findings(
            finding("M1", "Bug found", path="file.go", line=10),
            finding("S1", "Cleanup needed", path="file.go", line=20),
            finding("N1", "Style issue", path="file.go", line=30),
        )
        assert [(f.severity, f.body) for f in findings] == [
            ("M", "Bug found"), ("S", "Cleanup needed"), ("N", "Style issue"),
        ]

    def test_stray_text_between_sections_does_not_corrupt_previous_finding(self):
        findings = _findings(
            finding("S1", "Real should-fix body", path="a.go", line=10),
            finding("N1", "Real nit body", path="b.go", line=20),
        )
        by_id = {f.id: f for f in findings}
        assert len(findings) == 2
        assert by_id["S1"].body == "Real should-fix body"
        assert by_id["N1"].body == "Real nit body"

    def test_empty_section_markers_do_not_corrupt_adjacent_findings(self):
        findings = _findings(
            finding("S1", "Should body", path="a.go", line=1),
            finding("S2", "Last should body", path="b.go", line=2),
            finding("N1", "First nit", path="c.go", line=3),
            finding("I1", "First idiom", path="d.go", line=4),
        )
        by_id = {f.id: f for f in findings}
        assert len(findings) == 4
        assert by_id["S1"].body == "Should body"
        assert by_id["S2"].body == "Last should body"
        assert by_id["N1"].body == "First nit"
        assert by_id["I1"].body == "First idiom"

    def test_finding_with_stable_id_comment(self):
        findings = _findings(finding(
            "M1", "body text", path="file.go", line=10, sid="abc12345",
        ))
        assert len(findings) == 1
        assert (findings[0].id, findings[0].path, findings[0].line) == ("M1", "file.go", 10)

    def test_idioms_section(self):
        findings = _findings(finding("I1", "Good pattern", path="config.go", line=5))
        assert (findings[0].severity, findings[0].body) == ("I", "Good pattern")

    def test_a_whole_review_keeps_every_body(self):
        findings = _findings(
            finding(
                "M1",
                "Missing permissions block.\n"
                "  ```yaml\n"
                "  permissions:\n"
                "    contents: read\n"
                "  ```",
                path="workflow.yml", line=10, end_line=20,
            ),
            finding("M2", "Race condition in handler.", path="handler.go", line=50),
            finding("S1", "Hardcoded prefix.", path="config.sh", line=30),
            finding("N1", "Tests not in CI.", path="test.sh", line=5),
            verdict="Request changes.",
        )
        by_id = {f.id: f for f in findings}
        assert all(f.body for f in findings)
        assert "Missing permissions block." in by_id["M1"].body
        assert "Race condition in handler." in by_id["M2"].body
        assert "Hardcoded prefix." in by_id["S1"].body
        assert "Tests not in CI." in by_id["N1"].body

    def test_every_last_in_section_retains_body(self):
        findings = _findings(
            finding("M1", "Must-fix alpha", path="a.go", line=1),
            finding("M2", "Must-fix beta (last in section)", path="b.go", line=2),
            finding("S1", "Should-fix only (last in section)", path="c.go", line=3),
            finding("N1", "Nit alpha", path="d.go", line=4),
            finding("N2", "Nit beta (last in section)", path="e.go", line=5),
            verdict="Done.",
        )
        by_id = {f.id: f for f in findings}
        assert all(f.body for f in findings)
        assert by_id["M2"].body == "Must-fix beta (last in section)"
        assert by_id["S1"].body == "Should-fix only (last in section)"
        assert by_id["N2"].body == "Nit beta (last in section)"

    def test_a_checked_finding_is_declared_though_open_counts_omits_it(self):
        """The two readings differ deliberately: `open_counts` reports what is
        still outstanding, `findings` reports every declaration."""
        document = _doc(
            finding("M1", "done", path="a.go", line=1, checkbox=True,
                    disposition=Disposition.FIXED),
            finding("M2", "open", path="b.go", line=2, checkbox=True),
        )
        assert [f.id for f in document.findings] == ["M1", "M2"]
        assert document.open_counts["M"] == 1
