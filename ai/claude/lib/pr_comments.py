"""PR comments lifecycle tracking.

Handles thread lifecycle state computation, local state persistence,
and GitHub data fetching for the pr-comments skill.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


# ── State file I/O ─────────────────────────────────────────────────────────

def empty_state(repo: str, pr_number: int, my_login: str) -> dict:
    """Create a fresh state object."""
    return {
        "repo": repo,
        "pr_number": pr_number,
        "last_run": datetime.now(timezone.utc).isoformat(),
        "my_login": my_login,
        "threads": {},
    }


def load_state(path: Path) -> dict | None:
    """Load state from file. Returns None if file doesn't exist."""
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def save_state(path: Path, state: dict) -> None:
    """Save state to file, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    with open(path, "w") as f:
        json.dump(state, f, indent=2)
        f.write("\n")


# ── Thread lifecycle states ────────────────────────────────────────────────

STATE_NEW = "new"
STATE_ADDRESSED = "addressed"
STATE_VERIFIED = "verified"
STATE_CONTESTED = "contested"
STATE_RESOLVED = "resolved"
STATE_AMBIGUOUS = "ambiguous"

_ACK_WORDS = {"done", "lgtm", "looks good", "thanks", "fixed", "nice", "great"}
_ACK_EMOJI = {"👍", "✅", ":thumbsup:", ":white_check_mark:"}
_PUSHBACK_WORDS = {"still", "but", "however", "actually", "i think we should", "not quite", "doesn't address"}

ACK_MAX_LEN = 100


def _is_acknowledgment(body: str) -> bool:
    """Check if a reply body looks like an acknowledgment."""
    lower = body.lower().strip()
    if len(lower) > ACK_MAX_LEN:
        return False
    for word in _ACK_WORDS:
        if word in lower:
            return True
    for emoji in _ACK_EMOJI:
        if emoji in body:
            return True
    return False


def _is_pushback(body: str) -> bool:
    """Check if a reply body looks like pushback."""
    lower = body.lower().strip()
    for word in _PUSHBACK_WORDS:
        if word in lower:
            return True
    if "?" in body and len(lower) > 10:
        return True
    if len(lower) > ACK_MAX_LEN and not _is_acknowledgment(body):
        return True
    return False


def compute_thread_state(
    comments: list[dict],
    is_resolved: bool,
    my_login: str,
) -> str:
    """Compute the lifecycle state of a thread from its comments.

    Returns one of: new, addressed, verified, contested, resolved, ambiguous.
    """
    if is_resolved:
        return STATE_RESOLVED

    if not comments:
        return STATE_NEW

    my_login_lower = my_login.lower()
    last_comment = comments[-1]
    last_author = (last_comment.get("author") or {}).get("login", "").lower()

    has_my_reply = any(
        (c.get("author") or {}).get("login", "").lower() == my_login_lower
        for c in comments
    )

    if not has_my_reply:
        return STATE_NEW

    if last_author == my_login_lower:
        return STATE_ADDRESSED

    # Reviewer replied after me — classify the reply
    body = last_comment.get("body", "")
    if _is_acknowledgment(body):
        return STATE_VERIFIED
    if _is_pushback(body):
        return STATE_CONTESTED
    return STATE_AMBIGUOUS


# ── GitHub data fetching ───────────────────────────────────────────────────

GRAPHQL_THREADS = """
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      reviewThreads(first: 100) {
        nodes {
          id
          isResolved
          path
          line
          comments(first: 50) {
            nodes {
              id
              databaseId
              author { login }
              body
              createdAt
            }
          }
        }
      }
    }
  }
}
"""

GRAPHQL_RESOLVE = """
mutation($threadId: ID!) {
  resolveReviewThread(input: {threadId: $threadId}) {
    thread { isResolved }
  }
}
"""


def fetch_threads(owner: str, repo_name: str, pr_number: int) -> list[dict]:
    """Fetch all review threads via GraphQL. Returns list of thread nodes."""
    query = json.dumps({
        "query": GRAPHQL_THREADS,
        "variables": {"owner": owner, "repo": repo_name, "number": pr_number},
    })
    result = subprocess.run(
        ["gh", "api", "graphql", "--input", "-"],
        input=query, capture_output=True, text=True,
    )
    if result.returncode != 0:
        return []
    try:
        data = json.loads(result.stdout)
        return data["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return []


def _gh_rest(endpoint: str) -> tuple[int, str]:
    """Call gh api REST endpoint. Returns (exit_code, stdout)."""
    result = subprocess.run(
        ["gh", "api", endpoint],
        capture_output=True, text=True,
    )
    return result.returncode, result.stdout


def fetch_reviewer_verdicts(repo: str, pr_number: int) -> list[dict]:
    """Fetch latest review verdict per reviewer."""
    code, out = _gh_rest(f"repos/{repo}/pulls/{pr_number}/reviews")
    if code != 0:
        return []
    try:
        reviews = json.loads(out)
    except (json.JSONDecodeError, TypeError):
        return []
    by_user: dict[str, dict] = {}
    for r in reviews:
        user = r.get("user", {}).get("login", "")
        submitted = r.get("submitted_at", "")
        state = r.get("state", "")
        if state == "PENDING":
            continue
        if user not in by_user or submitted > by_user[user]["submitted_at"]:
            by_user[user] = {"user": user, "state": state, "submitted_at": submitted}
    return list(by_user.values())


def fetch_issue_comments(repo: str, pr_number: int, my_login: str) -> list[dict]:
    """Fetch issue-level comments (general discussion). Returns non-self ones."""
    code, out = _gh_rest(f"repos/{repo}/issues/{pr_number}/comments?per_page=100")
    if code != 0:
        return []
    try:
        comments = json.loads(out)
    except (json.JSONDecodeError, TypeError):
        return []
    result = []
    my_login_lower = my_login.lower()
    for c in comments:
        user = c.get("user", {}).get("login", "")
        if user.lower() == my_login_lower:
            continue
        result.append({
            "id": c.get("id"),
            "user": user,
            "body": c.get("body", ""),
            "created_at": c.get("created_at", ""),
        })
    return result


def resolve_thread(thread_id: str) -> bool:
    """Resolve a review thread on GitHub via GraphQL mutation."""
    query = json.dumps({
        "query": GRAPHQL_RESOLVE,
        "variables": {"threadId": thread_id},
    })
    result = subprocess.run(
        ["gh", "api", "graphql", "--input", "-"],
        input=query, capture_output=True, text=True,
    )
    return result.returncode == 0
