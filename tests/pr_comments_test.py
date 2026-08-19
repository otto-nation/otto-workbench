"""Tests for pr_comments library."""

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pr_comments
import pr_state
import publishing
import review_issue
from pr_comments import (
    load_state, save_state, empty_state, compute_thread_state, sync_threads,
    fetch_threads, render_dashboard, render_status, render_triage_status,
    render_fix_status,
    CLOSEOUT_COMMAND,
    STATE_NEW, STATE_ADDRESSED, STATE_VERIFIED, STATE_RESOLVED,
)


# ── fetch_threads ───────────────────────────────────────────────────────────

def test_fetch_threads_uses_the_paginated_fetcher():
    with patch.object(pr_comments, "fetch_review_threads",
                      return_value=[{"id": "PRRT_1"}]) as fetcher:
        assert fetch_threads("owner", "repo", 42) == [{"id": "PRRT_1"}]
    fetcher.assert_called_once_with("owner/repo", 42)


def test_fetch_threads_prefers_prefetched_pr_data():
    pr_data = SimpleNamespace(review_threads=[{"id": "PRRT_cached"}])
    with patch.object(pr_comments, "fetch_review_threads") as fetcher:
        assert fetch_threads("owner", "repo", 42, pr_data) == [{"id": "PRRT_cached"}]
    fetcher.assert_not_called()


# ── fetch_issue_comments ────────────────────────────────────────────────────

_ISSUE_COMMENTS = json.dumps([
    {"id": 1, "user": {"login": "me"}, "body": "Applied: drop the retry"},
    {"id": 2, "user": {"login": "kgn"}, "body": "drop the retry"},
    {"id": 3, "user": {"login": "bot", "type": "Bot"}, "body": "coverage fell"},
])


def _rest_listing():
    return patch.object(pr_comments, "_gh_rest", return_value=(0, _ISSUE_COMMENTS))


def test_fetch_issue_comments_drops_our_own_by_default():
    with _rest_listing():
        got = pr_comments.fetch_issue_comments("owner/repo", 42, "me")
    assert [c["user"] for c in got] == ["kgn"]


def test_fetch_issue_comments_keeps_our_own_when_asked():
    """`include_self` is the contract `review-threads` reads its reply through."""
    with _rest_listing():
        got = pr_comments.fetch_issue_comments(
            "owner/repo", 42, "me", include_self=True)
    assert [c["user"] for c in got] == ["me", "kgn"]


def test_fetch_issue_comments_drops_bots_either_way():
    with _rest_listing():
        got = pr_comments.fetch_issue_comments(
            "owner/repo", 42, "me", include_self=True)
    assert "bot" not in [c["user"] for c in got]


@pytest.mark.parametrize("include_self,expected", [(False, "me"), (True, "")])
def test_fetch_issue_comments_passes_the_exclusion_on_to_pr_data(
        include_self, expected):
    """Prefetched data takes the same filter, so both paths answer alike."""
    pr_data = SimpleNamespace(non_self_issue_comments=lambda login: [{"user": login}])
    got = pr_comments.fetch_issue_comments(
        "owner/repo", 42, "me", pr_data, include_self=include_self)
    assert got == [{"user": expected}]


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


def test_save_never_exposes_a_truncated_file(monkeypatch):
    """Regression: this save was the one copy of the write-and-rename pattern
    that had drifted into a plain `open(path, "w")`, which truncates the target
    before the first byte lands. A failed write left the thread lifecycle state
    half-written — the corruption the read side then has to discard."""
    import serde

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "state.json"
        save_state(path, empty_state("owner/repo", 1, "user"))

        def _explode(obj, fp, **kwargs):
            fp.write('{"partial":')
            raise OSError("disk full")

        monkeypatch.setattr(serde.json, "dump", _explode)
        with pytest.raises(OSError):
            save_state(path, empty_state("owner/repo", 2, "user"))

        assert load_state(path)["pr_number"] == 1
        assert list(Path(tmp).glob("*.tmp")) == []


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


def test_last_comment_is_mine_ignores_resolution():
    """The predicate compute_thread_state cannot answer once a thread is resolved."""
    comments = _make_comments(("alice", "Use RunTx here"), ("isaacg", "Fixed."))
    state = compute_thread_state(comments, is_resolved=True, my_login="isaacg")
    assert state == "resolved"
    assert pr_comments.last_comment_is_mine(comments, "isaacg")


def test_last_comment_is_mine_is_false_when_a_reviewer_answered():
    comments = _make_comments(
        ("alice", "Use RunTx here"),
        ("isaacg", "Fixed."),
        ("alice", "Not quite"),
    )
    assert not pr_comments.last_comment_is_mine(comments, "isaacg")


