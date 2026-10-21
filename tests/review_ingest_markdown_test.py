"""Markdown-only ingest subjects parked here while conversion tasks drop ingest.

Task 15 deletes this file with markdown ingest. Original bodies stay as they
were in the suite they moved from — these tests exist to hold parse quirks,
not the record builders.
"""

import sys
from pathlib import Path

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import review.ingest


class TestParseCheckboxState:
    def test_unchecked_finding(self):
        text = "## Must fix\n- [ ] **[M1]** **`file.go:10`** — Bug found\n"
        findings = [f.view() for f in review.ingest.ingest_review(text, strict=False).visible]
        assert len(findings) == 1
        assert findings[0].checked is False

    def test_checked_finding(self):
        text = "## Must fix\n- [x] **[M1]** **`file.go:10`** — Bug fixed\n"
        findings = [f.view() for f in review.ingest.ingest_review(text, strict=False).visible]
        assert len(findings) == 1
        assert findings[0].checked is True

    def test_no_checkbox_finding(self):
        text = "## Must fix\n- **[M1]** **`file.go:10`** — Bug found\n"
        findings = [f.view() for f in review.ingest.ingest_review(text, strict=False).visible]
        assert len(findings) == 1
        assert findings[0].checked is False

    def test_mixed_checkbox_states(self):
        text = (
            "## Must fix\n"
            "- [x] **[M1]** **`a.go:1`** — Fixed\n"
            "- [ ] **[M2]** **`b.go:2`** — Not fixed\n"
            "## Nit\n"
            "- [x] **[N1]** **`c.go:3`** — Also fixed\n"
        )
        findings = [f.view() for f in review.ingest.ingest_review(text, strict=False).visible]
        assert len(findings) == 3
        by_id = {f.id: f for f in findings}
        assert by_id["M1"].checked is True
        assert by_id["M2"].checked is False
        assert by_id["N1"].checked is True


class TestParseDeclinedFindings:
    """`*(declined — reason)*` is where an adjudicated verdict survives.

    The `## Prior findings` ledger is stripped before the review file is
    finished, so a decline recorded only there would reach the next fix pass
    looking like an ordinary open finding.
    """

    def test_reads_the_reason_off_the_line(self):
        text = (
            "## Must fix\n"
            "- [ ] **[M1]** `a.go:1` — *(declined — documented `ceiling:` tradeoff)* "
            "— Global lock serialises writes\n"
        )
        findings = [f.view() for f in review.ingest.ingest_review(text, strict=False).visible]
        assert findings[0].declined is True
        assert findings[0].decline_reason == "documented `ceiling:` tradeoff"

    def test_a_decline_without_a_reason_still_registers(self):
        text = "## Must fix\n- [ ] **[M1]** `a.go:1` — *(declined)* — Body\n"
        findings = [f.view() for f in review.ingest.ingest_review(text, strict=False).visible]
        assert findings[0].declined is True
        assert findings[0].decline_reason == ""

    def test_a_trailing_annotation_registers(self):
        """The templates also let the annotation close the line."""
        text = (
            "## Must fix\n"
            "- [ ] **[M1]** `a.go:1` — Global lock *(declined — by design)*\n"
        )
        findings = [f.view() for f in review.ingest.ingest_review(text, strict=False).visible]
        assert findings[0].declined is True
        assert findings[0].decline_reason == "by design"

    def test_a_finding_that_only_describes_the_annotation_is_not_declined(self):
        """Reviewing this parser writes the annotation into a finding's prose.

        Read as a decline, the finding leaves `run_fix_pass`'s work set
        permanently, and nothing warns that it did.
        """
        text = (
            "## Must fix\n"
            "- [ ] **[M1]** `review_document.py:99` — The `*(declined — reason)*` "
            "annotation is matched anywhere in the line, so prose trips it\n"
        )
        findings = [f.view() for f in review.ingest.ingest_review(text, strict=False).visible]
        assert findings[0].declined is False
        assert findings[0].decline_reason == ""

    def test_a_skip_is_not_a_decline(self):
        """A skip is work deferred; a decline is work rejected."""
        text = "## Must fix\n- [ ] **[M1]** `a.go:1` — *(skipped — needs design)* — Body\n"
        findings = [f.view() for f in review.ingest.ingest_review(text, strict=False).visible]
        assert findings[0].declined is False

    def test_a_file_without_declines_parses_unchanged(self):
        """Review files predating `Declined` must keep parsing."""
        text = (
            "## Must fix\n"
            "- [x] **[M1]** `a.go:1` — Fixed\n"
            "- [ ] **[M2]** `b.go:2` — Still open\n"
        )
        findings = [f.view() for f in review.ingest.ingest_review(text, strict=False).visible]
        assert [f.declined for f in findings] == [False, False]
        assert [f.decline_reason for f in findings] == ["", ""]
