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


def _prs_page(*nodes: dict) -> CmdResult:
    return CmdResult(0, json.dumps(
        {"data": {"repository": {"pullRequests": {"nodes": list(nodes)}}}}))


def _pr_node(number: int, merged_at: str) -> dict:
    return {"number": number, "title": f"pr {number}", "mergedAt": merged_at,
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
