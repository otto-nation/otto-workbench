"""Fetching a repo's recent review activity from GitHub.

Two GraphQL round trips per repo — one for the PRs in the window, one per PR
for its comments — falling back to REST when the API will not answer, flattened
into the plain comment dicts the rest of the retro reads. Deciding which
comments matter is `retro.rules`'; rendering them is `retro.report`'s.

This used to be one round trip per repo, on the reading that fewer calls is
cheaper. For GraphQL that is backwards: the hourly budget is spent in points
scored from the `first:` values *before the query runs*, so nesting fifty PRs
by a hundred threads by fifty comments was charged for 260,000 nodes — about
2,600 points, half the hourly budget — on every scan, whatever the repo
actually held. Worse, the window filter was applied in Python afterwards, so a
scan paid full price for every PR it then discarded.

Asking twice costs about 120 points for the same answer. The first query takes
numbers and merge dates only, which is what the window filter needs; the second
runs only for the PRs that survive it. A round trip is not the unit of cost
here — the requested node count is — and a reader tempted to batch this back
into one query should start from that.

Normalising a raw comment's shape and dropping the noise (approvals,
thumbs-up, a bare "nit") is part of producing that plain comment dict, so it
lives here too — every fetch path in this module needs it applied the same
way before a comment is fit to compare against a rule.
"""

# doc-group: platform

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from gh import client as gh_client
from core import log
from gh.pr_reads import fetch_review_threads
from retro.report import COMMENT_BODY_MAX


# ── Constants ────────────────────────────────────────────────────────────────

NOISE_PATTERNS = re.compile(
    r"^(?:lgtm|looks good|:?\+1:?|approved|nit:?|\U0001f44d)[\s!.]*$", re.IGNORECASE
)

# One page of the PR list. Not the window's size: the window is a date range
# and this is how many nodes one round trip asks for, so a window holding more
# than this is paged through rather than truncated to it.
GQL_MERGED_PRS_LIMIT = 50

# How many pages of the list `fetch_repo_review_data` will walk before it gives
# up and reports on what it has. The walk normally stops on its own at the
# first PR older than the window — see `_in_window_page` — so this bounds only
# the pathological case: a repo whose merged PRs are all inside the window,
# where the walk would otherwise page through its entire history. Five pages is
# 250 PRs in one window, well past any window this scans.
GQL_MERGED_PRS_MAX_PAGES = 5

# This module's own page sizes, rather than `gh.pr_reads`'s. The two queries
# have nothing in common but their shape: that one reads a single PR and
# recovers from truncation by paginating, while this one nests the same fields
# fifty PRs deep and recovers through `_threads_for`'s refetch. Sharing the
# constants meant tuning the single-PR query silently rewrote this one, fifty
# times over.
RETRO_THREADS_LIMIT = 100

# Same reasoning as `gh.pr_reads`' equivalent, and the same measurement behind
# it: p90 is two comments per thread and 3 threads in 217 exceed ten. Nested
# under `reviewThreads`, this is multiplied by 100 and is most of what the
# detail query costs. `_threads_for` refetches any PR whose threads were cut
# off, so a low value here is short reports' problem only if that refetch also
# fails — and it warns when it does.
RETRO_THREAD_COMMENTS_LIMIT = 10

RETRO_ISSUE_COMMENTS_LIMIT = 100

