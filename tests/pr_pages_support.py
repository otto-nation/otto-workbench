"""Fixtures shared by the PR-data and PR-pagination suites.

`gh.pr_data` hands its first pages to the drains in `gh.pr_pages`, so both
suites build the same thread nodes and connection pages.
"""

from core.proc import CmdResult


# What gh reports when the far end drops the call: nothing on stdout, the
# status line on stderr. Shared so a failure-path test never has to restate it.
_GQL_FAILED = CmdResult(1, stderr="gh: Bad gateway (HTTP 502)")


def _make_thread(
    thread_id="PRT_1", is_resolved=False, path="file.py", line=10,
    comments=None,
):
    if comments is None:
        comments = []
    return {
        "id": thread_id,
        "isResolved": is_resolved,
        "path": path,
        "line": line,
        "comments": {
            "totalCount": len(comments),
            "nodes": comments,
        },
    }


def _review_threads_node(nodes, has_next=False, cursor=None, total=None):
    return {
        "totalCount": len(nodes) if total is None else total,
        "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
        "nodes": nodes,
    }
