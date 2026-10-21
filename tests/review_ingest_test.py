"""Ingest: the one place an agent's markdown review becomes a ReviewRecord."""

import sys
from pathlib import Path

import pytest

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from review.ingest import (
    IngestErrorKind, IngestRejected, ingest_review,
    _finalize_finding, ends_finding_body, finding_spans,
    is_section_boundary, severity_of_heading, starts_finding_or_section,
)
from review.types import Finding, FindingScope, PriorDisposition
from review.record import Disposition
from review.render import BANNER

from review_record_support import fixture_text


def _kinds(text: str) -> list[IngestErrorKind]:
    with pytest.raises(IngestRejected) as exc:
        ingest_review(text)
    return [e.kind for e in exc.value.errors]


class TestRejections:
    @pytest.mark.parametrize("text, kind", [
        ("## Should fix\n- **[Q1]** `a.py:1` — what is this for?\n", IngestErrorKind.UNKNOWN_SEVERITY),
        ("## Nit\n- **[N1]** `a.py:1` — one\n- **[N1]** `b.py:2` — two\n", IngestErrorKind.DUPLICATE_ID),
        ("## Must fix\n- **[M1]**\n", IngestErrorKind.MISSING_ANCHOR),
        (f"# Review\n{BANNER}\n\n## Summary\nx\n", IngestErrorKind.RENDERED_INPUT),
    ])
    def test_a_malformed_entry_is_rejected_with_a_named_error(self, text, kind):
        assert kind in _kinds(text)

    def test_every_error_is_reported_not_just_the_first(self):
        text = "## Nit\n- **[N1]** `a.py:1` — x\n- **[N1]** `b.py:2` — y\n- **[X1]** `c.py:3` — z\n"
        assert set(_kinds(text)) >= {IngestErrorKind.DUPLICATE_ID, IngestErrorKind.UNKNOWN_SEVERITY}

    def test_errors_name_the_line(self):
        with pytest.raises(IngestRejected) as exc:
            ingest_review("## Summary\nok\n\n## Nit\n- **[N1]** `a.py:1` — x\n- **[N1]** `b.py:2` — y\n")
        assert exc.value.errors[0].line == 6
        assert "N1" in exc.value.errors[0].render()

    def test_errors_come_back_sorted_by_line(self):
        text = (
            "## Nit\n"
            "- **[X1]** `a.py:1` — z\n"
            "- **[N1]** `b.py:2` — x\n"
            "- **[N1]** `c.py:3` — y\n"
        )
        with pytest.raises(IngestRejected) as exc:
            ingest_review(text)
        assert [e.line for e in exc.value.errors] == [2, 4]

    def test_missing_anchor_quotes_the_unparsed_line(self):
        line = "- **[M1]**: `a.py:1` — body"
        with pytest.raises(IngestRejected) as exc:
            ingest_review(f"## Must fix\n{line}\n")
        err = exc.value.errors[0]
        assert err.kind is IngestErrorKind.MISSING_ANCHOR
        assert line in err.detail
        assert "says nothing" not in err.detail

    def test_a_capital_x_checkbox_stays_prose(self):
        record = ingest_review("## Should fix\n- [X] **[S1]** `a.py:1` — body\n")
        assert record.findings == ()

    def test_a_rendered_file_is_refused_even_when_not_strict(self):
        with pytest.raises(IngestRejected):
            ingest_review(f"{BANNER}\n## Summary\nx\n", strict=False)

    def test_not_strict_keeps_a_duplicate_rather_than_refusing(self):
        record = ingest_review(
            "## Nit\n- **[N1]** `a.py:1` — x\n- **[N1]** `b.py:2` — y\n",
            strict=False,
        )
        assert [f.id for f in record.findings] == ["N1", "N1"]


