"""Tests for review-threads: JSON extraction, thread classification, and prompt formatting."""

import atexit
import contextlib
import dataclasses
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from conftest import (
    assert_no_worktree_exit, git_in, git_out, make_ctx, run_checked,
    supersession_context, supersession_evidence, supersession_verdict,
)
import agent_retry
import fix_engine
import fix_tracking
import pr_state
import proc
from proc import CmdResult
from pr_comments_state import ThreadState
from pr_comments_fix import FixSummary
from pr_domains import SupersessionKind
from pr_fix import CommitStatus, FixOutcome, FixRecord, ItemOutcome
from pr_state import PRIdentity, PRState
from pr_thread_models import (
    CommentItem, PRReport, ReportThread, TrackingResult, TriageResult,
    TriageStats, triage_result_from_dict,
)
from review_document import SECTION_PRIOR_FINDINGS
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


def _fix(
    items=(), *, commit_sha="", commit_status=None, head_sha="", **kwargs,
) -> FixSummary:
    """A comment fix pass carrying these outcomes, as the domain stores them.

    The record's envelope fields stay keywords here rather than a nested
    `FixRecord` literal — a test that names a commit is making a point about the
    commit, not about which of the two objects holds it. `commit_status` takes
    the string a state file holds and coerces it, so a typo fails here instead
    of reaching a renderer as an unrecognised status.
    """
    return FixSummary(
        fix=FixRecord(
            items=list(items), commit_sha=commit_sha,
            commit_status=CommitStatus(commit_status) if commit_status else None,
            head_sha=head_sha,
        ),
        **kwargs,
    )


def _lookup_returns(*comments):
    """Patch the marker lookup to report `comments`, oldest first.

    Both the autouse default and the per-test override go through this one
    patch, so a test entering `_published(...)` inside the fixture's patch is
    plain `patch.object` nesting: the inner patch wins for its block and
    restores the fixture's on exit.

    A `MarkerComment` with no id is the "PR has no summary yet" stand-in rather
    than a comment, so it contributes the lookup's own outcome and no history.
    """
    import pr_comments
    newest = comments[-1]
    history = pr_comments.MarkerHistory(
        found=newest.found,
        comments=tuple(c for c in comments if c.comment_id),
        newest_other_at=newest.newest_other_at,
    )
    return patch.object(pr_comments, "find_marker_comments", return_value=history)


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
    return _lookup_returns(pr_comments.MarkerComment(
        True, 11, body, url="https://github.com/owner/repo/pull/1#issuecomment-11"))


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
# What the remote answers with when it did not keep the push: some other commit
# than the one the pass just made. Any SHA but the pushed one would do — this is
# named so the assertion reads as "the remote moved on without it".
_LOST_SHA = "0000000"


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
    """Create a CompletedProcess with the given results.

    For the gh call sites, which run through `proc` rather than a stub of the
    client itself. Git goes through `_git_ran`.
    """
    import subprocess
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


def _git_ran(returncode, stdout="", stderr=""):
    """What a stubbed `git_client.run` hands back.

    `out`, `ok`, `lines` and `head_sha` are all `run` underneath, so patching
    the one call covers every read the script makes.
    """
    return proc.CmdResult(returncode, stdout, stderr)


def _answering_the_owner(mock_run, sha="abc1234"):
    """Wrap a `git_client.run` stub so the push owner's verification is answered.

    Every push here goes through `push.push`, which finishes by asking the
    remote what it holds. A stub's catch-all answers that with the empty string
    — a branch the remote does not have — so every push would read as lost
    whatever the test was setting up, and the owner would retry it. Passing the
    SHA the stub commits makes the push land; passing a different one is how a
    test asks for the lost path.

    The answer echoes back the refname the owner asked for. It compares the
    refname it reads against the one it queried — a fixed one here would be
    discarded as somebody else's branch, and every push would read as lost for
    a reason that has nothing to do with what the test set up.
    """
    def run(*cmd, **kwargs):
        if cmd[:1] == ("ls-remote",):
            return _git_ran(0, stdout=f"{sha}\t{cmd[-1]}\n" if sha else "")
        return mock_run(*cmd, **kwargs)
    return run


def _tick_every_fix(wt_path):
    """An `invoke_fix` stub that answers every entry `fixed`.

    `fix_engine` rewrites the checklist immediately before each invocation, so
    an agent that answers anything has to answer it from inside the call —
    a file ticked beforehand is overwritten before the agent ever sees it.
    """
    tracking = Path(wt_path) / "ignore" / "pr-comments" / "fix-tracking.md"

    def invoke(_invocation):
        tracking.write_text(tracking.read_text().replace("- [ ] fixed", "- [x] fixed"))
        return 0
    return invoke


def _fix_adapter(rt, wt_path, **overrides):
    """A CommentFixAdapter over an otherwise empty pass.

    Every bucket defaults to empty so a test names only the one it is about.
    """
    report = overrides.pop("report", None) or PRReport(repo="owner/repo", pr_number=1)
    ctx = overrides.pop("ctx", None) or make_ctx(
        repo="owner/repo", pr_number=1, worktree_root=wt_path, target_dir=wt_path,
    )
    kwargs = dict(
        fixable=[], fixable_items=[], needs_human=[], dismissed=[],
        already_addressed=[], resolved=[], triage_replies=0,
        has_unaccounted=False, has_items=False,
    )
    kwargs.update(overrides)
    return rt.CommentFixAdapter(report, ctx, wt_path, **kwargs)


class TestCommentFixLanding:
    """The pass's boundary onto the landing owner.

    What the commit, the push, the regeneration retry and the recovery each do
    is the owner's, and `land_test.py` holds it against a real repo; asking for
    them is `fix_engine`'s, and `fix_engine_test.py` holds that. What is left to
    this command is the spec it hands over and the record it keeps of the answer.
    """

    @staticmethod
    def _spec(rt, tmp_path, *, fixed=1, deferred=0):
        outcomes = (
            [ItemOutcome(id=f"f{n}", outcome=FixOutcome.FIXED) for n in range(fixed)]
            + [ItemOutcome(id=f"d{n}", outcome=FixOutcome.DEFERRED)
               for n in range(deferred)]
        )
        return _fix_adapter(rt, tmp_path).landing(outcomes)

    @staticmethod
    def _recorded(rt, landed, *, short="abc1234"):
        with patch.object(rt.git_client, "run",
                          return_value=_git_ran(0, stdout=f"{short}\n")):
            return rt._pass_commit(Path("/fake"), landed)

    def test_the_owner_is_asked_for_the_retry_and_the_recovery(self, rt, tmp_path):
        """Both are options, and a pass that did not ask would get neither."""
        spec = self._spec(rt, tmp_path)

        assert spec.recover is True
        assert spec.regen

    def test_the_counts_ride_in_the_commit_message(self, rt, tmp_path):
        spec = self._spec(rt, tmp_path, fixed=2, deferred=3)

        subject, _, body = spec.message.partition("\n\n")
        assert subject == "fix: address review comments"
        assert body == "2 fixed, 3 deferred"

    def test_a_pass_that_fixed_nothing_says_only_what_it_did(self, rt, tmp_path):
        spec = self._spec(rt, tmp_path, fixed=0, deferred=4)

        assert spec.message == "fix: address review comments"

    def test_the_sha_is_recorded_at_the_width_the_state_file_uses(self, rt):
        """A commit recorded twice at two widths reads as two commits."""
        landed = rt.land.LandResult(rt.CommitStatus.PUSHED, sha="abc1234def56789")
        result = self._recorded(rt, landed)

        assert result.sha == "abc1234"
        assert result.status == "pushed"

    def test_a_landing_with_no_commit_records_no_sha(self, rt):
        result = self._recorded(rt, rt.land.LandResult(rt.CommitStatus.NO_CHANGES))

        assert result.sha is None
        assert result.status == "no_changes"

    def test_what_went_wrong_is_carried_through(self, rt):
        landed = rt.land.LandResult(
            rt.CommitStatus.PUSH_FAILED, sha="abc1234def", error="rejected",
        )
        result = self._recorded(rt, landed)

        assert result.status == "push_failed"
        assert result.sha == "abc1234"
        assert "rejected" in result.error


# ── _get_head_sha ────────────────────────────────────────────────────────────


class TestGetHeadSha:
    def test_returns_short_sha(self, rt):
        with patch.object(rt.git_client, "run",
                          return_value=_git_ran(0, stdout="abc1234\n")):
            result = rt._get_head_sha(Path("/fake"))
        assert result == "abc1234"


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

    def test_push_lost_says_the_remote_does_not_have_it(self, rt):
        """The operator saw a clean push, so "push failed" would read as wrong."""
        cp = rt.CommitPushResult("abc1234", "push_lost", "")
        text = rt._fixed_status_text(cp, "owner/repo")
        assert "committed locally" in text
        assert "remote does not have it" in text
        assert "abc1234" not in text

    def test_push_unverified_does_not_claim_the_remote_answered(self, rt):
        """An unreachable remote said neither yes nor no — say only that."""
        cp = rt.CommitPushResult("abc1234", "push_unverified", "")
        text = rt._fixed_status_text(cp, "owner/repo")
        assert "could not reach the remote" in text
        assert "does not have it" not in text
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


# ── _attribute_commit ───────────────────────────────────────────────────────


