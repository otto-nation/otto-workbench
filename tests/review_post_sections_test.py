"""Tests for the comment's non-finding sections and their order, as
`review.render.comment_body` writes them.
"""

import sys
from pathlib import Path

LIB_DIR = Path(__file__).resolve().parent.parent / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

# The module the `rp` fixture returns, imported so bin/local/select-pytest maps
# this suite to the code it reaches through `rp`.
import cli.review_post  # noqa: E402,F401
import review.render  # noqa: E402
from review.document import ReviewDocument  # noqa: E402
from review.record import (  # noqa: E402
    AgentFailure, Disposition, FocusSource, LedgerLine, ProseSection, TriageEntry,
)
from review.types import PriorDisposition, ReviewMeta, SEVERITIES  # noqa: E402

from review_behaviours_support import behaviour  # noqa: E402
from review_claims_support import claim  # noqa: E402
from review_focus_support import area, user_area  # noqa: E402
from review_record_support import finding, record  # noqa: E402


def _body_findings(rec):
    return ReviewDocument(record=rec).open_findings


def _comment(rec, findings=(), *, has_inline=False, severity_filter=None):
    return review.render.comment_body(
        rec, list(findings), meta=ReviewMeta(), has_inline=has_inline,
        severity_filter=set() if severity_filter is None else severity_filter)


RICH = record(
    finding("M1", "lock never released", path="a.py", line=3),
    finding("S1", "unclear name", path="b.py", line=9),
    finding("S2", "use a constant", path="b.py", line=12, disposition=Disposition.DECLINED),
    finding("N1", "trailing space", path="c.py", line=1),
    finding("I1", "pattern holds", path=""),
    summary="looked at locking.",
    focus=(user_area("Locking"),), claims=(claim(),), behaviours=(behaviour(),),
    failures=(AgentFailure("disprove", "timed out", "failed", recoverable=True),),
    extra=(ProseSection("Review gaps", "- group 2"),),
    static_analysis="### Nesting depth\n1 violation in 1 of 3 files checked",
)

REVIEW = record(
    finding("M1", "missing error check", path="handler.go", line=11),
    finding("S1", "unused variable", path="handler.go", line=25),
    summary="Clean refactor with good test coverage.",
    static_analysis="### Nesting depth\n1 violation in 1 of 3 files checked",
)


def test_the_comment_carries_summary_verdict_and_static_analysis():
    body = _comment(REVIEW)
    assert "Clean refactor with good test coverage." in body
    assert "**Verdict: Request changes.** 1 must-fix, 1 should-fix open." in body
    assert "Nesting depth" in body


def test_the_summary_comes_before_the_verdict():
    body = _comment(REVIEW)
    assert body.index("Clean refactor with good test coverage.") < body.index(
        "**Verdict: Request changes.**")
    assert "### Verdict" not in body


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
    body = _comment(rec)
    assert "Prior findings" not in body
    assert "[M4]" not in body


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
        "---", "Have some comments marked as",
        "**Findings outside the diff:**", "### Must fix", "### Should fix",
        "<summary>Nit (1)</summary>", "<summary>Idioms (1)</summary>",
        "**Declined — considered and not carried forward:**",
        "### Nesting depth", "## Agent Failures", "## Review gaps",
    )
    idxs = [body.index(m) for m in markers]
    assert idxs == sorted(idxs)
    assert "[C1]" not in body and "[B1]" not in body and "[F1]" not in body
    assert "File Triage" not in body and "Prior findings" not in body
    assert "**Verdict: Request changes.** 1 must-fix, 1 should-fix open; 1 should-fix declined, still counted." in body
    assert "M1 blocks." not in body