class TestWhatIsRead:
    def test_a_finding_keeps_its_location_text_and_body_apart(self):
        record = ingest_review("## Should fix\n- **[S1]** **`a.py:3-5`** — Missing guard.\n")
        (f,) = record.findings
        assert (f.path, f.line, f.end_line) == ("a.py", 3, 5)
        assert f.body == "Missing guard."

    def test_a_named_location_without_a_dash_still_splits(self):
        (f,) = ingest_review("## Should fix\n- **[S1]** `a.py:1` text without a dash\n").findings
        assert f.body == "text without a dash"
        assert (f.path, f.line) == ("a.py", 1)

    def test_an_unlocated_finding_keeps_its_whole_text_as_body(self):
        (f,) = ingest_review("## Should fix\n- [ ] **[S3]** PR scope — two changes.\n").findings
        assert f.body == "PR scope — two changes." and f.checkbox

    def test_evidence_is_split_from_the_body(self):
        text = (
            "## Should fix\n- **[S1]** **`a.py:2`** — Loop.\n"
            "  more prose\n  > ```python\n  > call()\n  > ```\n"
        )
        (f,) = ingest_review(text).findings
        assert f.body == "Loop.\n  more prose"
        assert f.evidence == "call()"

    def test_annotations_become_dispositions(self):
        text = (
            "## Nit\n"
            "- [x] **[N1]** `a.py:1` — done\n"
            "- [ ] **[N2]** `b.py:2` — no. *(declined — by design)*\n"
            "- [ ] **[N3]** `c.py:3` — hm. *(skipped — needs a person)*\n"
            "- [x] **[N4]** `d.py:4` — ok. *(unverified — suite red)*\n"
        )
        f = {x.id: x for x in ingest_review(text).findings}
        assert f["N1"].disposition is Disposition.FIXED
        assert (f["N2"].disposition, f["N2"].reason, f["N2"].body) == (Disposition.DECLINED, "by design", "no.")
        assert (f["N3"].disposition, f["N3"].reason) == (Disposition.SKIPPED, "needs a person")
        assert (f["N4"].unverified, f["N4"].reason) == (True, "suite red")

    def test_a_skip_on_the_declaration_does_not_count_once_a_continuation_follows(self):
        (f,) = ingest_review(
            "## Should fix\n- [ ] **[S1]** `a.py:1` — body *(skipped — r)*\n  more prose\n"
        ).findings
        assert f.disposition is Disposition.OPEN
        assert "*(skipped — r)*" in f.body

    def test_a_skip_at_the_end_of_the_last_continuation_counts(self):
        (f,) = ingest_review(
            "## Should fix\n- [ ] **[S1]** `a.py:1` — body\n  more prose *(skipped — r)*\n"
        ).findings
        assert (f.disposition, f.reason, f.body) == (Disposition.SKIPPED, "r", "body\n  more prose")

    def test_a_skip_head_on_a_continuation_counts_when_the_declaration_body_is_empty(self):
        (f,) = ingest_review(
            "## Should fix\n- [ ] **[S1]** `a.py:1` —\n  *(skipped — r)* more text\n"
        ).findings
        assert not f.body.startswith("—")
        assert (f.disposition, f.reason) == (Disposition.SKIPPED, "r")

    def test_a_bare_skip_with_no_reason_is_skipped(self):
        (f,) = ingest_review(
            "## Should fix\n- [ ] **[S1]** `a.py:1` — body *(skipped)*\n"
        ).findings
        assert (f.disposition, f.reason) == (Disposition.SKIPPED, "")

    def test_a_head_position_skip_on_the_declaration_takes_the_reason(self):
        (f,) = ingest_review(
            "## Should fix\n- [ ] **[S1]** `a.py:1` — *(skipped — r)* — body\n"
        ).findings
        assert (f.disposition, f.reason) == (Disposition.SKIPPED, "r")

    def test_a_tail_annotation_with_an_em_dash_is_not_the_body_separator(self):
        (f,) = ingest_review(
            "## Should fix\n- [ ] **[S1]** `a.py:1` text *(declined — x)*\n"
        ).findings
        assert (f.disposition, f.reason, f.body) == (Disposition.DECLINED, "x", "text")

    def test_a_sid_marker_is_kept_off_the_lead(self):
        (f,) = ingest_review("## Nit\n- **[N1]** <!-- sid:ab12cd34 --> `a.py:1` — x\n").findings
        assert f.sid == "ab12cd34" and (f.path, f.line) == ("a.py", 1)

    def test_the_stable_id_is_the_grammar_s(self):
        from review.grammar import FindingIdentity
        line = "- **[N1]** `a.py:1` — something"
        (f,) = ingest_review(f"## Nit\n{line}\n").findings
        assert f.stable_id == FindingIdentity.of(line).stable_id

    def test_sections_land_in_their_fields(self):
        record = ingest_review(fixture_text("synthesis_pr", "input.md"))
        assert record.summary.startswith("Adds speech-to-text")
        assert record.verdict.startswith("**Approve.**")
        assert [e.path for e in record.triage] == ["apps/desktop/package.json", "packages/shared/src/index.ts"]
        assert record.failures[0].agent == "disprove" and record.failures[0].recoverable
        assert record.severity_sections[0].note == "(none)"
        assert [f.id for f in record.findings] == ["S1", "S2", "N1", "N2", "I1"]

    def test_the_ledger_and_unknown_sections_are_kept(self):
        text = "## Prior findings\n- **[S1]** `a.py` — Fixed\n\n## Review gaps\n- group 2 failed\n"
        record = ingest_review(text)
        assert [(e.id, e.disposition, e.path) for e in record.ledger] == [
            ("S1", PriorDisposition.FIXED, "a.py"),
        ] and record.findings == ()
        assert record.extra[0].heading == "Review gaps"

    def test_the_header_is_not_read(self):
        record = ingest_review(
            "# T\n<!-- head_sha: abc -->\n<!-- summary: HEADER_ONLY_KEY -->\n"
            "This would be the summary if the header were read.\n\n## Summary\nx\n"
        )
        assert record.summary == "x"
        assert record.verdict == ""
        assert "HEADER_ONLY_KEY" not in repr(record)
        assert "would be the summary" not in repr(record)

