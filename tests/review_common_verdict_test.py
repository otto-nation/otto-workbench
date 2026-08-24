"""Tests for review_common's shared formatting and verdict resolution."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pr_domains
from review_common import plural, resolve_review_verdict


def test_plural_only_singular_at_one():
    assert [f"{n} file{plural(n)}" for n in (0, 1, 2)] == ["0 files", "1 file", "2 files"]


# ── resolve_review_verdict ──────────────────────────────────────────────────

def _review(tmp_path, body: str) -> Path:
    review = tmp_path / "review.md"
    review.write_text(body)
    return review


def test_resolve_verdict_derives_from_counts_when_prose_is_silent(tmp_path):
    review = _review(tmp_path, "## Should fix\n- **[S1]** a.py:1 — improvement\n")
    assert resolve_review_verdict(review) is pr_domains.ReviewVerdict.NEEDS_DISCUSSION


def test_resolve_verdict_prose_cannot_under_report_blocking_findings(tmp_path):
    review = _review(
        tmp_path,
        "## Must fix\n- **[M1]** a.py:1 — bug\n\n## Verdict\nApprove — looks fine.\n",
    )
    assert resolve_review_verdict(review) is pr_domains.ReviewVerdict.CHANGES_REQUESTED


def test_resolve_verdict_counts_cannot_discard_a_stronger_call(tmp_path):
    review = _review(
        tmp_path,
        "## Nit\n- **[N1]** a.py:1 — style\n\n## Verdict\nRequest changes — rework it.\n",
    )
    assert resolve_review_verdict(review) is pr_domains.ReviewVerdict.CHANGES_REQUESTED


def test_resolve_verdict_disapprove_survives_any_counts(tmp_path):
    review = _review(tmp_path, "## Verdict\nDisapprove — wrong approach.\n")
    assert resolve_review_verdict(review) is pr_domains.ReviewVerdict.DISAPPROVE


def test_resolve_verdict_self_review_is_advisory(tmp_path):
    review = _review(tmp_path, "## Must fix\n- **[M1]** a.py:1 — bug\n")
    assert resolve_review_verdict(review, self_review=True) is None


def test_resolve_verdict_self_review_still_reports_disapprove(tmp_path):
    """Disapprove judges the approach, which holds with or without a PR."""
    review = _review(tmp_path, "## Verdict\nDisapprove — wrong approach.\n")
    assert resolve_review_verdict(review, self_review=True) is pr_domains.ReviewVerdict.DISAPPROVE


def test_resolve_verdict_missing_file_has_no_verdict(tmp_path):
    assert resolve_review_verdict(tmp_path / "nope.md") is None
    assert resolve_review_verdict(None) is None


def test_resolve_verdict_uses_passed_counts_instead_of_rereading(tmp_path):
    # The file itself has no findings at all — if resolve_review_verdict re-read
    # it instead of trusting the passed counts, this would resolve to Approve.
    review = _review(tmp_path, "## Verdict\nRequest changes — rework it.\n")
    result = resolve_review_verdict(review, counts={"M": 0, "S": 1})
    assert result is pr_domains.ReviewVerdict.CHANGES_REQUESTED
