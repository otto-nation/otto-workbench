"""Tests for review-threads: JSON extraction, thread classification, and prompt formatting."""

import contextlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from conftest import (
    assert_no_worktree_exit, make_ctx,
    supersession_context, supersession_evidence, supersession_verdict,
    write_thrash_log,
)
import pr_state
from pr_comments import ThreadState
from pr_state import (
    CommitStatus, FixSummary, PRIdentity, PRState, SupersessionKind,
    ThreadAction, ThreadOutcome,
)
from pr_thread_models import (
    CommentItem, PRReport, ReportThread, TrackingResult, TriageResult,
    TriageStats, triage_result_from_dict,
)
from review_common import SECTION_PRIOR_FINDINGS, Diagnosis, DiagnosisKind
from review_issue import CreatedIssue, IssueDelivery, IssueResult
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


def _lookup_returns(comment):
    """Patch the marker lookup to report `comment`.

    Both the autouse default and the per-test override go through this one
    patch, so a test entering `_published(...)` inside the fixture's patch is
    plain `patch.object` nesting: the inner patch wins for its block and
    restores the fixture's on exit.
    """
    import pr_comments
    return patch.object(pr_comments, "find_marker_comment", return_value=comment)


@pytest.fixture(autouse=True)
def _no_published_summary():
    """Start every test from a PR with no summary comment yet.

    Every summary upsert reads the published comment first, so without this the
    suite would shell out to `gh api`. Tests covering the carry-forward stub it
    with a body of their own.
    """
    import pr_comments
    with _lookup_returns(pr_comments.MarkerComment(found=True)):
        yield


def _published(body: str):
    """Stub a prior summary comment with the given body."""
    import pr_comments
    return _lookup_returns(pr_comments.MarkerComment(True, 11, body))


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

# Stand-in SHAs for the attribution tests. Per-round attribution is only
# testable when each round has a distinguishable one, and an assertion reads as
# a claim about the round rather than about seven digits.
_ROUND_1_SHA = "1111111"
_ROUND_2_SHA = "2222222"
_PASS_SHA = "9999999"


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


class TestBuildPriorSectionLedger:
    def test_asks_for_the_ledger_alongside_the_context(self):
        result = _build_prior_section(
            "## Must fix\n- **[M1]** `a.py:10` — Issue",
            "This is a re-review.",
        )
        assert "This is a re-review." in result
        assert f"## {SECTION_PRIOR_FINDINGS}" in result

    def test_ledger_asked_for_without_a_context(self):
        result = _build_prior_section("## Must fix\n- **[M1]** `a.py:10` — Issue")
        assert f"## {SECTION_PRIOR_FINDINGS}" in result

    def test_prior_ledger_not_shown_back_to_the_agent(self):
        # Reconciliation strips it before publishing, but a review from an
        # older generator can still carry one — it dispositions findings from
        # the review before last, which is noise here.
        result = _build_prior_section(
            "## Must fix\n- **[M1]** `a.py:10` — Issue\n"
            f"## {SECTION_PRIOR_FINDINGS}\n- **[M9]** `gone.py` — Fixed\n"
        )
        assert "gone.py" not in result


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

    def test_untracked_only_changes_still_commit(self, rt, publishing_on):
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

    def test_push_failed(self, rt, publishing_on):
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

    def test_success(self, rt, publishing_on):
        """git push returns 0 → pushed with SHA."""
        def mock_run(cmd, **kwargs):
            if "rev-parse" in cmd:
                return _make_completed(0, stdout="abc1234\n")
            return _make_completed(0)

        result = self._commit(rt, mock_run)
        assert result.status == "pushed"
        assert result.sha == "abc1234"
        assert result.error == ""

    def test_draft_commits_but_holds_the_push(self, rt):
        """The commit is local and undoable; the push is the outward act."""
        calls = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            if "rev-parse" in cmd:
                return _make_completed(0, stdout="abc1234\n")
            return _make_completed(0)

        result = self._commit(rt, mock_run)
        assert result.status == "push_held"
        assert result.sha == "abc1234"
        assert not any("push" in cmd for cmd in calls)
        assert any("commit" in cmd for cmd in calls)


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
        """head_after == head_before, clean tree → no_changes, no push."""
        with patch.object(rt, "_get_head_sha", return_value="abc1234"), \
             patch.object(rt.review_common, "has_uncommitted_changes",
                          return_value=False):
            result = rt._recover_agent_commit(Path("/fake"), "abc1234")
        assert result.status == "no_changes"
        assert result.sha is None

    def test_a_known_failure_is_never_downgraded(self, rt):
        """Recovery adds information, it does not overwrite it."""
        prior = rt.CommitPushResult(None, "commit_failed", "hook rejected")
        with patch.object(rt, "_get_head_sha", return_value="abc1234"):
            result = rt._recover_agent_commit(Path("/fake"), "abc1234", prior=prior)
        assert result.status == "commit_failed"
        assert result.error == "hook rejected"

    def test_a_dirty_tree_is_a_refused_commit_not_an_empty_one(self, rt):
        """Nothing committed with changes still there means something said no."""
        with patch.object(rt, "_get_head_sha", return_value="abc1234"), \
             patch.object(rt.review_common, "has_uncommitted_changes",
                          return_value=True):
            result = rt._recover_agent_commit(Path("/fake"), "abc1234")
        assert result.status == "commit_failed"
        assert result.sha is None

    def test_already_pushed_skips_push(self, rt):
        """head changed and SHA already on remote → pushed without a new push."""
        with patch.object(rt, "_get_head_sha", return_value="def5678"), \
             patch.object(rt, "_is_pushed", return_value=True):
            result = rt._recover_agent_commit(Path("/fake"), "abc1234")
        assert result.status == "pushed"
        assert result.sha == "def5678"

    def test_push_success(self, rt, publishing_on):
        """head changed, not yet on remote, push succeeds → pushed."""
        with patch.object(rt, "_get_head_sha", return_value="def5678"), \
             patch.object(rt, "_is_pushed", return_value=False), \
             patch.object(rt.subprocess, "run", return_value=_make_completed(0)):
            result = rt._recover_agent_commit(Path("/fake"), "abc1234")
        assert result.status == "pushed"
        assert result.sha == "def5678"

    def test_push_failure(self, rt, publishing_on):
        """head changed, not yet on remote, push fails → push_failed with error."""
        with patch.object(rt, "_get_head_sha", return_value="def5678"), \
             patch.object(rt, "_is_pushed", return_value=False), \
             patch.object(rt.subprocess, "run", return_value=_make_completed(1, stderr="rejected\n")):
            result = rt._recover_agent_commit(Path("/fake"), "abc1234")
        assert result.status == "push_failed"
        assert result.sha == "def5678"
        assert "rejected" in result.error

    def test_draft_holds_the_agent_commit_too(self, rt):
        """The agent committing directly is not a way around the gate."""
        def boom(*a, **kw):
            raise AssertionError(f"a subprocess ran while the gate was shut: {a}")

        with patch.object(rt, "_get_head_sha", return_value="def5678"), \
             patch.object(rt, "_is_pushed", return_value=False), \
             patch.object(rt.subprocess, "run", boom):
            result = rt._recover_agent_commit(Path("/fake"), "abc1234")
        assert result.status == "push_held"
        assert result.sha == "def5678"


# ── _fixed_status_text ──────────────────────────────────────────────────────


