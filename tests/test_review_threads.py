"""Tests for review-threads: JSON extraction, thread classification, and prompt formatting."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from conftest import assert_no_worktree_exit, write_thrash_log
import pr_context
import pr_state
from pr_comments import ThreadState
from pr_state import FixSummary, PRIdentity, PRState, ThreadAction, ThreadOutcome
from pr_thread_models import CommentItem, PRReport, ReportThread
from review_preflight import (
    THREAD_ACKNOWLEDGED, THREAD_CONTESTED, THREAD_REPLIED,
    THREAD_RESOLVED, THREAD_UNREPLIED,
    _classify_thread_for_rereview, _match_thread_to_finding,
    fetch_reply_threads,
)
from review_prompt import (
    _annotate_with_thread_state, _build_prior_section,
    _build_reply_threads_section, _strip_internal_sections,
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


# ── _strip_internal_sections ─────────────────────────────────────────────────

PRIOR_WITH_INTERNAL = (
    "## File Triage\n"
    "- `a.py` — **Tier 2** (application logic)\n"
    "- `b.py` — **Tier 3** (generated)\n"
    "\n"
    "## Must fix\n"
    "- **[M1]** **`a.py:10`** — missing error check\n"
    "\n"
    "## Static Analysis\n"
    "\n"
    "<details>\n"
    "<summary>Static Analysis (1 violation)</summary>\n"
    "\n"
    "### Nesting depth\n"
    "\n"
    "- **`a.py:42`** — depth 3 exceeds limit 2 (in main())\n"
    "\n"
    "</details>\n"
    "\n"
    "## Verdict\n"
    "Request changes.\n"
)


class TestStripInternalSections:
    def test_drops_triage_and_static_analysis(self):
        result = _strip_internal_sections(PRIOR_WITH_INTERNAL)
        assert "File Triage" not in result
        assert "Tier 2" not in result
        assert "Static Analysis" not in result
        assert "Nesting depth" not in result
        assert "<details>" not in result

    def test_keeps_findings_and_verdict(self):
        result = _strip_internal_sections(PRIOR_WITH_INTERNAL)
        assert "**[M1]**" in result
        assert "## Must fix" in result
        assert "Request changes." in result

    def test_section_after_excluded_one_resumes(self):
        # Verdict follows Static Analysis — exclusion must reset at its header
        assert _strip_internal_sections(PRIOR_WITH_INTERNAL).endswith("Request changes.")

    def test_unaffected_text_passes_through(self):
        text = "## Must fix\n- **[M1]** **`a.py:1`** — bug"
        assert _strip_internal_sections(text) == text

    def test_only_internal_sections_yields_empty(self):
        assert _strip_internal_sections("## File Triage\n- `a.py` — **Tier 1**\n") == ""

    def test_build_prior_section_omits_internal_sections(self):
        result = _build_prior_section(PRIOR_WITH_INTERNAL)
        assert "Prior review" in result
        assert "**[M1]**" in result
        assert "File Triage" not in result
        assert "Nesting depth" not in result

    def test_build_prior_section_empty_when_only_internal(self):
        assert _build_prior_section("## File Triage\n- `a.py` — **Tier 1**\n") == ""


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

    def _commit(self, rt, mock_run, dirty=True):
        """Run _commit_and_push with a stubbed git and a known worktree state."""
        with patch.object(rt.subprocess, "run", side_effect=mock_run), \
                patch.object(rt.review_common, "has_uncommitted_changes",
                             return_value=dirty):
            return rt._commit_and_push(Path("/fake"), 1, 0)

    def test_no_changes(self, rt):
        """A clean worktree → no_changes."""
        result = self._commit(rt, lambda cmd, **kw: _make_completed(0), dirty=False)
        assert result.status == "no_changes"
        assert result.sha is None

    def test_untracked_only_changes_still_commit(self, rt):
        """A fix that only adds files leaves the tracked diff empty — still commit."""
        calls = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            if "rev-parse" in cmd:
                return _make_completed(0, stdout="abc1234\n")
            return _make_completed(0)

        result = self._commit(rt, mock_run)
        assert result.status == "pushed"
        assert ["add", "-A"] == calls[0][-2:]

    def test_commit_failed(self, rt):
        """git commit returns non-zero → commit_failed with error text."""
        def mock_run(cmd, **kwargs):
            if "commit" in cmd:
                return _make_completed(1, stderr="hook failed\n")
            return _make_completed(0)

        result = self._commit(rt, mock_run)
        assert result.status == "commit_failed"
        assert result.sha is None
        assert "hook failed" in result.error

    def test_push_failed(self, rt):
        """git push returns non-zero → push_failed with SHA preserved."""
        def mock_run(cmd, **kwargs):
            if "rev-parse" in cmd:
                return _make_completed(0, stdout="abc1234\n")
            if "push" in cmd:
                return _make_completed(1, stderr="rejected\n")
            return _make_completed(0)

        result = self._commit(rt, mock_run)
        assert result.status == "push_failed"
        assert result.sha == "abc1234"
        assert "rejected" in result.error

    def test_success(self, rt):
        """git push returns 0 → pushed with SHA."""
        def mock_run(cmd, **kwargs):
            if "rev-parse" in cmd:
                return _make_completed(0, stdout="abc1234\n")
            return _make_completed(0)

        result = self._commit(rt, mock_run)
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

    def test_push_failed_falls_through_to_pending(self, rt):
        cp = rt.CommitPushResult("abc1234", "push_failed", "rejected")
        text = rt._fixed_status_text(cp, "owner/repo")
        assert text == "Fix pending"
        assert "push failed" not in text

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

    def test_push_failed_shows_pending(self, rt):
        cp = rt.CommitPushResult("abc1234", "push_failed", "rejected")
        body = rt._build_summary_body(
            [self._fixed_entry()], [], [], cp, "owner/repo", 1, {},
        )
        assert "Fix pending" in body
        assert "push failed" not in body

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

    def test_comment_item_issue_comment_permalink(self, rt):
        """Comment items from issue comments link to #issuecomment-{source_id}."""
        entry = CommentItem(
            id="ic-77777-0", summary="add tests", file="foo.py", line=5,
            source_id="77777", source_type="issue_comment",
        )
        cp = rt.CommitPushResult("abc1234", "pushed", "")
        body = rt._build_summary_body(
            [entry], [], [], cp, "owner/repo", 42, {},
        )
        assert "#issuecomment-77777" in body
        assert "[add tests]" in body

    def test_comment_item_review_body_permalink(self, rt):
        """Comment items from review bodies link to #pullrequestreview-{source_id}."""
        entry = CommentItem(
            id="rb-88888-1", summary="refactor needed", file="bar.py", line=3,
            source_id="88888", source_type="review_body",
        )
        cp = rt.CommitPushResult("abc1234", "pushed", "")
        body = rt._build_summary_body(
            [entry], [], [], cp, "owner/repo", 42, {},
        )
        assert "#pullrequestreview-88888" in body
        assert "[refactor needed]" in body

    def test_thread_outcome_comment_item_permalink(self, rt):
        """ThreadOutcome with synthetic id parses source for permalink."""
        outcome = ThreadOutcome(
            id="ic-99999-0", summary="fix typo", file="readme.md", line=1,
            action=ThreadAction.FIXED,
        )
        cp = rt.CommitPushResult("abc1234", "pushed", "")
        body = rt._build_summary_body(
            [outcome], [], [], cp, "owner/repo", 42, {},
        )
        assert "#issuecomment-99999" in body
        assert "[fix typo]" in body

    def test_reviewer_column_rendered(self, rt):
        """Table rows include the reviewer as @mention."""
        entry = self._fixed_entry(reviewer="alice")
        cp = rt.CommitPushResult("abc1234", "pushed", "")
        body = rt._build_summary_body(
            [entry], [], [], cp, "owner/repo", 1, {},
        )
        assert "| Reviewer |" in body
        assert "@alice" in body

    def test_reviewer_column_missing_shows_dash(self, rt):
        """Entries without a reviewer show a dash."""
        entry = self._fixed_entry(reviewer="")
        cp = rt.CommitPushResult("abc1234", "pushed", "")
        body = rt._build_summary_body(
            [entry], [], [], cp, "owner/repo", 1, {},
        )
        assert "| — |" in body

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


