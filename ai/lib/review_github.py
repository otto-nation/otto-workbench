"""GitHub API primitives, retry logic, and PR metadata fetching.

Low-level wrappers around ``gh api`` with rate-limit handling and
exponential backoff.  Used by review_posting and review_dedup.
"""

# doc-group: publishing

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field

import log
import proc
import timeouts
from proc import CmdResult


# ── Constants ───────────────────────────────────────────────────────────────

MAX_RETRIES = 5
RATE_LIMIT_WAIT = 60
RATE_LIMIT_BACKOFF = 1.5
RATE_LIMIT_MAX_WAIT = 300
NON_RATE_LIMIT_DELAY = 5

REVIEW_STATE_PENDING = "PENDING"

# GraphQL page sizes — shared across queries. GitHub rejects `first:` above 100.
# Upgrade to a query builder class when: a third query shape is added, or
# fields become runtime-conditional.
GQL_REVIEWS_LIMIT = 100
GQL_THREADS_LIMIT = 100
GQL_THREAD_COMMENTS_LIMIT = 50
GQL_ISSUE_COMMENTS_LIMIT = 100
GQL_COMMITS_LIMIT = 100

# Ceiling on review-thread pages, so a server that keeps reporting hasNextPage
# cannot spin forever. 20 pages is 2000 threads — far beyond any real PR.
GQL_MAX_THREAD_PAGES = 20


# ── Exceptions ──────────────────────────────────────────────────────────────


class LineResolutionError(Exception):
    """GitHub cannot resolve line positions for inline comments."""


# ── Core API ────────────────────────────────────────────────────────────────

def _gh_api(
    endpoint: str, method: str = "GET", input_file: str | None = None,
    headers: dict[str, str] | None = None,
) -> CmdResult:
    """Call gh api and return everything it said.

    Both streams, not just stdout: gh writes an API error body to stdout but
    reports a transport failure — the 5xx that is worth retrying — only on
    stderr, and a wrapper returning `(returncode, stdout)` has nowhere to put
    it.
    """
    cmd = ["gh", "api", endpoint]
    if method != "GET":
        cmd.extend(["--method", method])
    if input_file:
        cmd.extend(["--input", input_file])
    for key, val in (headers or {}).items():
        cmd.extend(["--header", f"{key}: {val}"])

    return proc.run(cmd, timeout=timeouts.NETWORK)


def _gh_graphql(query: str, variables: dict) -> CmdResult:
    """Call gh api graphql and return everything it said.

    The query string is passed as a raw field (-f); all variables are passed
    as typed fields (-F) so gh auto-detects integers and booleans.
    """
    cmd = ["gh", "api", "graphql", "-f", f"query={query}"]
    for key, val in variables.items():
        cmd.extend(["-F", f"{key}={val}"])
    return proc.run(cmd, timeout=timeouts.NETWORK)


def _fetch_json_list(endpoint: str) -> list:
    r = _gh_api(endpoint)
    if not r.ok:
        return []
    try:
        return json.loads(r.stdout)
    except (json.JSONDecodeError, TypeError):
        return []


# ── PR metadata ─────────────────────────────────────────────────────────────

def _fetch_pr_metadata(repo: str, pr: str, pr_data: PRData | None = None) -> dict:
    """Fetch PR metadata (head SHA, head ref, base ref) in one call."""
    if pr_data is not None:
        return {"head_sha": pr_data.head_sha, "head_ref": pr_data.head_ref, "base_ref": pr_data.base_ref}
    r = _gh_api(f"repos/{repo}/pulls/{pr}")
    if not r.ok:
        log.error(proc.failure_message(f"Failed to fetch metadata for {repo}#{pr}", r))
        sys.exit(1)
    try:
        data = json.loads(r.stdout)
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
    r = _gh_api(
        f"repos/{repo}/pulls/{pr}",
        headers={"Accept": "application/vnd.github.v3.diff"},
    )
    if not r.ok:
        log.warn(proc.failure_message(
            "Failed to get diff from API — inline positioning unavailable", r))
        return ""
    return r.stdout


