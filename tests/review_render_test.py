"""review.md rendered from the record: parity with today's documents, and the
deviations the plan accepts (D1–D10), each pinned."""

import json
import re
import sys
from pathlib import Path

import pytest

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from review.record import (
    Disposition, LedgerLine, ProseSection, RecordFinding, ReviewRecord,
    SeveritySection, TriageEntry, load_record,
)
from review.document import MetaKey
from review.render import BANNER, evidence_block, finding_line, ledger_line, render_body, render_review, triage_line
from review.types import PriorDisposition, ReviewMeta

from review_focus_support import area, user_area
from review_record_support import (
    canonical, expected_text, fixture_meta, fixture_record, finding, record,
)

PARITY_CASES = [
    "synthesis_pr", "self_incremental_fixed", "mechanical_fallback", "clean",
    "unchanged", "evidence_dropped", "fix_annotated", "rebuild",
]


@pytest.mark.parametrize("case", PARITY_CASES)
def test_the_fixture_record_renders_its_expected_document(case):
    rendered = render_review(fixture_record(case), fixture_meta(case))
    assert canonical(rendered) == canonical(expected_text(case))


@pytest.mark.parametrize("case", PARITY_CASES)
def test_the_banner_is_the_only_line_rendering_adds(case):
    source = expected_text(case)
    rendered = render_review(fixture_record(case), fixture_meta(case))
    base = "\n".join(ln for ln in source.splitlines() if ln.strip() != BANNER)
    x1 = re.sub(r"\*\*(`[^`\n]+`)\*\*", r"\1", base)
    added = set(rendered.splitlines()) - set(x1.splitlines())
    assert added - {""} == {BANNER}
    lost = set(x1.splitlines()) - set(rendered.splitlines())
    assert lost - {""} == set()


def test_prompt_text_omits_the_banner():
    rendered = render_review(
        record(summary="x"), ReviewMeta(repo="a/b", pr_number=1),
        banner=False,
    )
    assert BANNER not in rendered


def test_the_header_is_meta_json_s():
    """D6: a key an agent typed into its header is not on the rendered page."""
    meta = ReviewMeta(repo="a/b", pr_number=1, head_sha="abc")
    rendered = render_review(record(summary="x"), meta)
    header = rendered.split("## ", 1)[0]
    assert "<!-- head_sha: abc -->" in header
    keys = re.findall(r"<!-- (\w+):", header)
    assert "weird" not in keys
    assert set(keys) <= {k.value for k in MetaKey}


def test_a_falsified_finding_is_not_rendered():
    rec = ReviewRecord(findings=(
        RecordFinding(id="S1", severity="S", path="a.py", line=1, body="kept"),
        RecordFinding(id="S2", severity="S", path="b.py", line=1, body="gone",
                      disposition=Disposition.FALSIFIED),
    ))
    assert "gone" not in render_body(rec) and "kept" in render_body(rec)


def test_a_head_annotation_renders_at_the_tail():
    """D3."""
    rec = record(finding(
        "N1", "text", checkbox=True, disposition=Disposition.DECLINED, reason="no",
    ))
    assert finding_line(rec.findings[0]) == (
        "- [ ] **[N1]** `a.py:1` — text *(declined — no)*"
    )


def test_an_unknown_section_renders_before_the_tail():
    """D4."""
    body = render_body(record(
        summary="s", verdict="ok", extra=(ProseSection("Review gaps", "g"),),
    ))
    assert body.index("## Review gaps") < body.index("## Verdict")
    assert body.index("## Summary") < body.index("## Review gaps")


def test_stray_severity_text_renders_above_the_findings():
    """D5."""
    body = render_body(record(
        finding("N1", "x"),
        severity_sections=(SeveritySection(
            "N", "- ~~**[S2]**~~ gone\n\n### Carried forward: g",
        ),),
    ))
    assert body.index("### Carried forward: g") < body.index("**[N1]**")
    assert body.index("- ~~**[S2]**~~ gone") < body.index("**[N1]**")


def test_annotation_spelling_is_canonical():
    """D7."""
    (f,) = record(finding(
        "N1", "x", checkbox=True, disposition=Disposition.DECLINED, reason="no",
    )).findings
    assert finding_line(f).endswith("*(declined — no)*")


def test_blockquotes_outside_findings_are_kept():
    """D8."""
    assert "  > quoted" in render_body(record(summary="Intro\n  > quoted"))


