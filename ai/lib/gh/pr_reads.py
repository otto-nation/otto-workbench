"""The review system's reads of a PR, and the GraphQL queries behind them.

The PR's own metadata, its surrounding conversation, the diff, the
pending-review check, and the consolidated review-thread query. Used by the
pipeline before any agent runs, and by review.posting and review.dedup after.

The transport is not here. ``gh_client`` owns running gh, the timeout tiers and
the rate-limit ladder; this module owns what the review system asks for and how
it reads the answer. Nothing here decides how a call is made, so a change to
retry or to a bound is made once, in the client, for every caller.

Nor is the worktree. A branch with no PR behind it is described from local git
by `review.collect.fetch_branch_metadata`, which reaches the same `PRMetadata`
this fetches — every read here goes to GitHub.
"""

# doc-group: publishing

from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from gh import client as gh_client
from git import client as git_client
from core import log
from git import numstat
from core import proc
from gh.types import PRContext, PRMetadata


# ── Constants ───────────────────────────────────────────────────────────────

REVIEW_STATE_PENDING = "PENDING"

# GraphQL page sizes — shared across queries. GitHub rejects `first:` above 100.
# Upgrade to a query builder class when: a third query shape is added, or
# fields become runtime-conditional.
GQL_REVIEWS_LIMIT = 100
GQL_THREADS_LIMIT = 100

# Sized for the thread people actually write, not the worst one on record.
# GitHub scores a query from its `first:` values before running it, and this
# one is nested inside `reviewThreads`, so it is multiplied by 100 and is most
# of what both queries cost. Measured over 217 threads on the repo under
# heaviest review: p50 and p90 are 2 comments, the mean is 4.2, and 3 threads
# exceed 10. Ten therefore serves 98.6% of threads whole for a fifth of the
# charge, and `_complete_truncated_comments` finishes off the rest — which is
# not a new risk: the old value of 50 was itself under the observed maximum of
# 163, so the set was already being truncated, silently.
GQL_THREAD_COMMENTS_LIMIT = 10

GQL_ISSUE_COMMENTS_LIMIT = 100
GQL_COMMITS_LIMIT = 100

# Ceiling on review-thread pages, so a server that keeps reporting hasNextPage
# cannot spin forever. 20 pages is 2000 threads — far beyond any real PR.
GQL_MAX_THREAD_PAGES = 20

# The same guard for issue comments. 10 pages is 1000 comments against an
# observed maximum of 230, so this bounds a misbehaving server rather than any
# PR someone might actually open.
GQL_MAX_ISSUE_COMMENT_PAGES = 10

# The same guard for a single thread's comments. _drain_thread_comments pages
# through _THREAD_COMMENTS_QUERY, which hardcodes `first: 100`, so 20 pages is
# 2000 comments against an observed maximum of 163 per thread — this bounds a
# misbehaving server rather than any thread someone might actually leave.
GQL_MAX_THREAD_COMMENT_PAGES = 20


# ── What a review knows about its PR ────────────────────────────────────────

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
        counts = numstat.parse_numstat(git_client.out(
            "diff", "--numstat", f"origin/{data['baseRefName']}...HEAD", cwd=wt_path,
        ))
        files = counts.files
        additions = counts.additions
        deletions = counts.deletions
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
    """The PR's surrounding conversation — commits, reviews, comments.

    ``pr_data`` is a consolidated query's answer; when it is given, the context
    is read out of it and no call is made.
    """
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


# ── PR refs ─────────────────────────────────────────────────────────────────

