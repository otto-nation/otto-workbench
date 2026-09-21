"""Tests for the retro's GitHub fetch.

The fetch had no tests, which is how a query nesting 50 PRs by 100 threads by
50 comments — about 2,600 GraphQL points, half the hourly budget, on every
scan — went unnoticed. These pin the shape that replaced it: ask which PRs are
in the window first, then ask only those for their comments.
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from core.proc import CmdResult  # noqa: E402
from gh.pr_reads import ThreadSet  # noqa: E402
from retro import github  # noqa: E402

# 2026-01-01 and 2026-06-01 as epoch seconds, for windows either side of a PR.
_JAN = 1767225600
_JUN = 1780272000


def _prs_page(*nodes: dict, next_cursor: str | None = None) -> CmdResult:
    """One page of the merged-PR list.

    *next_cursor* names the page after this one; without it the page is the
    last, which is what every single-page test wants.
    """
    return CmdResult(0, json.dumps({"data": {"repository": {"pullRequests": {
        "pageInfo": {
            "hasNextPage": next_cursor is not None,
            "endCursor": next_cursor,
        },
        "nodes": list(nodes),
    }}}}))


def _pr_node(number: int, merged_at: str, updated_at: str | None = None) -> dict:
    """A node of the PR list.

    ``updatedAt`` defaults to the merge date, which is what a PR that merged
    and was left alone actually carries. A test that needs the two to differ —
    the walk stops on update, not merge — passes it explicitly.
    """
    return {"number": number, "title": f"pr {number}", "mergedAt": merged_at,
            "updatedAt": updated_at or merged_at,
            "author": {"login": "alice"}}


def _detail(number: int, merged_at: str, body: str = "drop the retry") -> CmdResult:
    return CmdResult(0, json.dumps({"data": {"repository": {"pullRequest": {
        "number": number, "title": f"pr {number}", "mergedAt": merged_at,
        "author": {"login": "alice"},
        "reviewThreads": {"totalCount": 1, "nodes": [{
            "path": "handler.go", "line": 42,
            "comments": {"nodes": [{"author": {"login": "kgn"}, "body": body}]},
        }]},
        "comments": {"totalCount": 0, "nodes": []},
    }}}}))


def test_a_pr_merged_before_the_window_costs_no_detail_call():
    """The window filter runs before the expensive query, not after it.

    The single batched query this replaced asked for every PR's threads and
    comments up front and discarded the out-of-window ones in Python, so a scan
    paid full price for answers it threw away.
    """
    with patch.object(github.gh_client, "graphql") as gql:
        gql.side_effect = [
            _prs_page(_pr_node(1, "2026-08-01T00:00:00Z"),
                      _pr_node(2, "2026-02-01T00:00:00Z")),
            _detail(1, "2026-08-01T00:00:00Z"),
        ]
        results = github.fetch_repo_review_data("owner/repo", _JUN)

    assert [r["number"] for r in results] == [1]
    assert gql.call_count == 2
    assert gql.call_args.kwargs["variables"]["pr"] == 1


def test_every_in_window_pr_is_asked_about():
    with patch.object(github.gh_client, "graphql") as gql:
        gql.side_effect = [
            _prs_page(_pr_node(1, "2026-08-01T00:00:00Z"),
                      _pr_node(2, "2026-07-01T00:00:00Z")),
            _detail(1, "2026-08-01T00:00:00Z"),
            _detail(2, "2026-07-01T00:00:00Z"),
        ]
        results = github.fetch_repo_review_data("owner/repo", _JUN)

    assert sorted(r["number"] for r in results) == [1, 2]


def test_a_pr_whose_detail_fails_is_skipped_not_fatal(capsys):
    """One unreadable PR costs a rule signal, not the whole scan."""
    with patch.object(github.gh_client, "graphql") as gql:
        gql.side_effect = [
            _prs_page(_pr_node(1, "2026-08-01T00:00:00Z"),
                      _pr_node(2, "2026-08-02T00:00:00Z")),
            CmdResult(1, "", "upstream exploded"),
            _detail(2, "2026-08-02T00:00:00Z"),
        ]
        results = github.fetch_repo_review_data("owner/repo", _JUN)

    assert [r["number"] for r in results] == [2]
    assert "owner/repo#1" in capsys.readouterr().err


def test_a_failed_first_query_falls_back_to_rest():
    """The fallback still guards the phase that decides the window."""
    with patch.object(github.gh_client, "graphql",
                      return_value=CmdResult(1, "", "nope")), \
         patch.object(github, "_fetch_repo_review_data_rest",
                      return_value=[{"number": 9}]) as rest:
        results = github.fetch_repo_review_data("owner/repo", _JUN)

    assert results == [{"number": 9}]
    rest.assert_called_once()


def test_a_pr_with_no_merge_date_is_not_asked_about():
    """`mergedAt` is how the window is decided, so a PR without one cannot be
    placed in it — and must not be paid for on the chance that it belongs."""
    with patch.object(github.gh_client, "graphql") as gql:
        gql.side_effect = [_prs_page({"number": 1, "title": "t",
                                      "mergedAt": None, "author": {"login": "a"}})]
        results = github.fetch_repo_review_data("owner/repo", _JAN)

    assert results == []
    assert gql.call_count == 1


def test_the_detail_query_asks_for_one_pr_not_a_batch():
    """The cost fix in one assertion: the nesting multiplies by one.

    GitHub scores a query from its `first:` values before running it, so a
    query that nests 50 PRs by 100 threads by 50 comments is charged for all
    260,000 nodes whether or not they exist.
    """
    assert "pullRequest(number: $pr)" in github._RETRO_PR_DETAIL_QUERY
    assert "pullRequests(states: MERGED" not in github._RETRO_PR_DETAIL_QUERY
    # The window query carries no nested connections at all.
    assert "reviewThreads" not in github._RETRO_PRS_QUERY
    assert "comments" not in github._RETRO_PRS_QUERY


def test_the_window_is_paged_through_rather_than_truncated():
    """The bug a one-page query hid: in-window PRs past the page size.

    `first: 50` with no pagination silently dropped every in-window PR after
    the fiftieth, and a repo busy enough to merge that many in one window is
    the one whose review comments the retro most wants.
    """
    with patch.object(github.gh_client, "graphql") as gql:
        gql.side_effect = [
            _prs_page(_pr_node(1, "2026-08-01T00:00:00Z"), next_cursor="c1"),
            _prs_page(_pr_node(2, "2026-07-01T00:00:00Z")),
            _detail(1, "2026-08-01T00:00:00Z"),
            _detail(2, "2026-07-01T00:00:00Z"),
        ]
        results = github.fetch_repo_review_data("owner/repo", _JUN)

    assert sorted(r["number"] for r in results) == [1, 2]
    assert gql.call_args_list[1].kwargs["variables"]["cursor"] == "c1"


def test_paging_stops_at_the_first_pr_older_than_the_window():
    """Why ordering by UPDATED_AT is what makes the client-side filter cheap.

    GitHub's `pullRequests` connection takes no date argument, so the window is
    enforced here. Ordered by update descending, the first PR updated before
    the window began proves nothing after it can have merged inside one — so
    the walk stops rather than paging through the repo's whole history.
    """
    with patch.object(github.gh_client, "graphql") as gql:
        gql.side_effect = [
            _prs_page(_pr_node(1, "2026-08-01T00:00:00Z"),
                      _pr_node(2, "2026-02-01T00:00:00Z"),
                      next_cursor="c1"),
            _detail(1, "2026-08-01T00:00:00Z"),
        ]
        results = github.fetch_repo_review_data("owner/repo", _JUN)

    assert [r["number"] for r in results] == [1]
    # Two calls, not three: the second page was never asked for.
    assert gql.call_count == 2


def test_an_old_pr_updated_inside_the_window_does_not_end_the_walk():
    """The case that makes the stop test read `updatedAt` and the keep test
    read `mergedAt`.

    A PR merged long ago and commented on yesterday sorts near the top. It is
    not in the window and must not be fetched, but it is also not proof that
    the PRs after it are out of window — stopping there would drop them.
    """
    with patch.object(github.gh_client, "graphql") as gql:
        gql.side_effect = [
            _prs_page(
                _pr_node(1, "2026-01-15T00:00:00Z",
                         updated_at="2026-08-20T00:00:00Z"),
                _pr_node(2, "2026-08-01T00:00:00Z"),
            ),
            _detail(2, "2026-08-01T00:00:00Z"),
        ]
        results = github.fetch_repo_review_data("owner/repo", _JUN)

    assert [r["number"] for r in results] == [2]


def test_a_page_that_fails_mid_walk_falls_back_rather_than_reporting_short():
    """A partial list reads downstream as a quiet window.

    The REST path can still answer completely, so a failure part-way through
    the walk hands over to it instead of reporting the pages it managed.
    """
    with patch.object(github.gh_client, "graphql") as gql, \
         patch.object(github, "_fetch_repo_review_data_rest",
                      return_value=[{"number": 9}]) as rest:
        gql.side_effect = [
            _prs_page(_pr_node(1, "2026-08-01T00:00:00Z"), next_cursor="c1"),
            CmdResult(1, "", "nope"),
        ]
        results = github.fetch_repo_review_data("owner/repo", _JUN)

    assert results == [{"number": 9}]
    rest.assert_called_once()


def test_the_walk_is_bounded_when_every_pr_is_in_the_window(capsys):
    """The pathological repo: nothing old enough to stop the walk.

    Without a page bound this pages through the entire merge history. The bound
    reports what it skipped rather than going quiet about it.
    """
    with patch.object(github.gh_client, "graphql") as gql, \
         patch.object(github, "_fetch_pr_detail", return_value=None):
        gql.side_effect = [
            _prs_page(_pr_node(n, "2026-08-01T00:00:00Z"), next_cursor=f"c{n}")
            for n in range(github.GQL_MERGED_PRS_MAX_PAGES)
        ]
        github.fetch_repo_review_data("owner/repo", _JUN)

    assert gql.call_count == github.GQL_MERGED_PRS_MAX_PAGES
    assert "may be missing" in capsys.readouterr().err


def _rest_pr(number: int, merged_at: str, updated_at: str | None = None) -> dict:
    return {"number": number, "title": f"pr {number}", "merged_at": merged_at,
            "updated_at": updated_at or merged_at, "user": {"login": "alice"}}


def test_the_rest_fallback_keeps_only_the_window():
    """The filter the fallback does apply. What it cannot do is stop early:
    `_gh_api` passes `--paginate`, so every page is fetched before this sees
    the first row — see the ceiling on `fetch_merged_prs`.
    """
    prs = [
        _rest_pr(1, "2026-08-01T00:00:00Z"),
        _rest_pr(2, "2026-02-01T00:00:00Z"),
    ]
    with patch.object(github, "_gh_api", return_value=prs):
        kept = github.fetch_merged_prs("owner/repo", _JUN)

    assert [p["number"] for p in kept] == [1]


def test_the_rest_fallback_drops_a_closed_pr_that_never_merged():
    """`state=closed` returns both, and only a merge has review history worth
    reading.
    """
    prs = [
        _rest_pr(1, "2026-08-01T00:00:00Z"),
        {"number": 2, "title": "abandoned", "merged_at": None,
         "updated_at": "2026-08-02T00:00:00Z", "user": {"login": "alice"}},
    ]
    with patch.object(github, "_gh_api", return_value=prs):
        kept = github.fetch_merged_prs("owner/repo", _JUN)

    assert [p["number"] for p in kept] == [1]


def test_a_thread_with_more_comments_than_the_page_is_refetched():
    """Lowering the per-thread comment limit is only safe if a deep thread is
    noticed. The thread-count check cannot see this: the thread list is whole
    and it is the comments inside one of them that were cut off."""
    deep = json.loads(_detail(1, "2026-08-01T00:00:00Z").stdout)
    thread = deep["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"][0]
    thread["comments"]["totalCount"] = github.RETRO_THREAD_COMMENTS_LIMIT + 5

    with patch.object(github.gh_client, "graphql") as gql, \
         patch.object(github, "fetch_review_threads") as refetch:
        gql.side_effect = [
            _prs_page(_pr_node(1, "2026-08-01T00:00:00Z")),
            CmdResult(0, json.dumps(deep)),
        ]
        refetch.return_value = ThreadSet([thread])
        github.fetch_repo_review_data("owner/repo", _JUN)

    refetch.assert_called_once()


def test_a_failed_refetch_keeps_the_threads_already_in_hand():
    """The refetch improves on a truncated page; it must never lose to one.

    `fetch_review_threads` answers an empty list for a first page it could not
    read — no auth, no network, a spent budget. Returning that discards review
    comments the detail query already paid for and delivered, and the retro
    then reports a PR with real discussion as having none.
    """
    deep = json.loads(_detail(1, "2026-08-01T00:00:00Z").stdout)
    threads = deep["data"]["repository"]["pullRequest"]["reviewThreads"]
    threads["totalCount"] = 5

    with patch.object(github.gh_client, "graphql") as gql, \
         patch.object(github, "fetch_review_threads") as refetch:
        gql.side_effect = [
            _prs_page(_pr_node(1, "2026-08-01T00:00:00Z")),
            CmdResult(0, json.dumps(deep)),
        ]
        refetch.return_value = ThreadSet([], complete=False)
        results = github.fetch_repo_review_data("owner/repo", _JUN)

    refetch.assert_called_once()
    assert [c["body"] for c in results[0]["comments"]] == ["drop the retry"]


def test_a_refetch_that_found_more_replaces_what_was_in_hand():
    """The ordinary outcome, and the control for the test above.

    Compared on length rather than on `ThreadSet.complete`: the refetch is only
    ever made because the page in hand is known short, so an incomplete walk
    that still found more threads is the better answer.
    """
    deep = json.loads(_detail(1, "2026-08-01T00:00:00Z").stdout)
    threads = deep["data"]["repository"]["pullRequest"]["reviewThreads"]
    threads["totalCount"] = 5
    fuller = [
        {"path": "handler.go", "line": 42, "comments": {"nodes": [
            {"author": {"login": "kgn"}, "body": "the deeper finding"}]}},
        {"path": "other.go", "line": 7, "comments": {"nodes": [
            {"author": {"login": "kgn"}, "body": "a second one"}]}},
    ]

    with patch.object(github.gh_client, "graphql") as gql, \
         patch.object(github, "fetch_review_threads") as refetch:
        gql.side_effect = [
            _prs_page(_pr_node(1, "2026-08-01T00:00:00Z")),
            CmdResult(0, json.dumps(deep)),
        ]
        refetch.return_value = ThreadSet(fuller, complete=False)
        results = github.fetch_repo_review_data("owner/repo", _JUN)

    bodies = [c["body"] for c in results[0]["comments"]]
    assert bodies == ["the deeper finding", "a second one"]


def test_a_thread_within_the_page_is_not_refetched():
    """The control: the common thread must not earn a second round trip."""
    with patch.object(github.gh_client, "graphql") as gql, \
         patch.object(github, "fetch_review_threads") as refetch:
        gql.side_effect = [
            _prs_page(_pr_node(1, "2026-08-01T00:00:00Z")),
            _detail(1, "2026-08-01T00:00:00Z"),
        ]
        github.fetch_repo_review_data("owner/repo", _JUN)

    refetch.assert_not_called()
