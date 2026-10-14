"""Replies to review bodies, as the blocks the summary comment publishes.

GitHub has no reply API for a review body, and the summary comment is already
the surface reporting what became of the items cut out of one, so the answer
an operator gives with `pr comments --reply rb-<reviewId>` is published there:
one marked block per review, in a `### Replies to review comments` section
under the table. The marker format is `pr.published_record`'s. A review whose
cut items were dismissed with a reason gets a block too, one
`Dismissed: <finding> — <reason>` line per item under the reply (or alone), so
the argument against a finding reaches the reviewer who raised it.

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
import pr.settlement
import pr.summary_model
import pr.summary_rounds
from pr.fix import FixOutcome
from pr.thread_models import CommentItem, CommentSourceKind

REPLIES_HEADING = "### Replies to review comments"

# The reviewer a published block names, read back for a log line about it.
_BLOCK_REVIEWER_RE = re.compile(r"\*\*@([^*\s]+)\*\*")


def reply_block_keys(content: pr.summary_model.RoundContent) -> frozenset[str]:
    """The review ids this round has a reply block for.

    The one owner of the set. The render, `RoundScope.covers_reply` through it,
    and the carry-forward rule all ask this rather than reading
    `source_replies` themselves, so a source that gains a block for another
    reason is added here once and reaches all three. A review has a block when
    the operator answered it, or when an item cut from it was dismissed with a
    reason (`_dismissals`).
    """
    ids = (pr.permalinks.review_id_of(key) for key in content.source_replies)
    return frozenset(review_id for review_id in ids if review_id) | set(_dismissals(content))


def _item_index(entry: CommentItem) -> int:
    """Where `entry` sits in its review: `index`, else the id's trailing `-<n>`."""
    tail = entry.id.rsplit("-", 1)[-1]
    return entry.index or (int(tail) if tail.isdigit() else 0)


def _dismissals(content: pr.summary_model.RoundContent) -> dict[str, list[CommentItem]]:
    """Each review's dismissed items that carry a reason, in item order.

    Read off `record_items` — the whole record, not this round's buckets — so a
    dismissal an earlier round made keeps its line when a live round re-renders
    the block, and an operator's `--settle --as dismissed --reason` and the fix
    pass's own justification both count: either is an argument the reviewer
    may answer. The reason is the entry's
    `justification` — what the record persists — so a triage dismissal, whose
    argument is in `reasoning`, renders live exactly as its replay will. A
    dismissal with no reason has nothing to say beyond the table's Action cell,
    and adds no line.
    """
    found: dict[str, list[CommentItem]] = {}
    for entry in content.record_items:
        if entry.outcome is not FixOutcome.DISMISSED:
            continue
        source = pr.permalinks.comment_item_source(entry)
        if (source.type is CommentSourceKind.REVIEW_BODY and source.id.isdigit()
                and entry.justification.strip()):
            found.setdefault(source.id, []).append(entry)
    return {key: sorted(items, key=_item_index) for key, items in found.items()}


def _dismissal_line(entry: CommentItem) -> str:
    """`Dismissed: <finding> — <reason>`, the reason folded onto the one line."""
    finding = pr.settlement.finding_line(entry.summary) or entry.id
    return f"Dismissed: {finding} — {' '.join(entry.justification.split())}"


def _reviewer(content: pr.summary_model.RoundContent, review_id: str) -> str:
    """Who wrote review `review_id`, or "" when this round cannot say.

    The review listing answers for a review the fetch reached. One outside its
    window is answered by an item triage cut from it, in any round of the
    record, which carries the login `FixSummary.reviewers` recorded.
    """
    for comment in content.review_body_comments:
        if str(comment.get("id", "")) == review_id and comment.get("user"):
            return str(comment["user"])
    for entry in content.record_items:
        source = pr.permalinks.comment_item_source(entry)
        if (source.type is CommentSourceKind.REVIEW_BODY and source.id == review_id
                and entry.reviewer):
            return entry.reviewer
    return ""