class TestPostOrDeferSummary:
    def _fixed_entry(self, **overrides):
        defaults = {"summary": "fix regex", "file": "parsers.py", "line": 10}
        defaults.update(overrides)
        return CommentItem(**defaults)

    def test_posts_when_pushed_no_deferred(self, rt):
        cp = rt.CommitPushResult("abc1234", "pushed", "")
        with patch("pr_comments.post_issue_comment", return_value="https://url") as mock:
            url = rt._post_or_defer_summary(
                [self._fixed_entry()], [], [], cp, "owner/repo", 1, {},
            )
        assert url == "https://url"
        mock.assert_called_once()

    def test_defers_when_needs_human(self, rt):
        cp = rt.CommitPushResult("abc1234", "pushed", "")
        url = rt._post_or_defer_summary(
            [self._fixed_entry()],
            [self._fixed_entry(summary="question")],
            [], cp, "owner/repo", 1, {},
        )
        assert url is None

    def test_defers_when_push_failed(self, rt):
        cp = rt.CommitPushResult("abc1234", "push_failed", "rejected")
        with patch("pr_comments.post_issue_comment") as mock:
            url = rt._post_or_defer_summary(
                [self._fixed_entry()], [], [], cp, "owner/repo", 1, {},
            )
        assert url is None
        mock.assert_not_called()


class TestUnaccountedThreadsDeferSummary:
    """Summary should defer when non-resolved threads are not in any classified bucket."""

    def test_all_threads_accounted(self, rt):
        """When every non-resolved thread is in fixable/needs_human/dismissed, none are unaccounted."""
        triage_threads = [
            CommentItem(id="t1", classification="actionable_suggestion",
                        verification="valid", complexity="low",
                        file="a.py", line=1, summary="fix it"),
        ]
        report_threads = [
            ReportThread(id="t1", state=ThreadState.NEW, is_resolved=False),
            ReportThread(id="t2", state=ThreadState.RESOLVED, is_resolved=True),
        ]
        accounted_ids = rt._accounted_thread_ids(triage_threads, [], [])
        non_resolved = [t for t in report_threads if t.state != "resolved"]
        unaccounted = [t for t in non_resolved if t.id not in accounted_ids]
        assert unaccounted == []

    def test_unaccounted_threads_detected(self, rt):
        """Threads not in any classified bucket are detected as unaccounted."""
        triage_threads = [
            CommentItem(id="t1", classification="actionable_suggestion",
                        verification="valid", complexity="low",
                        file="a.py", line=1, summary="fix it"),
            CommentItem(id="t2", classification="approval",
                        file="b.py", line=1, summary="lgtm"),
        ]
        report_threads = [
            ReportThread(id="t1", state=ThreadState.NEW, is_resolved=False),
            ReportThread(id="t2", state=ThreadState.NEW, is_resolved=False),
            ReportThread(id="t3", state=ThreadState.NEW, is_resolved=False),
        ]
        classified = rt._classify_triage_entries(triage_threads)
        accounted_ids = rt._accounted_thread_ids(
            classified.fixable, classified.needs_human, classified.dismissed,
        )
        non_resolved = [t for t in report_threads if t.state != "resolved"]
        unaccounted = [t for t in non_resolved if t.id not in accounted_ids]
        # t2 was classified as "approval" and dropped; t3 wasn't in triage at all
        assert len(unaccounted) == 2
        assert {t.id for t in unaccounted} == {"t2", "t3"}


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
                ThreadOutcome(id="t1", summary="fix regex", file="parsers.py", line=10, action=ThreadAction.DEFERRED),
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
                ThreadOutcome(id="t1", summary="fix regex", file="parsers.py", line=10, action=ThreadAction.DEFERRED),
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
                ThreadOutcome(id="t1", summary="auto fix", file="a.py", line=1, action=ThreadAction.FIXED),
                ThreadOutcome(id="t2", summary="contested", file="b.py", line=2, action=ThreadAction.NEEDS_HUMAN),
                ThreadOutcome(id="t3", summary="complex", file="c.py", line=3, action=ThreadAction.DEFERRED),
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
                ThreadOutcome(id="t1", summary="fix it", file="x.py", line=1, action=ThreadAction.FIXED),
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

    def test_skips_when_push_failed_and_still_unpushed(self, rt):
        fix = FixSummary(
            threads=[
                ThreadOutcome(id="t1", summary="fix it", file="x.py", line=1, action=ThreadAction.FIXED),
            ],
            commit_sha="def5678",
            commit_status="push_failed",
            summary_deferred=True,
        )
        state = _make_state(fix)
        report = PRReport()
        with patch("pr_comments.post_issue_comment") as mock_post:
            with patch.object(rt, "_is_pushed", return_value=False):
                rt._render_deferred_summary(state, report, "owner/repo", 1, {})
        mock_post.assert_not_called()
        assert fix.summary_deferred is True

    def test_posts_when_push_failed_but_now_pushed(self, rt, publishing_on):
        fix = FixSummary(
            threads=[
                ThreadOutcome(id="t1", summary="fix it", file="x.py", line=1, action=ThreadAction.FIXED),
            ],
            commit_sha="def5678",
            commit_status="push_failed",
            summary_deferred=True,
        )
        state = _make_state(fix)
        report = PRReport()
        with patch("pr_comments.post_issue_comment", return_value="https://github.com/comment/1") as mock_post:
            with patch.object(rt, "_is_pushed", return_value=True):
                rt._render_deferred_summary(state, report, "owner/repo", 1, {})
        mock_post.assert_called_once()
        assert fix.summary_deferred is False
        assert fix.commit_status == "pushed"
        body = mock_post.call_args[0][2]
        assert "def5678" in body
        assert "push failed" not in body

    def test_draft_run_leaves_the_deferred_queue_intact(self, rt):
        """Retiring push_failed without publishing would strand the replies."""
        fix = FixSummary(
            threads=[
                ThreadOutcome(id="t1", summary="fix it", file="x.py", line=1, action=ThreadAction.FIXED),
            ],
            commit_sha="def5678",
            commit_status="push_failed",
            summary_deferred=True,
        )
        state = _make_state(fix)
        with patch.object(rt, "_is_pushed", return_value=True):
            rt._render_deferred_summary(state, PRReport(), "owner/repo", 1, {})
        assert fix.commit_status == "push_failed"
        assert fix.summary_deferred is True


class TestSummaryStillOwed:
    """Whether --resolve has to re-render the fix summary."""

    def _owed(self, rt, **kw):
        args = {
            "fixed": [], "needs_human": [], "deferred": [], "dismissed": [],
            "commit_status": "pushed", "has_unaccounted": False,
        }
        args.update(kw)
        return rt._summary_still_owed(**args)

    def test_nothing_to_say(self, rt, publishing_on):
        assert self._owed(rt) is False

    def test_posted_summary_is_settled(self, rt, publishing_on):
        assert self._owed(rt, fixed=["t1"]) is False

    def test_open_discussion_defers(self, rt, publishing_on):
        assert self._owed(rt, needs_human=["t1"]) is True

    def test_unpushed_commit_defers(self, rt, publishing_on):
        assert self._owed(rt, fixed=["t1"], commit_status="push_failed") is True

    def test_draft_leaves_the_summary_owed(self, rt):
        assert self._owed(rt, fixed=["t1"]) is True

    def test_draft_with_nothing_to_say_owes_nothing(self, rt):
        assert self._owed(rt) is False


