"""What the author did with the threads the prior review's findings opened.

Every finding posted inline opened a review thread, and what the author did
with that thread — answered it, argued with it, resolved it — is an account of
the finding independent of the `## Prior findings` ledger `review_reconcile`
reads. `fetch_reply_threads` classifies each thread into a `ReplyState` and
matches it back to the finding its root comment declared, so a re-review can
read the thread's account of a finding beside the ledger's. That match is a
`ThreadFinding` carrying both of the names a posted comment gives a finding,
because the visible one is renumbered every round and only the stable ID
survives to the next.

Only a thread whose first comment is the reviewing bot's own counts. A thread
the author opened is a comment on the PR rather than a reply to a finding, and
there is no finding for it to be an account of.

Fetching is `pr_comments`'s; what a re-review is shown of the result is
`review_prompt_sections`' and `review_prompt_prior`'s.
"""

# doc-group: findings

from __future__ import annotations

from dataclasses import dataclass

from core import log
from pr.comments import fetch_threads, is_acknowledgment, is_pushback
from review.dedup import get_bot_login
from gh.pr_reads import PRData
from review.grammar import BOLD_FINDING_ID_RE, SID_MARKER_RE
from review.types import ReplyState


@dataclass(frozen=True)
class ThreadVerdict:
    """What a reply thread says about the finding it hangs off.

    `replies` is every comment after ours, so a caller can quote the pushback
    rather than only report that there was some.
    """

    state: ReplyState
    replies: list[dict]


@dataclass(frozen=True)
class ReplyThreads:
    """Every reply thread on a PR, and the counts a re-review reports."""

    threads: list[dict]
    summary: dict


def _classify_thread_for_rereview(
    comments: list[dict], is_resolved: bool, bot_login: str,
) -> ThreadVerdict:
    """Classify a review thread from the bot-reviewer's perspective.

    `replies` on the result is every non-bot comment after the first bot
    comment.
    """
    if is_resolved:
        return ThreadVerdict(ReplyState.RESOLVED, [])

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
        return ThreadVerdict(ReplyState.UNREPLIED, [])

    last_reply = author_replies[-1]
    body = last_reply.get("body", "")
    if is_acknowledgment(body):
        return ThreadVerdict(ReplyState.ACKNOWLEDGED, author_replies)
    if is_pushback(body):
        return ThreadVerdict(ReplyState.CONTESTED, author_replies)
    return ThreadVerdict(ReplyState.REPLIED, author_replies)


@dataclass(frozen=True)
class ThreadFinding:
    """Which finding a posted comment's root body says it is about.

    Both names, because they answer in different rounds. `stable_id` is the
    durable one: it hashes the finding's location and wording, so the prior
    review's `<!-- sid: -->` markers still hold it a round later. `posted_id` is
    the `[M1]` a reviewer sees, assigned by diff position at post time — the
    same finding wears a different number in the review file, so it is the
    fallback for comments posted before the marker existed rather than the key.

    Empty on both counts when the body declares no finding, which is an ordinary
    outcome: a thread's root is only sometimes one of ours.
    """

    posted_id: str = ""
    stable_id: str = ""


def _match_thread_to_finding(root_body: str) -> ThreadFinding:
    """Which finding the bot-posted comment `root_body` opens a thread on."""
    sid = SID_MARKER_RE.search(root_body)
    m = BOLD_FINDING_ID_RE.search(root_body)
    return ThreadFinding(
        posted_id=m.group(1) if m else "",
        stable_id=sid.group(1) if sid else "",
    )


def fetch_reply_threads(
    repo: str, pr_number: str, bot_login: str = "",
    pr_data: PRData | None = None,
) -> ReplyThreads:
    """Fetch and classify reply threads on bot-authored review comments.

    ``bot_login`` is the reviewer whose comments count as roots; it is read off
    ``pr_data`` or detected when the caller does not name one. ``pr_data`` is a
    consolidated query's answer, which the threads are taken from rather than
    re-fetched.

    `threads` is a list of per-thread dicts with state, finding_id, stable_id,
    replies, path, line; `summary` is the count per state.
    """
    if not bot_login:
        bot_login = pr_data.viewer_login if pr_data is not None else get_bot_login()
    if not bot_login:
        log.warn("Could not detect bot login — skipping reply thread analysis")
        return ReplyThreads([], {})

    owner, name = repo.split("/", 1)
    try:
        raw_threads = fetch_threads(owner, name, int(pr_number), pr_data)
    except Exception as exc:
        # Context, not a prerequisite — but announced, for the same reason the
        # missing-login skip above is: a silent one is indistinguishable from a
        # PR that simply has no threads.
        log.warn(f"Could not fetch reply threads — skipping thread analysis: {exc}")
        return ReplyThreads([], {})

    if not raw_threads:
        return ReplyThreads([], {})

    bot_lower = bot_login.lower()
    classified = []
    summary: dict[ReplyState, int] = {}

    for thread in raw_threads:
        comments = thread.get("comments", {}).get("nodes", [])
        if not comments:
            continue
        root = comments[0]
        root_author = (root.get("author") or {}).get("login", "").lower()
        if root_author != bot_lower:
            continue

        is_resolved = thread.get("isResolved", False)
        verdict = _classify_thread_for_rereview(comments, is_resolved, bot_login)
        match = _match_thread_to_finding(root.get("body", ""))

        classified.append({
            "state": verdict.state,
            "finding_id": match.posted_id,
            "stable_id": match.stable_id,
            "path": thread.get("path", ""),
            "line": thread.get("line"),
            "replies": [
                {
                    "author": (r.get("author") or {}).get("login", ""),
                    "body": r.get("body", ""),
                }
                for r in verdict.replies
            ],
        })
        summary[verdict.state] = summary.get(verdict.state, 0) + 1

    return ReplyThreads(classified, summary)