# Phase one: which PRs are in the window. Fifty nodes and nothing nested, so
# one page costs a single point however many comments the repo's PRs turn out
# to carry. The window filter runs against this, and only the survivors are
# paid for below.
#
# The filter is client-side because GitHub cannot do it here: `pullRequests`
# takes `states`, `labels`, `headRefName`, `baseRefName`, `orderBy` and the
# pagination arguments, and nothing that bounds a date. (`filterBy: {{since}}`
# exists on `issues`, but GraphQL's `issues` connection returns `Issue` nodes
# only — a PR is a distinct type there and never appears in it.) The `search`
# root takes `merged:>=DATE` and would filter server-side, at 1 point a page,
# but its index is only near-realtime and it caps at 1000 results; a retro that
# silently missed a PR merged minutes ago would be reporting on a window it did
# not actually cover.
#
# So the ordering does the work instead: UPDATED_AT descending, and a PR merged
# after the window opened must have been updated at or after it merged. The
# walk therefore stops at the first node older than the window, and pages until
# then. `updatedAt` is selected for exactly that test — `mergedAt` cannot make
# it, since a PR merged inside the window can carry an older merge date than a
# stale PR's update.
_RETRO_PRS_QUERY = f"""
query($owner: String!, $name: String!, $cursor: String) {{
  repository(owner: $owner, name: $name) {{
    pullRequests(states: MERGED, first: {GQL_MERGED_PRS_LIMIT}, after: $cursor, orderBy: {{field: UPDATED_AT, direction: DESC}}) {{
      pageInfo {{
        hasNextPage
        endCursor
      }}
      nodes {{
        number
        title
        mergedAt
        updatedAt
        author {{ login }}
      }}
    }}
  }}
}}
"""

# Phase two: one in-window PR's review activity. The same fields the batched
# query asked for, but scoped to a single PR, so the nesting multiplies by one
# instead of by fifty.
_RETRO_PR_DETAIL_QUERY = f"""
query($owner: String!, $name: String!, $pr: Int!) {{
  repository(owner: $owner, name: $name) {{
    pullRequest(number: $pr) {{
      number
      title
      mergedAt
      author {{ login }}
      reviewThreads(first: {RETRO_THREADS_LIMIT}) {{
        totalCount
        nodes {{
          path
          line
          comments(first: {RETRO_THREAD_COMMENTS_LIMIT}) {{
            totalCount
            nodes {{
              author {{ login }}
              body
            }}
          }}
        }}
      }}
      comments(first: {RETRO_ISSUE_COMMENTS_LIMIT}) {{
        totalCount
        nodes {{
          author {{ login }}
          body
        }}
      }}
    }}
  }}
}}
"""


# ── Comment filtering ────────────────────────────────────────────────────────

def is_noise(body: str) -> bool:
    stripped = body.strip()
    if len(stripped) < 3:
        return True
    return bool(NOISE_PATTERNS.match(stripped))


def parse_review_comment(comment: dict) -> dict:
    body = comment.get("body", "")
    return {
        "author": comment.get("user", {}).get("login", "unknown"),
        "body": body[:COMMENT_BODY_MAX],
        "path": comment.get("path"),
        "line": comment.get("line"),
        "url": comment.get("html_url", ""),
    }


# ── GitHub API fetching ──────────────────────────────────────────────────────

def _gh_api(endpoint: str, repo: str) -> list[dict]:
    """Every page of a REST endpoint, or an empty list if it could not be read.

    The scan reports what it could not read rather than returning a short list
    quietly — an empty answer here is indistinguishable from a repo with no
    review comments, which is the shape a rate-limited scan would take.
    """
    r = gh_client.api(f"repos/{repo}/{endpoint}", paginate=True)
    if not r.ok:
        log.warn(f"gh api failed for {repo}/{endpoint}: {r.detail}")
        return []
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError as e:
        log.warn(f"gh api error for {repo}/{endpoint}: {e}")
        return []


def _graphql_comment_to_dict(node: dict, path: str | None, line: int | None) -> dict:
    """Convert a single GraphQL comment node to the retro-scan comment format."""
    return {
        "author": (node.get("author") or {}).get("login", "unknown"),
        "body": (node.get("body") or "")[:COMMENT_BODY_MAX],
        "path": path, "line": line, "url": "",
    }


