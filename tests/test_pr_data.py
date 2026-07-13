"""Tests for PRData dataclass and its extraction helpers."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "claude" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from review_github import (
    PRData, fetch_pr_data,
    GQL_THREADS_LIMIT, GQL_THREAD_COMMENTS_LIMIT,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _make_review(
    database_id=1, state="APPROVED", body="", login="alice",
    minimized_reason=None, submitted_at="2026-01-01T00:00:00Z",
):
    r = {
        "databaseId": database_id,
        "state": state,
        "body": body,
        "submittedAt": submitted_at,
        "author": {"login": login},
    }
    if minimized_reason is not None:
        r["minimizedReason"] = minimized_reason
    return r


def _make_thread(
    thread_id="PRT_1", is_resolved=False, path="file.py", line=10,
    comments=None,
):
    if comments is None:
        comments = []
    return {
        "id": thread_id,
        "isResolved": is_resolved,
        "path": path,
        "line": line,
        "comments": {
            "totalCount": len(comments),
            "nodes": comments,
        },
    }


def _make_thread_comment(
    comment_id="PRC_1", database_id=100, login="alice",
    body="comment text", created_at="2026-01-01T00:00:00Z",
):
    return {
        "id": comment_id,
        "databaseId": database_id,
        "author": {"login": login},
        "body": body,
        "createdAt": created_at,
    }


def _make_issue_comment(
    database_id=200, login="bob", body="issue comment",
    created_at="2026-01-01T00:00:00Z",
):
    return {
        "databaseId": database_id,
        "author": {"login": login},
        "body": body,
        "createdAt": created_at,
    }


def _make_commit(oid="abc123", message_headline="feat: something"):
    return {"commit": {"oid": oid, "messageHeadline": message_headline}}


def _make_pr_data(**overrides):
    defaults = {
        "viewer_login": "bot-user",
        "head_sha": "abc123def456",
        "head_ref": "feat/my-branch",
        "base_ref": "main",
        "reviews": [],
        "review_threads": [],
        "issue_comments": [],
        "commits": [],
    }
    defaults.update(overrides)
    return PRData(**defaults)


# ── PRData construction ──────────────────────────────────────────────────────

class TestPRDataConstruction:
    def test_basic_fields(self):
        pd = _make_pr_data()
        assert pd.viewer_login == "bot-user"
        assert pd.head_sha == "abc123def456"
        assert pd.head_ref == "feat/my-branch"
        assert pd.base_ref == "main"

    def test_empty_lists_by_default(self):
        pd = PRData(viewer_login="x", head_sha="a", head_ref="b", base_ref="c")
        assert pd.reviews == []
        assert pd.review_threads == []
        assert pd.issue_comments == []
        assert pd.commits == []


# ── pending_review_id ─────────────────────────────────────────────────────────

class TestPendingReviewId:
    def test_no_reviews(self):
        assert _make_pr_data().pending_review_id is None

    def test_no_pending(self):
        pd = _make_pr_data(reviews=[_make_review(state="APPROVED")])
        assert pd.pending_review_id is None

    def test_pending_found(self):
        pd = _make_pr_data(reviews=[
            _make_review(database_id=42, state="PENDING"),
            _make_review(database_id=99, state="APPROVED"),
        ])
        assert pd.pending_review_id == 42

    def test_pending_zero_id_returns_none(self):
        pd = _make_pr_data(reviews=[
            {"databaseId": 0, "state": "PENDING", "author": {"login": "x"}},
        ])
        assert pd.pending_review_id is None


# ── new_commit_count ──────────────────────────────────────────────────────────

class TestNewCommitCount:
    def test_no_commits(self):
        pd = _make_pr_data()
        assert pd.new_commit_count("abc123") == 0

    def test_sha_found_exact(self):
        pd = _make_pr_data(commits=[
            _make_commit(oid="aaa111"),
            _make_commit(oid="bbb222"),
            _make_commit(oid="ccc333"),
        ])
        assert pd.new_commit_count("bbb222") == 1

    def test_sha_found_prefix(self):
        pd = _make_pr_data(commits=[
            _make_commit(oid="aaa111full"),
            _make_commit(oid="bbb222full"),
        ])
        assert pd.new_commit_count("bbb222") == 0

    def test_sha_not_found(self):
        pd = _make_pr_data(commits=[
            _make_commit(oid="aaa111"),
            _make_commit(oid="bbb222"),
        ])
        assert pd.new_commit_count("zzz999") == 2


# ── bot_reviews_visible ──────────────────────────────────────────────────────

class TestBotReviewsVisible:
    def test_empty(self):
        assert _make_pr_data().bot_reviews_visible("bot") == []

    def test_filters_by_login(self):
        pd = _make_pr_data(reviews=[
            _make_review(database_id=1, login="bot", state="APPROVED"),
            _make_review(database_id=2, login="human", state="APPROVED"),
        ])
        result = pd.bot_reviews_visible("bot")
        assert len(result) == 1
        assert result[0]["id"] == 1

    def test_case_insensitive_login(self):
        pd = _make_pr_data(reviews=[
            _make_review(database_id=1, login="Bot-User", state="APPROVED"),
        ])
        assert len(pd.bot_reviews_visible("bot-user")) == 1

    def test_excludes_pending_dismissed(self):
        pd = _make_pr_data(reviews=[
            _make_review(database_id=1, login="bot", state="PENDING"),
            _make_review(database_id=2, login="bot", state="DISMISSED"),
            _make_review(database_id=3, login="bot", state="APPROVED"),
        ])
        result = pd.bot_reviews_visible("bot")
        assert len(result) == 1
        assert result[0]["id"] == 3

    def test_excludes_minimized(self):
        pd = _make_pr_data(reviews=[
            _make_review(database_id=1, login="bot", state="APPROVED", minimized_reason="outdated"),
        ])
        assert pd.bot_reviews_visible("bot") == []

    def test_null_author(self):
        pd = _make_pr_data(reviews=[
            {"databaseId": 1, "state": "APPROVED", "author": None, "body": "x"},
        ])
        assert pd.bot_reviews_visible("bot") == []


# ── bot_inline_comments ──────────────────────────────────────────────────────

class TestBotInlineComments:
    def test_empty(self):
        assert _make_pr_data().bot_inline_comments("bot") == []

    def test_flattens_threads(self):
        pd = _make_pr_data(review_threads=[
            _make_thread(path="a.py", comments=[
                _make_thread_comment(login="bot", body="fix this"),
                _make_thread_comment(login="human", body="why?"),
            ]),
            _make_thread(path="b.py", comments=[
                _make_thread_comment(login="bot", body="also this"),
            ]),
        ])
        result = pd.bot_inline_comments("bot")
        assert len(result) == 2
        assert result[0] == {"path": "a.py", "body": "fix this"}
        assert result[1] == {"path": "b.py", "body": "also this"}

    def test_case_insensitive(self):
        pd = _make_pr_data(review_threads=[
            _make_thread(comments=[
                _make_thread_comment(login="Bot-User", body="x"),
            ]),
        ])
        assert len(pd.bot_inline_comments("bot-user")) == 1


# ── bot_review_bodies ────────────────────────────────────────────────────────

class TestBotReviewBodies:
    def test_empty(self):
        assert _make_pr_data().bot_review_bodies("bot") == []

    def test_returns_non_empty_bodies(self):
        pd = _make_pr_data(reviews=[
            _make_review(login="bot", body="review text"),
            _make_review(login="bot", body=""),
            _make_review(login="human", body="other review"),
        ])
        result = pd.bot_review_bodies("bot")
        assert result == ["review text"]


# ── reviewer_verdicts ────────────────────────────────────────────────────────

class TestReviewerVerdicts:
    def test_empty(self):
        assert _make_pr_data().reviewer_verdicts() == []

    def test_latest_per_user(self):
        pd = _make_pr_data(reviews=[
            _make_review(login="alice", state="CHANGES_REQUESTED", submitted_at="2026-01-01T00:00:00Z"),
            _make_review(login="alice", state="APPROVED", submitted_at="2026-01-02T00:00:00Z"),
            _make_review(login="bob", state="COMMENTED", submitted_at="2026-01-01T00:00:00Z"),
        ])
        result = sorted(pd.reviewer_verdicts(), key=lambda v: v["user"])
        assert len(result) == 2
        assert result[0] == {"user": "alice", "state": "APPROVED", "submitted_at": "2026-01-02T00:00:00Z"}
        assert result[1] == {"user": "bob", "state": "COMMENTED", "submitted_at": "2026-01-01T00:00:00Z"}

    def test_excludes_pending(self):
        pd = _make_pr_data(reviews=[
            _make_review(login="alice", state="PENDING"),
        ])
        assert pd.reviewer_verdicts() == []


# ── non_self_issue_comments ──────────────────────────────────────────────────

class TestNonSelfIssueComments:
    def test_empty(self):
        assert _make_pr_data().non_self_issue_comments("me") == []

    def test_excludes_self(self):
        pd = _make_pr_data(issue_comments=[
            _make_issue_comment(database_id=1, login="me", body="my comment"),
            _make_issue_comment(database_id=2, login="reviewer", body="their comment"),
        ])
        result = pd.non_self_issue_comments("me")
        assert len(result) == 1
        assert result[0]["user"] == "reviewer"
        assert result[0]["id"] == 2

    def test_case_insensitive(self):
        pd = _make_pr_data(issue_comments=[
            _make_issue_comment(login="MyUser"),
        ])
        assert pd.non_self_issue_comments("myuser") == []

    def test_null_author(self):
        pd = _make_pr_data(issue_comments=[
            {"databaseId": 1, "author": None, "body": "ghost", "createdAt": "2026-01-01T00:00:00Z"},
        ])
        result = pd.non_self_issue_comments("me")
        assert len(result) == 1
        assert result[0]["user"] == ""


# ── review_body_comments ────────────────────────────────────────────────────

class TestReviewBodyComments:
    def test_empty(self):
        assert _make_pr_data().review_body_comments("me") == []

    def test_returns_non_self_reviews_with_body(self):
        pd = _make_pr_data(reviews=[
            _make_review(database_id=1, login="reviewer", state="COMMENTED",
                         body="This PR overlaps with #2284"),
            _make_review(database_id=2, login="me", state="COMMENTED",
                         body="My own review"),
        ])
        result = pd.review_body_comments("me")
        assert len(result) == 1
        assert result[0]["id"] == 1
        assert result[0]["user"] == "reviewer"
        assert result[0]["body"] == "This PR overlaps with #2284"
        assert result[0]["state"] == "COMMENTED"

    def test_excludes_empty_body(self):
        pd = _make_pr_data(reviews=[
            _make_review(database_id=1, login="reviewer", state="APPROVED", body=""),
            _make_review(database_id=2, login="reviewer", state="APPROVED", body="   "),
        ])
        assert pd.review_body_comments("me") == []

    def test_excludes_pending(self):
        pd = _make_pr_data(reviews=[
            _make_review(database_id=1, login="reviewer", state="PENDING",
                         body="draft review"),
        ])
        assert pd.review_body_comments("me") == []

    def test_excludes_minimized(self):
        pd = _make_pr_data(reviews=[
            _make_review(database_id=1, login="reviewer", state="COMMENTED",
                         body="outdated", minimized_reason="outdated"),
        ])
        assert pd.review_body_comments("me") == []

    def test_case_insensitive_self_filter(self):
        pd = _make_pr_data(reviews=[
            _make_review(database_id=1, login="MyUser", state="COMMENTED",
                         body="review text"),
        ])
        assert pd.review_body_comments("myuser") == []

    def test_null_author(self):
        pd = _make_pr_data(reviews=[
            {"databaseId": 1, "state": "COMMENTED", "body": "ghost review",
             "author": None, "submittedAt": "2026-01-01T00:00:00Z"},
        ])
        result = pd.review_body_comments("me")
        assert len(result) == 1
        assert result[0]["user"] == ""

    def test_includes_all_non_pending_states(self):
        pd = _make_pr_data(reviews=[
            _make_review(database_id=1, login="r", state="COMMENTED", body="a"),
            _make_review(database_id=2, login="r", state="APPROVED", body="b"),
            _make_review(database_id=3, login="r", state="CHANGES_REQUESTED", body="c"),
        ])
        result = pd.review_body_comments("me")
        assert len(result) == 3


# ── fetch_pr_data ────────────────────────────────────────────────────────────

class TestFetchPrData:
    def _graphql_response(self, **pr_overrides):
        pr = {
            "headRefOid": "sha123",
            "headRefName": "feat/test",
            "baseRefName": "main",
            "reviews": {"nodes": []},
            "reviewThreads": {"totalCount": 0, "nodes": []},
            "comments": {"nodes": []},
            "commits": {"nodes": []},
        }
        pr.update(pr_overrides)
        return json.dumps({
            "data": {
                "viewer": {"login": "bot-user"},
                "repository": {"pullRequest": pr},
            },
        })

    @patch("review_github._gh_graphql")
    def test_basic_parse(self, mock_gql):
        mock_gql.return_value = (0, self._graphql_response())
        pd = fetch_pr_data("owner/repo", "42")
        assert pd.viewer_login == "bot-user"
        assert pd.head_sha == "sha123"
        assert pd.head_ref == "feat/test"
        assert pd.base_ref == "main"
        mock_gql.assert_called_once()
        call_args = mock_gql.call_args
        assert call_args[0][1] == {"owner": "owner", "name": "repo", "pr": 42}

    @patch("review_github._gh_graphql")
    def test_reviews_parsed(self, mock_gql):
        review = _make_review(database_id=10, login="alice", state="APPROVED")
        mock_gql.return_value = (0, self._graphql_response(
            reviews={"nodes": [review]},
        ))
        pd = fetch_pr_data("owner/repo", "1")
        assert len(pd.reviews) == 1
        assert pd.reviews[0]["databaseId"] == 10

    @patch("review_github._gh_graphql")
    def test_threads_parsed(self, mock_gql):
        comment = _make_thread_comment(login="bot", body="fix this")
        thread = _make_thread(thread_id="PRT_1", path="a.py", comments=[comment])
        mock_gql.return_value = (0, self._graphql_response(
            reviewThreads={"totalCount": 1, "nodes": [thread]},
        ))
        pd = fetch_pr_data("owner/repo", "1")
        assert len(pd.review_threads) == 1
        assert pd.review_threads[0]["path"] == "a.py"

    @patch("review_github._gh_graphql")
    def test_graphql_failure_exits(self, mock_gql):
        mock_gql.return_value = (1, "error")
        with pytest.raises(SystemExit):
            fetch_pr_data("owner/repo", "1")

    @patch("review_github._gh_graphql")
    def test_invalid_json_exits(self, mock_gql):
        mock_gql.return_value = (0, "not json")
        with pytest.raises(SystemExit):
            fetch_pr_data("owner/repo", "1")

    @patch("review_github._gh_graphql")
    def test_truncation_warnings(self, mock_gql, capsys):
        thread = _make_thread(thread_id="PRT_1", path="big.py")
        thread["comments"]["totalCount"] = GQL_THREAD_COMMENTS_LIMIT + 1
        total_threads = GQL_THREADS_LIMIT + 1
        mock_gql.return_value = (0, self._graphql_response(
            reviewThreads={"totalCount": total_threads, "nodes": [thread]},
        ))
        pd = fetch_pr_data("owner/repo", "1")
        assert len(pd.review_threads) == 1
        stderr = capsys.readouterr().err
        assert f"{total_threads} review threads" in stderr
        assert "GQL_THREADS_LIMIT" in stderr
        assert "big.py" in stderr
        assert "GQL_THREAD_COMMENTS_LIMIT" in stderr
