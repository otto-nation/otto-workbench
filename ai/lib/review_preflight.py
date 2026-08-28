"""What a review knows about its PR before any agent runs.

Fetches the PR's metadata and its surrounding conversation — commits, reviews,
review comments, issue comments — and classifies the reply threads a re-review
has to answer. A branch with no PR behind it is described from the worktree
instead, so a self-review reaches the same `PRMetadata` by another route.

What a review *collects* off that surface — the diff, the files, the budget it
all has to fit — is `review_collect`'s; how the collected files are ranked and
divided is `review_grouping`'s; and the records this fills in are `review_types`'.
"""

# doc-group: pipeline

from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import gh_client
import git_client
import log
import pr_context
from pr_comments import _is_acknowledgment, _is_pushback, fetch_threads
from review_collect import fetch_base, fork_point, worktree_diff
from review_dedup import _get_bot_login
from review_document import BOLD_FINDING_ID_RE
from review_github import PRData
from review_types import PRContext, PRMetadata

# ── Constants ─────────────────────────────────────────────────────────────────

DEFAULT_MAX_PARALLEL = 1

# What the Summary says when no synthesis agent wrote the review. Each names
# why synthesis did not produce the document, because a reader who cannot tell a
# failed agent from one nobody asked to run reads the same review two ways.
FALLBACK_SUMMARY = "Synthesis agent failed — findings below are from individual group reviews."
SKIPPED_SUMMARY = "Synthesis skipped by --no-synthesis — findings below are from individual group reviews."
BUDGET_SUMMARY = (
    "Synthesis did not run — the cost budget was reached first. "
    "Findings below are from individual group reviews."
)

# How much of somebody else's prose a prompt quotes back: a prior review's body,
# a review comment, the root of a thread being re-reviewed. Each one is a
# gist — enough for the agent to recognise what was said and go read the thread
# — and there is no bound on how many of them a busy PR contributes, which is
# why the cap is per-body rather than on the section they land in.
MAX_REVIEW_BODY_LEN = 200



# ── PR data fetching ──────────────────────────────────────────────────────────

def _parse_numstat(numstat: str) -> tuple[list[dict], int, int]:
    files = []
    total_add = 0
    total_del = 0
    for line in numstat.strip().split("\n"):
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        add = int(parts[0]) if parts[0] != "-" else 0
        delete = int(parts[1]) if parts[1] != "-" else 0
        files.append({"path": parts[2], "additions": add, "deletions": delete})
        total_add += add
        total_del += delete
    return files, total_add, total_del


def fetch_branch_metadata(wt_path: str, base: str | None = None) -> PRMetadata:
    # Resolved rather than defaulted to "main" in the signature: this is the
    # no-PR self-review path, so nothing upstream has named a base, and a
    # `master` repository was previously fetched and diffed against a branch it
    # does not have.
    base = base or pr_context.default_branch(wt_path)
    fetch_base(wt_path, base)
    head_sha = git_client.head_sha(cwd=wt_path)
    branch = git_client.current_branch(cwd=wt_path)
    log_range = f"origin/{base}..HEAD"

    log_output = git_client.out("log", log_range, "--oneline", cwd=wt_path)
    first_subject = log_output.split("\n")[0].split(" ", 1)[-1] if log_output else branch
    title = first_subject

    # Diffing from the fork point reaches the working tree, so the file list
    # matches the diff review_collect builds for self-review: committed,
    # uncommitted and untracked changes alike.
    numstat = worktree_diff(wt_path, fork_point(wt_path, base), numstat=True)
    files, total_add, total_del = _parse_numstat(numstat)

    return PRMetadata(
        title=title,
        body="",
        head=branch,
        base=base,
        head_sha=head_sha,
        additions=total_add,
        deletions=total_del,
        changed_files=len(files),
        files=files,
    )