def _check_existing_pending(repo: str, pr: str, pr_data: PRData | None = None) -> int | None:
    """Check for existing PENDING review and return its ID."""
    if pr_data is not None:
        return pr_data.pending_review_id
    r = _gh_api(f"repos/{repo}/pulls/{pr}/reviews")
    if not r.ok:
        # Warned rather than silent: a caller that reads None as "no pending
        # review" opens a second one, and the reason it could not look is the
        # only thing that explains the duplicate.
        log.warn(proc.failure_message(
            f"Could not check {repo}#{pr} for an existing pending review", r))
        return None
    try:
        reviews = json.loads(r.stdout)
    except (json.JSONDecodeError, TypeError):
        return None
    for review in reviews:
        if review.get("state") == REVIEW_STATE_PENDING:
            return int(review.get("id", 0)) or None
    return None


def _count_new_commits(repo: str, pr: str, review_sha: str, pr_data: PRData | None = None) -> int:
    """Count commits on the PR since the review SHA."""
    if pr_data is not None:
        return pr_data.new_commit_count(review_sha)
    r = _gh_api(f"repos/{repo}/pulls/{pr}/commits?per_page=100")
    if not r.ok:
        # 0 is also the answer for "nothing new since the review", so the
        # warning is what tells those two apart.
        log.warn(proc.failure_message(f"Could not count new commits on {repo}#{pr}", r))
        return 0
    try:
        commits = json.loads(r.stdout)
    except (json.JSONDecodeError, TypeError):
        return 0
    for i, c in enumerate(commits):
        sha = c.get("sha", "")
        if sha.startswith(review_sha) or review_sha.startswith(sha):
            return len(commits) - i - 1
    return len(commits)


# ── Rate limiting & retries ─────────────────────────────────────────────────

# Classification reads both streams. gh puts an API error body on stdout and
# its own status line on stderr, so which one carries the evidence depends on
# how the call failed — a 403 explains itself in the body, a 503 or a dropped
# connection leaves the body empty.

def _is_rate_limited(output: str) -> bool:
    """Check if the API response indicates rate limiting."""
    lower = output.lower()
    return (
        "secondary rate limit" in lower
        or '"message": "forbidden"' in lower
        or "abuse detection" in lower
        or "retry later" in lower
    )


def _is_line_resolution_error(output: str) -> bool:
    """Check if the API response indicates unresolvable line positions."""
    return "line could not be resolved" in output.lower()


def _api_error_message(r: CmdResult) -> str:
    """The most specific account of a failed call the response supports.

    GitHub's error body is the best of the three, when there is one: it names
    the field that was rejected. Falling back to stderr rather than to a slice
    of an empty stdout is the difference between naming HTTP 503 and printing
    nothing after the colon.
    """
    try:
        parsed = json.loads(r.stdout)
        message = parsed.get("message", "")
        errors = parsed.get("errors", [])
    except (json.JSONDecodeError, AttributeError):
        message, errors = "", []
    if not message:
        return (r.detail or r.combined_output.strip())[:proc.DETAIL_LIMIT]
    if errors:
        message += " — " + "; ".join(str(e) for e in errors)
    return message


def _handle_api_attempt(attempt: int, r: CmdResult) -> dict | None:
    """Handle a single API attempt result. Returns parsed JSON on success, None to retry."""
    if r.ok:
        try:
            return json.loads(r.stdout)
        except json.JSONDecodeError:
            log.error(f"Invalid JSON in response (attempt {attempt + 1}/{MAX_RETRIES})")
            return None

    said = r.combined_output
    if _is_line_resolution_error(said):
        raise LineResolutionError(said[:proc.DETAIL_LIMIT])

    # A 5xx is the far end's, and waiting is the whole remedy — the same
    # treatment a secondary rate limit gets, and the case that used to fall
    # through to the flat 5-second delay because the evidence for it is on
    # stderr, where nothing was looking.
    if _is_rate_limited(said) or r.server_error:
        cause = "Server error" if r.server_error else "Rate limited"
        wait = int(min(RATE_LIMIT_WAIT * (RATE_LIMIT_BACKOFF ** attempt), RATE_LIMIT_MAX_WAIT))
        log.warn(f"{cause} (attempt {attempt + 1}/{MAX_RETRIES}), waiting {wait}s: {_api_error_message(r)}")
        time.sleep(wait)
        return None

    log.error(f"GitHub API error (attempt {attempt + 1}/{MAX_RETRIES}): {_api_error_message(r)}")
    if attempt < MAX_RETRIES - 1:
        time.sleep(NON_RATE_LIMIT_DELAY)
    return None