def _fetch_pr_refs(repo: str, pr: str, pr_data: PRData | None = None) -> dict:
    """Fetch the PR's refs (head SHA, head ref, base ref) in one call."""
    if pr_data is not None:
        return {"head_sha": pr_data.head_sha, "head_ref": pr_data.head_ref, "base_ref": pr_data.base_ref}
    r = gh_client.api(f"repos/{repo}/pulls/{pr}")
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
    r = gh_client.api(
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
    r = gh_client.api(f"repos/{repo}/pulls/{pr}/reviews")
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
    r = gh_client.api(f"repos/{repo}/pulls/{pr}/commits?per_page=100")
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
            pageInfo {{ hasNextPage endCursor }}
            nodes {{
              id
              databaseId
              author {{ login }}
              body
              createdAt
            }}
          }}
"""

# One thread's comments, addressed by the thread's own node id. This is what
# finishes off a thread the page size cut off, so it asks for the largest page
# GitHub allows: it only runs for a thread already known to be deep, and a
# second round trip is the thing worth avoiding here.
# Issue comments past the first page. Separate from the consolidated query so a
# PR over the 100-comment cap costs one extra call rather than making every PR
# pay for a second page it does not have.
_ISSUE_COMMENTS_QUERY = f"""
query($owner: String!, $name: String!, $pr: Int!, $endCursor: String) {{
  repository(owner: $owner, name: $name) {{
    pullRequest(number: $pr) {{
      comments(first: {GQL_ISSUE_COMMENTS_LIMIT}, after: $endCursor) {{
        totalCount
        pageInfo {{ hasNextPage endCursor }}
        nodes {{
          databaseId
          author {{ login __typename }}
          body
          createdAt
        }}
      }}
    }}
  }}
}}
"""

_THREAD_COMMENTS_QUERY = """
query($threadId: ID!, $endCursor: String) {
  node(id: $threadId) {
    ... on PullRequestReviewThread {
      comments(first: 100, after: $endCursor) {
        totalCount
        pageInfo { hasNextPage endCursor }
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


@dataclass(frozen=True)
class ThreadSet:
    """Every review thread on a PR, and whether that is actually all of them.

    `complete` is the pagination's own success, not a property of the PR. A
    caller that persists a thread ledger has to tell "this PR has N threads"
    from "we stopped early at N", and a bare list cannot: a page that failed
    mid-walk returned a short list indistinguishable from a complete one, so
    `pr comments` wrote a ledger missing every thread past the failure and
    dropped the triage verdicts on them — the one thing on a record that no API
    call re-derives.

    The distinction is per-fetch rather than per-thread, and that is enough:
    the fetch never learns which ids it failed to reach, but it knows for
    certain whether it walked to exhaustion. A thread absent from a *complete*
    fetch is deleted and its record should go; one absent from an *incomplete*
    fetch is merely unknown and its record must stand. That also makes the
    carry-forward self-healing — the first complete fetch afterwards reaps
    anything genuinely gone, so a truncated run cannot strand a record forever.
    """

    threads: list[dict] = field(default_factory=list)
    complete: bool = True


def _complete_truncated_comments(threads: list[dict]) -> bool:
    """Refetch the comments of any thread the page size cut off. Returns success.

    The page size is set for the common thread rather than the worst one — the
    p90 thread carries two comments — so the rare deep thread is finished off
    with a second call instead of being paid for on every thread in every
    query. A warning alone was what this used to do, and the truncated set went
    on to feed dedup, which reads a missing comment as one never posted and
    posts it again.
    """
    ok = True
    for thread in threads:
        comments_data = thread.get("comments", {})
        total = comments_data.get("totalCount", 0)
        nodes = comments_data.get("nodes", [])
        if total <= len(nodes):
            continue
        full = _drain_thread_comments(thread["id"], nodes, comments_data)
        if full is None:
            path = thread.get("path", "?")
            log.warn(
                f"Thread at {path} has {total} comments and the refetch failed — "
                f"only {len(nodes)} are available")
            ok = False
            continue
        comments_data["nodes"] = full
    return ok


def _drain_thread_comments(
    thread_id: str, first_nodes: list[dict], first_page: dict,
) -> list[dict] | None:
    """Every comment on one thread, or None when a page could not be read.

    Keyed on the thread's node id rather than the PR, so this asks only for the
    thread that overflowed instead of re-running the whole consolidated query
    at a larger page size.
    """
    nodes = list(first_nodes)
    page_info = first_page.get("pageInfo", {})
    seen: set[str] = set()

    for _ in range(GQL_MAX_THREAD_COMMENT_PAGES):
        if not page_info.get("hasNextPage"):
            return nodes
        cursor = page_info.get("endCursor")
        if not cursor or cursor in seen:
            return None
        seen.add(cursor)
        r = gh_client.graphql(
            _THREAD_COMMENTS_QUERY, variables={"threadId": thread_id, "endCursor": cursor},
        )
        if not r.ok:
            return None
        try:
            data = json.loads(r.stdout)
        except (json.JSONDecodeError, TypeError):
            return None
        page = (data.get("data", {}).get("node") or {}).get("comments") or {}
        nodes.extend(page.get("nodes", []))
        page_info = page.get("pageInfo", {})

    # ceiling: a thread past GQL_MAX_THREAD_COMMENT_PAGES pages of comments is
    # still short, and says so rather than reading as complete. Upgrade
    # trigger: raise the bound once a real thread trips it — the warning above
    # names the thread when one does.
    return None if page_info.get("hasNextPage") else nodes


def _drain_issue_comments(
    owner: str, name: str, pr: int, first_page: dict,
) -> tuple[list[dict], bool]:
    """Every issue comment on the PR, and whether the walk finished.

    100 is GitHub's hard cap on `first:`, so this is not a page size that can
    be raised — a PR past it needs a second call or it is silently short. This
    query asked for neither `totalCount` nor `pageInfo`, so nothing downstream
    could even tell: the observed maximum on the repo under heaviest review is
    230 comments, and the 130 past the cap were invisible to triage, to the
    dashboard, and to the settlement pass that looks for its own verdicts among
    them.

    A `tuple` rather than a type because both halves are the same fact — the
    comments and whether they are all of them — and unlike `ThreadSet` nothing
    carries this pair further than the line below.
    """
    nodes = list(first_page.get("nodes", []))
    page_info = first_page.get("pageInfo", {})
    seen: set[str] = set()

    for _ in range(GQL_MAX_ISSUE_COMMENT_PAGES - 1):
        if not page_info.get("hasNextPage"):
            return nodes, True
        cursor = page_info.get("endCursor")
        if not cursor or cursor in seen:
            return nodes, False
        seen.add(cursor)
        r = gh_client.graphql(
            _ISSUE_COMMENTS_QUERY,
            variables={"owner": owner, "name": name, "pr": pr, "endCursor": cursor},
        )
        if not r.ok:
            log.warn(proc.failure_message(
                "Failed to fetch a page of issue comments — the set is incomplete", r))
            return nodes, False
        try:
            data = json.loads(r.stdout)
        except (json.JSONDecodeError, TypeError):
            log.warn("Failed to parse a page of issue comments — the set is incomplete")
            return nodes, False
        pr_node = data.get("data", {}).get("repository", {}).get("pullRequest") or {}
        page = pr_node.get("comments") or {}
        nodes.extend(page.get("nodes", []))
        page_info = page.get("pageInfo", {})

    if page_info.get("hasNextPage"):
        log.warn(
            f"Issue comments hit the {GQL_MAX_ISSUE_COMMENT_PAGES}-page ceiling — "
            f"stopping at {len(nodes)}")
        return nodes, False
    return nodes, True


def _threads_page(owner: str, name: str, pr: int, cursor: str | None) -> dict | None:
    """One page of review threads, or None when the page could not be read.

    None rather than `{}`: an empty node is what a PR with no threads returns,
    and the caller has to tell that from a page it failed to fetch.
    """
    variables: dict = {"owner": owner, "name": name, "pr": pr}
    if cursor:
        variables["endCursor"] = cursor
    r = gh_client.graphql(_THREADS_PAGE_QUERY, variables=variables)
    if not r.ok:
        log.warn(proc.failure_message(
            "Failed to fetch a page of review threads (fetch) — the thread set is incomplete", r))
        return None
    try:
        data = json.loads(r.stdout)
    except (json.JSONDecodeError, TypeError):
        log.warn("Failed to fetch a page of review threads (parse) — the thread set is incomplete")
        return None
    pr_node = data.get("data", {}).get("repository", {}).get("pullRequest") or {}
    return pr_node.get("reviewThreads") or {}


def _drain_thread_pages(
    owner: str, name: str, pr: int, first_page: dict | None,
) -> ThreadSet:
    """Follow reviewThreads pagination from an already-fetched first page.

    Every way of stopping short — a failed page, a missing or repeated cursor,
    the page ceiling — comes back `complete=False`. Each of those used to warn
    and return the threads gathered so far, which the caller could not tell
    from the whole set.
    """
    if first_page is None:
        return ThreadSet([], complete=False)
    threads = list(first_page.get("nodes", []))
    page_info = first_page.get("pageInfo", {})
    # GitHub cursors are strictly increasing; a repeat means the server or the
    # query is not advancing, which would otherwise loop until timeout.
    seen: set[str] = set()

    for _ in range(GQL_MAX_THREAD_PAGES - 1):
        if not page_info.get("hasNextPage"):
            return ThreadSet(threads)
        cursor = page_info.get("endCursor")
        if not cursor:
            log.warn(f"Review threads report another page but no cursor — stopping at {len(threads)} threads")
            return ThreadSet(threads, complete=False)
        if cursor in seen:
            log.warn(f"Review thread pagination repeated cursor {cursor} — stopping at {len(threads)} threads")
            return ThreadSet(threads, complete=False)
        seen.add(cursor)
        page = _threads_page(owner, name, pr, cursor)
        if page is None:
            return ThreadSet(threads, complete=False)
        threads.extend(page.get("nodes", []))
        page_info = page.get("pageInfo", {})

    if page_info.get("hasNextPage"):
        log.warn(f"Review thread pagination hit the {GQL_MAX_THREAD_PAGES}-page ceiling — stopping at {len(threads)} threads")
        return ThreadSet(threads, complete=False)
    return ThreadSet(threads)


def fetch_review_threads(repo: str, pr: str | int) -> ThreadSet:
    """Every review thread on a PR, and whether the walk actually finished."""
    owner, name = repo.split("/", 1)
    first_page = _threads_page(owner, name, int(pr), None)
    found = _drain_thread_pages(owner, name, int(pr), first_page)
    if not _complete_truncated_comments(found.threads):
        return ThreadSet(found.threads, complete=False)
    return found


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
        totalCount
        pageInfo {{ hasNextPage endCursor }}
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
    # Whether `review_threads` is all of them — see `ThreadSet`, whose meaning
    # this carries through the consolidated read. Defaulted True so a caller
    # constructing PRData directly, as the tests do, keeps today's shape.
    threads_complete: bool = True

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
    r = gh_client.graphql(
        _PR_DATA_QUERY, variables={"owner": owner, "name": name, "pr": int(pr)},
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

    found = _drain_thread_pages(owner, name, int(pr), pr_node.get("reviewThreads") or {})
    comments_whole = _complete_truncated_comments(found.threads)
    issue_comments, issue_whole = _drain_issue_comments(
        owner, name, int(pr), pr_node.get("comments") or {})

    return PRData(
        viewer_login=viewer.get("login", ""),
        head_sha=pr_node.get("headRefOid", ""),
        head_ref=pr_node.get("headRefName", ""),
        base_ref=pr_node.get("baseRefName", ""),
        reviews=pr_node.get("reviews", {}).get("nodes", []),
        review_threads=found.threads,
        threads_complete=found.complete and comments_whole and issue_whole,
        issue_comments=issue_comments,
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