# ── The span walk ────────────────────────────────────────────────────────────


class TestTheWalk:
    # TestIsSectionBoundary
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


    # TestStartsFindingOrSection
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


    # TestMatchSeverityHeader
    def test_h2_must_fix(self):
        assert severity_of_heading("## Must fix") == "M"

    def test_h3_should_fix_hyphenated(self):
        assert severity_of_heading("### Should-fix") == "S"

    def test_h3_nit(self):
        assert severity_of_heading("### Nit") == "N"

    def test_h2_nits_plural(self):
        assert severity_of_heading("## Nits") == "N"

    def test_h4_idioms(self):
        assert severity_of_heading("#### Idioms") == "I"

    def test_case_insensitive(self):
        assert severity_of_heading("## SHOULD FIX") == "S"

    def test_non_severity_header_returns_none(self):
        assert severity_of_heading("## Summary") is None

    def test_non_header_returns_none(self):
        assert severity_of_heading("Some text") is None

    def test_findings_parent_returns_none(self):
        assert severity_of_heading("## Findings") is None


    # TestFinalizeFinding
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


    # TestEndsFindingBody
    # The one answer to where a finding stops, which every span walk asks.

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
        spans = finding_spans(
            "## Must fix\n"
            "- **[M1]** **`a.go:1`** — one\n"
            "- see also the helper above\n"
        )
        assert spans[0].finding.body.endswith("see also the helper above")


    # TestFindingSpans
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


def test_markdown_evidence_is_stored_as_raw_code():
    text = "## Must fix\n- **[M1]** **`a.py:2`** — bad\n  > ```python\n  > y = compute(x)\n  > ```\n"
    (f,) = ingest_review(text).findings
    assert f.evidence == "y = compute(x)"
    assert (f.path, f.line, f.body) == ("a.py", 2, "bad")


def test_a_triage_line_is_split_into_path_and_note():
    (entry,) = ingest_review("## File Triage\n- `a.go` — **Tier 2** (logic)\n").triage
    assert (entry.path, entry.note) == ("a.go", "**Tier 2** (logic)")


