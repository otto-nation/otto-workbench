"""Tests for pr_comments library."""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from conftest import triaged_thread_record

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from pr import comments as pr_comments
from pr import domains as pr_domains
from pr import state as pr_state
from core import publishing
from review import issue as review_issue
from core.proc import CmdResult
from gh.pr_reads import ThreadSet
from pr.comments import (
    compute_thread_state, sync_threads, fetch_threads, render_dashboard,
)
from pr.comments_state import ThreadRecord, ThreadState


REPO = "owner/repo"


# ── fetch_threads ───────────────────────────────────────────────────────────

def test_fetch_threads_uses_the_paginated_fetcher():
    with patch.object(pr_comments, "fetch_review_threads",
                      return_value=ThreadSet([{"id": "PRRT_1"}], complete=True)) as fetcher:
        assert fetch_threads("owner", "repo", 42) == ThreadSet([{"id": "PRRT_1"}], complete=True)
    fetcher.assert_called_once_with("owner/repo", 42)


def test_fetch_threads_prefers_prefetched_pr_data():
    pr_data = SimpleNamespace(review_threads=[{"id": "PRRT_cached"}], threads_complete=True)
    with patch.object(pr_comments, "fetch_review_threads") as fetcher:
        fetched = fetch_threads("owner", "repo", 42, pr_data)
    assert fetched.threads == [{"id": "PRRT_cached"}]
    assert fetched.complete
    fetcher.assert_not_called()


def test_fetch_threads_carries_a_short_prefetch_through():
    """The consolidated read knows the thread walk fell short; dropping that
    here is how the ledger came to be written from a partial set."""
    pr_data = SimpleNamespace(review_threads=[{"id": "PRRT_1"}], threads_complete=False)
    with patch.object(pr_comments, "fetch_review_threads") as fetcher:
        fetched = fetch_threads("owner", "repo", 42, pr_data)
    assert fetched.complete is False
    fetcher.assert_not_called()


# ── fetch_issue_comments ────────────────────────────────────────────────────

_ISSUE_COMMENTS = [
    {"id": 1, "user": {"login": "me"}, "body": "Applied: drop the retry"},
    {"id": 2, "user": {"login": "kgn"}, "body": "drop the retry"},
    {"id": 3, "user": {"login": "bot", "type": "Bot"}, "body": "coverage fell"},
]


def _rest_listing():
    return patch.object(pr_comments.gh_client, "api_json", return_value=_ISSUE_COMMENTS)


def test_fetch_issue_comments_drops_our_own_by_default():
    with _rest_listing():
        got = pr_comments.fetch_issue_comments(REPO, 42, "me")
    assert [c["user"] for c in got] == ["kgn"]


def test_fetch_issue_comments_keeps_our_own_when_asked():
    """`include_self` is the contract `review-threads` reads its reply through."""
    with _rest_listing():
        got = pr_comments.fetch_issue_comments(
            REPO, 42, "me", include_self=True)
    assert [c["user"] for c in got] == ["me", "kgn"]


def test_fetch_issue_comments_drops_bots_either_way():
    with _rest_listing():
        got = pr_comments.fetch_issue_comments(
            REPO, 42, "me", include_self=True)
    assert "bot" not in [c["user"] for c in got]


@pytest.mark.parametrize("include_self,expected", [(False, "me"), (True, "")])
def test_fetch_issue_comments_passes_the_exclusion_on_to_pr_data(
        include_self, expected):
    """Prefetched data takes the same filter, so both paths answer alike."""
    pr_data = SimpleNamespace(non_self_issue_comments=lambda login: [{"user": login}])
    got = pr_comments.fetch_issue_comments(
        REPO, 42, "me", pr_data, include_self=include_self)
    assert got == [{"user": expected}]


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
        "T_abc": ThreadRecord(
            state=ThreadState.ADDRESSED,
            classification="suggestion",
            reviewer="alice",
            summary="Old summary",
            decided_at="2026-06-14T15:00:00Z",
            last_seen_reply_id=1001,
        ),
    }
    result = sync_threads(ThreadSet(threads), prior_threads, "isaacg")
    assert result["T_abc"].classification is None
    assert result["T_abc"].summary is None
    assert result["T_abc"].decided_at is None


