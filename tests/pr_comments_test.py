"""Tests for pr_comments library."""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pr_state
from pr_comments import (
    load_state, save_state, empty_state, compute_thread_state, sync_threads,
    render_dashboard, render_status, render_triage_status, render_fix_status,
    STATE_NEW, STATE_ADDRESSED, STATE_VERIFIED, STATE_RESOLVED,
)


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


# ── render_status ────────────────────────────────────────────────────────


def test_render_status_not_checked():
    c = pr_state.CommentsSummary()
    assert render_status(c) == ["**Comments**: not checked yet"]


def test_render_status_with_threads():
    c = pr_state.CommentsSummary(
        total_threads=5, by_state={"new": 2, "resolved": 3}, updated_at="t",
    )
    lines = render_status(c)
    assert "5 thread(s)" in lines[0]
    assert any("new: 2" in l for l in lines)
    assert any("resolved: 3" in l for l in lines)


def test_render_status_with_blocking_reviewers():
    c = pr_state.CommentsSummary(
        total_threads=1, blocking_reviewers=["alice", "bob"], updated_at="t",
    )
    lines = render_status(c)
    assert any("blocking: alice, bob" in l for l in lines)


# ── render_triage_status ─────────────────────────────────────────────────


def test_render_triage_status_not_run():
    t = pr_state.TriageSummary()
    assert render_triage_status(t) == ["**Triage**: not run yet"]


def test_render_triage_status_with_data():
    t = pr_state.TriageSummary(
        total=5, actionable=2, valid=1, questions=1, updated_at="2024-01-01T00:00:00Z",
    )
    result = render_triage_status(t)
    assert len(result) == 1
    assert "5 threads" in result[0]
    assert "2 actionable" in result[0]
    assert "1 valid" in result[0]
    assert "1 questions" in result[0]


# ── render_fix_status ────────────────────────────────────────────────────


def test_render_fix_status_not_run():
    f = pr_state.FixSummary()
    assert render_fix_status(f) == ["**Fix**: not run yet"]


def test_render_fix_status_with_data():
    f = pr_state.FixSummary(
        threads=[
            pr_state.ThreadOutcome(id="t1", action=pr_state.ThreadAction.FIXED),
            pr_state.ThreadOutcome(id="t2", action=pr_state.ThreadAction.FIXED),
            pr_state.ThreadOutcome(id="t3", action=pr_state.ThreadAction.DEFERRED),
            pr_state.ThreadOutcome(id="t4", action=pr_state.ThreadAction.DISMISSED),
        ],
        commit_sha="abc1234", commit_status="pushed",
        updated_at="2026-07-14T00:00:00+00:00",
    )
    lines = render_fix_status(f)
    assert "**2 fixed**" in lines[0]
    assert "1 deferred" in lines[0]
    assert "1 dismissed" in lines[0]
    assert "abc1234" in lines[0]
    assert "pushed" in lines[0]


def test_render_fix_status_needs_human():
    f = pr_state.FixSummary(
        threads=[
            pr_state.ThreadOutcome(id="t1", action=pr_state.ThreadAction.NEEDS_HUMAN),
            pr_state.ThreadOutcome(id="t2", action=pr_state.ThreadAction.NEEDS_HUMAN),
        ],
        updated_at="2026-07-14T00:00:00+00:00",
    )
    lines = render_fix_status(f)
    assert "2 need discussion" in lines[0]


def test_render_fix_status_deferred_issue():
    f = pr_state.FixSummary(
        threads=[
            pr_state.ThreadOutcome(id="t1", action=pr_state.ThreadAction.DEFERRED),
        ],
        commit_sha="abc", commit_status="pushed",
        deferred_issue_id="ENG-456",
        updated_at="2026-07-14T00:00:00+00:00",
    )
    lines = render_fix_status(f)
    assert any("ENG-456" in line for line in lines)
    assert any("tracked in" in line for line in lines)


# ── post_issue_comment upsert ──────────────────────────────────────────────


MARKER = "<!-- pr-comments:summary -->"


def test_post_issue_comment_posts_new_without_marker():
    import pr_comments
    with patch.object(pr_comments, "_gh_post", return_value=(0, '{"html_url": "u"}')) as post, \
         patch.object(pr_comments, "_find_comment_by_marker") as find:
        url = pr_comments.post_issue_comment("owner/repo", 1, "body")
    assert url == "u"
    post.assert_called_once()
    find.assert_not_called()


