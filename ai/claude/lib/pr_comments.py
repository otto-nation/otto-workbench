"""PR comments lifecycle tracking.

Handles thread lifecycle state computation, local state persistence,
and GitHub data fetching for the pr-comments skill.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from review_github import (
    PRData, GQL_THREADS_LIMIT, GQL_THREAD_COMMENTS_LIMIT,
)


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

_ACK_WORDS = {"done", "lgtm", "looks good", "thanks", "thank you", "fixed", "nice", "great", "sounds good", "perfect", "agreed", "makes sense"}
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

GRAPHQL_THREADS = f"""
query($owner: String!, $repo: String!, $number: Int!) {{
  repository(owner: $owner, name: $repo) {{
    pullRequest(number: $number) {{
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
    }}
  }}
}}
"""

GRAPHQL_RESOLVE = """
mutation($threadId: ID!) {
  resolveReviewThread(input: {threadId: $threadId}) {
    thread { isResolved }
  }
}
"""


def fetch_threads(
    owner: str, repo_name: str, pr_number: int,
    pr_data: PRData | None = None,
) -> list[dict]:
    """Fetch all review threads via GraphQL. Returns list of thread nodes."""
    if pr_data is not None:
        return pr_data.review_threads

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
        threads_data = data["data"]["repository"]["pullRequest"]["reviewThreads"]
        nodes = threads_data["nodes"]
        total = threads_data.get("totalCount", len(nodes))
        if total > len(nodes):
            print(f"Warning: PR has {total} threads but only {len(nodes)} fetched (limit: GQL_THREADS_LIMIT={GQL_THREADS_LIMIT})", file=sys.stderr)
        for node in nodes:
            comments_data = node.get("comments", {})
            comment_total = comments_data.get("totalCount", 0)
            comment_nodes = comments_data.get("nodes", [])
            if comment_total > len(comment_nodes):
                path = node.get("path", "?")
                print(f"Warning: thread at {path} has {comment_total} comments but only {len(comment_nodes)} fetched (limit: GQL_THREAD_COMMENTS_LIMIT={GQL_THREAD_COMMENTS_LIMIT})", file=sys.stderr)
        return nodes
    except (json.JSONDecodeError, KeyError, TypeError):
        return []


def _gh_rest(endpoint: str) -> tuple[int, str]:
    """Call gh api REST endpoint. Returns (exit_code, stdout)."""
    result = subprocess.run(
        ["gh", "api", endpoint],
        capture_output=True, text=True,
    )
    return result.returncode, result.stdout


def fetch_reviewer_verdicts(
    repo: str, pr_number: int,
    pr_data: PRData | None = None,
) -> list[dict]:
    """Fetch latest review verdict per reviewer."""
    if pr_data is not None:
        return pr_data.reviewer_verdicts()

    code, out = _gh_rest(f"repos/{repo}/pulls/{pr_number}/reviews?per_page=100")
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


def fetch_issue_comments(
    repo: str, pr_number: int, my_login: str,
    pr_data: PRData | None = None,
) -> list[dict]:
    """Fetch issue-level comments (general discussion). Returns non-self ones."""
    if pr_data is not None:
        return pr_data.non_self_issue_comments(my_login)

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


# ── State sync ─────────────────────────────────────────────────────────────

def sync_threads(
    threads: list[dict],
    prior_threads: dict,
    my_login: str,
) -> dict:
    """Sync GitHub thread data with local state. Returns updated threads dict."""
    result = {}
    for thread in threads:
        tid = thread["id"]
        comments = thread.get("comments", {}).get("nodes", [])
        is_resolved = thread.get("isResolved", False)

        state = compute_thread_state(comments, is_resolved, my_login)
        last_comment_id = comments[-1]["databaseId"] if comments else None
        first_comment = comments[0] if comments else {}
        reviewer = (first_comment.get("author") or {}).get("login", "")

        prior = prior_threads.get(tid, {})
        prior_last_seen = prior.get("last_seen_reply_id")

        has_new_replies = prior_last_seen is not None and last_comment_id != prior_last_seen

        if has_new_replies:
            classification = None
            summary = None
            decided_at = None
        else:
            classification = prior.get("classification")
            summary = prior.get("summary")
            decided_at = prior.get("decided_at")

        result[tid] = {
            "state": state,
            "reviewer": reviewer,
            "last_seen_reply_id": last_comment_id,
            "file": thread.get("path"),
            "line": thread.get("line"),
            "classification": classification,
            "summary": summary,
            "decided_at": decided_at,
        }
    return result


# ── Dashboard ──────────────────────────────────────────────────────────────

def _relative_time(iso_str: str) -> str:
    """Convert ISO timestamp to relative time string."""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - dt
        hours = int(delta.total_seconds() // 3600)
        if hours < 1:
            return f"{int(delta.total_seconds() // 60)} minutes ago"
        if hours < 24:
            return f"{hours} hours ago"
        days = hours // 24
        return f"{days} day{'s' if days != 1 else ''} ago"
    except (ValueError, TypeError):
        return ""


def render_dashboard(
    pr_number: int,
    threads: dict,
    verdicts: list[dict],
    issue_comments: list[dict],
) -> str:
    """Render the status dashboard as a string."""
    lines = [f"## PR #{pr_number} Review Status", ""]

    lines.append("Reviewers:")
    for v in sorted(verdicts, key=lambda x: x.get("submitted_at", ""), reverse=True):
        time_str = _relative_time(v.get("submitted_at", ""))
        lines.append(f"  @{v['user']} — {v['state']} ({time_str})")
    lines.append("")

    counts = {STATE_RESOLVED: 0, STATE_ADDRESSED: 0, STATE_CONTESTED: 0,
              STATE_NEW: 0, STATE_VERIFIED: 0, STATE_AMBIGUOUS: 0}
    for t in threads.values():
        counts[t["state"]] = counts.get(t["state"], 0) + 1
    total = len(threads)

    lines.append(f"Threads: {total} total")
    if counts[STATE_RESOLVED]:
        lines.append(f"  ✓ {counts[STATE_RESOLVED]} resolved")
    if counts[STATE_VERIFIED]:
        lines.append(f"  ✓ {counts[STATE_VERIFIED]} verified (ready to resolve)")
    if counts[STATE_ADDRESSED]:
        lines.append(f"  ⏳ {counts[STATE_ADDRESSED]} addressed (awaiting reviewer)")
    if counts[STATE_CONTESTED]:
        lines.append(f"  ⚠ {counts[STATE_CONTESTED]} contested (reviewer pushed back)")
    if counts[STATE_AMBIGUOUS]:
        lines.append(f"  ? {counts[STATE_AMBIGUOUS]} ambiguous (needs your input)")
    if counts[STATE_NEW]:
        lines.append(f"  → {counts[STATE_NEW]} new (unaddressed)")

    if issue_comments:
        lines.append(f"  💬 {len(issue_comments)} discussion comments")
    lines.append("")

    blockers = [v["user"] for v in verdicts if v["state"] == "CHANGES_REQUESTED"]
    if blockers:
        lines.append(f"Blocking merge: {', '.join('@' + b for b in blockers)}")
    elif not any(v["state"] == "APPROVED" for v in verdicts):
        lines.append("Blocking merge: no approvals yet")
    lines.append("")

    return "\n".join(lines)
