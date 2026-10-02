"""Tests for the disprove gate, and for what either gate leaves behind when it
drops findings from a finished review.

The disprove gate is tested by reading an agent's verdicts back and applying
them; the span both gates cut is tested by running one table through each.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import review.spans
from review.verify import DisproveResult, apply_disprove_results, parse_disprove_output


# ── What both gates take out ─────────────────────────────────────────────────
#
# Evidence verification and the disprove gate each drop findings from a
# finished review, and they used to measure a finding's body for themselves.
# The table below is run through both, asserting the same bytes each time, so a
# reading that drifts into one of them fails here rather than passing twice.


def _by_evidence_gate(text: str, ids: list[str]) -> str:
    """What evidence verification leaves behind when it drops `ids`."""
    return review.spans.drop_findings(text, ids)


def _by_disprove_gate(text: str, ids: list[str]) -> str:
    """What the disprove gate leaves behind when `ids` are falsified."""
    results = [DisproveResult(fid, "FALSIFIED", "challenged") for fid in ids]
    return apply_disprove_results(text, results)[0]


GATES = [
    pytest.param(_by_evidence_gate, id="evidence"),
    pytest.param(_by_disprove_gate, id="disprove"),
]

DROP_CASES = [
    pytest.param(
        "## Must fix\n"
        "- **[M1]** **`a.go:1`** — one\n"
        "- a flat bullet continuing the finding\n"
        "- **[M2]** **`b.go:2`** — two\n",
        ["M1"],
        "## Must fix\n"
        "- **[M2]** **`b.go:2`** — two\n",
        id="a-flat-bullet-is-the-finding's-own-body",
    ),
    pytest.param(
        "## Must fix\n"
        "- **[M1]** **`a.go:1`** — one\n"
        "- ~~**[M2]** **`b.go:2`** — resolved~~\n"
        "- **[M3]** **`c.go:3`** — three\n",
        ["M1"],
        "## Must fix\n"
        "- ~~**[M2]** **`b.go:2`** — resolved~~\n"
        "- **[M3]** **`c.go:3`** — three\n",
        id="a-resolved-finding-below-a-dropped-one-survives",
    ),
    pytest.param(
        "## Must fix\n"
        "### Group A\n"
        "- **[M1]** **`a.go:1`** — one\n"
        "### Group B\n"
        "- **[M2]** **`b.go:2`** — two\n",
        ["M1"],
        "## Must fix\n"
        "### Group A\n"
        "### Group B\n"
        "- **[M2]** **`b.go:2`** — two\n",
        id="a-sub-heading-below-a-dropped-finding-survives",
    ),
    pytest.param(
        "## Must fix\n"
        "- **[M1]** **`a.go:1`** — one\n"
        "  - **[M2]** **`b.go:2`** — nested declaration\n"
        "- **[M3]** **`c.go:3`** — three\n",
        ["M1"],
        "## Must fix\n"
        "  - **[M2]** **`b.go:2`** — nested declaration\n"
        "- **[M3]** **`c.go:3`** — three\n",
        id="an-indented-declaration-is-a-declaration",
    ),
    pytest.param(
        "## Must fix\n"
        "- **[M1]** **`a.go:1`** — one\n"
        "## Prior findings\n"
        "- **[M1]** `old.go` — Fixed\n",
        ["M1"],
        "## Must fix\n"
        "## Prior findings\n"
        "- **[M1]** `old.go` — Fixed\n",
        id="a-ledger-entry-whose-id-collides-is-left-alone",
    ),
    pytest.param(
        "## Must fix\n"
        "- **[M1]** **`a.go:1`** — one\n"
        "  > ```go\n"
        "  > x := 1\n"
        "  > ```\n"
        "\n"
        "- **[M2]** **`b.go:2`** — two\n",
        ["M1"],
        "## Must fix\n"
        "- **[M2]** **`b.go:2`** — two\n",
        id="an-evidence-block-goes-with-the-finding-that-quotes-it",
    ),
    pytest.param(
        "## Must fix\n"
        "- **[M1]** **`a.go:1`** — one\n"
        "## Nit\n"
        "- **[N1]** **`b.go:2`** — two\n",
        ["M1", "N1"],
        # The document's last finding owns the blank line closing the file, so
        # a review whose last finding goes loses its trailing newline with it.
        "## Must fix\n"
        "## Nit",
        id="every-named-finding-goes-at-once",
    ),
    pytest.param(
        "## Must fix\n"
        "- **[M1]** **`a.go:1`** — one\n",
        ["S9"],
        "## Must fix\n"
        "- **[M1]** **`a.go:1`** — one\n",
        id="an-id-the-review-does-not-declare-changes-nothing",
    ),
]


@pytest.mark.parametrize("gate", GATES)
@pytest.mark.parametrize("text,ids,expected", DROP_CASES)
class TestBothGatesCutTheSameSpan:
    def test_the_gate_leaves_exactly_this(self, gate, text, ids, expected):
        assert gate(text, ids) == expected


class TestRemoveDroppedFindings:
    def test_remove_single(self):
        text = (
            "## Must fix\n"
            "- **[M1]** **`file.go:1`** — issue one\n"
            "- **[M2]** **`file.go:5`** — issue two\n"
        )
        result = _by_evidence_gate(text, ["M1"])
        assert "issue one" not in result
        assert "issue two" in result

    def test_remove_with_continuation(self):
        text = (
            "- **[M1]** **`file.go:1`** — issue one\n"
            "  continuation line\n"
            "  more detail\n"
            "- **[M2]** **`file.go:5`** — issue two\n"
        )
        result = _by_evidence_gate(text, ["M1"])
        assert "issue one" not in result
        assert "continuation line" not in result
        assert "issue two" in result

    def test_empty_dropped_list(self):
        text = "- **[M1]** **`file.go:1`** — issue\n"
        assert _by_evidence_gate(text, []) == text

    def test_remove_last_finding(self):
        text = (
            "## Must fix\n"
            "- **[M1]** **`file.go:1`** — only finding\n"
        )
        result = _by_evidence_gate(text, ["M1"])
        assert "only finding" not in result
        assert "## Must fix" in result


# ── Disprove-it gate ─────────────────────────────────────────────────────────


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
