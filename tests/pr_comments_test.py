"""Tests for pr_comments library."""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pr_comments
import pr_domains
import pr_state
import publishing
import review_issue
from proc import CmdResult
from pr_comments import (
    compute_thread_state, sync_threads, fetch_threads, render_dashboard,
)
from pr_comments_state import ThreadRecord, ThreadState


REPO = "owner/repo"


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
    result = sync_threads(threads, prior_threads, "isaacg")
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
    result = sync_threads(threads, prior_threads, "isaacg")
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
    result = sync_threads(threads, prior_threads, "isaacg")
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
    result = sync_threads(threads, prior_threads, "isaacg")
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
    result = sync_threads(threads, prior_threads, "isaacg")
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
    monkeypatch.setattr("proc.subprocess.run", boom)
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
            "proc.subprocess.run",
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


# ── _relative_time ──────────────────────────────────────────────────────────


class TestRelativeTime:
    """The dashboard's age column, at each unit boundary it crosses."""

    @staticmethod
    def _ago(**kwargs) -> str:
        stamp = datetime.now(timezone.utc) - timedelta(**kwargs)
        return pr_comments._relative_time(stamp.isoformat())

    @pytest.mark.parametrize("kwargs,expected", [
        ({"minutes": 5}, "5 minutes ago"),
        ({"minutes": 59}, "59 minutes ago"),
        ({"hours": 1}, "1 hours ago"),
        ({"hours": 23}, "23 hours ago"),
        ({"hours": 24}, "1 day ago"),
        ({"days": 3}, "3 days ago"),
    ])
    def test_each_unit_boundary(self, kwargs, expected):
        assert self._ago(**kwargs) == expected

    def test_an_unparseable_stamp_reads_as_no_age(self):
        assert pr_comments._relative_time("not a timestamp") == ""
