"""What a PR is owed an answer for — the one definition `pr comments` and `pr batch` share.

Owed feedback is three things: unresolved review threads nobody has settled;
review bodies from someone else that carry text and are neither pending nor
minimized (`PRData.review_body_comments`); and PR comments from someone else
that are not a bot's (`PRData.non_self_issue_comments`). The two top-level
kinds are then filtered through the seen record a successful triage writes
(`CommentsSummary.seen_*`), keyed on comment id *and* edit stamp.

`pr comments` flags what triage may skip with `mark_seen`; `pr batch` plans
its comments step from `owed_feedback`. Both read seen-ness through
`is_seen`, so the planner cannot skip a review body that `pr comments` would
decompose, nor plan a step for one it would drop.
"""

# doc-group: publishing

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from gh.pr_data import PRData

if TYPE_CHECKING:
    # Type-only: `pr.domains` renders an `OwedFeedback` on the status line, so
    # it imports this module and a runtime import back would be a cycle.
    from pr.domains import CommentsSummary


def edit_stamp(comment: dict) -> str:
    """When this comment's body was last rewritten, or "" if never.

    One spelling for the helpers below, so the key they record and the key
    they compare against cannot come to disagree about which field dates a
    comment.
    """
    return comment.get("last_edited_at", "") or ""


def is_seen(comment: dict, prior: dict[int, str]) -> bool:
    """Whether the last triage read *this* text of *comment*.

    Seen means this text was read, not that this comment id was: an edit keeps
    the id, so matching on id alone treats a rewritten comment as handled. A
    reviewer who edits a comment to add a demand is asking for something, and
    that is the case an id-only record loses.

    A comment absent from `prior` is unseen, which is also how a state file
    written before the stamps existed reads. That direction is deliberate: a
    false unseen re-reports something once, a false seen drops it for good.
    """
    cid = comment.get("id")
    return cid is not None and cid in prior and prior[cid] == edit_stamp(comment)


def mark_seen(comments: list[dict], prior: dict[int, str]) -> None:
    """Flag each comment the last round already read, in place.

    `triage.collect_unseen_comments` drops a comment flagged seen before an
    agent ever reads it; see `is_seen` for what counts.
    """
    for c in comments:
        c["seen"] = is_seen(c, prior)


def seen_record(comments: list[dict]) -> dict[int, str]:
    """What this round read, for the next round to compare against.

    A comment with no id is left out rather than recorded under one. Both
    builders take the id from `databaseId`, which GraphQL can omit, and the key
    type here is `int`: a `None` key serialises to the JSON string `"null"`,
    which `serde` then refuses to coerce back on load, so `load_state`
    discards the whole file as unreadable. Under the former `list[int]` shape
    the bad entry cost one dropped field; under this one it would cost every
    verdict, round and outcome the PR had accumulated.
    """
    return {c["id"]: edit_stamp(c) for c in comments if c.get("id") is not None}


def _plural(count: int, noun: str, plural: str) -> str:
    return f"{count} {noun if count == 1 else plural}"


@dataclass(frozen=True)
class OwedFeedback:
    """How much of each kind of feedback is still owed an answer."""

    threads: int = 0
    review_bodies: int = 0
    issue_comments: int = 0

    @property
    def owed(self) -> bool:
        return bool(self.threads or self.review_bodies or self.issue_comments)

    def describe(self) -> str:
        """Name what is owed — '1 unresolved thread, 2 unanswered review bodies' — or ""."""
        parts = []
        if self.threads:
            parts.append(_plural(self.threads, "unresolved thread", "unresolved threads"))
        if self.review_bodies:
            parts.append(_plural(self.review_bodies, "unanswered review body",
                                 "unanswered review bodies"))
        if self.issue_comments:
            parts.append(_plural(self.issue_comments, "unanswered PR comment",
                                 "unanswered PR comments"))
        return ", ".join(parts)


def owed_feedback(pr_data: PRData, settled: set[str],
                  seen: "CommentsSummary | None") -> OwedFeedback:
    """What `pr comments` would find to answer on the PR *pr_data* describes.

    *settled* is the thread ids a recorded fix outcome already settled, and
    *seen* the PR's saved comments summary — None when `pr comments` has never
    run, which leaves every top-level comment unseen. Reads only `pr_data`'s
    review threads (``id``, ``isResolved``), reviews and issue comments.
    """
    seen_bodies = seen.seen_review_body_comments if seen is not None else {}
    seen_comments = seen.seen_issue_comments if seen is not None else {}
    me = pr_data.viewer_login
    return OwedFeedback(
        threads=sum(1 for t in pr_data.review_threads
                    if not t.get("isResolved") and t.get("id") not in settled),
        review_bodies=sum(1 for c in pr_data.review_body_comments(me)
                          if not is_seen(c, seen_bodies)),
        issue_comments=sum(1 for c in pr_data.non_self_issue_comments(me)
                           if not is_seen(c, seen_comments)),
    )
