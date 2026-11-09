"""Tests for the comment's non-finding sections and their order, as
`review.render.comment_body` writes them.
"""

import sys
from pathlib import Path

import pytest

LIB_DIR = Path(__file__).resolve().parent.parent / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

# The module the `rp` fixture returns, imported so bin/local/select-pytest maps
# this suite to the code it reaches through `rp`.
import cli.review_post  # noqa: E402,F401
import review.dedup  # noqa: E402
import review.render  # noqa: E402
from review.document import ReviewDocument  # noqa: E402
from review.format import format_body_text, renumber_for_posting  # noqa: E402
from review.record import (  # noqa: E402
    AgentFailure, Disposition, FocusSource, LedgerLine, ProseSection, TriageEntry,
)
from review.sections import ReviewSections  # noqa: E402
from review.types import PriorDisposition, SEVERITIES  # noqa: E402

from review_behaviours_support import behaviour  # noqa: E402
from review_claims_support import claim  # noqa: E402
from review_focus_support import area, user_area  # noqa: E402
from review_record_support import finding, fixture_record, record  # noqa: E402


def _body_findings(rec):
    return renumber_for_posting([], ReviewDocument(record=rec).open_findings)[1]


def _comment(rec, findings=(), *, has_inline=False, severity_filter=None):
    return review.render.comment_body(
        rec, list(findings), has_inline=has_inline,
        severity_filter=set() if severity_filter is None else severity_filter)


RICH = record(
    finding("M1", "lock never released", path="a.py", line=3),
    finding("S1", "unclear name", path="b.py", line=9),
    finding("S2", "use a constant", path="b.py", line=12, disposition=Disposition.DECLINED),
    finding("N1", "trailing space", path="c.py", line=1),
    finding("I1", "pattern holds", path=""),
    summary="looked at locking.", verdict="Request changes — M1 blocks.",
    focus=(user_area("Locking"),), claims=(claim(),), behaviours=(behaviour(),),
    failures=(AgentFailure("disprove", "timed out", "failed", recoverable=True),),
    extra=(ProseSection("Review gaps", "- group 2"),),
    static_analysis="### Nesting depth\n1 violation in 1 of 3 files checked",
)

_FIXTURES = (
    "clean", "evidence_dropped", "fix_annotated", "mechanical_fallback",
    "rebuild", "self_incremental_fixed", "synthesis_pr", "unchanged",
)


class TestReviewSections:
    REVIEW = record(
        finding("M1", "missing error check", path="handler.go", line=11),
        finding("S1", "unused variable", path="handler.go", line=25),
        summary="Clean refactor with good test coverage.",
        verdict="Request changes — M1 is a blocker.",
        static_analysis="### Nesting depth\n1 violation in 1 of 3 files checked",
    )

    def test_from_record_ignores_severity_headers(self, rp):
        sections = rp.ReviewSections.from_record(self.REVIEW)
        assert sections.get("must_fix") == ""
        assert sections.get("should_fix") == ""

    def test_get_returns_empty_for_absent(self, rp):
        assert rp.ReviewSections.from_record(self.REVIEW).get("nonexistent") == ""

    def test_after_findings_returns_passthrough(self, rp):
        keys = [cfg.key for cfg, _ in rp.ReviewSections.from_record(self.REVIEW).after_findings()]
        assert "static_analysis" in keys

    def test_empty_constructor(self, rp):
        sections = rp.ReviewSections()
        assert sections.get("summary") == ""
        assert sections.before_findings() == []
        assert sections.after_findings() == []

    def test_before_findings_omits_empty(self, rp):
        rec = record(finding("M1", "bug", path="a.go", line=1))
        assert rp.ReviewSections.from_record(rec).before_findings() == []


def test_the_comment_carries_summary_verdict_and_static_analysis():
    body = _comment(TestReviewSections.REVIEW)
    assert "Clean refactor with good test coverage." in body
    assert "M1 is a blocker." in body
    assert "Nesting depth" in body


def test_the_summary_comes_before_the_verdict():
    body = _comment(TestReviewSections.REVIEW)
    assert body.index("## Summary") < body.index("### Verdict")


def test_the_comment_carries_extra_sections_after_agent_failures():
    rec = record(
        finding("M1", "bug", path="a.go", line=1), summary="Quick summary.",
        failures=(AgentFailure("disprove", "timed out", "failed"),),
        extra=(
            ProseSection("Performance Notes", "Consider caching the DB query."),
            ProseSection("Must fix", "not dropped"),
        ),
    )
    body = _comment(rec)
    assert "Consider caching the DB query." in body and "not dropped" in body
    assert body.index("## Agent Failures") < body.index("## Performance Notes")
    assert body.index("## Performance Notes") < body.index("## Must fix")


