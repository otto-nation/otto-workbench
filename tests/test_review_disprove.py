"""Tests for review_disprove: adversarial falsification parsing and application."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "claude" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from review_disprove import DisproveResult, apply_disprove_results, parse_disprove_output


# ── parse_disprove_output ────────────────────────────────────────────────────


DISPROVE_OUTPUT = """\
## Disprove Results

- [M1] SURVIVES — confirmed: no nil check before deref at handler.go:42
- [M2] FALSIFIED — the error is handled in the caller at service.go:88
- [S1] SURVIVES — timeout not set, could hang indefinitely
- [S2] FALSIFIED — deprecated API was replaced in the same PR, see diff line 204
"""


class TestParseDisproveOutput:
    def test_parses_all_results(self):
        results = parse_disprove_output(DISPROVE_OUTPUT)
        assert len(results) == 4

    def test_survives_verdict(self):
        results = parse_disprove_output(DISPROVE_OUTPUT)
        assert results[0].finding_id == "M1"
        assert results[0].verdict == "SURVIVES"
        assert "nil check" in results[0].reason

    def test_falsified_verdict(self):
        results = parse_disprove_output(DISPROVE_OUTPUT)
        assert results[1].finding_id == "M2"
        assert results[1].verdict == "FALSIFIED"
        assert "caller" in results[1].reason

    def test_empty_input(self):
        assert parse_disprove_output("") == []

    def test_no_matching_lines(self):
        assert parse_disprove_output("some random text\nno findings here\n") == []

    def test_double_dash_separator(self):
        text = "- [M1] SURVIVES -- reason here\n"
        results = parse_disprove_output(text)
        assert len(results) == 1
        assert results[0].reason == "reason here"


# ── apply_disprove_results ───────────────────────────────────────────────────


REVIEW_TEXT = """\
## Must fix

- [ ] **[M1]** Nil pointer dereference at handler.go:42
  The handler does not check for nil before calling `.Process()`.

  **Evidence:**
  ```go
  func Handle(r *Request) { r.Process() }
  ```

- [ ] **[M2]** Error ignored in database query
  The return value of `db.Query()` is discarded.

## Should fix

- [ ] **[S1]** No timeout on HTTP client
  The default client has no timeout, which could cause hangs.

- [ ] **[S2]** Using deprecated API
  `OldMethod()` is marked deprecated since v2.0.

## Nit

- [ ] **[N1]** Variable naming: `x` should be `count`
"""


class TestApplyDisproveResults:
    def test_removes_falsified_findings(self):
        results = [
            DisproveResult("M2", "FALSIFIED", "handled in caller"),
            DisproveResult("S2", "FALSIFIED", "replaced in same PR"),
        ]
        new_text, stats = apply_disprove_results(REVIEW_TEXT, results)
        assert "**[M1]**" in new_text
        assert "**[M2]**" not in new_text
        assert "**[S1]**" in new_text
        assert "**[S2]**" not in new_text
        assert "**[N1]**" in new_text

    def test_removes_multiline_body(self):
        results = [DisproveResult("M1", "FALSIFIED", "not real")]
        new_text, _ = apply_disprove_results(REVIEW_TEXT, results)
        assert "**[M1]**" not in new_text
        assert "r.Process()" not in new_text
        assert "**[M2]**" in new_text

    def test_all_survive(self):
        results = [
            DisproveResult("M1", "SURVIVES", "confirmed"),
            DisproveResult("M2", "SURVIVES", "confirmed"),
        ]
        new_text, stats = apply_disprove_results(REVIEW_TEXT, results)
        assert new_text == REVIEW_TEXT
        assert stats["survived"] == 2
        assert stats["falsified"] == 0

    def test_all_falsified(self):
        results = [
            DisproveResult("M1", "FALSIFIED", "r1"),
            DisproveResult("M2", "FALSIFIED", "r2"),
            DisproveResult("S1", "FALSIFIED", "r3"),
            DisproveResult("S2", "FALSIFIED", "r4"),
        ]
        new_text, stats = apply_disprove_results(REVIEW_TEXT, results)
        assert "**[M1]**" not in new_text
        assert "**[M2]**" not in new_text
        assert "**[S1]**" not in new_text
        assert "**[S2]**" not in new_text
        assert "**[N1]**" in new_text
        assert stats["falsified"] == 4

    def test_empty_results(self):
        new_text, stats = apply_disprove_results(REVIEW_TEXT, [])
        assert new_text == REVIEW_TEXT
        assert stats["total_challenged"] == 0

    def test_stats_structure(self):
        results = [
            DisproveResult("M1", "SURVIVES", "confirmed"),
            DisproveResult("M2", "FALSIFIED", "not real"),
            DisproveResult("S1", "SURVIVES", "confirmed"),
        ]
        _, stats = apply_disprove_results(REVIEW_TEXT, results)
        assert stats["total_challenged"] == 3
        assert stats["survived"] == 2
        assert stats["falsified"] == 1
        assert "M2" in stats["falsified_ids"]
        assert stats["reasons"]["M2"] == "not real"

    def test_section_headers_preserved(self):
        results = [DisproveResult("M1", "FALSIFIED", "x")]
        new_text, _ = apply_disprove_results(REVIEW_TEXT, results)
        assert "## Must fix" in new_text
        assert "## Should fix" in new_text
        assert "## Nit" in new_text

    def test_finding_without_checkbox(self):
        review = "## Must fix\n\n- **[M1]** Simple finding\n  Detail line.\n"
        results = [DisproveResult("M1", "FALSIFIED", "not real")]
        new_text, stats = apply_disprove_results(review, results)
        assert "**[M1]**" not in new_text
        assert stats["falsified"] == 1