def test_sync_new_thread_no_prior_state():
    threads = [{
        "id": "T_abc",
        "isResolved": False,
        "comments": {"nodes": _make_comments(("alice", "Fix this"))},
    }]
    prior_threads = {}
    result = sync_threads(ThreadSet(threads), prior_threads, "isaacg")
    assert "T_abc" in result
    assert result["T_abc"].state == ThreadState.NEW
    assert result["T_abc"].reviewer == "alice"
    assert result["T_abc"].last_seen_reply_id == 1000


def test_sync_keeps_cached_classification():
    threads = [{
        "id": "T_abc",
        "isResolved": False,
        "comments": {"nodes": _make_comments(("alice", "Fix this"))},
    }]
    prior_threads = {
        "T_abc": ThreadRecord(
            state=ThreadState.NEW,
            classification="suggestion",
            reviewer="alice",
            file="handler.go",
            line=42,
            summary="Fix the handler",
            decided_at="2026-06-14T15:00:00Z",
            last_seen_reply_id=1000,
        ),
    }
    result = sync_threads(ThreadSet(threads), prior_threads, "isaacg")
    assert result["T_abc"].classification == "suggestion"
    assert result["T_abc"].summary == "Fix the handler"


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
        "T_abc": ThreadRecord(
            state=ThreadState.NEW,
            classification="suggestion",
            reviewer="alice",
            last_seen_reply_id=1000,
        ),
    }
    result = sync_threads(ThreadSet(threads), prior_threads, "isaacg")
    assert result["T_abc"].state == ThreadState.ADDRESSED
    assert result["T_abc"].last_seen_reply_id == 1001


def test_sync_resolved_on_github_overrides():
    threads = [{
        "id": "T_abc",
        "isResolved": True,
        "comments": {"nodes": _make_comments(("alice", "Fix this"))},
    }]
    prior_threads = {
        "T_abc": ThreadRecord(
            state=ThreadState.NEW, last_seen_reply_id=1000, reviewer="alice"),
    }
    result = sync_threads(ThreadSet(threads), prior_threads, "isaacg")
    assert result["T_abc"].state == ThreadState.RESOLVED


def test_dashboard_shows_review_body_comments():
    threads = {"T_1": ThreadRecord(state=ThreadState.NEW)}
    verdicts = [{"user": "alice", "state": "COMMENTED", "submitted_at": "2026-01-01T00:00:00Z"}]
    review_body = [
        {"id": 1, "user": "alice", "body": "Overlaps with #2284", "state": "COMMENTED"},
        {"id": 2, "user": "bot", "body": "Acronym bug", "state": "COMMENTED"},
    ]
    dashboard = render_dashboard(42, threads, verdicts, [], review_body_comments=review_body)
    assert "2 review-level comments" in dashboard


def test_dashboard_omits_review_body_when_empty():
    threads = {"T_1": ThreadRecord(state=ThreadState.NEW)}
    verdicts = []
    dashboard = render_dashboard(42, threads, verdicts, [], review_body_comments=[])
    assert "review-level" not in dashboard


def test_dashboard_backward_compatible_without_review_body():
    threads = {"T_1": ThreadRecord(state=ThreadState.NEW)}
    verdicts = []
    dashboard = render_dashboard(42, threads, verdicts, [])
    assert "review-level" not in dashboard


# ── post_issue_comment upsert ──────────────────────────────────────────────


MARKER = "<!-- pr-comments:summary -->"


def _posted(url: str = "u") -> CmdResult:
    """What `_gh_post` answers a successful write with."""
    return CmdResult(returncode=0, stdout=json.dumps({"html_url": url}))


def test_post_issue_comment_posts_new_without_marker():
    with patch.object(pr_comments, "_gh_post", return_value=_posted()) as post, \
         patch.object(pr_comments, "find_marker_comment",
                      autospec=True) as find:
        url = pr_comments.post_issue_comment(REPO, 1, "body")
    assert url == "u"
    post.assert_called_once()
    find.assert_not_called()


