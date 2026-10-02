"""Tests for pr.comments library: fetching threads and comments, thread state and sync."""

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

import pr.comments
from gh.pr_reads import ThreadSet
from pr.comments import (
    compute_thread_state, sync_threads, fetch_threads, render_dashboard,
)
from pr.comments_state import ThreadRecord, ThreadState
import gh.client


REPO = "owner/repo"


# ── fetch_threads ───────────────────────────────────────────────────────────

def test_fetch_threads_uses_the_paginated_fetcher():
    with patch.object(pr.comments, "fetch_review_threads",
                      return_value=ThreadSet([{"id": "PRRT_1"}], complete=True)) as fetcher:
        assert fetch_threads("owner", "repo", 42) == ThreadSet([{"id": "PRRT_1"}], complete=True)
    fetcher.assert_called_once_with("owner/repo", 42)


def test_fetch_threads_prefers_prefetched_pr_data():
    pr_data = SimpleNamespace(review_threads=[{"id": "PRRT_cached"}], threads_complete=True)
    with patch.object(pr.comments, "fetch_review_threads") as fetcher:
        fetched = fetch_threads("owner", "repo", 42, pr_data)
    assert fetched.threads == [{"id": "PRRT_cached"}]
    assert fetched.complete
    fetcher.assert_not_called()


def test_fetch_threads_carries_a_short_prefetch_through():
    """The consolidated read knows the thread walk fell short; dropping that
    here is how the ledger came to be written from a partial set."""
    pr_data = SimpleNamespace(review_threads=[{"id": "PRRT_1"}], threads_complete=False)
    with patch.object(pr.comments, "fetch_review_threads") as fetcher:
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
    return patch.object(gh.client, "api_json", return_value=_ISSUE_COMMENTS)


def test_fetch_issue_comments_drops_our_own_by_default():
    with _rest_listing():
        got = pr.comments.fetch_issue_comments(REPO, 42, "me")
    assert [c["user"] for c in got] == ["kgn"]


def test_fetch_issue_comments_keeps_our_own_when_asked():
    """`include_self` is the contract `review-threads` reads its reply through."""
    with _rest_listing():
        got = pr.comments.fetch_issue_comments(
            REPO, 42, "me", include_self=True)
    assert [c["user"] for c in got] == ["me", "kgn"]


def test_fetch_issue_comments_drops_bots_either_way():
    with _rest_listing():
        got = pr.comments.fetch_issue_comments(
            REPO, 42, "me", include_self=True)
    assert "bot" not in [c["user"] for c in got]


@pytest.mark.parametrize("include_self,expected", [(False, "me"), (True, "")])
def test_fetch_issue_comments_passes_the_exclusion_on_to_pr_data(
        include_self, expected):
    """Prefetched data takes the same filter, so both paths answer alike."""
    pr_data = SimpleNamespace(non_self_issue_comments=lambda login: [{"user": login}])
    got = pr.comments.fetch_issue_comments(
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
    assert pr.comments.last_comment_is_mine(comments, "isaacg")


def test_last_comment_is_mine_is_false_when_a_reviewer_answered():
    comments = _make_comments(
        ("alice", "Use RunTx here"),
        ("isaacg", "Fixed."),
        ("alice", "Not quite"),
    )
    assert not pr.comments.last_comment_is_mine(comments, "isaacg")


@pytest.mark.parametrize("comments,login", [
    ([], "isaacg"),
    (_make_comments(("isaacg", "mine")), ""),
])
def test_last_comment_is_mine_needs_both_halves(comments, login):
    assert not pr.comments.last_comment_is_mine(comments, login)


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
        assert pr.comments._rest_edit_stamp({
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }) == ""

    def test_an_edited_comment_carries_the_edit_time(self):
        assert pr.comments._rest_edit_stamp({
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-02-01T00:00:00Z",
        }) == "2026-02-01T00:00:00Z"

    def test_a_payload_missing_both_fields_has_no_stamp(self):
        assert pr.comments._rest_edit_stamp({}) == ""

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
        assert pr.comments._rest_edit_stamp(rest) == expected


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

    def test_answering_by_editing_our_own_reply_closes_the_thread(self):
        """The convergence case, and it is not hypothetical.

        `thread_replies.upsert_thread_reply` answers a thread whose last
        comment is ours by PATCHing that comment, which moves `lastEditedAt`
        and leaves `createdAt` alone. Reading only our `createdAt` freezes our
        side at the original reply, so the reviewer's edit stays permanently
        "after" it and the thread re-enters triage every round forever.
        """
        thread = self._thread(reviewer_edit="2026-01-03T00:00:00Z")
        assert compute_thread_state(thread, False, "me") is ThreadState.AMBIGUOUS
        answered = self._thread(reviewer_edit="2026-01-03T00:00:00Z",
                                my_edit="2026-01-04T00:00:00Z")
        assert compute_thread_state(answered, False, "me") is ThreadState.ADDRESSED

    @pytest.mark.parametrize("ours,theirs,rewritten", [
        # The same instant in two spellings. '+' sorts below 'Z', so a string
        # compare calls this a rewrite and reopens a thread nobody touched.
        ("2026-01-02T00:00:00+00:00", "2026-01-02T00:00:00Z", False),
        # Half a second later, with a fraction. '.' sorts below 'Z', so a
        # string compare misses it — and a missed rewrite loses the demand.
        ("2026-01-02T00:00:00Z", "2026-01-02T00:00:00.500Z", True),
        # A fractional stamp that is genuinely earlier.
        ("2026-01-02T00:00:00.000Z", "2026-01-02T00:00:00Z", False),
        # Plain, unambiguous ordering.
        ("2026-01-02T00:00:00Z", "2026-01-03T00:00:00Z", True),
    ])
    def test_stamps_are_compared_as_instants_not_as_strings(
        self, ours, theirs, rewritten,
    ):
        thread = [
            {"author": {"login": "alice"}, "createdAt": "2026-01-01T00:00:00Z",
             "lastEditedAt": theirs},
            {"author": {"login": "me"}, "createdAt": ours},
        ]
        assert pr.comments._rewritten_since_my_reply(thread, "me") is rewritten

    def test_an_unreadable_stamp_is_not_a_rewrite(self):
        thread = [
            {"author": {"login": "alice"}, "createdAt": "2026-01-01T00:00:00Z",
             "lastEditedAt": "not a timestamp"},
            {"author": {"login": "me"}, "createdAt": "2026-01-02T00:00:00Z"},
        ]
        assert pr.comments._rewritten_since_my_reply(thread, "me") is False
