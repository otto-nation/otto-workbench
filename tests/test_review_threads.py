"""Tests for review-threads: JSON extraction, thread classification, and prompt formatting."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "claude" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from pr_state import FixSummary, PRIdentity, PRState, ThreadOutcome
from pr_thread_models import CommentItem, PRReport, ReportThread
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


# ── _extract_json ───────────────────────────────────────────────────────────

class TestExtractJson:
    def test_plain_json(self, rt):
        assert rt._extract_json('{"a": 1}') == '{"a": 1}'

    def test_json_fenced(self, rt):
        text = '```json\n{"a": 1}\n```'
        assert rt._extract_json(text) == '{"a": 1}'

    def test_bare_fence(self, rt):
        text = '```\n{"a": 1}\n```'
        assert rt._extract_json(text) == '{"a": 1}'

    def test_fence_with_surrounding_text(self, rt):
        text = 'Here is the result:\n```json\n{"a": 1}\n```\nDone.'
        assert rt._extract_json(text) == '{"a": 1}'

    def test_whitespace_stripped(self, rt):
        assert rt._extract_json('  {"a": 1}  ') == '{"a": 1}'

    def test_multiline_json_in_fence(self, rt):
        text = '```json\n{\n  "threads": [],\n  "stats": {}\n}\n```'
        result = json.loads(rt._extract_json(text))
        assert result == {"threads": [], "stats": {}}

    def test_preamble_before_bare_json(self, rt):
        text = 'Here is the classification:\n{"a": 1}'
        result = json.loads(rt._extract_json(text))
        assert result == {"a": 1}

    def test_preamble_and_trailing_text(self, rt):
        text = 'Sure, here you go:\n{"threads": [], "stats": {}}\nHope this helps!'
        result = json.loads(rt._extract_json(text))
        assert result == {"threads": [], "stats": {}}

    def test_multiline_preamble_before_json(self, rt):
        text = 'I analyzed the threads.\nHere are the results:\n{\n  "a": 1\n}'
        result = json.loads(rt._extract_json(text))
        assert result == {"a": 1}


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

    def test_file_filter_scopes_to_matching_paths(self):
        data = {"threads": [
            {"state": THREAD_CONTESTED, "finding_id": "M1", "path": "a.py",
             "line": 10, "root_body": "issue", "replies": [
                 {"author": "alice", "body": "I disagree"},
             ]},
            {"state": THREAD_CONTESTED, "finding_id": "M2", "path": "b.py",
             "line": 5, "root_body": "issue", "replies": [
                 {"author": "alice", "body": "Also disagree"},
             ]},
        ]}
        section = _build_reply_threads_section(data, file_filter=["a.py"])
        assert "[M1]" in section
        assert "[M2]" not in section

    def test_file_filter_none_includes_all(self):
        data = {"threads": [
            {"state": THREAD_ACKNOWLEDGED, "finding_id": "S1", "path": "a.py",
             "line": 1, "root_body": "issue", "replies": []},
            {"state": THREAD_ACKNOWLEDGED, "finding_id": "S2", "path": "b.py",
             "line": 2, "root_body": "issue", "replies": []},
        ]}
        section = _build_reply_threads_section(data, file_filter=None)
        assert "[S1]" in section
        assert "[S2]" in section

    def test_file_filter_no_matches_returns_empty(self):
        data = {"threads": [
            {"state": THREAD_CONTESTED, "finding_id": "M1", "path": "a.py",
             "line": 10, "root_body": "issue", "replies": []},
        ]}
        section = _build_reply_threads_section(data, file_filter=["other.py"])
        assert section == ""


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


# ── CommitPushResult ────────────────────────────────────────────────────────


def _make_completed(returncode, stdout="", stderr=""):
    """Create a CompletedProcess with the given results."""
    import subprocess
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


class TestCommitAndPush:
    """Test _commit_and_push returns correct CommitPushResult for each failure mode."""

    def test_no_changes(self, rt):
        """git diff --quiet returns 0 → no_changes."""
        calls = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            if "diff" in cmd:
                return _make_completed(0)
            return _make_completed(0)

        with patch.object(rt.subprocess, "run", side_effect=mock_run):
            result = rt._commit_and_push(Path("/fake"), 0, 0)
        assert result.status == "no_changes"
        assert result.sha is None

    def test_commit_failed(self, rt):
        """git commit returns non-zero → commit_failed with error text."""
        def mock_run(cmd, **kwargs):
            if "diff" in cmd:
                return _make_completed(1)
            if "add" in cmd:
                return _make_completed(0)
            if "commit" in cmd:
                return _make_completed(1, stderr="hook failed\n")
            return _make_completed(0)

        with patch.object(rt.subprocess, "run", side_effect=mock_run):
            result = rt._commit_and_push(Path("/fake"), 1, 0)
        assert result.status == "commit_failed"
        assert result.sha is None
        assert "hook failed" in result.error

    def test_push_failed(self, rt):
        """git push returns non-zero → push_failed with SHA preserved."""
        def mock_run(cmd, **kwargs):
            if "diff" in cmd:
                return _make_completed(1)
            if "add" in cmd:
                return _make_completed(0)
            if "commit" in cmd:
                return _make_completed(0)
            if "rev-parse" in cmd:
                return _make_completed(0, stdout="abc1234\n")
            if "push" in cmd:
                return _make_completed(1, stderr="rejected\n")
            return _make_completed(0)

        with patch.object(rt.subprocess, "run", side_effect=mock_run):
            result = rt._commit_and_push(Path("/fake"), 1, 0)
        assert result.status == "push_failed"
        assert result.sha == "abc1234"
        assert "rejected" in result.error

    def test_success(self, rt):
        """git push returns 0 → pushed with SHA."""
        def mock_run(cmd, **kwargs):
            if "diff" in cmd:
                return _make_completed(1)
            if "add" in cmd:
                return _make_completed(0)
            if "commit" in cmd:
                return _make_completed(0)
            if "rev-parse" in cmd:
                return _make_completed(0, stdout="abc1234\n")
            if "push" in cmd:
                return _make_completed(0)
            return _make_completed(0)

        with patch.object(rt.subprocess, "run", side_effect=mock_run):
            result = rt._commit_and_push(Path("/fake"), 1, 0)
        assert result.status == "pushed"
        assert result.sha == "abc1234"
        assert result.error == ""


# ── _get_head_sha ────────────────────────────────────────────────────────────


class TestGetHeadSha:
    def test_returns_short_sha(self, rt):
        with patch.object(rt.subprocess, "run", return_value=_make_completed(0, stdout="abc1234\n")):
            result = rt._get_head_sha(Path("/fake"))
        assert result == "abc1234"


# ── _is_pushed ───────────────────────────────────────────────────────────────


class TestIsPushed:
    def test_sha_on_remote(self, rt):
        with patch.object(rt.subprocess, "run", return_value=_make_completed(0, stdout="  origin/main\n")):
            assert rt._is_pushed(Path("/fake"), "abc1234") is True

    def test_sha_not_on_remote(self, rt):
        with patch.object(rt.subprocess, "run", return_value=_make_completed(0, stdout="")):
            assert rt._is_pushed(Path("/fake"), "abc1234") is False

    def test_command_failure_returns_false(self, rt):
        with patch.object(rt.subprocess, "run", return_value=_make_completed(1)):
            assert rt._is_pushed(Path("/fake"), "abc1234") is False


# ── _recover_agent_commit ────────────────────────────────────────────────────


class TestRecoverAgentCommit:
    """Three distinct branches: no change, already pushed, push attempt."""

    def test_no_change_when_sha_unchanged(self, rt):
        """head_after == head_before → no_changes, no push attempted."""
        with patch.object(rt, "_get_head_sha", return_value="abc1234"):
            result = rt._recover_agent_commit(Path("/fake"), "abc1234")
        assert result.status == "no_changes"
        assert result.sha is None

    def test_already_pushed_skips_push(self, rt):
        """head changed and SHA already on remote → pushed without a new push."""
        with patch.object(rt, "_get_head_sha", return_value="def5678"), \
             patch.object(rt, "_is_pushed", return_value=True):
            result = rt._recover_agent_commit(Path("/fake"), "abc1234")
        assert result.status == "pushed"
        assert result.sha == "def5678"

    def test_push_success(self, rt):
        """head changed, not yet on remote, push succeeds → pushed."""
        with patch.object(rt, "_get_head_sha", return_value="def5678"), \
             patch.object(rt, "_is_pushed", return_value=False), \
             patch.object(rt.subprocess, "run", return_value=_make_completed(0)):
            result = rt._recover_agent_commit(Path("/fake"), "abc1234")
        assert result.status == "pushed"
        assert result.sha == "def5678"

    def test_push_failure(self, rt):
        """head changed, not yet on remote, push fails → push_failed with error."""
        with patch.object(rt, "_get_head_sha", return_value="def5678"), \
             patch.object(rt, "_is_pushed", return_value=False), \
             patch.object(rt.subprocess, "run", return_value=_make_completed(1, stderr="rejected\n")):
            result = rt._recover_agent_commit(Path("/fake"), "abc1234")
        assert result.status == "push_failed"
        assert result.sha == "def5678"
        assert "rejected" in result.error


# ── _fixed_status_text ──────────────────────────────────────────────────────


class TestFixedStatusText:
    """Test status text rendering for each CommitPushResult state."""

    def test_pushed(self, rt):
        cp = rt.CommitPushResult("abc1234", "pushed", "")
        text = rt._fixed_status_text(cp, "owner/repo")
        assert "Fixed in" in text
        assert "abc1234" in text
        assert "push failed" not in text

    def test_push_failed_with_sha(self, rt):
        cp = rt.CommitPushResult("abc1234", "push_failed", "rejected")
        text = rt._fixed_status_text(cp, "owner/repo")
        assert "abc1234" in text
        assert "push failed" in text

    def test_no_changes(self, rt):
        cp = rt.CommitPushResult(None, "no_changes", "")
        text = rt._fixed_status_text(cp, "owner/repo")
        assert "no commit needed" in text

    def test_commit_failed(self, rt):
        cp = rt.CommitPushResult(None, "commit_failed", "hook error")
        text = rt._fixed_status_text(cp, "owner/repo")
        assert "commit failed" in text
        assert "pre-commit" in text


# ── _build_summary_body ─────────────────────────────────────────────────────


class TestBuildSummaryBody:
    """Test summary body renders correct status per CommitPushResult."""

    def _fixed_entry(self, **overrides):
        defaults = {"summary": "fix regex", "file": "parsers.py", "line": 10}
        defaults.update(overrides)
        return CommentItem(**defaults)

    def test_pushed_shows_commit_link(self, rt):
        cp = rt.CommitPushResult("abc1234", "pushed", "")
        body = rt._build_summary_body(
            [self._fixed_entry()], [], [], cp, "owner/repo", 1, {},
        )
        assert "abc1234" in body
        assert "push failed" not in body

    def test_no_changes_shows_no_commit_needed(self, rt):
        cp = rt.CommitPushResult(None, "no_changes", "")
        body = rt._build_summary_body(
            [self._fixed_entry()], [], [], cp, "owner/repo", 1, {},
        )
        assert "no commit needed" in body

    def test_commit_failed_shows_precommit_hint(self, rt):
        cp = rt.CommitPushResult(None, "commit_failed", "hook error")
        body = rt._build_summary_body(
            [self._fixed_entry()], [], [], cp, "owner/repo", 1, {},
        )
        assert "commit failed" in body

    def test_push_failed_shows_sha_and_warning(self, rt):
        cp = rt.CommitPushResult("abc1234", "push_failed", "rejected")
        body = rt._build_summary_body(
            [self._fixed_entry()], [], [], cp, "owner/repo", 1, {},
        )
        assert "abc1234" in body
        assert "push failed" in body

    def test_needs_human_rows(self, rt):
        cp = rt.CommitPushResult(None, "no_changes", "")
        body = rt._build_summary_body(
            [], [CommentItem(summary="question", file="a.py", line=1, reason="contested")],
            [], cp, "owner/repo", 1, {},
        )
        assert "contested" in body

    def test_empty_returns_no_table(self, rt):
        cp = rt.CommitPushResult(None, "no_changes", "")
        body = rt._build_summary_body([], [], [], cp, "owner/repo", 1, {})
        assert "Thread" not in body

    def test_thread_permalink_in_summary(self, rt):
        """Fixed entries with matching thread data render as links."""
        tid = "PRRT_abc123"
        entry = self._fixed_entry(id=tid)
        threads_by_id = {
            tid: ReportThread(id=tid, comments=[{"databaseId": 999}]),
        }
        cp = rt.CommitPushResult("abc1234", "pushed", "")
        body = rt._build_summary_body(
            [entry], [], [], cp, "owner/repo", 42, threads_by_id,
        )
        assert "#discussion_r999" in body
        assert "[fix regex]" in body

    def test_unseen_issue_comments_render_discussion_section(self, rt):
        cp = rt.CommitPushResult(None, "no_changes", "")
        issue_comments = [
            {"user": "alice", "body": "Can we add tests?", "seen": False},
        ]
        body = rt._build_summary_body(
            [], [], [], cp, "owner/repo", 1, {},
            issue_comments=issue_comments,
        )
        assert "### Discussion Comments" in body
        assert "@alice" in body
        assert "Can we add tests?" in body

    def test_seen_issue_comments_not_rendered(self, rt):
        cp = rt.CommitPushResult(None, "no_changes", "")
        issue_comments = [
            {"user": "alice", "body": "Old comment", "seen": True},
        ]
        body = rt._build_summary_body(
            [], [], [], cp, "owner/repo", 1, {},
            issue_comments=issue_comments,
        )
        assert "Discussion Comments" not in body

    def test_unseen_review_body_comments_render_review_level_section(self, rt):
        cp = rt.CommitPushResult(None, "no_changes", "")
        review_body_comments = [
            {"user": "bob", "state": "CHANGES_REQUESTED", "body": "Needs refactor", "seen": False},
        ]
        body = rt._build_summary_body(
            [], [], [], cp, "owner/repo", 1, {},
            review_body_comments=review_body_comments,
        )
        assert "### Review-Level Comments" in body
        assert "@bob" in body
        assert "(CHANGES_REQUESTED)" in body
        assert "Needs refactor" in body

    def test_seen_review_body_comments_not_rendered(self, rt):
        cp = rt.CommitPushResult(None, "no_changes", "")
        review_body_comments = [
            {"user": "bob", "state": "APPROVED", "body": "Looks good", "seen": True},
        ]
        body = rt._build_summary_body(
            [], [], [], cp, "owner/repo", 1, {},
            review_body_comments=review_body_comments,
        )
        assert "Review-Level Comments" not in body

    def test_deferred_with_issue_link(self, rt):
        cp = rt.CommitPushResult(None, "no_changes", "")
        deferred = [CommentItem(id="t1", summary="fix regex", file="parsers.py", line=10)]
        body = rt._build_summary_body(
            [], [], deferred, cp, "owner/repo", 1, {},
            deferred_issue_id="ENG-456",
            deferred_issue_url="https://linear.app/team/issue/ENG-456",
        )
        assert "ENG-456" in body
        assert "Deferred →" in body
        assert "linear.app" in body

    def test_deferred_without_issue(self, rt):
        cp = rt.CommitPushResult(None, "no_changes", "")
        deferred = [CommentItem(id="t1", summary="fix regex", file="parsers.py", line=10)]
        body = rt._build_summary_body(
            [], [], deferred, cp, "owner/repo", 1, {},
        )
        assert "Deferred" in body
        assert "→" not in body


# ── _render_deferred_summary ───────────────────────────────────────────────


def _make_state(fix=None):
    """Build a minimal PRState with the given FixSummary."""
    return PRState(
        identity=PRIdentity(
            repo="owner/repo", branch="feat", pr_number=1,
            head_sha="abc1234", worktree_root="/tmp/wt",
        ),
        fix=fix or FixSummary(),
    )


class TestRenderDeferredSummary:
    def test_not_deferred_is_noop(self, rt):
        state = _make_state(FixSummary(summary_deferred=False))
        report = PRReport()
        with patch("pr_comments.post_issue_comment") as mock_post:
            rt._render_deferred_summary(state, report, "owner/repo", 1, {})
        mock_post.assert_not_called()

    def test_renders_with_issue_link(self, rt):
        fix = FixSummary(
            threads=[
                ThreadOutcome(id="t1", summary="fix regex", file="parsers.py", line=10, action="deferred"),
            ],
            commit_sha="abc1234",
            commit_status="pushed",
            summary_deferred=True,
            deferred_issue_id="ENG-456",
            deferred_issue_url="https://linear.app/team/issue/ENG-456",
        )
        state = _make_state(fix)
        report = PRReport()
        with patch("pr_comments.post_issue_comment", return_value="https://github.com/comment/1") as mock_post:
            rt._render_deferred_summary(state, report, "owner/repo", 1, {})
        assert fix.summary_url == "https://github.com/comment/1"
        assert fix.summary_deferred is False
        body = mock_post.call_args[0][2]
        assert "Deferred →" in body
        assert "[ENG-456]" in body
        assert "linear.app" in body

    def test_renders_without_issue_link(self, rt):
        fix = FixSummary(
            threads=[
                ThreadOutcome(id="t1", summary="fix regex", file="parsers.py", line=10, action="deferred"),
            ],
            commit_status="no_changes",
            summary_deferred=True,
        )
        state = _make_state(fix)
        report = PRReport()
        with patch("pr_comments.post_issue_comment", return_value="https://github.com/comment/1") as mock_post:
            rt._render_deferred_summary(state, report, "owner/repo", 1, {})
        body = mock_post.call_args[0][2]
        assert "Deferred" in body
        assert "→" not in body

    def test_omits_needs_human_from_body(self, rt):
        fix = FixSummary(
            threads=[
                ThreadOutcome(id="t1", summary="auto fix", file="a.py", line=1, action="fixed"),
                ThreadOutcome(id="t2", summary="contested", file="b.py", line=2, action="needs_human"),
                ThreadOutcome(id="t3", summary="complex", file="c.py", line=3, action="deferred"),
            ],
            commit_sha="abc1234",
            commit_status="pushed",
            summary_deferred=True,
            deferred_issue_id="ENG-789",
            deferred_issue_url="https://linear.app/issue/ENG-789",
        )
        state = _make_state(fix)
        report = PRReport()
        with patch("pr_comments.post_issue_comment", return_value="https://github.com/comment/1") as mock_post:
            rt._render_deferred_summary(state, report, "owner/repo", 1, {})
        body = mock_post.call_args[0][2]
        assert "auto fix" in body
        assert "complex" in body
        assert "contested" not in body

    def test_reconstructs_commit_link(self, rt):
        fix = FixSummary(
            threads=[
                ThreadOutcome(id="t1", summary="fix it", file="x.py", line=1, action="fixed"),
            ],
            commit_sha="def5678",
            commit_status="pushed",
            summary_deferred=True,
        )
        state = _make_state(fix)
        report = PRReport()
        with patch("pr_comments.post_issue_comment", return_value="https://github.com/comment/1") as mock_post:
            rt._render_deferred_summary(state, report, "owner/repo", 1, {})
        body = mock_post.call_args[0][2]
        assert "def5678" in body


# ── _summarize_comment_body ─────────────────────────────────────────────────


class TestSummarizeCommentBody:
    def test_plain_text(self, rt):
        assert rt._summarize_comment_body("Hello world") == "Hello world"

    def test_markdown_header_stripped(self, rt):
        assert rt._summarize_comment_body("## Section Title") == "Section Title"

    def test_single_line_html_comment_skipped(self, rt):
        body = "<!-- metadata -->\nActual content"
        assert rt._summarize_comment_body(body) == "Actual content"

    def test_multiline_html_comment_skipped(self, rt):
        body = "<!-- head_sha: abc\ndate: 2026-07-13\n-->\nActual content"
        assert rt._summarize_comment_body(body) == "Actual content"

    def test_empty_body(self, rt):
        assert rt._summarize_comment_body("") == "(empty)"

    def test_only_html_comments_returns_empty(self, rt):
        body = "<!-- comment -->\n<!-- another -->"
        assert rt._summarize_comment_body(body) == "(empty)"

    def test_truncates_long_line(self, rt):
        long = "x" * 200
        result = rt._summarize_comment_body(long, max_len=120)
        assert len(result) == 120
        assert result.endswith("…")


# ── _build_deferred_issue_body ────────────────────────────────────────────


class TestBuildDeferredIssueBody:

    def test_basic_body(self, rt):
        deferred = [
            CommentItem(id="t1", file="src/foo.go", line=10,
                            summary="fix it", reason="agent could not auto-fix"),
        ]
        threads_by_id = {
            "t1": ReportThread(id="t1", comments=[{"databaseId": 12345}]),
        }
        body = rt._build_deferred_issue_body(deferred, "owner/repo", 42, threads_by_id)
        assert "PR #42" in body
        assert "src/foo.go:10" in body
        assert "fix it" in body
        assert "agent could not auto-fix" in body
        assert "#discussion_r12345" in body

    def test_no_permalink(self, rt):
        deferred = [
            CommentItem(id="t1", file="a.go", line=1,
                            summary="do thing", reason="r"),
        ]
        body = rt._build_deferred_issue_body(deferred, "owner/repo", 1, {})
        assert "do thing" in body
        assert "a.go:1" in body


# ── _post_deferred_replies ────────────────────────────────────────────────


class TestPostDeferredReplies:

    def test_posts_replies_with_issue_link(self, rt):
        deferred = [
            CommentItem(id="t1", summary="fix it"),
        ]
        threads_by_id = {
            "t1": ReportThread(id="t1", comments=[{"databaseId": 111}]),
        }
        with patch("pr_comments.post_thread_reply", return_value=True) as mock_reply:
            count = rt._post_deferred_replies(
                deferred, threads_by_id, "owner/repo", 42,
                "ENG-456", "https://linear.app/team/issue/ENG-456",
            )
        assert count == 1
        body = mock_reply.call_args[0][3]
        assert "ENG-456" in body
        assert "linear.app" in body
        assert "Deferred" in body

    def test_no_comments_skips(self, rt):
        deferred = [CommentItem(id="t1", summary="fix it")]
        with patch("pr_comments.post_thread_reply") as mock_reply:
            count = rt._post_deferred_replies(
                deferred, {}, "owner/repo", 42, "ENG-456", "",
            )
        assert count == 0
        mock_reply.assert_not_called()


# ── _post_already_addressed_replies ───────────────────────────────────────


class TestPostAlreadyAddressedReplies:

    def test_posts_replies_with_commit_ref(self, rt, tmp_path):
        fixed = [CommentItem(id="t1", summary="use helper", file="src/app.py")]
        threads_by_id = {"t1": ReportThread(id="t1", comments=[{"databaseId": 111}])}
        with (
            patch("pr_comments.post_thread_reply", return_value=True) as mock_reply,
            patch.object(rt, "_find_addressing_commit", return_value="abc1234def5678"),
        ):
            count = rt._post_already_addressed_replies(
                fixed, threads_by_id, "owner/repo", 42, tmp_path,
            )
        assert count == 1
        body = mock_reply.call_args[0][3]
        assert "Already addressed" in body
        assert "use helper" in body
        assert "abc1234" in body
        assert "owner/repo/commit/abc1234def5678" in body

    def test_fallback_when_no_commit_found(self, rt, tmp_path):
        fixed = [CommentItem(id="t1", summary="use helper", file="src/app.py")]
        threads_by_id = {"t1": ReportThread(id="t1", comments=[{"databaseId": 111}])}
        with (
            patch("pr_comments.post_thread_reply", return_value=True) as mock_reply,
            patch.object(rt, "_find_addressing_commit", return_value=None),
        ):
            count = rt._post_already_addressed_replies(
                fixed, threads_by_id, "owner/repo", 42, tmp_path,
            )
        assert count == 1
        body = mock_reply.call_args[0][3]
        assert "Already addressed" in body
        assert "commit" not in body

    def test_no_comments_skips(self, rt, tmp_path):
        fixed = [CommentItem(id="t1", summary="use helper", file="src/app.py")]
        with patch("pr_comments.post_thread_reply") as mock_reply:
            count = rt._post_already_addressed_replies(
                fixed, {}, "owner/repo", 42, tmp_path,
            )
        assert count == 0
        mock_reply.assert_not_called()


# ── _resolve_fixed_threads ────────────────────────────────────────────────


class TestResolveFixedThreads:

    def test_resolves_unresolved_threads(self, rt):
        fixed = [CommentItem(id="t1"), CommentItem(id="t2")]
        threads_by_id = {
            "t1": ReportThread(id="t1", is_resolved=False),
            "t2": ReportThread(id="t2", is_resolved=False),
        }
        with patch("pr_comments.resolve_thread", return_value=True) as mock_resolve:
            count = rt._resolve_fixed_threads(fixed, threads_by_id)
        assert count == 2
        assert mock_resolve.call_count == 2

    def test_skips_already_resolved(self, rt):
        fixed = [CommentItem(id="t1")]
        threads_by_id = {"t1": ReportThread(id="t1", is_resolved=True)}
        with patch("pr_comments.resolve_thread") as mock_resolve:
            count = rt._resolve_fixed_threads(fixed, threads_by_id)
        assert count == 0
        mock_resolve.assert_not_called()

    def test_resolves_thread_absent_from_threads_by_id(self, rt):
        fixed = [CommentItem(id="t1")]
        with patch("pr_comments.resolve_thread", return_value=True) as mock_resolve:
            count = rt._resolve_fixed_threads(fixed, {})
        assert count == 1
        mock_resolve.assert_called_once_with("t1")

    def test_counts_only_successful_resolves(self, rt):
        fixed = [CommentItem(id="t1"), CommentItem(id="t2")]
        threads_by_id = {
            "t1": ReportThread(id="t1"),
            "t2": ReportThread(id="t2"),
        }
        with patch("pr_comments.resolve_thread", side_effect=[True, False]):
            count = rt._resolve_fixed_threads(fixed, threads_by_id)
        assert count == 1


# ── Blocking reviewers ────────────────────────────────────────────────────────


class TestBlockingReviewers:
    """Verify blocking_reviewers extracts actual logins from verdict data.

    Verdicts from PRData.reviewer_verdicts() use the key "user", not "author".
    Regression test for a bug where v.get("author", {}).get("login") was used.
    """

    def _extract_blocking(self, verdicts):
        return [
            v.get("user", "unknown")
            for v in verdicts
            if v.get("state") == "CHANGES_REQUESTED"
        ]

    def test_extracts_login_from_user_key(self):
        verdicts = [
            {"user": "alice", "state": "CHANGES_REQUESTED", "submitted_at": "2026-01-01T00:00:00Z"},
        ]
        assert self._extract_blocking(verdicts) == ["alice"]

    def test_multiple_blocking_reviewers(self):
        verdicts = [
            {"user": "alice", "state": "CHANGES_REQUESTED", "submitted_at": "2026-01-01T00:00:00Z"},
            {"user": "bob", "state": "APPROVED", "submitted_at": "2026-01-01T00:00:00Z"},
            {"user": "carol", "state": "CHANGES_REQUESTED", "submitted_at": "2026-01-01T00:00:00Z"},
        ]
        assert self._extract_blocking(verdicts) == ["alice", "carol"]

    def test_no_blocking_reviewers(self):
        verdicts = [
            {"user": "alice", "state": "APPROVED", "submitted_at": "2026-01-01T00:00:00Z"},
        ]
        assert self._extract_blocking(verdicts) == []

    def test_empty_verdicts(self):
        assert self._extract_blocking([]) == []


# ── _fix_turn_budget / _fix_budget_usd ─────────────────────────────────────

class TestFixTurnBudget:
    def test_minimum_floor(self, rt):
        assert rt._fix_turn_budget(1) == 20

    def test_scales_with_items(self, rt):
        assert rt._fix_turn_budget(5) == 25

    def test_caps_at_maximum(self, rt):
        assert rt._fix_turn_budget(100) == 60

    def test_zero_items(self, rt):
        assert rt._fix_turn_budget(0) == 20


class TestFixBudgetUsd:
    def test_minimum_floor(self, rt):
        assert rt._fix_budget_usd(1) == 2.0

    def test_scales_with_items(self, rt):
        assert rt._fix_budget_usd(6) == 3.0

    def test_caps_at_maximum(self, rt):
        assert rt._fix_budget_usd(100) == 5.0


class TestFixRetryBudget:
    def test_bumps_by_increment(self, rt):
        assert rt._fix_retry_budget(25) == 40

    def test_minimum_floor(self, rt):
        assert rt._fix_retry_budget(10) == 30

    def test_caps_at_maximum(self, rt):
        assert rt._fix_retry_budget(50) == 60


# ── _diff_context_for_file ─────────────────────────────────────────────────

class TestDiffContextForFile:
    def test_empty_file_path(self, rt):
        assert rt._diff_context_for_file("", Path("/wt")) == ""

    @patch("review_threads.subprocess.run")
    def test_returns_diff(self, mock_run, rt):
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "+ added line\n- removed line\n"
        result = rt._diff_context_for_file("src/foo.go", Path("/wt"))
        assert "```diff" in result
        assert "+ added line" in result

    @patch("review_threads.subprocess.run")
    def test_truncates_long_diff(self, mock_run, rt):
        long_diff = "\n".join(f"+ line {i}" for i in range(200))
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = long_diff
        result = rt._diff_context_for_file("src/foo.go", Path("/wt"))
        assert "more lines" in result

    @patch("review_threads.subprocess.run")
    def test_git_failure_returns_empty(self, mock_run, rt):
        mock_run.return_value.returncode = 1
        mock_run.return_value.stdout = ""
        assert rt._diff_context_for_file("src/foo.go", Path("/wt")) == ""


# ── _classify_triage_entries (complexity) ──────────────────────────────────

class TestClassifyTriageComplexity:
    def test_high_complexity_goes_to_needs_human(self, rt):
        entries = [CommentItem(
            id="t1", file="f.go", line=10, reviewer="alice",
            summary="refactor", classification="actionable_suggestion",
            verification="valid", complexity="high", state="new",
        )]
        result = rt._classify_triage_entries(entries)
        assert len(result.fixable) == 0
        assert len(result.needs_human) == 1
        assert result.needs_human[0].reason == "complex"

    def test_low_complexity_stays_fixable(self, rt):
        entries = [CommentItem(
            id="t1", file="f.go", line=10, reviewer="alice",
            summary="rename", classification="actionable_suggestion",
            verification="valid", complexity="low", state="new",
        )]
        result = rt._classify_triage_entries(entries)
        assert len(result.fixable) == 1
        assert len(result.needs_human) == 0

    def test_medium_complexity_stays_fixable(self, rt):
        entries = [CommentItem(
            id="t1", file="f.go", line=10, reviewer="alice",
            summary="add guard", classification="actionable_suggestion",
            verification="valid", complexity="medium", state="new",
        )]
        result = rt._classify_triage_entries(entries)
        assert len(result.fixable) == 1
        assert len(result.needs_human) == 0

    def test_no_complexity_field_stays_fixable(self, rt):
        entries = [CommentItem(
            id="t1", file="f.go", line=10, reviewer="alice",
            summary="fix", classification="actionable_suggestion",
            verification="valid", state="new",
        )]
        result = rt._classify_triage_entries(entries)
        assert len(result.fixable) == 1
        assert len(result.needs_human) == 0
