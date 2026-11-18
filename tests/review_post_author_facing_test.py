"""Tests for the author-facing GitHub comment `review.render.comment_body` writes.

review.md keeps the operator ledger (declined still counts). The posted comment
asks the author for work.
"""

import sys
from pathlib import Path

LIB_DIR = Path(__file__).resolve().parent.parent / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import review.render  # noqa: E402
from pr.domains import ReviewVerdict  # noqa: E402
from review.record import Disposition, FocusSource, VerifiedNote  # noqa: E402
from review.types import PhaseRun, ReviewMeta  # noqa: E402
from review.verdict import resolve_review_verdict  # noqa: E402
from review_focus_support import area  # noqa: E402
from review_record_support import finding, record  # noqa: E402


class TestAuthorFacingComment:
    """The GitHub body is for the author. review.md keeps the operator ledger."""

    def test_declined_only_should_fix_approves_the_comment_not_the_ledger(self):
        rec = record(
            finding(
                "S1", "use a constant", path="b.py", line=12,
                disposition=Disposition.DECLINED, reason="the literal is clearer",
            ),
            summary="looked.",
        )
        meta = ReviewMeta()
        assert resolve_review_verdict(rec, meta) is ReviewVerdict.NEEDS_DISCUSSION
        rendered = review.render.render_review(rec, meta)
        assert "1 should-fix declined, still counted." in rendered
        body = review.render.comment_body(
            rec, [rec.finding("S1").view()], meta=meta, has_inline=False,
            severity_filter={"S"},
        )
        assert "**Verdict: Approve.**" in body
        assert "Needs discussion" not in body
        assert "still counted" not in body
        assert "Review findings" not in body
        assert "[should-fix]" not in body
        assert "**Declined — considered and not carried forward:**" in body

    def test_a_declined_must_fix_approves_the_comment_not_the_ledger(self):
        rec = record(
            finding(
                "M1", "lock", disposition=Disposition.DECLINED, reason="not a bug",
            ),
            summary="looked.",
        )
        meta = ReviewMeta()
        assert resolve_review_verdict(rec, meta) is ReviewVerdict.CHANGES_REQUESTED
        body = review.render.comment_body(
            rec, [rec.finding("M1").view()], meta=meta, has_inline=False,
            severity_filter={"M"},
        )
        assert "**Verdict: Approve.**" in body
        assert "Request changes" not in body

    def test_an_open_should_fix_keeps_needs_discussion_without_the_declined_tally(self):
        rec = record(
            finding("S1", "real bug", path="a.py", line=3),
            finding(
                "S2", "use a constant", path="b.py", line=12,
                disposition=Disposition.DECLINED, reason="literal is clearer",
            ),
            summary="looked.",
        )
        body = review.render.comment_body(
            rec, [f.view() for f in rec.visible], meta=ReviewMeta(),
            has_inline=False, severity_filter={"S"},
        )
        assert "**Verdict: Needs discussion.** 1 should-fix open." in body
        assert "still counted" not in body
        assert "Review findings" in body

    def test_noop_focus_is_omitted_from_the_comment(self):
        rec = record(
            summary="looked.",
            focus=(
                area(
                    id="F1", title="Generator",
                    coverage=(
                        "Checked the rules.go delta: late mismatch errors "
                        "if the debit is missing. No new issue."
                    ),
                ),
                area(
                    id="F2", title="SimHandler lock",
                    coverage=(
                        "simhttp.go unchanged since the prior review; "
                        "no new finding."
                    ),
                ),
            ),
        )
        body = review.render.comment_body(
            rec, [], meta=ReviewMeta(), has_inline=False, severity_filter={"M"},
        )
        assert "### Focus" in body
        assert "Generator" in body
        assert "SimHandler lock" not in body
        assert "unchanged since" not in body

    def test_substantive_unchanged_since_clause_is_kept(self):
        rec = record(
            summary="looked.",
            focus=(
                area(
                    title="Lock",
                    coverage=(
                        "Verified mutex ordering in simhttp.go, which is "
                        "unchanged since the prior review."
                    ),
                ),
            ),
        )
        body = review.render.comment_body(
            rec, [], meta=ReviewMeta(), has_inline=False, severity_filter={"M"},
        )
        assert "Verified mutex ordering" in body

    def test_all_noop_focus_drops_the_section(self):
        rec = record(
            summary="looked.",
            focus=(
                area(
                    title="Lock",
                    coverage="unchanged since the prior review; no new finding.",
                ),
            ),
        )
        body = review.render.comment_body(
            rec, [], meta=ReviewMeta(), has_inline=False, severity_filter={"M"},
        )
        assert "### Focus" not in body

    def test_a_requested_noop_focus_stays_on_the_comment(self):
        rec = record(
            summary="looked.",
            focus=(
                area(
                    title="Lock",
                    source=FocusSource.USER,
                    coverage="unchanged since the prior review; no new finding.",
                ),
            ),
        )
        body = review.render.comment_body(
            rec, [], meta=ReviewMeta(), has_inline=False, severity_filter={"M"},
        )
        assert "### Focus" in body
        assert "Lock" in body
        assert "*(requested)*" in body

    def test_static_analysis_stays_in_review_md_not_the_comment(self):
        rec = record(summary="looked.", static_analysis="### Nesting depth\n1 violation")
        meta = ReviewMeta()
        body = review.render.comment_body(
            rec, [], meta=meta, has_inline=False, severity_filter={"M"},
        )
        assert "Nesting depth" not in body
        assert "Nesting depth" in review.render.render_review(rec, meta)

    def test_verified_notes_stay_in_review_md_not_the_comment(self):
        rec = record(
            summary="looked.",
            verified=(VerifiedNote("pkg/client.go", 30, "fetch closes the body."),),
        )
        meta = ReviewMeta()
        body = review.render.comment_body(
            rec, [], meta=meta, has_inline=False, severity_filter={"M"},
        )
        assert "Verified, no issue" not in body
        assert "fetch closes the body." not in body
        assert "fetch closes the body." in review.render.render_review(rec, meta)

    def test_models_and_phases_stay_off_the_comment(self):
        rec = record(summary="looked.")
        meta = ReviewMeta(
            models="single m (low)",
            phases=(PhaseRun("single", True),),
        )
        body = review.render.comment_body(
            rec, [], meta=meta, has_inline=False, severity_filter={"M"},
        )
        assert "Models:" not in body
        assert "Phases:" not in body
        assert "<sub>" not in body