@pytest.mark.parametrize("comments,login", [
    ([], "isaacg"),
    (_make_comments(("isaacg", "mine")), ""),
])
def test_last_comment_is_mine_needs_both_halves(comments, login):
    assert not pr_comments.last_comment_is_mine(comments, login)


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


# ── closeout debt ────────────────────────────────────────────────────────


def _fix_with_closeout(**kwargs) -> pr_state.FixSummary:
    """A pushed fix pass with three reply-owing outcomes and one that owes none."""
    return pr_state.FixSummary(
        threads=[
            pr_state.ThreadOutcome(id="t1", action=pr_state.ThreadAction.FIXED),
            pr_state.ThreadOutcome(id="t2", action=pr_state.ThreadAction.ALREADY_ADDRESSED),
            pr_state.ThreadOutcome(id="t3", action=pr_state.ThreadAction.DISMISSED),
            pr_state.ThreadOutcome(id="t4", action=pr_state.ThreadAction.NEEDS_HUMAN),
        ],
        commit_sha="abc1234", commit_status="pushed",
        updated_at="2026-07-14T00:00:00+00:00",
        **kwargs,
    )


def _closeout_line(lines: list[str]) -> str | None:
    return next((line for line in lines if "closeout owed" in line), None)


def test_closeout_debt_reads_both_flags():
    debt = pr_comments.closeout_debt(
        _fix_with_closeout(summary_deferred=True, replies_pending=True),
    )
    assert debt.owed is True
    assert debt.summary is True
    assert debt.replies is True
    # NEEDS_HUMAN owes no reply — only the three buckets --finish drains count.
    assert debt.reply_count == 3


def test_closeout_debt_clean_state_owes_nothing():
    debt = pr_comments.closeout_debt(_fix_with_closeout())
    assert debt.owed is False
    assert debt.describe() == ""


def test_render_fix_status_warns_when_summary_and_replies_are_owed():
    lines = render_fix_status(
        _fix_with_closeout(summary_deferred=True, replies_pending=True),
    )
    assert _closeout_line(lines) == (
        f"  ⚠ closeout owed: summary + 3 replies — run: {CLOSEOUT_COMMAND}"
    )


def test_render_fix_status_warns_for_a_deferred_summary_alone():
    lines = render_fix_status(_fix_with_closeout(summary_deferred=True))
    assert _closeout_line(lines) == (
        f"  ⚠ closeout owed: summary — run: {CLOSEOUT_COMMAND}"
    )


def test_render_fix_status_warns_for_a_pending_reply_queue_alone():
    lines = render_fix_status(_fix_with_closeout(replies_pending=True))
    assert _closeout_line(lines) == (
        f"  ⚠ closeout owed: 3 replies — run: {CLOSEOUT_COMMAND}"
    )


def test_render_fix_status_singularises_a_one_reply_queue():
    f = pr_state.FixSummary(
        threads=[pr_state.ThreadOutcome(id="t1", action=pr_state.ThreadAction.FIXED)],
        replies_pending=True,
        updated_at="2026-07-14T00:00:00+00:00",
    )
    assert _closeout_line(render_fix_status(f)) == (
        f"  ⚠ closeout owed: 1 reply — run: {CLOSEOUT_COMMAND}"
    )


def test_render_fix_status_says_replies_when_no_outcome_carries_the_count():
    """A queue whose outcomes were pruned still says replies are owed, not zero."""
    f = pr_state.FixSummary(
        threads=[], replies_pending=True, updated_at="2026-07-14T00:00:00+00:00",
    )
    assert _closeout_line(render_fix_status(f)) == (
        f"  ⚠ closeout owed: replies — run: {CLOSEOUT_COMMAND}"
    )


def test_render_fix_status_silent_when_nothing_is_owed():
    lines = render_fix_status(_fix_with_closeout())
    assert _closeout_line(lines) is None


# ── post_issue_comment upsert ──────────────────────────────────────────────


MARKER = "<!-- pr-comments:summary -->"


def test_post_issue_comment_posts_new_without_marker():
    with patch.object(pr_comments, "_gh_post", return_value=(0, '{"html_url": "u"}')) as post, \
         patch.object(pr_comments, "find_marker_comment",
                      autospec=True) as find:
        url = pr_comments.post_issue_comment("owner/repo", 1, "body")
    assert url == "u"
    post.assert_called_once()
    find.assert_not_called()


def _pages(*pages):
    """Encode gh api --paginate --slurp output: an outer array of pages."""
    return json.dumps(list(pages))