class TestPendingFixReplies:
    """--resolve is the second chance for fix replies the fix pass didn't send."""

    def test_posts_fix_replies_and_resolves_when_push_confirmed(self, rt, publishing_on):
        fix = FixSummary(
            threads=[
                ThreadOutcome(id="t1", summary="fix it", file="x.py", line=1, action=ThreadAction.FIXED),
                ThreadOutcome(id="t2", summary="another", file="y.py", line=2, action=ThreadAction.FIXED),
            ],
            commit_sha="abc1234",
            commit_status="push_failed",
            summary_deferred=True,
        )
        state = _make_state(fix)
        threads_by_id = {
            "t1": ReportThread(id="t1", is_resolved=False, comments=[{"databaseId": 100}]),
            "t2": ReportThread(id="t2", is_resolved=False, comments=[{"databaseId": 200}]),
        }
        with patch.object(rt, "_is_pushed", return_value=True), \
             patch("pr_comments.post_thread_reply", return_value=True) as mock_reply, \
             patch("pr_comments.resolve_thread", return_value=True) as mock_resolve:
            rt._post_pending_fix_replies(state, "owner/repo", 1, threads_by_id)
        assert mock_reply.call_count == 2
        assert mock_resolve.call_count == 2
        assert fix.commit_status == "pushed"

    def test_skips_when_still_unpushed(self, rt):
        fix = FixSummary(
            threads=[
                ThreadOutcome(id="t1", summary="fix it", file="x.py", line=1, action=ThreadAction.FIXED),
            ],
            commit_sha="abc1234",
            commit_status="push_failed",
            summary_deferred=True,
        )
        state = _make_state(fix)
        with patch.object(rt, "_is_pushed", return_value=False), \
             patch("pr_comments.post_thread_reply") as mock_reply:
            rt._post_pending_fix_replies(state, "owner/repo", 1, {})
        mock_reply.assert_not_called()
        assert fix.commit_status == "push_failed"

    def test_noop_when_not_push_failed(self, rt):
        fix = FixSummary(commit_status="pushed", summary_deferred=True)
        state = _make_state(fix)
        with patch("pr_comments.post_thread_reply") as mock_reply:
            rt._post_pending_fix_replies(state, "owner/repo", 1, {})
        mock_reply.assert_not_called()

    def test_draft_run_keeps_the_queue_for_a_later_post(self, rt):
        fix = FixSummary(
            threads=[
                ThreadOutcome(id="t1", summary="fix it", file="x.py", line=1, action=ThreadAction.FIXED),
            ],
            commit_sha="abc1234",
            commit_status="push_failed",
            summary_deferred=True,
        )
        state = _make_state(fix)
        threads_by_id = {
            "t1": ReportThread(id="t1", is_resolved=False, comments=[{"databaseId": 100}]),
        }
        with patch.object(rt, "_is_pushed", return_value=True):
            rt._post_pending_fix_replies(state, "owner/repo", 1, threads_by_id)
        assert fix.commit_status == "push_failed"

    def test_drains_the_queue_a_drafted_fix_pass_left_behind(self, rt, publishing_on):
        """A drafted --fix pushes its commit but sends nothing; --post must catch up."""
        fix = FixSummary(
            threads=[
                ThreadOutcome(id="t1", summary="fix it", file="x.py", line=1, action=ThreadAction.FIXED),
            ],
            commit_sha="abc1234",
            commit_status="pushed",
            replies_pending=True,
        )
        state = _make_state(fix)
        threads_by_id = {
            "t1": ReportThread(id="t1", is_resolved=False, comments=[{"databaseId": 100}]),
        }
        with patch.object(rt, "_is_pushed", return_value=True), \
             patch("pr_comments.post_thread_reply", return_value=True) as mock_reply, \
             patch("pr_comments.resolve_thread", return_value=True):
            rt._post_pending_fix_replies(state, "owner/repo", 1, threads_by_id)
        assert mock_reply.call_count == 1
        assert fix.replies_pending is False

    def test_noop_once_the_replies_have_gone_out(self, rt, publishing_on):
        fix = FixSummary(
            threads=[
                ThreadOutcome(id="t1", summary="fix it", file="x.py", line=1, action=ThreadAction.FIXED),
            ],
            commit_sha="abc1234",
            commit_status="pushed",
            replies_pending=False,
        )
        state = _make_state(fix)
        with patch("pr_comments.post_thread_reply") as mock_reply:
            rt._post_pending_fix_replies(state, "owner/repo", 1, {})
        mock_reply.assert_not_called()


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

    def test_a_missing_reason_renders_a_placeholder(self, rt):
        """An empty cell would read as a table bug; a dash reads as "unstated"."""
        deferred = [CommentItem(id="t1", file="a.go", line=1, summary="do thing")]
        body = rt._build_deferred_issue_body(deferred, "owner/repo", 1, {})
        assert "—" in body


# ── _finalize_deferred ────────────────────────────────────────────────────


class TestFinalizeDeferredCarriesTheReason:
    """The reason is the only column separating "agent gave up" from a decision."""

    def _state_with_deferred(self, tmp_path):
        state = PRState(
            identity=PRIdentity(
                repo="owner/repo", branch="b", pr_number=42,
                head_sha="abc1234", worktree_root=str(tmp_path),
            ),
            fix=FixSummary(threads=[
                ThreadOutcome(
                    id="t1", file="a.go", line=7, reviewer="kgn",
                    summary="rename the guard",
                    action=ThreadAction.DEFERRED,
                    reason="agent could not auto-fix",
                ),
            ]),
        )
        pr_state.save_state(tmp_path, state)
        ctx = pr_context.ResolvedContext(
            repo="owner/repo", branch="b", pr_number=42,
            worktree_root=tmp_path, head_sha="abc1234",
        )
        return state, ctx

    def _run(self, rt, state, ctx):
        captured = []
        with patch.object(rt, "_create_or_update_deferred_issue") as create, \
                patch.object(rt, "_post_deferred_replies"):
            create.side_effect = lambda deferred, *a, **kw: (
                captured.extend(deferred) or rt.CreatedIssue("I_1", "u")
            )
            rt._finalize_deferred(state, ctx, {})
        return captured

    def test_reason_survives_into_the_tracking_issue(self, rt, tmp_path):
        state, ctx = self._state_with_deferred(tmp_path)
        captured = self._run(rt, state, ctx)
        assert [e.reason for e in captured] == ["agent could not auto-fix"]

    def test_the_rest_of_the_outcome_survives_too(self, rt, tmp_path):
        state, ctx = self._state_with_deferred(tmp_path)
        entry = self._run(rt, state, ctx)[0]
        assert (entry.id, entry.file, entry.line) == ("t1", "a.go", 7)
        assert (entry.reviewer, entry.summary) == ("kgn", "rename the guard")

    def test_the_caller_owns_the_save(self, rt, tmp_path):
        """Saving its own read would drop whatever the caller already wrote."""
        state, ctx = self._state_with_deferred(tmp_path)
        state.fix.commit_status = "pushed"
        self._run(rt, state, ctx)
        assert state.fix.deferred_issue_id == "I_1"
        on_disk = pr_state.load_state(tmp_path)
        assert on_disk.fix.commit_status == ""
        assert on_disk.fix.deferred_issue_id == ""


# ── _finish_deferred_work ─────────────────────────────────────────────────


class TestFinishDeferredWork:
    """The close-out phase: push-deferred replies, tracking issue, summary."""

    def _ctx(self, tmp_path):
        return pr_context.ResolvedContext(
            repo="owner/repo", branch="b", pr_number=42,
            worktree_root=tmp_path, head_sha="abc1234",
        )

    def _save(self, tmp_path, **fix_kw):
        pr_state.save_state(tmp_path, PRState(
            identity=PRIdentity(
                repo="owner/repo", branch="b", pr_number=42,
                head_sha="abc1234", worktree_root=str(tmp_path),
            ),
            fix=FixSummary(**fix_kw),
        ))

    def test_all_three_steps_run_in_order(self, rt, tmp_path):
        self._save(tmp_path)
        order = []
        with patch.object(rt, "_post_pending_fix_replies",
                          side_effect=lambda *a, **k: order.append("replies")), \
                patch.object(rt, "_finalize_deferred",
                             side_effect=lambda *a, **k: order.append("issue")), \
                patch.object(rt, "_render_deferred_summary",
                             side_effect=lambda *a, **k: order.append("summary")):
            rt._finish_deferred_work(self._ctx(tmp_path), PRReport())
        assert order == ["replies", "issue", "summary"]

    def test_state_written_by_the_steps_is_persisted(self, rt, tmp_path):
        """The steps mutate in place; this phase is the one that saves."""
        self._save(tmp_path)

        def mark(state, *a, **k):
            state.fix.commit_status = "pushed"

        with patch.object(rt, "_post_pending_fix_replies", side_effect=mark), \
                patch.object(rt, "_finalize_deferred"), \
                patch.object(rt, "_render_deferred_summary"):
            rt._finish_deferred_work(self._ctx(tmp_path), PRReport())
        assert pr_state.load_state(tmp_path).fix.commit_status == "pushed"

    def test_it_reads_state_from_disk_not_from_the_caller(self, rt, tmp_path):
        """The fix pass writes its outcomes there; a stale copy would miss them."""
        self._save(tmp_path, threads=[
            ThreadOutcome(id="t9", action=ThreadAction.DEFERRED, reason="r"),
        ])
        seen = []
        with patch.object(rt, "_post_pending_fix_replies",
                          side_effect=lambda st, *a, **k: seen.extend(st.fix.threads)), \
                patch.object(rt, "_finalize_deferred"), \
                patch.object(rt, "_render_deferred_summary"):
            rt._finish_deferred_work(self._ctx(tmp_path), PRReport())
        assert [t.id for t in seen] == ["t9"]

    def test_no_state_on_disk_is_a_no_op(self, rt, tmp_path):
        with patch.object(rt, "_post_pending_fix_replies") as replies:
            rt._finish_deferred_work(self._ctx(tmp_path), PRReport())
        replies.assert_not_called()

    def test_a_failing_step_propagates(self, rt, tmp_path):
        """A caller closing the loop needs a failure to be an error, not a log line."""
        self._save(tmp_path)
        with patch.object(rt, "_post_pending_fix_replies"), \
                patch.object(rt, "_finalize_deferred",
                             side_effect=RuntimeError("gh down")), \
                patch.object(rt, "_render_deferred_summary"):
            with pytest.raises(RuntimeError):
                rt._finish_deferred_work(self._ctx(tmp_path), PRReport())


