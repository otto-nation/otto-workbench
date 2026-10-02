"""review.reply_threads: classifying, matching and fetching reply threads."""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

# `_no_published_summary` is autouse: imported so pytest applies it here,
# never referenced by name.
from review_threads_support import _no_published_summary  # noqa: E402
from gh.pr_reads import ThreadSet
from review.grammar import sid_marker
from review.reply_threads import (
    ReplyThreads, ThreadFinding, _classify_thread_for_rereview,
    _match_thread_to_finding, fetch_reply_threads,
)
from review.format import format_inline_comment
from review.types import SEVERITIES, Finding, ReplyState
from review.prompt_prior import _annotate_with_thread_state


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
        verdict = _classify_thread_for_rereview(comments, True, "bot")
        assert verdict.state == ReplyState.RESOLVED
        assert verdict.replies == []

    def test_unreplied_no_author_replies(self):
        comments = _make_comments(("bot", "Missing error check"))
        verdict = _classify_thread_for_rereview(comments, False, "bot")
        assert verdict.state == ReplyState.UNREPLIED
        assert verdict.replies == []

    def test_acknowledged_reply(self):
        comments = _make_comments(
            ("bot", "Missing error check"),
            ("alice", "Fixed, thanks!"),
        )
        verdict = _classify_thread_for_rereview(comments, False, "bot")
        assert verdict.state == ReplyState.ACKNOWLEDGED
        assert len(verdict.replies) == 1
        assert verdict.replies[0]["body"] == "Fixed, thanks!"

    def test_contested_reply(self):
        comments = _make_comments(
            ("bot", "Should use shared helper"),
            ("alice", "I think we should keep it inline actually — it's clearer"),
        )
        verdict = _classify_thread_for_rereview(comments, False, "bot")
        assert verdict.state == ReplyState.CONTESTED
        assert len(verdict.replies) == 1

    def test_generic_reply(self):
        comments = _make_comments(
            ("bot", "Missing error check"),
            ("alice", "I see your point, let me look into this further"),
        )
        verdict = _classify_thread_for_rereview(comments, False, "bot")
        assert verdict.state == ReplyState.REPLIED
        assert len(verdict.replies) == 1

    def test_case_insensitive_bot_login(self):
        comments = _make_comments(
            ("Bot-User", "Issue"),
            ("alice", "Done"),
        )
        verdict = _classify_thread_for_rereview(comments, False, "bot-user")
        assert verdict.state == ReplyState.ACKNOWLEDGED

    def test_state_uses_last_reply(self):
        comments = _make_comments(
            ("bot", "Issue"),
            ("alice", "Done"),
            ("alice", "Actually no, I still think we should keep it"),
        )
        verdict = _classify_thread_for_rereview(comments, False, "bot")
        assert verdict.state == ReplyState.CONTESTED
        assert len(verdict.replies) == 2

    def test_bot_reply_between_author_replies(self):
        comments = _make_comments(
            ("bot", "Issue"),
            ("alice", "Why?"),
            ("bot", "Because X"),
            ("alice", "Done"),
        )
        verdict = _classify_thread_for_rereview(comments, False, "bot")
        assert verdict.state == ReplyState.ACKNOWLEDGED
        assert len(verdict.replies) == 2
        assert verdict.replies[0]["body"] == "Why?"
        assert verdict.replies[1]["body"] == "Done"


# ── _match_thread_to_finding ─────────────────────────────────────────────────

class TestMatchThreadToFinding:
    def test_extracts_finding_id(self):
        body = "**[M1]** `handler.go:42` — Missing error check on db.Query()"
        assert _match_thread_to_finding(body).posted_id == "M1"

    def test_extracts_different_severities(self):
        assert _match_thread_to_finding("**[S3]** something").posted_id == "S3"
        assert _match_thread_to_finding("**[N2]** nit").posted_id == "N2"
        assert _match_thread_to_finding("**[I1]** info").posted_id == "I1"

    def test_no_finding_id(self):
        assert _match_thread_to_finding("Just a comment") == ThreadFinding()

    def test_first_match_wins(self):
        body = "**[M1]** first\n**[M2]** second"
        assert _match_thread_to_finding(body).posted_id == "M1"

    @pytest.mark.parametrize("severity", [s.key for s in SEVERITIES])
    def test_the_body_the_poster_actually_writes_matches(self, severity):
        """The bodies this reads are labelled, and it used to match none of them.

        `format_inline_comment` has always written the severity label between
        the ID and the closing `**`, and the regex required them adjacent — so
        every thread came back with an empty `finding_id` and the annotation
        downstream was dead code. The round trip is the assertion: whatever the
        writer emits, the reader gets the ID back out of it.
        """
        finding = Finding(
            id=f"{severity}1", severity=severity, seq=1, path="ai/lib/x.py",
            line=12, end_line=None, body="missing error check",
        )
        finding.posted_id = f"{severity}1"
        body = format_inline_comment(finding)["body"]
        assert _match_thread_to_finding(body).posted_id == f"{severity}1"

    def test_the_identity_comes_back_out_of_a_posted_body(self):
        """The durable half of the match, which the posted comment carries.

        `posted_id` is reassigned every round, so on its own it names whatever
        finding happens to hold that number in the review file being annotated.
        """
        body = f"**[M1] [must-fix]**{sid_marker('abc12345')} Missing error check"
        assert _match_thread_to_finding(body) == ThreadFinding(
            posted_id="M1", stable_id="abc12345",
        )

    def test_a_comment_posted_before_the_marker_existed_keeps_its_number(self):
        body = "**[M1] [must-fix]** Missing error check"
        assert _match_thread_to_finding(body) == ThreadFinding(posted_id="M1")


