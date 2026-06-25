"""GitHub API primitives, retry logic, and PR metadata fetching.

Low-level wrappers around ``gh api`` with rate-limit handling and
exponential backoff.  Used by review_posting and review_dedup.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import dataclass, field

import log


# ── Constants ───────────────────────────────────────────────────────────────

MAX_RETRIES = 5
RATE_LIMIT_WAIT = 60
RATE_LIMIT_BACKOFF = 1.5
RATE_LIMIT_MAX_WAIT = 300
NON_RATE_LIMIT_DELAY = 5
GH_API_TIMEOUT = 30

REVIEW_STATE_PENDING = "PENDING"

# GraphQL pagination limits — shared across queries.
# Upgrade to a query builder class when: a third query shape is added,
# fields become runtime-conditional, or cursor-based pagination is needed.
GQL_REVIEWS_LIMIT = 100
GQL_THREADS_LIMIT = 100
GQL_THREAD_COMMENTS_LIMIT = 50
GQL_ISSUE_COMMENTS_LIMIT = 100
GQL_COMMITS_LIMIT = 100


# ── Exceptions ──────────────────────────────────────────────────────────────


class LineResolutionError(Exception):
    """GitHub cannot resolve line positions for inline comments."""


# ── Core API ────────────────────────────────────────────────────────────────

def _gh_api(
    endpoint: str, method: str = "GET", input_file: str | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, str]:
    """Call gh api and return (exit_code, stdout)."""
    cmd = ["gh", "api", endpoint]
    if method != "GET":
        cmd.extend(["--method", method])
    if input_file:
        cmd.extend(["--input", input_file])
    for key, val in (headers or {}).items():
        cmd.extend(["--header", f"{key}: {val}"])

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=GH_API_TIMEOUT)
    return result.returncode, result.stdout


def _gh_graphql(query: str, variables: dict) -> tuple[int, str]:
    """Call gh api graphql and return (exit_code, stdout).

    The query string is passed as a raw field (-f); all variables are passed
    as typed fields (-F) so gh auto-detects integers and booleans.
    """
    cmd = ["gh", "api", "graphql", "-f", f"query={query}"]
    for key, val in variables.items():
        cmd.extend(["-F", f"{key}={val}"])
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=GH_API_TIMEOUT)
    return result.returncode, result.stdout


def _fetch_json_list(endpoint: str) -> list:
    code, out = _gh_api(endpoint)
    if code != 0:
        return []
    try:
        return json.loads(out)
    except (json.JSONDecodeError, TypeError):
        return []


# ── PR metadata ─────────────────────────────────────────────────────────────

def _fetch_pr_metadata(repo: str, pr: str, pr_data: PRData | None = None) -> dict:
    """Fetch PR metadata (head SHA, head ref, base ref) in one call."""
    if pr_data is not None:
        return {"head_sha": pr_data.head_sha, "head_ref": pr_data.head_ref, "base_ref": pr_data.base_ref}
    code, out = _gh_api(f"repos/{repo}/pulls/{pr}")
    if code != 0:
        log.error("Failed to fetch PR metadata")
        sys.exit(1)
    try:
        data = json.loads(out)
    except (json.JSONDecodeError, TypeError):
        log.error("Failed to parse PR metadata from API response")
        sys.exit(1)
    return {
        "head_sha": data.get("head", {}).get("sha", ""),
        "head_ref": data.get("head", {}).get("ref", ""),
        "base_ref": data.get("base", {}).get("ref", ""),
    }


def _get_diff(repo: str, pr: str) -> str:
    """Get the PR diff. Returns empty string if the diff is unavailable
    (e.g. PRs exceeding GitHub's 300-file limit)."""
    code, out = _gh_api(
        f"repos/{repo}/pulls/{pr}",
        headers={"Accept": "application/vnd.github.v3.diff"},
    )
    if code != 0:
        log.warn("Failed to get diff from API — inline positioning unavailable")
        return ""
    return out


def _check_existing_pending(repo: str, pr: str, pr_data: PRData | None = None) -> int | None:
    """Check for existing PENDING review and return its ID."""
    if pr_data is not None:
        return pr_data.pending_review_id
    code, out = _gh_api(f"repos/{repo}/pulls/{pr}/reviews")
    if code != 0:
        return None
    try:
        reviews = json.loads(out)
    except (json.JSONDecodeError, TypeError):
        return None
    for r in reviews:
        if r.get("state") == REVIEW_STATE_PENDING:
            return int(r.get("id", 0)) or None
    return None


def _count_new_commits(repo: str, pr: str, review_sha: str, pr_data: PRData | None = None) -> int:
    """Count commits on the PR since the review SHA."""
    if pr_data is not None:
        return pr_data.new_commit_count(review_sha)
    code, out = _gh_api(f"repos/{repo}/pulls/{pr}/commits?per_page=100")
    if code != 0:
        return 0
    try:
        commits = json.loads(out)
    except (json.JSONDecodeError, TypeError):
        return 0
    for i, c in enumerate(commits):
        sha = c.get("sha", "")
        if sha.startswith(review_sha) or review_sha.startswith(sha):
            return len(commits) - i - 1
    return len(commits)


# ── Rate limiting & retries ─────────────────────────────────────────────────

def _is_rate_limited(stdout: str) -> bool:
    """Check if the API response indicates rate limiting."""
    lower = stdout.lower()
    return (
        "secondary rate limit" in lower
        or '"message": "forbidden"' in lower
        or "abuse detection" in lower
        or "retry later" in lower
    )


def _is_line_resolution_error(stdout: str) -> bool:
    """Check if the API response indicates unresolvable line positions."""
    return "line could not be resolved" in stdout.lower()


def _handle_api_attempt(attempt: int, rc: int, stdout: str) -> dict | None:
    """Handle a single API attempt result. Returns parsed JSON on success, None to retry."""
    if rc == 0:
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            log.error(f"Invalid JSON in response (attempt {attempt + 1}/{MAX_RETRIES})")
            return None

    if _is_line_resolution_error(stdout):
        raise LineResolutionError(stdout[:200])

    if _is_rate_limited(stdout):
        wait = int(min(RATE_LIMIT_WAIT * (RATE_LIMIT_BACKOFF ** attempt), RATE_LIMIT_MAX_WAIT))
        log.warn(f"Rate limited (attempt {attempt + 1}/{MAX_RETRIES}), waiting {wait}s...")
        time.sleep(wait)
        return None

    try:
        parsed = json.loads(stdout)
        error_msg = parsed.get("message", stdout[:200])
        errors = parsed.get("errors", [])
        if errors:
            error_msg += " — " + "; ".join(str(e) for e in errors)
    except (json.JSONDecodeError, AttributeError):
        error_msg = stdout[:200]
    log.error(f"GitHub API error (attempt {attempt + 1}/{MAX_RETRIES}): {error_msg}")
    if attempt < MAX_RETRIES - 1:
        time.sleep(NON_RATE_LIMIT_DELAY)
    return None


def _post_with_retries(endpoint: str, tmp_path: str) -> dict | None:
    """Post to GitHub API with retry logic. Returns parsed response or None."""
    for attempt in range(MAX_RETRIES):
        rc, stdout = _gh_api(endpoint, method="POST", input_file=tmp_path)
        result = _handle_api_attempt(attempt, rc, stdout)
        if result is not None:
            return result

    log.error(f"Failed after {MAX_RETRIES} attempts")
    return None


# ── Consolidated GraphQL PR data ───────────────────────────────────────────

_PR_DATA_QUERY = f"""
query($owner: String!, $name: String!, $pr: Int!) {{
  viewer {{ login }}
  repository(owner: $owner, name: $name) {{
    pullRequest(number: $pr) {{
      headRefOid
      headRefName
      baseRefName
      reviews(last: {GQL_REVIEWS_LIMIT}) {{
        nodes {{
          databaseId
          state
          body
          minimizedReason
          submittedAt
          author {{ login }}
        }}
      }}
      reviewThreads(first: {GQL_THREADS_LIMIT}) {{
        totalCount
        nodes {{
          id
          isResolved
          path
          line
          comments(first: {GQL_THREAD_COMMENTS_LIMIT}) {{
            totalCount
            nodes {{
              id
              databaseId
              author {{ login }}
              body
              createdAt
            }}
          }}
        }}
      }}
      comments(first: {GQL_ISSUE_COMMENTS_LIMIT}) {{
        nodes {{
          databaseId
          author {{ login }}
          body
          createdAt
        }}
      }}
      commits(last: {GQL_COMMITS_LIMIT}) {{
        nodes {{
          commit {{
            oid
            messageHeadline
          }}
        }}
      }}
    }}
  }}
}}
"""


@dataclass
class PRData:
    """Consolidated PR data from a single GraphQL query.

    Fields store raw GraphQL node shapes. Helper methods produce the
    output formats that downstream callers expect.
    """

    viewer_login: str
    head_sha: str
    head_ref: str
    base_ref: str
    reviews: list[dict] = field(default_factory=list)
    review_threads: list[dict] = field(default_factory=list)
    issue_comments: list[dict] = field(default_factory=list)
    commits: list[dict] = field(default_factory=list)

    @property
    def pending_review_id(self) -> int | None:
        for r in self.reviews:
            if r.get("state") == REVIEW_STATE_PENDING:
                return r.get("databaseId") or None
        return None

    def new_commit_count(self, review_sha: str) -> int:
        for i, c in enumerate(self.commits):
            sha = c.get("commit", {}).get("oid", "")
            if sha.startswith(review_sha) or review_sha.startswith(sha):
                return len(self.commits) - i - 1
        return len(self.commits)

    def bot_reviews_visible(self, bot_login: str) -> list[dict]:
        """Non-PENDING, non-DISMISSED, non-minimized reviews from bot_login."""
        bot_lower = bot_login.lower()
        return [
            {"id": r.get("databaseId"), "body": r.get("body", ""), "state": r.get("state", "")}
            for r in self.reviews
            if (r.get("author") or {}).get("login", "").lower() == bot_lower
            and r.get("state") not in ("PENDING", "DISMISSED")
            and not r.get("minimizedReason")
        ]

    def bot_inline_comments(self, bot_login: str) -> list[dict]:
        """Bot-authored inline review comments as [{path, body}]."""
        bot_lower = bot_login.lower()
        results = []
        for thread in self.review_threads:
            results.extend(self._bot_comments_in_thread(thread, bot_lower))
        return results

    @staticmethod
    def _bot_comments_in_thread(thread: dict, bot_lower: str) -> list[dict]:
        path = thread.get("path", "")
        return [
            {"path": path, "body": comment.get("body", "")}
            for comment in thread.get("comments", {}).get("nodes", [])
            if (comment.get("author") or {}).get("login", "").lower() == bot_lower
        ]

    def bot_review_bodies(self, bot_login: str) -> list[str]:
        """Body text of bot-authored reviews (for finding extraction)."""
        bot_lower = bot_login.lower()
        return [
            r.get("body", "")
            for r in self.reviews
            if (r.get("author") or {}).get("login", "").lower() == bot_lower
            and r.get("body")
        ]

    def reviewer_verdicts(self) -> list[dict]:
        """Latest review verdict per reviewer as [{user, state, submitted_at}]."""
        by_user: dict[str, dict] = {}
        for r in self.reviews:
            user = (r.get("author") or {}).get("login", "")
            submitted = r.get("submittedAt", "")
            state = r.get("state", "")
            if state == "PENDING":
                continue
            if user not in by_user or submitted > by_user[user]["submitted_at"]:
                by_user[user] = {"user": user, "state": state, "submitted_at": submitted}
        return list(by_user.values())

    def non_self_issue_comments(self, my_login: str) -> list[dict]:
        """Issue-level comments excluding my_login, as [{id, user, body, created_at}]."""
        my_lower = my_login.lower()
        return [
            {
                "id": c.get("databaseId"),
                "user": (c.get("author") or {}).get("login", ""),
                "body": c.get("body", ""),
                "created_at": c.get("createdAt", ""),
            }
            for c in self.issue_comments
            if (c.get("author") or {}).get("login", "").lower() != my_lower
        ]


def fetch_pr_data(repo: str, pr: str) -> PRData:
    """Fetch all PR review data in a single GraphQL query."""
    owner, name = repo.split("/", 1)
    rc, stdout = _gh_graphql(
        _PR_DATA_QUERY, {"owner": owner, "name": name, "pr": int(pr)},
    )
    if rc != 0:
        log.error("Failed to fetch PR data via GraphQL")
        sys.exit(1)
    try:
        data = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        log.error("Failed to parse PR data from GraphQL response")
        sys.exit(1)

    viewer = data.get("data", {}).get("viewer", {})
    pr_node = data.get("data", {}).get("repository", {}).get("pullRequest", {})

    threads_data = pr_node.get("reviewThreads", {})
    threads = threads_data.get("nodes", [])
    total_threads = threads_data.get("totalCount", len(threads))
    if total_threads > len(threads):
        log.warn(f"PR has {total_threads} review threads but only {len(threads)} fetched (limit: GQL_THREADS_LIMIT={GQL_THREADS_LIMIT})")

    for thread in threads:
        comments_data = thread.get("comments", {})
        total_comments = comments_data.get("totalCount", 0)
        comment_nodes = comments_data.get("nodes", [])
        if total_comments > len(comment_nodes):
            path = thread.get("path", "?")
            log.warn(f"Thread at {path} has {total_comments} comments but only {len(comment_nodes)} fetched (limit: GQL_THREAD_COMMENTS_LIMIT={GQL_THREAD_COMMENTS_LIMIT})")

    return PRData(
        viewer_login=viewer.get("login", ""),
        head_sha=pr_node.get("headRefOid", ""),
        head_ref=pr_node.get("headRefName", ""),
        base_ref=pr_node.get("baseRefName", ""),
        reviews=pr_node.get("reviews", {}).get("nodes", []),
        review_threads=threads,
        issue_comments=pr_node.get("comments", {}).get("nodes", []),
        commits=pr_node.get("commits", {}).get("nodes", []),
    )
