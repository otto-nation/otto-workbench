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
from review.record import LedgerLine, RecordFinding, ReviewRecord, SeveritySection
from review.render import render_body
from review.types import PriorDisposition
from review.verify import apply_disprove

from review_record_support import finding, record


# ── What each gate takes out ─────────────────────────────────────────────────
#
# Evidence verification drops findings by cutting spans in the finished
# review. The disprove gate used to share that cut; it now marks FALSIFIED on
# the record and render_body omits them. The table runs both, each against the
# bytes it actually produces, so a reading that drifts in either path fails
# here rather than passing twice on the same function.


def _by_disprove_gate(rec, ids: list[str]) -> str:
    """What apply_disprove leaves in the rendered body after falsifying `ids`."""
    artifact = DisproveArtifact(verdicts=tuple(
        DisproveVerdict(fid, DisproveOutcome.FALSIFIED, "x") for fid in ids
    ))
    return render_body(apply_disprove(rec, artifact).record)


GATES = [
    pytest.param(_by_disprove_gate, 1, id="disprove"),
]

DROP_CASES = [
    pytest.param(
        record(
            finding("M1", "one\n- a flat bullet continuing the finding", path="a.go"),
            finding("M2", "two", path="b.go", line=2),
            severity_sections=(SeveritySection("M"),),
        ),
        ["M1"],
        "## Must fix\n"
        "- **[M2]** `b.go:2` — two\n",
        "## Must fix\n\n"
        "- **[M2]** `b.go:2` — two\n",
        id="a-flat-bullet-is-the-finding's-own-body",
    ),
    pytest.param(
        record(
            finding("M1", "one", path="a.go"),
            finding("M3", "three", path="c.go", line=3),
            severity_sections=(SeveritySection(
                "M", "- ~~**[M2]** `b.go:2` — resolved~~",
            ),),
        ),
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
        record(
            finding("M1", "one", path="a.go"),
            finding("M2", "two", path="b.go", line=2),
            severity_sections=(SeveritySection("M", "### Group A\n### Group B"),),
        ),
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
        record(
            finding("M1", "one", path="a.go"),
            finding("M2", "nested declaration", path="b.go", line=2),
            finding("M3", "three", path="c.go", line=3),
            severity_sections=(SeveritySection("M"),),
        ),
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
        record(
            finding("M1", "one", path="a.go"),
            ledger=(LedgerLine("M1", PriorDisposition.FIXED, "old.go"),),
            severity_sections=(SeveritySection("M"),),
        ),
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
        record(
            finding("M1", "one", path="a.go", evidence="x := 1"),
            finding("M2", "two", path="b.go", line=2),
            severity_sections=(SeveritySection("M"),),
        ),
        ["M1"],
        "## Must fix\n"
        "- **[M2]** `b.go:2` — two\n",
        "## Must fix\n\n"
        "- **[M2]** `b.go:2` — two\n",
        id="an-evidence-block-goes-with-the-finding-that-quotes-it",
    ),
    pytest.param(
        record(
            finding("M1", "one", path="a.go"),
            finding("N1", "two", path="b.go", line=2),
            severity_sections=(SeveritySection("M"), SeveritySection("N")),
        ),
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
        record(
            finding("M1", "one", path="a.go"),
            severity_sections=(SeveritySection("M"),),
        ),
        ["S9"],
        "## Must fix\n"
        "- **[M1]** `a.go:1` — one\n",
        "## Must fix\n\n"
        "- **[M1]** `a.go:1` — one\n",
        id="an-id-the-review-does-not-declare-changes-nothing",
    ),
]


@pytest.mark.parametrize("gate,expected_idx", GATES)
@pytest.mark.parametrize("rec,ids,evidence_expected,disprove_expected", DROP_CASES)
class TestEachGateLeavesExactlyThis:
    def test_the_gate_leaves_exactly_this(
        self, gate, expected_idx, rec, ids, evidence_expected, disprove_expected,
    ):
        expected = (evidence_expected, disprove_expected)[expected_idx]
        assert gate(rec, ids) == expected


# ── apply_disprove ──────────────────────────────────────────────────────────


def test_a_falsified_finding_is_kept_but_no_longer_visible():
    rec = ReviewRecord(findings=(
        RecordFinding(id="M1", severity="M", path="a.py", line=1, body="x"),
        RecordFinding(id="S1", severity="S", path="a.py", line=2, body="y"),
    ))
    artifact = DisproveArtifact(verdicts=(
        DisproveVerdict("M1", DisproveOutcome.SURVIVES, "real"),
        DisproveVerdict("S1", DisproveOutcome.FALSIFIED, "handled at b.py:3"),
    ))
    applied = apply_disprove(rec, artifact)
    assert [f.id for f in applied.record.visible] == ["M1"]
    assert applied.summary["falsified_ids"] == ["S1"]
    assert applied.summary["reasons"] == {"S1": "handled at b.py:3"}


def test_a_verdict_for_a_finding_the_review_lacks_changes_nothing():
    rec = ReviewRecord(findings=(RecordFinding(id="M1", severity="M", body="x"),))
    artifact = DisproveArtifact(verdicts=(DisproveVerdict("S9", DisproveOutcome.FALSIFIED, "r"),))
    assert apply_disprove(rec, artifact).record.visible == rec.visible