def _post_with_retries(endpoint: str, tmp_path: str) -> dict | None:
    """Post to GitHub API with retry logic. Returns parsed response or None."""
    for attempt in range(MAX_RETRIES):
        r = _gh_api(endpoint, method="POST", input_file=tmp_path)
        result = _handle_api_attempt(attempt, r)
        if result is not None:
            return result

    log.error(f"Failed after {MAX_RETRIES} attempts")
    return None


# ── Review threads ─────────────────────────────────────────────────────────

# One definition of a review-thread node, shared by the consolidated PR query
# and the follow-up page query so the two cannot drift apart.
_THREAD_NODE_FIELDS = f"""
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
"""

# The cursor variable is named endCursor so this query stays compatible with
# `gh api graphql --paginate`, which only advances on that exact name. We drive
# the loop ourselves — a wrong name there re-requests page 1 forever rather
# than erroring.
_THREADS_PAGE_QUERY = f"""
query($owner: String!, $name: String!, $pr: Int!, $endCursor: String) {{
  repository(owner: $owner, name: $name) {{
    pullRequest(number: $pr) {{
      reviewThreads(first: {GQL_THREADS_LIMIT}, after: $endCursor) {{
        totalCount
        pageInfo {{ hasNextPage endCursor }}
        nodes {{
{_THREAD_NODE_FIELDS}
        }}
      }}
    }}
  }}
}}
"""


def _warn_truncated_comments(threads: list[dict]) -> None:
    """Warn for any thread whose comments were cut off by the page size."""
    for thread in threads:
        comments_data = thread.get("comments", {})
        total = comments_data.get("totalCount", 0)
        nodes = comments_data.get("nodes", [])
        if total > len(nodes):
            path = thread.get("path", "?")
            log.warn(f"Thread at {path} has {total} comments but only {len(nodes)} fetched (limit: GQL_THREAD_COMMENTS_LIMIT={GQL_THREAD_COMMENTS_LIMIT})")


def _threads_page(owner: str, name: str, pr: int, cursor: str | None) -> dict:
    """Fetch one page of review threads. Returns the reviewThreads node, or {}."""
    variables: dict = {"owner": owner, "name": name, "pr": pr}
    if cursor:
        variables["endCursor"] = cursor
    r = _gh_graphql(_THREADS_PAGE_QUERY, variables)
    if not r.ok:
        log.warn(proc.failure_message(
            "Failed to fetch a page of review threads (fetch) — the thread set is incomplete", r))
        return {}
    try:
        data = json.loads(r.stdout)
    except (json.JSONDecodeError, TypeError):
        log.warn("Failed to fetch a page of review threads (parse) — the thread set is incomplete")
        return {}
    pr_node = data.get("data", {}).get("repository", {}).get("pullRequest") or {}
    return pr_node.get("reviewThreads") or {}


def _drain_thread_pages(owner: str, name: str, pr: int, first_page: dict) -> list[dict]:
    """Follow reviewThreads pagination from an already-fetched first page."""
    threads = list(first_page.get("nodes", []))
    page_info = first_page.get("pageInfo", {})
    # GitHub cursors are strictly increasing; a repeat means the server or the
    # query is not advancing, which would otherwise loop until timeout.
    seen: set[str] = set()

    for _ in range(GQL_MAX_THREAD_PAGES - 1):
        if not page_info.get("hasNextPage"):
            return threads
        cursor = page_info.get("endCursor")
        if not cursor:
            log.warn(f"Review threads report another page but no cursor — stopping at {len(threads)} threads")
            return threads
        if cursor in seen:
            log.warn(f"Review thread pagination repeated cursor {cursor} — stopping at {len(threads)} threads")
            return threads
        seen.add(cursor)
        page = _threads_page(owner, name, pr, cursor)
        threads.extend(page.get("nodes", []))
        page_info = page.get("pageInfo", {})

    if page_info.get("hasNextPage"):
        log.warn(f"Review thread pagination hit the {GQL_MAX_THREAD_PAGES}-page ceiling — stopping at {len(threads)} threads")
    return threads


def fetch_review_threads(repo: str, pr: str | int) -> list[dict]:
    """Fetch every review thread on a PR, following pagination past page one."""
    owner, name = repo.split("/", 1)
    first_page = _threads_page(owner, name, int(pr), None)
    threads = _drain_thread_pages(owner, name, int(pr), first_page)
    _warn_truncated_comments(threads)
    return threads