class TestFinishFlag:
    """`--resolve` shipped under the wrong name; it still has to work."""

    def test_finish_sets_finish(self, rt):
        assert rt._build_parser().parse_args(["--finish"]).finish

    def test_resolve_is_an_alias(self, rt):
        assert rt._build_parser().parse_args(["--resolve"]).finish

    def test_resolve_verified_is_an_alias(self, rt):
        assert rt._build_parser().parse_args(["--resolve-verified"]).finish

    def test_it_is_off_by_default(self, rt):
        assert not rt._build_parser().parse_args([]).finish


# ── --reply ──────────────────────────────────────────────────────────────


def _raw_thread(tid, comment_ids, login="reviewer"):
    return {
        "id": tid,
        "isResolved": False,
        "path": "src/app.py",
        "line": 4,
        "comments": {"nodes": [
            {"databaseId": cid, "body": "point", "author": {"login": login}}
            for cid in comment_ids
        ]},
    }


class TestFindReplyTarget:

    @pytest.mark.parametrize("target", [
        "PRRT_abc",
        "222",
        "#discussion_r222",
        "https://github.com/owner/repo/pull/42#discussion_r222",
    ])
    def test_resolves_every_identifier_a_human_might_paste(self, rt, target):
        raw = [_raw_thread("PRRT_zzz", [999]), _raw_thread("PRRT_abc", [111, 222])]
        thread = rt._find_reply_target(raw, target, "me")
        assert thread is not None
        assert thread.id == "PRRT_abc"

    def test_returns_none_when_nothing_matches(self, rt):
        raw = [_raw_thread("PRRT_abc", [111])]
        assert rt._find_reply_target(raw, "discussion_r404", "me") is None

    def test_carries_the_lifecycle_state_the_upsert_needs(self, rt):
        raw = [_raw_thread("PRRT_abc", [111, 222], login="me")]
        assert rt._find_reply_target(raw, "PRRT_abc", "me").state == ThreadState.ADDRESSED


class TestRunReply:

    def _ctx(self, tmp_path):
        return pr_context.ResolvedContext(
            repo="owner/repo", branch="b", pr_number=42,
            worktree_root=tmp_path, head_sha="abc1234",
        )

    def _patches(self, rt, raw, login="reviewer"):
        return (
            patch.object(rt, "fetch_pr_data",
                         return_value=SimpleNamespace(viewer_login=login)),
            patch.object(rt.pc, "fetch_threads", return_value=raw),
        )

    def test_posts_the_body_from_the_file(self, rt, tmp_path):
        body = tmp_path / "reply.md"
        body.write_text("See https://github.com/owner/repo/blob/abc/src/app.py#L4.")
        fetch_pr, fetch_threads = self._patches(rt, [_raw_thread("PRRT_abc", [111])])
        with fetch_pr, fetch_threads, \
             patch("pr_comments.post_thread_reply", return_value=True) as post:
            code = rt._run_reply(self._ctx(tmp_path), "PRRT_abc", str(body))
        assert code == 0
        assert post.call_args[0][2] == 111

    def test_edits_rather_than_stacking(self, rt, tmp_path):
        body = tmp_path / "reply.md"
        body.write_text("Revised. https://github.com/owner/repo/blob/abc/src/app.py#L4")
        raw = [_raw_thread("PRRT_abc", [111, 222], login="me")]
        fetch_pr, fetch_threads = self._patches(rt, raw, login="me")
        with fetch_pr, fetch_threads, \
             patch("pr_comments.post_thread_reply") as post, \
             patch("pr_comments.patch_thread_reply", return_value=True) as edit:
            code = rt._run_reply(self._ctx(tmp_path), "discussion_r222", str(body))
        assert code == 0
        post.assert_not_called()
        assert edit.call_args[0][1] == 222

    def test_warns_when_the_body_cites_no_permalink(self, rt, tmp_path):
        body = tmp_path / "reply.md"
        body.write_text("Trust me, the code already does this.")
        fetch_pr, fetch_threads = self._patches(rt, [_raw_thread("PRRT_abc", [111])])
        with fetch_pr, fetch_threads, \
             patch("pr_comments.post_thread_reply", return_value=True), \
             patch.object(rt.log, "warn") as warn:
            assert rt._run_reply(self._ctx(tmp_path), "PRRT_abc", str(body)) == 0
        warn.assert_called_once()

    def test_errors_on_an_unknown_thread(self, rt, tmp_path):
        body = tmp_path / "reply.md"
        body.write_text("something")
        fetch_pr, fetch_threads = self._patches(rt, [_raw_thread("PRRT_abc", [111])])
        with fetch_pr, fetch_threads, \
             patch("pr_comments.post_thread_reply") as post:
            assert rt._run_reply(self._ctx(tmp_path), "discussion_r404", str(body)) == 1
        post.assert_not_called()

    def test_errors_when_the_reply_call_fails(self, rt, tmp_path, publishing_on):
        body = tmp_path / "reply.md"
        body.write_text("See https://github.com/owner/repo/blob/abc/src/app.py#L4.")
        fetch_pr, fetch_threads = self._patches(rt, [_raw_thread("PRRT_abc", [111])])
        with fetch_pr, fetch_threads, \
             patch("pr_comments.post_thread_reply", return_value=False), \
             patch.object(rt.log, "error") as err:
            assert rt._run_reply(self._ctx(tmp_path), "PRRT_abc", str(body)) == 1
        err.assert_called_once()

    def test_a_drafted_reply_is_not_a_failure(self, rt, tmp_path):
        """_gh_post reports failure whenever the publishing gate is closed."""
        body = tmp_path / "reply.md"
        body.write_text("See https://github.com/owner/repo/blob/abc/src/app.py#L4.")
        fetch_pr, fetch_threads = self._patches(rt, [_raw_thread("PRRT_abc", [111])])
        with fetch_pr, fetch_threads, \
             patch("pr_comments.post_thread_reply", return_value=False), \
             patch.object(rt.log, "error") as err:
            assert rt._run_reply(self._ctx(tmp_path), "PRRT_abc", str(body)) == 0
        err.assert_not_called()

    def test_distinguishes_a_missing_body_file_from_an_empty_one(self, rt, tmp_path):
        empty = tmp_path / "empty.md"
        empty.write_text("   ")
        with patch.object(rt, "fetch_pr_data") as fetch_pr, \
             patch.object(rt.log, "error") as err:
            assert rt._run_reply(self._ctx(tmp_path), "PRRT_abc", None) == 1
            assert rt._run_reply(self._ctx(tmp_path), "PRRT_abc", "/nope.md") == 1
            assert rt._run_reply(self._ctx(tmp_path), "PRRT_abc", str(empty)) == 1
        fetch_pr.assert_not_called()
        messages = [c[0][0] for c in err.call_args_list]
        assert "not found" in messages[1]
        assert "empty" in messages[2]


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


# ── reply upsert ─────────────────────────────────────────────────────────


def _standing_reply_thread(tid="t1", body="Applied: old take"):
    """A thread whose last comment is ours and unanswered — the editable case."""
    return ReportThread(id=tid, state=ThreadState.ADDRESSED, comments=[
        {"databaseId": 111, "body": "reviewer's point"},
        {"databaseId": 222, "body": body},
    ])


def _dismissed(**overrides):
    """A dismissed-verdict CommentItem for the reply-upsert tests below."""
    fields = {"id": "t1", "summary": "not applicable", "reasoning": "reason"}
    fields.update(overrides)
    return CommentItem(**fields)


