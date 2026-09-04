"""Deduplication of findings against already-posted PR comments.

Fetches existing bot comments (inline and review-body), compares via
Jaccard similarity, and filters out duplicates before posting.

Fuzzy, and deliberately so: this is the read that decides whether a finding
about to be posted is one a reviewer already made in different words, so it
compares word sets rather than identities. `review.grammar.FindingIdentity` is
the exact answer, for carrying a finding forward across reviews of the same
branch — two findings that are the same finding hash the same there, and a
near-duplicate does not.
"""

# doc-group: findings

from __future__ import annotations

import functools
import json
import re
from dataclasses import dataclass, replace

from gh import client as gh_client
from gh.pr_reads import PRData, GQL_REVIEWS_LIMIT

from review.format import CLASS_SKIPPED
from review.grammar import BODY_FINDING_RE, strip_line_suffix, strip_sid_markers
from review.types import Finding


# ── Constants ───────────────────────────────────────────────────────────────

DEDUP_THRESHOLD = 0.6
REVIEW_BODY_DEDUP_THRESHOLD = 0.8


# ── Similarity ──────────────────────────────────────────────────────────────

def word_set(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_]+", text.lower()))


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


# ── Posted findings ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PostedFinding:
    """A finding already on the PR, as the posted spelling reports it.

    `path` has any `:line` suffix stripped, matching what `strip_line_suffix`
    leaves; `body` is the declaration's prose. Named fields rather than a
    `{"path", "body"}` dict because the similarity read below compares one
    against the other, and a dict lets a caller pass them the wrong way round.
    """

    path: str
    body: str


# ── Body finding extraction ────────────────────────────────────────────────

def _extract_body_findings(body: str) -> list[PostedFinding]:
    results = []
    for m in BODY_FINDING_RE.finditer(body):
        raw_path = (m.group(1) or m.group(2) or "")
        results.append(PostedFinding(strip_line_suffix(raw_path), m.group(3)))
    return results


# ── Bot user lookup ─────────────────────────────────────────────────────────

@functools.lru_cache(maxsize=1)
def get_bot_login() -> str:
    """Return the authenticated GitHub user's login, or empty string on failure."""
    return gh_client.login()


# ── Bot comment collection ──────────────────────────────────────────────────

def _collect_inline_comments(repo: str, pr: str, bot_user: str, pr_data: PRData | None = None) -> list[PostedFinding]:
    if pr_data is not None:
        posted = [
            PostedFinding(c.get("path", ""), c.get("body", ""))
            for c in pr_data.bot_inline_comments(bot_user)
        ]
    else:
        all_comments = gh_client.api_json(f"repos/{repo}/pulls/{pr}/comments", default=[])
        posted = [
            PostedFinding(c.get("path", ""), c.get("body", ""))
            for c in all_comments
            if c.get("user", {}).get("login") == bot_user
        ]
    # The stable-ID marker a posted comment carries is a handle on the finding
    # rather than part of what it says, and the fresh finding it is about to be
    # scored against carries none — so it comes off before the words are
    # counted, or every comparison loses the two tokens only this side has.
    return [replace(c, body=strip_sid_markers(c.body)) for c in posted]


def _collect_review_findings(repo: str, pr: str, bot_user: str, pr_data: PRData | None = None) -> list[PostedFinding]:
    if pr_data is not None:
        bodies = pr_data.bot_review_bodies(bot_user)
    else:
        all_reviews = gh_client.api_json(f"repos/{repo}/pulls/{pr}/reviews", default=[])
        bodies = [
            r.get("body", "") for r in all_reviews
            if r.get("user", {}).get("login") == bot_user
        ]
    entries: list[PostedFinding] = []
    for body in bodies:
        if body:
            entries.extend(_extract_body_findings(body))
    return entries


