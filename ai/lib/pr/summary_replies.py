"""Replies to review bodies, as the blocks the summary comment publishes.

GitHub has no reply API for a review body, and the summary comment is already
the surface reporting what became of the items cut out of one, so the answer
an operator gives with `pr comments --reply rb-<reviewId>` is published there:
one marked block per review, in a `### Replies to review comments` section
under the table. The marker format is `pr.published_record`'s.

Which blocks a round writes is decided the way rows are — from the markers on
the published comments, through `summary_rounds.RoundScope.covers_reply` — and
never from a local "published in" record, because local state cannot know what
a reader has seen. A block on the comment being edited that this round must not
write is kept verbatim: a person edited it, or another checkout wrote it.

What is not here: the marker itself (`pr.published_record`), the scope
arithmetic (`pr.summary_rounds`), and where the section sits in the body
(`pr.summary_render`).
"""

# doc-group: publishing

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

import pr.permalinks
import pr.published_record
import pr.summary_model
import pr.summary_rounds
from pr.thread_models import CommentSourceKind

REPLIES_HEADING = "### Replies to review comments"

# The reviewer a published block names, read back for a log line about it.
_BLOCK_REVIEWER_RE = re.compile(r"\*\*@([^*\s]+)\*\*")


def reply_block_keys(content: pr.summary_model.RoundContent) -> frozenset[str]:
    """The review ids this round has a reply block for.

    The one owner of the set. The render, `RoundScope.covers_reply` through it,
    and the carry-forward rule all ask this rather than reading
    `source_replies` themselves, so a source that gains a block for another
    reason is added here once and reaches all three.
    """
    ids = (pr.permalinks.review_id_of(key) for key in content.source_replies)
    return frozenset(review_id for review_id in ids if review_id)


def _reviewer(content: pr.summary_model.RoundContent, review_id: str) -> str:
    """Who wrote review `review_id`, or "" when this round cannot say.

    The review listing answers for a review the fetch reached. One outside its
    window is answered by an item triage cut from it, which carries the login
    `FixSummary.reviewers` recorded.
    """
    for comment in content.review_body_comments:
        if str(comment.get("id", "")) == review_id and comment.get("user"):
            return str(comment["user"])
    entries = (e for bucket in content.by_outcome.values() for e in bucket)
    for entry in entries:
        source = pr.permalinks.comment_item_source(entry)
        if (source.type is CommentSourceKind.REVIEW_BODY and source.id == review_id
                and entry.reviewer):
            return entry.reviewer
    return ""


def reply_block_text(
    content: pr.summary_model.RoundContent, review_id: str,
    repo: str, pr_number: int, host: str = "",
) -> str:
    """The text inside review `review_id`'s block: who and where, then the reply.

    The digest the marker declares, and the one `covers_reply` compares, is of
    exactly this text.
    """
    source = pr.permalinks.CommentSource(CommentSourceKind.REVIEW_BODY, review_id)
    reviewer = _reviewer(content, review_id)
    header = " · ".join(part for part in (
        f"**@{reviewer}**" if reviewer else "",
        f"[review]({source.permalink(repo, pr_number, host)})",
    ) if part)
    reply = content.source_replies.get(pr.permalinks.review_source_key(review_id), "")
    return pr.published_record.normalise(f"{header}\n\n{reply}")


def render_reply_section(
    content: pr.summary_model.RoundContent,
    scope: pr.summary_rounds.RoundScope,
    kept: Mapping[str, str],
    repo: str, pr_number: int, host: str = "",
) -> list[str]:
    """The `### Replies to review comments` section, or nothing when it is empty.

    Holds the blocks `scope` says this round writes, and the ``kept`` blocks —
    keyed by review id, already rendered — re-emitted verbatim in place of any
    fresh one for the same review. Ordered by when the review was submitted; a
    review this round cannot date sorts after the dated ones, in the order the
    blocks were gathered.
    """
    blocks: dict[str, str] = {}
    for review_id in sorted(reply_block_keys(content) - set(kept), key=int):
        text = reply_block_text(content, review_id, repo, pr_number, host)
        if scope.covers_reply(review_id, pr.published_record.fingerprint(text)):
            blocks[review_id] = pr.published_record.mark_reply_block(review_id, text)
    blocks.update(kept)
    if not blocks:
        return []
    submitted = {
        str(c.get("id", "")): str(c.get("submitted_at") or "")
        for c in content.review_body_comments
    }
    ordered = sorted(blocks, key=lambda key: (0, submitted[key]) if submitted.get(key)
                     else (1, ""))
    parts = [REPLIES_HEADING, ""]
    for key in ordered:
        parts.extend([blocks[key], ""])
    return parts


@dataclass(frozen=True)
class KeptReplies:
    """The blocks on the comment being edited that this round re-emits verbatim.

    ``held`` were edited by a person; ``carried`` answer a review this
    checkout holds no reply for. Both are lost if an in-place edit drops them.
    """

    held: tuple[pr.published_record.PublishedReply, ...] = ()
    carried: tuple[pr.published_record.PublishedReply, ...] = ()

    @property
    def blocks(self) -> dict[str, str]:
        """Every kept block by review id, ready for `render_reply_section`."""
        return {block.key: block.block for block in (*self.held, *self.carried)}


def kept_replies(
    target_body: str, answered: bool, reply_keys: frozenset[str],
    elsewhere: frozenset[str], force: frozenset[str] = frozenset(),
) -> KeptReplies:
    """Which of the target's reply blocks an in-place edit must keep as published.

    Nothing on a fresh post, which replaces nothing. On an edit: a block whose
    text no longer matches its digest is a person's and is held, unless the
    operator's own `--reply` for that review is in ``force`` — answering it again
    is how they take it back, as a `--reply` replaces a hand-written thread
    reply. A block for a review outside ``reply_keys`` (`reply_block_keys`) was
    written by a checkout whose state this one lacks and is carried, unless
    another summary comment also holds it (``elsewhere``): the footer chain
    reaches it there.
    """
    if answered:
        return KeptReplies()
    blocks = {b.key: b for b in pr.published_record.published_reply_blocks(target_body)}
    held = tuple(b for b in blocks.values() if not b.intact and b.key not in force)
    held_keys = {b.key for b in held}
    carried = tuple(
        b for b in blocks.values()
        if b.key not in held_keys and b.key not in reply_keys and b.key not in elsewhere
    )
    return KeptReplies(held, carried)


def block_label(block: pr.published_record.PublishedReply) -> str:
    """A published block named for a log line: the reviewer, else the review id."""
    match = _BLOCK_REVIEWER_RE.search(block.block)
    return f"@{match.group(1)}'s review" if match else f"review {block.key}"
