"""Tests for pr_comments library."""

import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "claude" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from pr_comments import load_state, save_state, empty_state, compute_thread_state, sync_threads, render_dashboard, STATE_NEW, STATE_ADDRESSED, STATE_VERIFIED, STATE_RESOLVED


def test_empty_state_has_required_fields():
    state = empty_state("otto-nation/maximum", 142, "isaacg-otto")
    assert state["repo"] == "otto-nation/maximum"
    assert state["pr_number"] == 142
    assert state["my_login"] == "isaacg-otto"
    assert state["threads"] == {}
    assert "last_run" in state


def test_load_state_missing_file():
    state = load_state(Path("/nonexistent/state.json"))
    assert state is None


def test_save_and_load_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "state.json"
        state = empty_state("otto-nation/maximum", 142, "isaacg-otto")
        state["threads"]["12345"] = {
            "state": "new",
            "classification": None,
            "reviewer": "alice",
            "file": "handler.go",
            "line": 42,
            "summary": None,
            "decided_at": None,
            "last_seen_reply_id": None,
        }
        save_state(path, state)
        loaded = load_state(path)
        assert loaded == state


def test_save_creates_parent_directories():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "nested" / "dir" / "state.json"
        state = empty_state("repo", 1, "user")
        save_state(path, state)
        assert path.exists()


def _make_comments(*entries):
    """Helper: create comment list from (login, body) tuples."""
    comments = []
    for i, (login, body) in enumerate(entries):
        comments.append({
            "databaseId": 1000 + i,
            "author": {"login": login},
            "body": body,
            "createdAt": f"2026-01-01T{i:02d}:00:00Z",
        })
    return comments


def test_new_thread_no_replies():
    state = compute_thread_state(
        comments=_make_comments(("alice", "Use RunTx here")),
        is_resolved=False,
        my_login="isaacg",
    )
    assert state == "new"


def test_addressed_my_reply_is_latest():
    state = compute_thread_state(
        comments=_make_comments(
            ("alice", "Use RunTx here"),
            ("isaacg", "Fixed."),
        ),
        is_resolved=False,
        my_login="isaacg",
    )
    assert state == "addressed"


def test_verified_reviewer_acks():
    state = compute_thread_state(
        comments=_make_comments(
            ("alice", "Use RunTx here"),
            ("isaacg", "Fixed."),
            ("alice", "LGTM, thanks!"),
        ),
        is_resolved=False,
        my_login="isaacg",
    )
    assert state == "verified"


def test_contested_reviewer_pushes_back():
    state = compute_thread_state(
        comments=_make_comments(
            ("alice", "Use RunTx here"),
            ("isaacg", "Fixed."),
            ("alice", "I still think we should use the shared helper instead"),
        ),
        is_resolved=False,
        my_login="isaacg",
    )
    assert state == "contested"


def test_resolved_on_github():
    state = compute_thread_state(
        comments=_make_comments(("alice", "Use RunTx here")),
        is_resolved=True,
        my_login="isaacg",
    )
    assert state == "resolved"


def test_re_addressed_after_contested():
    state = compute_thread_state(
        comments=_make_comments(
            ("alice", "Use RunTx here"),
            ("isaacg", "Fixed."),
            ("alice", "Not quite, still need to handle the error"),
            ("isaacg", "Good point, updated."),
        ),
        is_resolved=False,
        my_login="isaacg",
    )
    assert state == "addressed"


def test_ambiguous_short_question():
    state = compute_thread_state(
        comments=_make_comments(
            ("alice", "Use RunTx here"),
            ("isaacg", "Fixed."),
            ("alice", "Hmm?"),
        ),
        is_resolved=False,
        my_login="isaacg",
    )
    assert state == "ambiguous"


def test_long_positive_reply_is_ambiguous_not_contested():
    long_positive = "Great work on this! The implementation looks solid and handles all the edge cases I was worried about. Ship it when ready, this is excellent."
    state = compute_thread_state(
        comments=_make_comments(
            ("alice", "Use RunTx here"),
            ("isaacg", "Fixed."),
            ("alice", long_positive),
        ),
        is_resolved=False,
        my_login="isaacg",
    )
    assert state == "ambiguous"