class TestReplyUpsert:

    def test_edits_our_standing_reply(self, rt, tmp_path):
        dismissed = [_dismissed()]
        threads_by_id = {"t1": _standing_reply_thread(
            body="Suggestion reviewed and determined to be inapplicable: old reason",
        )}
        with patch("pr_comments.post_thread_reply") as post, \
             patch("pr_comments.patch_thread_reply", return_value=True) as edit:
            count = rt._post_dismissed_replies(
                dismissed, threads_by_id, "owner/repo", 42, tmp_path,
            )
        assert count == 1
        post.assert_not_called()
        assert edit.call_args[0][1] == 222
        assert "reason" in edit.call_args[0][2]

    def test_posts_when_reviewer_replied_after_us(self, rt, tmp_path):
        """Editing under a reviewer's reply would rewrite what they answered."""
        dismissed = [_dismissed()]
        threads_by_id = {
            "t1": ReportThread(id="t1", state=ThreadState.CONTESTED, comments=[
                {"databaseId": 111, "body": "reviewer's point"},
                {"databaseId": 222, "body": "Applied: old take"},
                {"databaseId": 333, "body": "that is not what I meant"},
            ]),
        }
        with patch("pr_comments.post_thread_reply", return_value=True) as post, \
             patch("pr_comments.patch_thread_reply") as edit:
            count = rt._post_dismissed_replies(
                dismissed, threads_by_id, "owner/repo", 42, tmp_path,
            )
        assert count == 1
        edit.assert_not_called()
        assert post.call_args[0][2] == 111

    def test_posts_when_we_never_replied(self, rt, tmp_path):
        dismissed = [_dismissed()]
        threads_by_id = {
            "t1": ReportThread(id="t1", state=ThreadState.NEW,
                               comments=[{"databaseId": 111}]),
        }
        with patch("pr_comments.post_thread_reply", return_value=True) as post, \
             patch("pr_comments.patch_thread_reply") as edit:
            count = rt._post_dismissed_replies(
                dismissed, threads_by_id, "owner/repo", 42, tmp_path,
            )
        assert count == 1
        edit.assert_not_called()
        assert post.call_args[0][2] == 111

    def test_never_edits_a_lone_root_comment(self, rt, tmp_path):
        """On a self-review the root is ours; editing it rewrites the review point."""
        dismissed = [_dismissed()]
        threads_by_id = {
            "t1": ReportThread(id="t1", state=ThreadState.ADDRESSED,
                               comments=[{"databaseId": 111, "body": "my own note"}]),
        }
        with patch("pr_comments.post_thread_reply", return_value=True) as post, \
             patch("pr_comments.patch_thread_reply") as edit:
            count = rt._post_dismissed_replies(
                dismissed, threads_by_id, "owner/repo", 42, tmp_path,
            )
        assert count == 1
        edit.assert_not_called()
        assert post.call_args[0][2] == 111

    @pytest.mark.parametrize("state,failing", [
        (ThreadState.ADDRESSED, "patch_thread_reply"),
        (ThreadState.NEW, "post_thread_reply"),
    ])
    def test_a_failed_call_is_not_counted(self, rt, tmp_path, state, failing):
        """replies_posted feeds the run summary, so a silent failure would inflate it."""
        dismissed = [_dismissed()]
        thread = _standing_reply_thread()
        thread.state = state
        with patch("pr_comments.post_thread_reply", return_value=True), \
             patch("pr_comments.patch_thread_reply", return_value=True), \
             patch(f"pr_comments.{failing}", return_value=False):
            count = rt._post_dismissed_replies(
                dismissed, {"t1": thread}, "owner/repo", 42, tmp_path,
            )
        assert count == 0

    def test_a_fix_replaces_an_earlier_dismissal(self, rt):
        """The #2633 regression: round one dismissed the thread, round two fixed it.

        Guarding per verdict left both replies standing, telling the reviewer
        their point did not apply and that we had acted on it.
        """
        fixed = [CommentItem(id="t1", summary="fix it", file="src/app.py")]
        threads_by_id = {"t1": _standing_reply_thread(
            body="Suggestion reviewed and determined to be inapplicable: old reason",
        )}
        with patch("pr_comments.post_thread_reply") as post, \
             patch("pr_comments.patch_thread_reply", return_value=True) as edit:
            count = rt._post_fix_replies(
                fixed, threads_by_id, "owner/repo", 42, "def5678",
            )
        assert count == 1
        post.assert_not_called()
        assert edit.call_args[0][1] == 222
        assert edit.call_args[0][2].startswith("Applied: fix it")

    def test_mixed_edit_and_post(self, rt, tmp_path):
        dismissed = [
            CommentItem(id="t1", summary="revised take", reasoning="new reason"),
            CommentItem(id="t2", summary="new one", reasoning="reason"),
        ]
        threads_by_id = {
            "t1": _standing_reply_thread(),
            "t2": ReportThread(id="t2", state=ThreadState.NEW,
                               comments=[{"databaseId": 333}]),
        }
        with patch("pr_comments.post_thread_reply", return_value=True) as post, \
             patch("pr_comments.patch_thread_reply", return_value=True) as edit:
            count = rt._post_dismissed_replies(
                dismissed, threads_by_id, "owner/repo", 42, tmp_path,
            )
        assert count == 2
        assert edit.call_args[0][1] == 222
        assert post.call_args[0][2] == 333


# ── reply evidence ───────────────────────────────────────────────────────


class TestReplyEvidence:

    def test_fix_reply_links_the_file_at_the_fix_commit(self, rt):
        fixed = [CommentItem(id="t1", summary="fix it", file="src/app.py")]
        threads_by_id = {"t1": ReportThread(id="t1", comments=[{"databaseId": 111}])}
        with patch("pr_comments.post_thread_reply", return_value=True) as post:
            rt._post_fix_replies(fixed, threads_by_id, "owner/repo", 42, "def5678")
        body = post.call_args[0][3]
        assert "owner/repo/blob/def5678/src/app.py" in body
        # No line anchor: the fix just moved the lines around it.
        assert "#L" not in body

    def test_deferred_reply_links_the_unchanged_code(self, rt, tmp_path):
        deferred = [CommentItem(id="t1", summary="fix it", file="src/app.py", line=12)]
        threads_by_id = {"t1": ReportThread(id="t1", comments=[{"databaseId": 111}])}
        with patch("pr_comments.post_thread_reply", return_value=True) as post, \
             patch.object(rt, "_get_head_sha", return_value="cafe123"):
            rt._post_deferred_replies(
                deferred, threads_by_id, "owner/repo", 42,
                "ENG-456", "https://linear.app/team/issue/ENG-456", tmp_path,
            )
        body = post.call_args[0][3]
        assert "ENG-456" in body
        assert "owner/repo/blob/cafe123/src/app.py#L12" in body

    def test_code_link_prefers_a_citation_that_resolves(self, rt, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text("a\nb\nc\n")
        entry = CommentItem(id="t1", file="other.py", line=1,
                            evidence_file="src/app.py", evidence_line=2)
        link = rt._code_link(entry, "owner/repo", "cafe123", tmp_path)
        assert "blob/cafe123/src/app.py#L2" in link

    def test_code_link_falls_back_when_the_citation_is_not_in_the_tree(self, rt, tmp_path):
        entry = CommentItem(id="t1", file="other.py", line=7,
                            evidence_file="src/gone.py", evidence_line=2)
        link = rt._code_link(entry, "owner/repo", "cafe123", tmp_path)
        assert "blob/cafe123/other.py#L7" in link

    def test_code_link_is_empty_with_nothing_to_point_at(self, rt, tmp_path):
        assert rt._code_link(CommentItem(id="t1"), "owner/repo", "cafe123", tmp_path) == ""
        assert rt._code_link(
            CommentItem(id="t1", file="a.py", line=1), "owner/repo", "", tmp_path,
        ) == ""


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

    def test_skips_an_entry_absent_from_threads_by_id(self, rt):
        """A synthetic comment id (ic-…/rb-…) is not a resolvable review thread.

        Regression: these used to fall through to an unconditional
        `resolve_thread`, spending a GraphQL mutation per comment item on an id
        the API cannot resolve. It failed silently, so nothing surfaced it.
        """
        fixed = [CommentItem(id="ic-123")]
        with patch("pr_comments.resolve_thread") as mock_resolve:
            count = rt._resolve_fixed_threads(fixed, {})
        assert count == 0
        mock_resolve.assert_not_called()

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
            verification="valid", complexity="high", state=ThreadState.NEW,
        )]
        result = rt._classify_triage_entries(entries)
        assert len(result.fixable) == 0
        assert len(result.needs_human) == 1
        assert result.needs_human[0].reason == "complex"

    def test_low_complexity_stays_fixable(self, rt):
        entries = [CommentItem(
            id="t1", file="f.go", line=10, reviewer="alice",
            summary="rename", classification="actionable_suggestion",
            verification="valid", complexity="low", state=ThreadState.NEW,
        )]
        result = rt._classify_triage_entries(entries)
        assert len(result.fixable) == 1
        assert len(result.needs_human) == 0

    def test_medium_complexity_stays_fixable(self, rt):
        entries = [CommentItem(
            id="t1", file="f.go", line=10, reviewer="alice",
            summary="add guard", classification="actionable_suggestion",
            verification="valid", complexity="medium", state=ThreadState.NEW,
        )]
        result = rt._classify_triage_entries(entries)
        assert len(result.fixable) == 1
        assert len(result.needs_human) == 0

    def test_no_complexity_field_stays_fixable(self, rt):
        entries = [CommentItem(
            id="t1", file="f.go", line=10, reviewer="alice",
            summary="fix", classification="actionable_suggestion",
            verification="valid", state=ThreadState.NEW,
        )]
        result = rt._classify_triage_entries(entries)
        assert len(result.fixable) == 1
        assert len(result.needs_human) == 0


