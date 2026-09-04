"""Tests for the finding scanner — where a declaration starts in a document,
and where its body stops.

`ends_finding_body` is the one answer to where a finding stops, and
`finding_spans` is the traversal built on it. `is_section_boundary` and
`starts_finding_or_section` are the line-level questions that answer differs
by, and `drop_findings` is what an editing caller asks of the same spans.
"""

import sys
from pathlib import Path

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)
import pytest
from review.document import ReviewDocument
from review.spans import (
    _finalize_finding, _match_severity_header,
    drop_findings, ends_finding_body,
    finding_spans, is_section_boundary,
    starts_finding_or_section,
)
from review.types import Finding, FindingScope


class TestIsSectionBoundary:
    def test_sub_header_is_boundary(self):
        assert is_section_boundary("### Group A") is True

    def test_strikethrough_finding_is_boundary(self):
        assert is_section_boundary("- ~~**[M1]** **`file.go:10`** — Resolved~~") is True

    def test_non_strikethrough_finding_is_not_boundary(self):
        assert is_section_boundary("- **[M1]** **`file.go:10`** — Open") is False

    def test_regular_text_is_not_boundary(self):
        assert is_section_boundary("Some regular text here") is False

    def test_h2_header_is_not_boundary(self):
        assert is_section_boundary("## Must fix") is False


class TestStartsFindingOrSection:
    def test_finding_line(self):
        assert starts_finding_or_section("- **[M1]** desc") is True

    def test_checkbox_finding(self):
        assert starts_finding_or_section("- [ ] **[S1]** desc") is True

    def test_section_header(self):
        assert starts_finding_or_section("## Must fix") is True

    def test_plain_text(self):
        assert starts_finding_or_section("just plain text") is False

    def test_continuation_line(self):
        assert starts_finding_or_section("  continuation") is False

    def test_bullet_no_finding(self):
        assert starts_finding_or_section("- regular bullet") is False


class TestMatchSeverityHeader:
    def test_h2_must_fix(self):
        assert _match_severity_header("## Must fix") == "M"

    def test_h3_should_fix_hyphenated(self):
        assert _match_severity_header("### Should-fix") == "S"

    def test_h3_nit(self):
        assert _match_severity_header("### Nit") == "N"

    def test_h2_nits_plural(self):
        assert _match_severity_header("## Nits") == "N"

    def test_h4_idioms(self):
        assert _match_severity_header("#### Idioms") == "I"

    def test_case_insensitive(self):
        assert _match_severity_header("## SHOULD FIX") == "S"

    def test_non_severity_header_returns_none(self):
        assert _match_severity_header("## Summary") is None

    def test_non_header_returns_none(self):
        assert _match_severity_header("Some text") is None

    def test_findings_parent_returns_none(self):
        assert _match_severity_header("## Findings") is None


class TestFinalizeFinding:
    def test_non_empty_body_lines(self):
        f = Finding(id="M1", severity="M", seq=1, path="file.go", line=10,
                    end_line=None, body="original")
        _finalize_finding(f, ["first line", "second line"])
        assert "first line" in f.body
        assert "second line" in f.body

    def test_empty_body_lines_leaves_body_unchanged(self):
        f = Finding(id="M1", severity="M", seq=1, path="file.go", line=10,
                    end_line=None, body="original")
        _finalize_finding(f, [])
        assert f.body == "original"

    def test_trailing_sub_header_stripped(self):
        f = Finding(id="M1", severity="M", seq=1, path="file.go", line=10,
                    end_line=None, body="original")
        _finalize_finding(f, ["actual content", "### Group B"])
        assert "### Group B" not in f.body
        assert "actual content" in f.body


class TestEndsFindingBody:
    """The one answer to where a finding stops, which every span walk asks."""

    @pytest.mark.parametrize("line", [
        "- **[M2]** **`b.go:2`** — the next declaration",
        "- [ ] **[M2]** **`b.go:2`** — the next declaration, unchecked",
        "- [x] **[M2]** **`b.go:2`** — the next declaration, ticked off",
        "## Should fix",
        "## Prior findings",
        "### Group B",
        "#### Idioms",
        "- ~~**[M2]** **`b.go:2`** — resolved~~",
    ])
    def test_a_boundary_ends_the_body(self, line):
        assert ends_finding_body(line) is True

    @pytest.mark.parametrize("line", [
        "- a plain bullet someone typed without the indent",
        "  - an indented bullet",
        "> ```go",
        "prose about the finding",
        "",
    ])
    def test_a_continuation_does_not(self, line):
        assert ends_finding_body(line) is False

    def test_a_plain_bullet_is_body_because_the_prompt_asks_for_indentation(self):
        """`reviewer.md` indents evidence under the finding line, so a flat
        bullet in a severity section is a continuation, not a new list."""
        document = ReviewDocument.parse(
            "## Must fix\n"
            "- **[M1]** **`a.go:1`** — one\n"
            "- see also the helper above\n"
        )
        assert document.findings[0].body.endswith("see also the helper above")