def _pages(*pages):
    """Decoded gh api --paginate --slurp output: an outer array of pages."""
    return list(pages)


def _listing(value):
    return patch.object(pr_comments.gh_client, "api_json", return_value=value)


def test_post_issue_comment_edits_existing_marked_comment():
    """A second round must update the first round's summary, not append to it."""
    listing = _pages([
        {"id": 10, "body": "unrelated"},
        {"id": 11, "body": f"{MARKER}\nround one"},
    ])
    with _listing(listing), \
         patch.object(pr_comments, "_patch_issue_comment", return_value="u2") as patch_fn, \
         patch.object(pr_comments, "_gh_post") as post:
        url = pr_comments.post_issue_comment(REPO, 1, "round two", marker=MARKER)
    assert url == "u2"
    patch_fn.assert_called_once_with(REPO, 11, "round two")
    post.assert_not_called()


def test_post_issue_comment_posts_new_when_marker_absent():
    listing = _pages([{"id": 10, "body": "unrelated"}])
    with _listing(listing), \
         patch.object(pr_comments, "_gh_post", return_value=_posted()) as post:
        url = pr_comments.post_issue_comment(REPO, 1, "body", marker=MARKER)
    assert url == "u"
    post.assert_called_once()


def _found(comment_id, body):
    return pr_comments.MarkerComment(True, comment_id, body)


def test_find_marker_comment_prefers_latest():
    listing = _pages([
        {"id": 10, "body": f"{MARKER}\nold"},
        {"id": 11, "body": f"{MARKER}\nnew"},
    ])
    with _listing(listing):
        found = pr_comments.find_marker_comment(REPO, 1, MARKER)
    assert found == _found(11, f"{MARKER}\nnew")


def test_find_marker_comment_spans_pages():
    """The marker comment is posted first, so on a busy PR it is not on page one."""
    listing = _pages(
        [{"id": 10, "body": f"{MARKER}\nround one"}],
        [{"id": 11, "body": "unrelated"}],
    )
    with _listing(listing):
        found = pr_comments.find_marker_comment(REPO, 1, MARKER)
    assert found == _found(10, f"{MARKER}\nround one")


def test_find_marker_comment_carries_the_timeline():
    """The upsert has to know whether anyone spoke below the summary."""
    listing = _pages([
        {"id": 10, "body": f"{MARKER}\nround one", "created_at": "2026-01-01T00:00:00Z"},
        {"id": 11, "body": "not so fast", "created_at": "2026-01-02T00:00:00Z"},
    ])
    with _listing(listing):
        found = pr_comments.find_marker_comment(REPO, 1, MARKER)
    assert found.created_at == "2026-01-01T00:00:00Z"
    assert found.newest_other_at == "2026-01-02T00:00:00Z"


def test_find_marker_comment_does_not_read_an_older_summary_as_an_answer():
    """A superseded summary is ours; reading it as a reply reposts forever."""
    listing = _pages([
        {"id": 10, "body": f"{MARKER}\nround one", "created_at": "2026-01-01T00:00:00Z"},
        {"id": 11, "body": f"{MARKER}\nround two", "created_at": "2026-01-03T00:00:00Z"},
    ])
    with _listing(listing):
        found = pr_comments.find_marker_comment(REPO, 1, MARKER)
    assert found.comment_id == 11
    assert found.newest_other_at == ""


def test_find_marker_comment_accepts_flat_listing():
    """A single unslurped page must still be readable."""
    listing = [{"id": 12, "body": f"{MARKER}\nonly"}]
    with _listing(listing):
        found = pr_comments.find_marker_comment(REPO, 1, MARKER)
    assert found == _found(12, f"{MARKER}\nonly")


@pytest.mark.parametrize("payload", [
    None,
    {"message": "Not Found"},
])
def test_find_marker_comment_reports_lookup_failure(payload):
    """A failed listing must be distinguishable from an empty one.

    A caller reconciling against the published body reads an empty `body` as
    "the comment said nothing", so `found` has to carry the difference.
    """
    with _listing(payload):
        assert pr_comments.find_marker_comment(REPO, 1, MARKER) == \
            pr_comments.MarkerComment(found=False)


