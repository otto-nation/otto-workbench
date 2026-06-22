"""Tests for reply thread classification and prompt formatting for re-reviews."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "claude" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from review_preflight import (
    THREAD_ACKNOWLEDGED, THREAD_CONTESTED, THREAD_REPLIED,
    THREAD_RESOLVED, THREAD_UNREPLIED,
    _classify_thread_for_rereview, _match_thread_to_finding,
    fetch_reply_threads,
)
from review_prompt import (
    _annotate_with_thread_state, _build_prior_section,
    _build_reply_threads_section,
    _format_general_comments, _format_review_comments, _format_reviews,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_comments(*entries):
    """Create comment list from (login, body) tuples."""
    comments = []
    for i, (login, body) in enumerate(entries):
        comments.append({
            "databaseId": 1000 + i,
            "author": {"login": login},
            "body": body,
            "createdAt": f"2026-01-01T{i:02d}:00:00Z",
        })
    return comments


# ── _classify_thread_for_rereview ────────────────────────────────────────────

class TestClassifyThreadForRereview:
    def test_resolved_thread(self):
        comments = _make_comments(("bot", "Missing error check"))
        state, replies = _classify_thread_for_rereview(comments, True, "bot")
        assert state == THREAD_RESOLVED
        assert replies == []

    def test_unreplied_no_author_replies(self):
        comments = _make_comments(("bot", "Missing error check"))
        state, replies = _classify_thread_for_rereview(comments, False, "bot")
        assert state == THREAD_UNREPLIED
        assert replies == []

    def test_acknowledged_reply(self):
        comments = _make_comments(
            ("bot", "Missing error check"),
            ("alice", "Fixed, thanks!"),
        )
        state, replies = _classify_thread_for_rereview(comments, False, "bot")
        assert state == THREAD_ACKNOWLEDGED
        assert len(replies) == 1
        assert replies[0]["body"] == "Fixed, thanks!"

    def test_contested_reply(self):
        comments = _make_comments(
            ("bot", "Should use shared helper"),
            ("alice", "I think we should keep it inline actually — it's clearer"),
        )
        state, replies = _classify_thread_for_rereview(comments, False, "bot")
        assert state == THREAD_CONTESTED
        assert len(replies) == 1

    def test_generic_reply(self):
        comments = _make_comments(
            ("bot", "Missing error check"),
            ("alice", "I see your point, let me look into this further"),
        )
        state, replies = _classify_thread_for_rereview(comments, False, "bot")
        assert state == THREAD_REPLIED
        assert len(replies) == 1

    def test_case_insensitive_bot_login(self):
        comments = _make_comments(
            ("Bot-User", "Issue"),
            ("alice", "Done"),
        )
        state, _ = _classify_thread_for_rereview(comments, False, "bot-user")
        assert state == THREAD_ACKNOWLEDGED

    def test_state_uses_last_reply(self):
        comments = _make_comments(
            ("bot", "Issue"),
            ("alice", "Done"),
            ("alice", "Actually no, I still think we should keep it"),
        )
        state, replies = _classify_thread_for_rereview(comments, False, "bot")
        assert state == THREAD_CONTESTED
        assert len(replies) == 2

    def test_bot_reply_between_author_replies(self):
        comments = _make_comments(
            ("bot", "Issue"),
            ("alice", "Why?"),
            ("bot", "Because X"),
            ("alice", "Done"),
        )
        state, replies = _classify_thread_for_rereview(comments, False, "bot")
        assert state == THREAD_ACKNOWLEDGED
        assert len(replies) == 2
        assert replies[0]["body"] == "Why?"
        assert replies[1]["body"] == "Done"


# ── _match_thread_to_finding ─────────────────────────────────────────────────

class TestMatchThreadToFinding:
    def test_extracts_finding_id(self):
        body = "**[M1]** `handler.go:42` — Missing error check on db.Query()"
        assert _match_thread_to_finding(body) == "M1"

    def test_extracts_different_severities(self):
        assert _match_thread_to_finding("**[S3]** something") == "S3"
        assert _match_thread_to_finding("**[N2]** nit") == "N2"
        assert _match_thread_to_finding("**[I1]** info") == "I1"

    def test_no_finding_id(self):
        assert _match_thread_to_finding("Just a comment") == ""

    def test_first_match_wins(self):
        body = "**[M1]** first\n**[M2]** second"
        assert _match_thread_to_finding(body) == "M1"


# ── fetch_reply_threads ─────────────────────────────────────────────────────

class TestFetchReplyThreads:
    def test_empty_when_no_bot_login(self):
        with patch("review_preflight._get_bot_login", return_value=""), \
             patch("review_preflight.fetch_threads", return_value=[]):
            result = fetch_reply_threads("owner/repo", "42")
        assert result == {"threads": [], "summary": {}}

    def test_empty_when_no_threads(self):
        with patch("review_preflight._get_bot_login", return_value="bot"), \
             patch("review_preflight.fetch_threads", return_value=[]):
            result = fetch_reply_threads("owner/repo", "42")
        assert result == {"threads": [], "summary": {}}

    def test_filters_to_bot_authored_threads(self):
        threads = [
            {
                "id": "T1", "isResolved": False, "path": "main.py", "line": 10,
                "comments": {"nodes": _make_comments(
                    ("bot", "**[M1]** Issue"),
                    ("alice", "Fixed"),
                )},
            },
            {
                "id": "T2", "isResolved": False, "path": "util.py", "line": 5,
                "comments": {"nodes": _make_comments(
                    ("alice", "Regular comment"),
                    ("bob", "Agree"),
                )},
            },
        ]
        with patch("review_preflight._get_bot_login", return_value="bot"), \
             patch("review_preflight.fetch_threads", return_value=threads):
            result = fetch_reply_threads("owner/repo", "42")
        assert len(result["threads"]) == 1
        assert result["threads"][0]["finding_id"] == "M1"
        assert result["threads"][0]["state"] == THREAD_ACKNOWLEDGED
        assert result["summary"] == {THREAD_ACKNOWLEDGED: 1}

    def test_classifies_multiple_states(self):
        threads = [
            {
                "id": "T1", "isResolved": True, "path": "a.py", "line": 1,
                "comments": {"nodes": _make_comments(("bot", "**[M1]** Issue"))},
            },
            {
                "id": "T2", "isResolved": False, "path": "b.py", "line": 2,
                "comments": {"nodes": _make_comments(("bot", "**[S1]** Issue"))},
            },
        ]
        with patch("review_preflight._get_bot_login", return_value="bot"), \
             patch("review_preflight.fetch_threads", return_value=threads):
            result = fetch_reply_threads("owner/repo", "42")
        states = {t["state"] for t in result["threads"]}
        assert THREAD_RESOLVED in states
        assert THREAD_UNREPLIED in states

    def test_truncates_root_body(self):
        long_body = "x" * 300
        threads = [
            {
                "id": "T1", "isResolved": False, "path": "a.py", "line": 1,
                "comments": {"nodes": _make_comments(("bot", long_body))},
            },
        ]
        with patch("review_preflight._get_bot_login", return_value="bot"), \
             patch("review_preflight.fetch_threads", return_value=threads):
            result = fetch_reply_threads("owner/repo", "42")
        assert len(result["threads"][0]["root_body"]) == 200


# ── _build_reply_threads_section ─────────────────────────────────────────────

class TestBuildReplyThreadsSection:
    def test_empty_when_no_threads(self):
        assert _build_reply_threads_section({}) == ""
        assert _build_reply_threads_section({"threads": []}) == ""

    def test_groups_by_state(self):
        data = {"threads": [
            {"state": THREAD_CONTESTED, "finding_id": "M1", "path": "a.py",
             "line": 10, "root_body": "issue", "replies": [
                 {"author": "alice", "body": "I disagree because X"},
             ]},
            {"state": THREAD_ACKNOWLEDGED, "finding_id": "S1", "path": "b.py",
             "line": 5, "root_body": "issue", "replies": [
                 {"author": "alice", "body": "Fixed"},
             ]},
        ]}
        section = _build_reply_threads_section(data)
        assert "### Contested" in section
        assert "### Acknowledged" in section
        assert "[M1]" in section
        assert "[S1]" in section

    def test_includes_reply_text_for_contested(self):
        data = {"threads": [
            {"state": THREAD_CONTESTED, "finding_id": "M1", "path": "a.py",
             "line": 10, "root_body": "issue", "replies": [
                 {"author": "alice", "body": "I disagree because X"},
             ]},
        ]}
        section = _build_reply_threads_section(data)
        assert "@alice: I disagree because X" in section

    def test_no_reply_text_for_resolved(self):
        data = {"threads": [
            {"state": THREAD_RESOLVED, "finding_id": "M1", "path": "a.py",
             "line": 10, "root_body": "issue", "replies": []},
        ]}
        section = _build_reply_threads_section(data)
        assert "### Resolved" in section
        assert "> @" not in section

    def test_unreplied_threads(self):
        data = {"threads": [
            {"state": THREAD_UNREPLIED, "finding_id": "M2", "path": "c.py",
             "line": 1, "root_body": "issue", "replies": []},
        ]}
        section = _build_reply_threads_section(data)
        assert "### No reply" in section
        assert "[M2]" in section

    def test_replied_threads_include_text(self):
        data = {"threads": [
            {"state": THREAD_REPLIED, "finding_id": "S1", "path": "d.py",
             "line": 3, "root_body": "issue", "replies": [
                 {"author": "bob", "body": "Let me look into this"},
             ]},
        ]}
        section = _build_reply_threads_section(data)
        assert "### Author replied" in section
        assert "@bob: Let me look into this" in section


# ── _annotate_with_thread_state ──────────────────────────────────────────────

class TestAnnotateWithThreadState:
    def test_adds_labels_to_matching_findings(self):
        review = (
            "## Must-fix\n"
            "- **[M1]** `a.py:10` — Missing error check\n"
            "- **[M2]** `b.py:5` — SQL injection\n"
        )
        threads = {"threads": [
            {"finding_id": "M1", "state": THREAD_CONTESTED},
            {"finding_id": "M2", "state": THREAD_ACKNOWLEDGED},
        ]}
        result = _annotate_with_thread_state(review, threads)
        assert "[CONTESTED]" in result
        assert "[ACKNOWLEDGED]" in result

    def test_no_label_for_unreplied(self):
        review = "- **[M1]** `a.py:10` — Issue\n"
        threads = {"threads": [
            {"finding_id": "M1", "state": THREAD_UNREPLIED},
        ]}
        result = _annotate_with_thread_state(review, threads)
        assert "[UNREPLIED]" not in result
        assert result.strip() == review.strip()

    def test_empty_threads(self):
        review = "- **[M1]** `a.py:10` — Issue\n"
        result = _annotate_with_thread_state(review, {"threads": []})
        assert result == review

    def test_no_threads_key(self):
        review = "- **[M1]** `a.py:10` — Issue\n"
        result = _annotate_with_thread_state(review, {})
        assert result == review


# ── _build_prior_section with reply_threads ──────────────────────────────────

class TestBuildPriorSectionWithThreads:
    def test_without_threads_unchanged(self):
        result = _build_prior_section("## Must-fix\n- **[M1]** `a.py:10` — Issue")
        assert "[CONTESTED]" not in result
        assert "Prior review" in result

    def test_with_threads_annotates(self):
        threads = {"threads": [
            {"finding_id": "M1", "state": THREAD_CONTESTED},
        ]}
        result = _build_prior_section(
            "## Must-fix\n- **[M1]** `a.py:10` — Issue",
            reply_threads=threads,
        )
        assert "[CONTESTED]" in result

    def test_empty_prior_returns_empty(self):
        assert _build_prior_section("", reply_threads={"threads": []}) == ""


# ── _format_reviews ──────────────────────────────────────────────────────────

class TestFormatReviews:
    def test_formats_review_entries(self):
        data = json.dumps([
            {"user": "alice", "state": "APPROVED", "body": "looks good"},
        ])
        result = _format_reviews(data)
        assert "@alice" in result
        assert "**APPROVED**" in result
        assert "looks good" in result

    def test_empty_reviews(self):
        assert _format_reviews("[]") == "_None._"

    def test_invalid_json(self):
        assert _format_reviews("not json") == "_None._"

    def test_truncates_long_body(self):
        data = json.dumps([{"user": "a", "state": "APPROVED", "body": "x" * 300}])
        result = _format_reviews(data)
        assert "..." in result

    def test_review_without_body(self):
        data = json.dumps([{"user": "a", "state": "APPROVED", "body": ""}])
        result = _format_reviews(data)
        assert "APPROVED" in result
        assert "—" not in result


# ── _format_review_comments ──────────────────────────────────────────────────

class TestFormatReviewComments:
    def test_threads_replies_under_root(self):
        data = json.dumps([
            {"id": 1, "path": "main.py", "line": 10, "body": "issue here",
             "user": "alice", "in_reply_to_id": None},
            {"id": 2, "path": "main.py", "line": 10, "body": "fixed",
             "user": "bob", "in_reply_to_id": 1},
        ])
        result = _format_review_comments(data)
        lines = result.split("\n")
        assert any("main.py:10" in l and "@alice" in l for l in lines)
        assert any("@bob" in l and "fixed" in l for l in lines)

    def test_empty(self):
        assert _format_review_comments("[]") == "_None._"

    def test_invalid_json(self):
        assert _format_review_comments("not json") == "_None._"

    def test_standalone_root_no_replies(self):
        data = json.dumps([
            {"id": 1, "path": "a.py", "line": 5, "body": "check this",
             "user": "alice", "in_reply_to_id": None},
        ])
        result = _format_review_comments(data)
        assert "@alice" in result
        assert "  -" not in result


# ── _format_general_comments ─────────────────────────────────────────────────

class TestFormatGeneralComments:
    def test_formats_comments(self):
        data = json.dumps([{"user": "charlie", "body": "can we add tests?"}])
        result = _format_general_comments(data)
        assert "@charlie" in result
        assert "can we add tests?" in result

    def test_empty(self):
        assert _format_general_comments("[]") == "_None._"

    def test_invalid_json(self):
        assert _format_general_comments("not json") == "_None._"