def test_post_issue_comment_edits_existing_marked_comment():
    """A second round must update the first round's summary, not append to it."""
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
    listing = _pages([{"id": 10, "body": "unrelated"}])
    with patch.object(pr_comments, "_paginated_json", return_value=(0, listing)), \
         patch.object(pr_comments, "_gh_post", return_value=(0, '{"html_url": "u"}')) as post:
        url = pr_comments.post_issue_comment("owner/repo", 1, "body", marker=MARKER)
    assert url == "u"
    post.assert_called_once()


def _found(comment_id, body):
    return pr_comments.MarkerComment(True, comment_id, body)


def test_find_marker_comment_prefers_latest():
    listing = _pages([
        {"id": 10, "body": f"{MARKER}\nold"},
        {"id": 11, "body": f"{MARKER}\nnew"},
    ])
    with patch.object(pr_comments, "_paginated_json", return_value=(0, listing)):
        found = pr_comments.find_marker_comment("owner/repo", 1, MARKER)
    assert found == _found(11, f"{MARKER}\nnew")


def test_find_marker_comment_spans_pages():
    """The marker comment is posted first, so on a busy PR it is not on page one."""
    listing = _pages(
        [{"id": 10, "body": f"{MARKER}\nround one"}],
        [{"id": 11, "body": "unrelated"}],
    )
    with patch.object(pr_comments, "_paginated_json", return_value=(0, listing)):
        found = pr_comments.find_marker_comment("owner/repo", 1, MARKER)
    assert found == _found(10, f"{MARKER}\nround one")


def test_find_marker_comment_carries_the_timeline():
    """The upsert has to know whether anyone spoke below the summary."""
    listing = _pages([
        {"id": 10, "body": f"{MARKER}\nround one", "created_at": "2026-01-01T00:00:00Z"},
        {"id": 11, "body": "not so fast", "created_at": "2026-01-02T00:00:00Z"},
    ])
    with patch.object(pr_comments, "_paginated_json", return_value=(0, listing)):
        found = pr_comments.find_marker_comment("owner/repo", 1, MARKER)
    assert found.created_at == "2026-01-01T00:00:00Z"
    assert found.newest_other_at == "2026-01-02T00:00:00Z"


def test_find_marker_comment_does_not_read_an_older_summary_as_an_answer():
    """A superseded summary is ours; reading it as a reply reposts forever."""
    listing = _pages([
        {"id": 10, "body": f"{MARKER}\nround one", "created_at": "2026-01-01T00:00:00Z"},
        {"id": 11, "body": f"{MARKER}\nround two", "created_at": "2026-01-03T00:00:00Z"},
    ])
    with patch.object(pr_comments, "_paginated_json", return_value=(0, listing)):
        found = pr_comments.find_marker_comment("owner/repo", 1, MARKER)
    assert found.comment_id == 11
    assert found.newest_other_at == ""


def test_find_marker_comment_accepts_flat_listing():
    """A single unslurped page must still be readable."""
    listing = json.dumps([{"id": 12, "body": f"{MARKER}\nonly"}])
    with patch.object(pr_comments, "_paginated_json", return_value=(0, listing)):
        found = pr_comments.find_marker_comment("owner/repo", 1, MARKER)
    assert found == _found(12, f"{MARKER}\nonly")


@pytest.mark.parametrize("payload", [
    (1, ""),
    (0, "not json"),
    (0, '{"message": "Not Found"}'),
])
def test_find_marker_comment_reports_lookup_failure(payload):
    """A failed listing must be distinguishable from an empty one.

    A caller reconciling against the published body reads an empty `body` as
    "the comment said nothing", so `found` has to carry the difference.
    """
    with patch.object(pr_comments, "_paginated_json", return_value=payload):
        assert pr_comments.find_marker_comment("owner/repo", 1, MARKER) == \
            pr_comments.MarkerComment(found=False)


def test_find_marker_comment_reports_empty_listing():
    with patch.object(pr_comments, "_paginated_json", return_value=(0, "[]")):
        assert pr_comments.find_marker_comment("owner/repo", 1, MARKER) == \
            pr_comments.MarkerComment(found=True)


def test_post_issue_comment_reuses_a_supplied_lookup():
    """A caller that already read the comment must not pay for the listing twice."""
    with patch.object(pr_comments, "_paginated_json") as listing, \
         patch.object(pr_comments, "_patch_issue_comment", return_value="u2") as patch_fn:
        url = pr_comments.post_issue_comment(
            "owner/repo", 1, "round two", marker=MARKER,
            existing=_found(11, f"{MARKER}\nround one"),
        )
    assert url == "u2"
    listing.assert_not_called()
    patch_fn.assert_called_once_with("owner/repo", 11, "round two")