def _flatten_thread_comments(thread_nodes: list[dict]) -> list[dict]:
    """Flatten review thread nodes into comment dicts."""
    raw = [
        _graphql_comment_to_dict(c, thread.get("path"), thread.get("line"))
        for thread in thread_nodes
        for c in thread.get("comments", {}).get("nodes", [])
    ]
    return [c for c in raw if not is_noise(c["body"])]


def _flatten_issue_comments(comment_nodes: list[dict]) -> list[dict]:
    """Flatten issue comment nodes into comment dicts."""
    raw = [_graphql_comment_to_dict(c, None, None) for c in comment_nodes]
    return [c for c in raw if not is_noise(c["body"])]


def _threads_for(repo: str, pr_node: dict) -> list[dict]:
    """Thread nodes for one PR, refetched in full if the detail query truncated.

    The detail query asks for one PR but caps its thread list and the comments
    nested in each thread, so a PR past either cap comes back short. Oversized
    PRs are rare, so they earn a second round trip rather than costing every PR
    one.

    A short refetch is still better than the truncated page it replaces, and
    this feeds a report rather than a ledger: a missed thread costs a rule
    signal, so an incomplete walk is taken as-is rather than failing the scan.
    An *empty* one is different, and is the case `_richer_of` exists for.
    """
    threads_data = pr_node.get("reviewThreads", {})
    thread_nodes = threads_data.get("nodes", [])
    if threads_data.get("totalCount", 0) > len(thread_nodes):
        return _richer_of(thread_nodes, repo, pr_node["number"])
    # Thread *count* is not the only way this comes back short. The page size
    # for comments within a thread is set for the common thread, so a deep one
    # is cut off with the thread list itself intact — which the check above
    # cannot see. Refetching the PR is what pages those comments properly.
    if any(_comments_truncated(t) for t in thread_nodes):
        return _richer_of(thread_nodes, repo, pr_node["number"])
    return thread_nodes


def _richer_of(have: list[dict], repo: str, number: int) -> list[dict]:
    """The refetched threads, or the ones already in hand when it came back worse.

    The refetch exists to improve on a truncated page, so it must never leave
    the caller with less than it started with. `fetch_review_threads` answers
    an empty list for a first page it could not read at all — no auth, no
    network, a spent budget — and returning that would discard real review
    comments the detail query had already paid for and delivered, turning a
    failed second call into a PR the retro reports as having no discussion.

    Compared on length rather than on ``ThreadSet.complete``: an incomplete
    walk that still found more threads than the truncated page is the better
    answer, and that is the ordinary outcome here — the refetch is only ever
    made because the page in hand is known to be short.
    """
    refetched = fetch_review_threads(repo, number).threads
    if len(refetched) < len(have):
        log.warn(
            f"{repo}#{number}: refetching threads returned {len(refetched)} where "
            f"the first read had {len(have)} — keeping the first read")
        return have
    return refetched


def _comments_truncated(thread: dict) -> bool:
    """Whether this thread carries more comments than the page returned."""
    comments = thread.get("comments", {})
    return comments.get("totalCount", 0) > len(comments.get("nodes", []))


def _parse_pr_node(repo: str, pr_node: dict, since_date: str) -> dict | None:
    """Parse a single GraphQL PR node into the retro-scan format.

    The window is re-checked against *since_date* even though the caller has
    already applied it. Not redundant: the caller filtered the *list* query's
    nodes, and this reads the *detail* query's — a second response, fetched
    separately, whose ``mergedAt`` is its own field and can come back absent.
    A comparison against the empty string would place such a PR before every
    window, so the missing case is rejected outright rather than compared.
    """
    merged_at = pr_node.get("mergedAt", "")
    if not merged_at or merged_at < since_date:
        return None

    thread_nodes = _threads_for(repo, pr_node)

    comments_data = pr_node.get("comments", {})
    total_comments = comments_data.get("totalCount", 0)
    comment_nodes = comments_data.get("nodes", [])
    if total_comments > len(comment_nodes):
        log.warn(f"PR #{pr_node['number']}: {total_comments} issue comments but only {len(comment_nodes)} fetched (limit: RETRO_ISSUE_COMMENTS_LIMIT={RETRO_ISSUE_COMMENTS_LIMIT})")

    all_comments = _flatten_thread_comments(thread_nodes)
    all_comments.extend(_flatten_issue_comments(comment_nodes))

    if not all_comments:
        return None
    return {
        "number": pr_node.get("number"),
        "title": pr_node.get("title", ""),
        "user": {"login": (pr_node.get("author") or {}).get("login", "")},
        "merged_at": merged_at,
        "comments": all_comments,
    }