def _pages(*pages):
    """Encode gh api --paginate --slurp output: an outer array of pages."""
    return json.dumps(list(pages))


def test_post_issue_comment_edits_existing_marked_comment():
    """A second round must update the first round's summary, not append to it."""
    import pr_comments
    listing = _pages([
        {"id": 10, "body": "unrelated"},
        {"id": 11, "body": f"{MARKER}\nround one"},
    ])
    with patch.object(pr_comments, "_paginated_json", return_value=(0, listing)), \
         patch.object(pr_comments, "_patch_issue_comment", return_value="u2") as patch_fn, \
         patch.object(pr_comments, "_gh_post") as post:
        url = pr_comments.post_issue_comment("owner/repo", 1, "round two", marker=MARKER)
    assert url == "u2"
    patch_fn.assert_called_once_with("owner/repo", 11, "round two")
    post.assert_not_called()


def test_post_issue_comment_posts_new_when_marker_absent():
    import pr_comments
    listing = _pages([{"id": 10, "body": "unrelated"}])
    with patch.object(pr_comments, "_paginated_json", return_value=(0, listing)), \
         patch.object(pr_comments, "_gh_post", return_value=(0, '{"html_url": "u"}')) as post:
        url = pr_comments.post_issue_comment("owner/repo", 1, "body", marker=MARKER)
    assert url == "u"
    post.assert_called_once()


def test_find_comment_by_marker_prefers_latest():
    import pr_comments
    listing = _pages([
        {"id": 10, "body": f"{MARKER}\nold"},
        {"id": 11, "body": f"{MARKER}\nnew"},
    ])
    with patch.object(pr_comments, "_paginated_json", return_value=(0, listing)):
        assert pr_comments._find_comment_by_marker("owner/repo", 1, MARKER) == (True, 11)


def test_find_comment_by_marker_spans_pages():
    """The marker comment is posted first, so on a busy PR it is not on page one."""
    import pr_comments
    listing = _pages(
        [{"id": 10, "body": f"{MARKER}\nround one"}],
        [{"id": 11, "body": "unrelated"}],
    )
    with patch.object(pr_comments, "_paginated_json", return_value=(0, listing)):
        assert pr_comments._find_comment_by_marker("owner/repo", 1, MARKER) == (True, 10)


def test_find_comment_by_marker_accepts_flat_listing():
    """A single unslurped page must still be readable."""
    import pr_comments
    listing = json.dumps([{"id": 12, "body": f"{MARKER}\nonly"}])
    with patch.object(pr_comments, "_paginated_json", return_value=(0, listing)):
        assert pr_comments._find_comment_by_marker("owner/repo", 1, MARKER) == (True, 12)


def test_find_comment_by_marker_reports_lookup_failure():
    """A failed listing must be distinguishable from an empty one."""
    import pr_comments
    for payload in ((1, ""), (0, "not json"), (0, '{"message": "Not Found"}')):
        with patch.object(pr_comments, "_paginated_json", return_value=payload):
            assert pr_comments._find_comment_by_marker("owner/repo", 1, MARKER) == (False, None)


def test_find_comment_by_marker_reports_empty_listing():
    import pr_comments
    with patch.object(pr_comments, "_paginated_json", return_value=(0, "[]")):
        assert pr_comments._find_comment_by_marker("owner/repo", 1, MARKER) == (True, None)


def test_post_issue_comment_logs_when_lookup_fails():
    """Falling back to a new comment on lookup failure must not be silent."""
    import pr_comments
    with patch.object(pr_comments, "_paginated_json", return_value=(1, "")), \
         patch.object(pr_comments, "_gh_post", return_value=(0, '{"html_url": "u"}')), \
         patch.object(pr_comments.log, "error") as err:
        url = pr_comments.post_issue_comment("owner/repo", 1, "body", marker=MARKER)
    assert url == "u"
    err.assert_called_once()


def test_patch_issue_comment_uses_patch_method():
    import pr_comments
    with patch.object(pr_comments, "_gh_post", return_value=(0, '{"html_url": "u"}')) as post:
        assert pr_comments._patch_issue_comment("owner/repo", 11, "body") == "u"
    assert post.call_args.kwargs["method"] == "PATCH"
    assert post.call_args[0][0] == "repos/owner/repo/issues/comments/11"
