"""Tests for filing a tracker issue from the findings a review left open.

The selection discipline is the point of most of these. Filing posts under the
operator's name, so the default has to be that nothing is filed — and the way
that goes wrong is not a crash but a silent one: a `--track` id that names
nothing, or a second run that files the same findings twice.
"""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import review.finding_issue
from review.deferred_issue import TRACK_ALL
from review.issue import CreatedIssue, IssueDelivery, IssueResult
from review.paths import read_review_meta, write_review_meta
from review.types import OpenFinding, ReviewMeta


def _job(tmp_path, pr_number="7", host=""):
    return SimpleNamespace(
        repo="owner/repo",
        pr_number=pr_number,
        host=host,
        wt_path=str(tmp_path),
        artifact_dir=str(tmp_path),
        pr=SimpleNamespace(head="feat/x"),
    )


def _seed(tmp_path, *findings):
    write_review_meta(tmp_path, ReviewMeta(repo="owner/repo", open_findings=findings))


def _finding(fid, outcome="deferred", **kw):
    return OpenFinding(
        id=fid, outcome=outcome,
        summary=kw.get("summary", f"finding {fid}"),
        location=kw.get("location", "a.py:1"),
        reason=kw.get("reason", ""),
    )


# ── what is filable ─────────────────────────────────────────────────────────


def test_the_open_findings_come_from_the_sidecar_not_the_document(tmp_path):
    """A deferral writes no annotation, so the document cannot answer this."""
    _seed(tmp_path, _finding("M1"), _finding("S2"))
    assert [f.id for f in review.finding_issue.filable(tmp_path)] == ["M1", "S2"]


def test_a_review_with_no_sidecar_offers_nothing(tmp_path):
    assert review.finding_issue.filable(tmp_path) == []


# ── selection ───────────────────────────────────────────────────────────────


def test_a_track_id_naming_no_open_finding_is_refused(tmp_path):
    """Filing the rest silently would hide the typo."""
    _seed(tmp_path, _finding("M1"))
    assert review.finding_issue.validate_track(tmp_path, frozenset({"S9"})) is False


def test_track_all_accepts_whatever_is_there(tmp_path):
    _seed(tmp_path, _finding("M1"))
    assert review.finding_issue.validate_track(tmp_path, TRACK_ALL) is True


def test_nothing_is_filed_when_nothing_is_tracked(tmp_path):
    """The default: a deferral is a per-finding judgement, not a fallback."""
    _seed(tmp_path, _finding("M1"))
    with patch("review.issue.create_issue") as create:
        assert review.finding_issue.file_and_link(
            tmp_path, _job(tmp_path), frozenset()) is True
    create.assert_not_called()


def test_only_the_tracked_findings_reach_the_issue(tmp_path):
    _seed(tmp_path, _finding("M1"), _finding("S2"), _finding("N3"))
    with patch("review.issue.create_issue") as create, \
         patch("review.issue.load_issue_provider") as provider:
        provider.return_value = SimpleNamespace(
            name="github", options={}, resolved=True)
        create.return_value = IssueResult(
            IssueDelivery.FILED, CreatedIssue("55", "https://x/55"))
        review.finding_issue.file_and_link(tmp_path, _job(tmp_path), frozenset({"S2"}))
    body = create.call_args.args[3]
    assert "finding S2" in body
    assert "finding M1" not in body and "finding N3" not in body


# ── the filed queue ─────────────────────────────────────────────────────────


def test_a_filed_finding_is_not_offered_again(tmp_path):
    """A second --track-all would otherwise open a second issue for the same work."""
    _seed(tmp_path, _finding("M1"), _finding("S2"))
    with patch("review.issue.create_issue") as create, \
         patch("review.issue.load_issue_provider") as provider:
        provider.return_value = SimpleNamespace(
            name="github", options={}, resolved=True)
        create.return_value = IssueResult(
            IssueDelivery.FILED, CreatedIssue("55", "https://x/55"))
        review.finding_issue.file_and_link(tmp_path, _job(tmp_path), frozenset({"M1"}))
    assert [f.id for f in review.finding_issue.filable(tmp_path)] == ["S2"]