def test_merge_reads_section_headers_case_insensitively():
    # Agents write the headings the prompt names, in whatever case they
    # felt like typing; the section a heading opens is the same either way.
    from review.merge import merge_records
    from review.render import render_body
    rec = ingest_review(
        "## Must Fix\n- **[M1]** **`a.go:10`** — bug\n"
        "## NIT\n- **[N1]** **`a.go:20`** — style\n",
        strict=False,
    )
    result = render_body(merge_records([rec]), sids=True, evidence=True)
    assert "[M1]" in result
    assert "[N1]" in result


def _headed(text: str) -> str:
    if text.lstrip().startswith("## "):
        return text
    heading = "## Must fix\n" if "**[M" in text else "## Should fix\n"
    return heading + text


def _parse_findings_for_verification(text: str) -> list[dict]:
    out = []
    for f in ingest_review(_headed(text), strict=False).visible:
        if not f.path:
            continue
        out.append({
            "id": f.id, "severity": f.severity, "path": f.path,
            "body": f.body, "evidence": f.evidence,
        })
    return out


class TestParseVerificationStripsLine:
    def test_path_excludes_line_number(self):
        text = '- **[M1]** **`pkg/handler.go:42`** — missing error check\n'
        findings = _parse_findings_for_verification(text)
        assert findings[0]["path"] == "pkg/handler.go"

    def test_path_excludes_line_range(self):
        text = '- **[S1]** **`pkg/handler.go:10-20`** — issue\n'
        findings = _parse_findings_for_verification(text)
        assert findings[0]["path"] == "pkg/handler.go"

    def test_checkbox_path_excludes_line(self):
        text = '- [ ] **[M1]** `handler.go:42` — desc\n'
        findings = _parse_findings_for_verification(text)
        assert findings[0]["path"] == "handler.go"


class TestParseVerificationReadsThePostersLocation:
    """Verification checks the file the poster will place the finding on.

    The path comes from `finding_location`, not from a second reading of the
    line. That second reading kept any suffix it did not know as part of the
    path, so a finding at `replay.py:_short` was stat'd as a file literally
    named that and dropped as "file not found" while the poster read the same
    line as `replay.py`.
    """

    @pytest.mark.parametrize("location, path", [
        ("ai/lib/git/replay.py:_short", "ai/lib/git/replay.py"),
        ("ai/lib/git/replay.py:_short:12", "ai/lib/git/replay.py"),
        ("pkg/x.py:Foo.bar", "pkg/x.py"),
        ("pkg/x.py:~690", "pkg/x.py"),
        ("pkg/x.py:L12", "pkg/x.py"),
    ])
    def test_a_suffix_that_is_not_a_line_number_comes_off(self, location, path):
        text = f"- **[S1]** `{location}` — gap\n"
        findings = _parse_findings_for_verification(text)
        assert findings[0]["path"] == path

    def test_agrees_with_the_poster_on_every_location(self):
        """The property the fix is for, rather than a list of shapes."""
        locations = [
            "ai/lib/git/replay.py:_short", "src/x.py:64,82", "pkg/x.py:Foo.bar",
            "src/my notes.py:12-18", "ai/bin/ci-check", "handler.go:42",
        ]
        for location in locations:
            line = f"- **[S1]** **`{location}`** — gap"
            verified = _parse_findings_for_verification(line + "\n")
            posted = ingest_review(_headed(line + "\n"), strict=False).findings[0].path
            assert verified[0]["path"] == posted, location


class TestParseVerificationReadsALineList:
    """A finding naming two discrete lines still names one file.

    A duplicate declaration is the case that produces this location: the
    finding cites both offsets, and the path used to keep the whole `:64,82`
    suffix. `_match_evidence` then stat'd a path no filesystem holds and
    `_drop_reason` called it "file not found", discarding a correct finding
    about a file that was right there.
    """

    def test_a_comma_separated_pair_leaves_the_path(self):
        text = '- **[S2]** `ai/lib/review/run.py:64,82` — duplicate field\n'
        findings = _parse_findings_for_verification(text)
        assert findings[0]["path"] == "ai/lib/review/run.py"

    def test_a_three_line_list_leaves_the_path(self):
        text = '- **[S2]** `ai/lib/review/run.py:12,18,24` — duplicate field\n'
        findings = _parse_findings_for_verification(text)
        assert findings[0]["path"] == "ai/lib/review/run.py"