class TestFindingSpans:
    def test_a_span_runs_from_the_declaration_to_the_next_one(self):
        text = (
            "## Must fix\n"
            "- **[M1]** **`a.go:1`** — one\n"
            "  detail\n"
            "\n"
            "- **[M2]** **`b.go:2`** — two\n"
        )
        spans = finding_spans(text)
        assert [(s.finding.id, s.start, s.end) for s in spans] == [
            ("M1", 1, 4), ("M2", 4, 6),
        ]

    def test_text_of_returns_the_lines_the_span_claims(self):
        text = (
            "## Must fix\n"
            "- **[M1]** **`a.go:1`** — one\n"
            "  > ```go\n"
            "  > x := 1\n"
            "  > ```\n"
            "- **[M2]** **`b.go:2`** — two\n"
        )
        first = finding_spans(text)[0]
        assert first.text_of(text) == (
            "- **[M1]** **`a.go:1`** — one\n"
            "  > ```go\n"
            "  > x := 1\n"
            "  > ```"
        )

    def test_the_declaration_line_comes_back_stripped(self):
        spans = finding_spans("## Nit\n  - **[N1]** **`a.go:1`** — nested\n")
        assert spans[0].line == "- **[N1]** **`a.go:1`** — nested"

    def test_a_severity_heading_declares_the_findings_below_it(self):
        spans = finding_spans("## Must fix\n- **[M1]** **`a.go:1`** — one\n")
        assert spans[0].scope is FindingScope.DECLARED
        assert spans[0].reported is False

    def test_a_ledger_entry_reports_rather_than_declares(self):
        spans = finding_spans("## Prior findings\n- **[M1]** `old.go` — Fixed\n")
        assert spans[0].scope is FindingScope.REPORTED
        assert spans[0].reported is True

    def test_a_fragment_with_no_heading_is_neither(self):
        """What a caller holding one severity's findings on their own hands in."""
        spans = finding_spans("- **[M1]** **`a.go:1`** — one\n")
        assert spans[0].scope is FindingScope.UNHEADED

    def test_a_resolved_finding_ends_the_span_above_it_and_opens_none(self):
        text = (
            "## Must fix\n"
            "- **[M1]** **`a.go:1`** — one\n"
            "- ~~**[M2]** **`b.go:2`** — resolved~~\n"
        )
        spans = finding_spans(text)
        assert [(s.finding.id, s.end) for s in spans] == [("M1", 2)]

    def test_a_sub_heading_ends_the_span_above_it(self):
        text = (
            "## Must fix\n"
            "### Group A\n"
            "- **[M1]** **`a.go:1`** — one\n"
            "### Group B\n"
            "- **[M2]** **`b.go:2`** — two\n"
        )
        spans = finding_spans(text)
        assert [(s.finding.id, s.start, s.end) for s in spans] == [
            ("M1", 2, 3), ("M2", 4, 6),
        ]

    def test_an_indented_declaration_is_its_own_span(self):
        text = (
            "## Must fix\n"
            "- **[M1]** **`a.go:1`** — one\n"
            "  - **[M2]** **`b.go:2`** — nested\n"
        )
        assert [s.finding.id for s in finding_spans(text)] == ["M1", "M2"]

    def test_spans_never_overlap(self):
        text = (
            "## Must fix\n"
            "- **[M1]** **`a.go:1`** — one\n"
            "  detail\n"
            "- **[M2]** **`b.go:2`** — two\n"
            "## Nit\n"
            "- **[N1]** **`c.go:3`** — three\n"
        )
        spans = finding_spans(text)
        assert all(a.end <= b.start for a, b in zip(spans, spans[1:]))


class TestDropFindings:
    def test_the_finding_and_its_body_go(self):
        text = (
            "## Must fix\n"
            "- **[M1]** **`a.go:1`** — one\n"
            "  detail\n"
            "- **[M2]** **`b.go:2`** — two\n"
        )
        assert drop_findings(text, ["M1"]) == (
            "## Must fix\n"
            "- **[M2]** **`b.go:2`** — two\n"
        )

    def test_dropping_nothing_leaves_every_byte(self):
        text = "## Must fix\n- **[M1]** **`a.go:1`** — one\n"
        assert drop_findings(text, []) == text
        assert drop_findings(text, ["S9"]) == text

    def test_a_ledger_entry_sharing_an_id_is_left_alone(self):
        """The ledger's IDs number the prior review, so a collision there is
        not the finding this gate was told to drop."""
        text = (
            "## Must fix\n"
            "- **[M1]** **`a.go:1`** — one\n"
            "## Prior findings\n"
            "- **[M1]** `old.go` — Fixed\n"
        )
        assert drop_findings(text, ["M1"]) == (
            "## Must fix\n"
            "## Prior findings\n"
            "- **[M1]** `old.go` — Fixed\n"
        )