def test_find_marker_comment_reports_empty_listing():
    with _listing([]):
        assert pr_comments.find_marker_comment(REPO, 1, MARKER) == \
            pr_comments.MarkerComment(found=True)


def test_find_marker_comments_keeps_every_round_oldest_first():
    """A caller spreading its record across comments has to link the earlier ones."""
    listing = _pages([
        {"id": 10, "body": f"{MARKER}\nround one"},
        {"id": 11, "body": "unrelated"},
        {"id": 12, "body": f"{MARKER}\nround two"},
    ])
    with _listing(listing):
        history = pr_comments.find_marker_comments(REPO, 1, MARKER)
    assert [c.comment_id for c in history.comments] == [10, 12]
    assert history.bodies == [f"{MARKER}\nround one", f"{MARKER}\nround two"]
    assert history.newest.comment_id == 12


def test_find_marker_comments_carries_each_comments_url():
    """The footer chain links comments, so the listing's link is the only source."""
    listing = _pages([
        {"id": 10, "body": MARKER, "html_url": "https://gh/pull/1#issuecomment-10"},
    ])
    with _listing(listing):
        history = pr_comments.find_marker_comments(REPO, 1, MARKER)
    assert history.comments[0].url == "https://gh/pull/1#issuecomment-10"


def test_find_marker_comments_dates_the_body_as_well_as_the_comment():
    """A summary edited in place holds rows for surfaces newer than its post
    time, so a caller asking what the record covers needs the edit time too."""
    listing = _pages([
        {"id": 10, "body": MARKER,
         "created_at": "2026-01-02T00:00:00Z",
         "updated_at": "2026-01-06T00:00:00Z"},
    ])
    with _listing(listing):
        history = pr_comments.find_marker_comments(REPO, 1, MARKER)
    assert history.comments[0].created_at == "2026-01-02T00:00:00Z"
    assert history.comments[0].updated_at == "2026-01-06T00:00:00Z"


def test_find_marker_comments_reports_an_unread_listing_as_no_history():
    """`found` distinguishes it from a PR that genuinely has no summary yet."""
    with _listing(None):
        history = pr_comments.find_marker_comments(REPO, 1, MARKER)
    assert history == pr_comments.MarkerHistory(found=False)
    assert history.newest == pr_comments.MarkerComment(found=False)


def test_marker_history_newest_stands_in_for_an_unmarked_pr():
    """The upsert target of a PR with no summary is an empty comment, not None."""
    history = pr_comments.MarkerHistory(found=True, newest_other_at="2026-01-02T00:00:00Z")
    assert history.newest == pr_comments.MarkerComment(
        found=True, newest_other_at="2026-01-02T00:00:00Z")
    assert history.bodies == []


def test_post_issue_comment_reuses_a_supplied_lookup():
    """A caller that already read the comment must not pay for the listing twice."""
    with patch.object(pr_comments.gh_client, "api_json") as listing, \
         patch.object(pr_comments, "_patch_issue_comment", return_value="u2") as patch_fn:
        url = pr_comments.post_issue_comment(
            REPO, 1, "round two", marker=MARKER,
            existing=_found(11, f"{MARKER}\nround one"),
        )
    assert url == "u2"
    listing.assert_not_called()
    patch_fn.assert_called_once_with(REPO, 11, "round two")


def test_post_issue_comment_logs_when_lookup_fails():
    """Falling back to a new comment on lookup failure must not be silent."""
    with _listing(None), \
         patch.object(pr_comments, "_gh_post", return_value=_posted()), \
         patch.object(pr_comments.log, "error") as err:
        url = pr_comments.post_issue_comment(REPO, 1, "body", marker=MARKER)
    assert url == "u"
    err.assert_called_once()


def test_patch_issue_comment_uses_patch_method():
    with patch.object(pr_comments, "_gh_post", return_value=_posted()) as post:
        assert pr_comments._patch_issue_comment(REPO, 11, "body") == "u"
    assert post.call_args.kwargs["method"] == "PATCH"
    assert post.call_args[0][0] == f"repos/{REPO}/issues/comments/11"