def test_a_drafted_filing_keeps_the_findings_queued(tmp_path):
    """Without --post nothing was filed, so a later run must still be able to."""
    _seed(tmp_path, _finding("M1"))
    with patch("review.issue.create_issue") as create, \
         patch("review.issue.load_issue_provider") as provider:
        provider.return_value = SimpleNamespace(
            name="github", options={}, resolved=True)
        create.return_value = IssueResult(IssueDelivery.SKIPPED)
        review.finding_issue.file_and_link(tmp_path, _job(tmp_path), TRACK_ALL)
    assert [f.id for f in review.finding_issue.filable(tmp_path)] == ["M1"]


# ── the parent issue ────────────────────────────────────────────────────────


def test_a_github_parent_is_told_where_the_work_went(tmp_path):
    """A parent whose PR merged with findings open otherwise reads as complete."""
    _seed(tmp_path, _finding("M1"))
    result = IssueResult(IssueDelivery.FILED, CreatedIssue("55", "https://x/55"))
    with patch("pr.comments.post_issue_comment") as comment:
        comment.return_value = "https://x/comment"
        review.finding_issue.note_on_parent("github", "1200", result, _job(tmp_path))
    assert comment.call_args.args[1] == 1200
    assert "https://x/55" in comment.call_args.args[2]


def test_linear_gets_no_comment_because_the_relation_already_says_it(tmp_path):
    """`create_issue` passed --parent; a comment would say it twice."""
    result = IssueResult(IssueDelivery.FILED, CreatedIssue("ENG-9", "https://x"))
    with patch("pr.comments.post_issue_comment") as comment:
        review.finding_issue.note_on_parent("linear", "ENG-1", result, _job(tmp_path))
    comment.assert_not_called()


def test_a_tracker_key_is_not_posted_as_a_github_issue_number(tmp_path):
    """`extract_issue_id` can return ENG-1 from a branch name on any provider."""
    result = IssueResult(IssueDelivery.FILED, CreatedIssue("55", "https://x/55"))
    with patch("pr.comments.post_issue_comment") as comment:
        review.finding_issue.note_on_parent("github", "ENG-1", result, _job(tmp_path))
    comment.assert_not_called()


def test_an_undelivered_filing_tells_the_parent_nothing(tmp_path):
    """There is nowhere to point at yet."""
    with patch("pr.comments.post_issue_comment") as comment:
        review.finding_issue.note_on_parent(
            "github", "1200", IssueResult(IssueDelivery.UNDELIVERED), _job(tmp_path))
    comment.assert_not_called()


# ── the body ────────────────────────────────────────────────────────────────


def test_the_body_links_the_pr_on_the_repo_s_own_forge(tmp_path):
    """A self-review on an enterprise host must not link to github.com."""
    body = review.finding_issue.build_body(
        [_finding("M1")], _job(tmp_path, host="github.example.com"))
    assert "github.example.com/owner/repo/pull/7" in body


def test_a_branch_with_no_pr_is_named_by_its_branch(tmp_path):
    """Self-review files findings before a PR exists."""
    body = review.finding_issue.build_body([_finding("M1")], _job(tmp_path, pr_number=""))
    assert "`feat/x`" in body


def test_a_finding_with_no_reason_still_says_why_it_is_open(tmp_path):
    needs_human = _finding("S1", outcome="needs_human", reason="")
    assert "needs a person" in review.finding_issue.build_body([needs_human], _job(tmp_path))


def test_a_pipe_in_a_summary_does_not_break_the_table(tmp_path):
    body = review.finding_issue.build_body(
        [_finding("M1", summary="a | b")], _job(tmp_path))
    rows = [line for line in body.splitlines() if line.startswith("| a ")]
    # Escaped, so the cell's own pipe cannot be read as a column boundary.
    assert rows == [r"| a \| b | `a.py:1` | not reached by the fix pass |"]