def fetch_pr_metadata(
    repo: str, pr_number: str, pin_sha: str = "", wt_path: str = "",
) -> PRMetadata:
    """Fetch PR metadata, optionally pinned to an earlier commit.

    ``pin_sha`` is the commit a --recover run must complete against; ``wt_path``
    is a checkout of it. Both must be set for pinning to take effect.
    """
    data = gh_client.pr_view(
        pr_number, "title", "body", "headRefName", "baseRefName", "headRefOid",
        "additions", "deletions", "changedFiles", "files",
        "isDraft", "labels", "author",
        repo=repo,
    )
    if not data:
        log.error(f"failed to fetch PR #{pr_number} from {repo}")
        sys.exit(1)
    head_sha = data["headRefOid"]
    additions = data["additions"]
    deletions = data["deletions"]
    changed_files = data["changedFiles"]
    files = [
        {"path": f["path"], "additions": f["additions"], "deletions": f["deletions"]}
        for f in data["files"]
    ]

    # --recover completes a run against the commit it started from, so the
    # changeset must come from the pinned checkout rather than the moved PR head.
    if pin_sha and pin_sha != head_sha and wt_path:
        numstat = git_client.out(
            "diff", "--numstat", f"origin/{data['baseRefName']}...HEAD", cwd=wt_path,
        )
        files, additions, deletions = _parse_numstat(numstat)
        changed_files = len(files)
        head_sha = pin_sha

    return PRMetadata(
        title=data["title"],
        body=data.get("body") or "",
        head=data["headRefName"],
        base=data["baseRefName"],
        head_sha=head_sha,
        additions=additions,
        deletions=deletions,
        changed_files=changed_files,
        files=files,
        is_draft=data.get("isDraft", False),
        labels=[l["name"] for l in data.get("labels", [])],
        author=(data.get("author") or {}).get("login", ""),
    )


def fetch_pr_context(
    repo: str, pr_number: str, pr_data: PRData | None = None,
) -> PRContext:
    if pr_data is not None:
        return _pr_context_from_data(pr_data)

    cmds = {
        "commits": [
            "pr", "view", pr_number, "--repo", repo,
            "--json", "commits",
            "--jq", '[.commits[] | .messageHeadline] | join("\\n")',
        ],
        "reviews": [
            "api", f"repos/{repo}/pulls/{pr_number}/reviews",
            "--jq", '[.[] | {user: .user.login, state, body}]',
        ],
        "review_comments": [
            "api", f"repos/{repo}/pulls/{pr_number}/comments",
            "--jq", '[.[] | {id, path, line, body, user: .user.login, in_reply_to_id}]',
        ],
        "comments": [
            "api", f"repos/{repo}/issues/{pr_number}/comments",
            "--jq", '[.[] | {user: .user.login, body}]',
        ],
    }
    results = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(gh_client.out, *cmd): name for name, cmd in cmds.items()}
        for future in as_completed(futures):
            results[futures[future]] = future.result()
    return PRContext(
        commits=results["commits"],
        reviews=results["reviews"] or "[]",
        review_comments=results["review_comments"] or "[]",
        comments=results["comments"] or "[]",
    )


def _thread_comment_entries(thread: dict) -> list[dict]:
    """Convert a review thread's comment nodes into flat entry dicts."""
    path = thread.get("path", "")
    line = thread.get("line")
    nodes = thread.get("comments", {}).get("nodes", [])
    root_id = None
    entries = []
    for i, c in enumerate(nodes):
        entries.append({
            "id": c.get("databaseId"),
            "path": path,
            "line": line,
            "body": c.get("body", ""),
            "user": (c.get("author") or {}).get("login", ""),
            "in_reply_to_id": root_id,
        })
        if i == 0:
            root_id = c.get("databaseId")
    return entries


