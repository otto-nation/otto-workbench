"""review.prompt_sections: the reply-thread and comment sections of the review prompt."""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

# `_no_published_summary` is autouse: imported so pytest applies it here,
# never referenced by name.
from review_threads_support import _no_published_summary  # noqa: E402
from review.reply_threads import ReplyThreads
from review.types import ReplyState
from review.prompt_sections import (
    _build_reply_threads_section, _format_general_comments,
    _format_review_comments, _format_reviews,
)


# ── _build_reply_threads_section ─────────────────────────────────────────────

class TestBuildReplyThreadsSection:
    def test_empty_when_no_threads(self):
        assert _build_reply_threads_section(None) == ""
        assert _build_reply_threads_section(ReplyThreads(threads=[], summary={})) == ""

    def test_groups_by_state(self):
        data = ReplyThreads(threads=[
            {"state": ReplyState.CONTESTED, "finding_id": "M1", "path": "a.py",
             "line": 10, "root_body": "issue", "replies": [
                 {"author": "alice", "body": "I disagree because X"},
             ]},
            {"state": ReplyState.ACKNOWLEDGED, "finding_id": "S1", "path": "b.py",
             "line": 5, "root_body": "issue", "replies": [
                 {"author": "alice", "body": "Fixed"},
             ]},
        ], summary={})
        section = _build_reply_threads_section(data)
        assert "### Contested" in section
        assert "### Acknowledged" in section
        assert "[M1]" in section
        assert "[S1]" in section

    def test_includes_reply_text_for_contested(self):
        data = ReplyThreads(threads=[
            {"state": ReplyState.CONTESTED, "finding_id": "M1", "path": "a.py",
             "line": 10, "root_body": "issue", "replies": [
                 {"author": "alice", "body": "I disagree because X"},
             ]},
        ], summary={})
        section = _build_reply_threads_section(data)
        assert "@alice: I disagree because X" in section

    def test_no_reply_text_for_resolved(self):
        data = ReplyThreads(threads=[
            {"state": ReplyState.RESOLVED, "finding_id": "M1", "path": "a.py",
             "line": 10, "root_body": "issue", "replies": []},
        ], summary={})
        section = _build_reply_threads_section(data)
        assert "### Resolved" in section
        assert "> @" not in section

    def test_unreplied_threads(self):
        data = ReplyThreads(threads=[
            {"state": ReplyState.UNREPLIED, "finding_id": "M2", "path": "c.py",
             "line": 1, "root_body": "issue", "replies": []},
        ], summary={})
        section = _build_reply_threads_section(data)
        assert "### No reply" in section
        assert "[M2]" in section

    def test_replied_threads_include_text(self):
        data = ReplyThreads(threads=[
            {"state": ReplyState.REPLIED, "finding_id": "S1", "path": "d.py",
             "line": 3, "root_body": "issue", "replies": [
                 {"author": "bob", "body": "Let me look into this"},
             ]},
        ], summary={})
        section = _build_reply_threads_section(data)
        assert "### Author replied" in section
        assert "@bob: Let me look into this" in section

    def test_file_filter_scopes_to_matching_paths(self):
        data = ReplyThreads(threads=[
            {"state": ReplyState.CONTESTED, "finding_id": "M1", "path": "a.py",
             "line": 10, "root_body": "issue", "replies": [
                 {"author": "alice", "body": "I disagree"},
             ]},
            {"state": ReplyState.CONTESTED, "finding_id": "M2", "path": "b.py",
             "line": 5, "root_body": "issue", "replies": [
                 {"author": "alice", "body": "Also disagree"},
             ]},
        ], summary={})
        section = _build_reply_threads_section(data, file_filter=["a.py"])
        assert "[M1]" in section
        assert "[M2]" not in section

    def test_file_filter_none_includes_all(self):
        data = ReplyThreads(threads=[
            {"state": ReplyState.ACKNOWLEDGED, "finding_id": "S1", "path": "a.py",
             "line": 1, "root_body": "issue", "replies": []},
            {"state": ReplyState.ACKNOWLEDGED, "finding_id": "S2", "path": "b.py",
             "line": 2, "root_body": "issue", "replies": []},
        ], summary={})
        section = _build_reply_threads_section(data, file_filter=None)
        assert "[S1]" in section
        assert "[S2]" in section

    def test_file_filter_no_matches_returns_empty(self):
        data = ReplyThreads(threads=[
            {"state": ReplyState.CONTESTED, "finding_id": "M1", "path": "a.py",
             "line": 10, "root_body": "issue", "replies": []},
        ], summary={})
        section = _build_reply_threads_section(data, file_filter=["other.py"])
        assert section == ""


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
