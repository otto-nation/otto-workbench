"""Fetching a repo's recent review activity from GitHub.

One GraphQL round trip per repo where the API allows it, falling back to REST
when it does not, flattened into the plain comment dicts the rest of the retro
reads. Deciding which comments matter is `retro_rules`'; rendering them is
`retro_report`'s.

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

import gh_client
import log
from review_github import (
    GQL_THREADS_LIMIT, GQL_THREAD_COMMENTS_LIMIT, GQL_ISSUE_COMMENTS_LIMIT,
    fetch_review_threads,
)
from retro_report import COMMENT_BODY_MAX


# ── Constants ────────────────────────────────────────────────────────────────

NOISE_PATTERNS = re.compile(
    r"^(?:lgtm|looks good|:?\+1:?|approved|nit:?|\U0001f44d)[\s!.]*$", re.IGNORECASE
)

GQL_MERGED_PRS_LIMIT = 50

_RETRO_QUERY = f"""
query($owner: String!, $name: String!) {{
  repository(owner: $owner, name: $name) {{
    pullRequests(states: MERGED, first: {GQL_MERGED_PRS_LIMIT}, orderBy: {{field: UPDATED_AT, direction: DESC}}) {{
      nodes {{
        number
        title
        mergedAt
        author {{ login }}
        reviewThreads(first: {GQL_THREADS_LIMIT}) {{
          totalCount
          nodes {{
            path
            line
            comments(first: {GQL_THREAD_COMMENTS_LIMIT}) {{
              nodes {{
                author {{ login }}
                body
              }}
            }}
          }}
        }}
        comments(first: {GQL_ISSUE_COMMENTS_LIMIT}) {{
          totalCount
          nodes {{
            author {{ login }}
            body
          }}
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
    """Thread nodes for one PR, refetched in full if the batch query truncated.

    The batch query asks for 50 PRs at once, so it cannot paginate each PR's
    threads inline. Oversized PRs are rare, so they earn a second round trip
    rather than costing every PR one.
    """
    threads_data = pr_node.get("reviewThreads", {})
    thread_nodes = threads_data.get("nodes", [])
    if threads_data.get("totalCount", 0) <= len(thread_nodes):
        return thread_nodes
    return fetch_review_threads(repo, pr_node["number"])


def _parse_pr_node(repo: str, pr_node: dict, since_date: str) -> dict | None:
    """Parse a single GraphQL PR node into the retro-scan format."""
    merged_at = pr_node.get("mergedAt", "")
    if not merged_at or merged_at < since_date:
        return None

    thread_nodes = _threads_for(repo, pr_node)

    comments_data = pr_node.get("comments", {})
    total_comments = comments_data.get("totalCount", 0)
    comment_nodes = comments_data.get("nodes", [])
    if total_comments > len(comment_nodes):
        log.warn(f"PR #{pr_node['number']}: {total_comments} issue comments but only {len(comment_nodes)} fetched (limit: GQL_ISSUE_COMMENTS_LIMIT={GQL_ISSUE_COMMENTS_LIMIT})")

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
    """Fetch merged PRs with review data in a single GraphQL query.

    Returns list of dicts with: number, title, author, merged_at, comments.
    Each comment has: author, body, path (optional), line (optional).
    """
    since_date = (
        datetime.fromtimestamp(since_ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if since_ts > 0 else "2020-01-01T00:00:00Z"
    )
    owner, name = repo.split("/", 1)
    r = gh_client.graphql(_RETRO_QUERY, variables={"owner": owner, "name": name})
    if not r.ok:
        log.warn(f"GraphQL failed for {repo}: {r.detail}")
        return _fetch_repo_review_data_rest(repo, since_ts)
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError as e:
        log.warn(f"GraphQL error for {repo}: {e}")
        return _fetch_repo_review_data_rest(repo, since_ts)

    pr_nodes = (
        data.get("data", {})
        .get("repository", {})
        .get("pullRequests", {})
        .get("nodes", [])
    )

    results = []
    for pr_node in pr_nodes:
        parsed = _parse_pr_node(repo, pr_node, since_date)
        if parsed:
            results.append(parsed)
    return results


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