def _pr_context_from_data(pr_data: PRData) -> PRContext:
    """Build PRContext from PRData without any API calls."""
    commits = "\n".join(
        c.get("commit", {}).get("messageHeadline", "")
        for c in pr_data.commits
    )

    reviews = [
        {
            "user": (r.get("author") or {}).get("login", ""),
            "state": r.get("state", ""),
            "body": r.get("body", ""),
        }
        for r in pr_data.reviews
    ]

    review_comments = []
    for thread in pr_data.review_threads:
        review_comments.extend(_thread_comment_entries(thread))

    comments = [
        {
            "user": (c.get("author") or {}).get("login", ""),
            "body": c.get("body", ""),
        }
        for c in pr_data.issue_comments
    ]

    return PRContext(
        commits=commits,
        reviews=json.dumps(reviews),
        review_comments=json.dumps(review_comments),
        comments=json.dumps(comments),
    )


# ── Reply thread classification for re-reviews ──────────────────────────────

THREAD_RESOLVED = "resolved"
THREAD_ACKNOWLEDGED = "acknowledged"
THREAD_CONTESTED = "contested"
THREAD_REPLIED = "replied"
THREAD_UNREPLIED = "unreplied"

def _classify_thread_for_rereview(
    comments: list[dict], is_resolved: bool, bot_login: str,
) -> tuple[str, list[dict]]:
    """Classify a review thread from the bot-reviewer's perspective.

    Returns (state, author_replies) where author_replies are non-bot comments
    after the first bot comment.
    """
    if is_resolved:
        return THREAD_RESOLVED, []

    bot_lower = bot_login.lower()
    author_replies = []
    seen_bot = False
    for c in comments:
        login = (c.get("author") or {}).get("login", "").lower()
        if login == bot_lower:
            seen_bot = True
        elif seen_bot:
            author_replies.append(c)

    if not author_replies:
        return THREAD_UNREPLIED, []

    last_reply = author_replies[-1]
    body = last_reply.get("body", "")
    if _is_acknowledgment(body):
        return THREAD_ACKNOWLEDGED, author_replies
    if _is_pushback(body):
        return THREAD_CONTESTED, author_replies
    return THREAD_REPLIED, author_replies


def _match_thread_to_finding(root_body: str) -> str:
    """Extract finding ID (e.g. 'M1') from a bot-posted review comment body."""
    m = BOLD_FINDING_ID_RE.search(root_body)
    return m.group(1) if m else ""


def fetch_reply_threads(
    repo: str, pr_number: str, bot_login: str = "",
    pr_data: PRData | None = None,
) -> dict:
    """Fetch and classify reply threads on bot-authored review comments.

    Returns a dict with:
      - threads: list of per-thread dicts with state, finding_id, replies, path, line
      - summary: count per state
    """
    if not bot_login:
        bot_login = pr_data.viewer_login if pr_data is not None else _get_bot_login()
    if not bot_login:
        log.warn("Could not detect bot login — skipping reply thread analysis")
        return {"threads": [], "summary": {}}

    owner, name = repo.split("/", 1)
    try:
        raw_threads = fetch_threads(owner, name, int(pr_number), pr_data)
    except Exception:
        return {"threads": [], "summary": {}}

    if not raw_threads:
        return {"threads": [], "summary": {}}

    bot_lower = bot_login.lower()
    classified = []
    summary: dict[str, int] = {}

    for thread in raw_threads:
        comments = thread.get("comments", {}).get("nodes", [])
        if not comments:
            continue
        root = comments[0]
        root_author = (root.get("author") or {}).get("login", "").lower()
        if root_author != bot_lower:
            continue

        is_resolved = thread.get("isResolved", False)
        state, author_replies = _classify_thread_for_rereview(
            comments, is_resolved, bot_login,
        )
        finding_id = _match_thread_to_finding(root.get("body", ""))

        classified.append({
            "state": state,
            "finding_id": finding_id,
            "path": thread.get("path", ""),
            "line": thread.get("line"),
            "root_body": root.get("body", "")[:MAX_REVIEW_BODY_LEN],
            "replies": [
                {
                    "author": (r.get("author") or {}).get("login", ""),
                    "body": r.get("body", ""),
                }
                for r in author_replies
            ],
        })
        summary[state] = summary.get(state, 0) + 1

    return {"threads": classified, "summary": summary}

