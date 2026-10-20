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

from review.artifacts import DisproveArtifact, DisproveOutcome, DisproveVerdict
from review.ingest import ingest_review
from review.record import RecordFinding, ReviewRecord
from review.render import render_body
from review.verify import apply_disprove


# ── What each gate takes out ─────────────────────────────────────────────────
#
# Evidence verification drops findings by cutting spans in the finished
# review. The disprove gate used to share that cut; it now marks FALSIFIED on
# the record and render_body omits them. The table runs both, each against the
# bytes it actually produces, so a reading that drifts in either path fails
# here rather than passing twice on the same function.


def _by_disprove_gate(text: str, ids: list[str]) -> str:
    """What apply_disprove leaves in the rendered body after falsifying `ids`."""
    artifact = DisproveArtifact(verdicts=tuple(
        DisproveVerdict(fid, DisproveOutcome.FALSIFIED, "x") for fid in ids
    ))
    return render_body(apply_disprove(ingest_review(text, strict=False), artifact).record)


GATES = [
    pytest.param(_by_disprove_gate, 1, id="disprove"),
]

DROP_CASES = [
    pytest.param(
        "## Must fix\n"
        "- **[M1]** `a.go:1` — one\n"
        "- a flat bullet continuing the finding\n"
        "- **[M2]** `b.go:2` — two\n",
        ["M1"],
        "## Must fix\n"
        "- **[M2]** `b.go:2` — two\n",
        "## Must fix\n\n"
        "- **[M2]** `b.go:2` — two\n",
        id="a-flat-bullet-is-the-finding's-own-body",
    ),
    pytest.param(
        "## Must fix\n"
        "- **[M1]** `a.go:1` — one\n"
        "- ~~**[M2]** `b.go:2` — resolved~~\n"
        "- **[M3]** `c.go:3` — three\n",
        ["M1"],
        "## Must fix\n"
        "- ~~**[M2]** `b.go:2` — resolved~~\n"
        "- **[M3]** `c.go:3` — three\n",
        "## Must fix\n\n"
        "- ~~**[M2]** `b.go:2` — resolved~~\n"
        "- **[M3]** `c.go:3` — three\n",
        id="a-resolved-finding-below-a-dropped-one-survives",
    ),
    pytest.param(
        "## Must fix\n"
        "### Group A\n"
        "- **[M1]** `a.go:1` — one\n"
        "### Group B\n"
        "- **[M2]** `b.go:2` — two\n",
        ["M1"],
        "## Must fix\n"
        "### Group A\n"
        "### Group B\n"
        "- **[M2]** `b.go:2` — two\n",
        "## Must fix\n\n"
        "### Group A\n"
        "### Group B\n"
        "- **[M2]** `b.go:2` — two\n",
        id="a-sub-heading-below-a-dropped-finding-survives",
    ),
    pytest.param(
        "## Must fix\n"
        "- **[M1]** `a.go:1` — one\n"
        "  - **[M2]** `b.go:2` — nested declaration\n"
        "- **[M3]** `c.go:3` — three\n",
        ["M1"],
        "## Must fix\n"
        "  - **[M2]** `b.go:2` — nested declaration\n"
        "- **[M3]** `c.go:3` — three\n",
        "## Must fix\n\n"
        "- **[M2]** `b.go:2` — nested declaration\n"
        "- **[M3]** `c.go:3` — three\n",
        id="an-indented-declaration-is-a-declaration",
    ),
    pytest.param(
        "## Must fix\n"
        "- **[M1]** `a.go:1` — one\n"
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
        "- **[M1]** `a.go:1` — one\n"
        "  > ```go\n"
        "  > x := 1\n"
        "  > ```\n"
        "\n"
        "- **[M2]** `b.go:2` — two\n",
        ["M1"],
        "## Must fix\n"
        "- **[M2]** `b.go:2` — two\n",
        "## Must fix\n\n"
        "- **[M2]** `b.go:2` — two\n",
        id="an-evidence-block-goes-with-the-finding-that-quotes-it",
    ),
    pytest.param(
        "## Must fix\n"
        "- **[M1]** `a.go:1` — one\n"
        "## Nit\n"
        "- **[N1]** `b.go:2` — two\n",
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
        "- **[M1]** `a.go:1` — one\n",
        ["S9"],
        "## Must fix\n"
        "- **[M1]** `a.go:1` — one\n",
        "## Must fix\n\n"
        "- **[M1]** `a.go:1` — one\n",
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


# ── apply_disprove ──────────────────────────────────────────────────────────


def test_a_falsified_finding_is_kept_but_no_longer_visible():
    record = ReviewRecord(findings=(
        RecordFinding(id="M1", severity="M", path="a.py", line=1, body="x"),
        RecordFinding(id="S1", severity="S", path="a.py", line=2, body="y"),
    ))
    artifact = DisproveArtifact(verdicts=(
        DisproveVerdict("M1", DisproveOutcome.SURVIVES, "real"),
        DisproveVerdict("S1", DisproveOutcome.FALSIFIED, "handled at b.py:3"),
    ))
    applied = apply_disprove(record, artifact)
    assert [f.id for f in applied.record.visible] == ["M1"]
    assert applied.summary["falsified_ids"] == ["S1"]
    assert applied.summary["reasons"] == {"S1": "handled at b.py:3"}


def test_a_verdict_for_a_finding_the_review_lacks_changes_nothing():
    record = ReviewRecord(findings=(RecordFinding(id="M1", severity="M", body="x"),))
    artifact = DisproveArtifact(verdicts=(DisproveVerdict("S9", DisproveOutcome.FALSIFIED, "r"),))
    assert apply_disprove(record, artifact).record.visible == record.visible