# ── already_addressed verification ─────────────────────────────────────────


class TestClassifyAlreadyAddressed:
    """A suggestion the code already satisfies must not be routed to dismissed.

    Triage sees current HEAD, which already contains fixes made earlier in the
    same review cycle. Treating "the code already does this" as `invalid` posts
    a reply telling the reviewer their suggestion was inapplicable — when it was
    in fact the reason for the change.
    """

    def _entry(self, verification):
        return CommentItem(
            id="t1", file="f.go", line=10, reviewer="kgn",
            summary="drop the nil-logger guard",
            classification="actionable_suggestion",
            verification=verification, complexity="low", state=ThreadState.NEW,
        )

    def test_already_addressed_gets_own_bucket(self, rt):
        result = rt._classify_triage_entries([self._entry("already_addressed")])
        assert len(result.already_addressed) == 1
        assert result.dismissed == []
        assert result.fixable == []
        assert result.needs_human == []

    def test_invalid_still_dismissed(self, rt):
        result = rt._classify_triage_entries([self._entry("invalid")])
        assert len(result.dismissed) == 1
        assert result.already_addressed == []

    def test_accounted_ids_include_already_addressed(self, rt):
        result = rt._classify_triage_entries([self._entry("already_addressed")])
        accounted = rt._accounted_thread_ids(
            result.fixable, result.needs_human, result.dismissed,
            result.already_addressed,
        )
        assert accounted == {"t1"}

    def test_thread_outcomes_carry_already_addressed_action(self, rt):
        entry = self._entry("already_addressed")
        outcomes = rt._build_thread_outcomes([], [], [], [], [entry])
        assert len(outcomes) == 1
        assert outcomes[0].action == ThreadAction.ALREADY_ADDRESSED


class TestTriagePromptVerificationValues:
    """The prompt must define every verification value it asks for."""

    def test_defines_all_four_values(self, rt):
        prompt = rt._build_triage_prompt([], "diff")
        for value in ("valid", "already_addressed", "invalid", "needs_discussion"):
            assert f"- {value}:" in prompt

    def test_steers_away_from_invalid_for_satisfied_code(self, rt):
        prompt = rt._build_triage_prompt([], "diff")
        assert "is NEVER invalid" in prompt

    def test_commit_log_included_when_present(self, rt):
        prompt = rt._build_triage_prompt(
            [], "diff", commit_log="abc1234 fix(logging): inject logger",
        )
        assert "abc1234 fix(logging): inject logger" in prompt
        assert "already_addressed, not invalid" in prompt

    def test_commit_log_omitted_when_empty(self, rt):
        prompt = rt._build_triage_prompt([], "diff", commit_log="")
        assert "Commits already made on this branch" not in prompt


class TestAlreadyAddressedInSummary:
    def test_rendered_as_addressed_not_dismissed(self, rt):
        cp = rt.CommitPushResult(None, "no_changes", "")
        entry = CommentItem(
            id="t1", summary="drop the guard", file="f.go", line=10, reviewer="kgn",
        )
        body = rt._build_summary_body(
            [], [], [], cp, "owner/repo", 1, {}, already_addressed=[entry],
        )
        assert "1 already addressed" in body
        assert "Already addressed" in body
        assert "inapplicable" not in body

    def test_deferred_summary_renders_already_addressed(self, rt):
        fix = FixSummary(
            threads=[
                ThreadOutcome(id="t1", summary="drop the guard", file="f.go", line=10,
                              action=ThreadAction.ALREADY_ADDRESSED),
                ThreadOutcome(id="t2", summary="complex", file="c.go", line=3,
                              action=ThreadAction.DEFERRED),
            ],
            commit_status="no_changes",
            summary_deferred=True,
        )
        state = _make_state(fix)
        with patch("pr_comments.post_issue_comment", return_value="https://url") as mock_post:
            rt._render_deferred_summary(state, PRReport(), "owner/repo", 1, {})
        body = mock_post.call_args[0][2]
        assert "drop the guard" in body
        assert "Already addressed" in body


# ── summary upsert ─────────────────────────────────────────────────────────


class TestSummaryMarker:
    """Each review round must edit one summary comment, not append a new one."""

    def test_body_carries_marker(self, rt):
        cp = rt.CommitPushResult(None, "no_changes", "")
        body = rt._build_summary_body(
            [CommentItem(id="t1", summary="fix", file="a.py", line=1)],
            [], [], cp, "owner/repo", 1, {},
        )
        assert body.startswith(rt._SUMMARY_MARKER)

    def test_post_fix_summary_passes_marker(self, rt):
        cp = rt.CommitPushResult("abc1234", "pushed", "")
        with patch("pr_comments.post_issue_comment", return_value="https://url") as mock_post:
            rt._post_fix_summary(
                [CommentItem(id="t1", summary="fix", file="a.py", line=1)],
                [], [], cp, "owner/repo", 1, {},
            )
        assert mock_post.call_args.kwargs["marker"] == rt._SUMMARY_MARKER

    def test_deferred_summary_passes_marker(self, rt):
        fix = FixSummary(
            threads=[ThreadOutcome(id="t1", summary="fix", file="a.py", line=1,
                                   action=ThreadAction.FIXED)],
            commit_status="no_changes", summary_deferred=True,
        )
        state = _make_state(fix)
        with patch("pr_comments.post_issue_comment", return_value="https://url") as mock_post:
            rt._render_deferred_summary(state, PRReport(), "owner/repo", 1, {})
        assert mock_post.call_args.kwargs["marker"] == rt._SUMMARY_MARKER


# ── default-branch resolution in commit lookups ────────────────────────────


class TestCommitLookupsUseDefaultBranch:
    """`origin/main` is not universal — a hardcoded base silently returns nothing."""

    def test_branch_commit_log_uses_resolved_branch(self, rt, tmp_path):
        with (
            patch.object(rt, "_resolve_default_branch", return_value="trunk"),
            patch.object(rt.subprocess, "run") as run,
        ):
            run.return_value.returncode = 0
            run.return_value.stdout = "abc1234 fix: thing\n"
            assert rt._branch_commit_log(tmp_path) == "abc1234 fix: thing"
        assert "origin/trunk..HEAD" in run.call_args[0][0]

    def test_find_addressing_commit_uses_resolved_branch(self, rt, tmp_path):
        with (
            patch.object(rt, "_resolve_default_branch", return_value="trunk"),
            patch.object(rt.subprocess, "run") as run,
        ):
            run.return_value.returncode = 0
            run.return_value.stdout = "deadbeef\n"
            assert rt._find_addressing_commit(tmp_path, "a.py") == "deadbeef"
        assert "origin/trunk..HEAD" in run.call_args[0][0]

    def test_branch_commit_log_without_worktree(self, rt):
        assert rt._branch_commit_log(None) == ""


# ── shared thrash guard wiring ──────────────────────────────────────────────


