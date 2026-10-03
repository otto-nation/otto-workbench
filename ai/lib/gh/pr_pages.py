"""The paginated connections of a PR: page sizes, the queries that page, the drains.

GitHub caps every `first:` at 100, so a connection that can outgrow one page is
walked to exhaustion here or reported as short — never returned short and
silent. Each page size below is justified by the walk that pays for it, which
is why they live beside the walks rather than beside the queries that
interpolate them.

The consolidated one-call read of a PR is `gh.pr_data`, which hands its first
pages to the drains here. The single-call REST reads are `gh.pr_reads`. The
transport — running gh, timeouts, the rate-limit ladder — is `gh.client`.
"""

# doc-group: publishing

from __future__ import annotations

import json
from dataclasses import dataclass, field

import gh.client
import core.log
import core.proc


# ── Page sizes ──────────────────────────────────────────────────────────────

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
# charge, and `complete_truncated_comments` finishes off the rest — which is
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

# The page size for a thread already known to be deep. Deliberately the
# largest GitHub allows, and deliberately not GQL_THREAD_COMMENTS_LIMIT: that
# one is small because it is nested under `reviewThreads` and multiplied by
# 100, where this one is scored on its own for a thread whose comments we
# already know we want. A second round trip is the thing worth avoiding here.
GQL_THREAD_REFETCH_LIMIT = 100

# The same guard for a single thread's comments. _drain_thread_comments pages
# through _THREAD_COMMENTS_QUERY at GQL_THREAD_REFETCH_LIMIT a page, so 20
# pages is 2000 comments against an observed maximum of 163 per thread — this
# bounds a misbehaving server rather than any thread someone might leave.
GQL_MAX_THREAD_COMMENT_PAGES = 20


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
              lastEditedAt
            }}
          }}
"""

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
          lastEditedAt
        }}
      }}
    }}
  }}
}}
"""

# One thread's comments, addressed by the thread's own node id. This is what
# finishes off a thread the nested page size cut off, so it asks for the
# largest page GitHub allows — see GQL_THREAD_REFETCH_LIMIT for why that is the
# opposite choice from the nested one.
_THREAD_COMMENTS_QUERY = f"""
query($threadId: ID!, $endCursor: String) {{
  node(id: $threadId) {{
    ... on PullRequestReviewThread {{
      comments(first: {GQL_THREAD_REFETCH_LIMIT}, after: $endCursor) {{
        totalCount
        pageInfo {{ hasNextPage endCursor }}
        nodes {{
          id
          databaseId
          author {{ login }}
          body
          createdAt
          lastEditedAt
        }}
      }}
    }}
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


def warn_if_truncated(
    connection: dict, what: str,
    consequence: str = "reads over the older ones are incomplete",
) -> bool:
    """Report a `last:`-paged connection that came back short of its totalCount.

    Returns whether it truncated, so a caller with something better to do than
    warn may. A page of nodes looks the same whole or cut off, and `totalCount`
    is the only thing in the answer that tells the two apart — without it the
    reads below the cut are wrong with no symptom.

    Every caller pages with `last:`, so the entries missing are the oldest and
    the newest are intact. That is what keeps the truncation a warning rather
    than a failure: review verdicts, the pending-review lookup and the
    new-commit scan all read the recent end. The exposure is dedup, which looks
    for a finding it posted long enough ago to have fallen off and, not finding
    it, posts it again — so the warning is what makes a duplicate post
    explicable instead of inexplicable. `consequence` names what a specific
    caller loses when that happens; the default is generic for a caller with
    nothing more specific to say.

    Absent `totalCount` this reports nothing: a caller that did not ask for it
    gets today's silence rather than a warning on every read.
    """
    total = connection.get("totalCount", 0)
    nodes = connection.get("nodes", [])
    if total <= len(nodes):
        return False
    core.log.warn(f"{what}: {total} exist but only the newest {len(nodes)} were read "
             f"— {consequence}")
    return True


def complete_truncated_comments(threads: list[dict]) -> bool:
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
            core.log.warn(
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
        r = gh.client.graphql(
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


def drain_issue_comments(
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
        r = gh.client.graphql(
            _ISSUE_COMMENTS_QUERY,
            variables={"owner": owner, "name": name, "pr": pr, "endCursor": cursor},
        )
        if not r.ok:
            core.log.warn(core.proc.failure_message(
                "Failed to fetch a page of issue comments — the set is incomplete", r))
            return nodes, False
        try:
            data = json.loads(r.stdout)
        except (json.JSONDecodeError, TypeError):
            core.log.warn("Failed to parse a page of issue comments — the set is incomplete")
            return nodes, False
        pr_node = data.get("data", {}).get("repository", {}).get("pullRequest") or {}
        page = pr_node.get("comments") or {}
        nodes.extend(page.get("nodes", []))
        page_info = page.get("pageInfo", {})

    if page_info.get("hasNextPage"):
        core.log.warn(
            f"Issue comments hit the {GQL_MAX_ISSUE_COMMENT_PAGES}-page ceiling — "
            f"stopping at {len(nodes)}")
        return nodes, False
    return nodes, True


def _threads_page(owner: str, name: str, pr: int, cursor: str | None) -> dict | None:
    """One page of review threads, or None when the page could not be read.

    None rather than `{}`: an empty node is what a PR with no threads returns,
    and the caller has to tell that from a page it failed to fetch.
    """
    variables = {"owner": owner, "name": name, "pr": pr, "endCursor": cursor}
    r = gh.client.graphql(_THREADS_PAGE_QUERY, variables=variables)
    if not r.ok:
        core.log.warn(core.proc.failure_message(
            "Failed to fetch a page of review threads (fetch) — the thread set is incomplete", r))
        return None
    try:
        data = json.loads(r.stdout)
    except (json.JSONDecodeError, TypeError):
        core.log.warn("Failed to fetch a page of review threads (parse) — the thread set is incomplete")
        return None
    pr_node = data.get("data", {}).get("repository", {}).get("pullRequest") or {}
    return pr_node.get("reviewThreads") or {}


def drain_thread_pages(
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
            core.log.warn(f"Review threads report another page but no cursor — stopping at {len(threads)} threads")
            return ThreadSet(threads, complete=False)
        if cursor in seen:
            core.log.warn(f"Review thread pagination repeated cursor {cursor} — stopping at {len(threads)} threads")
            return ThreadSet(threads, complete=False)
        seen.add(cursor)
        page = _threads_page(owner, name, pr, cursor)
        if page is None:
            return ThreadSet(threads, complete=False)
        threads.extend(page.get("nodes", []))
        page_info = page.get("pageInfo", {})

    if page_info.get("hasNextPage"):
        core.log.warn(f"Review thread pagination hit the {GQL_MAX_THREAD_PAGES}-page ceiling — stopping at {len(threads)} threads")
        return ThreadSet(threads, complete=False)
    return ThreadSet(threads)


def fetch_review_threads(repo: str, pr: str | int) -> ThreadSet:
    """Every review thread on a PR, and whether the walk actually finished."""
    owner, name = repo.split("/", 1)
    first_page = _threads_page(owner, name, int(pr), None)
    found = drain_thread_pages(owner, name, int(pr), first_page)
    if not complete_truncated_comments(found.threads):
        return ThreadSet(found.threads, complete=False)
    return found