def fetch_repo_review_data(repo: str, since_ts: int) -> list[dict]:
    """Merged PRs in the window, with the review comments left on them.

    One cheap query for the PR list, then one detail query per PR that survives
    the window filter — not the single nested query this used to be, which was
    scored for every PR it went on to discard. The module docstring has why.

    Returns list of dicts with: number, title, author, merged_at, comments.
    Each comment has: author, body, path (optional), line (optional).
    """
    since_date = (
        datetime.fromtimestamp(since_ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if since_ts > 0 else "2020-01-01T00:00:00Z"
    )
    owner, name = repo.split("/", 1)
    in_window = _in_window_prs(repo, owner, name, since_date)
    if in_window is None:
        return _fetch_repo_review_data_rest(repo, since_ts)

    results = []
    for pr_node in in_window:
        detail = _fetch_pr_detail(repo, owner, name, pr_node["number"])
        if detail is None:
            continue
        parsed = _parse_pr_node(repo, detail, since_date)
        if parsed:
            results.append(parsed)
    return results


def _in_window_prs(
    repo: str, owner: str, name: str, since_date: str,
) -> list[dict] | None:
    """Every merged PR in the window, paged, or None when GraphQL cannot answer.

    None rather than an empty list, because the caller falls back to REST on it
    and an empty window is a legitimate answer that must not trigger that.

    Pages until a node older than the window proves the rest are too, since the
    list is ordered by UPDATED_AT descending. Asking for one page and stopping
    — which this did — silently dropped every in-window PR past the fiftieth,
    and a repo busy enough to merge fifty PRs in a window is exactly the one
    whose review comments the retro most wants to read.

    A page that fails mid-walk falls back whole rather than returning what it
    has: a partial list is indistinguishable from a quiet window to every
    caller downstream, and the REST path can still answer completely.
    """
    in_window: list[dict] = []
    cursor = None
    for _ in range(GQL_MERGED_PRS_MAX_PAGES):
        page = _prs_page(repo, owner, name, cursor)
        if page is None:
            return None
        kept, done = _in_window_page(page.get("nodes") or [], since_date)
        in_window.extend(kept)
        info = page.get("pageInfo") or {}
        if done or not info.get("hasNextPage"):
            return in_window
        cursor = info.get("endCursor")
        if not cursor:
            return in_window
    log.warn(
        f"{repo}: stopped after {GQL_MERGED_PRS_MAX_PAGES} pages of merged PRs — "
        "the report may be missing the oldest of them",
    )
    return in_window


def _in_window_page(nodes: list[dict], since_date: str) -> tuple[list[dict], bool]:
    """One page's in-window PRs, and whether the walk can stop here.

    Filtered before the detail query rather than after it: the window is why
    most of these PRs are not wanted, and asking about them anyway was most of
    what the old single query spent.

    The stop test reads ``updatedAt`` while the keep test reads ``mergedAt``,
    and they are different questions. Ordering is by update, so the first node
    updated before the window began is the point past which nothing can have
    been merged inside it — but a node can be updated inside the window and
    have merged long before it, which is kept out by the merge date without
    ending the walk. A node missing ``updatedAt`` cannot prove the walk is
    done, so it does not end it.
    """
    kept = []
    for node in nodes:
        updated = node.get("updatedAt")
        if updated and updated < since_date:
            return kept, True
        merged = node.get("mergedAt")
        if merged and merged >= since_date:
            kept.append(node)
    return kept, False


def _prs_page(
    repo: str, owner: str, name: str, cursor: str | None,
) -> dict | None:
    """One page of the merged-PR list, or None when GraphQL cannot answer."""
    r = gh_client.graphql(
        _RETRO_PRS_QUERY,
        variables={"owner": owner, "name": name, "cursor": cursor},
    )
    if not r.ok:
        log.warn(f"GraphQL failed for {repo}: {r.detail}")
        return None
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError as e:
        log.warn(f"GraphQL error for {repo}: {e}")
        return None
    repository = data.get("data", {}).get("repository") or {}
    return repository.get("pullRequests") or {}


def _fetch_pr_detail(repo: str, owner: str, name: str, number: int) -> dict | None:
    """One in-window PR's review activity, or None when it cannot be read.

    A PR that fails is skipped rather than failing the scan: the retro reads
    what it can and reports on that, and one unreadable PR costs a rule signal
    rather than the whole run. Said out loud so a short report has a reason.
    """
    r = gh_client.graphql(
        _RETRO_PR_DETAIL_QUERY,
        variables={"owner": owner, "name": name, "pr": number},
    )
    if not r.ok:
        log.warn(f"GraphQL failed for {repo}#{number}: {r.detail}")
        return None
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError as e:
        log.warn(f"GraphQL error for {repo}#{number}: {e}")
        return None
    return (data.get("data", {}).get("repository", {}) or {}).get("pullRequest")


def _fetch_repo_review_data_rest(repo: str, since_ts: int) -> list[dict]:
    """REST fallback for fetch_repo_review_data."""
    merged_prs = fetch_merged_prs(repo, since_ts)
    results = []
    for pr in merged_prs:
        comments = fetch_pr_comments(repo, pr["number"])
        if comments:
            results.append({
                "number": pr["number"],
                "title": pr.get("title", ""),
                "user": pr.get("user", {}),
                "merged_at": pr.get("merged_at", ""),
                "comments": comments,
            })
    return results


def fetch_merged_prs(repo: str, since_ts: int) -> list[dict]:
    """Merged PRs in the window, from REST.

    The window is applied here rather than in the request because the REST
    endpoint takes no date bound either — `state`, `sort` and `direction` are
    what it offers.
    """
    # ceiling: reads every closed PR the repo has, to keep the few in window.
    # `_gh_api` passes `--paginate`, which walks the whole result set before it
    # returns, so no client-side early exit can save a request — they are all
    # already made by the time the first row is seen. Bounding it means paging
    # by hand against `&page=N` with the window as the stop condition, which is
    # a change to the shared helper rather than to this caller.
    # Upgrade trigger: if the GraphQL path is removed, or once a scanned repo
    # has more than a few hundred closed PRs. Free today because GraphQL
    # answers first and this runs only when that fails.
    since_date = datetime.fromtimestamp(since_ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if since_ts > 0 else "2020-01-01T00:00:00Z"
    endpoint = "pulls?state=closed&sort=updated&direction=desc&per_page=100"
    prs = _gh_api(endpoint, repo)
    return [
        pr for pr in prs
        if pr.get("merged_at") and pr["merged_at"] >= since_date
    ]


def fetch_pr_comments(repo: str, pr_number: int) -> list[dict]:
    review_comments = _gh_api(f"pulls/{pr_number}/comments", repo)
    issue_comments = _gh_api(f"issues/{pr_number}/comments", repo)
    all_comments = []
    for c in review_comments:
        parsed = parse_review_comment(c)
        if not is_noise(parsed["body"]):
            all_comments.append(parsed)
    for c in issue_comments:
        parsed = parse_review_comment(c)
        if not is_noise(parsed["body"]):
            all_comments.append(parsed)
    return all_comments
