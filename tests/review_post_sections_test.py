"""Tests for `review.sections` — how `ReviewSections` reads the non-finding
sections out of a review and orders them around the findings.
"""

import sys
from pathlib import Path

LIB_DIR = Path(__file__).resolve().parent.parent / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))


class TestReviewSections:
    REVIEW_TEXT = (
        "# Review: test-org/test-repo#42 — Fix handler\n"
        "<!-- head_sha: abc123def456 -->\n"
        "\n"
        "## Summary\n"
        "Clean refactor with good test coverage.\n"
        "\n"
        "## Must fix\n"
        "\n"
        "- **[M1]** **`handler.go:11`** — missing error check\n"
        "\n"
        "## Should fix\n"
        "\n"
        "- **[S1]** **`handler.go:25`** — unused variable\n"
        "\n"
        "## Static Analysis\n"
        "\n"
        "### Nesting depth\n"
        "1 violation in 1 of 3 files checked\n"
        "\n"
        "## Verdict\n"
        "\n"
        "Request changes — M1 is a blocker.\n"
    )

    def test_from_text_extracts_known_sections(self, rp):
        sections = rp.ReviewSections.from_text(self.REVIEW_TEXT)
        assert sections.get("summary") == "Clean refactor with good test coverage."
        assert "Request changes" in sections.get("verdict")
        assert "Nesting depth" in sections.get("static_analysis")

    def test_from_text_ignores_severity_headers(self, rp):
        sections = rp.ReviewSections.from_text(self.REVIEW_TEXT)
        assert sections.get("must_fix") == ""
        assert sections.get("should_fix") == ""

    def test_get_returns_empty_for_absent(self, rp):
        sections = rp.ReviewSections.from_text(self.REVIEW_TEXT)
        assert sections.get("nonexistent") == ""

    def test_before_findings_returns_ordered_pairs(self, rp):
        sections = rp.ReviewSections.from_text(self.REVIEW_TEXT)
        before = sections.before_findings()
        keys = [cfg.key for cfg, _ in before]
        assert "summary" in keys
        assert "verdict" in keys
        assert keys.index("summary") < keys.index("verdict")

    def test_after_findings_returns_passthrough(self, rp):
        sections = rp.ReviewSections.from_text(self.REVIEW_TEXT)
        after = sections.after_findings()
        keys = [cfg.key for cfg, _ in after]
        assert "static_analysis" in keys

    def test_empty_constructor(self, rp):
        sections = rp.ReviewSections()
        assert sections.get("summary") == ""
        assert sections.before_findings() == []
        assert sections.after_findings() == []

    def test_auto_discovers_unknown_section(self, rp):
        text = (
            "## Summary\n"
            "Quick summary.\n"
            "\n"
            "## Performance Notes\n"
            "Consider caching the DB query.\n"
            "\n"
            "## Must fix\n"
            "\n"
            "- **[M1]** **`a.go:1`** — bug\n"
        )
        sections = rp.ReviewSections.from_text(text)
        assert sections.get("performance_notes") == "Consider caching the DB query."
        after = sections.after_findings()
        keys = [cfg.key for cfg, _ in after]
        assert "performance_notes" in keys

    def test_file_triage_is_not_extracted(self, rp):
        text = (
            "## File Triage\n"
            "- `a.go` — **Tier 1** (core logic)\n"
            "\n"
            "## Must fix\n"
            "\n"
            "- **[M1]** **`a.go:1`** — bug\n"
        )
        sections = rp.ReviewSections.from_text(text)
        assert sections.get("file_triage") == ""
        assert sections.after_findings() == []

    def test_prior_findings_is_not_extracted(self, rp):
        text = (
            "## Prior findings\n"
            "- **[M4]** `a.go` — Fixed\n"
            "- **[S7]** `b.go` — Still open\n"
            "\n"
            "## Must fix\n"
            "\n"
            "- **[M1]** **`a.go:1`** — bug\n"
        )
        sections = rp.ReviewSections.from_text(text)
        assert sections.get("prior_findings") == ""
        assert sections.after_findings() == []

    def test_before_findings_omits_empty(self, rp):
        text = "## Must fix\n\n- **[M1]** **`a.go:1`** — bug\n"
        sections = rp.ReviewSections.from_text(text)
        assert sections.before_findings() == []

    def test_severity_alias_nits_ignored(self, rp):
        text = "## Nits\n\n- **[N1]** **`a.go:1`** — nit\n"
        sections = rp.ReviewSections.from_text(text)
        assert sections.get("nits") == ""