class TestAttributeCommit:
    """One resolver answers "which commit carries this thread?" for every surface.

    Each renderer used to derive that itself, and an empty `commit_sha` meant
    something different to each of them — so a fix that made one honest
    inverted another. The claim is the discriminator that lets them disagree
    about *rendering* without disagreeing about the facts.
    """

    @staticmethod
    def _entry(**kw):
        return CommentItem(id="t1", summary="fix it", file="a.py", line=1, **kw)

    def test_a_recorded_commit_outranks_the_running_pass(self, rt):
        """An earlier round's commit is the one that carries the change."""
        got = rt._attribute_commit(
            self._entry(commit_sha=_ROUND_1_SHA),
            rt.CommitPushResult(_PASS_SHA, "pushed", ""),
        )
        assert got.claim is rt.CommitClaim.RECORDED
        assert got.sha == _ROUND_1_SHA

    def test_an_entry_the_pass_landed_rides_the_pass_commit(self, rt):
        got = rt._attribute_commit(
            self._entry(commit_sha=_PASS_SHA),
            rt.CommitPushResult(_PASS_SHA, "pushed", ""),
        )
        assert got.claim is rt.CommitClaim.PASS
        assert got.sha == _PASS_SHA

    def test_an_unpublished_pass_commit_is_not_citable(self, rt):
        """A SHA the remote does not have would 404 for whoever clicks it."""
        got = rt._attribute_commit(
            self._entry(commit_sha=_PASS_SHA),
            rt.CommitPushResult(_PASS_SHA, "push_failed", "rejected"),
        )
        assert got.claim is rt.CommitClaim.PASS
        assert got.cited is False

    def test_an_entry_the_pass_never_recorded_claims_nothing(self, rt):
        """The pass committed and this entry is not in that commit."""
        got = rt._attribute_commit(
            self._entry(), rt.CommitPushResult(_PASS_SHA, "pushed", ""),
        )
        assert got.claim is rt.CommitClaim.UNRECORDED
        assert got.cited is False

    def test_a_reconciled_pass_lends_the_commit_it_recovered(self, rt):
        """One published commit landed outside the pass — the operator's."""
        got = rt._attribute_commit(
            self._entry(),
            rt.CommitPushResult(_PASS_SHA, "pushed", "",
                                claim=rt.CommitClaim.RECONCILED),
        )
        assert got.claim is rt.CommitClaim.RECONCILED
        assert got.sha == _PASS_SHA

    def test_an_undetermined_pass_lends_nothing(self, rt):
        """Several commits landed outside the pass; none of them answers for a row."""
        got = rt._attribute_commit(
            self._entry(),
            rt.CommitPushResult(_PASS_SHA, "pushed", "",
                                claim=rt.CommitClaim.UNDETERMINED),
        )
        assert got.claim is rt.CommitClaim.UNDETERMINED
        assert got.cited is False

    def test_a_pass_with_no_commit_leaves_the_row_to_the_pass(self, rt):
        """Nothing was committed by anyone, so there is nothing row-specific to say."""
        got = rt._attribute_commit(
            self._entry(), rt.CommitPushResult(None, "no_changes", ""),
        )
        assert got.claim is rt.CommitClaim.PASS
        assert got.cited is False

    def test_the_pass_stamps_the_entries_it_landed(self, rt):
        """The one write of thread → commit; every reader goes through the resolver."""
        fresh, earlier = self._entry(), self._entry(commit_sha=_ROUND_1_SHA)
        rt._stamp_pass_commit([fresh, earlier], _PASS_SHA)
        assert fresh.commit_sha == _PASS_SHA
        assert earlier.commit_sha == _ROUND_1_SHA


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
            [self._fixed_entry(commit_sha="abc1234")], [], [], cp, "owner/repo", 1, {},
        )
        assert "/commit/abc1234" in body
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
            [self._fixed_entry(commit_sha="abc1234")], [], [], cp, "owner/repo", 1, {},
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

    def test_a_replayed_comment_item_keeps_its_permalink(self, rt):
        """An entry rebuilt from a recorded outcome still parses its source id.

        The replay path `--finish` takes: what state holds is an `ItemOutcome`,
        and every renderer downstream reads a `CommentItem`, so the synthetic id
        has to survive `from_outcome` intact for the permalink to resolve.
        """
        entry = CommentItem.from_outcome(ItemOutcome(
            id="ic-99999-0", summary="fix typo", file="readme.md", line=1,
            outcome=FixOutcome.FIXED,
        ))
        cp = rt.CommitPushResult("abc1234", "pushed", "")
        body = rt._build_summary_body(
            [entry], [], [], cp, "owner/repo", 42, {},
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


# A worktree root that exists but holds no repo. Existing is the part that
# matters: the git client passes the root as `cwd`, so a made-up path fails in
# Python before git is reached, where `git -C` used to just exit non-zero. Not
# being a repo is what these tests want — every git read degrades to its
# default, which is the state each of them was written against.
_STATE_WORKTREE = tempfile.mkdtemp(prefix="review-threads-state-")
atexit.register(shutil.rmtree, _STATE_WORKTREE, ignore_errors=True)


def _make_state(fix=None):
    """Build a minimal PRState with the given FixSummary."""
    return PRState(
        identity=PRIdentity(
            repo="owner/repo", branch="feat", pr_number=1,
            head_sha="abc1234", worktree_root=_STATE_WORKTREE,
        ),
        fix=fix or _fix(),
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
        state = _make_state(_fix(summary_deferred=False))
        report = PRReport()
        with patch("pr_comments.post_issue_comment") as mock_post:
            rt._render_deferred_summary(state, report, "owner/repo", 1, {})
        mock_post.assert_not_called()

    def test_renders_with_issue_link(self, rt):
        fix = _fix(
            items=[
                ItemOutcome(id="t1", summary="fix regex", file="parsers.py", line=10, outcome=FixOutcome.DEFERRED),
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
        fix = _fix(
            items=[
                ItemOutcome(id="t1", summary="fix regex", file="parsers.py", line=10, outcome=FixOutcome.DEFERRED),
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
        fix = _fix(
            items=[
                ItemOutcome(id="t1", summary="auto fix", file="a.py", line=1, outcome=FixOutcome.FIXED),
                ItemOutcome(id="t2", summary="premise disputed", file="b.py", line=2,
                              outcome=FixOutcome.NEEDS_HUMAN, reason="contested"),
                ItemOutcome(id="t3", summary="complex", file="c.py", line=3, outcome=FixOutcome.DEFERRED),
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
            fix=_fix(head_sha="aaaaaaa", summary_deferred=True,
                           commit_status="no_changes", items=[
                               ItemOutcome(id="t1", summary="premise disputed",
                                             file="b.py", line=2,
                                             outcome=FixOutcome.NEEDS_HUMAN,
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
        fix = _fix(
            items=[
                ItemOutcome(id="t1", summary="fix it", file="x.py", line=1, outcome=FixOutcome.FIXED),
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
        fix = _fix(
            items=[
                ItemOutcome(id="t1", summary="fix it", file="x.py", line=1, outcome=FixOutcome.FIXED),
            ],
            commit_sha="def5678",
            commit_status="push_failed",
            summary_deferred=True,
        )
        state = _make_state(fix)
        report = PRReport()
        with patch("pr_comments.post_issue_comment") as mock_post:
            with patch.object(rt.push, "holds", return_value=False):
                rt._render_deferred_summary(state, report, "owner/repo", 1, {})
        mock_post.assert_not_called()
        assert fix.summary_deferred is True

    def test_posts_when_push_failed_but_now_pushed(self, rt, publishing_on):
        fix = _fix(
            items=[
                ItemOutcome(id="t1", summary="fix it", file="x.py", line=1, outcome=FixOutcome.FIXED),
            ],
            commit_sha="def5678",
            commit_status="push_failed",
            summary_deferred=True,
        )
        state = _make_state(fix)
        report = PRReport()
        with patch("pr_comments.post_issue_comment", return_value="https://github.com/comment/1") as mock_post:
            with patch.object(rt.push, "holds", return_value=True):
                rt._render_deferred_summary(state, report, "owner/repo", 1, {})
        mock_post.assert_called_once()
        assert fix.summary_deferred is False
        assert fix.fix.commit_status == "pushed"
        body = mock_post.call_args[0][2]
        assert "def5678" in body
        assert "push failed" not in body

    def test_held_commit_keeps_the_summary_deferred(self, rt, publishing_on):
        """The commit link would 404 — same hazard as a failed push."""
        fix = _fix(
            items=[
                ItemOutcome(id="t1", summary="fix it", file="x.py", line=1, outcome=FixOutcome.FIXED),
            ],
            commit_sha="def5678",
            commit_status="push_held",
            summary_deferred=True,
        )
        state = _make_state(fix)
        with patch("pr_comments.post_issue_comment") as mock_post:
            with patch.object(rt.push, "holds", return_value=False):
                rt._render_deferred_summary(state, PRReport(), "owner/repo", 1, {})
        mock_post.assert_not_called()
        assert fix.summary_deferred is True

    def test_draft_run_leaves_the_deferred_queue_intact(self, rt):
        """Retiring push_failed without publishing would strand the replies."""
        fix = _fix(
            items=[
                ItemOutcome(id="t1", summary="fix it", file="x.py", line=1, outcome=FixOutcome.FIXED),
            ],
            commit_sha="def5678",
            commit_status="push_failed",
            summary_deferred=True,
        )
        state = _make_state(fix)
        with patch.object(rt.push, "holds", return_value=True):
            rt._render_deferred_summary(state, PRReport(), "owner/repo", 1, {})
        assert fix.fix.commit_status == "push_failed"
        assert fix.summary_deferred is True


class TestSummaryUsesPerThreadCommit:
    """A thread's row names the commit that fixed it, not the last pass's."""

    def _post(self, rt, *threads, commit_sha="", commit_status="no_changes"):
        fix = _fix(
            commit_sha=commit_sha, commit_status=commit_status,
            summary_deferred=True, items=list(threads),
        )
        with patch("pr_comments.post_issue_comment", return_value="u") as post:
            rt._render_deferred_summary(_make_state(fix), PRReport(), "owner/repo", 1, {})
        return post.call_args[0][2]

    def test_row_links_the_thread_own_commit(self, rt):
        body = self._post(rt, ItemOutcome(
            id="t1", summary="fix regex", file="p.py", line=10,
            outcome=FixOutcome.FIXED, commit_sha="deadbee",
        ))
        assert "deadbee" in body
        assert "no commit needed" not in body

    def test_row_without_a_sha_claims_no_commit(self, rt):
        body = self._post(rt, ItemOutcome(
            id="t1", summary="fix regex", file="p.py", line=10,
            outcome=FixOutcome.FIXED,
        ))
        assert rt._UNATTRIBUTED_STATUS_TEXT in body

    def test_each_round_keeps_its_own_attribution(self, rt):
        """The failure: one pass's envelope SHA relabelled every round."""
        body = self._post(
            rt,
            ItemOutcome(id="t1", summary="round one", file="a.py", line=1,
                          outcome=FixOutcome.FIXED, commit_sha="1111111"),
            ItemOutcome(id="t2", summary="round two", file="b.py", line=2,
                          outcome=FixOutcome.FIXED, commit_sha="2222222"),
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
            ItemOutcome(id="t1", summary="fixed by hand", file="a.py", line=1,
                          outcome=FixOutcome.FIXED,
                          reason=rt._RECONCILED_REASON),
            commit_sha="def5678", commit_status="pushed",
        )
        assert "Fixed in" not in body
        assert "Addressed outside the fix pass" in body

    def test_a_thread_with_no_sha_does_not_borrow_the_pass(self, rt):
        """The pass committed; this row is not in that commit, so it says so.

        The row's file cell still permalinks at the pass's SHA — a location
        anchor pins the tree the reviewer should read, which is a different
        claim from "this commit fixed your thread". Only the status cell makes
        that claim, and it has nothing to make it with.
        """
        body = self._post(
            rt,
            ItemOutcome(id="t1", summary="fix it", file="a.py", line=1,
                          outcome=FixOutcome.FIXED),
            commit_sha="def5678", commit_status="pushed",
        )
        assert rt._UNATTRIBUTED_STATUS_TEXT in body
        assert "Fixed in" not in body
        assert "/blob/def5678/a.py" in body


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

        def mock_run(*cmd, **kwargs):
            if "push" in cmd:
                pushes.append(cmd)
            if "commit" in cmd:
                commits.append(cmd)
                return _git_ran(1, stderr="pre-commit hook failed\n")
            return _git_ran(0, stdout="aaa1111\n")

        with patch.object(rt.agent_invoke.ai_backend, "invoke_fix",
                          side_effect=_tick_every_fix(tmp_path)), \
             patch.object(rt, "_diff_context_for_file", return_value=""), \
             patch.object(rt, "_find_and_update_main_worktree", return_value=None), \
             patch.object(rt, "_resolve_default_branch", return_value="main"), \
             patch.object(rt, "_persist_fix_state") as persist, \
             patch.object(rt.git_client, "run", side_effect=mock_run), \
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
        assert run.persisted.fix.commit_status == "commit_failed"
        assert run.persisted.fix.commit_sha == ""

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
        fix = _fix(
            items=[ItemOutcome(id="t1", summary="t1 summary", file="f.go",
                                   line=10, outcome=FixOutcome.FIXED)],
            commit_status="commit_failed", head_sha="aaa1111",
            summary_deferred=True,
        )
        with patch.object(rt, "_get_head_sha", return_value="bbb2222"), \
             patch.object(rt.push, "holds", return_value=True), \
             patch("pr_comments.post_issue_comment", return_value="u") as post:
            rt._render_deferred_summary(_make_state(fix), PRReport(), "owner/repo", 1, {})
        body = post.call_args[0][2]
        assert "bbb2222" in body
        assert "no commit needed" not in body
        assert rt._UNATTRIBUTED_STATUS_TEXT not in body

    def test_several_hand_commits_credit_none_of_them(self, rt):
        """Reconciliation is one yes/no about the branch, not per-row evidence.

        HEAD moved by more than one commit, so "the operator landed the pass's
        work" stops identifying a commit. Naming HEAD anyway would credit the
        last commit for every row, including rows that landed two commits
        earlier — a link that opens a diff the reviewer's thread is not in.
        """
        fix = _fix(
            items=[ItemOutcome(id="t1", summary="t1 summary", file="f.go",
                                   line=10, outcome=FixOutcome.FIXED)],
            commit_status="commit_failed", head_sha="aaa1111",
            summary_deferred=True,
        )
        with patch.object(rt, "_get_head_sha", return_value="ccc3333"), \
             patch.object(rt.push, "holds", return_value=True), \
             patch.object(rt, "_commits_since", return_value=["ccc3333", "bbb2222"]), \
             patch("pr_comments.post_issue_comment", return_value="u") as post:
            rt._render_deferred_summary(_make_state(fix), PRReport(), "owner/repo", 1, {})
        body = post.call_args[0][2]
        assert rt._UNATTRIBUTED_STATUS_TEXT in body
        assert "Fixed in" not in body
        # Where to look stays knowable even when who landed it does not: the
        # file cell pins the tree that holds the work.
        assert "/blob/ccc3333/f.go" in body

    def test_a_range_git_cannot_read_keeps_the_single_commit_reading(self, rt, worktree):
        """An unresolvable range must not withdraw every attribution on the branch.

        The multi-commit guard fires on positive evidence of more than one
        commit. A rebased-away snapshot makes `rev-list` fail, which is not
        that evidence — so this asks a real repo for a range neither end of
        which it has, rather than standing that in with an absent worktree.
        """
        assert rt._commits_since(worktree, "aaa1111", "bbb2222") == []

    def test_an_unpushed_hand_commit_claims_nothing(self, rt):
        """A SHA a reviewer cannot open is not worth naming."""
        fix = _fix(
            items=[ItemOutcome(id="t1", summary="t1 summary", file="f.go",
                                   line=10, outcome=FixOutcome.FIXED)],
            commit_status="commit_failed", head_sha="aaa1111",
            summary_deferred=True,
        )
        with patch.object(rt, "_get_head_sha", return_value="bbb2222"), \
             patch.object(rt.push, "holds", return_value=False), \
             patch("pr_comments.post_issue_comment", return_value="u") as post:
            rt._render_deferred_summary(_make_state(fix), PRReport(), "owner/repo", 1, {})
        body = post.call_args[0][2]
        assert rt._RECONCILED_STATUS_TEXT in body
        assert "bbb2222" not in body

    def test_a_still_unmoved_head_keeps_the_failure(self, rt):
        """Nothing was committed by anyone — the cell must not invent a commit."""
        fix = _fix(
            items=[ItemOutcome(id="t1", summary="t1 summary", file="f.go",
                                   line=10, outcome=FixOutcome.FIXED)],
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
            [CommentItem(id="t1", summary="fix it", file="a.py", line=1)],
            cp, "owner/repo",
        )
        assert "no commit to attribute" in capsys.readouterr().err


class TestTheWarningCountsTheRowsThatReachTheReader:
    """The warned number and the table it describes are one answer.

    The warning ran against the list before the renderer folded it and before
    the renderer settled which cell each row gets, so it counted rows nobody
    would see and rows that render a perfectly good claim. On the report this
    came from it said 10 over a table carrying 6.
    """

    @staticmethod
    def _outcome(tid, file, line, reason=""):
        return ItemOutcome(
            id=tid, file=file, line=line,
            summary=f"{tid} summary", outcome=FixOutcome.FIXED, reason=reason,
        )

    def _publish(self, rt, threads):
        """Render a summary whose pass committed nothing and whose HEAD stood still."""
        by_id = {
            t.id: ReportThread(id=t.id, file=t.file, line=t.line,
                               comments=[{"databaseId": 100 + n}])
            for n, t in enumerate(threads) if not t.id.startswith("ic-")
        }
        fix = _fix(
            items=threads, commit_status="no_changes", head_sha="aaa1111",
            reviewers={t.id: "kgn" for t in threads},
            summary_deferred=True, has_comment_items=True,
        )
        with patch.object(rt, "_get_head_sha", return_value="aaa1111"), \
             patch("pr_comments.post_issue_comment", return_value="u") as post:
            rt._render_deferred_summary(
                _make_state(fix), PRReport(), "owner/repo", 1, by_id,
            )
        return post.call_args[0][2]

    def _threads(self, rt):
        return [
            # Two rows nothing on the branch accounts for — what the warning is for.
            self._outcome("t1", "f.go", 10),
            self._outcome("t2", "g.go", 20),
            # The comment item restating t1: same reviewer, same file:line, and
            # no thread of its own, so the renderer folds it into t1's row.
            self._outcome("ic-500-1", "f.go", 10),
            # Settled outside the pass. Uncitable, but the cell says where the
            # fix went, so it is no contradiction to report.
            self._outcome("t3", "h.go", 30, reason=rt._RECONCILED_REASON),
        ]

    def test_the_count_equals_the_rows_rendered_without_a_claim(self, rt, capsys):
        body = self._publish(rt, self._threads(rt))
        warned = int(re.search(
            r"(\d+) fixed row\(s\) have no commit", capsys.readouterr().err,
        ).group(1))
        assert warned == body.count(rt._UNATTRIBUTED_STATUS_TEXT)

    def test_the_folded_row_is_neither_counted_nor_rendered(self, rt, capsys):
        body = self._publish(rt, self._threads(rt))
        assert "ic-500-1 summary" not in body
        assert "2 fixed row(s) have no commit" in capsys.readouterr().err

    def test_a_row_settled_outside_the_pass_is_not_a_contradiction(self, rt, capsys):
        body = self._publish(rt, self._threads(rt))
        err = capsys.readouterr().err
        # Three rows carry no commit link; only two of them claim nothing. The
        # third says where its fix went, which is why "uncited" is the wrong
        # test and the rendered cell is the right one.
        assert body.count(rt._RECONCILED_STATUS_TEXT) == 1
        assert body.count(rt._UNATTRIBUTED_STATUS_TEXT) == 2
        assert "2 fixed row(s) have no commit" in err

    def test_a_table_with_nothing_to_report_stays_quiet(self, rt, capsys):
        """Every row folded or settled leaves no contradiction to warn about."""
        body = self._publish(rt, [
            self._outcome("t3", "h.go", 30, reason=rt._RECONCILED_REASON),
        ])
        assert rt._RECONCILED_STATUS_TEXT in body
        assert "no commit to attribute" not in capsys.readouterr().err


class TestSummaryHasContent:
    """The one owner of "does this round have anything for a table to say".

    Tested directly rather than only through its two callers: the whole point
    of the helper is that both read the same answer, and a contract pinned only
    through one of them is one the other can still be changed out from under.
    """

    def _content(self, rt, **kw):
        args = {
            "fixed": [], "needs_human": [], "deferred": [], "dismissed": [],
            "already_addressed": [], "issue_comments": [],
            "review_body_comments": [],
        }
        args.update(kw)
        return rt._summary_has_content(**args)

    def test_an_empty_round_has_nothing_to_say(self, rt):
        assert self._content(rt) is False

    @pytest.mark.parametrize(
        "bucket", ["fixed", "needs_human", "deferred", "dismissed", "already_addressed"],
    )
    def test_any_settled_thread_is_a_row(self, rt, bucket):
        assert self._content(rt, **{bucket: ["t1"]}) is True

    @pytest.mark.parametrize("kind", ["issue_comments", "review_body_comments"])
    def test_an_unseen_comment_is_a_row_with_no_thread_behind_it(self, rt, kind):
        assert self._content(rt, **{kind: [{"seen": False}]}) is True

    @pytest.mark.parametrize("kind", ["issue_comments", "review_body_comments"])
    def test_a_comment_the_round_saw_is_not(self, rt, kind):
        assert self._content(rt, **{kind: [{"seen": True}]}) is False

    def test_a_comment_with_no_seen_key_reads_as_unseen(self, rt):
        """External data, so the read is a `.get` and its default is the answer."""
        assert self._content(rt, issue_comments=[{}]) is True


class TestSummaryStillOwed:
    """Whether the round has a fix summary the PR has not been told about."""

    def _owed(self, rt, **kw):
        args = {
            "fixed": [], "needs_human": [], "deferred": [], "dismissed": [],
            "commit_status": "pushed", "has_unaccounted": False,
            "already_addressed": [], "issue_comments": [],
            "review_body_comments": [],
        }
        args.update(kw)
        return rt._summary_still_owed(**args)

    def test_nothing_to_say(self, rt, publishing_on):
        assert self._owed(rt) is False

    def test_open_discussion_defers(self, rt, publishing_on):
        assert self._owed(rt, needs_human=["t1"]) is True

    def test_unpushed_commit_defers(self, rt, publishing_on):
        assert self._owed(rt, fixed=["t1"], commit_status="push_failed") is True

    def test_held_commit_defers(self, rt, publishing_on):
        """A held push leaves the same gap as a failed one: no remote commit."""
        assert self._owed(rt, fixed=["t1"], commit_status="push_held") is True

    def test_a_round_with_rows_owes_them(self, rt, publishing_on):
        """Owed is about the table, not about whether the post went out.

        The caller settles that half with `summary_url is None`, so a post the
        API refused leaves the summary owed instead of closing the round out.
        """
        assert self._owed(rt, fixed=["t1"]) is True

    def test_draft_leaves_the_summary_owed(self, rt):
        assert self._owed(rt, fixed=["t1"]) is True

    def test_draft_with_nothing_to_say_owes_nothing(self, rt):
        assert self._owed(rt) is False

    def test_an_already_addressed_only_round_owes_its_table(self, rt):
        """The round the bucket test missed: no fix, no dismissal, a full table.

        Every thread settled before this pass reached it, so the draft renders
        rows for them and records outcomes for none of the two buckets the old
        clause named.
        """
        assert self._owed(rt, already_addressed=["t1"]) is True

    def test_an_unread_issue_comment_owes_a_table_on_its_own(self, rt):
        """The summary reports unseen comments, so one is a row to render."""
        assert self._owed(rt, issue_comments=[{"seen": False}]) is True
        assert self._owed(rt, review_body_comments=[{"seen": False}]) is True

    def test_comments_the_round_already_saw_owe_nothing(self, rt):
        assert self._owed(rt, issue_comments=[{"seen": True}]) is False


class TestPushHeldCommit:
    """--finish --post is the human saying the held work may land."""

    @staticmethod
    def _state(status="push_held", sha="abc1234"):
        return _make_state(_fix(commit_sha=sha, commit_status=status))

    def test_pushes_and_marks_it_pushed(self, rt, publishing_on):
        state = self._state()
        with patch.object(rt.push, "holds", return_value=False), \
             patch.object(rt.git_client, "run",
                          side_effect=_answering_the_owner(
                              lambda *c, **kw: _git_ran(0, stdout="abc1234\n"))) as run:
            rt._push_held_commit(state, Path("/fake"))
        assert state.fix.fix.commit_status == "pushed"
        assert ("push",) in [call.args for call in run.call_args_list]

    def test_a_push_the_remote_never_took_is_push_lost_for_a_held_commit(
        self, rt, publishing_on,
    ):
        """The held commit was released, retried once, and still did not arrive."""
        def clean_tree(*cmd, **kwargs):
            # The porcelain read has to come back empty. A blanket stub answers
            # it with a SHA, which reads as a dirty tree — and the owner refuses
            # to retry into one, so the retry this test is about never runs.
            if cmd[:2] == ("status", "--porcelain"):
                return _git_ran(0)
            return _git_ran(0, stdout="abc1234\n")

        state = self._state()
        with patch.object(rt.push, "holds", return_value=False), \
             patch.object(rt.git_client, "run",
                          side_effect=_answering_the_owner(
                              clean_tree, _LOST_SHA)) as run:
            rt._push_held_commit(state, Path("/fake"))
        assert state.fix.fix.commit_status == "push_lost"
        pushes = [c.args for c in run.call_args_list if c.args[:1] == ("push",)]
        assert pushes == [("push",), ("push", "--no-verify")]

    def test_a_draft_finish_still_holds_it(self, rt):
        """--finish without --post is not the human saying go."""
        def boom(*a, **kw):
            raise AssertionError(f"a subprocess ran while the gate was shut: {a}")

        state = self._state()
        with patch.object(rt.push, "holds", return_value=False), \
             patch.object(rt.git_client, "run", boom):
            rt._push_held_commit(state, Path("/fake"))
        assert state.fix.fix.commit_status == "push_held"

    def test_a_hold_placed_this_run_outranks_post(self, rt, publishing_on):
        """--fix --finish --post in one run: the discussion is still open."""
        import publishing
        publishing.hold("discussion open")

        def boom(*a, **kw):
            raise AssertionError(f"a subprocess ran while the gate was shut: {a}")

        state = self._state()
        with patch.object(rt.push, "holds", return_value=False), \
             patch.object(rt.git_client, "run", boom):
            rt._push_held_commit(state, Path("/fake"))
        assert state.fix.fix.commit_status == "push_held"

    def test_a_failed_push_is_recorded_as_such(self, rt, publishing_on):
        state = self._state()
        with patch.object(rt.push, "holds", return_value=False), \
             patch.object(rt.git_client, "run",
                          return_value=_git_ran(1, stderr="rejected\n")):
            rt._push_held_commit(state, Path("/fake"))
        assert state.fix.fix.commit_status == "push_failed"

    def test_a_failed_push_reaches_the_trail(self, rt, publishing_on):
        """Same as the two sibling push paths — a failure here is not silent."""
        trail = MagicMock()
        state = self._state()
        with patch.object(rt.push, "holds", return_value=False), \
             patch.object(rt.git_client, "run",
                          return_value=_git_ran(1, stderr="rejected\n")):
            rt._push_held_commit(state, Path("/fake"), trail)
        trail.error.assert_called_once()
        assert "rejected" in trail.error.call_args.kwargs["data"]["error"]

    def test_a_commit_already_on_the_remote_is_just_marked(self, rt, publishing_on):
        """Someone pushed by hand between the two runs."""
        def boom(*a, **kw):
            raise AssertionError(f"pushed a commit the remote already had: {a}")

        state = self._state()
        with patch.object(rt.push, "holds", return_value=True), \
             patch.object(rt.git_client, "run", boom):
            rt._push_held_commit(state, Path("/fake"))
        assert state.fix.fix.commit_status == "pushed"

    def test_noop_when_the_commit_already_went_out(self, rt, publishing_on):
        def boom(*a, **kw):
            raise AssertionError(f"pushed an already-pushed commit: {a}")

        state = self._state(status="pushed")
        with patch.object(rt.git_client, "run", boom):
            rt._push_held_commit(state, Path("/fake"))
        assert state.fix.fix.commit_status == "pushed"

    def test_noop_when_the_pass_made_no_commit(self, rt, publishing_on):
        def boom(*a, **kw):
            raise AssertionError(f"pushed with no commit to push: {a}")

        state = self._state(status="no_changes", sha="")
        with patch.object(rt.git_client, "run", boom):
            rt._push_held_commit(state, Path("/fake"))
        assert state.fix.fix.commit_status == "no_changes"


def _short_sha(path, rev="HEAD") -> str:
    """The short SHA of *rev* in the repo at *path*."""
    return subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--short", rev],
        check=True, capture_output=True, text=True, timeout=10,
    ).stdout.strip()


# What every fix pass commits under (`_commit_fixes`), so two rounds on one
# branch are indistinguishable by subject — which is why identity is content.
_FIX_SUBJECT = "fix: address review comments"

# One author date for every fix commit a helper here writes. Two rounds
# genuinely do land in the same second — a retry, fast CI — and a test that
# waited for the clock to tick would only be reproducing the easy case.
_FIX_DATE = "2026-01-01T00:00:00+00:00"


def _feature_off_origin(tmp_path) -> Path:
    """A clone with one pushed commit on `main` and `feature` checked out.

    Real git throughout, because the bug these build is precisely the
    disagreement between two ways of asking about a rewritten commit: the
    recorded SHA still resolves as an object, and no branch anywhere contains
    it. Nothing short of an actual rebase produces that pair.
    """
    origin = tmp_path / "origin"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(origin)],
                   check=True, capture_output=True, timeout=10)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(origin), str(work)],
                   check=True, capture_output=True, timeout=10)
    git_in(work, "config", "user.email", "t@example.com")
    git_in(work, "config", "user.name", "Test")

    (work / "base.txt").write_text("base\n")
    git_in(work, "add", "-A")
    git_in(work, "commit", "-q", "--no-verify", "-m", "base")
    git_in(work, "push", "-q", "-u", "origin", "main")
    git_in(work, "checkout", "-q", "-b", "feature")
    return work


def _fix_commit(work, name, content=None) -> str:
    """A fix-pass commit adding *name*, under the pass's own subject and date."""
    (work / name).write_text(content if content is not None else f"{name}\n")
    git_in(work, "add", "-A")
    git_in(work, "commit", "-q", "--no-verify", "--date", _FIX_DATE, "-m", _FIX_SUBJECT)
    return _short_sha(work)


def _rebase_onto_moved_main(work) -> None:
    """Move `main` on and replay `feature` over it, the way `pr rebase` does."""
    git_in(work, "checkout", "-q", "main")
    (work / "upstream.txt").write_text("upstream\n")
    git_in(work, "add", "-A")
    git_in(work, "commit", "-q", "--no-verify", "-m", "upstream work")
    git_in(work, "push", "-q", "origin", "main")
    git_in(work, "checkout", "-q", "feature")
    git_in(work, "rebase", "-q", "origin/main")


def _held_fix_branch(tmp_path, *, rebase=True, drop=False, push=True):
    """A branch carrying a fix commit the pass held.

    `rebase` replays the fix commit onto upstream work the way `pr rebase` does,
    `drop` throws it away instead, and `push` decides whether what survives ever
    reached the remote. `.held` is the SHA the fix pass recorded; `.replay` is
    what the branch carries afterwards.
    """
    work = _feature_off_origin(tmp_path)
    held = _fix_commit(work, "fix.txt")

    if drop:
        git_in(work, "reset", "-q", "--hard", "HEAD~1")
        return SimpleNamespace(path=work, held=held, replay="")

    if rebase:
        _rebase_onto_moved_main(work)
    if push:
        git_in(work, "push", "-q", "-u", "origin", "feature")
    return SimpleNamespace(path=work, held=held, replay=_short_sha(work))


def _two_held_rounds(tmp_path):
    """Two rounds of held fixes on one branch, then a rebase over both.

    The pair a snapshot cannot tell apart by anything but content: same static
    subject, same author date, two different commits it has to map separately.
    """
    work = _feature_off_origin(tmp_path)
    first = _fix_commit(work, "one.txt")
    second = _fix_commit(work, "two.txt")
    _rebase_onto_moved_main(work)
    git_in(work, "push", "-q", "-u", "origin", "feature")
    return SimpleNamespace(
        path=work, first=first, second=second,
        first_replay=_short_sha(work, "HEAD~1"), second_replay=_short_sha(work),
    )


def _duplicated_fix(tmp_path):
    """A branch where the held fix's patch appears twice: applied, undone, redone.

    Two commits with one patch id, so "which commit replays the orphan?" has no
    answer — the state a closeout must refuse to guess at rather than pick from.
    """
    work = _feature_off_origin(tmp_path)
    held = _fix_commit(work, "fix.txt")
    (work / "fix.txt").unlink()
    git_in(work, "add", "-A")
    git_in(work, "commit", "-q", "--no-verify", "-m", "revert: back that out")
    _fix_commit(work, "fix.txt")
    _rebase_onto_moved_main(work)
    git_in(work, "push", "-q", "-u", "origin", "feature")
    return SimpleNamespace(path=work, held=held)


class TestFollowHistoryRewrite:
    """A rebase renames the held commit; it does not unpublish the work.

    `pr rebase --fix` is what a supersession warning tells the operator to run,
    and it rewrote every SHA the fix pass had recorded. The closeout then read
    its own commit as unpushed forever, with no way forward that did not discard
    the reviewed replies.
    """

    def _state(self, repo, status=CommitStatus.PUSH_HELD):
        return _make_state(_fix(
            commit_sha=repo.held, commit_status=status, head_sha=repo.held,
            items=[ItemOutcome(id="t1", outcome=FixOutcome.FIXED,
                                   commit_sha=repo.held, read_sha=repo.held)],
        ))

    def test_a_rebased_commit_is_followed_to_its_replay(self, rt, tmp_path):
        repo = _held_fix_branch(tmp_path)
        state = self._state(repo)
        rt._follow_history_rewrite(state, repo.path)
        assert state.fix.fix.commit_sha == repo.replay
        assert state.fix.fix.head_sha == repo.replay
        assert state.fix.fix.items[0].commit_sha == repo.replay
        assert state.fix.fix.items[0].read_sha == repo.replay

    def test_the_replay_is_what_the_remote_has(self, rt, tmp_path):
        """The point of following it: the hold is over a name, not the work."""
        repo = _held_fix_branch(tmp_path)
        assert rt.push.holds(repo.path, repo.held) is False
        state = self._state(repo)
        rt._follow_history_rewrite(state, repo.path)
        assert rt.push.holds(repo.path, state.fix.fix.commit_sha) is True

    def test_the_closeout_stops_holding_after_a_rebase(
        self, rt, tmp_path, publishing_on,
    ):
        """Regression: --finish blocked on a SHA the rebase it advised orphaned."""
        repo = _held_fix_branch(tmp_path)
        pr_state.save_state(repo.path / "target", PRState(
            identity=PRIdentity(repo="owner/repo", branch="feature", pr_number=42,
                                head_sha=repo.held, worktree_root=str(repo.path)),
            fix=_fix(commit_sha=repo.held, commit_status=CommitStatus.PUSH_HELD,
                           head_sha=repo.held),
        ))
        ctx = make_ctx(branch="feature", worktree_root=repo.path,
                       head_sha=repo.replay, target_dir=repo.path / "target")
        with patch.object(rt, "_post_pending_fix_replies"), \
                patch.object(rt, "_finalize_deferred"), \
                patch.object(rt, "_render_deferred_summary"):
            rt._finish_deferred_work(ctx, PRReport())
        saved = pr_state.load_state(repo.path / "target")
        assert saved.fix.fix.commit_sha == repo.replay
        assert saved.fix.fix.commit_status == CommitStatus.PUSHED

    def test_two_rounds_each_reach_their_own_replay(self, rt, tmp_path):
        """Every fix pass commits under one static subject, so identity is content.

        A snapshot spans rounds — a thread fixed two commits ago still cites the
        commit that fixed it — so mapping both orphans onto whichever replay was
        found first would post one round's permalink under the other's work.
        """
        repo = _two_held_rounds(tmp_path)
        state = _make_state(_fix(
            commit_sha=repo.second, commit_status=CommitStatus.PUSH_HELD,
            head_sha=repo.second,
            items=[
                ItemOutcome(id="t1", outcome=FixOutcome.FIXED,
                              commit_sha=repo.first, read_sha=repo.first),
                ItemOutcome(id="t2", outcome=FixOutcome.FIXED,
                              commit_sha=repo.second, read_sha=repo.second),
            ],
        ))
        rt._follow_history_rewrite(state, repo.path)
        assert repo.first_replay != repo.second_replay
        assert state.fix.fix.items[0].commit_sha == repo.first_replay
        assert state.fix.fix.items[1].commit_sha == repo.second_replay
        assert state.fix.fix.commit_sha == repo.second_replay

    def test_two_commits_carrying_one_patch_are_not_guessed_between(
        self, rt, tmp_path,
    ):
        """Applied, undone, redone: the branch offers two answers, so there is none."""
        repo = _duplicated_fix(tmp_path)
        state = _make_state(_fix(
            commit_sha=repo.held, commit_status=CommitStatus.PUSH_HELD,
            head_sha=repo.held,
        ))
        warned = []
        with patch.object(rt.log, "warn", side_effect=warned.append):
            rt._follow_history_rewrite(state, repo.path)
        assert state.fix.fix.commit_sha == repo.held
        assert any(repo.held in w and "pr comments --fix" in w for w in warned)

    def test_the_deferred_replies_are_let_out(self, rt, tmp_path):
        """The other gate the orphan jammed: every reply cites the commit."""
        repo = _held_fix_branch(tmp_path)
        state = self._state(repo)
        state.identity.worktree_root = str(repo.path)
        state.fix.replies_pending = True
        logged = []
        with patch.object(rt.log, "info", side_effect=logged.append), \
                patch.object(rt, "_reply_to_fixed", return_value=1) as reply, \
                patch.object(rt, "_resolve_fixed_threads"):
            rt._follow_history_rewrite(state, repo.path)
            rt._post_pending_fix_replies(state, "owner/repo", 42, {})
        assert not any("Push still pending" in m for m in logged)
        assert reply.call_args[0][4].sha == repo.replay

    def test_a_rebase_nobody_pushed_still_holds(self, rt, tmp_path):
        """The replay is real and local — which is an ordinary unpushed commit."""
        repo = _held_fix_branch(tmp_path, push=False)
        state = self._state(repo)
        rt._follow_history_rewrite(state, repo.path)
        assert state.fix.fix.commit_sha == repo.replay
        assert rt.push.holds(repo.path, state.fix.fix.commit_sha) is False
        rt._push_held_commit(state, repo.path)
        assert state.fix.fix.commit_status == CommitStatus.PUSH_HELD

    def test_a_commit_that_was_never_pushed_is_left_alone(self, rt, tmp_path):
        """No rewrite happened: the SHA is on the branch and simply not sent."""
        repo = _held_fix_branch(tmp_path, rebase=False, push=False)
        state = self._state(repo)
        rt._follow_history_rewrite(state, repo.path)
        assert state.fix.fix.commit_sha == repo.held
        assert rt.push.holds(repo.path, state.fix.fix.commit_sha) is False
        rt._push_held_commit(state, repo.path)
        assert state.fix.fix.commit_status == CommitStatus.PUSH_HELD

    def test_an_orphan_with_no_replay_holds_and_says_how_to_recover(
        self, rt, tmp_path,
    ):
        """Dropped, squashed, reworded: the work is not there under any name."""
        repo = _held_fix_branch(tmp_path, drop=True)
        state = self._state(repo)
        warned = []
        with patch.object(rt.log, "warn", side_effect=warned.append):
            rt._follow_history_rewrite(state, repo.path)
        assert state.fix.fix.commit_sha == repo.held
        assert any(repo.held in w and "pr comments --fix" in w for w in warned)

    def test_an_orphan_says_nothing_once_the_hold_is_over(self, rt, tmp_path):
        """A pushed status has nothing to unblock, so the warning is only noise."""
        repo = _held_fix_branch(tmp_path, drop=True)
        state = self._state(repo, status=CommitStatus.PUSHED)
        warned = []
        with patch.object(rt.log, "warn", side_effect=warned.append):
            rt._follow_history_rewrite(state, repo.path)
        assert warned == []

    def test_a_snapshot_with_no_shas_asks_git_nothing(self, rt):
        def boom(*a, **kw):
            raise AssertionError(f"a snapshot with nothing recorded ran git: {a}")

        state = _make_state(_fix(items=[ItemOutcome(id="t1")]))
        with patch.object(rt.git_client, "run", boom):
            rt._follow_history_rewrite(state, Path("/fake"))
        assert state.fix.fix.commit_sha == ""


class TestDeliverPrBody:
    """A comment answered by rewriting the PR description is gated like a reply.

    The fix agent may not run `gh` at all, so the rewrite arrives as a file in
    the worktree and this is what sends it. Every test here asserts on what
    reached (or did not reach) the process boundary rather than on a flag the
    caller consulted first.
    """

    def _draft(self, rt, wt_path, body="A rewritten description.\n"):
        path = rt._pr_body_draft(wt_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
        return path

    def test_a_draft_run_issues_no_gh_call(self, rt, worktree):
        """The regression: --fix without --post must not edit the PR."""
        def boom(*a, **kw):
            raise AssertionError(f"a subprocess ran while the gate was shut: {a}")

        draft = self._draft(rt, worktree)
        with patch("proc.subprocess.run", boom):
            assert rt._deliver_pr_body(worktree, "owner/repo", 42) is True
        assert draft.exists(), "the undelivered rewrite must survive for --finish"

    def test_the_gate_is_checked_at_the_write_not_by_the_caller(self, rt, worktree):
        """No `publishing.enabled()` guard here — the client refuses on its own.

        `_deliver_pr_body` is called unconditionally by the fix pass. If the gate
        lived at the call site instead, this call would publish.
        """
        self._draft(rt, worktree)
        with patch.object(rt.pc, "_gh_post", return_value=CmdResult(1)) as post:
            rt._deliver_pr_body(worktree, "owner/repo", 42)
        post.assert_called_once()

    def test_post_sends_it_through_the_pulls_endpoint(self, rt, worktree, publishing_on):
        calls = []
        self._draft(rt, worktree)
        with patch(
            "proc.subprocess.run",
            lambda *a, **kw: calls.append(a[0]) or _make_completed(0),
        ):
            assert rt._deliver_pr_body(worktree, "owner/repo", 42) is False
        assert calls == [[
            "gh", "api", "repos/owner/repo/pulls/42",
            "--method", "PATCH", "--input", "-",
        ]]

    def test_a_delivered_rewrite_is_not_sent_twice(self, rt, worktree, publishing_on):
        draft = self._draft(rt, worktree)
        with patch.object(rt.pc, "update_pr_body", return_value=True):
            rt._deliver_pr_body(worktree, "owner/repo", 42)
        assert not draft.exists()

    def test_the_fix_prompt_names_the_file_the_delivery_reads(self, rt, worktree):
        """One path, two ends: the agent writes where `_deliver_pr_body` looks."""
        adapter = _fix_adapter(rt, worktree)
        adapter.tracking_path.parent.mkdir(parents=True, exist_ok=True)
        adapter.tracking_path.write_text("")
        with patch.object(rt, "_find_and_update_main_worktree", return_value=None):
            prompt = fix_engine._prompt(adapter, 10)

        assert str(rt._pr_body_draft(worktree)) in prompt
        assert "${pr_body_file}" not in prompt

    def test_no_draft_owes_nothing(self, rt, worktree):
        def boom(*a, **kw):
            raise AssertionError(f"a subprocess ran with nothing to send: {a}")

        with patch("proc.subprocess.run", boom):
            assert rt._deliver_pr_body(worktree, "owner/repo", 42) is False

    def test_an_empty_draft_is_discarded_rather_than_sent(self, rt, worktree,
                                                          publishing_on):
        """Sending it would blank the description the reviewer is reading."""
        draft = self._draft(rt, worktree, body="   \n")
        with patch.object(rt.pc, "update_pr_body") as update:
            assert rt._deliver_pr_body(worktree, "owner/repo", 42) is False
        update.assert_not_called()
        assert not draft.exists()


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
        fix = _fix(
            items=[
                ItemOutcome(id=tid, summary=summary, file=path, line=line,
                              outcome=FixOutcome.FIXED)
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
        with patch.object(rt.push, "holds", return_value=True), \
             patch("pr_comments.post_thread_reply", return_value=True) as mock_reply, \
             patch("pr_comments.resolve_thread", return_value=True) as mock_resolve:
            rt._post_pending_fix_replies(state, "owner/repo", 1, threads_by_id)
        assert mock_reply.call_count == 2
        assert mock_resolve.call_count == 2
        assert fix.fix.commit_status == "pushed"

    def test_skips_when_still_unpushed(self, rt):
        fix, _ = self._queue(commit_status="push_failed", summary_deferred=True)
        state = _make_state(fix)
        with patch.object(rt.push, "holds", return_value=False), \
             patch("pr_comments.post_thread_reply") as mock_reply:
            rt._post_pending_fix_replies(state, "owner/repo", 1, {})
        mock_reply.assert_not_called()
        assert fix.fix.commit_status == "push_failed"

    def test_noop_when_not_push_failed(self, rt):
        fix = _fix(commit_status="pushed", summary_deferred=True)
        state = _make_state(fix)
        with patch("pr_comments.post_thread_reply") as mock_reply:
            rt._post_pending_fix_replies(state, "owner/repo", 1, {})
        mock_reply.assert_not_called()

    def test_draft_run_keeps_the_queue_for_a_later_post(self, rt):
        fix, threads_by_id = self._queue(commit_status="push_failed", summary_deferred=True)
        state = _make_state(fix)
        with patch.object(rt.push, "holds", return_value=True):
            rt._post_pending_fix_replies(state, "owner/repo", 1, threads_by_id)
        assert fix.fix.commit_status == "push_failed"

    def test_drains_the_queue_a_drafted_fix_pass_left_behind(self, rt, publishing_on):
        """A drafted --fix commits and sends nothing; --post must catch up.

        The `pushed` status here is a run whose push landed before the gate
        applied to it — the queue survives on `replies_pending` alone.
        """
        fix, threads_by_id = self._queue(commit_status="pushed", replies_pending=True)
        state = _make_state(fix)
        with patch.object(rt.push, "holds", return_value=True), \
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
        with patch.object(rt.push, "holds", return_value=True), \
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
        with patch.object(rt.push, "holds", return_value=True):
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
             patch.object(rt.push, "holds", return_value=True), \
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
             patch.object(rt.push, "holds", return_value=True), \
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
    is precisely the shape the old `if not fix.fix.commit_sha: return` swallowed.
    """

    _ADDRESSED = f"t-{FixOutcome.ALREADY_ADDRESSED}"

    def _queue(self, *outcomes, **fix_kw):
        fix_kw.setdefault("replies_pending", True)
        fix = _fix(
            items=[
                ItemOutcome(id=f"t-{o}", summary=f"the {o} one",
                            file="x.py", line=1, outcome=o,
                            reason=f"because the {o} premise says so")
                for o in outcomes
            ],
            commit_status="no_changes",
            **fix_kw,
        )
        threads_by_id = {
            f"t-{o}": ReportThread(id=f"t-{o}", is_resolved=False,
                                   comments=[{"databaseId": 100 + n}])
            for n, o in enumerate(outcomes)
        }
        return fix, threads_by_id

    def test_drains_replies_a_pass_that_committed_nothing_left_behind(
        self, rt, publishing_on,
    ):
        fix, threads_by_id = self._queue(
            FixOutcome.ALREADY_ADDRESSED, FixOutcome.DISMISSED,
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
            FixOutcome.ALREADY_ADDRESSED, FixOutcome.DISMISSED,
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
        fix, threads_by_id = self._queue(FixOutcome.DISMISSED)
        state = _make_state(fix)
        with patch.object(rt, "_get_head_sha", return_value="deadbee"), \
             patch("pr_comments.post_thread_reply", return_value=True) as mock_reply:
            rt._post_pending_fix_replies(state, "owner/repo", 1, threads_by_id)
        assert "because the dismissed premise says so" in mock_reply.call_args.args[3]

    def test_a_commitless_queue_does_not_wait_on_a_push(self, rt, publishing_on):
        """These replies cite HEAD, not a fix commit, so there is nothing to wait for."""
        fix, threads_by_id = self._queue(FixOutcome.ALREADY_ADDRESSED)
        state = _make_state(fix)
        with patch.object(rt.push, "holds", return_value=False) as mock_pushed, \
             patch.object(rt, "_get_head_sha", return_value="deadbee"), \
             patch.object(rt, "_find_addressing_commit", return_value=None), \
             patch("pr_comments.post_thread_reply", return_value=True) as mock_reply, \
             patch("pr_comments.resolve_thread", return_value=True):
            rt._post_pending_fix_replies(state, "owner/repo", 1, threads_by_id)
        mock_pushed.assert_not_called()
        assert mock_reply.call_count == 1

    def test_no_changes_is_not_rewritten_as_pushed(self, rt, publishing_on):
        """The pass committed nothing; saying it pushed would invent a commit."""
        fix, threads_by_id = self._queue(FixOutcome.ALREADY_ADDRESSED)
        state = _make_state(fix)
        with patch.object(rt, "_get_head_sha", return_value="deadbee"), \
             patch.object(rt, "_find_addressing_commit", return_value=None), \
             patch("pr_comments.post_thread_reply", return_value=True), \
             patch("pr_comments.resolve_thread", return_value=True):
            rt._post_pending_fix_replies(state, "owner/repo", 1, threads_by_id)
        assert fix.fix.commit_status == "no_changes"

    def test_a_draft_drain_keeps_the_queue(self, rt):
        """post_thread_reply is left real here — the draft gate lives inside it."""
        fix, threads_by_id = self._queue(FixOutcome.ALREADY_ADDRESSED)
        state = _make_state(fix)
        with patch.object(rt, "_get_head_sha", return_value="deadbee"), \
             patch.object(rt, "_find_addressing_commit", return_value=None):
            rt._post_pending_fix_replies(state, "owner/repo", 1, threads_by_id)
        assert fix.replies_posted == 0
        assert fix.replies_pending is True

    def test_a_settled_queue_is_left_alone(self, rt, publishing_on):
        fix, threads_by_id = self._queue(
            FixOutcome.ALREADY_ADDRESSED, replies_pending=False,
        )
        state = _make_state(fix)
        with patch("pr_comments.post_thread_reply") as mock_reply:
            rt._post_pending_fix_replies(state, "owner/repo", 1, threads_by_id)
        mock_reply.assert_not_called()


def _gated(*_args, **_kwargs):
    """Stand in for a GitHub write, reporting what the real one would.

    `pr_comments.post_thread_reply` and `resolve_thread` both refuse and return
    False when the gate is shut. A mock hardwired to True would report a drafted
    run as having published, which is the exact confusion these tests exist to
    catch.
    """
    import publishing
    return publishing.enabled()


class TestResolutionsReachThePersistedTally:
    """The closeout resolves threads after the counts were written.

    `pr status` reads `comments.by_state`, and that snapshot is saved at fetch
    time — before the fix pass or the drain runs. Without the delta a fully
    closed-out PR keeps reporting the threads it just resolved as open. This
    class covers the drain; `TestFixPassResolutionsReachTheTally` covers the
    fix pass, which resolves through the same helper on the commoner path.
    """

    def _drain(self, rt, by_state, *, prior=ThreadState.NEW, count=2):
        ids = [f"t{n}" for n in range(1, count + 1)]
        fix = _fix(
            items=[
                ItemOutcome(id=tid, summary="s", file="x.py", line=1,
                              outcome=FixOutcome.FIXED)
                for tid in ids
            ],
            commit_sha="abc1234", commit_status="pushed", replies_pending=True,
        )
        threads_by_id = {
            tid: ReportThread(id=tid, state=prior, is_resolved=False,
                              comments=[{"databaseId": 100 + n}])
            for n, tid in enumerate(ids)
        }
        state = _make_state(fix)
        state.comments.by_state = dict(by_state)
        with patch.object(rt.push, "holds", return_value=True), \
             patch("pr_comments.post_thread_reply", side_effect=_gated), \
             patch("pr_comments.resolve_thread", side_effect=_gated):
            rt._post_pending_fix_replies(state, "owner/repo", 1, threads_by_id)
        return state.comments

    def test_resolved_threads_leave_their_prior_bucket(self, rt, publishing_on):
        comments = self._drain(rt, {"new": 3, "resolved": 1})
        assert comments.by_state[ThreadState.NEW] == 1
        assert comments.by_state[ThreadState.RESOLVED] == 3

    def test_the_first_resolution_opens_the_bucket(self, rt, publishing_on):
        """A PR with nothing resolved yet has no `resolved` key to increment."""
        comments = self._drain(rt, {"addressed": 2}, prior=ThreadState.ADDRESSED)
        assert comments.by_state[ThreadState.RESOLVED] == 2
        assert comments.by_state[ThreadState.ADDRESSED] == 0

    def test_a_draft_moves_nothing(self, rt):
        """Nothing was resolved on GitHub, so the tally must not claim it was."""
        comments = self._drain(rt, {"new": 3, "resolved": 1})
        assert comments.by_state == {"new": 3, "resolved": 1}

    def test_the_tally_is_stamped_only_when_it_moves(self, rt, publishing_on):
        assert self._drain(rt, {"new": 2}).updated_at
        assert not self._drain(rt, {"new": 2}, count=0).updated_at

    def test_counts_never_go_negative(self, rt, publishing_on):
        """A bucket the snapshot under-counts must not wrap past zero."""
        comments = self._drain(rt, {"new": 1})
        assert comments.by_state[ThreadState.NEW] == 0
        assert comments.by_state[ThreadState.RESOLVED] == 2

    def test_an_undercounted_bucket_says_so(self, rt, publishing_on, capsys):
        """The clamp is a floor, not a reason to stay quiet about the mismatch."""
        self._drain(rt, {"new": 1})
        assert "no new left to move" in capsys.readouterr().err


class TestFixPassResolutionsReachTheTally:
    """The fix pass resolves after the counts were saved, same as the drain.

    This is the commoner path of the two: a pass that fixed, pushed, replied and
    resolved in one run leaves `replies_pending` false, so the drain returns
    early and never sees those threads. `_persist_fix_state` is where the pass
    writes its own results, and so where the delta has to land.
    """

    def _persist(self, rt, by_state, resolved):
        ctx = make_ctx()
        state = _make_state(_fix())
        state.comments.by_state = dict(by_state)
        with patch("pr_state.load_or_init", return_value=state), \
             patch("pr_state.save_state") as save:
            rt._persist_fix_state(_fix(), Path("/wt"), ctx, None,
                                  resolved=resolved)
        assert save.called, "the pass must still save what it persisted"
        return state.comments

    def test_the_pass_moves_what_it_resolved(self, rt):
        comments = self._persist(
            rt, {"new": 2, "addressed": 1},
            [ThreadState.NEW, ThreadState.ADDRESSED],
        )
        assert comments.by_state[ThreadState.NEW] == 1
        assert comments.by_state[ThreadState.ADDRESSED] == 0
        assert comments.by_state[ThreadState.RESOLVED] == 2

    def test_a_pass_that_resolved_nothing_leaves_the_tally_alone(self, rt):
        """The default, and the shape of every caller that predates the delta."""
        assert self._persist(rt, {"new": 2}, []).by_state == {"new": 2}

    def test_omitting_the_argument_is_the_same_as_none_resolved(self, rt):
        ctx = make_ctx()
        state = _make_state(_fix())
        state.comments.by_state = {"new": 2}
        with patch("pr_state.load_or_init", return_value=state), \
             patch("pr_state.save_state"):
            rt._persist_fix_state(_fix(), Path("/wt"), ctx, None)
        assert state.comments.by_state == {"new": 2}


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
        fix = _fix(
            items=list(outcomes), commit_sha=pass_sha,
            commit_status="pushed", replies_pending=True,
        )
        threads_by_id = {
            o.id: ReportThread(id=o.id, is_resolved=False,
                               comments=[{"databaseId": 100 + n}])
            for n, o in enumerate(outcomes)
        }
        with patch.object(rt.push, "holds", return_value=True), \
             patch("pr_comments.post_thread_reply", return_value=True) as reply, \
             patch("pr_comments.resolve_thread", return_value=True):
            rt._post_pending_fix_replies(_make_state(fix), "owner/repo", 1, threads_by_id)
        bodies = [call[0][3] for call in reply.call_args_list]
        return dict(zip([o.id for o in outcomes], bodies))

    @staticmethod
    def _fixed(tid, sha, path):
        return ItemOutcome(id=tid, summary=f"{tid} summary", file=path, line=1,
                             outcome=FixOutcome.FIXED, commit_sha=sha)

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

    def test_an_entry_with_no_commit_of_its_own_borrows_none(self, rt, publishing_on):
        """An entry the pass never recorded must not be credited to the pass.

        The pass committed, and this entry is not in that commit — it was
        replayed from a round that recorded nothing. Citing the pass's SHA
        sends the reviewer to a commit their thread is not in.
        """
        outcome = ItemOutcome(id="t1", summary="t1 summary", file="a.py", line=1,
                                outcome=FixOutcome.FIXED)
        bodies = self._drain(rt, outcome)
        assert _PASS_SHA not in bodies["t1"]
        assert "t1 summary" in bodies["t1"]

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
        entry = CommentItem(id="t1", summary="fix it", file="a.py", line=1,
                            commit_sha="abc1234")
        threads_by_id = {"t1": _standing_reply_thread(body=body)}
        with patch("pr_comments.patch_thread_reply", return_value=True) as edit, \
             patch("pr_comments.post_thread_reply", return_value=True) as post:
            count = rt._post_fix_replies(
                [entry], threads_by_id, "owner/repo", 42,
                rt.CommitPushResult("abc1234", "pushed", ""),
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

    def _reply_below_a_reviewer_answer(self, rt, body):
        """As `_reply`, but a reviewer has since answered our standing reply."""
        entry = CommentItem(id="t1", summary="fix it", file="a.py", line=1,
                            commit_sha="abc1234")
        thread = _standing_reply_thread(body=body, state=ThreadState.CONTESTED)
        thread.comments.append({
            "databaseId": 333,
            "body": "Agreed — I verified the rewrite at four DOM positions.",
            "author": {"login": "kgn"},
        })
        with patch("pr_comments.patch_thread_reply", return_value=True) as edit, \
             patch("pr_comments.post_thread_reply", return_value=True) as post:
            count = rt._post_fix_replies(
                [entry], {"t1": thread}, "owner/repo", 42,
                rt.CommitPushResult("abc1234", "pushed", ""),
            )
        return count, edit, post

    def test_a_rewritten_reply_survives_a_reviewer_answering_it(self, rt):
        """The protection used to vanish the moment the thread became a
        conversation: a reviewer's answer retired the standing-reply id the
        hand-written check was gated on, and the round stacked a third comment
        restating a settled position."""
        count, edit, post = self._reply_below_a_reviewer_answer(rt, (
            "Applied: fix it\n\n"
            "On reflection we are not doing this — the reviewer's premise "
            "assumes a code path that was removed."
        ))
        assert count == 0
        edit.assert_not_called()
        post.assert_not_called()

    def test_a_template_reply_is_still_reposted_once_answered(self, rt):
        """Pairs with the case above — proves that assertion is not vacuous.

        A generated standing reply is still replaced by a fresh comment under
        the reviewer's answer; only the hand-written case is protected.
        """
        count, edit, post = self._reply_below_a_reviewer_answer(rt, (
            "Applied: fix it\n\n"
            "Fixed in [`0000000`](https://github.com/owner/repo/commit/0000000)."
        ))
        assert count == 1
        edit.assert_not_called()
        assert post.call_args[0][2] == 111

    def test_the_newest_reply_of_ours_decides(self, rt):
        """`--reply` after a hand edit is the escape hatch, and it must settle
        the question — the older hand-written reply cannot outvote it."""
        thread = _standing_reply_thread(body="We are not doing this, and here is why.")
        thread.comments.append({
            "databaseId": 333, "body": "no", "author": {"login": "kgn"}})
        thread.comments.append({
            "databaseId": 444,
            "body": "Applied: fix it\n\nFixed in [`0000000`]"
                    "(https://github.com/owner/repo/commit/0000000).",
            "author": {"login": "me"},
        })
        assert rt._has_hand_written_reply(thread) is False

    def test_a_thread_nobody_of_ours_has_touched_is_not_held(self, rt):
        thread = ReportThread(id="t1", my_login="me", comments=[
            {"databaseId": 111, "body": "the point", "author": {"login": "kgn"}},
            {"databaseId": 222, "body": "seconded", "author": {"login": "ana"}},
        ])
        assert rt._has_hand_written_reply(thread) is False

    def test_our_own_review_point_is_not_a_reply(self, rt):
        """On a self-review the root is ours and is hand-written by definition;
        reading it as our standing reply would skip every thread."""
        thread = ReportThread(id="t1", my_login="me", comments=[
            {"databaseId": 111, "body": "this needs a guard", "author": {"login": "me"}},
        ])
        assert rt._has_hand_written_reply(thread) is False

    def test_our_root_is_still_skipped_once_someone_replies(self, rt):
        """The other half of the docstring's root-skip: a self-review root
        authored by us, with a reply since posted. Scanning the root instead
        of skipping it would misread our own review point as a hand-written
        reply and return True here."""
        thread = ReportThread(id="t1", my_login="me", comments=[
            {"databaseId": 111, "body": "this needs a guard", "author": {"login": "me"}},
            {"databaseId": 222, "body": "Agreed, fixed.", "author": {"login": "kgn"}},
        ])
        assert rt._has_hand_written_reply(thread) is False

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
            fix=_fix(items=[
                ItemOutcome(
                    id="t1", file="a.go", line=7,
                    summary="rename the guard",
                    outcome=FixOutcome.DEFERRED,
                    reason="agent could not auto-fix",
                ),
            ], reviewers={"t1": "kgn"}),
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
        state.fix.fix.commit_status = CommitStatus.PUSHED
        self._run(rt, state, ctx)
        assert state.fix.deferred_issue_id == "I_1"
        on_disk = pr_state.load_state(worktree)
        assert on_disk.fix.fix.commit_status is None
        assert on_disk.fix.deferred_issue_id == ""


class TestDeferralRequiresAChoice:
    """Deferral is a decision. An agent running out of turns is not one."""

    def _state(self, worktree, ids):
        state = PRState(
            identity=PRIdentity(
                repo="owner/repo", branch="b", pr_number=42,
                head_sha="abc1234", worktree_root=str(worktree),
            ),
            fix=_fix(items=[
                ItemOutcome(
                    id=i, file="a.go", line=1,
                    summary=f"item {i}", outcome=FixOutcome.DEFERRED,
                    reason="agent could not auto-fix",
                )
                for i in ids
            ], reviewers={i: "kgn" for i in ids}),
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
        state.fix.fix.items.append(ItemOutcome(id="t2", outcome=FixOutcome.FIXED))
        with pytest.raises(SystemExit):
            self._run(rt, state, self._ctx(worktree), track={"t2"})


class TestUnfiledDeferralsAreNamed:
    """The report has to name exactly the threads nobody asked to file."""

    def _report(self, rt, ids, track):
        state = PRState(
            identity=PRIdentity(repo="owner/repo", branch="b", pr_number=42,
                                head_sha="abc1234", worktree_root=_STATE_WORKTREE),
            fix=_fix(items=[
                ItemOutcome(id=i, outcome=FixOutcome.DEFERRED) for i in ids
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
            fix=_fix(**fix_kw),
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
            state.fix.fix.commit_status = CommitStatus.PUSHED

        with patch.object(rt, "_post_pending_fix_replies", side_effect=mark), \
                patch.object(rt, "_finalize_deferred"), \
                patch.object(rt, "_render_deferred_summary"):
            rt._finish_deferred_work(self._ctx(worktree), PRReport())
        on_disk = pr_state.load_state(worktree / "target")
        assert on_disk.fix.fix.commit_status == CommitStatus.PUSHED

    def test_it_reads_state_from_disk_not_from_the_caller(self, rt, worktree):
        """The fix pass writes its outcomes there; a stale copy would miss them."""
        self._save(worktree, items=[
            ItemOutcome(id="t9", outcome=FixOutcome.DEFERRED, reason="r"),
        ])
        seen = []
        with patch.object(rt, "_post_pending_fix_replies",
                          side_effect=lambda st, *a, **k: seen.extend(st.fix.fix.items)), \
                patch.object(rt, "_finalize_deferred"), \
                patch.object(rt, "_render_deferred_summary"):
            rt._finish_deferred_work(self._ctx(worktree), PRReport())
        assert [t.id for t in seen] == ["t9"]

    def test_no_state_on_disk_is_a_no_op(self, rt, worktree):
        with patch.object(rt, "_post_pending_fix_replies") as replies:
            rt._finish_deferred_work(self._ctx(worktree), PRReport())
        replies.assert_not_called()

    def test_a_held_pr_description_is_delivered_and_the_debt_cleared(
        self, rt, worktree, publishing_on,
    ):
        self._save(worktree, pr_body_pending=True)
        draft = rt._pr_body_draft(worktree)
        draft.parent.mkdir(parents=True, exist_ok=True)
        draft.write_text("A rewritten description.\n")
        with patch.object(rt.pc, "update_pr_body", return_value=True) as update, \
                patch.object(rt, "_post_pending_fix_replies"), \
                patch.object(rt, "_finalize_deferred"), \
                patch.object(rt, "_render_deferred_summary"):
            rt._finish_deferred_work(self._ctx(worktree), PRReport())
        update.assert_called_once_with(
            "owner/repo", 42, "A rewritten description.",
        )
        assert pr_state.load_state(worktree / "target").fix.pr_body_pending is False

    def test_a_description_nobody_drafted_is_not_looked_for(self, rt, worktree):
        self._save(worktree)
        with patch.object(rt, "_deliver_pr_body") as deliver, \
                patch.object(rt, "_post_pending_fix_replies"), \
                patch.object(rt, "_finalize_deferred"), \
                patch.object(rt, "_render_deferred_summary"):
            rt._finish_deferred_work(self._ctx(worktree), PRReport())
        deliver.assert_not_called()

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
        return _make_state(_fix(head_sha="aaaaaaa", items=[
            ItemOutcome(id="t1", file="a.go", line=1,
                        summary="one", outcome=FixOutcome.DEFERRED,
                        reason="agent could not auto-fix"),
        ], reviewers={"t1": "kgn"}))

    def _thread(self, comments, **kw):
        kw.setdefault("state", ThreadState.NEW)
        kw.setdefault("is_resolved", False)
        return ReportThread(id="t1", comments=comments, **kw)

    def test_resolved_thread_is_reclaimed(self, rt):
        state = self._state()
        threads = {"t1": self._thread([{"body": "x"}],
                                      state=ThreadState.RESOLVED, is_resolved=True)}
        assert rt._reconcile_fix_snapshot(state, threads) == 1
        assert state.fix.fix.items[0].outcome == FixOutcome.FIXED

    def test_thread_with_a_fix_reply_is_reclaimed_even_if_unresolved(self, rt):
        """The 13 contradicted threads on the incident PR all looked like this."""
        state = self._state()
        threads = {"t1": self._thread([
            {"body": "please rename this"},
            {"body": "Applied: renamed the guard\n\nFixed in `abc1234`."},
        ])}
        assert rt._reconcile_fix_snapshot(state, threads) == 1
        assert state.fix.fix.items[0].outcome == FixOutcome.FIXED

    def test_genuinely_open_thread_stays_deferred(self, rt):
        state = self._state()
        threads = {"t1": self._thread([{"body": "please rename this"}])}
        assert rt._reconcile_fix_snapshot(state, threads) == 0
        assert state.fix.fix.items[0].outcome == FixOutcome.DEFERRED

    def test_a_deferred_reply_is_not_evidence_of_a_fix(self, rt):
        """Our own prior Deferred: reply must not reclaim the thread."""
        state = self._state()
        threads = {"t1": self._thread([
            {"body": "please rename this"},
            {"body": "Deferred: rename the guard\n\nTracked in ENG-3021."},
        ])}
        assert rt._reconcile_fix_snapshot(state, threads) == 0
        assert state.fix.fix.items[0].outcome == FixOutcome.DEFERRED

    def test_a_thread_absent_from_github_stays_deferred(self, rt):
        """An id nothing on GitHub knows anything about settles nothing.

        Still the right answer for a genuinely unknown thread id. A comment item
        is no longer the same case: it is absent from this map by construction,
        and TestCommentItemsSettleThroughTheirSource covers what does settle it.
        """
        state = self._state()
        assert rt._reconcile_fix_snapshot(state, {}, frozenset({"77"})) == 0
        assert state.fix.fix.items[0].outcome == FixOutcome.DEFERRED

    def test_a_needs_human_thread_settled_by_hand_is_reclaimed(self, rt):
        """The pass handed it to the operator; the operator answering it is the ending."""
        state = _make_state(_fix(head_sha="aaaaaaa", items=[
            ItemOutcome(id="t1", outcome=FixOutcome.NEEDS_HUMAN, reason="contested"),
        ]))
        threads = {"t1": self._thread([{"body": "x"}],
                                      state=ThreadState.RESOLVED, is_resolved=True)}
        assert rt._reconcile_fix_snapshot(state, threads) == 1
        assert state.fix.fix.items[0].outcome == FixOutcome.FIXED

    def test_a_needs_human_thread_still_open_is_left_alone(self, rt):
        state = _make_state(_fix(head_sha="aaaaaaa", items=[
            ItemOutcome(id="t1", outcome=FixOutcome.NEEDS_HUMAN, reason="contested"),
        ]))
        threads = {"t1": self._thread([{"body": "why not do it the other way?"}])}
        assert rt._reconcile_fix_snapshot(state, threads) == 0
        assert state.fix.fix.items[0].outcome == FixOutcome.NEEDS_HUMAN

    def test_a_declined_thread_settled_by_hand_is_reclaimed(self, rt):
        """The agent refused it; the operator doing it anyway outranks that refusal.

        Without this the thread republishes as declined on every later run, so the
        reviewer keeps reading a verdict the tree stopped supporting.
        """
        state = _make_state(_fix(head_sha="aaaaaaa", items=[
            ItemOutcome(id="t1", outcome=FixOutcome.DECLINED,
                          reason="the premise does not hold"),
        ]))
        threads = {"t1": self._thread([{"body": "x"}],
                                      state=ThreadState.RESOLVED, is_resolved=True)}
        assert rt._reconcile_fix_snapshot(state, threads) == 1
        assert state.fix.fix.items[0].outcome == FixOutcome.FIXED
        assert "reconciled" in state.fix.fix.items[0].reason

    def test_a_declined_thread_still_open_is_left_alone(self, rt):
        state = _make_state(_fix(head_sha="aaaaaaa", items=[
            ItemOutcome(id="t1", outcome=FixOutcome.DECLINED,
                          reason="the premise does not hold"),
        ]))
        threads = {"t1": self._thread([{"body": "why not do it the other way?"}])}
        assert rt._reconcile_fix_snapshot(state, threads) == 0
        assert state.fix.fix.items[0].outcome == FixOutcome.DECLINED

    def test_settled_outcomes_are_left_alone(self, rt):
        """Only the open actions are reconcilable — the rest are already decided."""
        settled = (FixOutcome.FIXED, FixOutcome.DISMISSED,
                   FixOutcome.ALREADY_ADDRESSED)
        state = _make_state(_fix(head_sha="aaaaaaa", items=[
            ItemOutcome(id=f"t{i}", outcome=o)
            for i, o in enumerate(settled)
        ]))
        threads = {
            f"t{i}": ReportThread(id=f"t{i}", comments=[{"body": "x"}],
                                  state=ThreadState.RESOLVED, is_resolved=True)
            for i in range(len(settled))
        }
        assert rt._reconcile_fix_snapshot(state, threads) == 0
        assert [t.outcome for t in state.fix.fix.items] == list(settled)

    def test_the_reason_records_why_it_flipped(self, rt):
        state = self._state()
        threads = {"t1": self._thread([{"body": "x"}],
                                      state=ThreadState.RESOLVED, is_resolved=True)}
        rt._reconcile_fix_snapshot(state, threads)
        assert "reconciled" in state.fix.fix.items[0].reason


class TestReconcileRunsBeforeTheWrites:
    """Within one invocation the two must not disagree about the same thread."""

    def test_reconciled_thread_never_reaches_the_tracking_issue(self, rt, worktree):
        pr_state.save_state(worktree / "target", PRState(
            identity=PRIdentity(repo="owner/repo", branch="b", pr_number=42,
                                head_sha="aaaaaaa", worktree_root=str(worktree)),
            fix=_fix(head_sha="aaaaaaa", items=[
                ItemOutcome(id="t1", file="a.go", line=1,
                            summary="one", outcome=FixOutcome.DEFERRED),
            ], reviewers={"t1": "kgn"}),
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
            fix=_fix(head_sha="aaaaaaa", items=[
                ItemOutcome(id="t1", outcome=FixOutcome.DEFERRED),
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
        assert on_disk.fix.fix.items[0].outcome == FixOutcome.FIXED


class TestStaleSnapshotIsAnnounced:
    """A snapshot from a different HEAD is a record of the past, not a plan."""

    def _state(self, worktree, snapshot_sha):
        state = PRState(
            identity=PRIdentity(repo="owner/repo", branch="b", pr_number=42,
                                head_sha=snapshot_sha, worktree_root=str(worktree)),
            fix=_fix(
                head_sha=snapshot_sha,
                items=[ItemOutcome(id="t1", file="a.go", line=7,
                                   summary="rename the guard",
                                   outcome=FixOutcome.DEFERRED,
                                   reason="agent could not auto-fix")],
                reviewers={"t1": "kgn"},
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
        state.fix.fix.head_sha = ""
        pr_state.save_state(worktree / "target", state)
        assert any("(unrecorded)" in w for w in self._warnings(rt, worktree, "aaaaaaa"))

    def test_an_empty_snapshot_has_nothing_to_be_stale_about(self, rt, worktree):
        pr_state.save_state(worktree / "target", PRState(
            identity=PRIdentity(repo="owner/repo", branch="b", pr_number=42,
                                head_sha="aaaaaaa", worktree_root=str(worktree)),
            fix=_fix(),
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
             patch("proc.subprocess.run") as run, \
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


# ── --settle ──────────────────────────────────────────────────────────────


def _hand_fixed(tmp_path, *, pushed=True):
    """A `feature` branch whose one commit changed line 1 of `a.py` by hand.

    Real git rather than a stub, because settle-time attribution is exactly the
    pair of questions no stub can stand in for: which commit changed this line,
    and does the remote have it. The second only has an honest answer when there
    is a remote to ask, which is why the origin is a real bare repo.
    """
    origin = tmp_path / "origin"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(origin)],
                   check=True, capture_output=True, timeout=10)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(origin), str(work)],
                   check=True, capture_output=True, timeout=10)
    git_in(work, "config", "user.email", "t@example.com")
    git_in(work, "config", "user.name", "Test")
    (work / "a.py").write_text("one\ntwo\n")
    git_in(work, "add", "-A")
    git_in(work, "commit", "-q", "--no-verify", "-m", "base")
    git_in(work, "push", "-q", "-u", "origin", "main")
    git_in(work, "checkout", "-q", "-b", "feature")
    (work / "a.py").write_text("ONE\ntwo\n")
    git_in(work, "commit", "-q", "--no-verify", "-am", "fix line one by hand")
    if pushed:
        git_in(work, "push", "-q", "-u", "origin", "feature")
    return SimpleNamespace(path=work, sha=git_out(work, "rev-parse", "HEAD").strip())


class TestSettleFlagParsing:
    def test_settle_is_repeatable(self, rt):
        args = rt._build_parser().parse_args(["--settle", "t1", "--settle", "t2"])
        assert args.settle == ["t1", "t2"]

    def test_settle_records_a_fix_unless_told_otherwise(self, rt):
        """The ending an operator who was offered "fix it" chose."""
        args = rt._build_parser().parse_args(["--settle", "t1"])
        assert args.settle_as == FixOutcome.FIXED.value

    @pytest.mark.parametrize("outcome", [o.value for o in (
        FixOutcome.FIXED, FixOutcome.DISMISSED, FixOutcome.ALREADY_ADDRESSED)])
    def test_every_bucket_the_closeout_can_reply_to_is_offered(self, rt, outcome):
        args = rt._build_parser().parse_args(["--settle", "t1", "--as", outcome])
        assert args.settle_as == outcome

    def test_deferral_is_not_a_settlement(self, rt):
        """--track already files work still owed; --settle records work finished."""
        with pytest.raises(SystemExit):
            rt._build_parser().parse_args(["--settle", "t1", "--as", "deferred"])

    def test_nothing_is_settled_unless_asked(self, rt):
        assert rt._build_parser().parse_args(["--finish"]).settle == []


class TestSettleFlagValidation:
    """A flag the recorded outcome will never read is refused, not ignored."""

    def test_a_dismissal_needs_its_reason(self, rt):
        assert "--reason" in rt._settle_flag_error(FixOutcome.DISMISSED, "", "")

    def test_a_dismissal_that_gives_the_reviewer_something_to_answer_passes(self, rt):
        assert rt._settle_flag_error(FixOutcome.DISMISSED, "not our layer", "") == ""

    @pytest.mark.parametrize("kind", [FixOutcome.FIXED, FixOutcome.ALREADY_ADDRESSED])
    def test_a_reason_no_reply_renders_is_refused(self, rt, kind):
        assert "--reason is only read" in rt._settle_flag_error(kind, "because", "")

    @pytest.mark.parametrize("kind,reason", [
        (FixOutcome.DISMISSED, "not our layer"),
        (FixOutcome.ALREADY_ADDRESSED, ""),
    ])
    def test_a_commit_no_row_cites_is_refused(self, rt, kind, reason):
        assert "--commit is only read" in rt._settle_flag_error(kind, reason, "abc1234")

    def test_a_fix_may_name_the_commit_that_carries_it(self, rt):
        assert rt._settle_flag_error(FixOutcome.FIXED, "", "abc1234") == ""


class TestSettleTargets:
    """Every id is checked before any outcome is written."""

    def _record(self):
        return FixRecord(items=[
            ItemOutcome(id="t1", outcome=FixOutcome.NEEDS_HUMAN),
            ItemOutcome(id="t2", outcome=FixOutcome.FIXED),
            ItemOutcome(id="t3", outcome=FixOutcome.DEFERRED),
        ])

    def test_resolves_the_named_outcomes(self, rt):
        picked = rt._settle_targets(self._record(), ["t3", "t1"])
        assert [o.id for o in picked] == ["t3", "t1"]

    def test_one_unknown_id_settles_none_of_them(self, rt, capsys):
        """"Settled nothing" and "settled the thread you meant" read alike."""
        assert rt._settle_targets(self._record(), ["t1", "typo"]) is None
        assert "typo" in capsys.readouterr().err

    def test_the_error_names_the_threads_still_waiting_on_a_person(self, rt, capsys):
        rt._settle_targets(self._record(), ["typo"])
        err = capsys.readouterr().err
        assert "t1, t3" in err
        assert "t2" not in err

    def test_a_snapshot_with_nothing_left_to_settle_says_so(self, rt, capsys):
        record = FixRecord(items=[ItemOutcome(id="t2", outcome=FixOutcome.FIXED)])
        assert rt._settle_targets(record, ["typo"]) is None
        assert "No thread in the fix snapshot is waiting" in capsys.readouterr().err


class TestRecordSettlement:

    def _outcome(self):
        return ItemOutcome(id="t1", outcome=FixOutcome.NEEDS_HUMAN,
                           reason="too complex to auto-fix")

    def test_a_dismissal_carries_the_operators_own_words(self, rt):
        """Its reply is the one a reviewer may argue with, so it is theirs to write."""
        outcome = self._outcome()
        assert rt._record_settlement(outcome, FixOutcome.DISMISSED, "not our layer", "")
        assert outcome.outcome is FixOutcome.DISMISSED
        assert outcome.reason == "not our layer"

    def test_a_fix_records_where_the_settlement_came_from(self, rt):
        outcome = self._outcome()
        assert rt._record_settlement(outcome, FixOutcome.FIXED, "", "abc1234")
        assert outcome.reason == rt._SETTLED_REASON
        assert outcome.commit_sha == "abc1234"

    def test_saying_the_same_thing_twice_is_a_no_op(self, rt):
        outcome = self._outcome()
        rt._record_settlement(outcome, FixOutcome.FIXED, "", "abc1234")
        assert rt._record_settlement(outcome, FixOutcome.FIXED, "", "abc1234") is False

    def test_a_commit_that_has_since_become_resolvable_is_a_change(self, rt):
        """Reporting it as a no-op would leave a row uncited that can now cite."""
        outcome = self._outcome()
        rt._record_settlement(outcome, FixOutcome.FIXED, "", "")
        assert rt._record_settlement(outcome, FixOutcome.FIXED, "", "abc1234")

    def test_an_earlier_attribution_survives_a_re_settle_that_found_none(self, rt):
        outcome = self._outcome()
        rt._record_settlement(outcome, FixOutcome.FIXED, "", "abc1234")
        rt._record_settlement(outcome, FixOutcome.FIXED, "", "")
        assert outcome.commit_sha == "abc1234"

    @pytest.mark.parametrize("kind,reason", [
        (FixOutcome.DISMISSED, "not our layer"),
        (FixOutcome.ALREADY_ADDRESSED, ""),
    ])
    def test_an_ending_that_cites_no_commit_drops_the_one_it_replaced(
        self, rt, kind, reason,
    ):
        """"Dismissed, fixed in abc1234" is not a state the operator can have meant."""
        outcome = self._outcome()
        rt._record_settlement(outcome, FixOutcome.FIXED, "", "abc1234")
        assert rt._record_settlement(outcome, kind, reason, "")
        assert outcome.commit_sha == ""


class TestResolveSettledCommit:
    """Which commit a hand-landed fix may cite, asked while the answer exists."""

    def _outcome(self, line=1):
        return ItemOutcome(id="t1", outcome=FixOutcome.NEEDS_HUMAN,
                           file="a.py", line=line)

    def test_infers_the_commit_that_changed_the_threads_own_line(self, rt, tmp_path):
        repo = _hand_fixed(tmp_path)
        with patch.object(rt, "_resolve_default_branch", return_value="main"):
            resolved = rt._resolve_settled_commit(repo.path, self._outcome(), "")
        assert resolved.ok
        assert resolved.sha == repo.sha[:7]

    def test_an_unpushed_fix_is_still_recorded_but_cites_nothing(self, rt, tmp_path):
        """A link into a commit the remote never saw is a 404 for the reviewer."""
        repo = _hand_fixed(tmp_path, pushed=False)
        with patch.object(rt, "_resolve_default_branch", return_value="main"):
            resolved = rt._resolve_settled_commit(repo.path, self._outcome(), "")
        assert resolved.ok
        assert resolved.sha == ""

    def test_a_thread_with_no_line_cites_nothing_and_is_no_error(self, rt, tmp_path):
        repo = _hand_fixed(tmp_path)
        with patch.object(rt, "_resolve_default_branch", return_value="main"):
            resolved = rt._resolve_settled_commit(repo.path, self._outcome(line=0), "")
        assert resolved.ok
        assert resolved.sha == ""

    def test_a_commit_this_worktree_does_not_have_stops_the_run(self, rt, tmp_path):
        repo = _hand_fixed(tmp_path)
        resolved = rt._resolve_settled_commit(repo.path, self._outcome(), "nosuchref")
        assert not resolved.ok
        assert "nosuchref" in resolved.error

    def test_an_unpushed_commit_the_operator_named_stops_the_run(self, rt, tmp_path):
        """They asked for this citation, so declining it quietly is the wrong answer."""
        repo = _hand_fixed(tmp_path, pushed=False)
        resolved = rt._resolve_settled_commit(repo.path, self._outcome(), repo.sha)
        assert not resolved.ok
        assert "404" in resolved.error

    def test_the_named_commit_is_taken_over_the_inferred_one(self, rt, tmp_path):
        """The point of --commit: a fix that landed away from the anchored line."""
        repo = _hand_fixed(tmp_path)
        with patch.object(rt, "_find_addressing_commit") as infer:
            resolved = rt._resolve_settled_commit(repo.path, self._outcome(), "HEAD")
        infer.assert_not_called()
        assert resolved.sha == repo.sha[:7]


class TestRunSettle:
    """The ending the fix pass cannot see, told to the CLI rather than to state.json."""

    def _ctx(self, tmp_path):
        return make_ctx(branch="feature", worktree_root=tmp_path / "wt",
                        head_sha="abc1234", target_dir=tmp_path / "target")

    @staticmethod
    def _needs_human(tid="t1"):
        return ItemOutcome(id=tid, outcome=FixOutcome.NEEDS_HUMAN,
                           summary="contested", reason="too complex to auto-fix",
                           file="a.py", line=1)

    def _save(self, ctx, *items):
        pr_state.save_state(ctx.target_dir, _make_state(_fix(list(items))))

    def _reload(self, ctx):
        return pr_state.load_state(ctx.target_dir).fix

    def _resolves_to(self, rt, sha):
        return patch.object(rt, "_resolve_settled_commit",
                            return_value=rt._SettledCommit(sha=sha))

    def test_a_settled_thread_rejoins_the_ordinary_closeout(self, rt, tmp_path):
        ctx = self._ctx(tmp_path)
        self._save(ctx, self._needs_human())
        assert rt._run_settle(ctx, ["t1"], "dismissed", "not our layer", "") == 0
        fix = self._reload(ctx)
        assert fix.fix.items[0].outcome is FixOutcome.DISMISSED
        assert fix.fix.items[0].reason == "not our layer"
        # Both gates --finish reads before it does anything, and both of what
        # puts `⚠ closeout owed` back on `pr status`.
        assert fix.replies_pending
        assert fix.summary_deferred

    def test_a_settled_fix_is_attributed_to_the_commit_carrying_it(self, rt, tmp_path):
        ctx = self._ctx(tmp_path)
        self._save(ctx, self._needs_human())
        with self._resolves_to(rt, "abc1234"):
            assert rt._run_settle(ctx, ["t1"], "fixed", "", "") == 0
        outcome = self._reload(ctx).fix.items[0]
        assert outcome.outcome is FixOutcome.FIXED
        assert outcome.commit_sha == "abc1234"
        assert outcome.reason == rt._SETTLED_REASON

    def test_it_publishes_nothing_and_names_the_step_that_does(self, rt, tmp_path, capsys):
        ctx = self._ctx(tmp_path)
        self._save(ctx, self._needs_human())
        with patch("proc.subprocess.run") as run:
            assert rt._run_settle(ctx, ["t1"], "already_addressed", "", "") == 0
        run.assert_not_called()
        assert rt.pr_comments_fix.CLOSEOUT_COMMAND in capsys.readouterr().err

    def test_a_dismissal_with_no_reason_writes_nothing(self, rt, tmp_path):
        ctx = self._ctx(tmp_path)
        self._save(ctx, self._needs_human())
        assert rt._run_settle(ctx, ["t1"], "dismissed", "", "") == 1
        assert self._reload(ctx).fix.items[0].outcome is FixOutcome.NEEDS_HUMAN

    def test_no_fix_snapshot_names_the_pass_that_makes_one(self, rt, tmp_path, capsys):
        assert rt._run_settle(self._ctx(tmp_path), ["t1"], "fixed", "", "") == 1
        assert "pr comments --fix" in capsys.readouterr().err

    def test_an_unknown_id_leaves_every_other_thread_alone(self, rt, tmp_path):
        ctx = self._ctx(tmp_path)
        self._save(ctx, self._needs_human("t1"), self._needs_human("t2"))
        with self._resolves_to(rt, "abc1234"):
            assert rt._run_settle(ctx, ["t1", "typo"], "fixed", "", "") == 1
        assert [o.outcome for o in self._reload(ctx).fix.items] == \
            [FixOutcome.NEEDS_HUMAN] * 2

    def test_an_unresolvable_commit_discards_the_whole_run(self, rt, tmp_path):
        """Half a run recorded is the state surgery this command exists to replace."""
        ctx = self._ctx(tmp_path)
        self._save(ctx, self._needs_human("t1"), self._needs_human("t2"))
        answers = [rt._SettledCommit(sha="abc1234"),
                   rt._SettledCommit(error="--commit names no commit")]
        with patch.object(rt, "_resolve_settled_commit", side_effect=answers):
            assert rt._run_settle(ctx, ["t1", "t2"], "fixed", "", "") == 1
        assert [o.outcome for o in self._reload(ctx).fix.items] == \
            [FixOutcome.NEEDS_HUMAN] * 2

    def test_recording_the_same_settlement_twice_rewrites_nothing(
        self, rt, tmp_path, capsys,
    ):
        ctx = self._ctx(tmp_path)
        self._save(ctx, self._needs_human())
        rt._run_settle(ctx, ["t1"], "dismissed", "not our layer", "")
        state_file = ctx.target_dir / pr_state.STATE_FILE
        before = state_file.read_text()
        capsys.readouterr()
        assert rt._run_settle(ctx, ["t1"], "dismissed", "not our layer", "") == 0
        assert "already recorded as dismissed" in capsys.readouterr().err
        assert state_file.read_text() == before

    def test_a_different_ending_replaces_the_first_and_says_which(
        self, rt, tmp_path, capsys,
    ):
        ctx = self._ctx(tmp_path)
        self._save(ctx, self._needs_human())
        rt._run_settle(ctx, ["t1"], "dismissed", "not our layer", "")
        capsys.readouterr()
        with self._resolves_to(rt, "abc1234"):
            assert rt._run_settle(ctx, ["t1"], "fixed", "", "") == 0
        assert "(was dismissed)" in capsys.readouterr().err
        assert self._reload(ctx).fix.items[0].outcome is FixOutcome.FIXED

    def test_a_fix_with_no_pushed_commit_says_how_its_row_will_read(
        self, rt, tmp_path, capsys,
    ):
        ctx = self._ctx(tmp_path)
        self._save(ctx, self._needs_human())
        with self._resolves_to(rt, ""):
            assert rt._run_settle(ctx, ["t1"], "fixed", "", "") == 0
        err = capsys.readouterr().err
        assert rt._RECONCILED_STATUS_TEXT in err
        assert "--commit" in err


class TestSettleIsNotAPublishingPhase:
    """Recording is its own step, the way --post gates every other write."""

    @pytest.mark.parametrize("flag", ["--post", "--finish", "--fix", "--triage"])
    def test_it_refuses_to_run_alongside_a_phase_that_publishes(self, rt, capsys, flag):
        argv = ["review-threads", "--settle", "t1", flag]
        with patch.object(sys, "argv", argv), \
             patch.object(rt.pr_context, "resolve") as resolve, \
             pytest.raises(SystemExit) as exc:
            rt.main()
        assert exc.value.code == 1
        resolve.assert_not_called()
        assert flag in capsys.readouterr().err

    def test_the_conflict_named_is_the_one_that_was_typed(self, rt, capsys):
        """--fix widens itself into --triage; the operator did not type --triage."""
        with patch.object(sys, "argv", ["review-threads", "--settle", "t1", "--fix"]), \
             patch.object(rt.pr_context, "resolve"), \
             pytest.raises(SystemExit):
            rt.main()
        err = capsys.readouterr().err
        assert "--fix" in err
        assert "--triage" not in err

    def test_it_does_not_announce_a_draft_run_it_is_not(self, rt, capsys):
        """Nothing here was ever going to be posted, drafted or otherwise."""
        with patch.object(sys, "argv", ["review-threads", "--settle", "t1"]), \
             patch.object(rt.pr_context, "resolve", return_value=make_ctx()), \
             patch.object(rt, "_run_settle", return_value=0) as settle, \
             pytest.raises(SystemExit) as exc:
            rt.main()
        assert exc.value.code == 0
        assert "Draft mode" not in capsys.readouterr().err
        assert settle.call_args[0][1] == ["t1"]


class TestSettledRowsAreNotCreditedToThePass:
    """The fix pass did not land this work, so its commit must not be named for it."""

    def test_an_uncitable_settled_row_says_the_work_was_handled(self, rt):
        entry = CommentItem(id="t1", summary="fix it", file="a.py", line=1,
                            reason=rt._SETTLED_REASON)
        cp = rt.CommitPushResult("aaa1111", "pushed", "")
        cell = rt._fixed_status_for(entry, cp, "owner/repo")
        assert cell == rt._RECONCILED_STATUS_TEXT
        assert cell != rt._UNATTRIBUTED_STATUS_TEXT

    def test_a_settled_row_that_resolved_a_commit_cites_that_one(self, rt):
        entry = CommentItem(id="t1", summary="fix it", file="a.py", line=1,
                            reason=rt._SETTLED_REASON, commit_sha="bbb2222")
        cp = rt.CommitPushResult("aaa1111", "pushed", "")
        cell = rt._fixed_status_for(entry, cp, "owner/repo")
        assert "bbb2222" in cell
        assert "aaa1111" not in cell


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
        fixed = [CommentItem(id="t1", summary="fix it", file="src/app.py",
                             commit_sha="def5678")]
        threads_by_id = {"t1": _standing_reply_thread(
            body="Suggestion reviewed and determined to be inapplicable: old reason",
        )}
        with patch("pr_comments.post_thread_reply") as post, \
             patch("pr_comments.patch_thread_reply", return_value=True) as edit:
            count = rt._post_fix_replies(
                fixed, threads_by_id, "owner/repo", 42,
                rt.CommitPushResult("def5678", "pushed", ""),
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
        fixed = [CommentItem(id="t1", summary="fix it", file="src/app.py",
                             commit_sha="def5678")]
        threads_by_id = {"t1": ReportThread(id="t1", comments=[{"databaseId": 111}])}
        with patch("pr_comments.post_thread_reply", return_value=True) as post:
            rt._post_fix_replies(fixed, threads_by_id, "owner/repo", 42,
                                 rt.CommitPushResult("def5678", "pushed", ""))
        body = post.call_args[0][3]
        assert "owner/repo/blob/def5678/src/app.py" in body
        # No line anchor: the fix just moved the lines around it.
        assert "#L" not in body

    def test_deferred_reply_links_the_unchanged_code(self, rt, tmp_path):
        deferred = [CommentItem(id="t1", summary="fix it", file="src/app.py", line=12,
                                read_sha="cafe123")]
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
                            evidence_file="src/app.py", evidence_line=2,
                            read_sha="cafe123")
        link = rt._code_link(entry, "owner/repo", "cafe123", tmp_path)
        assert "blob/cafe123/src/app.py#L2" in link

    def test_code_link_falls_back_when_the_citation_is_not_in_the_tree(self, rt, tmp_path):
        entry = CommentItem(id="t1", file="other.py", line=7,
                            evidence_file="src/gone.py", evidence_line=2,
                            read_sha="cafe123")
        link = rt._code_link(entry, "owner/repo", "cafe123", tmp_path)
        assert "blob/cafe123/other.py#L7" in link

    def test_code_link_drops_an_anchor_it_cannot_vouch_for(self, rt, tmp_path):
        """A line with no recorded tree is a number, not a location."""
        entry = CommentItem(id="t1", file="other.py", line=7)
        link = rt._code_link(entry, "owner/repo", "cafe123", tmp_path)
        assert link == "[`other.py`](https://github.com/owner/repo/blob/cafe123/other.py)"

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
            "t1": ReportThread(id="t1", state=ThreadState.NEW, is_resolved=False),
            "t2": ReportThread(id="t2", state=ThreadState.ADDRESSED, is_resolved=False),
        }
        with patch("pr_comments.resolve_thread", return_value=True) as mock_resolve:
            resolved = rt._resolve_fixed_threads(fixed, threads_by_id)
        assert resolved == [ThreadState.NEW, ThreadState.ADDRESSED]
        assert mock_resolve.call_count == 2

    def test_skips_already_resolved(self, rt):
        fixed = [CommentItem(id="t1")]
        threads_by_id = {"t1": ReportThread(id="t1", is_resolved=True)}
        with patch("pr_comments.resolve_thread") as mock_resolve:
            resolved = rt._resolve_fixed_threads(fixed, threads_by_id)
        assert resolved == []
        mock_resolve.assert_not_called()

    def test_skips_an_entry_absent_from_threads_by_id(self, rt):
        """A synthetic comment id (ic-…/rb-…) is not a resolvable review thread.

        Regression: these used to fall through to an unconditional
        `resolve_thread`, spending a GraphQL mutation per comment item on an id
        the API cannot resolve. It failed silently, so nothing surfaced it.
        """
        fixed = [CommentItem(id="ic-123")]
        with patch("pr_comments.resolve_thread") as mock_resolve:
            resolved = rt._resolve_fixed_threads(fixed, {})
        assert resolved == []
        mock_resolve.assert_not_called()

    def test_reports_only_successful_resolves(self, rt):
        """The buckets feed the tally, so a refused mutation must not appear.

        A drafted run refuses every one of them, which is how a run that
        published nothing is kept from moving the counts.
        """
        fixed = [CommentItem(id="t1"), CommentItem(id="t2")]
        threads_by_id = {
            "t1": ReportThread(id="t1", state=ThreadState.NEW),
            "t2": ReportThread(id="t2", state=ThreadState.ADDRESSED),
        }
        with patch("pr_comments.resolve_thread", side_effect=[True, False]):
            resolved = rt._resolve_fixed_threads(fixed, threads_by_id)
        assert resolved == [ThreadState.NEW]

    def test_a_second_pass_over_the_same_threads_moves_nothing(self, rt):
        """The buckets feed the tally, so resolving twice must not count twice.

        A combined --fix --finish run whose commit was held resolves the
        already-addressed bucket in the fix pass and then drains that same
        bucket in the closeout, both off the report's thread objects. Nothing
        re-fetches in between, so only the write-back marks them resolved.
        """
        fixed = [CommentItem(id="t1"), CommentItem(id="t2")]
        threads_by_id = {
            "t1": ReportThread(id="t1", state=ThreadState.NEW),
            "t2": ReportThread(id="t2", state=ThreadState.ADDRESSED),
        }
        with patch("pr_comments.resolve_thread", return_value=True) as mock_resolve:
            first = rt._resolve_fixed_threads(fixed, threads_by_id)
            second = rt._resolve_fixed_threads(fixed, threads_by_id)
        assert first == [ThreadState.NEW, ThreadState.ADDRESSED]
        assert second == []
        assert mock_resolve.call_count == 2

    def test_a_refused_resolve_stays_open_for_the_next_pass(self, rt):
        """Only a mutation that landed marks the thread resolved.

        A drafted run refuses every one of them, and the closeout that follows
        with --post has to find the bucket still owed.
        """
        fixed = [CommentItem(id="t1")]
        threads_by_id = {"t1": ReportThread(id="t1", state=ThreadState.NEW)}
        with patch("pr_comments.resolve_thread", return_value=False):
            rt._resolve_fixed_threads(fixed, threads_by_id)
        assert threads_by_id["t1"].is_resolved is False


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


# ── _diff_context_for_file ─────────────────────────────────────────────────

class TestDiffContextForFile:
    def test_empty_file_path(self, rt):
        assert rt._diff_context_for_file("", Path("/wt")) == ""

    @patch("git_client.run")
    def test_returns_diff(self, mock_run, rt):
        mock_run.return_value = _git_ran(0, stdout="+ added line\n- removed line\n")
        result = rt._diff_context_for_file("src/foo.go", Path("/wt"))
        assert "```diff" in result
        assert "+ added line" in result

    @patch("git_client.run")
    def test_truncates_long_diff(self, mock_run, rt):
        long_diff = "\n".join(f"+ line {i}" for i in range(200))
        mock_run.return_value = _git_ran(0, stdout=long_diff)
        result = rt._diff_context_for_file("src/foo.go", Path("/wt"))
        assert "more lines" in result

    @patch("git_client.run")
    def test_git_failure_returns_empty(self, mock_run, rt):
        mock_run.return_value = _git_ran(1)
        assert rt._diff_context_for_file("src/foo.go", Path("/wt")) == ""

    @patch("git_client.run")
    def test_an_omitted_branch_is_resolved_not_assumed_to_be_main(self, mock_run, rt):
        """The signature used to default to the literal "main".

        Every production caller passes the resolved trunk, so the literal only
        ever fired for one that forgot — and then silently, as an empty diff
        from a ref the repository does not have.
        """
        mock_run.return_value = _git_ran(0, stdout="+ added line\n")
        with patch.object(rt, "_resolve_default_branch", return_value="trunk"):
            rt._diff_context_for_file("src/foo.go", Path("/wt"))

        assert "origin/trunk" in mock_run.call_args[0]


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

    def test_the_record_carries_the_already_addressed_outcome(self, rt):
        entry = self._entry("already_addressed")
        record = rt._build_fix_record(
            {FixOutcome.ALREADY_ADDRESSED: [entry]},
        )
        assert len(record.items) == 1
        assert record.items[0].outcome == FixOutcome.ALREADY_ADDRESSED

    def test_an_outcome_the_caller_did_not_name_records_nothing(self, rt):
        """The mapping is the whole vocabulary of a call — nothing is implied."""
        assert rt._build_fix_record({}).items == []

    def test_a_declined_thread_is_recorded_as_declined(self, rt):
        """Not folded into needs-human: the state file keeps the two apart."""
        entry = CommentItem(id="t9", reviewer="kgn", reason="premise does not hold")
        record = rt._build_fix_record({FixOutcome.DECLINED: [entry]})
        assert record.items[0].outcome == FixOutcome.DECLINED
        assert record.items[0].reason == "premise does not hold"

    def test_the_reviewer_is_kept_beside_the_record_not_on_it(self, rt):
        """`ItemOutcome` is every domain's; a login is only the comment pass's."""
        by_outcome = {FixOutcome.DECLINED: [
            CommentItem(id="t9", reviewer="kgn"),
            CommentItem(id="t8"),
        ]}
        assert rt._reviewers_for(by_outcome) == {"t9": "kgn"}

    def test_only_fixed_outcomes_carry_the_pass_commit(self, rt):
        """A deferred thread was not fixed by this commit — or any."""
        fixed = self._entry("valid")
        deferred = CommentItem(id="t2", file="b.py", line=2, reviewer="kgn",
                               summary="too complex")
        record = rt._build_fix_record({
            FixOutcome.FIXED: [fixed],
            FixOutcome.DEFERRED: [deferred],
        }, commit_sha="deadbee")
        by_id = {o.id: o.commit_sha for o in record.items}
        assert by_id == {"t1": "deadbee", "t2": ""}

    def test_no_commit_leaves_the_sha_empty(self, rt):
        record = rt._build_fix_record(
            {FixOutcome.FIXED: [self._entry("valid")]}, commit_sha="",
        )
        assert record.items[0].commit_sha == ""


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

        def mock_run(*cmd, **kwargs):
            if "push" in cmd:
                pushes.append(cmd)
            if "commit" in cmd:
                commits.append(cmd)
            return _git_ran(0, stdout="abc1234\n")

        with patch.object(rt.agent_invoke.ai_backend, "invoke_fix",
                          side_effect=_tick_every_fix(tmp_path)), \
             patch.object(rt, "_diff_context_for_file", return_value=""), \
             patch.object(rt, "_find_and_update_main_worktree", return_value=None), \
             patch.object(rt, "_resolve_default_branch", return_value="main"), \
             patch.object(rt, "_persist_fix_state"), \
             patch.object(rt.git_client, "run",
                          side_effect=_answering_the_owner(mock_run)), \
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


class TestAnAlreadyAddressedDraftRoundOwesItsSummary:
    """The reported drop, driven through `_run_comment_fix` itself.

    Every thread settled before the pass reached it, so there is nothing to fix
    and the round takes the early return, which had a narrower rule for what it
    owed than the pass that commits. The draft printed a full table and recorded
    that it owed nothing, so `--finish --post` returned at its
    `summary_deferred` guard and the published comment kept the previous
    round's rows. Asserted end to end because that is where it is invisible:
    the closeout exits 0 and `pr status` reports a clean PR.
    """

    def _run(self, rt, tmp_path):
        threads = [CommentItem(
            id="t1", file="f.go", line=10, reviewer="kgn", summary="t1 summary",
            classification="actionable_suggestion",
            verification="already_addressed",
            complexity="low", state=ThreadState.NEW,
        )]
        report = PRReport(
            repo="owner/repo", pr_number=1,
            threads=[ReportThread(id="t1", file="f.go", line=10,
                                  comments=[{"databaseId": 100}])],
        )
        ctx = SimpleNamespace(
            repo="owner/repo", branch="b", pr_number=1, head_sha="aaa1111",
            target_dir=tmp_path,
        )
        with patch.object(rt, "_diff_context_for_file", return_value=""), \
             patch.object(rt, "_find_and_update_main_worktree", return_value=None), \
             patch.object(rt, "_resolve_default_branch", return_value="main"), \
             patch.object(rt, "_persist_fix_state"), \
             patch.object(rt.git_client, "run",
                          side_effect=_answering_the_owner(
                              lambda *c, **kw: _git_ran(0, stdout="abc1234\n"))), \
             patch("pr_comments.post_thread_reply", return_value=True), \
             patch("pr_comments.resolve_thread", return_value=True):
            return rt._run_comment_fix(
                TriageResult(threads=threads), report, tmp_path, ctx,
            )

    def test_the_draft_leaves_its_table_owed(self, rt, tmp_path):
        result = self._run(rt, tmp_path)
        assert [t.id for t in result.already_addressed] == ["t1"]
        # Neither of the two buckets the old rule named, so the round it
        # described looked like a round with nothing to say.
        assert not result.fixed
        assert not result.dismissed
        assert result.summary_url is None
        assert result.summary_deferred is True

    def test_a_published_round_owes_nothing(self, rt, tmp_path, publishing_on):
        """The other half: once the table is out, it is not owed again."""
        with patch("pr_comments.post_issue_comment", return_value="https://u"):
            result = self._run(rt, tmp_path)
        assert result.summary_url == "https://u"
        assert result.summary_deferred is False


class TestARoundWhoseOnlyContentIsAnUnreadComment:
    """No thread settled either way, and still a table to publish.

    An unseen issue or review-body comment is a row the summary renders, so a
    round that settled no thread at all can still owe one. The gate on whether
    to attempt the post named the four thread buckets instead of asking
    `_summary_has_content`, so this round was recorded as owing a summary it
    never tried to publish — recoverable on the next `--finish`, a cycle late.
    """

    def _run(self, rt, tmp_path):
        report = PRReport(
            repo="owner/repo", pr_number=1,
            issue_comments=[{"id": "c1", "author": "kgn", "body": "one thought",
                             "seen": False}],
        )
        ctx = SimpleNamespace(
            repo="owner/repo", branch="b", pr_number=1, head_sha="aaa1111",
            target_dir=tmp_path,
        )
        with patch.object(rt, "_find_and_update_main_worktree", return_value=None), \
             patch.object(rt, "_resolve_default_branch", return_value="main"), \
             patch.object(rt, "_persist_fix_state"), \
             patch.object(rt.git_client, "run",
                          side_effect=_answering_the_owner(
                              lambda *c, **kw: _git_ran(0, stdout="abc1234\n"))):
            return rt._run_comment_fix(TriageResult(), report, tmp_path, ctx)

    def test_the_round_publishes_its_table(self, rt, tmp_path, publishing_on):
        with patch("pr_comments.post_issue_comment", return_value="https://u"):
            result = self._run(rt, tmp_path)
        assert result.summary_url == "https://u"
        assert result.summary_deferred is False

    def test_a_draft_still_owes_it(self, rt, tmp_path):
        result = self._run(rt, tmp_path)
        assert result.summary_url is None
        assert result.summary_deferred is True


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
        fix = _fix(
            items=[
                ItemOutcome(id="t1", summary="drop the guard", file="f.go", line=10,
                              outcome=FixOutcome.ALREADY_ADDRESSED),
                ItemOutcome(id="t2", summary="complex", file="c.go", line=3,
                              outcome=FixOutcome.DEFERRED),
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
        fix = _fix(
            items=[ItemOutcome(id="t1", summary="fix", file="a.py", line=1,
                                   outcome=FixOutcome.FIXED)],
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

    def _state_fix(self, **overrides):
        defaults = dict(
            items=[ItemOutcome(id="t2", summary="round two work", file="new.go",
                               line=1, outcome=FixOutcome.FIXED)],
            commit_status="no_changes", summary_deferred=True,
        )
        defaults.update(overrides)
        return _fix(**defaults)

    def _render(self, rt, published):
        state = _make_state(self._state_fix())
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
        state = _make_state(self._state_fix())
        with patch.object(pr_comments, "find_marker_comments",
                          return_value=pr_comments.MarkerHistory(found=False)), \
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
        state = _make_state(self._state_fix())
        with _published(_published_summary(rt, ROUND_ONE_ROW)) as find, \
                patch("pr_comments.post_issue_comment", return_value="https://url") as post:
            rt._render_deferred_summary(state, PRReport(), "owner/repo", 1, {})
        find.assert_called_once()
        assert post.call_args.kwargs["existing"] == pr_comments.MarkerComment(
            True, 11, _published_summary(rt, ROUND_ONE_ROW),
            url="https://github.com/owner/repo/pull/1#issuecomment-11")


# ── rows a human rewrote, that local state can account for ─────────────────


_GENERATED_ACTION_CELL = "Fixed in [`9f2e1a0`](https://github.com/owner/repo/commit/9f2e1a0)"
_HAND_WRITTEN_ACTION_CELL = (
    "Superseded — @kgn reproduced it independently and the next review round "
    "accepted the root cause"
)
HAND_EDITED_ROW = ROUND_ONE_ROW.replace(
    _GENERATED_ACTION_CELL, _HAND_WRITTEN_ACTION_CELL)


class TestGeneratedActionCell:
    """A cell no generated opening claims was written by a person."""

    def test_every_fix_status_the_renderer_writes_is_recognised(self, rt):
        """Assert on what the builders emit, not on a transcribed copy — a
        wording change there must not silently freeze the rows it renders."""
        for status in CommitStatus:
            cp = rt.CommitPushResult("9f2e1a0", status, "")
            assert rt._is_generated_action(rt._fixed_status_text(cp, "owner/repo")) is True
            bare = rt.CommitPushResult(None, status, "")
            assert rt._is_generated_action(rt._fixed_status_text(bare, "owner/repo")) is True

    def test_every_human_reason_prose_is_recognised(self, rt):
        for reason in rt.HumanReason:
            assert rt._is_generated_action(reason.prose) is True

    @pytest.mark.parametrize("cell", [
        "Already addressed",
        "Dismissed (invalid)",
        "Deferred",
        "Deferred → ENG-1",
        "Deferred → [ENG-1](https://linear.app/i/ENG-1)",
        "Addressed outside the fix pass",
    ])
    def test_the_literal_cells_are_recognised(self, rt, cell):
        assert rt._is_generated_action(cell) is True

    def test_a_retired_wording_is_still_recognised(self, rt):
        """A published summary outlives the builder that wrote its cells, so an
        opening no builder produces any more still opens rows on live PRs."""
        assert rt._is_generated_action("Added to the PR description (no commit)") is True

    @pytest.mark.parametrize("cell", [
        "",
        _HAND_WRITTEN_ACTION_CELL,
        "Withdrawn by the reviewer",
    ])
    def test_anything_else_reads_as_hand_written(self, rt, cell):
        assert rt._is_generated_action(cell) is False

    def test_only_a_row_the_render_covers_is_held(self, rt):
        """A hand-written row the render does not cover is the carry-forward
        case, and must not be reported twice."""
        published = _published_summary(rt, HAND_EDITED_ROW)
        fresh = _published_summary(
            rt,
            "| [new work](https://github.com/owner/repo/pull/1#discussion_r222) "
            "| @kgn | `new.go:1` | Fixed in `bbbbbbb` |")
        assert rt._hand_written_rows([published], fresh) == []
        assert rt._carried_over_rows(published, fresh) == [HAND_EDITED_ROW]


class TestActionCellOutcome:
    """Which outcome a published Action cell states, under the wording drift.

    One outcome is written several ways across rounds — a fix reported with a
    commit one round and without one the next — so the cell's text cannot stand
    in for the outcome it reports. Only the outcome tells a round that changed a
    row from one that merely re-rendered it.
    """

    def test_every_fix_status_reads_as_fixed(self, rt):
        for status in CommitStatus:
            cp = rt.CommitPushResult("9f2e1a0", status, "")
            assert rt._action_outcome(
                rt._fixed_status_text(cp, "owner/repo")) is FixOutcome.FIXED
            bare = rt.CommitPushResult(None, status, "")
            assert rt._action_outcome(
                rt._fixed_status_text(bare, "owner/repo")) is FixOutcome.FIXED

    def test_a_fix_reported_two_ways_reads_the_same(self, rt):
        """The false positive a cell comparison produces: same outcome, two
        wordings, because one round resolved a commit and the next did not."""
        cited = rt._fixed_in_cell("9f2e1a0", "owner/repo")
        assert rt._action_outcome(cited) is rt._action_outcome(
            rt._UNATTRIBUTED_STATUS_TEXT)

    def test_every_human_reason_prose_reads_as_open(self, rt):
        for reason in rt.HumanReason:
            assert rt._action_outcome(reason.prose) is FixOutcome.NEEDS_HUMAN

    @pytest.mark.parametrize("cell,outcome", [
        ("Already addressed", FixOutcome.ALREADY_ADDRESSED),
        ("Dismissed (invalid)", FixOutcome.DISMISSED),
        ("Deferred", FixOutcome.DEFERRED),
        ("Deferred → ENG-1", FixOutcome.DEFERRED),
        ("Addressed outside the fix pass", FixOutcome.FIXED),
        ("Added to the PR description (no commit)", FixOutcome.FIXED),
    ])
    def test_the_literal_cells_read_as_their_outcome(self, rt, cell, outcome):
        assert rt._action_outcome(cell) is outcome

    @pytest.mark.parametrize("cell", ["", _HAND_WRITTEN_ACTION_CELL])
    def test_a_cell_we_did_not_write_states_no_outcome(self, rt, cell):
        """None is what keeps a hand-written cell from reading as a round's own
        re-classification — the row is the hand-held path's business, not this."""
        assert rt._action_outcome(cell) is None

    def test_no_opening_opens_another_under_a_different_outcome(self, rt):
        """What lets `_action_outcome` scan `_ACTION_OUTCOMES` in any order. Add
        an opening that another one opens and the cell reports whichever the
        scan reached first, so the row is restated every round or frozen holding
        the outcome it left — with no wording anywhere to show which."""
        overlaps = [
            f"{opening!r} ({outcome}) opens {longer!r} ({other})"
            for opening, outcome in rt._ACTION_OUTCOMES.items()
            for longer, other in rt._ACTION_OUTCOMES.items()
            if longer != opening and longer.startswith(opening) and other is not outcome
        ]
        assert overlaps == []

    def test_a_row_with_no_action_cell_is_re_rendered(self, rt):
        """A shape this renderer no longer produces is repaired, not frozen."""
        stub = "| [drop the guard](https://github.com/owner/repo/pull/1#discussion_r111) |"
        fresh = _published_summary(rt, ROUND_ONE_ROW)
        assert rt._hand_written_rows([_published_summary(rt, stub)], fresh) == []

    def test_the_held_row_names_both_halves(self, rt):
        fresh = _published_summary(
            rt, ROUND_ONE_ROW.replace(_GENERATED_ACTION_CELL, "Conflicting reviewer feedback"))
        held = rt._hand_written_rows([_published_summary(rt, HAND_EDITED_ROW)], fresh)
        assert [h.key for h in held] == ["#discussion_r111"]
        assert rt._row_action_cell(held[0].published) == _HAND_WRITTEN_ACTION_CELL
        assert rt._row_action_cell(held[0].replaced_by) == "Conflicting reviewer feedback"

    def test_an_edit_on_an_older_comment_is_still_found(self, rt):
        """Once a round posts its own comment, the edited cell is on one no
        later round targets — reading only the newest hands the row back."""
        fresh = _published_summary(rt, ROUND_ONE_ROW)
        held = rt._hand_written_rows(
            [_published_summary(rt, HAND_EDITED_ROW),
             _published_summary(rt, "| [other](https://x/pull/1#discussion_r9) "
                                    "| @kgn | `b.go:1` | Fixed |")],
            fresh)
        assert [rt._row_action_cell(h.published) for h in held] == [
            _HAND_WRITTEN_ACTION_CELL]

    def test_the_newest_comment_wins_the_row(self, rt):
        """Restoring a generated cell on the newest comment hands the row back."""
        fresh = _published_summary(rt, ROUND_ONE_ROW)
        assert rt._hand_written_rows(
            [_published_summary(rt, HAND_EDITED_ROW),
             _published_summary(rt, ROUND_ONE_ROW)], fresh) == []

    def test_a_later_hand_edit_supersedes_the_generated_cell(self, rt):
        """The mirror case — proves the newest-wins rule is not just first-wins."""
        fresh = _published_summary(rt, ROUND_ONE_ROW)
        held = rt._hand_written_rows(
            [_published_summary(rt, ROUND_ONE_ROW),
             _published_summary(rt, HAND_EDITED_ROW)], fresh)
        assert [h.published for h in held] == [HAND_EDITED_ROW]


class TestHandEditedCellsSurviveTheRender:
    """State regaining coverage of a thread used to be what destroyed the edit."""

    def _threads(self):
        return {"t1": ReportThread(id="t1", comments=[{"databaseId": 111}])}

    def _state_fix(self, **overrides):
        defaults = dict(
            items=[ItemOutcome(
                id="t1", summary="drop the guard", file="old.go", line=4,
                outcome=FixOutcome.NEEDS_HUMAN, reason="conflicting")],
            commit_status="no_changes", summary_deferred=True,
        )
        defaults.update(overrides)
        return _fix(**defaults)

    def _render(self, rt, published):
        state = _make_state(self._state_fix())
        with _published(published), \
                patch("pr_comments.post_issue_comment", return_value="https://url") as post:
            rt._render_deferred_summary(state, PRReport(), "owner/repo", 1, self._threads())
        return post.call_args[0][2]

    def test_the_hand_written_cell_is_republished(self, rt):
        body = self._render(rt, _published_summary(rt, HAND_EDITED_ROW))
        assert _HAND_WRITTEN_ACTION_CELL in body
        assert "Conflicting reviewer feedback" not in body

    def test_the_row_is_not_duplicated(self, rt):
        body = self._render(rt, _published_summary(rt, HAND_EDITED_ROW))
        assert body.count("drop the guard") == 1

    def test_the_header_count_follows_the_cell(self, rt):
        """A row reading `Superseded` under a header reading `1 need discussion`
        reopens the question the hand edit closed."""
        body = self._render(rt, _published_summary(rt, HAND_EDITED_ROW))
        assert "need discussion" not in body
        assert "1 hand-written" in body

    def test_the_reader_is_told_why_the_row_was_not_re_rendered(self, rt):
        body = self._render(rt, _published_summary(rt, HAND_EDITED_ROW))
        assert "written by hand" in body

    def test_holding_a_row_is_idempotent(self, rt):
        once = self._render(rt, _published_summary(rt, HAND_EDITED_ROW))
        assert self._render(rt, once) == once

    def test_the_run_names_the_row_and_what_it_would_have_said(self, rt):
        """An overwritten hand edit was silent — the warning listed only the
        rows the run kept, never the one it replaced."""
        with patch.object(rt.log, "warn") as warn:
            self._render(rt, _published_summary(rt, HAND_EDITED_ROW))
        held = next(c[0][0] for c in warn.call_args_list if "hand-written" in c[0][0])
        assert "#discussion_r111" in held
        assert _HAND_WRITTEN_ACTION_CELL in held
        assert "Conflicting reviewer feedback" in held

    def test_a_generated_cell_is_still_re_rendered(self, rt):
        """Pairs with the cases above — proves those assertions are not vacuous."""
        body = self._render(rt, _published_summary(rt, ROUND_ONE_ROW))
        assert "Conflicting reviewer feedback" in body
        assert "1 need discussion" in body
        assert "hand-written" not in body

    def test_the_fix_pass_upsert_holds_the_cell_too(self, rt):
        """--fix edits the same comment, so it can destroy the edit the same way."""
        cp = rt.CommitPushResult("bbbbbbb", CommitStatus.PUSHED, "")
        with _published(_published_summary(rt, HAND_EDITED_ROW)), \
                patch("pr_comments.post_issue_comment", return_value="https://url") as post:
            rt._post_fix_summary(
                [CommentItem(id="t1", summary="drop the guard", file="old.go", line=4)],
                [], [], cp, "owner/repo", 1, self._threads(),
            )
        body = post.call_args[0][2]
        assert _HAND_WRITTEN_ACTION_CELL in body
        assert "1 hand-written" in body
        assert "1 fixed" not in body


# ── items triage cut out of one top-level comment ──────────────────────────


# Three points raised in one issue comment, so every row triage renders for
# them links back to the one permalink that comment has.
_SIBLING_ITEMS = [
    CommentItem(id="ic-900-0", summary="drop the guard", reviewer="kgn",
                file="old.go", line=4),
    CommentItem(id="ic-900-1", summary="name the timeout", reviewer="kgn",
                file="net.go", line=12),
    CommentItem(id="ic-900-2", summary="log the retry", reviewer="kgn",
                file="net.go", line=31),
]
_ROUND_ONE_ITEM_CELL = "Fixed in [`aaaaaaa`](https://github.com/owner/repo/commit/aaaaaaa)"


def _sibling_rows(rt, hand_written: str = "") -> list[str]:
    """The three item rows as an earlier round published them.

    ``hand_written`` names the item whose Action cell a person rewrote after
    that round posted.
    """
    return [
        rt._build_row(
            item,
            _HAND_WRITTEN_ACTION_CELL if item.id == hand_written
            else _ROUND_ONE_ITEM_CELL,
            {}, "owner/repo", 1, "aaaaaaa",
        )
        for item in _SIBLING_ITEMS
    ]


class TestSiblingItemsKeyApart:
    """One anchor, N rows: the anchor names the source, not the row."""

    def _row(self, rt, item, status="Fixed", sha="aaaaaaa"):
        return rt._build_row(item, status, {}, "owner/repo", 1, sha)

    def test_each_sibling_gets_its_own_key(self, rt):
        keys = {rt._summary_row_key(self._row(rt, i)) for i in _SIBLING_ITEMS}
        assert len(keys) == len(_SIBLING_ITEMS)

    def test_the_anchor_is_still_half_the_key(self, rt):
        """Two comments raising the same point are two rows, not one."""
        elsewhere = CommentItem(id="ic-901-0", summary="drop the guard",
                                reviewer="kgn", file="old.go", line=4)
        assert (rt._summary_row_key(self._row(rt, _SIBLING_ITEMS[0]))
                != rt._summary_row_key(self._row(rt, elsewhere)))

    def test_a_sibling_keys_the_same_across_rounds(self, rt):
        first = self._row(rt, _SIBLING_ITEMS[0], status="Deferred", sha="aaaaaaa")
        later = self._row(rt, _SIBLING_ITEMS[0], status="Fixed in `bbbbbbb`",
                          sha="ccccccc")
        assert rt._summary_row_key(first) == rt._summary_row_key(later)

    def test_a_thread_row_keys_on_its_anchor_alone(self, rt):
        """A thread renders one row, so its summary must stay out of the key —
        a reworded summary is the same finding, not a new one."""
        reworded = ROUND_ONE_ROW.replace("drop the guard", "remove the guard")
        assert rt._summary_row_key(reworded) == rt._summary_row_key(ROUND_ONE_ROW)


class TestEveryItemReachesTheTable:
    """A held row used to stand in for its siblings, which then vanished."""

    def _render(self, rt, published=""):
        cp = rt.CommitPushResult("bbbbbbb", CommitStatus.PUSHED, "")
        with _published(published), \
                patch("pr_comments.post_issue_comment", return_value="https://url") as post:
            rt._post_fix_summary(
                list(_SIBLING_ITEMS), [], [], cp, "owner/repo", 1, {})
        return post.call_args[0][2]

    def test_three_items_render_three_rows(self, rt):
        body = self._render(rt, _published_summary(rt, *_sibling_rows(rt)))
        assert len(rt._summary_table_rows(body)) == len(_SIBLING_ITEMS)

    def test_a_held_row_stands_in_for_its_own_row_only(self, rt):
        published = _published_summary(rt, *_sibling_rows(rt, hand_written="ic-900-1"))
        body = self._render(rt, published)
        assert body.count(_HAND_WRITTEN_ACTION_CELL) == 1
        for item in _SIBLING_ITEMS:
            assert body.count(f"[{item.summary}]") == 1

    def test_the_counts_match_the_rows(self, rt):
        published = _published_summary(rt, *_sibling_rows(rt, hand_written="ic-900-1"))
        body = self._render(rt, published)
        assert len(rt._summary_table_rows(body)) == len(_SIBLING_ITEMS)
        assert f"**{len(_SIBLING_ITEMS) - 1} fixed**" in body
        assert "1 hand-written" in body

    def test_nothing_published_counts_every_row_as_fixed(self, rt):
        body = self._render(rt)
        assert len(rt._summary_table_rows(body)) == len(_SIBLING_ITEMS)
        assert f"**{len(_SIBLING_ITEMS)} fixed**" in body
        assert "hand-written" not in body

    def test_holding_one_sibling_is_idempotent(self, rt):
        published = _published_summary(rt, *_sibling_rows(rt, hand_written="ic-900-1"))
        once = self._render(rt, published)
        assert self._render(rt, once) == once

    def test_a_sibling_state_lost_is_carried_rather_than_dropped(self, rt):
        """One sibling in the fresh render used to account for all of them."""
        published = _published_summary(rt, *_sibling_rows(rt))
        cp = rt.CommitPushResult("bbbbbbb", CommitStatus.PUSHED, "")
        with _published(published), \
                patch("pr_comments.post_issue_comment", return_value="https://url") as post:
            rt._post_fix_summary([_SIBLING_ITEMS[0]], [], [], cp, "owner/repo", 1, {})
        body = post.call_args[0][2]
        assert len(rt._summary_table_rows(body)) == 3
        assert "2 carried over" in body


# ── reposting a summary the PR has moved past ──────────────────────────────


_SUMMARY_POSTED_AT = "2026-01-02T00:00:00Z"
_AFTER_THE_SUMMARY = "2026-01-03T00:00:00Z"
_BEFORE_THE_SUMMARY = "2026-01-01T00:00:00Z"
_ROUND_ONE_URL = "https://github.com/owner/repo/pull/1#issuecomment-11"


def _round_one_marker(rt, *rows: str, **overrides):
    """The summary a first round published, spoken over since it went up."""
    import pr_comments
    defaults = dict(
        found=True, comment_id=11, body=_published_summary(rt, *rows),
        created_at=_SUMMARY_POSTED_AT, newest_other_at=_AFTER_THE_SUMMARY,
        url=_ROUND_ONE_URL,
    )
    defaults.update(overrides)
    return pr_comments.MarkerComment(**defaults)


_ROUND_TWO_OUTCOME = ItemOutcome(
    id="t2", summary="round two work", file="new.go", line=1,
    outcome=FixOutcome.FIXED,
)
# What round one recorded, as a state file that still carries it into round two.
_ROUND_ONE_OUTCOME = ItemOutcome(
    id="t1", summary="drop the guard", file="old.go", line=4,
    outcome=FixOutcome.FIXED,
)


def _repost_over(rt, *rows: str, outcomes=(), threads=None, report=None):
    """Render a second round's summary over a first round that was answered.

    Returns the body posted. `rows` is what the first round published, and
    `outcomes` what local state still holds beside round two's own fixed `t2`.
    """
    items = [_ROUND_TWO_OUTCOME, *outcomes]
    state = _make_state(_fix(
        items=items, reviewers={o.id: "kgn" for o in items},
        commit_status="no_changes", summary_deferred=True,
    ))
    with _lookup_returns(_round_one_marker(rt, *rows)), \
            patch("pr_comments.post_issue_comment", return_value="https://url") as post:
        rt._render_deferred_summary(
            state, report or PRReport(), "owner/repo", 1, threads or {})
    assert "marker" not in post.call_args.kwargs
    return post.call_args[0][2]


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
            rt._publish_summary("owner/repo", 1,
                                lambda carried_over, scope, chain: "body",
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

    def test_the_fresh_comment_describes_its_own_round(self, rt):
        """The earlier round stays where it was posted, and is linked, not restated."""
        body = _repost_over(rt, ROUND_ONE_ROW)
        assert rt._SUMMARY_MARKER in body
        assert "round two work" in body
        assert "drop the guard" not in body
        assert f"**Earlier rounds:** [1]({_ROUND_ONE_URL})" in body


def _reviewed_thread(created_at, login="kgn", thread_id="t1"):
    """A review thread whose only comment `login` left at `created_at`."""
    return {thread_id: ReportThread(
        id=thread_id, my_login="me",
        comments=[{"databaseId": 111, "author": {"login": login},
                   "createdAt": created_at}],
    )}


_OPEN_OUTCOME = dataclasses.replace(
    _ROUND_ONE_OUTCOME, outcome=FixOutcome.NEEDS_HUMAN, reason="conflicting")


def _published_open_row(rt) -> str:
    """`ROUND_ONE_ROW` as the round that left the thread open published it.

    Built from `HumanReason` rather than transcribed, so the cell this round
    renders and the one the record holds cannot drift apart — the whole of what
    tells "still open, and quiet" from "re-classified this round".
    """
    return ROUND_ONE_ROW.replace(
        _GENERATED_ACTION_CELL, rt.HumanReason.prose_for(_OPEN_OUTCOME.reason))


class TestASummaryDescribesItsOwnRound:
    """A repost restating every round the PR ever had is complete and unreadable."""

    def test_a_settled_quiet_thread_is_left_where_it_was_published(self, rt):
        body = _repost_over(
            rt, ROUND_ONE_ROW, outcomes=[_ROUND_ONE_OUTCOME],
            threads=_reviewed_thread(_BEFORE_THE_SUMMARY),
        )
        assert "drop the guard" not in body
        assert "1 thread settled in an earlier round" in body
        assert "**1 fixed**" in body

    def test_a_thread_spoken_on_since_comes_back(self, rt):
        """The point of the scoping is the round's own activity, not silence."""
        body = _repost_over(
            rt, ROUND_ONE_ROW, outcomes=[_ROUND_ONE_OUTCOME],
            threads=_reviewed_thread(_AFTER_THE_SUMMARY),
        )
        assert "drop the guard" in body
        assert "settled in an earlier round" not in body

    def test_our_own_reply_is_not_activity(self, rt):
        """The fix pass replies before it publishes — counting those never settles."""
        body = _repost_over(
            rt, ROUND_ONE_ROW, outcomes=[_ROUND_ONE_OUTCOME],
            threads=_reviewed_thread(_AFTER_THE_SUMMARY, login="me"),
        )
        assert "drop the guard" not in body

    def test_an_open_thread_quiet_since_is_left_where_it_was_published(self, rt):
        """#1017 — #714 exempted every open thread from the scoping, which at
        forty-three of them rebuilds the document the scoping exists to prevent.
        One row among forty-three is no easier to find than one round back."""
        body = _repost_over(
            rt, _published_open_row(rt), outcomes=[_OPEN_OUTCOME],
            threads=_reviewed_thread(_BEFORE_THE_SUMMARY),
        )
        assert "drop the guard" not in body
        assert "1 thread still open" in body
        assert "settled in an earlier round" not in body

    def test_an_open_thread_spoken_on_since_comes_back(self, rt):
        """The rule is the round's own activity — open threads get no exemption
        from it, and no different treatment under it."""
        body = _repost_over(
            rt, _published_open_row(rt), outcomes=[_OPEN_OUTCOME],
            threads=_reviewed_thread(_AFTER_THE_SUMMARY),
        )
        assert "drop the guard" in body
        assert "1 need discussion" in body
        assert "still open" not in body

    def test_a_newly_open_thread_is_written_whatever_else_is_dropped(self, rt):
        """#712 outranks the scoping for a row no comment holds, and an open
        question reaching a reader for the first time is that row."""
        fresh = dataclasses.replace(
            _OPEN_OUTCOME, id="t9", summary="never published")
        body = _repost_over(
            rt, _published_open_row(rt), outcomes=[_OPEN_OUTCOME, fresh],
            threads=_reviewed_thread(_BEFORE_THE_SUMMARY),
        )
        assert "never published" in body
        assert "drop the guard" not in body
        assert "1 thread still open" in body

    def test_an_entry_the_run_cannot_date_reads_as_quiet(self, rt):
        """An item keeps its source anchor whether or not the report still
        carries the comment, so it is the entry this reading decides. A settled
        thread stops being fetched, so undatable is the ordinary shape of the
        row being scoped out, not a signal that it is new."""
        item = ItemOutcome(id="ic-900-0", summary="drop the guard",
                           file="old.go", line=4, outcome=FixOutcome.FIXED)
        body = _repost_over(rt, *_sibling_rows(rt), outcomes=[item])
        assert "drop the guard" not in body
        assert "1 thread settled in an earlier round" in body

    def test_an_undatable_entry_no_comment_holds_is_still_written(self, rt):
        """#712 outranks that reading: absent from the record, so never dropped."""
        item = ItemOutcome(id="ic-901-0", summary="never published",
                           file="old.go", line=4, outcome=FixOutcome.FIXED)
        body = _repost_over(rt, *_sibling_rows(rt), outcomes=[item])
        assert "never published" in body

    def test_a_row_no_summary_comment_holds_is_written(self, rt):
        """#712 read against the set: absent from the record, so never dropped."""
        body = _repost_over(
            rt, ROUND_ONE_ROW,
            outcomes=[dataclasses.replace(_ROUND_ONE_OUTCOME, id="t9",
                                          summary="never published")],
            threads=_reviewed_thread(_BEFORE_THE_SUMMARY),
        )
        assert "never published" in body

    def test_the_footer_links_every_earlier_summary(self, rt):
        import pr_comments
        second = pr_comments.MarkerComment(
            True, 12, _published_summary(rt, ROUND_ONE_ROW),
            created_at=_SUMMARY_POSTED_AT, newest_other_at=_AFTER_THE_SUMMARY,
            url="https://github.com/owner/repo/pull/1#issuecomment-12",
        )
        state = _make_state(_fix(
            items=[_ROUND_TWO_OUTCOME], commit_status="no_changes",
            summary_deferred=True))
        with _lookup_returns(_round_one_marker(rt), second), \
                patch("pr_comments.post_issue_comment", return_value="https://url") as post:
            rt._render_deferred_summary(state, PRReport(), "owner/repo", 1, {})
        assert (f"**Earlier rounds:** [1]({_ROUND_ONE_URL}) · "
                f"[2]({second.url})") in post.call_args[0][2]

    def test_a_first_summary_has_no_footer(self, rt):
        cp = rt.CommitPushResult("bbbbbbb", CommitStatus.PUSHED, "")
        with patch("pr_comments.post_issue_comment", return_value="https://url") as post:
            rt._post_fix_summary(
                [CommentItem(id="t2", summary="round two work", file="new.go", line=1)],
                [], [], cp, "owner/repo", 1, {},
            )
        assert "Earlier rounds" not in post.call_args[0][2]


class TestARoundWritesWhatItChanged:
    """A row this round re-classified is this round's business, however quiet.

    `covers` reads reviewer activity, and a round changing a row's outcome is
    not that: nobody has to speak for a deferred thread to become a fixed one.
    Left to the activity test alone, the new outcome reaches no summary at all
    and the record's newest word on the row is the outcome it has replaced.
    """

    def test_a_reclassified_row_is_written_though_nobody_spoke(self, rt):
        body = _repost_over(
            rt, _published_open_row(rt), outcomes=[_ROUND_ONE_OUTCOME],
            threads=_reviewed_thread(_BEFORE_THE_SUMMARY),
        )
        assert "drop the guard" in body
        assert "**2 fixed**" in body

    def test_an_unchanged_row_is_still_left_where_it_was_published(self, rt):
        """The guard is the outcome, not the round: one wording per outcome is
        not something the renderer promises, so a re-worded cell is not news."""
        body = _repost_over(
            rt, ROUND_ONE_ROW, outcomes=[_ROUND_ONE_OUTCOME],
            threads=_reviewed_thread(_BEFORE_THE_SUMMARY),
        )
        assert "drop the guard" not in body
        assert "1 thread settled in an earlier round" in body

    def test_a_hand_written_cell_is_not_a_reclassification(self, rt):
        """A person's wording states no outcome, so it cannot differ from one.
        Reading it as a change would restate the row every round — the ratchet
        this issue removes, rebuilt on the one path a human controls."""
        body = _repost_over(
            rt, HAND_EDITED_ROW, outcomes=[_ROUND_ONE_OUTCOME],
            threads=_reviewed_thread(_BEFORE_THE_SUMMARY),
        )
        assert "drop the guard" not in body
        assert _HAND_WRITTEN_ACTION_CELL not in body


def _edit_over_chain(rt, earlier_rows, target_rows, outcomes=(), threads=None):
    """Edit the newest of two summary comments, with `earlier_rows` below it.

    The target postdates the first comment and nothing has been said under it,
    so the round edits in place — the path where dropping a row the target
    alone holds would delete it from the record rather than defer to a link.
    """
    import pr_comments
    earlier = _round_one_marker(rt, *earlier_rows, newest_other_at=_BEFORE_THE_SUMMARY)
    target = pr_comments.MarkerComment(
        True, 12, _published_summary(rt, *target_rows),
        created_at=_AFTER_THE_SUMMARY, newest_other_at=_BEFORE_THE_SUMMARY,
        url="https://github.com/owner/repo/pull/1#issuecomment-12",
    )
    state = _make_state(_fix(
        items=[*outcomes], commit_status="no_changes", summary_deferred=True))
    with _lookup_returns(earlier, target), \
            patch("pr_comments.post_issue_comment", return_value="https://url") as post:
        rt._render_deferred_summary(state, PRReport(), "owner/repo", 1, threads or {})
    assert post.call_args.kwargs["marker"] == rt._SUMMARY_MARKER
    return post.call_args[0][2]


class TestAnEditKeepsOnlyWhatItAloneHolds:
    """`target_keys` protected every row on the edited comment, which made it a
    ratchet: once a row reached a summary, every later edit of that comment
    re-rendered it whatever else carried it. Dropping a row an earlier comment
    also holds deletes nothing — the reader still finds it one link back."""

    def test_a_row_an_earlier_comment_also_holds_is_dropped(self, rt):
        body = _edit_over_chain(
            rt, [ROUND_ONE_ROW], [ROUND_ONE_ROW], outcomes=[_ROUND_ONE_OUTCOME],
            threads=_reviewed_thread(_BEFORE_THE_SUMMARY),
        )
        assert "drop the guard" not in body
        assert "1 thread settled in an earlier round" in body

    def test_the_dropped_row_is_not_handed_back_by_the_carry_forward(self, rt):
        """The two gates ask one question. Scoping a row out of the body while
        carry-forward reads it as a round local state lost puts it straight
        back, verbatim, and reports it as carried."""
        body = _edit_over_chain(
            rt, [ROUND_ONE_ROW], [ROUND_ONE_ROW], outcomes=[_ROUND_ONE_OUTCOME],
            threads=_reviewed_thread(_BEFORE_THE_SUMMARY),
        )
        assert "carried over" not in body

    def test_a_row_the_target_alone_holds_is_still_re_rendered(self, rt):
        """Dropping it here deletes it: an edit rewrites the body wholesale and
        no earlier comment carries it."""
        body = _edit_over_chain(
            rt, [], [ROUND_ONE_ROW], outcomes=[_ROUND_ONE_OUTCOME],
            threads=_reviewed_thread(_BEFORE_THE_SUMMARY),
        )
        assert "drop the guard" in body
        assert "settled in an earlier round" not in body


class TestAnEditKeepsItsTargetWhole:
    """An edit replaces the comment, so scoping it would delete the round."""

    def _edit(self, rt, *rows, outcomes=(), threads=None):
        state = _make_state(_fix(
            items=[*outcomes], commit_status="no_changes", summary_deferred=True))
        marker = _round_one_marker(rt, *rows, newest_other_at=_BEFORE_THE_SUMMARY)
        with _lookup_returns(marker), \
                patch("pr_comments.post_issue_comment", return_value="https://url") as post:
            rt._render_deferred_summary(
                state, PRReport(), "owner/repo", 1, threads or {})
        assert post.call_args.kwargs["marker"] == rt._SUMMARY_MARKER
        return post.call_args[0][2]

    def test_a_quiet_row_the_target_holds_is_re_rendered(self, rt):
        """The --finish pass updating a --fix round's own status is this case."""
        body = self._edit(
            rt, ROUND_ONE_ROW, outcomes=[_ROUND_ONE_OUTCOME],
            threads=_reviewed_thread(_BEFORE_THE_SUMMARY),
        )
        assert "drop the guard" in body
        assert "settled in an earlier round" not in body
        assert "carried over" not in body

    def test_the_edited_comment_does_not_link_itself(self, rt):
        assert "Earlier rounds" not in self._edit(rt, ROUND_ONE_ROW)


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


# ── per-line addressing commits ─────────────────────────────────────────────


class TestAddressingCommitIsPerLine:
    """Two threads on one file must not both be sent to the same commit.

    The already-addressed reply asked which branch commit last touched the
    *file*, so on a file two reviewers had both commented on, whichever commit
    landed last was cited to both of them — including the reviewer whose lines
    that commit never touched. The lookup is over the thread's line now, which
    is the only mechanism that can answer for a change no fix pass committed.
    """

    @staticmethod
    def _git(wt, *args):
        git_out(wt, *args)

    def _sha(self, wt, rev):
        return git_out(wt, "rev-parse", rev).strip()

    @pytest.fixture
    def branch(self, worktree):
        """A branch off origin/main with one commit per line of `a.py`."""
        # Empty hooks dir for the same reason the fix-pass fixture has one: a
        # global core.hooksPath would run the developer's own pre-commit here.
        hooks = worktree / ".git" / "empty-hooks"
        hooks.mkdir()
        self._git(worktree, "config", "user.email", "test@example.com")
        self._git(worktree, "config", "user.name", "Test")
        self._git(worktree, "config", "commit.gpgsign", "false")
        self._git(worktree, "config", "core.hooksPath", str(hooks))
        (worktree / "a.py").write_text("one\ntwo\n")
        self._git(worktree, "add", "-A")
        self._git(worktree, "commit", "-qm", "base")
        self._git(worktree, "update-ref", "refs/remotes/origin/main", "HEAD")
        (worktree / "a.py").write_text("ONE\ntwo\n")
        self._git(worktree, "commit", "-qam", "address line one")
        first = self._sha(worktree, "HEAD")
        (worktree / "a.py").write_text("ONE\nTWO\n")
        self._git(worktree, "commit", "-qam", "address line two")
        return SimpleNamespace(path=worktree, first=first,
                               second=self._sha(worktree, "HEAD"))

    def test_each_line_resolves_to_the_commit_that_changed_it(self, rt, branch):
        with patch.object(rt, "_resolve_default_branch", return_value="main"):
            assert rt._find_addressing_commit(branch.path, "a.py", 1) == branch.first
            assert rt._find_addressing_commit(branch.path, "a.py", 2) == branch.second

    def test_a_thread_with_no_line_claims_no_commit(self, rt, branch):
        """A file-wide thread has no line history to read, so it cites nothing."""
        with patch.object(rt, "_resolve_default_branch", return_value="main"):
            assert rt._find_addressing_commit(branch.path, "a.py", 0) is None

    def test_a_line_past_the_end_of_the_file_claims_no_commit(self, rt, branch):
        """git refuses the range rather than answering — nothing is invented."""
        with patch.object(rt, "_resolve_default_branch", return_value="main"):
            assert rt._find_addressing_commit(branch.path, "a.py", 99) is None

    def test_two_threads_on_one_file_cite_different_commits(self, rt, branch):
        entries = [
            CommentItem(id="t1", summary="line one", file="a.py", line=1),
            CommentItem(id="t2", summary="line two", file="a.py", line=2),
        ]
        threads_by_id = {
            "t1": ReportThread(id="t1", comments=[{"databaseId": 111}]),
            "t2": ReportThread(id="t2", comments=[{"databaseId": 222}]),
        }
        with patch.object(rt, "_resolve_default_branch", return_value="main"), \
             patch("pr_comments.post_thread_reply", return_value=True) as post:
            rt._post_already_addressed_replies(
                entries, threads_by_id, "owner/repo", 42, branch.path,
            )
        # The "Addressed in" line only: the blob permalink beside it pins HEAD
        # for both threads, because where to read the code is the same question
        # for both and when it became true is not.
        cited = {
            call[0][2]: [ln for ln in call[0][3].splitlines()
                         if ln.startswith("Addressed in")]
            for call in post.call_args_list
        }
        assert cited[111] == [
            f"Addressed in [`{branch.first[:7]}`]"
            f"(https://github.com/owner/repo/commit/{branch.first}).",
        ]
        assert cited[222] == [
            f"Addressed in [`{branch.second[:7]}`]"
            f"(https://github.com/owner/repo/commit/{branch.second}).",
        ]


class TestLineAnchorsAreTreeScoped:
    """A line read in one tree is not a location in another.

    Every reply pinned its permalink to the tree the fix commit produced while
    numbering it from a line read before that commit, so a reviewer following
    the link landed on whatever code inherited the number rather than on the
    code they had commented on.
    """

    @staticmethod
    def _git(wt, *args):
        git_out(wt, *args)

    def _sha(self, wt, rev):
        return git_out(wt, "rev-parse", rev).strip()

    @pytest.fixture
    def trees(self, worktree):
        """Two commits: the second rewrites `moved.py` and leaves `still.py`."""
        hooks = worktree / ".git" / "empty-hooks"
        hooks.mkdir()
        self._git(worktree, "config", "user.email", "test@example.com")
        self._git(worktree, "config", "user.name", "Test")
        self._git(worktree, "config", "commit.gpgsign", "false")
        self._git(worktree, "config", "core.hooksPath", str(hooks))
        (worktree / "moved.py").write_text("guard\ncheck\n")
        (worktree / "still.py").write_text("one\ntwo\n")
        self._git(worktree, "add", "-A")
        self._git(worktree, "commit", "-qm", "reviewed")
        read = self._sha(worktree, "HEAD")
        (worktree / "moved.py").write_text("import os\nguard\ncheck\n")
        self._git(worktree, "commit", "-qam", "fix pass")
        return SimpleNamespace(path=worktree, read=read,
                               fixed=self._sha(worktree, "HEAD"))

    def test_a_line_in_an_untouched_file_keeps_its_anchor(self, rt, trees):
        entry = CommentItem(id="t1", file="still.py", line=2, read_sha=trees.read)
        assert rt._anchored_line(
            entry, "still.py", 2, trees.fixed, trees.path) == 2

    def test_a_line_in_a_rewritten_file_loses_its_anchor(self, rt, trees):
        entry = CommentItem(id="t1", file="moved.py", line=1, read_sha=trees.read)
        assert rt._anchored_line(
            entry, "moved.py", 1, trees.fixed, trees.path) == 0

    def test_the_same_tree_needs_no_comparison(self, rt, trees):
        """The triage replies go out before the fix commit, so this is the common case."""
        entry = CommentItem(id="t1", file="moved.py", line=1, read_sha=trees.read)
        assert rt._anchored_line(
            entry, "moved.py", 1, trees.read, trees.path) == 1

    def test_an_unrecorded_tree_loses_the_anchor(self, rt, trees):
        entry = CommentItem(id="t1", file="still.py", line=2)
        assert rt._anchored_line(
            entry, "still.py", 2, trees.fixed, trees.path) == 0

    def test_a_reply_drafted_after_the_fix_commit_links_the_file(self, rt, trees):
        """End to end: the shape that sent reviewers to unrelated code."""
        entry = CommentItem(id="t1", file="moved.py", line=1, read_sha=trees.read)
        link = rt._code_link(entry, "owner/repo", trees.fixed, trees.path)
        assert link.endswith(f"/blob/{trees.fixed}/moved.py)")
        assert "#L" not in link


# ── addressed in response, or genuinely already addressed ──────────────────


_THE_REVIEW_COMMENT = "2025-01-01T00:00:00Z"
_BEFORE_THE_REVIEW = "2020-01-01T00:00:00+0000"
_AFTER_THE_REVIEW = "2030-01-01T00:00:00+0000"


class TestAddressedInResponseFraming:
    """A reviewer we acted for must not be told their comment needed no action.

    Triage reads current HEAD, which holds the fixes an earlier round of the
    same cycle landed, so a thread the pass fixed comes back `already_addressed`
    on the next run. The verdict answers "does the code do this now?" correctly;
    the flat "Already addressed" answers "was your comment moot?" wrongly.
    """

    @staticmethod
    def _git(wt, *args, when=""):
        env = dict(os.environ)
        if when:
            env["GIT_AUTHOR_DATE"] = when
            env["GIT_COMMITTER_DATE"] = when
        run_checked(["git", "-C", str(wt), *args], env=env)

    def _sha(self, wt, rev):
        return git_out(wt, "rev-parse", rev).strip()

    @pytest.fixture
    def branch(self, worktree):
        """A branch with one commit dated before the review and one after it."""
        hooks = worktree / ".git" / "empty-hooks"
        hooks.mkdir()
        self._git(worktree, "config", "user.email", "test@example.com")
        self._git(worktree, "config", "user.name", "Test")
        self._git(worktree, "config", "commit.gpgsign", "false")
        self._git(worktree, "config", "core.hooksPath", str(hooks))
        (worktree / "a.py").write_text("one\ntwo\n")
        self._git(worktree, "add", "-A")
        self._git(worktree, "commit", "-qm", "base")
        self._git(worktree, "update-ref", "refs/remotes/origin/main", "HEAD")
        (worktree / "a.py").write_text("ONE\ntwo\n")
        self._git(worktree, "commit", "-qam", "line one, long before the review",
                  when=_BEFORE_THE_REVIEW)
        before = self._sha(worktree, "HEAD")
        (worktree / "a.py").write_text("ONE\nTWO\n")
        self._git(worktree, "commit", "-qam", "line two, in response to the review",
                  when=_AFTER_THE_REVIEW)
        return SimpleNamespace(path=worktree, before=before,
                               after=self._sha(worktree, "HEAD"))

    @staticmethod
    def _thread(tid, database_id):
        return ReportThread(id=tid, comments=[
            {"databaseId": database_id, "createdAt": _THE_REVIEW_COMMENT},
        ])

    def _reply_body(self, rt, entry, thread, wt_path, **kwargs):
        with patch.object(rt, "_resolve_default_branch", return_value="main"), \
             patch("pr_comments.post_thread_reply", return_value=True) as post:
            rt._post_already_addressed_replies(
                [entry], {entry.id: thread}, "owner/repo", 42, wt_path, **kwargs,
            )
        return post.call_args[0][3]

    def test_a_commit_after_the_review_reads_as_addressed_in_response(self, rt, branch):
        body = self._reply_body(
            rt, CommentItem(id="t2", summary="rename it", file="a.py", line=2),
            self._thread("t2", 222), branch.path,
        )
        assert body.startswith("Applied: rename it")
        assert "Already addressed" not in body
        assert f"Fixed in [`{branch.after[:7]}`]" in body

    def test_code_predating_the_comment_stays_already_addressed(self, rt, branch):
        """The genuine case: the reviewer's point was true before they made it."""
        body = self._reply_body(
            rt, CommentItem(id="t1", summary="use the helper", file="a.py", line=1),
            self._thread("t1", 111), branch.path,
        )
        assert body.startswith("Already addressed: use the helper")
        assert "Applied:" not in body
        assert f"Addressed in [`{branch.before[:7]}`]" in body

    def test_an_undated_thread_keeps_the_pre_existing_reading(self, rt, branch):
        """Claiming credit is the assertion that needs evidence, not the absence."""
        body = self._reply_body(
            rt, CommentItem(id="t2", summary="rename it", file="a.py", line=2),
            ReportThread(id="t2", comments=[{"databaseId": 222}]), branch.path,
        )
        assert body.startswith("Already addressed: rename it")

    def test_a_fix_the_resolver_cannot_cite_still_reads_as_a_fix(self, rt, branch):
        """#827's caller: a FIXED entry whose commit a hook rejected lands here.

        The pass acted on the thread — that is what put the entry in `fixed` —
        so the reply says so even though no commit can be named for it. The one
        commit the branch offers for that line predates the comment and cannot
        be what carried a fix made after it, so nothing is cited.
        """
        entry = CommentItem(id="t1", summary="use the helper", file="a.py", line=1)
        cp = rt.CommitPushResult(None, CommitStatus.NO_CHANGES, "")
        with patch.object(rt, "_resolve_default_branch", return_value="main"), \
             patch("pr_comments.post_thread_reply", return_value=True) as post:
            rt._reply_to_fixed(
                [entry], {"t1": self._thread("t1", 111)}, "owner/repo", 42,
                cp, branch.path,
            )
        body = post.call_args[0][3]
        assert body.startswith("Applied: use the helper")
        assert "Already addressed" not in body
        assert branch.before[:7] not in body

    def _summary(self, rt, entry, thread, wt_path):
        cp = rt.CommitPushResult(None, CommitStatus.NO_CHANGES, "")
        with patch.object(rt, "_resolve_default_branch", return_value="main"):
            return rt._build_summary_body(
                [], [], [], cp, "owner/repo", 42, {entry.id: thread},
                already_addressed=[entry], wt_path=wt_path,
            )

    def test_the_summary_row_reports_a_responsive_fix_as_fixed(self, rt, branch):
        body = self._summary(
            rt, CommentItem(id="t2", summary="rename it", file="a.py", line=2),
            self._thread("t2", 222), branch.path,
        )
        assert f"Fixed in [`{branch.after}`]" in body
        assert "Already addressed" not in body
        assert "**1 fixed**" in body

    def test_the_summary_row_keeps_already_addressed_for_older_code(self, rt, branch):
        body = self._summary(
            rt, CommentItem(id="t1", summary="use the helper", file="a.py", line=1),
            self._thread("t1", 111), branch.path,
        )
        assert "Already addressed" in body
        assert "1 already addressed" in body
        assert "fixed" not in body


# ── per-row attribution when work landed by hand across commits ────────────


def _git_at(wt, *args, when=""):
    env = dict(os.environ)
    if when:
        env["GIT_AUTHOR_DATE"] = when
        env["GIT_COMMITTER_DATE"] = when
    run_checked(["git", "-C", str(wt), *args], env=env)


def _rev(wt, rev):
    return git_out(wt, "rev-parse", rev).strip()


@pytest.fixture
def hand_landed_branch(worktree):
    """Two hand-landed commits after the review, each at its own line.

    `snapshot` is where the fix pass left the branch, so both commits are
    outside it and `_reconciled_commit` reads UNDETERMINED — the state the
    per-row resolver exists for, and the one a row the pass never landed must
    not be resolved through. `stale` predates the review and is the control: a
    line whose only commit is older cannot be what carried a fix made later.
    """
    hooks = worktree / ".git" / "empty-hooks"
    hooks.mkdir()
    _git_at(worktree, "config", "user.email", "test@example.com")
    _git_at(worktree, "config", "user.name", "Test")
    _git_at(worktree, "config", "commit.gpgsign", "false")
    _git_at(worktree, "config", "core.hooksPath", str(hooks))
    (worktree / "a.py").write_text("one\ntwo\nthree\n")
    _git_at(worktree, "add", "-A")
    _git_at(worktree, "commit", "-qm", "base")
    _git_at(worktree, "update-ref", "refs/remotes/origin/main", "HEAD")
    (worktree / "a.py").write_text("one\ntwo\nTHREE\n")
    _git_at(worktree, "commit", "-qam", "line three, before the review",
            when=_BEFORE_THE_REVIEW)
    stale = _rev(worktree, "HEAD")
    snapshot = _rev(worktree, "HEAD")
    (worktree / "a.py").write_text("ONE\ntwo\nTHREE\n")
    _git_at(worktree, "commit", "-qam", "line one, by hand", when=_AFTER_THE_REVIEW)
    first = _rev(worktree, "HEAD")
    (worktree / "a.py").write_text("ONE\nTWO\nTHREE\n")
    _git_at(worktree, "commit", "-qam", "line two, by hand", when=_AFTER_THE_REVIEW)
    return SimpleNamespace(
        path=worktree, snapshot=snapshot, stale=stale,
        first=first, second=_rev(worktree, "HEAD"),
    )


def _reviewed(tid, database_id):
    return ReportThread(id=tid, comments=[
        {"databaseId": database_id, "createdAt": _THE_REVIEW_COMMENT},
    ])


def _undetermined_pass(rt, branch):
    """The pass's own view of the branch: nothing recorded, HEAD moved on."""
    record = FixRecord(
        commit_status=CommitStatus.NO_CHANGES, head_sha=branch.snapshot,
    )
    with patch.object(rt.push, "holds", return_value=True):
        cp = rt._reconciled_commit(record, CommitStatus.NO_CHANGES, branch.path)
    assert cp.claim is rt.CommitClaim.UNDETERMINED, "fixture must reach the gap"
    return cp


def _row(tid, line, summary, **kw):
    return CommentItem(
        id=tid, file="a.py", line=line, reviewer="kgn", summary=summary, **kw,
    )


def _summary_over(rt, branch, entries, threads):
    cp = _undetermined_pass(rt, branch)
    with patch.object(rt, "_resolve_default_branch", return_value="main"):
        return rt._build_summary_body(
            entries, [], [], cp, "owner/repo", 42, threads, wt_path=branch.path,
        )


class TestRowsResolveTheirOwnCommitAcrossHandLandedWork:
    """Several commits outside the pass is a fact about the branch, not the row.

    Reconciliation could only ask "did this branch move?", so more than one
    commit left it with nothing per-row to say and it declined for every row at
    once — correctly, since the SHA it would otherwise stamp is HEAD, picked for
    having no relationship to any of them. The line a thread is anchored to is a
    different question with a different answer, and it is answerable: the commit
    that changed that line after the reviewer asked is evidence about that
    thread.

    The reply path has resolved rows this way since #820. Only the table
    declined, so one PR carried a thread reply reading "Fixed in `abc1234`"
    beside a summary row reading "Fix applied (commit not recorded)".
    """

    def test_each_row_cites_the_commit_that_carried_it(self, rt, hand_landed_branch):
        """The defect: both rows rendered "commit not recorded" together."""
        branch = hand_landed_branch
        body = _summary_over(
            rt,
            branch,
            [_row("t1", 1, "first point"), _row("t2", 2, "second point")],
            {"t1": _reviewed("t1", 111), "t2": _reviewed("t2", 222)},
        )
        assert f"Fixed in [`{branch.first}`]" in body
        assert f"Fixed in [`{branch.second}`]" in body
        assert "commit not recorded" not in body
        assert "**2 fixed**" in body

    def test_a_row_whose_line_predates_the_review_is_not_credited(
        self, rt, hand_landed_branch,
    ):
        """A commit older than the comment cannot be the fix that answered it."""
        body = _summary_over(
            rt, hand_landed_branch, [_row("t3", 3, "third point")],
            {"t3": _reviewed("t3", 333)},
        )
        assert hand_landed_branch.stale not in body
        assert "Fix applied (commit not recorded)" in body

    def test_a_row_with_no_line_stays_uncited(self, rt, hand_landed_branch):
        """A file-wide thread has no line history, so nothing resolves it."""
        body = _summary_over(
            rt, hand_landed_branch, [_row("t4", 0, "file-wide point")],
            {"t4": _reviewed("t4", 444)},
        )
        assert "Fix applied (commit not recorded)" in body

    def test_a_render_with_no_worktree_still_declines(self, rt, hand_landed_branch):
        """No tree to read is the case reconciliation was right to decline."""
        cp = _undetermined_pass(rt, hand_landed_branch)
        with patch.object(rt, "_resolve_default_branch", return_value="main"):
            body = rt._build_summary_body(
                [_row("t1", 1, "first point")], [], [], cp,
                "owner/repo", 42, {"t1": _reviewed("t1", 111)},
            )
        assert hand_landed_branch.first not in body
        assert "Fix applied (commit not recorded)" in body

    def test_the_table_and_the_reply_name_the_same_commit(self, rt, hand_landed_branch):
        """One thread, two surfaces — they read the same resolver or they lie."""
        branch = hand_landed_branch
        entry = CommentItem(id="t1", summary="first point", file="a.py", line=1)
        cp = _undetermined_pass(rt, branch)
        with patch.object(rt, "_resolve_default_branch", return_value="main"), \
             patch("pr_comments.post_thread_reply", return_value=True) as post:
            rt._reply_to_fixed(
                [entry], {"t1": _reviewed("t1", 111)}, "owner/repo", 42,
                cp, branch.path,
            )
        reply = post.call_args[0][3]
        row = _summary_over(
            rt, branch, [_row("t1", 1, "first point")],
            {"t1": _reviewed("t1", 111)},
        )
        assert branch.first[:7] in reply
        assert f"Fixed in [`{branch.first}`]" in row

    def test_a_resolved_row_is_not_warned_about(self, rt, hand_landed_branch, capsys):
        """The warning counts rows the table publishes without a claim.

        A row the table now cites is attributed, so counting it would report an
        attribution problem no reader of that table can find.
        """
        cp = _undetermined_pass(rt, hand_landed_branch)
        with patch.object(rt, "_resolve_default_branch", return_value="main"):
            rt._warn_unattributed_fixes(
                [_row("t1", 1, "first point")], cp, "owner/repo", None,
                rt.AddressingHistory(hand_landed_branch.path),
                {"t1": _reviewed("t1", 111)},
            )
        assert "no commit to attribute" not in capsys.readouterr().err

    def test_a_row_that_stays_uncited_is_still_warned_about(
        self, rt, hand_landed_branch, capsys,
    ):
        cp = _undetermined_pass(rt, hand_landed_branch)
        with patch.object(rt, "_resolve_default_branch", return_value="main"):
            rt._warn_unattributed_fixes(
                [_row("t3", 3, "third point")], cp, "owner/repo", None,
                rt.AddressingHistory(hand_landed_branch.path),
                {"t3": _reviewed("t3", 333)},
            )
        assert "1 fixed row(s) have no commit" in capsys.readouterr().err


class TestRowsTheFixPassDidNotLandCiteNoCommit:
    """A reason saying "handled outside the pass" outranks the line history.

    The resolver above answers "which commit changed the line this thread is
    anchored to, after the reviewer asked". For a row the pass fixed that is
    evidence. For a row it did not, it is a coincidence: reconciliation stamps
    `fixed` on any thread that merely looks settled on GitHub — resolved covers
    answered, deferred and declined — so a busy file's newest commit was
    credited to a thread whose own standing reply said it was tracked elsewhere
    rather than fixed here.

    Same tree as the class above, so the only difference between a cited row and
    an uncited one is the reason recorded on it.
    """

    def test_a_reconciled_row_declines_the_commit_that_touched_its_line(
        self, rt, hand_landed_branch,
    ):
        body = _summary_over(
            rt, hand_landed_branch,
            [_row("t1", 1, "first point", reason=rt._RECONCILED_REASON)],
            {"t1": _reviewed("t1", 111)},
        )
        assert hand_landed_branch.first not in body
        assert rt._RECONCILED_STATUS_TEXT in body

    def test_a_settled_row_declines_it_too(self, rt, hand_landed_branch):
        """`--settle` already promises this cell when no commit resolves."""
        body = _summary_over(
            rt, hand_landed_branch,
            [_row("t1", 1, "first point", reason=rt._SETTLED_REASON)],
            {"t1": _reviewed("t1", 111)},
        )
        assert hand_landed_branch.first not in body
        assert rt._RECONCILED_STATUS_TEXT in body

    def test_the_reply_declines_the_commit_the_table_declined(
        self, rt, hand_landed_branch,
    ):
        """The drain path carries the reason in `reasoning`, and it counts too.

        `CommentItem.from_outcome` puts it there for the reply templates, so a
        predicate reading only `reason` would leave the reply citing a commit
        the summary row beside it refuses to name.
        """
        branch = hand_landed_branch
        entry = CommentItem(id="t1", summary="first point", file="a.py", line=1,
                            reasoning=rt._RECONCILED_REASON)
        with patch.object(rt, "_resolve_default_branch", return_value="main"), \
             patch("pr_comments.post_thread_reply", return_value=True) as post:
            rt._reply_to_fixed(
                [entry], {"t1": _reviewed("t1", 111)}, "owner/repo", 42,
                _undetermined_pass(rt, branch), branch.path,
            )
        reply = post.call_args[0][3]
        assert branch.first[:7] not in reply
        assert "/commit/" not in reply

    def test_a_recorded_commit_survives_the_decline(self, rt, hand_landed_branch):
        """Only inference is refused. A SHA `--settle` resolved is a record."""
        body = _summary_over(
            rt, hand_landed_branch,
            [_row("t1", 1, "first point", reason=rt._SETTLED_REASON,
                  commit_sha=hand_landed_branch.second)],
            {"t1": _reviewed("t1", 111)},
        )
        assert f"Fixed in [`{hand_landed_branch.second}`]" in body

    def test_a_row_with_no_such_reason_still_cites_its_line(
        self, rt, hand_landed_branch,
    ):
        """The control: the decline is the reason's doing, not the fixture's."""
        body = _summary_over(
            rt, hand_landed_branch, [_row("t1", 1, "first point")],
            {"t1": _reviewed("t1", 111)},
        )
        assert f"Fixed in [`{hand_landed_branch.first}`]" in body

    @pytest.mark.parametrize("field", ["reason", "reasoning"])
    def test_both_reason_fields_are_read(self, rt, field):
        for text in (rt._RECONCILED_REASON, rt._SETTLED_REASON):
            assert rt._handled_outside(CommentItem(id="t1", **{field: text}))

    def test_an_ordinary_reason_is_not_one_of_them(self, rt):
        assert not rt._handled_outside(CommentItem(id="t1", reason="agent gave up"))
        assert not rt._handled_outside(CommentItem(id="t1"))


# ── default-branch resolution in commit lookups ────────────────────────────


class TestCommitLookupsUseDefaultBranch:
    """`origin/main` is not universal — a hardcoded base silently returns nothing."""

    def test_branch_commit_log_uses_resolved_branch(self, rt, tmp_path):
        with (
            patch.object(rt, "_resolve_default_branch", return_value="trunk"),
            patch.object(rt.git_client, "run") as run,
        ):
            run.return_value = _git_ran(0, stdout="abc1234 fix: thing\n")
            assert rt._branch_commit_log(tmp_path) == "abc1234 fix: thing"
        assert "origin/trunk..HEAD" in run.call_args[0]

    def test_find_addressing_commit_uses_resolved_branch(self, rt, tmp_path):
        with (
            patch.object(rt, "_resolve_default_branch", return_value="trunk"),
            patch.object(rt.git_client, "run") as run,
        ):
            run.return_value = _git_ran(0, stdout="deadbeef\n")
            assert rt._find_addressing_commit(tmp_path, "a.py", 10) == "deadbeef"
        assert "origin/trunk..HEAD" in run.call_args[0]

    def test_branch_commit_log_without_worktree(self, rt):
        assert rt._branch_commit_log(None) == ""


# ── shared thrash guard wiring ──────────────────────────────────────────────


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
            patch.object(rt.agent_invoke.ai_backend, "prompt", side_effect=prompt),
            patch.object(rt, "_branch_commit_log", return_value=""),
        ):
            result, rc = rt._run_triage(report, tmp_path, {})

        assert rc == 0
        assert result is not None
        assert len(prompts) == 2
        assert prompts[1].startswith(agent_retry.BLANK_RESPONSE_HINT)


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
            evidence_file="app.py", evidence_line=12, read_sha="cafe123",
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
            evidence_file="app.py", evidence_line=4, read_sha="cafe123",
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
            [CommentItem(id="t1", summary="fix", file="a.py", line=9,
                         read_sha="abc1234")],
            [], [], cp, "owner/repo", 1, {},
        )
        assert "https://github.com/owner/repo/blob/abc1234/a.py#L9" in body

    def test_summary_file_cell_drops_a_line_read_in_another_tree(self, rt):
        """The fix commit moved the line, so the cell links the file alone."""
        cp = rt.CommitPushResult("abc1234", "pushed", "")
        body = rt._build_summary_body(
            [CommentItem(id="t1", summary="fix", file="a.py", line=9,
                         read_sha="0ldc0de")],
            [], [], cp, "owner/repo", 1, {},
        )
        assert "https://github.com/owner/repo/blob/abc1234/a.py)" in body
        assert "#L9" not in body

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
    """Every entry point fails the same actionable way with no worktree."""

    def _ctx(self):
        return make_ctx(branch="isaac/feat/x", worktree_root=None,
                        head_sha="abc1234")

    def test_run_threads_exits_before_touching_github(self, rt, capsys):
        assert_no_worktree_exit(capsys, "isaac/feat/x",
                                rt._run_threads, None, None, self._ctx())

    def test_finish_deferred_work_exits_with_guidance(self, rt, capsys):
        assert_no_worktree_exit(capsys, "isaac/feat/x",
                                rt._finish_deferred_work, self._ctx(), PRReport())

    def test_settle_exits_before_reading_the_snapshot(self, rt, capsys):
        """A settled fix is attributed to a commit, which needs a checkout to find."""
        assert_no_worktree_exit(capsys, "isaac/feat/x",
                                rt._run_settle, self._ctx(), ["t1"], "fixed", "", "")


class TestCommentTrackingRoundTrip:
    """What the agent records comes back on the thread that earned it.

    The file format is `fix_tracking`'s and is tested there. What is tested here
    is the domain's half of the round trip: the section bodies this pass renders,
    and the entries the parsed verdicts are attached back onto.
    """

    def _thread(self, tid="t1"):
        return CommentItem(id=tid, file="a.py", line=3, reviewer="kgn",
                           summary="rename it")

    def _built(self, rt, tmp_path, threads, comment_items=()):
        """Write the checklist the way `fix_engine` writes it for this adapter."""
        adapter = _fix_adapter(
            rt, tmp_path,
            report=PRReport(repo="owner/repo", pr_number=42),
            fixable=list(threads), fixable_items=list(comment_items),
        )
        with patch.object(rt, "_diff_context_for_file", return_value=""), \
             patch.object(rt, "_resolve_default_branch", return_value="main"):
            fix_tracking.write(
                adapter.tracking_path, adapter.title, adapter.items(),
            )
        return adapter.tracking_path

    def _answer(self, path, label, reason=""):
        """Tick one box the way the agent's Edit would."""
        suffix = f" — {reason}" if reason else ""
        placeholder = "" if label == "fixed" else " — <why>"
        path.write_text(path.read_text().replace(
            f"- [ ] {label}{placeholder}", f"- [x] {label}{suffix}", 1,
        ))

    def _parsed(self, rt, path, threads, comment_items=()):
        return rt._parse_tracking_results(
            fix_tracking.parse(path), list(threads),
            fixable_items=list(comment_items),
        )

    def test_the_section_carries_the_id_the_reviewer_and_the_context(self, rt, tmp_path):
        text = self._built(rt, tmp_path, [self._thread()]).read_text()
        assert text.startswith("# Comment Fix Tracking — PR #42\n")
        assert "## <!-- fix:t1 --> a.py:3 — @kgn" in text
        assert "**Summary:** rename it" in text

    def test_a_ticked_fix_comes_back_as_the_entry_the_pass_handed_over(self, rt, tmp_path):
        threads = [self._thread()]
        path = self._built(rt, tmp_path, threads)
        self._answer(path, "fixed")
        result = self._parsed(rt, path, threads)
        assert [e.id for e in result.bucket(FixOutcome.FIXED)] == ["t1"]
        assert result.bucket(FixOutcome.FIXED)[0].reviewer == "kgn"

    def test_a_declined_thread_keeps_the_agent_s_own_words(self, rt, tmp_path):
        threads = [self._thread()]
        path = self._built(rt, tmp_path, threads)
        self._answer(path, "declined", "the helper it names does not exist")
        entry = self._parsed(rt, path, threads).bucket(FixOutcome.DECLINED)[0]
        assert entry.reason == "the helper it names does not exist"

    def test_a_verdict_with_no_reason_still_says_something(self, rt, tmp_path):
        threads = [self._thread()]
        path = self._built(rt, tmp_path, threads)
        self._answer(path, "needs a person")
        entry = self._parsed(rt, path, threads).bucket(FixOutcome.NEEDS_HUMAN)[0]
        assert entry.reason == "agent could not auto-fix"

    def test_an_untouched_thread_is_work_still_owed(self, rt, tmp_path):
        threads = [self._thread()]
        path = self._built(rt, tmp_path, threads)
        entry = self._parsed(rt, path, threads).bucket(FixOutcome.DEFERRED)[0]
        assert entry.reason == "agent could not auto-fix"

    def test_a_comment_item_is_kept_apart_from_a_thread(self, rt, tmp_path):
        """Only a thread has somewhere to reply, so the two never merge."""
        items = [CommentItem(id="c9", file="b.py", line=1, reviewer="ana",
                             body="two spaces")]
        path = self._built(rt, tmp_path, [], items)
        self._answer(path, "fixed")
        result = self._parsed(rt, path, [], items)
        assert result.bucket(FixOutcome.FIXED) == []
        assert [e.id for e in result.bucket(FixOutcome.FIXED, item=True)] == ["c9"]

    def test_a_section_the_pass_never_handed_over_is_ignored(self, rt, tmp_path):
        """The file is agent-editable — an invented id names nobody to reply to."""
        threads = [self._thread()]
        path = self._built(rt, tmp_path, threads)
        path.write_text(path.read_text() + (
            "\n## <!-- fix:invented --> z.py:1 — @nobody\n\n- [x] fixed\n"
        ))
        result = self._parsed(rt, path, threads)
        assert result.bucket(FixOutcome.FIXED) == []
        assert [e.id for e in result.bucket(FixOutcome.DEFERRED)] == ["t1"]


class TestMergeTracking:
    def test_batch_results_accumulate(self, rt):
        total = rt.TrackingResult(
            threads={FixOutcome.FIXED: ["a"]}, items={FixOutcome.DEFERRED: ["z"]},
        )
        total.merge(rt.TrackingResult(
            threads={FixOutcome.FIXED: ["b"], FixOutcome.DEFERRED: ["c"]},
        ))
        assert total.bucket(FixOutcome.FIXED) == ["a", "b"]
        assert total.bucket(FixOutcome.DEFERRED) == ["c"]
        assert total.bucket(FixOutcome.DEFERRED, item=True) == ["z"]

    def test_a_merge_does_not_alias_the_source_s_lists(self, rt):
        """A batch merged into an empty total must not hand over its own list."""
        batch = rt.TrackingResult(threads={FixOutcome.FIXED: ["a"]})
        total = rt.TrackingResult()
        total.merge(batch)
        total.add(FixOutcome.FIXED, "b")
        assert batch.bucket(FixOutcome.FIXED) == ["a"]

    def test_dropping_an_outcome_forgets_threads_and_items_alike(self, rt):
        total = rt.TrackingResult(
            threads={FixOutcome.DEFERRED: ["a"], FixOutcome.FIXED: ["k"]},
            items={FixOutcome.DEFERRED: ["z"]},
        )
        total.drop(FixOutcome.DEFERRED)
        assert total.both(FixOutcome.DEFERRED) == []
        assert total.bucket(FixOutcome.FIXED) == ["k"]


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
        `ItemOutcome`s rather than the triage entries, so the prose mapping has
        to hold across the rehydration `from_outcome` performs.
        """
        cp = rt.CommitPushResult(None, "no_changes", "")
        entry = CommentItem.from_outcome(ItemOutcome(
            id="t1", summary="premise disputed", file="a.py", line=1,
            outcome=FixOutcome.NEEDS_HUMAN, reason=rt.HumanReason.CONTESTED.value,
        ))
        body = rt._build_summary_body([], [entry], [], cp, "owner/repo", 1, {})
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

    def _outcomes(self, outcome=FixOutcome.NEEDS_HUMAN, iid="ic-77-0"):
        return [ItemOutcome(id=iid, outcome=outcome, reason="contested")]

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
                [ItemOutcome(id="t1", outcome=FixOutcome.DEFERRED)],
                "owner/repo", 42, "me")
        assert answered == frozenset()
        fetch.assert_not_called()

    def test_a_settled_item_is_not_worth_a_listing_either(self, rt):
        with _fetches([]) as fetch:
            rt._answered_comment_sources(
                self._outcomes(outcome=FixOutcome.FIXED), "owner/repo", 42, "me")
        fetch.assert_not_called()

    def test_without_our_login_no_reply_can_be_called_ours(self, rt):
        with _fetches([_our_reply("#issuecomment-77")]) as fetch:
            answered = rt._answered_comment_sources(
                self._outcomes(), "owner/repo", 42, "")
        assert answered == frozenset()
        fetch.assert_not_called()


class TestCommentItemsSettleThroughTheirSource:
    """The outcome the fix pass handed to the operator has to be clearable."""

    def _state(self, outcome=FixOutcome.NEEDS_HUMAN, iid="ic-77-0"):
        return _make_state(_fix(head_sha="aaaaaaa", items=[
            ItemOutcome(id=iid, file="a.go", line=7,
                        summary="drop the retry", outcome=outcome,
                        reason="contested"),
        ], reviewers={iid: "kgn"}))

    def test_an_answered_item_reconciles_to_fixed(self, rt):
        state = self._state()
        assert rt._reconcile_fix_snapshot(state, {}, frozenset({"77"})) == 1
        assert state.fix.fix.items[0].outcome == FixOutcome.FIXED
        assert "reconciled" in state.fix.fix.items[0].reason

    def test_a_deferred_item_reconciles_the_same_way(self, rt):
        state = self._state(outcome=FixOutcome.DEFERRED)
        assert rt._reconcile_fix_snapshot(state, {}, frozenset({"77"})) == 1
        assert state.fix.fix.items[0].outcome == FixOutcome.FIXED

    def test_a_review_body_item_reconciles_through_its_review(self, rt):
        state = self._state(iid="rb-88-1")
        assert rt._reconcile_fix_snapshot(state, {}, frozenset({"88"})) == 1
        assert state.fix.fix.items[0].outcome == FixOutcome.FIXED

    def test_an_answer_to_another_comment_is_not_this_items_answer(self, rt):
        state = self._state()
        assert rt._reconcile_fix_snapshot(state, {}, frozenset({"99"})) == 0
        assert state.fix.fix.items[0].outcome == FixOutcome.NEEDS_HUMAN

    def test_an_unanswered_item_still_holds_the_summary_back(self, rt):
        state = self._state()
        assert rt._reconcile_fix_snapshot(state, {}, frozenset()) == 0
        needs_human = [t for t in state.fix.fix.items
                       if t.outcome == FixOutcome.NEEDS_HUMAN]
        assert needs_human
        assert rt._summary_still_owed(
            [], needs_human, [], [], CommitStatus.PUSHED, False,
            already_addressed=[], issue_comments=[],
            review_body_comments=[]) is True

    def test_an_item_restating_a_settled_thread_settles_with_it(self, rt):
        """The duplicate is one finding; one of its two copies being closed closes it."""
        state = self._state()
        threads = {"t1": ReportThread(
            id="t1", file="a.go", line=7, reviewer="kgn",
            state=ThreadState.RESOLVED, is_resolved=True, comments=[{"body": "x"}],
        )}
        assert rt._reconcile_fix_snapshot(state, threads) == 1
        assert state.fix.fix.items[0].outcome == FixOutcome.FIXED

    def test_an_item_restating_an_open_thread_stays_open(self, rt):
        state = self._state()
        threads = {"t1": ReportThread(
            id="t1", file="a.go", line=7, reviewer="kgn",
            state=ThreadState.NEW, is_resolved=False,
            comments=[{"body": "why not the other way?"}],
        )}
        assert rt._reconcile_fix_snapshot(state, threads) == 0
        assert state.fix.fix.items[0].outcome == FixOutcome.NEEDS_HUMAN

    def test_a_settled_thread_elsewhere_settles_nothing_here(self, rt):
        state = self._state()
        threads = {"t1": ReportThread(
            id="t1", file="b.go", line=3, reviewer="kgn",
            state=ThreadState.RESOLVED, is_resolved=True, comments=[{"body": "x"}],
        )}
        assert rt._reconcile_fix_snapshot(state, threads) == 0
        assert state.fix.fix.items[0].outcome == FixOutcome.NEEDS_HUMAN


class TestFinishReconcilesCommentItems:
    """The wiring: --finish is what asks GitHub about the source comments."""

    def _save(self, worktree):
        pr_state.save_state(worktree / "target", PRState(
            identity=PRIdentity(repo="owner/repo", branch="b", pr_number=42,
                                head_sha="aaaaaaa", worktree_root=str(worktree)),
            fix=_fix(head_sha="aaaaaaa", items=[
                ItemOutcome(id="ic-77-0", file="a.go", line=7,
                            summary="drop the retry",
                            outcome=FixOutcome.NEEDS_HUMAN, reason="contested"),
            ], reviewers={"ic-77-0": "kgn"}),
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
        assert saved.fix.fix.items[0].outcome == FixOutcome.FIXED

    def test_an_unanswered_item_survives_the_round(self, rt, worktree):
        ctx = self._save(worktree)
        self._run(rt, ctx, [_our_reply("#issuecomment-99")])
        saved = pr_state.load_state(worktree / "target")
        assert saved.fix.fix.items[0].outcome == FixOutcome.NEEDS_HUMAN


class TestDuplicateFindingRendersOnce:
    """One review point that arrived twice is still one row in the table."""

    def _thread(self, **kw):
        defaults = {"id": "t1", "file": "a.go", "line": 7, "reviewer": "kgn",
                    "summary": "drop the retry"}
        defaults.update(kw)
        return CommentItem(**defaults)

    def _item(self, **kw):
        defaults = {"id": "ic-77-0", "file": "a.go", "line": 7, "reviewer": "kgn",
                    "summary": "also drop the retry", "reason": "contested"}
        defaults.update(kw)
        return CommentItem(**defaults)

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
            [self._thread(id="t2", summary="and rename it")],
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
            fix=_fix(items=[
                ItemOutcome(id="t1", file="a.go", line=7,
                            summary="rename the guard", outcome=FixOutcome.DEFERRED),
            ], reviewers={"t1": "kgn"}),
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
        fix = self._finalize(rt, worktree, self._provider("linear"))
        assert fix.deferred_issue_pending is True
        assert fix.closeout_debt().owed is True

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