# ── fetch_reply_threads ─────────────────────────────────────────────────────

class TestFetchReplyThreads:
    def test_empty_when_no_bot_login(self):
        with patch("review.reply_threads.get_bot_login", return_value=""), \
             patch("review.reply_threads.fetch_threads", return_value=ThreadSet([])):
            result = fetch_reply_threads("owner/repo", "42")
        assert result == ReplyThreads(threads=[], summary={})

    def test_empty_when_no_threads(self):
        with patch("review.reply_threads.get_bot_login", return_value="bot"), \
             patch("review.reply_threads.fetch_threads", return_value=ThreadSet([])):
            result = fetch_reply_threads("owner/repo", "42")
        assert result == ReplyThreads(threads=[], summary={})

    def test_warns_when_the_fetch_raises(self):
        with patch("review.reply_threads.get_bot_login", return_value="bot"), \
             patch("review.reply_threads.fetch_threads", side_effect=RuntimeError("boom")), \
             patch("core.log.warn") as warn:
            result = fetch_reply_threads("owner/repo", "42")
        assert result == ReplyThreads(threads=[], summary={})
        assert warn.call_count == 1
        assert "boom" in warn.call_args[0][0]

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
        with patch("review.reply_threads.get_bot_login", return_value="bot"), \
             patch("review.reply_threads.fetch_threads", return_value=ThreadSet(threads)):
            result = fetch_reply_threads("owner/repo", "42")
        assert len(result.threads) == 1
        assert result.threads[0]["finding_id"] == "M1"
        assert result.threads[0]["state"] == ReplyState.ACKNOWLEDGED
        assert result.summary == {ReplyState.ACKNOWLEDGED: 1}

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
        with patch("review.reply_threads.get_bot_login", return_value="bot"), \
             patch("review.reply_threads.fetch_threads", return_value=ThreadSet(threads)):
            result = fetch_reply_threads("owner/repo", "42")
        states = {t["state"] for t in result.threads}
        assert ReplyState.RESOLVED in states
        assert ReplyState.UNREPLIED in states

    def test_a_thread_on_a_real_posted_comment_reaches_the_prior_review(self):
        """The whole path, from the body the poster wrote to the annotated line.

        Every test above hands the classifier a hand-written `**[M1]**` body,
        which is the review file's spelling and not the one that reaches
        GitHub. Posting the finding for real is what caught that the reader
        never matched: the tag it looks for carries the severity label.
        """
        finding = Finding(
            id="M1", severity="M", seq=1, path="a.py", line=10, end_line=None,
            body="Missing error check",
        )
        finding.posted_id = "M1"
        posted = format_inline_comment(finding)["body"]

        threads = [{
            "id": "T1", "isResolved": False, "path": "a.py", "line": 10,
            "comments": {"nodes": _make_comments(
                ("bot", posted),
                ("alice", "However, the error is handled upstream"),
            )},
        }]
        with patch("review.reply_threads.get_bot_login", return_value="bot"), \
             patch("review.reply_threads.fetch_threads", return_value=ThreadSet(threads)):
            result = fetch_reply_threads("owner/repo", "42")

        assert result.threads[0]["finding_id"] == "M1"
        assert result.threads[0]["state"] == ReplyState.CONTESTED

        review = "## Must fix\n- **[M1]** `a.py:10` — Missing error check\n"
        annotated = _annotate_with_thread_state(review, result)
        assert annotated.split("\n")[1].endswith("Missing error check  [CONTESTED]")

    def test_a_thread_carries_both_names_of_the_finding_it_hangs_off(self):
        threads = [{
            "id": "T1", "isResolved": False, "path": "a.py", "line": 10,
            "comments": {"nodes": _make_comments(
                ("bot", f"**[M1] [must-fix]**{sid_marker('abc12345')} Missing error check"),
                ("alice", "Fixed in the next commit"),
            )},
        }]
        with patch("review.reply_threads.get_bot_login", return_value="bot"), \
             patch("review.reply_threads.fetch_threads", return_value=ThreadSet(threads)):
            result = fetch_reply_threads("owner/repo", "42")

        assert result.threads[0]["finding_id"] == "M1"
        assert result.threads[0]["stable_id"] == "abc12345"