# ── Consolidated GraphQL PR data ───────────────────────────────────────────

_PR_DATA_QUERY = f"""
query($owner: String!, $name: String!, $pr: Int!) {{
  viewer {{ login }}
  repository(owner: $owner, name: $name) {{
    pullRequest(number: $pr) {{
      headRefOid
      headRefName
      baseRefName
      author {{ login }}
      isDraft
      labels(first: 20) {{ nodes {{ name }} }}
      reviewDecision
      reviewRequests(first: 20) {{ nodes {{ requestedReviewer {{ ... on User {{ login }} ... on Team {{ name slug }} }} }} }}
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
        pageInfo {{ hasNextPage endCursor }}
        nodes {{
{_THREAD_NODE_FIELDS}
        }}
      }}
      comments(first: {GQL_ISSUE_COMMENTS_LIMIT}) {{
        nodes {{
          databaseId
          author {{ login __typename }}
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
    author: str = ""
    is_draft: bool = False
    labels: list[str] = field(default_factory=list)
    review_decision: str = ""
    requested_reviewers: list[str] = field(default_factory=list)

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

    def review_body_comments(self, my_login: str) -> list[dict]:
        """Non-self reviews with substantive body text, as [{id, user, body, state, submitted_at}]."""
        my_lower = my_login.lower()
        results = []
        for r in self.reviews:
            author = r.get("author") or {}
            login = author.get("login", "")
            if login.lower() == my_lower:
                continue
            state = r.get("state", "")
            if state == "PENDING":
                continue
            if r.get("minimizedReason"):
                continue
            body = (r.get("body") or "").strip()
            if not body:
                continue
            results.append({
                "id": r.get("databaseId"),
                "user": login,
                "body": body,
                "state": state,
                "submitted_at": r.get("submittedAt", ""),
            })
        return results

    def non_self_issue_comments(self, my_login: str) -> list[dict]:
        """Issue-level comments excluding my_login and bots, as [{id, user, body, created_at}]."""
        my_lower = my_login.lower()
        results = []
        for c in self.issue_comments:
            author = c.get("author") or {}
            login = author.get("login", "")
            if login.lower() == my_lower:
                continue
            if author.get("__typename") == "Bot":
                continue
            results.append({
                "id": c.get("databaseId"),
                "user": login,
                "body": c.get("body", ""),
                "created_at": c.get("createdAt", ""),
            })
        return results


def fetch_pr_data(repo: str, pr: str) -> PRData:
    """Fetch all PR review data in a single GraphQL query."""
    owner, name = repo.split("/", 1)
    r = _gh_graphql(
        _PR_DATA_QUERY, {"owner": owner, "name": name, "pr": int(pr)},
    )
    if not r.ok:
        log.error(proc.failure_message(
            f"Failed to fetch data for {repo}#{pr} via GraphQL", r))
        sys.exit(1)
    try:
        data = json.loads(r.stdout)
    except (json.JSONDecodeError, TypeError):
        log.error("Failed to parse PR data from GraphQL response")
        sys.exit(1)

    viewer = data.get("data", {}).get("viewer", {})
    pr_node = data.get("data", {}).get("repository", {}).get("pullRequest", {})

    threads = _drain_thread_pages(owner, name, int(pr), pr_node.get("reviewThreads") or {})
    _warn_truncated_comments(threads)

    return PRData(
        viewer_login=viewer.get("login", ""),
        head_sha=pr_node.get("headRefOid", ""),
        head_ref=pr_node.get("headRefName", ""),
        base_ref=pr_node.get("baseRefName", ""),
        reviews=pr_node.get("reviews", {}).get("nodes", []),
        review_threads=threads,
        issue_comments=pr_node.get("comments", {}).get("nodes", []),
        commits=pr_node.get("commits", {}).get("nodes", []),
        author=(pr_node.get("author") or {}).get("login", ""),
        is_draft=pr_node.get("isDraft", False),
        labels=[n["name"] for n in pr_node.get("labels", {}).get("nodes", [])],
        review_decision=pr_node.get("reviewDecision") or "",
        requested_reviewers=[
            (n.get("requestedReviewer") or {}).get("login", "")
            for n in pr_node.get("reviewRequests", {}).get("nodes", [])
            if (n.get("requestedReviewer") or {}).get("login")
        ],
    )
