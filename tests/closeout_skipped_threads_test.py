"""Closeout reports skipped FIXED/ALREADY_ADDRESSED threads and keeps the debt."""

import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from review_threads_support import (  # noqa: E402
    _fix, _make_state, _no_published_summary,  # noqa: F401
)
import git.client
import git.push
import pr.attribution
from pr.fix import FixOutcome, ItemOutcome
from pr.thread_models import ReportThread
import review.closeout


def _thread(tid="t1"):
    return ReportThread(id=tid, is_resolved=False, comments=[{"databaseId": 1}])


def test_unanswered_already_addressed_drains_without_a_pending_flag(
    publishing_on,
):
    """NO_CHANGES + replies_pending False used to skip an unanswered thread."""
    fix = _fix(
        items=[ItemOutcome(
            id="t1", outcome=FixOutcome.ALREADY_ADDRESSED,
            summary="already done", file="x.py", line=1,
        )],
        commit_status="no_changes",
        replies_pending=False,
    )
    threads_by_id = {"t1": _thread()}
    state = _make_state(fix)
    with patch.object(git.client, "head_sha", return_value="deadbee"), \
            patch.object(pr.attribution, "find_addressing_commit", return_value=None), \
            patch("pr.comments.post_thread_reply", return_value=True) as mock_reply, \
            patch("pr.comments.resolve_thread", return_value=True) as mock_resolve:
        review.closeout.post_pending_fix_replies(
            state, "owner/repo", 1, threads_by_id)
    mock_reply.assert_called_once()
    mock_resolve.assert_called_once()


def test_a_failed_resolve_keeps_closeout_owed(publishing_on, capsys):
    fix = _fix(
        items=[ItemOutcome(
            id="t1", outcome=FixOutcome.ALREADY_ADDRESSED,
            summary="already done", file="x.py", line=1,
        )],
        commit_status="no_changes",
        replies_pending=False,
    )
    threads_by_id = {"t1": _thread()}
    state = _make_state(fix)
    with patch.object(git.client, "head_sha", return_value="deadbee"), \
            patch.object(pr.attribution, "find_addressing_commit", return_value=None), \
            patch("pr.comments.post_thread_reply", return_value=True), \
            patch("pr.comments.resolve_thread", return_value=False):
        review.closeout.post_pending_fix_replies(
            state, "owner/repo", 1, threads_by_id)
    err = capsys.readouterr().err
    assert "t1: left open — resolve failed" in err
    assert fix.replies_pending is True


def test_a_withheld_fixed_item_is_left_open_for_push(publishing_on, capsys):
    fix = _fix(
        items=[ItemOutcome(
            id="t1", outcome=FixOutcome.FIXED,
            summary="renamed", file="x.py", line=1,
        )],
        commit_sha="abc1234",
        commit_status="push_failed",
        replies_pending=True,
    )
    threads_by_id = {"t1": _thread()}
    state = _make_state(fix)
    with patch.object(git.push, "holds", return_value=False), \
            patch("pr.comments.post_thread_reply") as mock_reply, \
            patch("pr.comments.resolve_thread") as mock_resolve:
        review.closeout.post_pending_fix_replies(
            state, "owner/repo", 1, threads_by_id)
    mock_reply.assert_not_called()
    mock_resolve.assert_not_called()
    assert "t1: left open — push pending" in capsys.readouterr().err
    assert fix.replies_pending is True