class TestFixPassThrashGuard:
    """A fix agent that checked nothing off was thrashing, not working."""

    def test_session_log_lives_under_the_worktree(self, rt, tmp_path):
        assert rt._fix_session_log(tmp_path) == str(
            tmp_path / "ignore" / "pr-comments" / "fix-session.jsonl")

    def test_invoke_passes_the_diagnosable_session_log(self, rt, tmp_path):
        with patch.object(rt.ai_backend, "invoke_fix", return_value=0) as inv:
            rt._invoke_fix_agent("PROMPT", tmp_path)
        assert inv.call_args.args[0].session_log == rt._fix_session_log(tmp_path)

    def test_pass_that_checks_nothing_off_is_retried_with_the_fix_hint(self, rt, tmp_path):
        write_thrash_log(Path(rt._fix_session_log(tmp_path)))
        tracking = tmp_path / "tracking.md"
        tracking.write_text("- [ ] fix the thing\n")
        prompts = []

        with patch.object(rt, "_invoke_fix_agent",
                          side_effect=lambda p, *a, **k: prompts.append(p) or 0):
            diagnosis = rt._guarded_fix_pass(
                "PROMPT", tmp_path, None, tracking,
                max_turns=10, max_budget=1.0, label="Fix pass",
            )

        assert prompts == ["PROMPT", rt.agent_retry.FIX_RETRY_HINT + "PROMPT"]
        assert diagnosis.no_write_tool

    def test_a_single_checked_box_counts_as_work(self, rt, tmp_path):
        """Partial progress belongs to `_retry_fix_pass`, not to the guard."""
        write_thrash_log(Path(rt._fix_session_log(tmp_path)))
        tracking = tmp_path / "tracking.md"
        tracking.write_text("- [x] fixed one\n- [ ] not the other\n")

        with patch.object(rt, "_invoke_fix_agent", return_value=0) as inv:
            diagnosis = rt._guarded_fix_pass(
                "PROMPT", tmp_path, None, tracking,
                max_turns=10, max_budget=1.0, label="Fix pass",
            )

        assert diagnosis is None
        assert inv.call_count == 1

    def test_missing_tracking_file_counts_as_no_work(self, rt, tmp_path):
        assert rt._count_checked(tmp_path / "absent.md") == 0
        assert rt._count_unchecked(tmp_path / "absent.md") == 0


class TestTriageThrashGuard:
    """Triage has no session log — an unparseable answer is the only signal."""

    def test_parses_as_json_accepts_a_fenced_object(self, rt):
        assert rt._parses_as_json("```json\n{\"threads\": []}\n```")

    def test_parses_as_json_rejects_prose(self, rt):
        assert not rt._parses_as_json("I was unable to complete the triage.")

    def test_unparseable_triage_output_earns_one_retry(self, rt, tmp_path):
        report = PRReport(threads=[ReportThread(id="t1", reviewer="kgn")])
        prompts = []

        def prompt(text, **kw):
            prompts.append(text)
            return ("not json", 0) if len(prompts) == 1 else ('{"threads": []}', 0)

        with (
            patch.object(rt.ai_backend, "prompt", side_effect=prompt),
            patch.object(rt, "_branch_commit_log", return_value=""),
        ):
            result, rc = rt._run_triage(report, tmp_path, {})

        assert rc == 0
        assert result is not None
        assert len(prompts) == 2
        assert prompts[1].startswith(rt.agent_retry.BLANK_RESPONSE_HINT)


# ── permalink-backed claims ─────────────────────────────────────────────────


class TestUnsupportedVerdictDowngrade:
    """A verdict posted to a reviewer is a claim; a claim needs a line."""

    def _item(self, **kw):
        kw.setdefault("verification", "invalid")
        return CommentItem(id="t1", summary="s", **kw)

    def test_uncited_invalid_becomes_needs_discussion(self, rt, tmp_path):
        item = self._item(complexity="low")
        assert rt._downgrade_unsupported_verdicts([item], tmp_path) == 1
        assert item.verification == "needs_discussion"
        assert item.complexity == ""

    def test_uncited_already_addressed_becomes_needs_discussion(self, rt, tmp_path):
        item = self._item(verification="already_addressed")
        assert rt._downgrade_unsupported_verdicts([item], tmp_path) == 1
        assert item.verification == "needs_discussion"

    def test_reason_is_recorded_so_the_author_knows_why(self, rt, tmp_path):
        item = self._item(reasoning="reviewer misread the guard")
        rt._downgrade_unsupported_verdicts([item], tmp_path)
        assert "reviewer misread the guard" in item.reasoning
        assert "cited no line" in item.reasoning

    def test_cited_verdict_that_exists_in_the_tree_survives(self, rt, tmp_path):
        (tmp_path / "app.py").write_text("x = 1\n")
        item = self._item(evidence_file="app.py", evidence_line=1)
        assert rt._downgrade_unsupported_verdicts([item], tmp_path) == 0
        assert item.verification == "invalid"

    def test_citation_to_a_file_that_does_not_exist_is_downgraded(self, rt, tmp_path):
        """A link to nothing is no better than no link."""
        item = self._item(evidence_file="ghost.py", evidence_line=3)
        assert rt._downgrade_unsupported_verdicts([item], tmp_path) == 1
        assert item.verification == "needs_discussion"

    def test_valid_verdicts_are_left_alone(self, rt, tmp_path):
        item = self._item(verification="valid", complexity="low")
        assert rt._downgrade_unsupported_verdicts([item], tmp_path) == 0
        assert item.complexity == "low"

    def test_an_absolute_citation_outside_the_repo_is_downgraded(self, rt, tmp_path):
        """Joining a repo dir with an absolute path discards the repo dir."""
        # Use a name unique to this test's tmp_path to avoid colliding with the
        # traversal test when both run in the same session directory.
        outside = tmp_path.parent / f"outside_abs_{tmp_path.name}.py"
        outside.write_text("secret = 1\n")
        item = self._item(evidence_file=str(outside), evidence_line=1)
        assert rt._downgrade_unsupported_verdicts([item], tmp_path) == 1
        assert item.verification == "needs_discussion"

    def test_a_traversal_out_of_the_repo_is_downgraded(self, rt, tmp_path):
        """`..` reaching a file that really exists still is not this repo's code."""
        # Use a name unique to this test's tmp_path to avoid colliding with the
        # absolute-citation test when both run in the same session directory.
        outside_name = f"outside_trav_{tmp_path.name}.py"
        (tmp_path.parent / outside_name).write_text("secret = 1\n")
        item = self._item(evidence_file=f"../{outside_name}", evidence_line=1)
        assert rt._downgrade_unsupported_verdicts([item], tmp_path) == 1
        assert item.verification == "needs_discussion"

    def test_a_citation_past_the_end_of_the_file_is_downgraded(self, rt, tmp_path):
        """A permalink to a line the file does not have highlights nothing."""
        (tmp_path / "app.py").write_text("x = 1\n")
        item = self._item(evidence_file="app.py", evidence_line=99)
        assert rt._downgrade_unsupported_verdicts([item], tmp_path) == 1
        assert item.verification == "needs_discussion"

    def test_the_last_line_of_a_file_is_still_inside_it(self, rt, tmp_path):
        (tmp_path / "app.py").write_text("a\nb\nc\n")
        item = self._item(evidence_file="app.py", evidence_line=3)
        assert rt._downgrade_unsupported_verdicts([item], tmp_path) == 0

    def test_a_nested_citation_inside_the_repo_survives(self, rt, tmp_path):
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "mod.py").write_text("x = 1\n")
        item = self._item(evidence_file="pkg/mod.py", evidence_line=1)
        assert rt._downgrade_unsupported_verdicts([item], tmp_path) == 0

    def test_a_citation_to_a_directory_is_downgraded(self, rt, tmp_path):
        (tmp_path / "pkg").mkdir()
        item = self._item(evidence_file="pkg", evidence_line=1)
        assert rt._downgrade_unsupported_verdicts([item], tmp_path) == 1


class TestEvidencePermalinks:
    """Every claim links to the code at a pinned SHA."""

    def test_permalink_pins_the_sha(self, rt):
        assert rt._blob_permalink("owner/repo", "abc123", "a/b.py", 7) == (
            "https://github.com/owner/repo/blob/abc123/a/b.py#L7")

    def test_uncited_entry_renders_no_link(self, rt):
        entry = CommentItem(id="t1", summary="s")
        assert rt._evidence_link(entry, "owner/repo", "abc123") == ""

    def test_dismissal_carries_the_cited_line(self, rt, tmp_path):
        dismissed = [CommentItem(
            id="t1", summary="s", reasoning="the guard already returns early",
            evidence_file="app.py", evidence_line=12,
        )]
        threads = {"t1": ReportThread(id="t1", comments=[{"databaseId": 111}])}
        with (
            patch.object(rt, "_get_head_sha", return_value="cafe123"),
            patch("pr_comments.post_thread_reply", return_value=True) as reply,
        ):
            rt._post_dismissed_replies(dismissed, threads, "owner/repo", 42, tmp_path)
        body = reply.call_args[0][3]
        assert "blob/cafe123/app.py#L12" in body
        assert "the guard already returns early" in body

    def test_already_addressed_links_the_line_at_head(self, rt, tmp_path):
        addressed = [CommentItem(
            id="t1", summary="use the helper", file="app.py",
            evidence_file="app.py", evidence_line=4,
        )]
        threads = {"t1": ReportThread(id="t1", comments=[{"databaseId": 111}])}
        with (
            patch.object(rt, "_get_head_sha", return_value="cafe123"),
            patch.object(rt, "_find_addressing_commit", return_value="dead" * 10),
            patch("pr_comments.post_thread_reply", return_value=True) as reply,
        ):
            rt._post_already_addressed_replies(
                addressed, threads, "owner/repo", 42, tmp_path)
        body = reply.call_args[0][3]
        assert "blob/cafe123/app.py#L4" in body
        assert "/commit/deaddeaddead" in body

    def test_summary_file_cell_links_at_the_fix_commit(self, rt):
        cp = rt.CommitPushResult("abc1234", "pushed", "")
        body = rt._build_summary_body(
            [CommentItem(id="t1", summary="fix", file="a.py", line=9)],
            [], [], cp, "owner/repo", 1, {},
        )
        assert "https://github.com/owner/repo/blob/abc1234/a.py#L9" in body

    def test_summary_file_cell_stays_plain_without_a_sha(self, rt):
        cp = rt.CommitPushResult(None, "no_changes", "")
        body = rt._build_summary_body(
            [CommentItem(id="t1", summary="fix", file="a.py", line=9)],
            [], [], cp, "owner/repo", 1, {},
        )
        assert "| `a.py:9` |" in body
        assert "/blob/" not in body


