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
from review.budget import MAX_ISSUE_CONTEXT_BYTES
from review.prompt_sections import (
    _build_focus_section, _build_issue_section, _build_reply_threads_section,
    _format_general_comments, _format_review_comments, _format_reviews,
)
from review.record import FocusSource
from review_focus_support import area


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


# ── _build_issue_section ─────────────────────────────────────────────────────

class TestBuildIssueSection:
    def test_link_and_body_render_together(self):
        section = _build_issue_section(
            "https://x/1", '{"title": "T", "body": "B"}',
        )
        assert "https://x/1" in section
        assert '{"title": "T", "body": "B"}' in section
        assert section.index("https://x/1") < section.index(
            '{"title": "T", "body": "B"}',
        )

    def test_link_alone(self):
        assert _build_issue_section("https://x/1", "") == (
            "\n## Related issue\nhttps://x/1"
        )

    def test_body_alone(self):
        assert _build_issue_section("", "BODY") == "\n## Related issue\nBODY"

    def test_nothing_is_empty(self):
        assert _build_issue_section("", "") == ""

    def test_a_long_body_is_truncated_and_says_so(self):
        link = "https://x/1"
        body = "x" * (20 * 1024)
        section = _build_issue_section(link, body)
        assert body[:MAX_ISSUE_CONTEXT_BYTES] in section
        assert body[:MAX_ISSUE_CONTEXT_BYTES + 1] not in section
        assert f"read the rest at {link}" in section

    def test_a_long_body_without_a_link_is_truncated_and_says_so(self):
        body = "x" * (20 * 1024)
        section = _build_issue_section("", body)
        assert body[:MAX_ISSUE_CONTEXT_BYTES] in section
        assert body[:MAX_ISSUE_CONTEXT_BYTES + 1] not in section
        assert "the rest in the tracker" in section

    def test_truncation_drops_a_partial_utf8_character(self):
        euro = "€"
        body = ("x" * (MAX_ISSUE_CONTEXT_BYTES - 1)) + euro
        section = _build_issue_section("", body)
        assert euro not in section
        assert "\ufffd" not in section
        assert ("x" * (MAX_ISSUE_CONTEXT_BYTES - 1)) in section


class TestBuildFocusSection:
    def test_requested_areas_are_marked_and_listed_first(self):
        text = _build_focus_section((
            area(id="F1", title="Auth", source=FocusSource.USER),
            area(id="F2", title="DerivedBit", source=FocusSource.DERIVED),
        ))
        assert "- **F1** *(requested)* — Auth" in text
        assert "- **F2** — DerivedBit" in text
        assert text.index("Auth") < text.index("DerivedBit")
        assert "*(requested)*" not in text[text.index("DerivedBit"):]

    def test_no_areas_render_nothing(self):
        assert _build_focus_section(()) == ""

    def test_paths_and_rationale_follow_the_title(self):
        text = _build_focus_section((
            area(id="F1", title="Auth", rationale="check the gate",
                 paths=("a.py", "b.py"), source=FocusSource.USER),
        ))
        title = text.index("- **F1** *(requested)* — Auth")
        why = text.index("  Why: check the gate")
        paths = text.index("  Paths: `a.py`, `b.py`")
        assert title < why < paths