class TestFixedStatusText:
    """Test status text rendering for each CommitPushResult state."""

    def test_pushed(self, rt):
        cp = rt.CommitPushResult("abc1234", "pushed", "")
        text = rt._fixed_status_text(cp, "owner/repo")
        assert "Fixed in" in text
        assert "abc1234" in text
        assert "push failed" not in text

    def test_push_failed_says_the_commit_exists(self, rt):
        """"Fix pending" would deny a commit that is sitting in the worktree."""
        cp = rt.CommitPushResult("abc1234", "push_failed", "rejected")
        text = rt._fixed_status_text(cp, "owner/repo")
        assert "committed locally" in text
        assert "push failed" in text
        assert "abc1234" not in text

    def test_push_held_says_why_it_is_waiting(self, rt):
        cp = rt.CommitPushResult("abc1234", "push_held", "")
        text = rt._fixed_status_text(cp, "owner/repo")
        assert "committed locally" in text
        assert "push held" in text
        assert "abc1234" not in text

    def test_no_changes_claims_nothing_about_why(self, rt):
        """"Fixed" and "nothing committed" cannot both be true."""
        cp = rt.CommitPushResult(None, "no_changes", "")
        text = rt._fixed_status_text(cp, "owner/repo")
        assert text == rt._UNATTRIBUTED_STATUS_TEXT
        assert "no commit needed" not in text

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

    def test_no_changes_shows_an_unattributed_fix(self, rt):
        cp = rt.CommitPushResult(None, "no_changes", "")
        body = rt._build_summary_body(
            [self._fixed_entry()], [], [], cp, "owner/repo", 1, {},
        )
        assert rt._UNATTRIBUTED_STATUS_TEXT in body
        assert "no commit needed" not in body

    def test_commit_failed_shows_precommit_hint(self, rt):
        cp = rt.CommitPushResult(None, "commit_failed", "hook error")
        body = rt._build_summary_body(
            [self._fixed_entry()], [], [], cp, "owner/repo", 1, {},
        )
        assert "commit failed" in body

    def test_push_failed_names_the_local_commit(self, rt):
        """The row says the work is committed but unpublished, and links nothing.

        A SHA the remote does not have would 404 for whoever clicks it, so the
        cell states the situation rather than citing it.
        """
        cp = rt.CommitPushResult("abc1234", "push_failed", "rejected")
        body = rt._build_summary_body(
            [self._fixed_entry()], [], [], cp, "owner/repo", 1, {},
        )
        assert "committed locally (push failed)" in body
        assert "/commit/abc1234" not in body

    def test_needs_human_rows(self, rt):
        cp = rt.CommitPushResult(None, "no_changes", "")
        body = rt._build_summary_body(
            [], [CommentItem(summary="question", file="a.py", line=1, reason="contested")],
            [], cp, "owner/repo", 1, {},
        )
        assert rt.HumanReason.CONTESTED.prose in body

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

    def test_reports_needs_human_as_open(self, rt):
        """The one condition that routes here is a needs_human thread."""
        fix = FixSummary(
            threads=[
                ThreadOutcome(id="t1", summary="auto fix", file="a.py", line=1, action=ThreadAction.FIXED),
                ThreadOutcome(id="t2", summary="premise disputed", file="b.py", line=2,
                              action=ThreadAction.NEEDS_HUMAN, reason="contested"),
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
        assert "premise disputed" in body
        assert "1 need discussion" in body

    def test_needs_human_settled_by_hand_renders_as_fixed(self, rt, worktree):
        """--finish reconciles first, so the row credits the hand fix."""
        pr_state.save_state(worktree / "target", PRState(
            identity=PRIdentity(repo="owner/repo", branch="b", pr_number=42,
                                head_sha="aaaaaaa", worktree_root=str(worktree)),
            fix=FixSummary(head_sha="aaaaaaa", summary_deferred=True,
                           commit_status="no_changes", threads=[
                               ThreadOutcome(id="t1", summary="premise disputed",
                                             file="b.py", line=2,
                                             action=ThreadAction.NEEDS_HUMAN,
                                             reason="contested"),
                           ]),
        ))
        ctx = make_ctx(branch="b", worktree_root=worktree, head_sha="aaaaaaa",
                       target_dir=worktree / "target")
        report = PRReport(threads=[ReportThread(
            id="t1", state=ThreadState.RESOLVED, is_resolved=True,
            comments=[{"body": "x"}],
        )])
        with patch.object(rt, "_get_head_sha", return_value="aaaaaaa"), \
                patch("pr_comments.post_issue_comment", return_value="https://url") as mock_post:
            rt._finish_deferred_work(ctx, report, track=rt.TRACK_ALL)
        body = mock_post.call_args[0][2]
        assert "premise disputed" in body
        assert "Addressed outside the fix pass" in body
        assert "need discussion" not in body

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

    def test_held_commit_keeps_the_summary_deferred(self, rt, publishing_on):
        """The commit link would 404 — same hazard as a failed push."""
        fix = FixSummary(
            threads=[
                ThreadOutcome(id="t1", summary="fix it", file="x.py", line=1, action=ThreadAction.FIXED),
            ],
            commit_sha="def5678",
            commit_status="push_held",
            summary_deferred=True,
        )
        state = _make_state(fix)
        with patch("pr_comments.post_issue_comment") as mock_post:
            with patch.object(rt, "_is_pushed", return_value=False):
                rt._render_deferred_summary(state, PRReport(), "owner/repo", 1, {})
        mock_post.assert_not_called()
        assert fix.summary_deferred is True

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


class TestSummaryUsesPerThreadCommit:
    """A thread's row names the commit that fixed it, not the last pass's."""

    def _post(self, rt, *threads, commit_sha="", commit_status="no_changes"):
        fix = FixSummary(
            commit_sha=commit_sha, commit_status=commit_status,
            summary_deferred=True, threads=list(threads),
        )
        with patch("pr_comments.post_issue_comment", return_value="u") as post:
            rt._render_deferred_summary(_make_state(fix), PRReport(), "owner/repo", 1, {})
        return post.call_args[0][2]

    def test_row_links_the_thread_own_commit(self, rt):
        body = self._post(rt, ThreadOutcome(
            id="t1", summary="fix regex", file="p.py", line=10,
            action=ThreadAction.FIXED, commit_sha="deadbee",
        ))
        assert "deadbee" in body
        assert "no commit needed" not in body

    def test_row_without_a_sha_claims_no_commit(self, rt):
        body = self._post(rt, ThreadOutcome(
            id="t1", summary="fix regex", file="p.py", line=10,
            action=ThreadAction.FIXED,
        ))
        assert rt._UNATTRIBUTED_STATUS_TEXT in body

    def test_each_round_keeps_its_own_attribution(self, rt):
        """The failure: one pass's envelope SHA relabelled every round."""
        body = self._post(
            rt,
            ThreadOutcome(id="t1", summary="round one", file="a.py", line=1,
                          action=ThreadAction.FIXED, commit_sha="1111111"),
            ThreadOutcome(id="t2", summary="round two", file="b.py", line=2,
                          action=ThreadAction.FIXED, commit_sha="2222222"),
        )
        assert "1111111" in body
        assert "2222222" in body

    def test_a_reconciled_thread_claims_no_commit(self, rt):
        """It was fixed by hand — crediting the pass's commit would be a lie.

        The file cell still permalinks at the pass's SHA; that is a location
        anchor, not a claim about who fixed it. The status cell is the claim.
        """
        body = self._post(
            rt,
            ThreadOutcome(id="t1", summary="fixed by hand", file="a.py", line=1,
                          action=ThreadAction.FIXED,
                          reason=rt._RECONCILED_REASON),
            commit_sha="def5678", commit_status="pushed",
        )
        assert "Fixed in" not in body
        assert "Addressed outside the fix pass" in body

    def test_pass_sha_still_covers_a_thread_with_none(self, rt):
        body = self._post(
            rt,
            ThreadOutcome(id="t1", summary="fix it", file="a.py", line=1,
                          action=ThreadAction.FIXED),
            commit_sha="def5678", commit_status="pushed",
        )
        assert "def5678" in body


class TestFailedCommitIsNotReportedAsNoCommit:
    """A hook-rejected commit published as "no commit needed".

    Two independent defects, one visible claim: recovery overwrote the known
    failure on its way out of the fix pass, and the renderer then read the
    status cell straight off a snapshot it never checked against the worktree.
    """

    @staticmethod
    def _item(tid, verification="valid"):
        return CommentItem(
            id=tid, file="f.go", line=10, reviewer="kgn", summary=f"{tid} summary",
            classification="actionable_suggestion", verification=verification,
            complexity="low", state=ThreadState.NEW,
        )

    def _fix_pass(self, rt, tmp_path):
        """Drive a fix pass whose commit is rejected and whose HEAD never moves."""
        threads = [self._item("t1"), self._item("t2")]
        report = PRReport(
            repo="owner/repo", pr_number=1,
            threads=[
                ReportThread(id=t.id, file=t.file, line=t.line,
                             comments=[{"databaseId": 100 + n}])
                for n, t in enumerate(threads)
            ],
        )
        ctx = SimpleNamespace(
            repo="owner/repo", branch="b", pr_number=1, head_sha="aaa1111",
            target_dir=tmp_path,
        )

        pushes = []
        commits = []

        def mock_run(cmd, **kwargs):
            if "push" in cmd:
                pushes.append(cmd)
            if "commit" in cmd:
                commits.append(cmd)
                return _make_completed(1, stderr="pre-commit hook failed\n")
            return _make_completed(0, stdout="aaa1111\n")

        batch = rt.FixBatchResult(
            tracking=TrackingResult(fixed=threads),
            unproductive=False, max_turns=10, max_budget=1.0,
        )
        with patch.object(rt, "_run_fix_batch", return_value=batch), \
             patch.object(rt, "_find_and_update_main_worktree", return_value=None), \
             patch.object(rt, "_resolve_default_branch", return_value="main"), \
             patch.object(rt, "_persist_fix_state") as persist, \
             patch.object(rt.review_common, "has_uncommitted_changes", return_value=True), \
             patch.object(rt.subprocess, "run", side_effect=mock_run), \
             patch("pr_comments.post_thread_reply", return_value=True), \
             patch("pr_comments.post_issue_comment", return_value="u"), \
             patch("pr_comments.resolve_thread", return_value=True):
            result = rt._run_comment_fix(
                TriageResult(threads=threads), report, tmp_path, ctx,
            )
        return SimpleNamespace(
            result=result, persisted=persist.call_args[0][0],
            pushes=pushes, commits=commits,
        )

    def test_the_failure_survives_recovery(self, rt, tmp_path, publishing_on):
        """The persisted status is what --finish reads on the next run."""
        run = self._fix_pass(rt, tmp_path)
        assert run.result.commit_status == "commit_failed"
        assert run.persisted.commit_status == "commit_failed"
        assert run.persisted.commit_sha == ""

    def test_a_rejected_commit_pushes_nothing(self, rt, tmp_path, publishing_on):
        """There is no commit to publish, so no push may be attempted.

        The status cell is only half the claim: pushing a branch whose commit
        the hook rejected would put the *previous* head in front of a reviewer
        as though it carried this round's fixes.
        """
        run = self._fix_pass(rt, tmp_path)
        assert run.commits
        assert run.pushes == []

    def test_a_hand_commit_gets_the_credit(self, rt):
        """HEAD moved past the snapshot: attribute the fixes to what landed."""
        fix = FixSummary(
            threads=[ThreadOutcome(id="t1", summary="t1 summary", file="f.go",
                                   line=10, action=ThreadAction.FIXED)],
            commit_status="commit_failed", head_sha="aaa1111",
            summary_deferred=True,
        )
        with patch.object(rt, "_get_head_sha", return_value="bbb2222"), \
             patch.object(rt, "_is_pushed", return_value=True), \
             patch("pr_comments.post_issue_comment", return_value="u") as post:
            rt._render_deferred_summary(_make_state(fix), PRReport(), "owner/repo", 1, {})
        body = post.call_args[0][2]
        assert "bbb2222" in body
        assert "no commit needed" not in body
        assert rt._UNATTRIBUTED_STATUS_TEXT not in body

    def test_an_unpushed_hand_commit_claims_nothing(self, rt):
        """A SHA a reviewer cannot open is not worth naming."""
        fix = FixSummary(
            threads=[ThreadOutcome(id="t1", summary="t1 summary", file="f.go",
                                   line=10, action=ThreadAction.FIXED)],
            commit_status="commit_failed", head_sha="aaa1111",
            summary_deferred=True,
        )
        with patch.object(rt, "_get_head_sha", return_value="bbb2222"), \
             patch.object(rt, "_is_pushed", return_value=False), \
             patch("pr_comments.post_issue_comment", return_value="u") as post:
            rt._render_deferred_summary(_make_state(fix), PRReport(), "owner/repo", 1, {})
        body = post.call_args[0][2]
        assert rt._RECONCILED_STATUS_TEXT in body
        assert "bbb2222" not in body

    def test_a_still_unmoved_head_keeps_the_failure(self, rt):
        """Nothing was committed by anyone — the cell must not invent a commit."""
        fix = FixSummary(
            threads=[ThreadOutcome(id="t1", summary="t1 summary", file="f.go",
                                   line=10, action=ThreadAction.FIXED)],
            commit_status="commit_failed", head_sha="aaa1111",
            summary_deferred=True,
        )
        with patch.object(rt, "_get_head_sha", return_value="aaa1111"), \
             patch("pr_comments.post_issue_comment", return_value="u") as post:
            rt._render_deferred_summary(_make_state(fix), PRReport(), "owner/repo", 1, {})
        body = post.call_args[0][2]
        assert "commit failed" in body

    def test_the_contradiction_is_reported(self, rt, capsys):
        """N fixes and no commit is caught, not rendered quietly."""
        cp = rt.CommitPushResult(None, "commit_failed", "hook")
        rt._warn_unattributed_fixes(
            [CommentItem(id="t1", summary="fix it", file="a.py", line=1)], cp,
        )
        assert "no commit to attribute" in capsys.readouterr().err


class TestSummaryStillOwed:
    """Whether --finish has to re-render the fix summary."""

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

    def test_held_commit_defers(self, rt, publishing_on):
        """A held push leaves the same gap as a failed one: no remote commit."""
        assert self._owed(rt, fixed=["t1"], commit_status="push_held") is True

    def test_draft_leaves_the_summary_owed(self, rt):
        assert self._owed(rt, fixed=["t1"]) is True

    def test_draft_with_nothing_to_say_owes_nothing(self, rt):
        assert self._owed(rt) is False


class TestPushHeldCommit:
    """--finish --post is the human saying the held work may land."""

    @staticmethod
    def _state(status="push_held", sha="abc1234"):
        return _make_state(FixSummary(commit_sha=sha, commit_status=status))

    def test_pushes_and_marks_it_pushed(self, rt, publishing_on):
        state = self._state()
        with patch.object(rt, "_is_pushed", return_value=False), \
             patch.object(rt.subprocess, "run", return_value=_make_completed(0)) as run:
            rt._push_held_commit(state, Path("/fake"))
        assert state.fix.commit_status == "pushed"
        assert "push" in run.call_args[0][0]

    def test_a_draft_finish_still_holds_it(self, rt):
        """--finish without --post is not the human saying go."""
        def boom(*a, **kw):
            raise AssertionError(f"a subprocess ran while the gate was shut: {a}")

        state = self._state()
        with patch.object(rt, "_is_pushed", return_value=False), \
             patch.object(rt.subprocess, "run", boom):
            rt._push_held_commit(state, Path("/fake"))
        assert state.fix.commit_status == "push_held"

    def test_a_hold_placed_this_run_outranks_post(self, rt, publishing_on):
        """--fix --finish --post in one run: the discussion is still open."""
        import publishing
        publishing.hold("discussion open")

        def boom(*a, **kw):
            raise AssertionError(f"a subprocess ran while the gate was shut: {a}")

        state = self._state()
        with patch.object(rt, "_is_pushed", return_value=False), \
             patch.object(rt.subprocess, "run", boom):
            rt._push_held_commit(state, Path("/fake"))
        assert state.fix.commit_status == "push_held"

    def test_a_failed_push_is_recorded_as_such(self, rt, publishing_on):
        state = self._state()
        with patch.object(rt, "_is_pushed", return_value=False), \
             patch.object(rt.subprocess, "run", return_value=_make_completed(1, stderr="rejected\n")):
            rt._push_held_commit(state, Path("/fake"))
        assert state.fix.commit_status == "push_failed"

    def test_a_failed_push_reaches_the_trail(self, rt, publishing_on):
        """Same as the two sibling push paths — a failure here is not silent."""
        trail = MagicMock()
        state = self._state()
        with patch.object(rt, "_is_pushed", return_value=False), \
             patch.object(rt.subprocess, "run", return_value=_make_completed(1, stderr="rejected\n")):
            rt._push_held_commit(state, Path("/fake"), trail)
        trail.error.assert_called_once()
        assert "rejected" in trail.error.call_args.kwargs["data"]["error"]

    def test_a_commit_already_on_the_remote_is_just_marked(self, rt, publishing_on):
        """Someone pushed by hand between the two runs."""
        def boom(*a, **kw):
            raise AssertionError(f"pushed a commit the remote already had: {a}")

        state = self._state()
        with patch.object(rt, "_is_pushed", return_value=True), \
             patch.object(rt.subprocess, "run", boom):
            rt._push_held_commit(state, Path("/fake"))
        assert state.fix.commit_status == "pushed"

    def test_noop_when_the_commit_already_went_out(self, rt, publishing_on):
        def boom(*a, **kw):
            raise AssertionError(f"pushed an already-pushed commit: {a}")

        state = self._state(status="pushed")
        with patch.object(rt.subprocess, "run", boom):
            rt._push_held_commit(state, Path("/fake"))
        assert state.fix.commit_status == "pushed"

    def test_noop_when_the_pass_made_no_commit(self, rt, publishing_on):
        def boom(*a, **kw):
            raise AssertionError(f"pushed with no commit to push: {a}")

        state = self._state(status="no_changes", sha="")
        with patch.object(rt.subprocess, "run", boom):
            rt._push_held_commit(state, Path("/fake"))
        assert state.fix.commit_status == "no_changes"


class TestPendingFixReplies:
    """--finish is the second chance for fix replies the fix pass didn't send."""

    # id, summary, file, line, root comment databaseId
    _SEEDS = [
        ("t1", "fix it", "x.py", 1, 100),
        ("t2", "and this", "y.py", 2, 200),
    ]

    def _queue(self, count=1, **fix_kw):
        """A queue of `count` fixed threads: the FixSummary and its threads_by_id.

        Which thread is which never matters here — every test in this class turns
        on the queue's state (pushed, drafted, already drained), not its contents.
        So the seeds stay fixed and each test names only the fields it turns on.
        """
        seeds = self._SEEDS[:count]
        fix_kw.setdefault("commit_sha", "abc1234")
        fix = FixSummary(
            threads=[
                ThreadOutcome(id=tid, summary=summary, file=path, line=line,
                              action=ThreadAction.FIXED)
                for tid, summary, path, line, _ in seeds
            ],
            **fix_kw,
        )
        threads_by_id = {
            tid: ReportThread(id=tid, is_resolved=False, comments=[{"databaseId": db}])
            for tid, _, _, _, db in seeds
        }
        return fix, threads_by_id

    def test_posts_fix_replies_and_resolves_when_push_confirmed(self, rt, publishing_on):
        fix, threads_by_id = self._queue(2, commit_status="push_failed", summary_deferred=True)
        state = _make_state(fix)
        with patch.object(rt, "_is_pushed", return_value=True), \
             patch("pr_comments.post_thread_reply", return_value=True) as mock_reply, \
             patch("pr_comments.resolve_thread", return_value=True) as mock_resolve:
            rt._post_pending_fix_replies(state, "owner/repo", 1, threads_by_id)
        assert mock_reply.call_count == 2
        assert mock_resolve.call_count == 2
        assert fix.commit_status == "pushed"

    def test_skips_when_still_unpushed(self, rt):
        fix, _ = self._queue(commit_status="push_failed", summary_deferred=True)
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
        fix, threads_by_id = self._queue(commit_status="push_failed", summary_deferred=True)
        state = _make_state(fix)
        with patch.object(rt, "_is_pushed", return_value=True):
            rt._post_pending_fix_replies(state, "owner/repo", 1, threads_by_id)
        assert fix.commit_status == "push_failed"

    def test_drains_the_queue_a_drafted_fix_pass_left_behind(self, rt, publishing_on):
        """A drafted --fix commits and sends nothing; --post must catch up.

        The `pushed` status here is a run whose push landed before the gate
        applied to it — the queue survives on `replies_pending` alone.
        """
        fix, threads_by_id = self._queue(commit_status="pushed", replies_pending=True)
        state = _make_state(fix)
        with patch.object(rt, "_is_pushed", return_value=True), \
             patch("pr_comments.post_thread_reply", return_value=True) as mock_reply, \
             patch("pr_comments.resolve_thread", return_value=True):
            rt._post_pending_fix_replies(state, "owner/repo", 1, threads_by_id)
        assert mock_reply.call_count == 1
        assert fix.replies_pending is False

    def test_counts_the_replies_it_drained(self, rt, publishing_on):
        """The drafted pass recorded 0 sent; the run that sends them owns the count."""
        fix, threads_by_id = self._queue(
            2, commit_status="pushed", replies_pending=True, replies_posted=0,
        )
        state = _make_state(fix)
        with patch.object(rt, "_is_pushed", return_value=True), \
             patch("pr_comments.post_thread_reply", return_value=True), \
             patch("pr_comments.resolve_thread", return_value=True):
            rt._post_pending_fix_replies(state, "owner/repo", 1, threads_by_id)
        assert fix.replies_posted == 2

    def test_draft_drain_counts_nothing(self, rt):
        """A draft sends nothing, so the counter must not move on its way past."""
        fix, threads_by_id = self._queue(
            commit_status="pushed", replies_pending=True, replies_posted=0,
        )
        state = _make_state(fix)
        with patch.object(rt, "_is_pushed", return_value=True):
            rt._post_pending_fix_replies(state, "owner/repo", 1, threads_by_id)
        assert fix.replies_posted == 0

    def test_noop_once_the_replies_have_gone_out(self, rt, publishing_on):
        fix, _ = self._queue(commit_status="pushed", replies_pending=False)
        state = _make_state(fix)
        with patch("pr_comments.post_thread_reply") as mock_reply:
            rt._post_pending_fix_replies(state, "owner/repo", 1, {})
        mock_reply.assert_not_called()

    def test_attributes_the_commit_the_operator_landed_by_hand(self, rt, publishing_on):
        """A hook-rejected commit records no SHA; the summary reconciled, the reply did not.

        The pass edits the files, a pre-commit hook rejects the commit, and the
        operator commits and pushes the same work themselves. The summary rows
        pick that commit up off the moved HEAD, so the reply has to as well —
        otherwise the reviewer gets "Fixed in ``" over an empty commit link.
        """
        fix, threads_by_id = self._queue(
            commit_status="commit_failed", commit_sha="", replies_pending=True,
            head_sha="abc1234",
        )
        state = _make_state(fix)
        with patch.object(rt, "_get_head_sha", return_value="def5678"), \
             patch.object(rt, "_is_pushed", return_value=True), \
             patch("pr_comments.post_thread_reply", return_value=True) as mock_reply, \
             patch("pr_comments.resolve_thread", return_value=True):
            rt._post_pending_fix_replies(state, "owner/repo", 1, threads_by_id)
        body = mock_reply.call_args[0][3]
        assert "Fixed in [`def5678`](https://github.com/owner/repo/commit/def5678)" in body
        assert "owner/repo/blob/def5678/x.py" in body
        assert fix.replies_pending is False

    def test_falls_back_to_the_linkless_shape_when_no_commit_can_be_named(
        self, rt, publishing_on,
    ):
        """HEAD never moved, so there is no commit to cite and none is invented."""
        fix, threads_by_id = self._queue(
            commit_status="commit_failed", commit_sha="", replies_pending=True,
            head_sha="abc1234",
        )
        state = _make_state(fix)
        with patch.object(rt, "_get_head_sha", return_value="abc1234"), \
             patch.object(rt, "_is_pushed", return_value=True), \
             patch.object(rt, "_find_addressing_commit", return_value=None), \
             patch("pr_comments.post_thread_reply", return_value=True) as mock_reply, \
             patch("pr_comments.resolve_thread", return_value=True):
            rt._post_pending_fix_replies(state, "owner/repo", 1, threads_by_id)
        body = mock_reply.call_args[0][3]
        assert "Fixed in" not in body
        assert "/commit/)" not in body
        assert "/blob//" not in body
        assert fix.replies_pending is False


class TestTriageOnlyPassQueue:
    """A pass with nothing to fix dropped every reply it drafted.

    The already-addressed and dismissed replies are sent during triage, before
    the pass knows whether anything is fixable, so a drafted run rendered them
    to stderr and kept no record. When the same pass then found nothing fixable
    it took the early return, which recorded neither a commit nor a pending
    queue — and `--finish --post` exited 0 having published nothing.

    Every test here therefore carries no fixed entry and no commit SHA, which
    is precisely the shape the old `if not fix.commit_sha: return` swallowed.
    """

    _ADDRESSED = f"t-{ThreadAction.ALREADY_ADDRESSED}"

    def _queue(self, *actions, **fix_kw):
        fix_kw.setdefault("replies_pending", True)
        fix = FixSummary(
            threads=[
                ThreadOutcome(id=f"t-{action}", summary=f"the {action} one",
                              file="x.py", line=1, action=action,
                              reason=f"because the {action} premise says so")
                for action in actions
            ],
            commit_status="no_changes",
            **fix_kw,
        )
        threads_by_id = {
            f"t-{action}": ReportThread(id=f"t-{action}", is_resolved=False,
                                        comments=[{"databaseId": 100 + n}])
            for n, action in enumerate(actions)
        }
        return fix, threads_by_id

    def test_drains_replies_a_pass_that_committed_nothing_left_behind(
        self, rt, publishing_on,
    ):
        fix, threads_by_id = self._queue(
            ThreadAction.ALREADY_ADDRESSED, ThreadAction.DISMISSED,
        )
        state = _make_state(fix)
        with patch.object(rt, "_get_head_sha", return_value="deadbee"), \
             patch.object(rt, "_find_addressing_commit", return_value=None), \
             patch("pr_comments.post_thread_reply", return_value=True) as mock_reply, \
             patch("pr_comments.resolve_thread", return_value=True):
            rt._post_pending_fix_replies(state, "owner/repo", 1, threads_by_id)
        assert mock_reply.call_count == 2
        assert fix.replies_posted == 2
        assert fix.replies_pending is False

    def test_only_the_already_addressed_thread_is_resolved(self, rt, publishing_on):
        """A dismissal is the reply most likely to be argued with — leave it open."""
        fix, threads_by_id = self._queue(
            ThreadAction.ALREADY_ADDRESSED, ThreadAction.DISMISSED,
        )
        state = _make_state(fix)
        with patch.object(rt, "_get_head_sha", return_value="deadbee"), \
             patch.object(rt, "_find_addressing_commit", return_value=None), \
             patch("pr_comments.post_thread_reply", return_value=True), \
             patch("pr_comments.resolve_thread", return_value=True) as mock_resolve:
            rt._post_pending_fix_replies(state, "owner/repo", 1, threads_by_id)
        assert [c.args[0] for c in mock_resolve.call_args_list] == [self._ADDRESSED]

    def test_a_drained_dismissal_still_carries_its_reasoning(self, rt, publishing_on):
        """`to_outcome` folds `reasoning` into `reason`; the drain must fold it back.

        Without that, the reply degrades to the bare "reviewed and determined to
        be inapplicable" fallback — telling a reviewer their premise fails and
        giving them nothing to argue with.
        """
        fix, threads_by_id = self._queue(ThreadAction.DISMISSED)
        state = _make_state(fix)
        with patch.object(rt, "_get_head_sha", return_value="deadbee"), \
             patch("pr_comments.post_thread_reply", return_value=True) as mock_reply:
            rt._post_pending_fix_replies(state, "owner/repo", 1, threads_by_id)
        assert "because the dismissed premise says so" in mock_reply.call_args.args[3]

    def test_a_commitless_queue_does_not_wait_on_a_push(self, rt, publishing_on):
        """These replies cite HEAD, not a fix commit, so there is nothing to wait for."""
        fix, threads_by_id = self._queue(ThreadAction.ALREADY_ADDRESSED)
        state = _make_state(fix)
        with patch.object(rt, "_is_pushed", return_value=False) as mock_pushed, \
             patch.object(rt, "_get_head_sha", return_value="deadbee"), \
             patch.object(rt, "_find_addressing_commit", return_value=None), \
             patch("pr_comments.post_thread_reply", return_value=True) as mock_reply, \
             patch("pr_comments.resolve_thread", return_value=True):
            rt._post_pending_fix_replies(state, "owner/repo", 1, threads_by_id)
        mock_pushed.assert_not_called()
        assert mock_reply.call_count == 1

    def test_no_changes_is_not_rewritten_as_pushed(self, rt, publishing_on):
        """The pass committed nothing; saying it pushed would invent a commit."""
        fix, threads_by_id = self._queue(ThreadAction.ALREADY_ADDRESSED)
        state = _make_state(fix)
        with patch.object(rt, "_get_head_sha", return_value="deadbee"), \
             patch.object(rt, "_find_addressing_commit", return_value=None), \
             patch("pr_comments.post_thread_reply", return_value=True), \
             patch("pr_comments.resolve_thread", return_value=True):
            rt._post_pending_fix_replies(state, "owner/repo", 1, threads_by_id)
        assert fix.commit_status == "no_changes"

    def test_a_draft_drain_keeps_the_queue(self, rt):
        """post_thread_reply is left real here — the draft gate lives inside it."""
        fix, threads_by_id = self._queue(ThreadAction.ALREADY_ADDRESSED)
        state = _make_state(fix)
        with patch.object(rt, "_get_head_sha", return_value="deadbee"), \
             patch.object(rt, "_find_addressing_commit", return_value=None):
            rt._post_pending_fix_replies(state, "owner/repo", 1, threads_by_id)
        assert fix.replies_posted == 0
        assert fix.replies_pending is True

    def test_a_settled_queue_is_left_alone(self, rt, publishing_on):
        fix, threads_by_id = self._queue(
            ThreadAction.ALREADY_ADDRESSED, replies_pending=False,
        )
        state = _make_state(fix)
        with patch("pr_comments.post_thread_reply") as mock_reply:
            rt._post_pending_fix_replies(state, "owner/repo", 1, threads_by_id)
        mock_reply.assert_not_called()


class TestTriageQueueIsRecorded:
    """The flag the drain turns on: a drafted triage owes its replies."""

    def _item(self):
        return CommentItem(id="t1", summary="s", file="x.py", line=1)

    def test_a_drafted_triage_records_what_it_did_not_send(self, rt):
        assert rt._triage_replies_drafted([self._item()], []) is True
        assert rt._triage_replies_drafted([], [self._item()]) is True

    def test_a_published_triage_owes_nothing(self, rt, publishing_on):
        assert rt._triage_replies_drafted([self._item()], [self._item()]) is False

    def test_a_triage_with_no_replies_owes_nothing(self, rt):
        assert rt._triage_replies_drafted([], []) is False


class TestReplyAttributionAcrossRounds:
    """The reply cited the running pass's commit, whatever fixed the thread.

    A single-round fixture cannot tell per-entry attribution from pass-level —
    they agree — which is exactly why this went unnoticed. So every test here
    drains a queue whose entries were fixed by different commits than the pass
    that is now sending their replies.
    """

    def _drain(self, rt, *outcomes, pass_sha=_PASS_SHA):
        """Send the deferred replies for `outcomes`; return body by thread id."""
        fix = FixSummary(
            threads=list(outcomes), commit_sha=pass_sha,
            commit_status="pushed", replies_pending=True,
        )
        threads_by_id = {
            o.id: ReportThread(id=o.id, is_resolved=False,
                               comments=[{"databaseId": 100 + n}])
            for n, o in enumerate(outcomes)
        }
        with patch.object(rt, "_is_pushed", return_value=True), \
             patch("pr_comments.post_thread_reply", return_value=True) as reply, \
             patch("pr_comments.resolve_thread", return_value=True):
            rt._post_pending_fix_replies(_make_state(fix), "owner/repo", 1, threads_by_id)
        bodies = [call[0][3] for call in reply.call_args_list]
        return dict(zip([o.id for o in outcomes], bodies))

    @staticmethod
    def _fixed(tid, sha, path):
        return ThreadOutcome(id=tid, summary=f"{tid} summary", file=path, line=1,
                             action=ThreadAction.FIXED, commit_sha=sha)

    def test_each_reply_cites_the_commit_that_fixed_it(self, rt, publishing_on):
        bodies = self._drain(
            rt,
            self._fixed("t1", _ROUND_1_SHA, "a.py"),
            self._fixed("t2", _ROUND_2_SHA, "b.py"),
        )
        assert _ROUND_1_SHA in bodies["t1"]
        assert _ROUND_2_SHA not in bodies["t1"]
        assert _ROUND_2_SHA in bodies["t2"]
        assert _PASS_SHA not in bodies["t1"] + bodies["t2"]

    def test_each_permalink_is_pinned_to_that_commit(self, rt, publishing_on):
        """The blob link is evidence — pinned to the wrong SHA it shows the wrong code."""
        bodies = self._drain(
            rt,
            self._fixed("t1", _ROUND_1_SHA, "a.py"),
            self._fixed("t2", _ROUND_2_SHA, "b.py"),
        )
        assert f"/blob/{_ROUND_1_SHA}/a.py" in bodies["t1"]
        assert f"/blob/{_ROUND_2_SHA}/b.py" in bodies["t2"]

    def test_an_entry_with_no_commit_of_its_own_uses_the_pass(self, rt, publishing_on):
        outcome = ThreadOutcome(id="t1", summary="t1 summary", file="a.py", line=1,
                                action=ThreadAction.FIXED)
        bodies = self._drain(rt, outcome)
        assert _PASS_SHA in bodies["t1"]

    def test_the_summary_row_and_the_reply_agree(self, rt, publishing_on):
        """One precedence rule, two renderers — they must not disagree."""
        outcome = self._fixed("t1", _ROUND_1_SHA, "a.py")
        bodies = self._drain(rt, outcome)
        cell = rt._fixed_status_for(outcome, rt.CommitPushResult(_PASS_SHA, "pushed", ""),
                                    "owner/repo")
        assert _ROUND_1_SHA in cell
        assert _ROUND_1_SHA in bodies["t1"]


class TestHandWrittenRepliesSurvive:
    """Re-draining the queue must not overwrite replies a human rewrote."""

    def _reply(self, rt, body):
        """Run the fix-reply upsert against a thread whose standing reply is `body`."""
        entry = CommentItem(id="t1", summary="fix it", file="a.py", line=1)
        threads_by_id = {"t1": _standing_reply_thread(body=body)}
        with patch("pr_comments.patch_thread_reply", return_value=True) as edit, \
             patch("pr_comments.post_thread_reply", return_value=True) as post:
            count = rt._post_fix_replies(
                [entry], threads_by_id, "owner/repo", 42, "abc1234",
            )
        return count, edit, post

    def test_a_rewritten_reply_is_left_alone(self, rt):
        count, edit, post = self._reply(rt, (
            "Applied: fix it\n\n"
            "On reflection we are not doing this — the reviewer's premise "
            "assumes a code path that was removed in #700."
        ))
        assert count == 0
        edit.assert_not_called()
        post.assert_not_called()

    def test_a_reply_that_is_still_the_template_is_refreshed(self, rt):
        """Pairs with the case above — proves that assertion is not vacuous."""
        count, edit, _ = self._reply(rt, (
            "Applied: fix it\n\n"
            "Fixed in [`0000000`](https://github.com/owner/repo/commit/0000000)."
        ))
        assert count == 1
        edit.assert_called_once()
        assert "abc1234" in edit.call_args[0][2]

    def test_a_reviewers_own_words_are_never_taken_for_ours(self, rt):
        count, _, _ = self._reply(rt, "Thanks, that works for me.")
        assert count == 0

    @pytest.mark.parametrize("body", [
        "Applied: fix it",
        "Applied: fix it\n\nResult is in [`a.py`](https://github.com/o/r/blob/s/a.py).",
        "Already addressed: fix it\n\nCurrent behaviour is at https://x/#L1.",
        "Already addressed in the current implementation: fix it",
        "Suggestion reviewed and determined to be inapplicable: nope",
        "Suggestion reviewed and determined to be inapplicable.\n\nSee https://x/#L1.",
        "Deferred: fix it\n\nTracked in [ENG-1](https://linear.app/i/ENG-1).",
        "Deferred: fix it\n\nTracked in ENG-1.\n\nUnchanged at https://x/#L1.",
    ])
    def test_every_generated_shape_is_recognised(self, rt, body):
        """A shape this misses is a reply the pass refuses to ever update again."""
        assert rt._is_generated_reply(body) is True

    @pytest.mark.parametrize("body", [
        "",
        "Sounds good to me.",
        "Applied: fix it\n\nBut see the caveat below.",
        "Deferred: fix it\n\nI disagree that this is deferrable.",
    ])
    def test_anything_else_is_treated_as_a_human_reply(self, rt, body):
        assert rt._is_generated_reply(body) is False

    def test_a_body_that_only_reads_like_ours_is_still_theirs(self, rt):
        """The near-miss is the dangerous one: it opens with our prefix and ends
        in a sentence, and only the followup opening tells it from the template."""
        body = (
            "Applied: fix it\n\n"
            "Landed in the follow-up branch rather than here."
        )
        assert rt._is_generated_reply(body) is False
        assert self._reply(rt, body)[0] == 0

    def test_a_hand_sentence_reusing_a_followup_opening_is_missed(self, rt):
        """Documents the ceiling on `_is_generated_reply`, not an endorsement.

        The followup pattern matches any sentence under a known opening, so a
        human who happens to start theirs with one is overwritten. Tightening it
        to demand a link would orphan the linkless generated shapes, which is
        the worse failure — a reply the pass can never update again.
        """
        body = "Applied: fix it\n\nFixed in the follow-up branch rather than here."
        assert rt._is_generated_reply(body) is True

    def test_the_addressed_fallback_body_is_recognised(self, rt, tmp_path):
        """The single-paragraph shapes are built by a different branch of the
        body builders, so assert on what they emit rather than on a transcribed
        copy — a wording change there must not silently orphan the reply."""
        entry = CommentItem(id="t1", summary="use helper", file="src/app.py")
        with patch("pr_comments.post_thread_reply", return_value=True) as post, \
             patch.object(rt, "_find_addressing_commit", return_value=None), \
             patch.object(rt, "_code_link", return_value=""):
            rt._post_already_addressed_replies(
                [entry], {"t1": ReportThread(id="t1", comments=[{"databaseId": 111}])},
                "owner/repo", 42, tmp_path,
            )
        assert rt._is_generated_reply(post.call_args[0][3]) is True

    def test_the_dismissal_body_with_no_evidence_is_recognised(self, rt, tmp_path):
        entry = CommentItem(id="t1", summary="not applicable", reasoning="premise fails")
        with patch("pr_comments.post_thread_reply", return_value=True) as post, \
             patch.object(rt, "_evidence_link", return_value=""):
            rt._post_dismissed_replies(
                [entry], {"t1": ReportThread(id="t1", comments=[{"databaseId": 111}])},
                "owner/repo", 42, tmp_path,
            )
        assert rt._is_generated_reply(post.call_args[0][3]) is True

    def test_the_dismissal_body_with_no_reasoning_is_recognised(self, rt, tmp_path):
        entry = CommentItem(id="t1", summary="not applicable")
        with patch("pr_comments.post_thread_reply", return_value=True) as post, \
             patch.object(rt, "_evidence_link", return_value=""):
            rt._post_dismissed_replies(
                [entry], {"t1": ReportThread(id="t1", comments=[{"databaseId": 111}])},
                "owner/repo", 42, tmp_path,
            )
        assert rt._is_generated_reply(post.call_args[0][3]) is True


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

    def _state_with_deferred(self, worktree):
        state = PRState(
            identity=PRIdentity(
                repo="owner/repo", branch="b", pr_number=42,
                head_sha="abc1234", worktree_root=str(worktree),
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
        pr_state.save_state(worktree, state)
        ctx = make_ctx(branch="b", worktree_root=worktree, head_sha="abc1234",
                       target_dir=worktree / "target")
        return state, ctx

    def _run(self, rt, state, ctx):
        captured = []
        with patch.object(rt, "_create_or_update_deferred_issue") as create, \
                patch.object(rt, "_post_deferred_replies"):
            create.side_effect = lambda deferred, *a, **kw: (
                captured.extend(deferred) or _filed("I_1", "u")
            )
            rt._finalize_deferred(state, ctx, {}, track={"t1"})
        return captured

    def test_reason_survives_into_the_tracking_issue(self, rt, worktree):
        state, ctx = self._state_with_deferred(worktree)
        captured = self._run(rt, state, ctx)
        assert [e.reason for e in captured] == ["agent could not auto-fix"]

    def test_the_rest_of_the_outcome_survives_too(self, rt, worktree):
        state, ctx = self._state_with_deferred(worktree)
        entry = self._run(rt, state, ctx)[0]
        assert (entry.id, entry.file, entry.line) == ("t1", "a.go", 7)
        assert (entry.reviewer, entry.summary) == ("kgn", "rename the guard")

    def test_the_caller_owns_the_save(self, rt, worktree):
        """Saving its own read would drop whatever the caller already wrote."""
        state, ctx = self._state_with_deferred(worktree)
        state.fix.commit_status = "pushed"
        self._run(rt, state, ctx)
        assert state.fix.deferred_issue_id == "I_1"
        on_disk = pr_state.load_state(worktree)
        assert on_disk.fix.commit_status == ""
        assert on_disk.fix.deferred_issue_id == ""


class TestDeferralRequiresAChoice:
    """Deferral is a decision. An agent running out of turns is not one."""

    def _state(self, worktree, ids):
        state = PRState(
            identity=PRIdentity(
                repo="owner/repo", branch="b", pr_number=42,
                head_sha="abc1234", worktree_root=str(worktree),
            ),
            fix=FixSummary(threads=[
                ThreadOutcome(
                    id=i, file="a.go", line=1, reviewer="kgn",
                    summary=f"item {i}", action=ThreadAction.DEFERRED,
                    reason="agent could not auto-fix",
                )
                for i in ids
            ]),
        )
        pr_state.save_state(worktree, state)
        return state

    def _ctx(self, worktree):
        return make_ctx(branch="b", worktree_root=worktree, head_sha="abc1234",
                        target_dir=worktree / "target")

    def _run(self, rt, state, ctx, track):
        captured = []
        with patch.object(rt, "_create_or_update_deferred_issue") as create, \
                patch.object(rt, "_post_deferred_replies") as reply:
            create.side_effect = lambda deferred, *a, **kw: (
                captured.extend(deferred) or _filed("I_1", "u")
            )
            rt._finalize_deferred(state, ctx, {}, track=track)
        return captured, create, reply

    def test_no_selection_files_nothing(self, rt, worktree):
        state = self._state(worktree, ["t1", "t2"])
        captured, create, reply = self._run(
            rt, state, self._ctx(worktree), track=frozenset())
        assert captured == []
        create.assert_not_called()
        reply.assert_not_called()

    def test_default_is_no_selection(self, rt, worktree):
        """Omitting track entirely must not fall back to filing everything."""
        state = self._state(worktree, ["t1", "t2"])
        with patch.object(rt, "_create_or_update_deferred_issue") as create, \
                patch.object(rt, "_post_deferred_replies"):
            rt._finalize_deferred(state, self._ctx(worktree), {})
        create.assert_not_called()

    def test_only_selected_threads_are_filed(self, rt, worktree):
        state = self._state(worktree, ["t1", "t2", "t3"])
        captured, _, _ = self._run(
            rt, state, self._ctx(worktree), track={"t2"})
        assert [e.id for e in captured] == ["t2"]

    def test_track_all_files_everything(self, rt, worktree):
        state = self._state(worktree, ["t1", "t2"])
        captured, _, _ = self._run(
            rt, state, self._ctx(worktree), track=rt.TRACK_ALL)
        assert [e.id for e in captured] == ["t1", "t2"]

    def test_unknown_id_is_an_error_not_a_silent_skip(self, rt, worktree):
        state = self._state(worktree, ["t1"])
        with pytest.raises(SystemExit):
            self._run(rt, state, self._ctx(worktree), track={"t9"})

    def test_a_non_deferred_id_is_also_an_error(self, rt, worktree):
        """Naming a thread the pass already fixed is a mistake worth surfacing."""
        state = self._state(worktree, ["t1"])
        state.fix.threads.append(ThreadOutcome(id="t2", action=ThreadAction.FIXED))
        with pytest.raises(SystemExit):
            self._run(rt, state, self._ctx(worktree), track={"t2"})


class TestUnfiledDeferralsAreNamed:
    """The report has to name exactly the threads nobody asked to file."""

    def _report(self, rt, ids, track):
        state = PRState(
            identity=PRIdentity(repo="owner/repo", branch="b", pr_number=42,
                                head_sha="abc1234", worktree_root="/tmp/wt"),
            fix=FixSummary(threads=[
                ThreadOutcome(id=i, action=ThreadAction.DEFERRED) for i in ids
            ]),
        )
        with patch.object(rt.log, "info") as info:
            rt._report_unfiled_deferrals(state, track)
        return " ".join(str(c) for c in info.call_args_list)

    def test_no_selection_names_every_deferral(self, rt):
        msg = self._report(rt, ["t1", "t2"], frozenset())
        assert "t1" in msg and "t2" in msg

    def test_partial_selection_names_only_the_rest(self, rt):
        """A non-empty selection is not a reason to stop reporting the others."""
        msg = self._report(rt, ["t1", "t2", "t3"], frozenset({"t2"}))
        assert "t1" in msg and "t3" in msg
        assert "t2" not in msg

    def test_track_all_leaves_nothing_unfiled(self, rt):
        assert self._report(rt, ["t1", "t2"], rt.TRACK_ALL) == ""

    def test_nothing_deferred_says_nothing(self, rt):
        assert self._report(rt, [], frozenset()) == ""

    def test_the_sentinel_is_not_an_empty_set(self, rt):
        """It selects everything; code that asks `if track:` must hear yes."""
        assert bool(rt.TRACK_ALL) is True


class TestTrackFlagParsing:
    def test_track_is_repeatable(self, rt):
        args = rt._build_parser().parse_args(
            ["--finish", "--track", "t1", "--track", "t2"])
        assert args.track == ["t1", "t2"]

    def test_track_all_is_separate(self, rt):
        args = rt._build_parser().parse_args(["--finish", "--track-all"])
        assert args.track_all is True
        assert args.track == []

    def test_track_defaults_to_empty(self, rt):
        args = rt._build_parser().parse_args(["--finish"])
        assert args.track == []
        assert args.track_all is False


# ── _finish_deferred_work ─────────────────────────────────────────────────


class TestFinishDeferredWork:
    """The close-out phase: push-deferred replies, tracking issue, summary."""

    def _ctx(self, worktree):
        return make_ctx(branch="b", worktree_root=worktree, head_sha="abc1234",
                        target_dir=worktree / "target")

    def _save(self, worktree, **fix_kw):
        pr_state.save_state(worktree / "target", PRState(
            identity=PRIdentity(
                repo="owner/repo", branch="b", pr_number=42,
                head_sha="abc1234", worktree_root=str(worktree),
            ),
            fix=FixSummary(**fix_kw),
        ))

    def test_all_three_steps_run_in_order(self, rt, worktree):
        self._save(worktree)
        order = []
        with patch.object(rt, "_post_pending_fix_replies",
                          side_effect=lambda *a, **k: order.append("replies")), \
                patch.object(rt, "_finalize_deferred",
                             side_effect=lambda *a, **k: order.append("issue")), \
                patch.object(rt, "_render_deferred_summary",
                             side_effect=lambda *a, **k: order.append("summary")):
            rt._finish_deferred_work(self._ctx(worktree), PRReport())
        assert order == ["replies", "issue", "summary"]

    def test_state_written_by_the_steps_is_persisted(self, rt, worktree):
        """The steps mutate in place; this phase is the one that saves."""
        self._save(worktree)

        def mark(state, *a, **k):
            state.fix.commit_status = "pushed"

        with patch.object(rt, "_post_pending_fix_replies", side_effect=mark), \
                patch.object(rt, "_finalize_deferred"), \
                patch.object(rt, "_render_deferred_summary"):
            rt._finish_deferred_work(self._ctx(worktree), PRReport())
        assert pr_state.load_state(worktree / "target").fix.commit_status == "pushed"

    def test_it_reads_state_from_disk_not_from_the_caller(self, rt, worktree):
        """The fix pass writes its outcomes there; a stale copy would miss them."""
        self._save(worktree, threads=[
            ThreadOutcome(id="t9", action=ThreadAction.DEFERRED, reason="r"),
        ])
        seen = []
        with patch.object(rt, "_post_pending_fix_replies",
                          side_effect=lambda st, *a, **k: seen.extend(st.fix.threads)), \
                patch.object(rt, "_finalize_deferred"), \
                patch.object(rt, "_render_deferred_summary"):
            rt._finish_deferred_work(self._ctx(worktree), PRReport())
        assert [t.id for t in seen] == ["t9"]

    def test_no_state_on_disk_is_a_no_op(self, rt, worktree):
        with patch.object(rt, "_post_pending_fix_replies") as replies:
            rt._finish_deferred_work(self._ctx(worktree), PRReport())
        replies.assert_not_called()

    def test_a_failing_step_propagates(self, rt, worktree):
        """A caller closing the loop needs a failure to be an error, not a log line."""
        self._save(worktree)
        with patch.object(rt, "_post_pending_fix_replies"), \
                patch.object(rt, "_finalize_deferred",
                             side_effect=RuntimeError("gh down")), \
                patch.object(rt, "_render_deferred_summary"):
            with pytest.raises(RuntimeError):
                rt._finish_deferred_work(self._ctx(worktree), PRReport())


class TestReconcileFixSnapshot:
    """Evidence on GitHub outranks a stale snapshot."""

    def _state(self):
        return _make_state(FixSummary(head_sha="aaaaaaa", threads=[
            ThreadOutcome(id="t1", file="a.go", line=1, reviewer="kgn",
                          summary="one", action=ThreadAction.DEFERRED,
                          reason="agent could not auto-fix"),
        ]))

    def _thread(self, comments, **kw):
        kw.setdefault("state", ThreadState.NEW)
        kw.setdefault("is_resolved", False)
        return ReportThread(id="t1", comments=comments, **kw)

    def test_resolved_thread_is_reclaimed(self, rt):
        state = self._state()
        threads = {"t1": self._thread([{"body": "x"}],
                                      state=ThreadState.RESOLVED, is_resolved=True)}
        assert rt._reconcile_fix_snapshot(state, threads) == 1
        assert state.fix.threads[0].action == ThreadAction.FIXED

    def test_thread_with_a_fix_reply_is_reclaimed_even_if_unresolved(self, rt):
        """The 13 contradicted threads on the incident PR all looked like this."""
        state = self._state()
        threads = {"t1": self._thread([
            {"body": "please rename this"},
            {"body": "Applied: renamed the guard\n\nFixed in `abc1234`."},
        ])}
        assert rt._reconcile_fix_snapshot(state, threads) == 1
        assert state.fix.threads[0].action == ThreadAction.FIXED

    def test_genuinely_open_thread_stays_deferred(self, rt):
        state = self._state()
        threads = {"t1": self._thread([{"body": "please rename this"}])}
        assert rt._reconcile_fix_snapshot(state, threads) == 0
        assert state.fix.threads[0].action == ThreadAction.DEFERRED

    def test_a_deferred_reply_is_not_evidence_of_a_fix(self, rt):
        """Our own prior Deferred: reply must not reclaim the thread."""
        state = self._state()
        threads = {"t1": self._thread([
            {"body": "please rename this"},
            {"body": "Deferred: rename the guard\n\nTracked in ENG-3021."},
        ])}
        assert rt._reconcile_fix_snapshot(state, threads) == 0
        assert state.fix.threads[0].action == ThreadAction.DEFERRED

    def test_a_thread_absent_from_github_stays_deferred(self, rt):
        """An id nothing on GitHub knows anything about settles nothing.

        Still the right answer for a genuinely unknown thread id. A comment item
        is no longer the same case: it is absent from this map by construction,
        and TestCommentItemsSettleThroughTheirSource covers what does settle it.
        """
        state = self._state()
        assert rt._reconcile_fix_snapshot(state, {}, frozenset({"77"})) == 0
        assert state.fix.threads[0].action == ThreadAction.DEFERRED

    def test_a_needs_human_thread_settled_by_hand_is_reclaimed(self, rt):
        """The pass handed it to the operator; the operator answering it is the ending."""
        state = _make_state(FixSummary(head_sha="aaaaaaa", threads=[
            ThreadOutcome(id="t1", action=ThreadAction.NEEDS_HUMAN, reason="contested"),
        ]))
        threads = {"t1": self._thread([{"body": "x"}],
                                      state=ThreadState.RESOLVED, is_resolved=True)}
        assert rt._reconcile_fix_snapshot(state, threads) == 1
        assert state.fix.threads[0].action == ThreadAction.FIXED

    def test_a_needs_human_thread_still_open_is_left_alone(self, rt):
        state = _make_state(FixSummary(head_sha="aaaaaaa", threads=[
            ThreadOutcome(id="t1", action=ThreadAction.NEEDS_HUMAN, reason="contested"),
        ]))
        threads = {"t1": self._thread([{"body": "why not do it the other way?"}])}
        assert rt._reconcile_fix_snapshot(state, threads) == 0
        assert state.fix.threads[0].action == ThreadAction.NEEDS_HUMAN

    def test_settled_outcomes_are_left_alone(self, rt):
        """Only the two open actions are reconcilable — the rest are already decided."""
        settled = (ThreadAction.FIXED, ThreadAction.DISMISSED,
                   ThreadAction.ALREADY_ADDRESSED)
        state = _make_state(FixSummary(head_sha="aaaaaaa", threads=[
            ThreadOutcome(id=f"t{i}", action=action)
            for i, action in enumerate(settled)
        ]))
        threads = {
            f"t{i}": ReportThread(id=f"t{i}", comments=[{"body": "x"}],
                                  state=ThreadState.RESOLVED, is_resolved=True)
            for i in range(len(settled))
        }
        assert rt._reconcile_fix_snapshot(state, threads) == 0
        assert [t.action for t in state.fix.threads] == list(settled)

    def test_the_reason_records_why_it_flipped(self, rt):
        state = self._state()
        threads = {"t1": self._thread([{"body": "x"}],
                                      state=ThreadState.RESOLVED, is_resolved=True)}
        rt._reconcile_fix_snapshot(state, threads)
        assert "reconciled" in state.fix.threads[0].reason


class TestReconcileRunsBeforeTheWrites:
    """Within one invocation the two must not disagree about the same thread."""

    def test_reconciled_thread_never_reaches_the_tracking_issue(self, rt, worktree):
        pr_state.save_state(worktree / "target", PRState(
            identity=PRIdentity(repo="owner/repo", branch="b", pr_number=42,
                                head_sha="aaaaaaa", worktree_root=str(worktree)),
            fix=FixSummary(head_sha="aaaaaaa", threads=[
                ThreadOutcome(id="t1", file="a.go", line=1, reviewer="kgn",
                              summary="one", action=ThreadAction.DEFERRED),
            ]),
        ))
        ctx = make_ctx(branch="b", worktree_root=worktree, head_sha="aaaaaaa",
                       target_dir=worktree / "target")
        report = PRReport(threads=[ReportThread(
            id="t1", state=ThreadState.NEW, is_resolved=False,
            comments=[{"body": "x"}, {"body": "Applied: one\n\nFixed in `abc1234`."}],
        )])
        with patch.object(rt, "_get_head_sha", return_value="aaaaaaa"), \
                patch.object(rt, "_create_or_update_deferred_issue") as create, \
                patch.object(rt, "_post_deferred_replies") as reply, \
                patch.object(rt, "_render_deferred_summary"):
            rt._finish_deferred_work(ctx, report, track=rt.TRACK_ALL)
        create.assert_not_called()
        reply.assert_not_called()

    def test_the_flip_is_persisted(self, rt, worktree):
        """Otherwise the next --finish re-derives it from the same stale row."""
        pr_state.save_state(worktree / "target", PRState(
            identity=PRIdentity(repo="owner/repo", branch="b", pr_number=42,
                                head_sha="aaaaaaa", worktree_root=str(worktree)),
            fix=FixSummary(head_sha="aaaaaaa", threads=[
                ThreadOutcome(id="t1", action=ThreadAction.DEFERRED),
            ]),
        ))
        ctx = make_ctx(branch="b", worktree_root=worktree, head_sha="aaaaaaa",
                       target_dir=worktree / "target")
        report = PRReport(threads=[ReportThread(
            id="t1", state=ThreadState.RESOLVED, is_resolved=True,
            comments=[{"body": "x"}],
        )])
        with patch.object(rt, "_get_head_sha", return_value="aaaaaaa"), \
                patch.object(rt, "_render_deferred_summary"):
            rt._finish_deferred_work(ctx, report)
        on_disk = pr_state.load_state(worktree / "target")
        assert on_disk.fix.threads[0].action == ThreadAction.FIXED


class TestStaleSnapshotIsAnnounced:
    """A snapshot from a different HEAD is a record of the past, not a plan."""

    def _state(self, worktree, snapshot_sha):
        state = PRState(
            identity=PRIdentity(repo="owner/repo", branch="b", pr_number=42,
                                head_sha=snapshot_sha, worktree_root=str(worktree)),
            fix=FixSummary(
                head_sha=snapshot_sha,
                threads=[ThreadOutcome(id="t1", file="a.go", line=7, reviewer="kgn",
                                       summary="rename the guard",
                                       action=ThreadAction.DEFERRED,
                                       reason="agent could not auto-fix")],
            ),
        )
        pr_state.save_state(worktree / "target", state)
        return state

    def _ctx(self, worktree):
        return make_ctx(branch="b", worktree_root=worktree, head_sha="aaaaaaa",
                        target_dir=worktree / "target")

    def _warnings(self, rt, worktree, current_sha):
        seen = []
        with patch.object(rt, "_get_head_sha", return_value=current_sha), \
                patch.object(rt.log, "warn", side_effect=seen.append), \
                patch.object(rt, "_post_pending_fix_replies"), \
                patch.object(rt, "_render_deferred_summary"), \
                patch.object(rt, "_finalize_deferred"):
            rt._finish_deferred_work(self._ctx(worktree), PRReport())
        return seen

    def test_head_moved_is_announced(self, rt, worktree):
        self._state(worktree, "aaaaaaa")
        warned = self._warnings(rt, worktree, "bbbbbbb")
        assert any("aaaaaaa" in w and "bbbbbbb" in w for w in warned)

    def test_head_unchanged_says_nothing(self, rt, worktree):
        self._state(worktree, "aaaaaaa")
        assert self._warnings(rt, worktree, "aaaaaaa") == []

    def test_missing_snapshot_sha_is_treated_as_stale(self, rt, worktree):
        """Legacy state predates the field; it cannot be vouched for."""
        state = self._state(worktree, "aaaaaaa")
        state.fix.head_sha = ""
        pr_state.save_state(worktree / "target", state)
        assert any("(unrecorded)" in w for w in self._warnings(rt, worktree, "aaaaaaa"))

    def test_an_empty_snapshot_has_nothing_to_be_stale_about(self, rt, worktree):
        pr_state.save_state(worktree / "target", PRState(
            identity=PRIdentity(repo="owner/repo", branch="b", pr_number=42,
                                head_sha="aaaaaaa", worktree_root=str(worktree)),
            fix=FixSummary(),
        ))
        assert self._warnings(rt, worktree, "bbbbbbb") == []


class TestFinishFlag:
    """`--finish` is the one spelling.

    It shipped as `--resolve-verified` on the removed `claude-review threads`
    subcommand and carried both historical names as aliases. Nothing calls them
    any more, and three spellings is three chances for a doc to pick a dead one
    — so they are gone, and an unknown-flag error is the whole migration path.
    """

    def test_finish_sets_finish(self, rt):
        assert rt._build_parser().parse_args(["--finish"]).finish

    def test_it_is_off_by_default(self, rt):
        assert not rt._build_parser().parse_args([]).finish

    @pytest.mark.parametrize("alias", ["--resolve", "--resolve-verified"])
    def test_the_old_aliases_are_rejected(self, rt, alias):
        # Exit 2 specifically: argparse's unknown-flag code, not any SystemExit
        # a broken parser might raise on the way past.
        with pytest.raises(SystemExit) as exc:
            rt._build_parser().parse_args([alias])
        assert exc.value.code == 2


# ── --reply ──────────────────────────────────────────────────────────────


def _raw_thread(tid, comment_ids, login="reviewer", resolved=False):
    return {
        "id": tid,
        "isResolved": resolved,
        "path": "src/app.py",
        "line": 4,
        "comments": {"nodes": [
            {"databaseId": cid, "body": "point", "author": {"login": login}}
            for cid in comment_ids
        ]},
    }


def _raw_answered_thread(tid="PRRT_abc", resolved=False):
    """A reviewer's thread we have already replied to, as GraphQL returns it."""
    raw = _raw_thread(tid, [111, 222], resolved=resolved)
    raw["comments"]["nodes"][1]["author"] = {"login": "me"}
    return raw


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

    def test_carries_the_viewer_login_the_upsert_needs(self, rt):
        """Without it the upsert cannot tell our own reply from a reviewer's."""
        raw = [_raw_answered_thread(resolved=True)]
        thread = rt._find_reply_target(raw, "PRRT_abc", "me")
        assert thread.my_login == "me"
        assert rt._our_last_reply_id(thread) == 222


class TestRunReply:

    def _ctx(self, tmp_path):
        return make_ctx(branch="b", worktree_root=tmp_path, head_sha="abc1234",
                        target_dir=tmp_path / "target")

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

    def test_a_drafted_reply_says_draft_and_sends_nothing(self, rt, tmp_path):
        """The closing line must not claim a post no draft run ever made."""
        body = tmp_path / "reply.md"
        body.write_text("See https://github.com/owner/repo/blob/abc/src/app.py#L4.")
        fetch_pr, fetch_threads = self._patches(rt, [_raw_thread("PRRT_abc", [111])])
        with fetch_pr, fetch_threads, \
             patch.object(rt.pc.subprocess, "run") as run, \
             patch.object(rt.log, "info") as info:
            assert rt._run_reply(self._ctx(tmp_path), "PRRT_abc", str(body)) == 0
        run.assert_not_called()
        lines = [c[0][0] for c in info.call_args_list]
        assert any("DRAFT (not published)" in line for line in lines)
        assert not any("Posted" in line or "Edited" in line for line in lines)

    @pytest.mark.parametrize("raw,call,verb", [
        ([_raw_thread("PRRT_abc", [111])], "post_thread_reply", "Posted"),
        ([_raw_answered_thread()], "patch_thread_reply", "Edited"),
    ])
    def test_a_published_reply_reports_what_it_did(
        self, rt, tmp_path, publishing_on, raw, call, verb,
    ):
        body = tmp_path / "reply.md"
        body.write_text("See https://github.com/owner/repo/blob/abc/src/app.py#L4.")
        fetch_pr, fetch_threads = self._patches(rt, raw, login="me")
        with fetch_pr, fetch_threads, \
             patch(f"pr_comments.{call}", return_value=True), \
             patch.object(rt.log, "info") as info:
            assert rt._run_reply(self._ctx(tmp_path), "PRRT_abc", str(body)) == 0
        assert f"{verb} reply on PRRT_abc" in [c[0][0] for c in info.call_args_list]

    def test_edits_the_standing_reply_on_a_resolved_thread(
        self, rt, tmp_path, publishing_on,
    ):
        """End to end: --finish --post resolves the threads it answers."""
        body = tmp_path / "reply.md"
        body.write_text("Revised. https://github.com/owner/repo/blob/abc/src/app.py#L4")
        fetch_pr, fetch_threads = self._patches(
            rt, [_raw_answered_thread(resolved=True)], login="me",
        )
        with fetch_pr, fetch_threads, \
             patch("pr_comments.post_thread_reply") as post, \
             patch("pr_comments.patch_thread_reply", return_value=True) as edit:
            assert rt._run_reply(self._ctx(tmp_path), "PRRT_abc", str(body)) == 0
        post.assert_not_called()
        assert edit.call_args[0][1] == 222

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


def _standing_reply_thread(tid="t1", body="Applied: old take", **kw):
    """A thread whose last comment is ours and unanswered — the editable case."""
    kw.setdefault("state", ThreadState.ADDRESSED)
    kw.setdefault("my_login", "me")
    return ReportThread(id=tid, comments=[
        {"databaseId": 111, "body": "reviewer's point", "author": {"login": "kgn"}},
        {"databaseId": 222, "body": body, "author": {"login": "me"}},
    ], **kw)


def _dismissed(**overrides):
    """A dismissed-verdict CommentItem for the reply-upsert tests below."""
    fields = {"id": "t1", "summary": "not applicable", "reasoning": "reason"}
    fields.update(overrides)
    return CommentItem(**fields)


class TestOurLastReplyId:
    """Edit-vs-post turns on who spoke last, not on the thread's lifecycle."""

    def test_our_unanswered_reply_is_editable(self, rt):
        assert rt._our_last_reply_id(_standing_reply_thread()) == 222

    def test_resolution_does_not_retire_our_standing_reply(self, rt):
        """--finish --post resolves what it replies to, and RESOLVED outranks
        ADDRESSED — reading the state the other way stacks a second comment."""
        thread = _standing_reply_thread(state=ThreadState.RESOLVED, is_resolved=True)
        assert rt._our_last_reply_id(thread) == 222

    def test_none_once_a_reviewer_has_answered(self, rt):
        thread = _standing_reply_thread(state=ThreadState.CONTESTED)
        thread.comments.append(
            {"databaseId": 333, "body": "not what I meant", "author": {"login": "kgn"}},
        )
        assert rt._our_last_reply_id(thread) is None

    def test_none_for_a_lone_root_comment(self, rt):
        thread = ReportThread(id="t1", my_login="me", state=ThreadState.ADDRESSED,
                              comments=[{"databaseId": 111, "author": {"login": "me"}}])
        assert rt._our_last_reply_id(thread) is None

    def test_none_without_a_viewer_login(self, rt):
        """An unknown viewer cannot claim authorship of anything."""
        thread = _standing_reply_thread(my_login="")
        assert rt._our_last_reply_id(thread) is None

    def test_none_for_a_thread_that_is_not_there(self, rt):
        assert rt._our_last_reply_id(None) is None


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
            "t1": ReportThread(id="t1", my_login="me",
                               state=ThreadState.CONTESTED, comments=[
                {"databaseId": 111, "body": "reviewer's point",
                 "author": {"login": "kgn"}},
                {"databaseId": 222, "body": "Applied: old take",
                 "author": {"login": "me"}},
                {"databaseId": 333, "body": "that is not what I meant",
                 "author": {"login": "kgn"}},
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
            "t1": ReportThread(id="t1", state=ThreadState.ADDRESSED, my_login="me",
                               comments=[{"databaseId": 111, "body": "my own note",
                                          "author": {"login": "me"}}]),
        }
        with patch("pr_comments.post_thread_reply", return_value=True) as post, \
             patch("pr_comments.patch_thread_reply") as edit:
            count = rt._post_dismissed_replies(
                dismissed, threads_by_id, "owner/repo", 42, tmp_path,
            )
        assert count == 1
        edit.assert_not_called()
        assert post.call_args[0][2] == 111

    @pytest.mark.parametrize("standing,failing", [
        (True, "patch_thread_reply"),
        (False, "post_thread_reply"),
    ])
    def test_a_failed_call_is_not_counted(self, rt, tmp_path, standing, failing):
        """replies_posted feeds the run summary, so a silent failure would inflate it."""
        dismissed = [_dismissed()]
        thread = _standing_reply_thread() if standing else ReportThread(
            id="t1", my_login="me",
            comments=[{"databaseId": 111, "author": {"login": "kgn"}}],
        )
        with patch("pr_comments.post_thread_reply", return_value=True), \
             patch("pr_comments.patch_thread_reply", return_value=True), \
             patch(f"pr_comments.{failing}", return_value=False):
            count = rt._post_dismissed_replies(
                dismissed, {"t1": thread}, "owner/repo", 42, tmp_path,
            )
        assert count == 0

    def test_a_fix_replaces_an_earlier_dismissal(self, rt):
        """Round one dismissed the thread, round two fixed it.

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


# ── triage_result_from_dict ──────────────────────────────────────────────

class TestTriageResultFromDict:
    """The AI is the input class that is malformed occasionally by nature —
    this must degrade a wrong-shaped field rather than crash the whole
    triage pass, the same as it did before `serde.from_dict` started
    rejecting non-dict input outright.
    """

    def test_a_non_dict_stats_value_degrades_to_default_stats(self):
        result = triage_result_from_dict({
            "threads": [{"id": "t1", "summary": "ok"}],
            "stats": [],
        })
        assert result.stats == TriageStats()
        assert result.threads == [CommentItem(id="t1", summary="ok")]

    def test_a_non_dict_thread_entry_degrades_to_a_default_item(self):
        result = triage_result_from_dict({
            "threads": ["not-a-dict", {"id": "t2", "summary": "real"}],
        })
        assert result.threads == [CommentItem(), CommentItem(id="t2", summary="real")]

    def test_a_non_dict_comment_item_degrades_to_a_default_item(self):
        result = triage_result_from_dict({"comment_items": [0]})
        assert result.comment_items == [CommentItem()]

    def test_an_explicit_null_list_degrades_to_no_entries(self):
        """`d.get(key, [])` only defaults on an absent key, not a null one."""
        result = triage_result_from_dict({"threads": None, "comment_items": None})
        assert result.threads == []
        assert result.comment_items == []

    def test_a_scalar_where_a_list_belongs_degrades_to_no_entries(self):
        result = triage_result_from_dict({"threads": "t1", "comment_items": 7})
        assert result.threads == []
        assert result.comment_items == []

    def test_a_null_stats_value_degrades_to_default_stats(self):
        assert triage_result_from_dict({"stats": None}).stats == TriageStats()

    def test_well_formed_input_is_unaffected(self):
        result = triage_result_from_dict({
            "threads": [{"id": "t1"}],
            "comment_items": [{"id": "c1"}],
            "stats": {"total": 3, "actionable": 2},
        })
        assert result.threads == [CommentItem(id="t1")]
        assert result.comment_items == [CommentItem(id="c1")]
        assert result.stats.total == 3
        assert result.stats.actionable == 2


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

    def test_only_fixed_outcomes_carry_the_pass_commit(self, rt):
        """A deferred thread was not fixed by this commit — or any."""
        fixed = self._entry("valid")
        deferred = CommentItem(id="t2", file="b.py", line=2, reviewer="kgn",
                               summary="too complex")
        outcomes = rt._build_thread_outcomes(
            [fixed], [deferred], [], [], commit_sha="deadbee",
        )
        by_id = {o.id: o.commit_sha for o in outcomes}
        assert by_id == {"t1": "deadbee", "t2": ""}

    def test_no_commit_leaves_the_sha_empty(self, rt):
        outcomes = rt._build_thread_outcomes(
            [self._entry("valid")], [], [], [], commit_sha="",
        )
        assert outcomes[0].commit_sha == ""


class TestHoldIfSuperseded:
    """What the preflight's findings are allowed to do to this run.

    A hold, not the refusal `pr review` answers with: by the time this runs the
    triage pass is already paid for, so stopping saves nothing — what must not
    happen is asserting outward that superseded code was fixed. Detection
    itself is `supersession`'s, and tested there.
    """

    def test_evidence_shuts_the_gate(self, rt, publishing_on):
        import publishing
        rt._hold_if_superseded(supersession_verdict(supersession_evidence()))
        assert publishing.enabled() is False
        assert "supersession signal" in publishing.held()

    def test_context_alone_leaves_it_open(self, rt, publishing_on):
        """A rebase is how the problem becomes visible, not the problem."""
        import publishing
        rt._hold_if_superseded(supersession_verdict(supersession_context()))
        assert publishing.enabled() is True

    def test_nothing_found_says_nothing(self, rt, publishing_on, capsys):
        rt._hold_if_superseded(supersession_verdict())
        assert capsys.readouterr().err == ""

    def test_the_output_names_the_signal_that_fired(self, rt, publishing_on, capsys):
        rt._hold_if_superseded(supersession_verdict(
            supersession_context("replayed onto a moved base"),
            supersession_evidence("`foo` is gone from origin/main"),
        ))
        err = capsys.readouterr().err
        assert "[rebase_skew] replayed onto a moved base" in err
        assert "[readds_removed_symbol] `foo` is gone from origin/main" in err

    def test_the_hold_is_recorded_on_the_trail(self, rt, publishing_on):
        trail = MagicMock()
        rt._hold_if_superseded(supersession_verdict(supersession_evidence()), trail)
        data = trail.decision.call_args.kwargs["data"]
        assert data["signals"] == [SupersessionKind.READDS_REMOVED_SYMBOL]


class TestHoldWhileContested:
    """Real fixes must not reach a branch a reviewer said should not land."""

    @staticmethod
    def _entry(reason, id="t1"):
        return CommentItem(id=id, file="f.go", line=10, reviewer="kgn",
                           summary="the root cause does not exist", reason=reason)

    def test_an_open_thread_shuts_the_gate(self, rt, publishing_on):
        import publishing
        rt._hold_while_contested([self._entry("needs_discussion")])
        assert publishing.enabled() is False
        assert "1 thread(s)" in publishing.held()

    def test_nothing_contested_leaves_the_gate_alone(self, rt, publishing_on):
        import publishing
        rt._hold_while_contested([])
        assert publishing.enabled() is True
        assert publishing.held() == ""

    def test_every_needs_human_reason_holds(self, rt, publishing_on):
        """Contested, conflicting, question, complex — all route to needs_human.

        The halt is on the bucket, not the reason: distinguishing a
        premise-invalidating question from a bikeshed is the problem this
        deliberately does not try to solve.
        """
        import publishing
        rt._hold_while_contested([self._entry("complex")])
        assert publishing.enabled() is False

    def test_the_hold_is_recorded_on_the_trail(self, rt, publishing_on):
        trail = MagicMock()
        rt._hold_while_contested(
            [self._entry("needs_discussion"), self._entry("question", id="t2")],
            trail,
        )
        trail.decision.assert_called_once()
        data = trail.decision.call_args.kwargs["data"]
        assert data["reasons"] == ["needs_discussion", "question"]


class TestFixPassHoldsWhenContested:
    """The whole point of the hold, asserted through `_run_comment_fix` itself.

    `TestHoldWhileContested` and `TestCommitAndPush` each cover one half. Neither
    catches a reorder that puts the commit before the hold, which is precisely
    how the bug works — so this drives the real entry point with one contested
    thread and one fixable one, and asserts nothing was pushed.
    """

    @staticmethod
    def _item(id, verification, **kw):
        return CommentItem(
            id=id, file="f.go", line=10, reviewer="kgn", summary=f"{id} summary",
            classification="actionable_suggestion", verification=verification,
            complexity="low", state=ThreadState.NEW, **kw,
        )

    def _run(self, rt, tmp_path, *, contested, publishing_on_):
        threads = [self._item("t1", "valid")]
        if contested:
            threads.append(self._item("t2", "needs_discussion"))

        # Real comment IDs, so the reply path can actually fire — without them
        # `_post_fix_replies` finds nothing to reply to and returns 0 whether or
        # not the gate is shut, which would make the reply assertion vacuous.
        report = PRReport(
            repo="owner/repo", pr_number=1,
            threads=[
                ReportThread(id=t.id, file=t.file, line=t.line,
                             comments=[{"databaseId": 100 + n}])
                for n, t in enumerate(threads)
            ],
        )
        ctx = SimpleNamespace(
            repo="owner/repo", branch="b", pr_number=1, head_sha="aaa1111",
            target_dir=tmp_path,
        )
        pushes = []
        commits = []

        def mock_run(cmd, **kwargs):
            if "push" in cmd:
                pushes.append(cmd)
            if "commit" in cmd:
                commits.append(cmd)
            return _make_completed(0, stdout="abc1234\n")

        batch = rt.FixBatchResult(
            tracking=TrackingResult(fixed=[threads[0]]),
            unproductive=False, max_turns=10, max_budget=1.0,
        )
        with patch.object(rt, "_run_fix_batch", return_value=batch), \
             patch.object(rt, "_find_and_update_main_worktree", return_value=None), \
             patch.object(rt, "_resolve_default_branch", return_value="main"), \
             patch.object(rt, "_persist_fix_state"), \
             patch.object(rt.review_common, "has_uncommitted_changes", return_value=True), \
             patch.object(rt.subprocess, "run", side_effect=mock_run), \
             patch("pr_comments.post_thread_reply", return_value=True), \
             patch("pr_comments.post_issue_comment", return_value="u"), \
             patch("pr_comments.resolve_thread", return_value=True):
            result = rt._run_comment_fix(
                TriageResult(threads=threads), report, tmp_path, ctx,
            )
        return SimpleNamespace(result=result, pushes=pushes, commits=commits)

    def test_a_contested_thread_stops_the_push(self, rt, tmp_path, publishing_on):
        run = self._run(rt, tmp_path, contested=True, publishing_on_=True)
        assert run.result.commit_status == CommitStatus.PUSH_HELD
        assert run.pushes == []

    def test_the_commit_is_still_made(self, rt, tmp_path, publishing_on):
        """Holding must not cost the work — only its publication.

        A local commit asserts nothing to a reviewer, since
        only the push makes it visible, and the push is what the hold stops.
        """
        run = self._run(rt, tmp_path, contested=True, publishing_on_=True)
        assert run.result.commit_sha == "abc1234"
        assert run.commits

    def test_the_fixes_are_still_applied(self, rt, tmp_path, publishing_on):
        """Holding must not cost the work — only the acts that assert it."""
        run = self._run(rt, tmp_path, contested=True, publishing_on_=True)
        assert [t.id for t in run.result.fixed] == ["t1"]

    def test_no_fixed_replies_go_out_while_held(self, rt, tmp_path, publishing_on):
        run = self._run(rt, tmp_path, contested=True, publishing_on_=True)
        assert run.result.replies_posted == 0
        assert run.result.summary_url is None
        assert run.result.summary_deferred is True

    def test_an_uncontested_pass_still_pushes(self, rt, tmp_path, publishing_on):
        """The gate must not have closed on the common case."""
        run = self._run(rt, tmp_path, contested=False, publishing_on_=True)
        assert run.result.commit_status == CommitStatus.PUSHED
        assert run.pushes
        assert run.commits

    def test_an_uncontested_pass_still_replies(self, rt, tmp_path, publishing_on):
        """Pairs with the held case: proves that assertion is not vacuous."""
        run = self._run(rt, tmp_path, contested=False, publishing_on_=True)
        assert run.result.replies_posted == 1


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


# ── rows the published comment has and local state does not ────────────────


ROUND_ONE_ROW = (
    "| [drop the guard](https://github.com/owner/repo/pull/1#discussion_r111) "
    "| @kgn | [`old.go:4`](https://github.com/owner/repo/blob/aaaaaaa/old.go#L4) "
    "| Fixed in [`9f2e1a0`](https://github.com/owner/repo/commit/9f2e1a0) |"
)


def _published_summary(rt, *rows: str) -> str:
    """A prior summary comment carrying the given rendered rows."""
    return "\n".join([
        rt._SUMMARY_MARKER, "## Review Comments Addressed", "",
        "**1 fixed**", "",
        rt._SUMMARY_TABLE_HEADER, rt._SUMMARY_TABLE_DIVIDER,
        *rows, "",
    ])


class TestSummaryRowKey:
    """Two renders of one thread must key the same, across rounds."""

    def test_anchor_identifies_the_row(self, rt):
        assert rt._summary_row_key(ROUND_ONE_ROW) == "#discussion_r111"

    def test_action_and_sha_may_change(self, rt):
        later = ROUND_ONE_ROW.replace("9f2e1a0", "bbbbbbb").replace("aaaaaaa", "ccccccc")
        assert rt._summary_row_key(later) == rt._summary_row_key(ROUND_ONE_ROW)

    def test_comment_item_anchors_do_not_collide_with_threads(self, rt):
        thread = "| [x](https://x/pull/1#discussion_r7) | @a | `f.go` | Fixed |"
        item = "| [x](https://x/pull/1#issuecomment-7) | @a | `f.go` | Fixed |"
        assert rt._summary_row_key(thread) != rt._summary_row_key(item)

    def test_falls_back_to_the_row_text_without_a_permalink(self, rt):
        row = "| plain summary | @kgn | `f.go:2` | Fixed in `abc` |"
        assert rt._summary_row_key(row) == "plain summary | @kgn | f.go:2"

    def test_the_fallback_ignores_the_action_cell(self, rt):
        row = "| plain summary | @kgn | `f.go:2` | Deferred |"
        later = "| plain summary | @kgn | `f.go:2` | Fixed in `abc` |"
        assert rt._summary_row_key(row) == rt._summary_row_key(later)


class TestPipesStayInTheirCell:
    """Summary prose is unconstrained; one pipe would shift every later cell."""

    def _row(self, rt, summary, status="Fixed"):
        entry = CommentItem(id="t1", summary=summary, reviewer="kgn", file="f.go", line=2)
        return rt._build_row(entry, status, {}, "owner/repo", 1)

    def test_a_summary_pipe_does_not_add_a_cell(self, rt):
        row = self._row(rt, "use a || b, not a | b")
        assert len(rt._row_cells(row)) == len(rt._SUMMARY_TABLE_COLUMNS)

    def test_a_status_pipe_does_not_add_a_cell(self, rt):
        row = self._row(rt, "plain", status="Deferred — a | b")
        assert len(rt._row_cells(row)) == len(rt._SUMMARY_TABLE_COLUMNS)

    def test_the_fallback_key_survives_a_summary_pipe(self, rt):
        deferred = self._row(rt, "use a | b", status="Deferred")
        fixed = self._row(rt, "use a | b", status="Fixed in `abc`")
        assert rt._summary_row_key(deferred) == rt._summary_row_key(fixed)
        assert rt._carried_over_rows(
            _published_summary(rt, deferred), _published_summary(rt, fixed)) == []


class TestSummaryTableRows:
    def test_header_and_divider_are_not_rows(self, rt):
        assert rt._summary_table_rows(_published_summary(rt, ROUND_ONE_ROW)) == [ROUND_ONE_ROW]

    def test_a_body_without_a_table_has_no_rows(self, rt):
        assert rt._summary_table_rows("## Review Comments Addressed\n\nnothing yet\n") == []


class TestCarriedOverRows:
    def test_a_row_state_never_saw_is_carried(self, rt):
        fresh = _published_summary(
            rt,
            "| [new work](https://github.com/owner/repo/pull/1#discussion_r222) "
            "| @kgn | `new.go:1` | Fixed in `bbbbbbb` |")
        assert rt._carried_over_rows(_published_summary(rt, ROUND_ONE_ROW), fresh) == [ROUND_ONE_ROW]

    def test_a_row_state_still_holds_is_not_duplicated(self, rt):
        fresh = _published_summary(rt, ROUND_ONE_ROW.replace("Fixed in", "Deferred —"))
        assert rt._carried_over_rows(_published_summary(rt, ROUND_ONE_ROW), fresh) == []

    def test_nothing_published_carries_nothing(self, rt):
        assert rt._carried_over_rows("", _published_summary(rt, ROUND_ONE_ROW)) == []


class TestPublishedRowsSurviveTheEdit:
    """State is per-worktree; the comment is the record of rounds it never saw."""

    def _fix(self, **overrides):
        defaults = dict(
            threads=[ThreadOutcome(id="t2", summary="round two work", file="new.go",
                                   line=1, action=ThreadAction.FIXED)],
            commit_status="no_changes", summary_deferred=True,
        )
        defaults.update(overrides)
        return FixSummary(**defaults)

    def _render(self, rt, published):
        state = _make_state(self._fix())
        with _published(published), \
                patch("pr_comments.post_issue_comment", return_value="https://url") as post:
            rt._render_deferred_summary(state, PRReport(), "owner/repo", 1, {})
        return post.call_args[0][2]

    def test_the_earlier_round_survives_finish(self, rt):
        body = self._render(rt, _published_summary(rt, ROUND_ONE_ROW))
        assert "drop the guard" in body
        assert "round two work" in body

    def test_the_carried_row_is_counted_and_explained(self, rt):
        body = self._render(rt, _published_summary(rt, ROUND_ONE_ROW))
        assert "1 carried over" in body
        assert "state file does not cover" in body

    def test_carrying_forward_is_idempotent(self, rt):
        once = self._render(rt, _published_summary(rt, ROUND_ONE_ROW))
        twice = self._render(rt, once)
        assert twice == once

    def test_a_run_that_warns_says_how_many(self, rt):
        with patch.object(rt.log, "warn") as warn:
            self._render(rt, _published_summary(rt, ROUND_ONE_ROW))
        assert "1 row(s)" in warn.call_args[0][0]

    def test_a_failed_lookup_invents_no_rows(self, rt):
        """An unreadable listing must not be read as an empty published comment."""
        import pr_comments
        state = _make_state(self._fix())
        with patch.object(pr_comments, "find_marker_comment",
                          return_value=pr_comments.MarkerComment(found=False)), \
                patch("pr_comments.post_issue_comment", return_value="https://url") as post:
            rt._render_deferred_summary(state, PRReport(), "owner/repo", 1, {})
        assert "carried over" not in post.call_args[0][2]

    def test_the_fix_pass_upsert_carries_too(self, rt):
        """--fix edits the same comment, so it can shrink it the same way."""
        cp = rt.CommitPushResult("bbbbbbb", "pushed", "")
        with _published(_published_summary(rt, ROUND_ONE_ROW)), \
                patch("pr_comments.post_issue_comment", return_value="https://url") as post:
            rt._post_fix_summary(
                [CommentItem(id="t2", summary="round two work", file="new.go", line=1)],
                [], [], cp, "owner/repo", 1, {},
            )
        body = post.call_args[0][2]
        assert "drop the guard" in body
        assert "1 carried over" in body

    def test_the_lookup_is_not_repeated_for_the_write(self, rt):
        import pr_comments
        state = _make_state(self._fix())
        with _published(_published_summary(rt, ROUND_ONE_ROW)) as find, \
                patch("pr_comments.post_issue_comment", return_value="https://url") as post:
            rt._render_deferred_summary(state, PRReport(), "owner/repo", 1, {})
        find.assert_called_once()
        assert post.call_args.kwargs["existing"] == pr_comments.MarkerComment(
            True, 11, _published_summary(rt, ROUND_ONE_ROW))


# ── reposting a summary the PR has moved past ──────────────────────────────


_SUMMARY_POSTED_AT = "2026-01-02T00:00:00Z"
_AFTER_THE_SUMMARY = "2026-01-03T00:00:00Z"
_BEFORE_THE_SUMMARY = "2026-01-01T00:00:00Z"


class TestAnsweredSummariesArePostedAgain:
    """An edit notifies nobody, so a summary spoken over is reposted, not patched."""

    def _marker(self, **overrides):
        import pr_comments
        defaults = dict(found=True, comment_id=11, body="",
                        created_at=_SUMMARY_POSTED_AT)
        defaults.update(overrides)
        return pr_comments.MarkerComment(**defaults)

    def _publish(self, rt, marker, activity_at=""):
        with _lookup_returns(marker), \
                patch("pr_comments.post_issue_comment", return_value="https://url") as post:
            rt._publish_summary("owner/repo", 1, lambda carried_over: "body",
                                activity_at=activity_at)
        return post.call_args

    def test_a_round_that_still_has_the_last_word_edits_in_place(self, rt):
        call = self._publish(rt, self._marker())
        assert call.kwargs["marker"] == rt._SUMMARY_MARKER

    def test_a_later_issue_comment_forces_a_fresh_one(self, rt):
        call = self._publish(rt, self._marker(newest_other_at=_AFTER_THE_SUMMARY))
        assert "marker" not in call.kwargs

    def test_a_later_review_forces_a_fresh_one(self, rt):
        call = self._publish(rt, self._marker(), activity_at=_AFTER_THE_SUMMARY)
        assert "marker" not in call.kwargs

    def test_activity_from_before_the_summary_changes_nothing(self, rt):
        call = self._publish(
            rt, self._marker(newest_other_at=_BEFORE_THE_SUMMARY),
            activity_at=_BEFORE_THE_SUMMARY,
        )
        assert call.kwargs["marker"] == rt._SUMMARY_MARKER

    def test_a_target_with_no_timestamp_is_still_edited(self, rt):
        """Guessing "buried" here would append a duplicate summary every round."""
        call = self._publish(rt, self._marker(created_at=""),
                             activity_at=_AFTER_THE_SUMMARY)
        assert call.kwargs["marker"] == rt._SUMMARY_MARKER

    def test_the_fresh_comment_carries_what_the_old_one_held(self, rt):
        """A repost that lost the earlier rounds would be worse than the edit."""
        import pr_comments
        state = _make_state(FixSummary(
            threads=[ThreadOutcome(id="t2", summary="round two work", file="new.go",
                                   line=1, action=ThreadAction.FIXED)],
            commit_status="no_changes", summary_deferred=True,
        ))
        marker = pr_comments.MarkerComment(
            True, 11, _published_summary(rt, ROUND_ONE_ROW),
            created_at=_SUMMARY_POSTED_AT, newest_other_at=_AFTER_THE_SUMMARY,
        )
        with _lookup_returns(marker), \
                patch("pr_comments.post_issue_comment", return_value="https://url") as post:
            rt._render_deferred_summary(state, PRReport(), "owner/repo", 1, {})
        body = post.call_args[0][2]
        assert "marker" not in post.call_args.kwargs
        assert rt._SUMMARY_MARKER in body
        assert "drop the guard" in body
        assert "round two work" in body


class TestNewestReviewerActivity:
    """What counts as somebody else having spoken since the summary went up."""

    def _report(self, **overrides):
        defaults = dict(my_login="me")
        defaults.update(overrides)
        return PRReport(**defaults)

    def _thread(self, login, created_at):
        return ReportThread(
            id="t1", my_login="me",
            comments=[{"author": {"login": login}, "createdAt": created_at}],
        )

    def test_a_reviewer_reply_counts(self, rt):
        report = self._report(threads=[self._thread("kgn", _AFTER_THE_SUMMARY)])
        assert rt._newest_reviewer_activity(report) == _AFTER_THE_SUMMARY

    def test_our_own_replies_do_not(self, rt):
        """The fix pass replies before it publishes — counting those never settles."""
        report = self._report(threads=[self._thread("me", _AFTER_THE_SUMMARY)])
        assert rt._newest_reviewer_activity(report) == ""

    def test_a_verdict_with_no_body_counts(self, rt):
        report = self._report(verdicts=[
            {"user": "kgn", "state": "APPROVED", "submitted_at": _AFTER_THE_SUMMARY},
        ])
        assert rt._newest_reviewer_activity(report) == _AFTER_THE_SUMMARY

    def test_our_own_verdict_does_not(self, rt):
        report = self._report(verdicts=[
            {"user": "Me", "state": "COMMENTED", "submitted_at": _AFTER_THE_SUMMARY},
        ])
        assert rt._newest_reviewer_activity(report) == ""

    def test_the_newest_of_several_wins(self, rt):
        report = self._report(
            threads=[self._thread("kgn", _BEFORE_THE_SUMMARY)],
            verdicts=[{"user": "kgn", "state": "APPROVED",
                       "submitted_at": _AFTER_THE_SUMMARY}],
        )
        assert rt._newest_reviewer_activity(report) == _AFTER_THE_SUMMARY

    def test_an_unknown_author_counts_as_somebody_else(self, rt):
        """An author this cannot identify is not evidence the comment is ours."""
        report = self._report(threads=[
            ReportThread(id="t1", comments=[{"createdAt": _AFTER_THE_SUMMARY}]),
        ])
        assert rt._newest_reviewer_activity(report) == _AFTER_THE_SUMMARY

    def test_an_unresolved_identity_counts_everything_as_somebody_else(self, rt):
        """An empty `my_login` must not make an equally-empty author match it."""
        report = self._report(
            my_login="",
            threads=[ReportThread(id="t1", comments=[
                {"createdAt": _AFTER_THE_SUMMARY},
            ])],
            verdicts=[{"state": "COMMENTED", "submitted_at": _BEFORE_THE_SUMMARY}],
        )
        assert rt._newest_reviewer_activity(report) == _AFTER_THE_SUMMARY

    def test_a_quiet_pr_reports_nothing(self, rt):
        assert rt._newest_reviewer_activity(self._report()) == ""


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
        return make_ctx(branch="isaac/feat/x", worktree_root=None,
                        head_sha="abc1234")

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


class TestPartitionBatches:
    """One stalled batch must not spend every other batch's retry."""

    def _batch(self, rt, deferred, *, unproductive):
        return rt.FixBatchResult(
            tracking=rt.TrackingResult(deferred=deferred),
            unproductive=Diagnosis(DiagnosisKind.MAX_TURNS) if unproductive else None,
            max_turns=50, max_budget=5.0,
        )

    def test_a_stalled_batch_does_not_block_the_others(self, rt):
        retryable, stalled = rt._partition_batches([
            self._batch(rt, ["a"], unproductive=False),
            self._batch(rt, ["b"], unproductive=True),
        ])
        assert retryable.deferred == ["a"]
        assert stalled.deferred == ["b"]

    def test_all_productive_leaves_nothing_stalled(self, rt):
        retryable, stalled = rt._partition_batches([
            self._batch(rt, ["a"], unproductive=False),
            self._batch(rt, ["b"], unproductive=False),
        ])
        assert retryable.deferred == ["a", "b"]
        assert stalled.deferred == []

    def test_all_stalled_leaves_nothing_retryable(self, rt):
        retryable, stalled = rt._partition_batches([
            self._batch(rt, ["a"], unproductive=True),
        ])
        assert retryable.deferred == []
        assert stalled.deferred == ["a"]


# ── HumanReason ─────────────────────────────────────────────────────────────


class TestHumanReason:
    """The Action cell of a needs-human row reads as prose, never as a token."""

    def _action_cell(self, rt, reason):
        """The rendered Action cell for a needs-human entry with this reason."""
        cp = rt.CommitPushResult(None, "no_changes", "")
        body = rt._build_summary_body(
            [], [CommentItem(summary="s", file="a.py", line=1, reason=reason)],
            [], cp, "owner/repo", 1, {},
        )
        rows = rt._summary_table_rows(body)
        assert len(rows) == 1
        return rt._row_cells(rows[0])[-1]

    @pytest.mark.parametrize("reason", [
        "contested", "conflicting", "question", "complex", "needs_discussion",
    ])
    def test_every_known_reason_renders_as_prose(self, rt, reason):
        assert self._action_cell(rt, reason) == rt.HumanReason(reason).prose

    def test_no_rendered_cell_holds_a_snake_case_token(self, rt):
        for member in rt.HumanReason:
            cell = self._action_cell(rt, member.value)
            assert "_" not in cell
            assert cell[0].isupper()

    def test_an_unknown_reason_falls_back_to_readable_text(self, rt):
        assert self._action_cell(rt, "wat_is_this") == "Needs discussion"

    def test_an_empty_reason_falls_back_to_readable_text(self, rt):
        assert self._action_cell(rt, "") == "Needs discussion"

    def test_the_persisted_tokens_stay_stable(self, rt):
        """State files written before the enum existed must still read back."""
        assert [m.value for m in rt.HumanReason] == [
            "contested", "conflicting", "question", "complex", "needs_discussion",
        ]

    def test_triage_stamps_the_token_not_the_prose(self, rt):
        """`reason` stays machine-readable — the state file and JSON report carry it."""
        entries = [
            CommentItem(id="t1", state=ThreadState.CONTESTED),
            CommentItem(id="t2", classification="conflicting"),
            CommentItem(id="t3", classification="question"),
            CommentItem(id="t4", classification="actionable_suggestion",
                        verification="valid", complexity="high"),
            CommentItem(id="t5", classification="actionable_suggestion",
                        verification="needs_discussion"),
        ]
        result = rt._classify_triage_entries(entries)
        assert [e.reason for e in result.needs_human] == [
            "contested", "conflicting", "question", "complex", "needs_discussion",
        ]

    def test_a_token_read_back_from_state_renders_as_prose(self, rt):
        """The round trip the token stability exists for: state file → Action cell.

        `--finish` rebuilds the needs-human bucket out of persisted
        `ThreadOutcome`s rather than the triage entries, so the prose mapping has
        to hold for that shape too.
        """
        cp = rt.CommitPushResult(None, "no_changes", "")
        outcome = ThreadOutcome(
            id="t1", summary="premise disputed", file="a.py", line=1,
            action=ThreadAction.NEEDS_HUMAN, reason=rt.HumanReason.CONTESTED.value,
        )
        body = rt._build_summary_body([], [outcome], [], cp, "owner/repo", 1, {})
        rows = rt._summary_table_rows(body)
        assert rt._row_cells(rows[0])[-1] == rt.HumanReason.CONTESTED.prose


# ── comment items settle through their source comment ─────────────────────


def _fetches(comments):
    """Stub the PR's issue-comment listing with `comments`."""
    return patch("pr_comments.fetch_issue_comments", return_value=comments)


def _our_reply(anchor, prefix="Applied:", user="me"):
    return {
        "user": user,
        "body": f"{prefix} drop the retry\n\n"
                f"See https://github.com/owner/repo/pull/42{anchor}.",
    }


class TestAnsweredCommentSources:
    """A comment item has no thread, so the evidence is on the comment itself."""

    def _outcomes(self, action=ThreadAction.NEEDS_HUMAN, iid="ic-77-0"):
        return [ThreadOutcome(id=iid, action=action, reason="contested")]

    def test_our_handled_reply_marks_its_source_answered(self, rt):
        with _fetches([_our_reply("#issuecomment-77")]):
            answered = rt._answered_comment_sources(
                self._outcomes(), "owner/repo", 42, "me")
        assert answered == frozenset({"77"})

    def test_the_listing_is_asked_to_keep_our_own_comments(self, rt):
        """The reply being looked for is ours, so the self filter has to be off."""
        with _fetches([_our_reply("#issuecomment-77")]) as fetch:
            rt._answered_comment_sources(self._outcomes(), "owner/repo", 42, "me")
        assert fetch.call_args.kwargs["include_self"] is True

    def test_a_review_body_is_answered_through_its_own_anchor(self, rt):
        with _fetches([_our_reply("#pullrequestreview-88")]):
            answered = rt._answered_comment_sources(
                self._outcomes(iid="rb-88-1"), "owner/repo", 42, "me")
        assert answered == frozenset({"88"})

    def test_the_login_match_ignores_case(self, rt):
        with _fetches([_our_reply("#issuecomment-77", user="Me")]):
            answered = rt._answered_comment_sources(
                self._outcomes(), "owner/repo", 42, "me")
        assert answered == frozenset({"77"})

    def test_the_reviewer_restating_their_point_is_not_an_answer(self, rt):
        with _fetches([_our_reply("#issuecomment-77", user="kgn")]):
            answered = rt._answered_comment_sources(
                self._outcomes(), "owner/repo", 42, "me")
        assert answered == frozenset()

    def test_a_deferred_reply_says_the_opposite(self, rt):
        """Same carve-out the thread evidence makes — it is not a settlement."""
        with _fetches([_our_reply("#issuecomment-77", prefix="Deferred:")]):
            answered = rt._answered_comment_sources(
                self._outcomes(), "owner/repo", 42, "me")
        assert answered == frozenset()

    def test_a_reply_that_cites_nothing_settles_nothing(self, rt):
        with _fetches([{"user": "me", "body": "Applied: drop the retry"}]):
            answered = rt._answered_comment_sources(
                self._outcomes(), "owner/repo", 42, "me")
        assert answered == frozenset()

    def test_a_non_comment_item_is_not_worth_a_listing(self, rt):
        """`t1` is open, but a thread-shaped id has no source comment to read."""
        with _fetches([]) as fetch:
            answered = rt._answered_comment_sources(
                [ThreadOutcome(id="t1", action=ThreadAction.DEFERRED)],
                "owner/repo", 42, "me")
        assert answered == frozenset()
        fetch.assert_not_called()

    def test_a_settled_item_is_not_worth_a_listing_either(self, rt):
        with _fetches([]) as fetch:
            rt._answered_comment_sources(
                self._outcomes(action=ThreadAction.FIXED), "owner/repo", 42, "me")
        fetch.assert_not_called()

    def test_without_our_login_no_reply_can_be_called_ours(self, rt):
        with _fetches([_our_reply("#issuecomment-77")]) as fetch:
            answered = rt._answered_comment_sources(
                self._outcomes(), "owner/repo", 42, "")
        assert answered == frozenset()
        fetch.assert_not_called()


class TestCommentItemsSettleThroughTheirSource:
    """The outcome the fix pass handed to the operator has to be clearable."""

    def _state(self, action=ThreadAction.NEEDS_HUMAN, iid="ic-77-0"):
        return _make_state(FixSummary(head_sha="aaaaaaa", threads=[
            ThreadOutcome(id=iid, file="a.go", line=7, reviewer="kgn",
                          summary="drop the retry", action=action,
                          reason="contested"),
        ]))

    def test_an_answered_item_reconciles_to_fixed(self, rt):
        state = self._state()
        assert rt._reconcile_fix_snapshot(state, {}, frozenset({"77"})) == 1
        assert state.fix.threads[0].action == ThreadAction.FIXED
        assert "reconciled" in state.fix.threads[0].reason

    def test_a_deferred_item_reconciles_the_same_way(self, rt):
        state = self._state(action=ThreadAction.DEFERRED)
        assert rt._reconcile_fix_snapshot(state, {}, frozenset({"77"})) == 1
        assert state.fix.threads[0].action == ThreadAction.FIXED

    def test_a_review_body_item_reconciles_through_its_review(self, rt):
        state = self._state(iid="rb-88-1")
        assert rt._reconcile_fix_snapshot(state, {}, frozenset({"88"})) == 1
        assert state.fix.threads[0].action == ThreadAction.FIXED

    def test_an_answer_to_another_comment_is_not_this_items_answer(self, rt):
        state = self._state()
        assert rt._reconcile_fix_snapshot(state, {}, frozenset({"99"})) == 0
        assert state.fix.threads[0].action == ThreadAction.NEEDS_HUMAN

    def test_an_unanswered_item_still_holds_the_summary_back(self, rt):
        state = self._state()
        assert rt._reconcile_fix_snapshot(state, {}, frozenset()) == 0
        needs_human = [t for t in state.fix.threads
                       if t.action == ThreadAction.NEEDS_HUMAN]
        assert needs_human
        assert rt._summary_still_owed(
            [], needs_human, [], [], CommitStatus.PUSHED, False) is True

    def test_an_item_restating_a_settled_thread_settles_with_it(self, rt):
        """The duplicate is one finding; one of its two copies being closed closes it."""
        state = self._state()
        threads = {"t1": ReportThread(
            id="t1", file="a.go", line=7, reviewer="kgn",
            state=ThreadState.RESOLVED, is_resolved=True, comments=[{"body": "x"}],
        )}
        assert rt._reconcile_fix_snapshot(state, threads) == 1
        assert state.fix.threads[0].action == ThreadAction.FIXED

    def test_an_item_restating_an_open_thread_stays_open(self, rt):
        state = self._state()
        threads = {"t1": ReportThread(
            id="t1", file="a.go", line=7, reviewer="kgn",
            state=ThreadState.NEW, is_resolved=False,
            comments=[{"body": "why not the other way?"}],
        )}
        assert rt._reconcile_fix_snapshot(state, threads) == 0
        assert state.fix.threads[0].action == ThreadAction.NEEDS_HUMAN

    def test_a_settled_thread_elsewhere_settles_nothing_here(self, rt):
        state = self._state()
        threads = {"t1": ReportThread(
            id="t1", file="b.go", line=3, reviewer="kgn",
            state=ThreadState.RESOLVED, is_resolved=True, comments=[{"body": "x"}],
        )}
        assert rt._reconcile_fix_snapshot(state, threads) == 0
        assert state.fix.threads[0].action == ThreadAction.NEEDS_HUMAN


class TestFinishReconcilesCommentItems:
    """The wiring: --finish is what asks GitHub about the source comments."""

    def _save(self, worktree):
        pr_state.save_state(worktree / "target", PRState(
            identity=PRIdentity(repo="owner/repo", branch="b", pr_number=42,
                                head_sha="aaaaaaa", worktree_root=str(worktree)),
            fix=FixSummary(head_sha="aaaaaaa", threads=[
                ThreadOutcome(id="ic-77-0", file="a.go", line=7, reviewer="kgn",
                              summary="drop the retry",
                              action=ThreadAction.NEEDS_HUMAN, reason="contested"),
            ]),
        ))
        return make_ctx(branch="b", worktree_root=worktree, head_sha="aaaaaaa",
                        target_dir=worktree / "target")

    def _run(self, rt, ctx, comments):
        with patch.object(rt, "_get_head_sha", return_value="aaaaaaa"), \
                _fetches(comments), \
                patch.object(rt, "_render_deferred_summary"):
            rt._finish_deferred_work(ctx, PRReport(my_login="me"))

    def test_the_answered_item_is_persisted_as_fixed(self, rt, worktree):
        ctx = self._save(worktree)
        self._run(rt, ctx, [_our_reply("#issuecomment-77")])
        saved = pr_state.load_state(worktree / "target")
        assert saved.fix.threads[0].action == ThreadAction.FIXED

    def test_an_unanswered_item_survives_the_round(self, rt, worktree):
        ctx = self._save(worktree)
        self._run(rt, ctx, [_our_reply("#issuecomment-99")])
        saved = pr_state.load_state(worktree / "target")
        assert saved.fix.threads[0].action == ThreadAction.NEEDS_HUMAN


class TestDuplicateFindingRendersOnce:
    """One review point that arrived twice is still one row in the table."""

    def _thread(self, **kw):
        defaults = {"id": "t1", "file": "a.go", "line": 7, "reviewer": "kgn",
                    "summary": "drop the retry", "action": ThreadAction.FIXED}
        defaults.update(kw)
        return ThreadOutcome(**defaults)

    def _item(self, **kw):
        defaults = {"id": "ic-77-0", "file": "a.go", "line": 7, "reviewer": "kgn",
                    "summary": "also drop the retry",
                    "action": ThreadAction.NEEDS_HUMAN, "reason": "contested"}
        defaults.update(kw)
        return ThreadOutcome(**defaults)

    def _body(self, rt, fixed, needs_human, threads_by_id=None):
        cp = rt.CommitPushResult("abc1234", "pushed", "")
        return rt._build_summary_body(
            fixed, needs_human, [], cp, "owner/repo", 42,
            threads_by_id if threads_by_id is not None else self._threads(),
        )

    def _threads(self):
        return {"t1": ReportThread(id="t1", file="a.go", line=7, reviewer="kgn",
                                   comments=[{"databaseId": 5}])}

    def test_the_item_folds_into_the_thread_it_restates(self, rt):
        body = self._body(rt, [self._thread()], [self._item()])
        assert len(rt._summary_table_rows(body)) == 1
        assert "#issuecomment-77" not in body
        assert "#discussion_r5" in body

    def test_the_counts_line_never_promises_a_row_it_folded(self, rt):
        body = self._body(rt, [self._thread()], [self._item()])
        assert "need discussion" not in body
        assert "1 fixed" in body

    def test_another_line_is_another_finding(self, rt):
        body = self._body(rt, [self._thread()], [self._item(line=9)])
        assert len(rt._summary_table_rows(body)) == 2

    def test_an_item_naming_no_line_is_never_folded(self, rt):
        """Without a line there is nothing precise enough to call it the same point."""
        body = self._body(rt, [self._thread()], [self._item(line=0)])
        assert len(rt._summary_table_rows(body)) == 2

    def test_another_reviewers_point_is_another_finding(self, rt):
        body = self._body(rt, [self._thread()], [self._item(reviewer="amp")])
        assert len(rt._summary_table_rows(body)) == 2

    def test_two_real_threads_are_never_folded_together(self, rt):
        threads = self._threads()
        threads["t2"] = ReportThread(id="t2", file="a.go", line=7, reviewer="kgn",
                                     comments=[{"databaseId": 6}])
        body = self._body(
            rt, [self._thread()],
            [self._thread(id="t2", action=ThreadAction.NEEDS_HUMAN,
                          summary="and rename it")],
            threads,
        )
        assert len(rt._summary_table_rows(body)) == 2

    def test_an_item_with_no_thread_to_fold_into_still_renders(self, rt):
        body = self._body(rt, [], [self._item()], {})
        rows = rt._summary_table_rows(body)
        assert len(rows) == 1
        assert "#issuecomment-77" in body


class TestFoldedRowsAreNotCarriedBack:
    """A round that folded a duplicate must not read its own removal as a loss."""

    THREAD_ROW = ("| [drop the retry](https://github.com/o/r/pull/1#discussion_r5) "
                  "| @kgn | [`a.go:7`](https://github.com/o/r/blob/abc/a.go#L7) | Fixed |")
    ITEM_ROW = ("| [also drop the retry](https://github.com/o/r/pull/1"
                "#issuecomment-77) | @kgn | `a.go:7` | contested |")

    def test_the_published_duplicate_is_accounted_for(self, rt):
        published = f"{self.THREAD_ROW}\n{self.ITEM_ROW}"
        assert rt._carried_over_rows(published, self.THREAD_ROW) == []

    def test_an_item_row_elsewhere_is_still_carried(self, rt):
        elsewhere = self.ITEM_ROW.replace("a.go:7", "b.go:3")
        published = f"{self.THREAD_ROW}\n{elsewhere}"
        assert rt._carried_over_rows(published, self.THREAD_ROW) == [elsewhere]

    def test_a_published_thread_row_is_carried_as_before(self, rt):
        """Only comment items fold; a thread row this render lost is still a loss."""
        other = self.THREAD_ROW.replace("discussion_r5", "discussion_r9")
        published = f"{self.THREAD_ROW}\n{other}"
        assert rt._carried_over_rows(published, self.THREAD_ROW) == [other]


def _filed(issue_id: str, url: str) -> IssueResult:
    """What create_issue answers once a tracker accepted the write."""
    return IssueResult(IssueDelivery.FILED, CreatedIssue(id=issue_id, url=url))


class TestDeferredIssueProvider:
    """#795: a GitHub repo was skipped for a Linear-shaped reason.

    ``ensure_issue_provider`` only runs once publishing is enabled — a draft
    run files nothing, so asking which tracker to use has no consequence.
    Every case here that expects the resolved-provider path to run therefore
    opens the gate with ``publishing_on``; the one exception is the gate test
    itself, which relies on the default closed gate.
    """

    def _deferred(self):
        return [CommentItem(id="t1", summary="fix regex", file="parsers.py", line=10)]

    def _create(self, rt, **overrides):
        kwargs = dict(
            deferred=self._deferred(), repo="owner/repo", pr_number=1,
            threads_by_id={}, ctx=make_ctx(), existing_issue_id="", trail=None,
        )
        kwargs.update(overrides)
        return rt._create_or_update_deferred_issue(**kwargs)

    def test_stops_when_no_tracker_is_configured(self, rt, publishing_on):
        """An unset provider must report, not quietly file nothing."""
        import review_issue
        with patch.object(
            review_issue, "ensure_issue_provider",
            return_value=review_issue.IssueProviderInfo(),
        ), patch.object(review_issue, "create_issue") as created:
            result = self._create(rt)
        assert result.issue.id == ""
        assert result.owed is True
        created.assert_not_called()

    def test_github_needs_no_team_key(self, rt, publishing_on):
        """gh issue create is addressed by repo; a branch with no ABC-123 is fine."""
        import review_issue
        info = review_issue.IssueProviderInfo(name="github", options={})
        with patch.object(review_issue, "ensure_issue_provider", return_value=info), \
             patch.object(
                 review_issue, "create_issue",
                 return_value=_filed("#42", "https://gh/42"),
             ) as created:
            result = self._create(rt)
        assert result.issue.id == "#42"
        created.assert_called_once()

    def test_linear_prefers_the_configured_team(self, rt, publishing_on):
        """issue_tracker.team is published config; it should be read."""
        import review_issue
        info = review_issue.IssueProviderInfo(name="linear", options={"team": "ENG"})
        with patch.object(review_issue, "ensure_issue_provider", return_value=info), \
             patch.object(
                 review_issue, "create_issue",
                 return_value=_filed("ENG-9", "https://linear/ENG-9"),
             ) as created:
            self._create(rt)
        created.assert_called_once_with(
            "linear", "ENG", ANY, ANY, parent_id=None, repo="owner/repo", opts={"team": "ENG"},
        )

    def test_linear_falls_back_to_the_branch_derived_team(self, rt, publishing_on):
        """With no configured team, the branch-derived id still supplies one."""
        import review_issue
        info = review_issue.IssueProviderInfo(name="linear", options={})
        with patch.object(review_issue, "ensure_issue_provider", return_value=info), \
             patch.object(
                 review_issue, "create_issue",
                 return_value=_filed("ENG-9", "https://linear/ENG-9"),
             ) as created:
            self._create(rt, ctx=make_ctx(branch="isaac/ENG-1/x"))
        created.assert_called_once_with(
            "linear", "ENG", ANY, ANY, parent_id="ENG-1", repo="owner/repo", opts={},
        )

    def test_linear_still_skips_with_no_team_anywhere(self, rt, publishing_on):
        """Skipped, but owed: nothing was filed and the deferrals have no home."""
        import review_issue
        info = review_issue.IssueProviderInfo(name="linear", options={})
        with patch.object(review_issue, "ensure_issue_provider", return_value=info), \
             patch.object(review_issue, "create_issue") as created:
            result = self._create(rt)
        assert result.issue.id == ""
        assert result.owed is True
        created.assert_not_called()

    def test_a_draft_run_does_not_ask_which_tracker(self, rt):
        """create_issue files nothing while publishing is off, so asking is pointless."""
        import publishing
        import review_issue
        with patch.object(publishing, "enabled", return_value=False), \
             patch.object(review_issue, "ensure_issue_provider") as asked, \
             patch.object(
                 review_issue, "load_issue_provider",
                 return_value=review_issue.IssueProviderInfo(),
             ) as loaded:
            result = self._create(rt)
        asked.assert_not_called()
        loaded.assert_called_once_with("/wt")
        assert result.issue == CreatedIssue()
        assert result.owed is False

    def test_unresolved_provider_reaches_the_trail_as_an_error(self, rt, publishing_on):
        """Deleting the trail.error call would leave the suite green without this."""
        import review_issue
        trail = MagicMock()
        with patch.object(
            review_issue, "ensure_issue_provider",
            return_value=review_issue.IssueProviderInfo(),
        ), patch.object(review_issue, "create_issue"):
            self._create(rt, trail=trail)
        trail.error.assert_called_once_with("deferred_issue", "no issue tracker configured")
        trail.info.assert_not_called()

    def test_unresolved_provider_in_draft_mode_reaches_the_trail_as_info(self, rt):
        """The unresolved-path event fires here too, but as info — and only here.

        A resolved provider whose creation genuinely fails still reaches
        ``trail.error("deferred_issue", "creation failed")`` in
        ``_create_deferred_issue`` whether or not the gate is open — this
        asserts the unresolved path never does while the gate is shut.
        """
        import review_issue
        trail = MagicMock()
        with patch.object(
            review_issue, "load_issue_provider",
            return_value=review_issue.IssueProviderInfo(),
        ), patch.object(review_issue, "create_issue"):
            self._create(rt, trail=trail)
        trail.info.assert_called_once_with(
            "deferred_issue", "skipped — no issue tracker configured",
        )
        trail.error.assert_not_called()


class TestDeferredIssueDraftIsNotAFailure:
    """#804: a draft run and a refused tracker both used to arrive as ``None``.

    The gate declining a write is the gate working, so it must not reach the
    closeout `pr status` reads — while a creation that genuinely failed must,
    gate open or shut.
    """

    def _create(self, rt, delivery, trail):
        import review_issue
        info = review_issue.IssueProviderInfo(name="github", options={})
        with patch.object(review_issue, "load_issue_provider", return_value=info), \
             patch.object(
                 review_issue, "create_issue",
                 return_value=IssueResult(delivery),
             ):
            return rt._create_or_update_deferred_issue(
                deferred=[CommentItem(id="t1", summary="fix regex")],
                repo="owner/repo", pr_number=1, threads_by_id={},
                ctx=make_ctx(), existing_issue_id="", trail=trail,
            )

    def test_a_declined_write_is_reported_as_deferral(self, rt, capsys):
        trail = MagicMock()
        self._create(rt, IssueDelivery.SKIPPED, trail)
        trail.info.assert_called_once_with("deferred_issue", "skipped — publishing off")
        trail.error.assert_not_called()
        assert "Failed to create" not in capsys.readouterr().err

    def test_a_failed_creation_is_an_error_even_while_the_gate_is_shut(self, rt, capsys):
        """Reading the gate again instead of the return value would lose this."""
        trail = MagicMock()
        self._create(rt, IssueDelivery.UNDELIVERED, trail)
        trail.error.assert_called_once_with("deferred_issue", "creation failed")
        trail.info.assert_not_called()
        assert "Failed to create deferred tracking issue" in capsys.readouterr().err


class TestUndeliveredDeferredIssueReachesTheState:
    """#805: threads were filed against a tracking issue that never existed.

    The only record was a trail event, and nobody reads the trail to decide
    whether a PR is safe to merge — so `pr status` said ready while the
    deferred comments had no home.
    """

    def _finalize(self, rt, worktree, provider, create=None):
        import review_issue
        state = PRState(
            identity=PRIdentity(repo="owner/repo", branch="b", pr_number=42,
                                head_sha="abc1234", worktree_root=str(worktree)),
            fix=FixSummary(threads=[
                ThreadOutcome(id="t1", file="a.go", line=7, reviewer="kgn",
                              summary="rename the guard", action=ThreadAction.DEFERRED),
            ]),
        )
        ctx = make_ctx(branch="b", worktree_root=worktree, head_sha="abc1234",
                       target_dir=worktree / "target")
        creation = patch.object(review_issue, "create_issue", return_value=create) \
            if create is not None else contextlib.nullcontext()
        with patch.object(review_issue, "ensure_issue_provider", return_value=provider), \
                patch.object(review_issue, "load_issue_provider", return_value=provider), \
                patch.object(rt, "_post_deferred_replies"), creation:
            rt._finalize_deferred(state, ctx, {}, track={"t1"})
        return state.fix

    def _provider(self, name):
        import review_issue
        return review_issue.IssueProviderInfo(name=name, options={})

    def test_a_creation_failure_is_recorded(self, rt, worktree, publishing_on):
        fix = self._finalize(
            rt, worktree, self._provider("github"),
            create=IssueResult(IssueDelivery.UNDELIVERED),
        )
        assert fix.deferred_issue_pending is True

    def test_a_provider_that_cannot_create_issues_is_recorded(
        self, rt, worktree, publishing_on,
    ):
        fix = self._finalize(rt, worktree, self._provider("jira"))
        assert fix.deferred_issue_pending is True

    def test_no_tracker_configured_is_recorded(self, rt, worktree, publishing_on):
        fix = self._finalize(rt, worktree, self._provider(""))
        assert fix.deferred_issue_pending is True

    def test_a_tracker_with_no_team_key_is_recorded(self, rt, worktree, publishing_on):
        """A branch with no ABC-123 and no configured team files nothing either."""
        import pr_comments
        fix = self._finalize(rt, worktree, self._provider("linear"))
        assert fix.deferred_issue_pending is True
        assert pr_comments.closeout_debt(fix).owed is True

    def test_a_draft_run_with_no_team_key_owes_nothing(self, rt, worktree):
        """Nothing was attempted, so the missing key cost the run nothing."""
        fix = self._finalize(rt, worktree, self._provider("linear"))
        assert fix.deferred_issue_pending is False

    def test_a_filed_issue_owes_nothing(self, rt, worktree, publishing_on):
        fix = self._finalize(
            rt, worktree, self._provider("github"), create=_filed("#42", "https://gh/42"),
        )
        assert fix.deferred_issue_pending is False
        assert fix.deferred_issue_id == "#42"

    def test_a_draft_run_owes_nothing(self, rt, worktree):
        """The gate declining the write is not a tracking issue gone missing."""
        fix = self._finalize(rt, worktree, self._provider("github"))
        assert fix.deferred_issue_pending is False