def test_patch_thread_reply_uses_patch_method():
    with patch.object(pr_comments, "_gh_post", return_value=CmdResult(0)) as post:
        assert pr_comments.patch_thread_reply(REPO, 99, "body") is True
    assert post.call_args.kwargs["method"] == "PATCH"
    assert post.call_args[0][0] == f"repos/{REPO}/pulls/comments/99"


def test_patch_thread_reply_reports_failure():
    with patch.object(pr_comments, "_gh_post", return_value=CmdResult(1)):
        assert pr_comments.patch_thread_reply(REPO, 99, "body") is False


def test_update_pr_body_patches_the_pull_endpoint():
    with patch.object(pr_comments, "_gh_post", return_value=CmdResult(0)) as post:
        assert pr_comments.update_pr_body(REPO, 7, "new body") is True
    assert post.call_args.kwargs["method"] == "PATCH"
    assert post.call_args[0][0] == f"repos/{REPO}/pulls/7"
    assert post.call_args[0][1] == "new body"


def test_update_pr_body_reports_failure():
    with patch.object(pr_comments, "_gh_post", return_value=CmdResult(1)):
        assert pr_comments.update_pr_body(REPO, 7, "new body") is False


# ── Publishing gate ──────────────────────────────────────────────────────────


@pytest.fixture
def no_subprocess(monkeypatch):
    """Any external call in draft mode is a bug, so make one impossible to miss."""
    def boom(*a, **kw):
        raise AssertionError(f"a subprocess ran in draft mode: {a}")
    monkeypatch.setattr("core.proc.subprocess.run", boom)
    monkeypatch.setattr(review_issue.proc, "run", boom)


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

    def test_pr_description_is_not_edited(self, no_subprocess):
        assert pr_comments.update_pr_body("o/r", 1, "new body") is False

    def test_pr_description_draft_goes_to_stderr(self, no_subprocess, capsys):
        pr_comments.update_pr_body("o/r", 1, "the rewritten description")
        assert "the rewritten description" in capsys.readouterr().err

    def test_draft_body_goes_to_stderr(self, no_subprocess, capsys):
        pr_comments.post_thread_reply("o/r", 1, 99, "the reply text")
        captured = capsys.readouterr()
        assert "the reply text" in captured.err
        assert captured.out == ""

    def test_enable_opens_the_gate(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            "core.proc.subprocess.run",
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
        assert created.filed is False
        assert created.issue == review_issue.CreatedIssue()

    def test_a_declined_write_is_not_a_failed_one(self, no_subprocess):
        """The gate declining a write owes nothing — a refused tracker does."""
        created = review_issue.create_issue(
            "linear", "ENG", "title", "description",
        )
        assert created.delivery is review_issue.IssueDelivery.SKIPPED
        assert created.owed is False

    def test_issue_is_not_updated(self, no_subprocess):
        assert review_issue.update_issue("linear", "ENG-1", "description") is False

    def test_draft_names_the_provider_and_title(self, no_subprocess, capsys):
        review_issue.create_issue("linear", "ENG", "the issue title", "body")
        assert "the issue title" in capsys.readouterr().err


# ── The reviewer age column ─────────────────────────────────────────────────
#
# `relative_time` itself is pinned in `text_test.py`, where it lives. What is
# here is that the dashboard still reaches it.


# passes-at-base: behaviour this change moved rather than added — the helper left this module for core.text, and the case holds that the dashboard still reaches it
def test_reviewer_verdicts_are_dated():
    submitted = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    out = render_dashboard(
        7, {}, [{"user": "alice", "state": "APPROVED", "submitted_at": submitted}], [],
    )
    assert "@alice — APPROVED (3 days ago)" in out


# ── An incomplete fetch must not erase what it could not see ────────────────
#
# The triage fields are the only thing on a ThreadRecord that no API call
# rebuilds — a person decided them. sync_threads builds its result from the
# threads it was handed, so a short fetch silently dropped the records for
# every thread it missed. The pair below is the whole argument: the flag is
# what lets the carry-forward happen without also resurrecting threads that
# were really deleted.


_triaged = triaged_thread_record


def test_sync_keeps_a_triage_verdict_for_a_thread_the_fetch_could_not_reach():
    """#1354: a thread on an unread page is unknown, not gone."""
    reached = [{
        "id": "T_page1",
        "isResolved": False,
        "comments": {"nodes": _make_comments(("alice", "Fix this"))},
    }]
    prior = {"T_page1": _triaged("page one"), "T_page2": _triaged("page two")}

    result = sync_threads(ThreadSet(reached, complete=False), prior, "isaacg")

    assert "T_page2" in result
    assert result["T_page2"].classification == "suggestion"
    assert result["T_page2"].summary == "page two"
    assert result["T_page2"].decided_at == "2026-06-14T15:00:00Z"


def test_sync_drops_a_thread_absent_from_a_complete_fetch():
    """The reaper. Without this the carry-forward above would strand a record
    for every thread GitHub really deleted, and nothing would ever clear it."""
    reached = [{
        "id": "T_page1",
        "isResolved": False,
        "comments": {"nodes": _make_comments(("alice", "Fix this"))},
    }]
    prior = {"T_page1": _triaged("page one"), "T_deleted": _triaged("gone")}

    result = sync_threads(ThreadSet(reached, complete=True), prior, "isaacg")

    assert "T_deleted" not in result


def test_sync_prefers_the_fetched_record_over_the_carried_one():
    """The seed must not shadow the loop: a thread we did read is authoritative
    even when the fetch as a whole fell short."""
    prior = {"T_abc": _triaged("stale")}
    reached = [{
        "id": "T_abc",
        "isResolved": True,
        "comments": {"nodes": _make_comments(("alice", "Fix this"))},
    }]

    result = sync_threads(ThreadSet(reached, complete=False), prior, "isaacg")

    assert result["T_abc"].state == ThreadState.RESOLVED
    assert result["T_abc"].summary == "stale"


# ── The two fetch paths must date a comment the same way ────────────────────
#
# GraphQL sends `lastEditedAt: null` for a comment nobody has edited; REST has
# no such field and sends `updated_at == created_at`. Seen-tracking compares
# the recorded stamp against the fetched one, so if the paths disagree about an
# untouched comment, a run that alternates between them re-reports it forever.


class TestRestEditStamp:

    def test_an_unedited_comment_has_no_stamp(self):
        """`updated_at == created_at` is REST's way of saying "never edited"."""
        assert pr_comments._rest_edit_stamp({
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }) == ""

    def test_an_edited_comment_carries_the_edit_time(self):
        assert pr_comments._rest_edit_stamp({
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-02-01T00:00:00Z",
        }) == "2026-02-01T00:00:00Z"

    def test_a_payload_missing_both_fields_has_no_stamp(self):
        assert pr_comments._rest_edit_stamp({}) == ""

    # The payloads below are real, taken from cli/cli#9000: comment 2082656785
    # was edited, the other two were not. Both APIs were queried for the same
    # three comments so the two columns are the same comments, not a guess at
    # what GitHub sends.
    @pytest.mark.parametrize("graphql_last_edited,rest,expected", [
        # An untouched comment: GraphQL nulls the field, REST equates the two
        # timestamps. Both must reduce to "" or seen-ness flips whenever the
        # fetch path changes, and every comment is re-reported forever.
        (None,
         {"created_at": "2024-04-26T21:44:55Z", "updated_at": "2024-04-26T21:44:55Z"},
         ""),
        # An edited one: both APIs report the same instant, so the recorded
        # stamp is the same string either way.
        ("2024-04-29T13:04:57Z",
         {"created_at": "2024-04-29T12:56:41Z", "updated_at": "2024-04-29T13:04:57Z"},
         "2024-04-29T13:04:57Z"),
    ])
    def test_the_two_fetch_paths_record_the_same_stamp(
        self, graphql_last_edited, rest, expected,
    ):
        """The regression this normalisation exists to prevent."""
        graphql_stamp = graphql_last_edited or ""   # non_self_issue_comments
        assert graphql_stamp == expected
        assert pr_comments._rest_edit_stamp(rest) == expected


# ── A demand added by editing a comment is not "addressed" ──────────────────
#
# An edit posts nothing and moves nothing, so a thread whose reviewer rewrote
# their comment after our reply still ended with us and still read as
# ADDRESSED. `settlement_for` grades an ADDRESSED thread SETTLED_ELSEWHERE, so
# the added demand was not merely missed — it was recorded as answered and
# closed out.


class TestRewrittenAfterOurReply:

    @staticmethod
    def _thread(reviewer_edit=None, my_edit=None) -> list[dict]:
        return [
            {"author": {"login": "alice"}, "body": "nit: rename this",
             "createdAt": "2026-01-01T00:00:00Z", "lastEditedAt": reviewer_edit},
            {"author": {"login": "me"}, "body": "Renamed in abc123",
             "createdAt": "2026-01-02T00:00:00Z", "lastEditedAt": my_edit},
        ]

    def test_an_edit_after_our_reply_reopens_the_thread(self):
        """The defect: the reviewer's new demand used to read as answered."""
        state = compute_thread_state(
            self._thread(reviewer_edit="2026-01-03T00:00:00Z"), False, "me")
        assert state is ThreadState.AMBIGUOUS

    # passes-at-base: the case the fix must NOT catch — we answered the current text, and reopening it would reopen every thread with a tidied comment
    def test_an_edit_before_our_reply_stays_addressed(self):
        """We answered the text as it now stands, so nothing is owed."""
        state = compute_thread_state(
            self._thread(reviewer_edit="2026-01-01T12:00:00Z"), False, "me")
        assert state is ThreadState.ADDRESSED

    # passes-at-base: the ordinary thread, held unchanged by the fix
    def test_a_thread_nobody_edited_stays_addressed(self):
        assert compute_thread_state(self._thread(), False, "me") is ThreadState.ADDRESSED

    # passes-at-base: our own edit is not a reviewer demand; holds the author filter in _rewritten_since_my_reply
    def test_editing_our_own_reply_does_not_reopen_the_thread(self):
        """Our own later edit is us tidying our answer, not a new demand."""
        state = compute_thread_state(
            self._thread(my_edit="2026-01-05T00:00:00Z"), False, "me")
        assert state is ThreadState.ADDRESSED

    # passes-at-base: RESOLVED is answered before the edit check runs, and must stay that way
    def test_resolution_still_outranks_a_later_edit(self):
        """The button is the reviewer's own word on how the thread ended."""
        state = compute_thread_state(
            self._thread(reviewer_edit="2026-01-09T00:00:00Z"), True, "me")
        assert state is ThreadState.RESOLVED

    # passes-at-base: NEW already says everything is owed; holds the check from masking it
    def test_a_thread_we_never_answered_is_new_not_reopened(self):
        """NEW already says everything is owed; the edit check must not mask it."""
        thread = [{"author": {"login": "alice"}, "body": "nit",
                   "createdAt": "2026-01-01T00:00:00Z",
                   "lastEditedAt": "2026-01-03T00:00:00Z"}]
        assert compute_thread_state(thread, False, "me") is ThreadState.NEW

    # passes-at-base: a payload with no stamps must behave exactly as before the field was fetched
    def test_an_unstamped_thread_is_not_treated_as_rewritten(self):
        """A payload with no edit stamps at all must behave as it always did."""
        thread = [
            {"author": {"login": "alice"}, "body": "nit"},
            {"author": {"login": "me"}, "body": "done"},
        ]
        assert compute_thread_state(thread, False, "me") is ThreadState.ADDRESSED

    # passes-at-base: holds the check under last_comment_is_mine, where a real reviewer reply still classifies normally
    def test_a_reviewer_reply_after_ours_is_classified_not_reopened(self):
        """The edit check sits under `last_comment_is_mine` and must not
        intercept a thread where the reviewer actually spoke last."""
        thread = self._thread(reviewer_edit="2026-01-03T00:00:00Z")
        thread.append({"author": {"login": "alice"}, "body": "thanks, looks good",
                       "createdAt": "2026-01-04T00:00:00Z"})
        assert compute_thread_state(thread, False, "me") is ThreadState.VERIFIED