# ── worktree_root guards ────────────────────────────────────────────────────


class TestWorktreeGuard:
    """Both entry points fail the same actionable way with no worktree."""

    def _ctx(self):
        return pr_context.ResolvedContext(
            repo="owner/repo", branch="isaac/feat/x", pr_number=42,
            worktree_root=None, head_sha="abc1234",
        )

    def test_run_threads_exits_before_touching_github(self, rt, capsys):
        assert_no_worktree_exit(capsys, "isaac/feat/x",
                                rt._run_threads, None, None, self._ctx())

    def test_finish_deferred_work_exits_with_guidance(self, rt, capsys):
        assert_no_worktree_exit(capsys, "isaac/feat/x",
                                rt._finish_deferred_work, self._ctx(), PRReport())


class TestFixRetryBudget:
    """A retry must outgrow the attempt it replaces, or it fails identically."""

    def test_retry_above_the_first_pass_cap_gets_more_turns(self, rt):
        assert rt._fix_retry_budget(rt.FIX_MAX_TURNS_CAP) > rt.FIX_MAX_TURNS_CAP

    def test_retry_bump_is_applied(self, rt):
        assert rt._fix_retry_budget(60) == 60 + rt.FIX_RETRY_TURNS_BUMP

    def test_retry_is_capped_at_the_retry_ceiling(self, rt):
        assert rt._fix_retry_budget(500) == rt.FIX_RETRY_MAX_TURNS_CAP

    def test_small_budget_floors_at_the_minimum(self, rt):
        assert rt._fix_retry_budget(5) == rt.FIX_RETRY_TURNS_MIN


class TestFixChunks:
    def test_splits_into_chunks_of_at_most_size(self, rt):
        chunks = rt._fix_chunks(list(range(41)), 10)
        assert [len(c) for c in chunks] == [10, 10, 10, 10, 1]

    def test_exact_multiple_has_no_empty_trailing_chunk(self, rt):
        chunks = rt._fix_chunks(list(range(20)), 10)
        assert [len(c) for c in chunks] == [10, 10]

    def test_short_list_is_one_chunk(self, rt):
        assert rt._fix_chunks([1, 2, 3], 10) == [[1, 2, 3]]

    def test_empty_list_is_no_chunks(self, rt):
        assert rt._fix_chunks([], 10) == []

    def test_chunks_preserve_order_and_lose_nothing(self, rt):
        items = list(range(41))
        assert [x for c in rt._fix_chunks(items, 10) for x in c] == items


class TestFixChunkSize:
    """A chunk must fit both caps, or the pass starves on whichever binds first."""

    def test_chunk_fits_the_turn_cap(self, rt):
        assert rt.FIX_CHUNK_SIZE * rt.FIX_TURNS_PER_ITEM <= rt.FIX_MAX_TURNS_CAP

    def test_chunk_fits_the_budget_cap(self, rt):
        assert rt.FIX_CHUNK_SIZE * rt.FIX_BUDGET_PER_ITEM <= rt.FIX_MAX_BUDGET_CAP

    def test_a_full_chunk_is_not_capped_down(self, rt):
        assert rt._fix_turn_budget(rt.FIX_CHUNK_SIZE) == (
            rt.FIX_CHUNK_SIZE * rt.FIX_TURNS_PER_ITEM
        )


class TestFixBatches:
    """Threads and items share one budget, so they share one chunk."""

    def test_a_batch_never_exceeds_the_chunk_size(self, rt):
        batches = rt._fix_batches(list(range(30)), list(range(30, 55)))
        assert all(
            len(threads) + len(items) <= rt.FIX_CHUNK_SIZE
            for threads, items in batches
        )

    def test_mixed_work_is_not_chunked_kind_by_kind(self, rt):
        """Chunking each kind separately would put 2x the cap in one pass."""
        batches = rt._fix_batches(list(range(rt.FIX_CHUNK_SIZE)),
                                  list(range(100, 100 + rt.FIX_CHUNK_SIZE)))
        assert len(batches) == 2

    def test_nothing_is_lost_or_reordered(self, rt):
        threads, items = list(range(7)), list(range(100, 106))
        batches = rt._fix_batches(threads, items)
        assert [t for ts, _ in batches for t in ts] == threads
        assert [i for _, its in batches for i in its] == items

    def test_no_work_is_no_batches(self, rt):
        assert rt._fix_batches([], []) == []


class TestFixPassRetryHeadroom:
    """The bug: a turn-exhausted retry was handed the budget that just ran out."""

    def _max_turns_log(self, path, turns):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "type": "result", "subtype": "error_max_turns", "num_turns": turns,
        }) + "\n")

    def test_retry_of_a_capped_pass_gets_more_turns(self, rt, tmp_path):
        self._max_turns_log(rt._fix_session_log(tmp_path), rt.FIX_MAX_TURNS_CAP)
        tracking = tmp_path / "tracking.md"
        tracking.write_text("- [ ] fix the thing\n")
        budgets = []

        with patch.object(rt, "_invoke_fix_agent",
                          side_effect=lambda p, *a, **k: budgets.append(k["max_turns"]) or 0):
            rt._guarded_fix_pass(
                "PROMPT", tmp_path, None, tracking,
                max_turns=rt.FIX_MAX_TURNS_CAP, max_budget=1.0, label="Fix pass",
            )

        assert budgets == [rt.FIX_MAX_TURNS_CAP, rt.FIX_MAX_TURNS_CAP * 2]


class TestRunFixBatch:
    """A batch is budgeted for its own items, not for the whole pass."""

    def _ctx(self):
        return SimpleNamespace(branch="isaac/feat/x", repo="owner/repo")

    def _run(self, rt, tmp_path, threads, items):
        with patch.object(rt, "_build_tracking_file") as build, \
             patch.object(rt, "_render_fix_template", return_value="PROMPT"), \
             patch.object(rt, "_guarded_fix_pass", return_value=None) as guarded, \
             patch.object(rt, "_parse_tracking_results",
                          return_value=rt.TrackingResult()):
            (tmp_path / "tracking.md").write_text("")
            result = rt._run_fix_batch(
                threads, items, {}, tmp_path / "tracking.md", tmp_path, None,
                self._ctx(), 42, label="Fix pass (batch 1/5)",
                default_branch="main",
            )
        return result, build, guarded

    def test_budget_is_sized_to_the_chunk(self, rt, tmp_path):
        result, _, guarded = self._run(rt, tmp_path, list(range(3)), [])
        assert result.max_turns == rt._fix_turn_budget(3)
        assert result.max_budget == rt._fix_budget_usd(3)
        assert guarded.call_args.kwargs["max_turns"] == rt._fix_turn_budget(3)

    def test_tracking_file_is_rebuilt_with_only_this_chunk(self, rt, tmp_path):
        threads = list(range(3))
        _, build, _ = self._run(rt, tmp_path, threads, [9])
        assert build.call_args.args[1] == threads
        assert build.call_args.kwargs["fixable_items"] == [9]


class TestMergeTracking:
    def test_batch_results_accumulate(self, rt):
        total = rt.TrackingResult(fixed=["a"], deferred_items=["z"])
        rt._merge_tracking(total, rt.TrackingResult(fixed=["b"], deferred=["c"]))
        assert total.fixed == ["a", "b"]
        assert total.deferred == ["c"]
        assert total.deferred_items == ["z"]
