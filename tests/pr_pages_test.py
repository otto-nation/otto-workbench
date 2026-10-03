"""Tests for gh.pr_pages: page sizes, truncation detection, thread pagination."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from core.proc import CmdResult
import gh.pr_pages
from gh.pr_pages import (
    GQL_MAX_THREAD_PAGES, GQL_THREAD_COMMENTS_LIMIT, GQL_THREAD_REFETCH_LIMIT,
    fetch_review_threads, warn_if_truncated,
)
from pr_pages_support import _GQL_FAILED, _make_thread, _review_threads_node


# ── Page sizes ──────────────────────────────────────────────────────

class TestPageSizes:
    def test_the_refetch_asks_for_a_bigger_page_than_the_nested_read(self):
        """The two limits pull in opposite directions and must not be unified.

        The nested one is multiplied by the thread page size before GitHub
        scores the query, so it is small. The refetch is scored on its own, for
        a thread already known to be deep, so it is the largest page allowed —
        shrinking it to match would turn one extra call into ten.
        """
        assert GQL_THREAD_REFETCH_LIMIT > GQL_THREAD_COMMENTS_LIMIT
        assert f"comments(first: {GQL_THREAD_REFETCH_LIMIT}, after: $endCursor)" \
            in gh.pr_pages._THREAD_COMMENTS_QUERY

    def test_the_nested_read_is_the_small_page(self):
        """The control: the connection inside `reviewThreads` stays cheap."""
        assert f"comments(first: {GQL_THREAD_COMMENTS_LIMIT})" \
            in gh.pr_pages._THREAD_NODE_FIELDS


# ── Truncation detection ────────────────────────────────────────────

class TestWarnIfTruncated:
    def test_reports_a_short_page(self, capsys):
        assert warn_if_truncated({"totalCount": 5, "nodes": [1, 2]}, "x reviews") is True
        assert "x reviews" in capsys.readouterr().err

    def test_silent_when_whole(self, capsys):
        assert warn_if_truncated({"totalCount": 2, "nodes": [1, 2]}, "x") is False
        assert capsys.readouterr().err == ""

    def test_silent_without_a_total_count(self, capsys):
        """A connection that did not ask for it gets today's silence, not noise."""
        assert warn_if_truncated({"nodes": [1, 2]}, "x") is False
        assert capsys.readouterr().err == ""


# ── Review thread pagination ─────────────────────────────────────────────────

def _threads_response(nodes, has_next=False, cursor=None):
    return json.dumps({
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": _review_threads_node(nodes, has_next, cursor),
                },
            },
        },
    })


class TestFetchReviewThreads:
    @patch("gh.client.graphql")
    def test_single_page_makes_one_call(self, mock_gql):
        mock_gql.return_value = CmdResult(0, _threads_response([_make_thread("PRT_1")]))
        found = fetch_review_threads("owner/repo", 7)
        assert [t["id"] for t in found.threads] == ["PRT_1"]
        assert found.complete
        mock_gql.assert_called_once()
        # None rather than absent: `graphql()` itself omits a None variable
        # from the wire request, so the caller no longer needs its own guard.
        assert mock_gql.call_args.kwargs["variables"]["endCursor"] is None

    @patch("gh.client.graphql")
    def test_follows_pages_until_exhausted(self, mock_gql):
        mock_gql.side_effect = [
            CmdResult(0, _threads_response([_make_thread("PRT_1")], has_next=True, cursor="c1")),
            CmdResult(0, _threads_response([_make_thread("PRT_2")], has_next=True, cursor="c2")),
            CmdResult(0, _threads_response([_make_thread("PRT_3")])),
        ]
        found = fetch_review_threads("owner/repo", 7)
        assert [t["id"] for t in found.threads] == ["PRT_1", "PRT_2", "PRT_3"]
        # The positive control: a walk that reached the end says so, which is
        # what stops `complete` from being hardwired False.
        assert found.complete
        cursors = [c.kwargs["variables"].get("endCursor") for c in mock_gql.call_args_list]
        assert cursors == [None, "c1", "c2"]

    @patch("gh.client.graphql")
    def test_repeated_cursor_stops_instead_of_looping(self, mock_gql, capsys):
        # A cursor variable gh does not recognise re-serves page 1 forever.
        mock_gql.return_value = CmdResult(
            0, _threads_response([_make_thread("PRT_1")], has_next=True, cursor="stuck"),
        )
        found = fetch_review_threads("owner/repo", 7)
        assert mock_gql.call_count == 2
        assert len(found.threads) == 2
        assert found.complete is False
        assert "repeated cursor" in capsys.readouterr().err

    @patch("gh.client.graphql")
    def test_another_page_without_a_cursor_warns(self, mock_gql, capsys):
        mock_gql.return_value = CmdResult(
            0, _threads_response([_make_thread("PRT_1")], has_next=True, cursor=None),
        )
        found = fetch_review_threads("owner/repo", 7)
        assert [t["id"] for t in found.threads] == ["PRT_1"]
        assert found.complete is False
        mock_gql.assert_called_once()
        assert "no cursor" in capsys.readouterr().err

    @patch("gh.client.graphql")
    def test_page_ceiling_stops_and_warns(self, mock_gql, capsys):
        mock_gql.side_effect = [
            CmdResult(0, _threads_response([_make_thread(f"PRT_{i}")], has_next=True, cursor=f"c{i}"))
            for i in range(GQL_MAX_THREAD_PAGES)
        ]
        found = fetch_review_threads("owner/repo", 7)
        assert mock_gql.call_count == GQL_MAX_THREAD_PAGES
        assert len(found.threads) == GQL_MAX_THREAD_PAGES
        assert found.complete is False
        assert f"{GQL_MAX_THREAD_PAGES}-page ceiling" in capsys.readouterr().err

    @patch("gh.client.graphql")
    def test_a_failed_page_reports_the_set_as_incomplete(self, mock_gql, capsys):
        """The threads gathered so far are kept, but not as the whole set.

        This used to return a bare list, so a caller writing a thread ledger
        could not tell it from a PR that really has one thread.
        """
        mock_gql.side_effect = [
            CmdResult(0, _threads_response([_make_thread("PRT_1")], has_next=True, cursor="c1")),
            _GQL_FAILED,
        ]
        found = fetch_review_threads("owner/repo", 7)
        assert [t["id"] for t in found.threads] == ["PRT_1"]
        assert found.complete is False
        assert "incomplete" in capsys.readouterr().err

    @patch("gh.client.graphql")
    def test_first_page_failure_is_not_a_pr_without_threads(self, mock_gql):
        """A total failure and an empty PR were the same value: `[]`."""
        mock_gql.return_value = _GQL_FAILED
        found = fetch_review_threads("owner/repo", 7)
        assert found.threads == []
        assert found.complete is False