def test_sid_markers_render_only_when_asked():
    (f,) = record(finding("N1", "x", sid="ab12cd34")).findings
    assert finding_line(f) == "- **[N1]** `a.py:1` — x"
    assert finding_line(f, sids=True) == "- **[N1]** <!-- sid:ab12cd34 --> `a.py:1` — x"


def test_evidence_is_never_on_the_page():
    """D9 — and the prompt view keeps it."""
    rec = record(finding("S1", "Loop.", path="a.py", line=2, evidence="call()"))
    assert "call()" not in render_review(rec, ReviewMeta(repo="a/b", pr_number=1))
    assert "  > call()" in render_body(rec, evidence=True)


def test_a_finding_renders_under_the_section_its_id_names():
    """D10."""
    body = render_body(record(
        finding("S1", "x", checkbox=True),
        finding("N1", "y", path="b.py", line=2, checkbox=True),
    ))
    assert "## Should fix" in body
    assert "**[S1]**" in body.split("## Should fix", 1)[1].split("## ", 1)[0]
    assert "**[N1]**" in body.split("## Nit", 1)[1].split("## ", 1)[0]
    assert body.index("## Should fix") < body.index("## Nit")


def test_omitted_sections_are_left_out():
    rec = fixture_record("synthesis_pr")
    body = render_body(rec, omit=frozenset({"File Triage"}))
    assert "## File Triage" not in body and "## Summary" in body


def test_a_location_renders_from_the_fields():
    """X1: the location is the record's, in one canonical spelling."""
    ranged = RecordFinding(id="M1", severity="M", path="a.py", line=3, end_line=5, body="x")
    bare = RecordFinding(id="N1", severity="N", body="PR title says docs-only")
    assert finding_line(ranged) == "- **[M1]** `a.py:3-5` — x"
    assert finding_line(bare) == "- **[N1]** PR title says docs-only"
    assert triage_line(TriageEntry("a.py", "**Tier 2**")) == "- `a.py` — **Tier 2**"
    assert triage_line(TriageEntry("", "`a.py`, `b.py` — **Tier 1**")) == "- `a.py`, `b.py` — **Tier 1**"


def test_evidence_renders_as_a_quoted_fence_only_when_asked():
    f = RecordFinding(id="M1", severity="M", path="a.py", line=1, body="x", evidence="a = 1\nb = 2")
    assert finding_line(f) == "- **[M1]** `a.py:1` — x"
    assert finding_line(f, evidence=True).split("\n")[1:] == [
        "  > ```", "  > a = 1", "  > b = 2", "  > ```",
    ]
    assert evidence_block("a") == "  > ```\n  > a\n  > ```"


def test_a_multi_file_location_renders_its_first_file(tmp_path):
    """X1: a schema-1 finding naming two files kept the first as `path`; the rest lived in `lead`."""
    (tmp_path / "review.json").write_text(json.dumps({"schema_version": 1, "findings": [
        {"id": "M1", "severity": "M", "path": "a.py", "lead": "`a.py`, `b.py` — ", "body": "x"},
    ]}))
    (f,) = load_record(tmp_path).findings
    assert finding_line(f) == "- **[M1]** `a.py` — x"


def test_a_ledger_line_renders_id_path_disposition_and_note():
    assert ledger_line(LedgerLine("M1", PriorDisposition.FIXED, "a.py", "moved")) == \
        "- **[M1]** `a.py` — Fixed — moved"
    assert ledger_line(LedgerLine("S2", PriorDisposition.DECLINED)) == "- **[S2]** — Declined"


def test_focus_renders_after_the_summary_requested_first():
    body = render_body(record(
        finding("M1", "bug"),
        summary="looked at locking",
        focus=(
            user_area("Locking"),
            area(id="F2", title="Retries", coverage="checked backoff"),
        ),
    ))
    assert body.index("## Summary") < body.index("## Focus")
    assert body.index("## Focus") < body.index("## Must fix")
    focus = body.split("## Focus", 1)[1].split("## ", 1)[0]
    lines = [ln for ln in focus.splitlines() if ln.startswith("- **[")]
    assert "Locking" in lines[0] and "*(requested)*" in lines[0]
    assert "Retries" in lines[1] and "*(requested)*" not in lines[1]


def test_an_unassessed_area_says_so():
    body = render_body(record(summary="s", focus=(area(title="Locks"),)))
    assert "*Not assessed this round.*" in body


def test_no_focus_renders_no_section():
    assert "## Focus" not in render_body(record(summary="s"))