def test_post_issue_comment_logs_when_lookup_fails():
    """Falling back to a new comment on lookup failure must not be silent."""
    with patch.object(pr_comments, "_paginated_json", return_value=(1, "")), \
         patch.object(pr_comments, "_gh_post", return_value=(0, '{"html_url": "u"}')), \
         patch.object(pr_comments.log, "error") as err:
        url = pr_comments.post_issue_comment("owner/repo", 1, "body", marker=MARKER)
    assert url == "u"
    err.assert_called_once()


def test_patch_issue_comment_uses_patch_method():
    with patch.object(pr_comments, "_gh_post", return_value=(0, '{"html_url": "u"}')) as post:
        assert pr_comments._patch_issue_comment("owner/repo", 11, "body") == "u"
    assert post.call_args.kwargs["method"] == "PATCH"
    assert post.call_args[0][0] == "repos/owner/repo/issues/comments/11"


def test_patch_thread_reply_uses_patch_method():
    with patch.object(pr_comments, "_gh_post", return_value=(0, "")) as post:
        assert pr_comments.patch_thread_reply("owner/repo", 99, "body") is True
    assert post.call_args.kwargs["method"] == "PATCH"
    assert post.call_args[0][0] == "repos/owner/repo/pulls/comments/99"


def test_patch_thread_reply_reports_failure():
    with patch.object(pr_comments, "_gh_post", return_value=(1, "")):
        assert pr_comments.patch_thread_reply("owner/repo", 99, "body") is False


# ── Publishing gate ──────────────────────────────────────────────────────────


@pytest.fixture
def no_subprocess(monkeypatch):
    """Any external call in draft mode is a bug, so make one impossible to miss."""
    def boom(*a, **kw):
        raise AssertionError(f"a subprocess ran in draft mode: {a}")
    monkeypatch.setattr(pr_comments.subprocess, "run", boom)
    monkeypatch.setattr(review_issue.subprocess, "run", boom)


class TestPublishingGate:
    """Nothing reaches GitHub until --post says so."""

    def test_defaults_to_drafts(self):
        assert publishing.enabled() is False

    def test_thread_reply_is_not_posted(self, no_subprocess):
        assert pr_comments.post_thread_reply("o/r", 1, 99, "body") is False

    def test_issue_comment_is_not_posted(self, no_subprocess):
        assert pr_comments.post_issue_comment("o/r", 1, "body") is None

    def test_thread_is_not_resolved(self, no_subprocess):
        assert pr_comments.resolve_thread("PRRT_1") is False

    def test_draft_body_goes_to_stderr(self, no_subprocess, capsys):
        pr_comments.post_thread_reply("o/r", 1, 99, "the reply text")
        captured = capsys.readouterr()
        assert "the reply text" in captured.err
        assert captured.out == ""

    def test_enable_opens_the_gate(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            pr_comments.subprocess, "run",
            lambda *a, **kw: calls.append(a) or SimpleNamespace(
                returncode=0, stdout='{"html_url": "u"}', stderr="",
            ),
        )
        publishing.enable()
        assert pr_comments.post_thread_reply("o/r", 1, 99, "body") is True
        assert len(calls) == 1


class TestPublishingHold:
    """A hold outranks --post, and nothing reopens it."""

    def test_hold_shuts_a_gate_post_had_opened(self, no_subprocess):
        publishing.enable()
        publishing.hold("discussion open")
        assert publishing.enabled() is False
        assert pr_comments.post_thread_reply("o/r", 1, 99, "body") is False

    def test_enable_after_a_hold_does_not_reopen(self, no_subprocess):
        publishing.hold("discussion open")
        publishing.enable()
        assert publishing.enabled() is False

    def test_the_first_reason_is_the_one_kept(self):
        publishing.hold("discussion open")
        publishing.hold("something else")
        assert publishing.held() == "discussion open"

    def test_no_hold_by_default(self):
        assert publishing.held() == ""


class TestIssueTrackerGate:
    """A tracking issue is as public as a reply — same gate.

    Deferral issues were filed from an incorrect review claim once; drafting them
    keeps that mistake on this machine.
    """

    def test_issue_is_not_created(self, no_subprocess):
        created = review_issue.create_issue(
            "linear", "ENG", "title", "description",
        )
        assert created is None

    def test_issue_is_not_updated(self, no_subprocess):
        assert review_issue.update_issue("linear", "ENG-1", "description") is False

    def test_draft_names_the_provider_and_title(self, no_subprocess, capsys):
        review_issue.create_issue("linear", "ENG", "the issue title", "body")
        assert "the issue title" in capsys.readouterr().err