def reply_block_text(
    content: pr.summary_model.RoundContent, review_id: str,
    repo: str, pr_number: int, host: str = "",
) -> str:
    """The text inside review `review_id`'s block: who and where, reply, dismissals.

    One `_dismissal_line` per item `_dismissals` names, under the reply.

    The digest the marker declares, and the one `covers_reply` compares, is of
    exactly this text — the generated header included, by design. So a change
    in how the reviewer resolves (a login found or lost) or in the forge host
    the permalink is built on reads as a revised block, and re-states a reply
    whose text did not change. The dismissal lines are inside it too, so a new
    or changed dismissal revises the block exactly as an edited reply does.
    """
    source = pr.permalinks.CommentSource(CommentSourceKind.REVIEW_BODY, review_id)
    reviewer = _reviewer(content, review_id)
    header = " · ".join(part for part in (
        f"**@{reviewer}**" if reviewer else "",
        f"[review]({source.permalink(repo, pr_number, host)})",
    ) if part)
    reply = content.source_replies.get(pr.permalinks.review_source_key(review_id), "")
    dismissed = "\n".join(map(_dismissal_line, _dismissals(content).get(review_id, [])))
    body = "\n\n".join(part for part in (reply, dismissed) if part)
    return pr.published_record.normalise(f"{header}\n\n{body}")


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
    def sort_key(key: str) -> tuple[int, str]:
        ts = submitted.get(key, "")
        return (0, ts) if ts else (1, "")

    ordered = sorted(blocks, key=sort_key)
    parts = [REPLIES_HEADING, ""]
    for key in ordered:
        parts.extend([blocks[key], ""])
    return parts


@dataclass(frozen=True)
class KeptReplies:
    """The blocks on the comment being edited that this round re-emits verbatim.

    ``held`` were edited by a person; ``carried`` answer a review this
    checkout holds no reply for. Both are lost if an in-place edit drops them.
    ``withdrawn`` are not kept: dismissal-only blocks for a review this
    checkout knows, whose dismissal was re-settled away — named so the drop is
    reported rather than silent.
    """

    held: tuple[pr.published_record.PublishedReply, ...] = ()
    carried: tuple[pr.published_record.PublishedReply, ...] = ()
    withdrawn: tuple[pr.published_record.PublishedReply, ...] = ()

    @property
    def blocks(self) -> dict[str, str]:
        """Every kept block by review id, ready for `render_reply_section`."""
        return {block.key: block.block for block in (*self.held, *self.carried)}


def known_reviews(content: pr.summary_model.RoundContent) -> frozenset[str]:
    """The review ids any item in ``content`` was cut from.

    A review this checkout holds items for is one whose state it has, so a
    dismissal-only block for it that `reply_block_keys` no longer names was
    withdrawn — a dismissal re-settled away — not written by another checkout.
    A block carrying reply text is never read this way: `source_replies` never
    shrinks, so reply text this checkout lacks is another checkout's. Meaningful
    only for a content holding the whole record (`render_deferred_summary`'s).
    """
    entries = (e for bucket in content.by_outcome.values() for e in bucket)
    sources = map(pr.permalinks.comment_item_source, entries)
    return frozenset(source.id for source in sources
                     if source.type is CommentSourceKind.REVIEW_BODY and source.id.isdigit())


def _dismissal_only(block: pr.published_record.PublishedReply) -> bool:
    """Whether `block` holds nothing under its header but `Dismissed:` lines.

    The shape `reply_block_text` gives a review with dismissals and no reply.
    The first line inside the markers is the header; the last is the close.
    """
    inner = block.block.split("\n")[1:-1]
    return all(line.startswith("Dismissed: ") for line in inner[1:] if line.strip())


def kept_replies(
    target_body: str, answered: bool, reply_keys: frozenset[str],
    elsewhere: frozenset[str], force: frozenset[str] = frozenset(),
    known: frozenset[str] = frozenset(),
) -> KeptReplies:
    """Which of the target's reply blocks an in-place edit must keep as published.

    Nothing on a fresh post, which replaces nothing. On an edit: a block whose
    text no longer matches its digest is a person's and is held, unless the
    operator's own `--reply` for that review is in ``force`` — answering it again
    is how they take it back, as a `--reply` replaces a hand-written thread
    reply. A block for a review outside ``reply_keys`` (`reply_block_keys`) was
    written by a checkout whose state this one lacks and is carried, unless
    another summary comment also holds it (``elsewhere``): the footer chain
    reaches it there. A dismissal-only block for a review this checkout holds
    items from (``known``, `known_reviews`) was withdrawn, and is dropped and
    named in ``withdrawn``; one carrying reply text is carried all the same.
    """
    if answered:
        return KeptReplies()
    blocks = {b.key: b for b in pr.published_record.published_reply_blocks(target_body)}
    held = tuple(b for b in blocks.values() if not b.intact and b.key not in force)
    held_keys = {b.key for b in held}
    loose = [b for b in blocks.values()
             if b.key not in held_keys and b.key not in reply_keys | elsewhere]
    withdrawn = tuple(b for b in loose if b.key in known and _dismissal_only(b))
    carried = tuple(b for b in loose if b not in withdrawn)
    return KeptReplies(held, carried, withdrawn)


def block_label(block: pr.published_record.PublishedReply) -> str:
    """A published block named for a log line: the reviewer, else the review id."""
    match = _BLOCK_REVIEWER_RE.search(block.block)
    return f"@{match.group(1)}'s review" if match else f"review {block.key}"