class TestParseVerificationSpacedPaths:
    def test_spaced_path_finding_is_not_swallowed_by_the_previous_one(self):
        text = (
            "## Must fix\n"
            "- [ ] **[M1]** **`pkg/a.go:1`** — First finding\n"
            "- [ ] **[M2]** **`src/my notes.py:2`** — Second finding, spaced path\n"
            "- [ ] **[M3]** **`pkg/c.go:3`** — Third finding\n"
        )
        findings = _parse_findings_for_verification(text)
        assert [f["id"] for f in findings] == ["M1", "M2", "M3"]
        assert findings[0]["body"] == "First finding"
        assert findings[1]["path"] == "src/my notes.py"

    def test_spaced_path_with_line_range(self):
        text = '- **[S1]** **`src/my notes.py:12-18`** — issue\n'
        findings = _parse_findings_for_verification(text)
        assert findings[0]["path"] == "src/my notes.py"

    def test_non_ascii_spaced_path(self):
        text = '- [ ] **[M1]** **`src/café brûlé.py:42`** — desc\n'
        findings = _parse_findings_for_verification(text)
        assert findings[0]["path"] == "src/café brûlé.py"

    def test_spaced_path_in_a_bare_code_span(self):
        text = '- **[N1]** `docs/release notes.md:3` — stale\n'
        findings = _parse_findings_for_verification(text)
        assert findings[0]["path"] == "docs/release notes.md"

    def test_unchecked_and_plain_forms_both_parse(self):
        text = (
            "- [ ] **[M1]** **`pkg/a.go:1`** — checkbox form\n"
            "- **[M2]** **`pkg/b.go:2`** — plain form\n"
        )
        findings = _parse_findings_for_verification(text)
        assert [f["id"] for f in findings] == ["M1", "M2"]
        assert [f["body"] for f in findings] == ["checkbox form", "plain form"]


class TestVerificationReadsEachFindingsOwnBody:
    """A finding is checked against the evidence written under it and no other.

    A finding with no path is not checked. A declaration ingest cannot place
    used to be appended to the finding above it, which is how a finding came
    to be verified against a quotation belonging to its neighbour.
    """

    UNREADABLE_LOCATION = (
        "## Must fix\n"
        "- **[M1]** **`a.go:1`** — one\n"
        "  > ```go\n"
        "  > x := 1\n"
        "  > ```\n"
        "- **[M2]** Nil pointer dereference at handler.go:42\n"
        "  > ```go\n"
        "  > y := 2\n"
        "  > ```\n"
        "- **[M3]** **`c.go:3`** — three\n"
    )

    def test_an_unreadable_location_is_not_checked(self):
        findings = _parse_findings_for_verification(self.UNREADABLE_LOCATION)
        assert [f["id"] for f in findings] == ["M1", "M3"]

    def test_an_unreadable_location_does_not_join_the_finding_above_it(self):
        findings = _parse_findings_for_verification(self.UNREADABLE_LOCATION)
        assert findings[0]["evidence"] == "x := 1"

    def test_a_ledger_entry_is_not_checked(self):
        text = (
            "## Must fix\n"
            "- **[M1]** **`a.go:1`** — one\n"
            "## Prior findings\n"
            "- **[S1]** **`old.go:2`** — Fixed\n"
        )
        findings = _parse_findings_for_verification(text)
        assert [f["id"] for f in findings] == ["M1"]

    def test_a_body_stops_at_the_resolved_finding_below_it(self):
        text = (
            "## Must fix\n"
            "- **[M1]** **`a.go:1`** — one\n"
            "  > ```go\n"
            "  > x := 1\n"
            "  > ```\n"
            "- ~~**[M2]** **`b.go:2`** — resolved~~\n"
            "  > ```go\n"
            "  > y := 2\n"
            "  > ```\n"
        )
        findings = _parse_findings_for_verification(text)
        assert [f["id"] for f in findings] == ["M1"]
        assert "y := 2" not in findings[0]["body"]


