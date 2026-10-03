"""The consolidated read of a PR — one GraphQL query, and the `PRData` it fills.

Reviews, review threads, issue comments, commits and the PR's own refs come back
in a single call, so a review that needs all of them pays for one round trip.
The connections that can outgrow a page are finished off by the drains in
`gh.pr_pages`, which also owns the page sizes the query interpolates.
`pr_context_from_data` reads the conversation back out of a `PRData` for a
caller that already holds one.

The single-call REST reads are `gh.pr_reads`; the transport is `gh.client`.
"""

# doc-group: publishing

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field

import gh.client
import gh.pr_pages
import core.log
import core.proc
from gh.types import PRContext


REVIEW_STATE_PENDING = "PENDING"


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
      reviews(last: {gh.pr_pages.GQL_REVIEWS_LIMIT}) {{
        totalCount
        nodes {{
          databaseId
          state
          body
          minimizedReason
          submittedAt
          lastEditedAt
          author {{ login }}
        }}
      }}
      reviewThreads(first: {gh.pr_pages.GQL_THREADS_LIMIT}) {{
        totalCount
        pageInfo {{ hasNextPage endCursor }}
        nodes {{
{gh.pr_pages._THREAD_NODE_FIELDS}
        }}
      }}
      comments(first: {gh.pr_pages.GQL_ISSUE_COMMENTS_LIMIT}) {{
        totalCount
        pageInfo {{ hasNextPage endCursor }}
        nodes {{
          databaseId
          author {{ login __typename }}
          body
          createdAt
          lastEditedAt
        }}
      }}
      commits(last: {gh.pr_pages.GQL_COMMITS_LIMIT}) {{
        totalCount
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
    # Whether `review_threads` is all of them — see `gh.pr_pages.ThreadSet`,
    # whose meaning this carries through the consolidated read. Defaulted True
    # so a caller constructing PRData directly, as the tests do, keeps today's
    # shape.
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
        """Non-self reviews with substantive body text.

        ``[{id, user, body, state, submitted_at, last_edited_at}]``.

        ``last_edited_at`` is what lets a caller tell a review it has already
        read from the same review re-worded since. An edit keeps the id and
        does not move the review, so id alone cannot: see
        `cli.review_threads`, which keys seen-ness on both.
        """
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
                # None when never edited, which is the common case — normalised
                # to "" so every consumer compares strings.
                "last_edited_at": r.get("lastEditedAt") or "",
            })
        return results

    def non_self_issue_comments(self, my_login: str) -> list[dict]:
        """Issue-level comments excluding my_login and bots.

        ``[{id, user, body, created_at, last_edited_at}]``. See
        `review_body_comments` for why the edit stamp travels with the id.
        """
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
                "last_edited_at": c.get("lastEditedAt") or "",
            })
        return results


def fetch_pr_data(repo: str, pr: str) -> PRData:
    """Fetch all PR review data in a single GraphQL query."""
    owner, name = repo.split("/", 1)
    r = gh.client.graphql(
        _PR_DATA_QUERY, variables={"owner": owner, "name": name, "pr": int(pr)},
    )
    if not r.ok:
        core.log.error(core.proc.failure_message(
            f"Failed to fetch data for {repo}#{pr} via GraphQL", r))
        sys.exit(1)
    try:
        data = json.loads(r.stdout)
    except (json.JSONDecodeError, TypeError):
        core.log.error("Failed to parse PR data from GraphQL response")
        sys.exit(1)

    viewer = data.get("data", {}).get("viewer", {})
    pr_node = data.get("data", {}).get("repository", {}).get("pullRequest", {})

    reviews = pr_node.get("reviews") or {}
    commits = pr_node.get("commits") or {}
    # The two connections nothing pages: `last:` keeps the newest, which every
    # caller wants, so the oldest falling off is reported rather than fetched.
    gh.pr_pages.warn_if_truncated(
        reviews, f"{repo}#{pr} reviews",
        "an older bot review may be missed, so dedup can repost its findings")
    gh.pr_pages.warn_if_truncated(
        commits, f"{repo}#{pr} commits",
        "the drift count is a floor rather than the real number")

    found = gh.pr_pages.drain_thread_pages(owner, name, int(pr), pr_node.get("reviewThreads") or {})
    comments_whole = gh.pr_pages.complete_truncated_comments(found.threads)
    issue_comments, issue_whole = gh.pr_pages.drain_issue_comments(
        owner, name, int(pr), pr_node.get("comments") or {})

    return PRData(
        viewer_login=viewer.get("login", ""),
        head_sha=pr_node.get("headRefOid", ""),
        head_ref=pr_node.get("headRefName", ""),
        base_ref=pr_node.get("baseRefName", ""),
        reviews=reviews.get("nodes", []),
        review_threads=found.threads,
        threads_complete=found.complete and comments_whole and issue_whole,
        issue_comments=issue_comments,
        commits=commits.get("nodes", []),
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


# ── The conversation, read out of PRData ────────────────────────────────────

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


def pr_context_from_data(pr_data: PRData) -> PRContext:
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
