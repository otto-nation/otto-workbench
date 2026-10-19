"""review.md rendered from the record: parity with today's documents, and the
deviations the plan accepts, each pinned."""

import sys
from pathlib import Path

import pytest

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from review.ingest import ingest_review
from review.record import Disposition, RecordFinding, ReviewRecord
from review.render import BANNER, finding_line, render_body, render_review
from review.types import ReviewMeta

from review_record_support import canonical, expected_text, fixture_meta, fixture_text

PARITY_CASES = [
    "synthesis_pr", "self_incremental_fixed", "mechanical_fallback", "clean",
    "unchanged", "evidence_dropped", "fix_annotated", "rebuild",
]


@pytest.mark.parametrize("case", PARITY_CASES)
def test_rendering_the_ingested_review_reproduces_todays_document(case):
    record = ingest_review(fixture_text(case, "input.md"))
    rendered = render_review(record, fixture_meta(case))
    assert canonical(rendered) == canonical(expected_text(case))


def test_the_banner_is_the_only_line_rendering_adds():
    source = fixture_text("synthesis_pr", "input.md")
    rendered = render_review(ingest_review(source), fixture_meta("synthesis_pr"))
    added = set(rendered.splitlines()) - set(source.splitlines())
    assert added - {""} == {BANNER}


def test_the_header_is_meta_json_s():
    """D6: a key an agent typed into its header is not on the rendered page."""
    record = ingest_review("# T\n<!-- weird: 1 -->\n\n## Summary\nx\n")
    assert "weird" not in render_review(record, ReviewMeta(repo="a/b", pr_number=1))


def test_a_falsified_finding_is_not_rendered():
    record = ReviewRecord(findings=(
        RecordFinding(id="S1", severity="S", lead="`a.py:1` — ", body="kept"),
        RecordFinding(id="S2", severity="S", lead="`b.py:1` — ", body="gone",
                      disposition=Disposition.FALSIFIED),
    ))
    assert "gone" not in render_body(record) and "kept" in render_body(record)


def test_a_head_annotation_renders_at_the_tail():
    """D3."""
    record = ingest_review(
        "## Nit\n- [ ] **[N1]** `a.py:1` — *(declined — no)* text\n"
    )
    assert finding_line(record.findings[0]) == (
        "- [ ] **[N1]** `a.py:1` — text *(declined — no)*"
    )


def test_an_unknown_section_renders_before_the_tail():
    """D4."""
    body = render_body(ingest_review("## Verdict\nok\n\n## Review gaps\ng\n\n## Summary\ns\n"))
    assert body.index("## Review gaps") < body.index("## Verdict")
    assert body.index("## Summary") < body.index("## Review gaps")


def test_stray_severity_text_renders_above_the_findings():
    """D5."""
    text = (
        "## Nit\n"
        "- **[N1]** `a.py:1` — x\n"
        "- ~~**[S2]**~~ gone\n"
        "\n"
        "### Carried forward: g\n"
    )
    body = render_body(ingest_review(text, strict=False))
    assert body.index("### Carried forward: g") < body.index("**[N1]**")
    assert body.index("- ~~**[S2]**~~ gone") < body.index("**[N1]**")


def test_annotation_spelling_is_canonical():
    """D7."""
    (f,) = ingest_review("## Nit\n- [ ] **[N1]** `a.py:1` — x *(Declined – no)*\n").findings
    assert finding_line(f).endswith("*(declined — no)*")


def test_blockquotes_outside_findings_are_kept():
    """D8."""
    assert "  > quoted" in render_body(ingest_review("## Summary\nIntro\n  > quoted\n"))


def test_sid_markers_render_only_when_asked():
    (f,) = ingest_review("## Nit\n- **[N1]** <!-- sid:ab12cd34 --> `a.py:1` — x\n").findings
    assert finding_line(f) == "- **[N1]** `a.py:1` — x"
    assert finding_line(f, sids=True) == "- **[N1]** <!-- sid:ab12cd34 --> `a.py:1` — x"


def test_evidence_is_never_on_the_page():
    """D9 — and the prompt view keeps it."""
    text = "## Should fix\n- **[S1]** `a.py:2` — Loop.\n  > ```python\n  > call()\n  > ```\n"
    record = ingest_review(text)
    assert "call()" not in render_review(record, ReviewMeta(repo="a/b", pr_number=1))
    assert "  > call()" in render_body(record, evidence=True)


def test_omitted_sections_are_left_out():
    record = ingest_review(fixture_text("synthesis_pr", "input.md"))
    body = render_body(record, omit=frozenset({"File Triage"}))
    assert "## File Triage" not in body and "## Summary" in body
