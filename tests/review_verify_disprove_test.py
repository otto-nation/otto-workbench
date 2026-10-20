"""Tests for the disprove gate, and for what either gate leaves behind when it
drops findings from a finished review.

The disprove gate is tested by reading an agent's verdicts back and applying
them; evidence verification still cuts spans. The table below runs the same
cases through each production path.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import review.spans
from review.ingest import ingest_review
from review.render import render_body
from review.verify import DisproveResult, apply_disprove, parse_disprove_output


# ── What each gate takes out ─────────────────────────────────────────────────
#
# Evidence verification drops findings by cutting spans in the finished
# review. The disprove gate used to share that cut; it now marks FALSIFIED on
# the record and render_body omits them. The table runs both, each against the
# bytes it actually produces, so a reading that drifts in either path fails
# here rather than passing twice on the same function.


def _by_evidence_gate(text: str, ids: list[str]) -> str:
    """What evidence verification leaves behind when it drops `ids`."""
    return review.spans.drop_findings(text, ids)


def _by_disprove_gate(text: str, ids: list[str]) -> str:
    """What apply_disprove leaves in the rendered body after falsifying `ids`."""
    results = [DisproveResult(fid, "FALSIFIED", "x") for fid in ids]
    return render_body(apply_disprove(ingest_review(text, strict=False), results).record)


GATES = [
    pytest.param(_by_evidence_gate, 0, id="evidence"),
    pytest.param(_by_disprove_gate, 1, id="disprove"),
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
        "## Must fix\n\n"
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
        "## Must fix\n\n"
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
        "## Must fix\n\n"
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
        "## Must fix\n\n"
        "- **[M2]** **`b.go:2`** — nested declaration\n"
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
        "## Must fix\n\n"
        "## Prior findings\n\n"
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
        "## Must fix\n\n"
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
        "## Must fix\n\n"
        "## Nit\n",
        id="every-named-finding-goes-at-once",
    ),
    pytest.param(
        "## Must fix\n"
        "- **[M1]** **`a.go:1`** — one\n",
        ["S9"],
        "## Must fix\n"
        "- **[M1]** **`a.go:1`** — one\n",
        "## Must fix\n\n"
        "- **[M1]** **`a.go:1`** — one\n",
        id="an-id-the-review-does-not-declare-changes-nothing",
    ),
]


@pytest.mark.parametrize("gate,expected_idx", GATES)
@pytest.mark.parametrize("text,ids,evidence_expected,disprove_expected", DROP_CASES)
class TestEachGateLeavesExactlyThis:
    def test_the_gate_leaves_exactly_this(
        self, gate, expected_idx, text, ids, evidence_expected, disprove_expected,
    ):
        expected = (evidence_expected, disprove_expected)[expected_idx]
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


# ── apply_disprove ──────────────────────────────────────────────────────────


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


class TestApplyDisprove:
    def _apply(self, results, text=REVIEW_TEXT):
        return apply_disprove(ingest_review(text, strict=False), results)

    def test_removes_falsified_findings(self):
        applied = self._apply([
            DisproveResult("M2", "FALSIFIED", "handled in caller"),
            DisproveResult("S2", "FALSIFIED", "replaced in same PR"),
        ])
        assert applied.record.finding("M1") is not None
        assert applied.record.finding("M2") is None
        assert applied.record.finding("S1") is not None
        assert applied.record.finding("S2") is None
        assert applied.record.finding("N1") is not None
        assert applied.summary["falsified"] == 2

    def test_removes_multiline_body(self):
        applied = self._apply([DisproveResult("M1", "FALSIFIED", "not real")])
        assert applied.record.finding("M1") is None
        assert applied.record.finding("M2") is not None
        assert applied.summary["falsified"] == 1

    def test_all_survive(self):
        applied = self._apply([
            DisproveResult("M1", "SURVIVES", "confirmed"),
            DisproveResult("M2", "SURVIVES", "confirmed"),
        ])
        assert applied.summary["survived"] == 2
        assert applied.summary["falsified"] == 0
        assert applied.summary["falsified_ids"] == []
        assert applied.record.finding("M1") is not None

    def test_all_falsified(self):
        applied = self._apply([
            DisproveResult("M1", "FALSIFIED", "r1"),
            DisproveResult("M2", "FALSIFIED", "r2"),
            DisproveResult("S1", "FALSIFIED", "r3"),
            DisproveResult("S2", "FALSIFIED", "r4"),
        ])
        for fid in ("M1", "M2", "S1", "S2"):
            assert applied.record.finding(fid) is None
        assert applied.record.finding("N1") is not None
        assert applied.summary["falsified"] == 4

    def test_empty_results(self):
        applied = self._apply([])
        assert applied.summary["total_challenged"] == 0
        assert applied.summary["falsified"] == 0

    def test_stats_structure(self):
        applied = self._apply([
            DisproveResult("M1", "SURVIVES", "confirmed"),
            DisproveResult("M2", "FALSIFIED", "not real"),
            DisproveResult("S1", "SURVIVES", "confirmed"),
        ])
        stats = applied.summary
        assert stats["total_challenged"] == 3
        assert stats["survived"] == 2
        assert stats["falsified"] == 1
        assert "M2" in stats["falsified_ids"]
        assert stats["reasons"]["M2"] == "not real"

    def test_section_headers_preserved(self):
        applied = self._apply([DisproveResult("M1", "FALSIFIED", "x")])
        assert applied.record.finding("M2") is not None
        assert applied.record.finding("S1") is not None
        assert applied.record.finding("N1") is not None

    def test_finding_without_checkbox(self):
        applied = self._apply(
            [DisproveResult("M1", "FALSIFIED", "not real")],
            "## Must fix\n\n- **[M1]** Simple finding\n  Detail line.\n",
        )
        assert applied.record.finding("M1") is None
        assert applied.summary["falsified"] == 1