def _fetch_bot_comments(repo: str, pr: str, pr_data: PRData | None = None) -> list[PostedFinding]:
    bot_user = pr_data.viewer_login if pr_data is not None else get_bot_login()
    if not bot_user:
        return []

    entries = _collect_inline_comments(repo, pr, bot_user, pr_data)
    entries.extend(_collect_review_findings(repo, pr, bot_user, pr_data))
    return entries


# ── Dedup ───────────────────────────────────────────────────────────────────

def dedup_against_posted(
    findings: list[Finding], repo: str, pr: str,
    pr_data: PRData | None = None,
) -> tuple[list[Finding], list[Finding]]:
    existing = _fetch_bot_comments(repo, pr, pr_data)
    if not existing:
        return findings, []

    posted_entries = [
        (c.path, word_set(c.body))
        for c in existing
    ]

    kept, deduped = [], []
    for f in findings:
        f_words = word_set(f.body)
        is_dup = any(
            f.path and posted_path and
            f.path == posted_path and
            jaccard(f_words, posted_words) >= DEDUP_THRESHOLD
            for posted_path, posted_words in posted_entries
        )
        if is_dup:
            f.classification = CLASS_SKIPPED
            f.skip_reason = "duplicate of existing comment"
            deduped.append(f)
        else:
            kept.append(f)

    return kept, deduped


# ── Bot review fetching ───────────────────────────────────────────────────

def fetch_bot_reviews(repo: str, pr: str, pr_data: PRData | None = None) -> list[dict]:
    """Return all visible, non-PENDING, non-DISMISSED reviews from the bot.

    Uses GraphQL to detect minimized (hidden/outdated) reviews and exclude
    them — the REST API does not expose minimizedReason.
    Each entry has keys: id, body, state.

    Note: fetches at most the last 100 reviews. PRs with more than 100 reviews
    may miss older bot reviews — cursor-based pagination is not implemented.
    """
    if pr_data is not None:
        return pr_data.bot_reviews_visible(pr_data.viewer_login)

    bot_user = get_bot_login()
    if not bot_user:
        return []

    owner, name = repo.split("/", 1)
    query = f"""
    query($owner: String!, $name: String!, $pr: Int!) {{
      repository(owner: $owner, name: $name) {{
        pullRequest(number: $pr) {{
          reviews(last: {GQL_REVIEWS_LIMIT}) {{
            nodes {{
              databaseId
              state
              body
              minimizedReason
              author {{ login }}
            }}
          }}
        }}
      }}
    }}
    """
    result = gh_client.graphql(
        query, variables={"owner": owner, "name": name, "pr": int(pr)},
    )
    if not result.ok:
        all_reviews = gh_client.api_json(f"repos/{repo}/pulls/{pr}/reviews", default=[])
        return [
            {"id": r["id"], "body": r.get("body", ""), "state": r.get("state", "")}
            for r in all_reviews
            if r.get("user", {}).get("login") == bot_user
            and r.get("state") not in ("PENDING", "DISMISSED")
        ]

    try:
        data = json.loads(result.stdout)
        nodes = data["data"]["repository"]["pullRequest"]["reviews"]["nodes"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return []

    return [
        {"id": n["databaseId"], "body": n.get("body", ""), "state": n.get("state", "")}
        for n in nodes
        if n.get("author", {}).get("login") == bot_user
        and n.get("state") not in ("PENDING", "DISMISSED")
        and not n.get("minimizedReason")
    ]


# ── Whole-review dedup ────────────────────────────────────────────────────

def check_review_already_posted(
    bot_reviews: list[dict], body_text: str,
) -> list[int]:
    """Check if a review with matching body has already been posted.

    Takes a pre-fetched list of bot reviews (from fetch_bot_reviews).
    Returns list of matching review IDs (empty if no match).
    """
    body_words = word_set(body_text)
    matching_ids: list[int] = []

    for r in bot_reviews:
        review_body = r.get("body", "")
        if jaccard(body_words, word_set(review_body)) >= REVIEW_BODY_DEDUP_THRESHOLD:
            matching_ids.append(r["id"])

    return matching_ids