def test_sync_clears_summary_on_new_replies():
    threads = [{
        "id": "T_abc",
        "isResolved": False,
        "comments": {"nodes": _make_comments(
            ("alice", "Fix this"),
            ("isaacg", "Fixed."),
            ("alice", "Not quite, still needs work"),
        )},
    }]
    prior_threads = {
        "T_abc": {
            "state": STATE_ADDRESSED,
            "classification": "suggestion",
            "reviewer": "alice",
            "file": None,
            "line": None,
            "summary": "Old summary",
            "decided_at": "2026-06-14T15:00:00Z",
            "last_seen_reply_id": 1001,
        },
    }
    result = sync_threads(threads, prior_threads, "isaacg")
    assert result["T_abc"]["classification"] is None
    assert result["T_abc"]["summary"] is None
    assert result["T_abc"]["decided_at"] is None


def test_sync_new_thread_no_prior_state():
    threads = [{
        "id": "T_abc",
        "isResolved": False,
        "comments": {"nodes": _make_comments(("alice", "Fix this"))},
    }]
    prior_threads = {}
    result = sync_threads(threads, prior_threads, "isaacg")
    assert "T_abc" in result
    assert result["T_abc"]["state"] == STATE_NEW
    assert result["T_abc"]["reviewer"] == "alice"
    assert result["T_abc"]["last_seen_reply_id"] == 1000


def test_sync_keeps_cached_classification():
    threads = [{
        "id": "T_abc",
        "isResolved": False,
        "comments": {"nodes": _make_comments(("alice", "Fix this"))},
    }]
    prior_threads = {
        "T_abc": {
            "state": STATE_NEW,
            "classification": "suggestion",
            "reviewer": "alice",
            "file": "handler.go",
            "line": 42,
            "summary": "Fix the handler",
            "decided_at": "2026-06-14T15:00:00Z",
            "last_seen_reply_id": 1000,
        },
    }
    result = sync_threads(threads, prior_threads, "isaacg")
    assert result["T_abc"]["classification"] == "suggestion"
    assert result["T_abc"]["summary"] == "Fix the handler"


def test_sync_detects_new_reply_updates_state():
    threads = [{
        "id": "T_abc",
        "isResolved": False,
        "comments": {"nodes": _make_comments(
            ("alice", "Fix this"),
            ("isaacg", "Fixed."),
        )},
    }]
    prior_threads = {
        "T_abc": {
            "state": STATE_NEW,
            "classification": "suggestion",
            "reviewer": "alice",
            "file": None,
            "line": None,
            "summary": None,
            "decided_at": None,
            "last_seen_reply_id": 1000,
        },
    }
    result = sync_threads(threads, prior_threads, "isaacg")
    assert result["T_abc"]["state"] == STATE_ADDRESSED
    assert result["T_abc"]["last_seen_reply_id"] == 1001


def test_sync_resolved_on_github_overrides():
    threads = [{
        "id": "T_abc",
        "isResolved": True,
        "comments": {"nodes": _make_comments(("alice", "Fix this"))},
    }]
    prior_threads = {
        "T_abc": {"state": STATE_NEW, "last_seen_reply_id": 1000,
                  "classification": None, "reviewer": "alice",
                  "file": None, "line": None, "summary": None, "decided_at": None},
    }
    result = sync_threads(threads, prior_threads, "isaacg")
    assert result["T_abc"]["state"] == STATE_RESOLVED


def test_dashboard_shows_review_body_comments():
    threads = {"T_1": {"state": STATE_NEW}}
    verdicts = [{"user": "alice", "state": "COMMENTED", "submitted_at": "2026-01-01T00:00:00Z"}]
    review_body = [
        {"id": 1, "user": "alice", "body": "Overlaps with #2284", "state": "COMMENTED"},
        {"id": 2, "user": "bot", "body": "Acronym bug", "state": "COMMENTED"},
    ]
    dashboard = render_dashboard(42, threads, verdicts, [], review_body_comments=review_body)
    assert "2 review-level comments" in dashboard


def test_dashboard_omits_review_body_when_empty():
    threads = {"T_1": {"state": STATE_NEW}}
    verdicts = []
    dashboard = render_dashboard(42, threads, verdicts, [], review_body_comments=[])
    assert "review-level" not in dashboard


def test_dashboard_backward_compatible_without_review_body():
    threads = {"T_1": {"state": STATE_NEW}}
    verdicts = []
    dashboard = render_dashboard(42, threads, verdicts, [])
    assert "review-level" not in dashboard