def test_the_comment_omits_file_triage():
    rec = record(
        finding("M1", "bug", path="a.go", line=1), summary="looked",
        triage=(TriageEntry("a.go", "**Tier 1** (core logic)"),),
    )
    body = _comment(rec)
    assert "File Triage" not in body and "Tier 1" not in body


def test_the_comment_omits_the_prior_findings_ledger():
    rec = record(
        finding("M1", "bug", path="a.go", line=1), summary="looked",
        ledger=(
            LedgerLine("M4", PriorDisposition.FIXED, "a.go"),
            LedgerLine("S7", PriorDisposition.STILL_OPEN, "b.go"),
        ),
    )
    assert "Prior findings" not in _comment(rec)


def test_the_comment_carries_claims_under_the_summary():
    body = _comment(record(summary="looked", claims=(claim(),)))
    assert body.index("## Summary") < body.index("### Claims") < body.index("---")
    assert "**supported**" in body and "a.py" not in body and "[C1]" not in body


def test_the_comment_carries_test_evidence():
    body = _comment(record(summary="looked", behaviours=(behaviour(),)))
    assert body.index("## Summary") < body.index("### Test evidence") < body.index("---")
    assert "[B1]" not in body


def test_the_comment_carries_unassessed_claims():
    body = _comment(record(claims=None))
    assert "### Claims" in body
    assert "Not assessed" in body.split("### Claims", 1)[1]


def test_the_comment_carries_unassessed_test_evidence():
    body = _comment(record(behaviours=None))
    assert "### Test evidence" in body
    assert "Not assessed" in body.split("### Test evidence", 1)[1]


def test_the_comment_omits_empty_test_evidence():
    assert "### Test evidence" not in _comment(record(behaviours=()))


def test_the_comment_carries_focus_under_the_summary():
    body = _comment(record(summary="looked", focus=(user_area("Locking"),)))
    assert body.index("## Summary") < body.index("### Focus") < body.index("---")
    assert "Locking" in body


def test_the_comment_focus_line_is_title_mark_and_coverage():
    asked = area(title="Locking", source=FocusSource.USER, coverage="checked the mutex")
    body = _comment(record(summary="looked", focus=(asked,)))
    assert "- **Locking** *(requested)* — checked the mutex" in body.splitlines()


def test_multiline_comment_coverage_stays_one_list_item():
    text = review.render.focus_comment([area(title="Locks", coverage="a\n\nb")])
    line_b = next(ln for ln in text.splitlines() if ln.strip() == "b")
    assert line_b.startswith("  ")


def test_the_comment_sections_run_in_posted_order():
    body = _comment(
        RICH, _body_findings(RICH), has_inline=True,
        severity_filter={s.key for s in SEVERITIES},
    )
    markers = (
        "## Summary", "### Focus", "### Claims", "### Test evidence",
        "### Verdict", "---", "Have some comments marked as",
        "**Findings outside the diff:**", "### Must fix", "### Should fix",
        "<summary>Nit (1)</summary>", "<summary>Idioms (1)</summary>",
        "**Declined — considered and not carried forward:**",
        "### Nesting depth", "## Agent Failures", "## Review gaps",
    )
    idxs = [body.index(m) for m in markers]
    assert idxs == sorted(idxs)
    assert "[C1]" not in body and "[B1]" not in body and "[F1]" not in body
    assert "File Triage" not in body and "Prior findings" not in body
    assert "Request changes" not in body and "M1 blocks." in body


@pytest.mark.parametrize("case", [*_FIXTURES, "rich"])
@pytest.mark.parametrize("has_inline", [False, True], ids=["no-inline", "inline"])
def test_the_record_renderer_keeps_the_posted_word_set(case, has_inline):
    """Transitional: deleted with `format_body_text` in the same PR. The already-posted guard compares these word sets."""
    rec = RICH if case == "rich" else fixture_record(case)
    findings = _body_findings(rec)
    filt = {s.key for s in SEVERITIES}
    new = review.render.comment_body(rec, findings, has_inline=has_inline, severity_filter=filt)
    old = format_body_text(findings, has_inline, filt, ReviewSections.from_record(rec))
    assert review.dedup.word_set(new) == review.dedup.word_set(old)
