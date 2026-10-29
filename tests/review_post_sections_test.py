"""Tests for `review.sections` — how `ReviewSections` reads the non-finding
sections out of a review and orders them around the findings.
"""

import sys
from pathlib import Path

LIB_DIR = Path(__file__).resolve().parent.parent / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

# The module the `rp` fixture returns, imported so bin/local/select-pytest maps
# this suite to the code it reaches through `rp`.
import cli.review_post  # noqa: E402,F401
from review.format import format_body_text  # noqa: E402
from review.render import focus_comment  # noqa: E402
from review.record import LedgerLine, ProseSection, TriageEntry  # noqa: E402
from review.types import PriorDisposition  # noqa: E402

from review.record import FocusSource  # noqa: E402
from review_focus_support import area, user_area  # noqa: E402
from review_record_support import finding, record  # noqa: E402


def _sections(rp, rec):
    return rp.ReviewSections.from_record(rec)


class TestReviewSections:
    REVIEW = record(
        finding("M1", "missing error check", path="handler.go", line=11),
        finding("S1", "unused variable", path="handler.go", line=25),
        summary="Clean refactor with good test coverage.",
        verdict="Request changes — M1 is a blocker.",
        static_analysis="### Nesting depth\n1 violation in 1 of 3 files checked",
    )

    def test_from_record_extracts_known_sections(self, rp):
        sections = _sections(rp, self.REVIEW)
        assert sections.get("summary") == "Clean refactor with good test coverage."
        assert "Request changes" in sections.get("verdict")
        assert "Nesting depth" in sections.get("static_analysis")

    def test_from_record_ignores_severity_headers(self, rp):
        sections = _sections(rp, self.REVIEW)
        assert sections.get("must_fix") == ""
        assert sections.get("should_fix") == ""

    def test_get_returns_empty_for_absent(self, rp):
        sections = _sections(rp, self.REVIEW)
        assert sections.get("nonexistent") == ""

    def test_before_findings_returns_ordered_pairs(self, rp):
        sections = _sections(rp, self.REVIEW)
        before = sections.before_findings()
        keys = [cfg.key for cfg, _ in before]
        assert "summary" in keys
        assert "verdict" in keys
        assert keys.index("summary") < keys.index("verdict")

    def test_after_findings_returns_passthrough(self, rp):
        sections = _sections(rp, self.REVIEW)
        after = sections.after_findings()
        keys = [cfg.key for cfg, _ in after]
        assert "static_analysis" in keys

    def test_empty_constructor(self, rp):
        sections = rp.ReviewSections()
        assert sections.get("summary") == ""
        assert sections.before_findings() == []
        assert sections.after_findings() == []

    def test_auto_discovers_unknown_section(self, rp):
        rec = record(
            finding("M1", "bug", path="a.go", line=1),
            summary="Quick summary.",
            extra=(ProseSection("Performance Notes", "Consider caching the DB query."),),
        )
        sections = _sections(rp, rec)
        assert sections.get("performance_notes") == "Consider caching the DB query."
        after = sections.after_findings()
        keys = [cfg.key for cfg, _ in after]
        assert "performance_notes" in keys

    def test_file_triage_is_not_extracted(self, rp):
        rec = record(
            finding("M1", "bug", path="a.go", line=1),
            triage=(TriageEntry("a.go", "**Tier 1** (core logic)"),),
        )
        sections = _sections(rp, rec)
        assert sections.get("file_triage") == ""
        assert sections.after_findings() == []

    def test_prior_findings_is_not_extracted(self, rp):
        rec = record(
            finding("M1", "bug", path="a.go", line=1),
            ledger=(
                LedgerLine("M4", PriorDisposition.FIXED, "a.go"),
                LedgerLine("S7", PriorDisposition.STILL_OPEN, "b.go"),
            ),
        )
        sections = _sections(rp, rec)
        assert sections.get("prior_findings") == ""
        assert sections.after_findings() == []

    def test_before_findings_omits_empty(self, rp):
        rec = record(finding("M1", "bug", path="a.go", line=1))
        sections = _sections(rp, rec)
        assert sections.before_findings() == []

    def test_the_comment_carries_focus_under_the_summary(self, rp):
        rec = record(summary="looked", focus=(user_area("Locking"),))
        body = format_body_text([], False, set(), rp.ReviewSections.from_record(rec))
        assert body.index("## Summary") < body.index("### Focus")
        assert "Locking" in body
        assert body.index("### Focus") < body.index("---")

    def test_the_comment_focus_line_is_title_mark_and_coverage(self, rp):
        asked = area(title="Locking", source=FocusSource.USER, coverage="checked the mutex")
        rec = record(summary="looked", focus=(asked,))
        body = format_body_text([], False, set(), rp.ReviewSections.from_record(rec))
        assert "- **Locking** *(requested)* — checked the mutex" in body.splitlines()

    def test_multiline_comment_coverage_stays_one_list_item(self):
        text = focus_comment([area(title="Locks", coverage="a\n\nb")])
        line_b = next(ln for ln in text.splitlines() if ln.strip() == "b")
        assert line_b.startswith("  ")
