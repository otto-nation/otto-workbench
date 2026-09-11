"""The summary comment's body: the table, its notes, and the sections under it.

One round of the fix pass, rendered as the comment a reviewer reads. The table
is built row by row from the round's buckets, and the counts above it are
derived from the rows that actually reached it rather than from the buckets —
a row left to an earlier comment is not counted here, or the header would
describe a table the reader cannot see.

What is not here: how one row's cells are built (`pr.summary_row`), what a row's
identity is (`summary_model.row_key_from_cells`), which rows this round may omit
(`pr.summary_rounds`), and how the finished body reaches GitHub
(`pr.summary_publish`).

The counts vocabulary is `pr.comments_fix`'s — `count_line` prints the same line
on the state dashboard, and a reword in one surface must not change the other.
"""

# doc-group: publishing

from __future__ import annotations

from pathlib import Path

from core import text
from pr import attribution
from pr import comments_fix as pr_comments_fix
from pr import summary_model
from pr import summary_rounds
from pr import summary_row
from pr.fix import FixOutcome
from pr.thread_models import CommentItem, ReportThread

SUMMARY_MARKER = "<!-- pr-comments:summary -->"

def summarize_comment_body(body: str, max_len: int = 120) -> str:
    """Extract first meaningful line from a comment body, truncated."""
    in_html_comment = False
    for raw_line in body.splitlines():
        stripped = raw_line.strip()
        if in_html_comment:
            in_html_comment = "-->" not in stripped
            continue
        if stripped.startswith("<!--"):
            in_html_comment = "-->" not in stripped
            continue
        line = stripped.lstrip("#").strip()
        if not line:
            continue
        if len(line) > max_len:
            return line[:max_len - 1] + "…"
        return line
    return "(empty)"


def build_summary_body(
    content: summary_model.RoundContent,
    cp: attribution.CommitPushResult,
    repo: str,
    pr_number: int,
    threads_by_id: dict[str, ReportThread],
    deferred_issue_id: str = "",
    deferred_issue_url: str = "",
    has_comment_items: bool = False,
    head_sha: str = "",
    carried_over: list[str] | None = None,
    hand_held: list[summary_model.HeldRow] | None = None,
    wt_path: Path | None = None,
    history: attribution.AddressingHistory | None = None,
    scope: summary_rounds.RoundScope | None = None,
    chain: list[summary_rounds.SummaryRound] | None = None,
) -> str:
    """Build the markdown body for the fix summary comment.

    ``carried_over`` holds rows lifted verbatim from the published comment that
    this render cannot account for — see `summary_scope.carried_over_rows`. They are already
    rendered, so they are re-emitted as-is rather than round-tripped through an
    entry the state file does not have.

    ``hand_held`` holds rows this render *can* account for and still must not
    write: the Action cell was rewritten by hand — see `summary_scope.hand_written_rows`.
    Each one is re-emitted in the position its entry would have taken, and the
    entry behind it is dropped from the header counts, because a row reading
    ``Superseded`` under a header reading ``1 need discussion`` reopens the
    question the hand edit closed.

    ``wt_path`` is what lets a satisfied row say who satisfied it: the branch
    is the only place the answer lives. Without it every such row falls back to
    the pre-existing reading, which is what a render with no tree to read can
    honestly claim.

    ``history`` is that reading, and a caller that has already taken it should
    pass it rather than let one be built here. It is a memo over `git log -L`,
    which costs a process per location, and the caller with one in hand is the
    one that just asked the same question of the same rows — the warning ahead
    of this render. Passing it cannot change an answer, only avoid paying for
    it twice.

    ``scope`` is which of the entries this body may leave to an earlier summary
    comment — see `summary_rounds.RoundScope`, whose default covers everything. ``chain`` is
    the footer that makes leaving them out safe to read: every summary comment
    already on the PR, linked in order.
    """
    carried_over = carried_over or []
    scope = scope or summary_rounds.RoundScope()
    chain = chain or []
    issue_comments = content.issue_comments
    review_body_comments = content.review_body_comments
    sources_at = summary_rounds.comment_timestamps(issue_comments, review_body_comments)
    held_by_key = {row.key: row.published for row in hand_held or []}
    # Ahead of the counts, not just the rows: a count with no row under it is a
    # claim the table cannot show the reader.
    folded = summary_model.folded_item_ids(content, threads_by_id)

    def unfolded(*outcomes: FixOutcome) -> list[CommentItem]:
        return [e for e in content.of(*outcomes) if e.id not in folded]

    fixed = unfolded(FixOutcome.FIXED)
    already_addressed = unfolded(FixOutcome.ALREADY_ADDRESSED)
    dismissed = unfolded(FixOutcome.DISMISSED)
    settled_elsewhere = unfolded(FixOutcome.SETTLED_ELSEWHERE)
    needs_human = [e for e in content.needs_a_person if e.id not in folded]
    deferred = unfolded(FixOutcome.DEFERRED)

    rows: list[str] = []
    held: list[str] = []
    # Row keys this render left to an earlier summary comment, not entries — the
    # two lists below feed the notes under the table, and are unrelated to the
    # SETTLED_ELSEWHERE bucket above.
    settled_earlier: list[str] = []
    open_earlier: list[str] = []
    # The fix commit is the tree the table describes; fall back to the reviewed
    # head when nothing was committed.
    link_sha = cp.sha or head_sha
    if deferred_issue_id and deferred_issue_url:
        deferred_status = f"Deferred → [{deferred_issue_id}]({deferred_issue_url})"
    elif deferred_issue_id:
        deferred_status = f"Deferred → {deferred_issue_id}"
    else:
        deferred_status = "Deferred"

    def emit(
        entry: CommentItem, status: str, sha: str, *, open_thread: bool = False,
    ) -> bool:
        """Append this entry's row, or the published one a human rewrote.

        Returns whether the entry's own classification reached the table, which
        is what the header counts count — the counts are derived from the rows
        rather than from the buckets so the two cannot disagree.

        A row an earlier summary comment already carries is left to that comment
        rather than restated — see `summary_rounds.RoundScope`. ``open_thread`` does not exempt
        a row from that test: an open question is as hard to find as one row
        among forty-three as it is one round back, and exempting the bucket is
        what turned the newest summary into the whole history again. It says
        which count reports the row when it is left out, because "settled" is
        the wrong word for a question still owed an answer.
        """
        cells = summary_row.row_cells_for(
            entry, status, threads_by_id, repo, pr_number, sha, wt_path)
        row = summary_row.render_row(cells)
        key = summary_model.row_key_from_cells(cells)
        if not scope.covers(
            key, summary_rounds.entry_activity_at(entry, threads_by_id, sources_at),
            summary_model.action_outcome(status),
        ):
            (open_earlier if open_thread else settled_earlier).append(key)
            return False
        published = held_by_key.get(key)
        if published is None:
            rows.append(row)
            return True
        rows.append(published)
        held.append(published)
        return False

    # Resolved once for the whole table: the count and the row it belongs to
    # are two renderings of one answer, and a count that disagreed with its own
    # rows would be the same contradiction in a smaller font.
    history = history or attribution.AddressingHistory(wt_path)
    addressed_framings = [
        history.framing(e, threads_by_id.get(e.id)) for e in already_addressed
    ]

    # Built as lists, not generators, so the row-emitting side effect always
    # runs to completion before the count is taken — a later swap to a
    # short-circuiting aggregate over these lists cannot skip an entry.
    fixed_count = sum([
        emit(
            e,
            summary_row.fixed_status_for(e, cp, repo, history, threads_by_id.get(e.id)),
            getattr(e, "commit_sha", "") or link_sha,
        )
        for e in fixed
    ])
    # link_sha, not the addressing commit: the file cell's line number was read
    # at HEAD, and the commit answers "when", not "where".
    addressed_shown = [
        (framing, emit(e, summary_row.addressed_status_for(framing, repo), link_sha))
        for e, framing in zip(already_addressed, addressed_framings, strict=True)
    ]
    # A satisfied row a commit made true after the review is a fix, and counts
    # as one. Only the rows whose code predates the comment are "already".
    fixed_count += sum(1 for f, shown in addressed_shown if shown and f.in_response)
    addressed_count = sum(
        1 for f, shown in addressed_shown if shown and not f.in_response
    )
    dismissed_count = sum([emit(e, "Dismissed (invalid)", link_sha) for e in dismissed])
    # Its own count, kept out of the fixed one. The row reports that the thread
    # is no longer owed, which is all its evidence supports: GitHub's resolve
    # button covers a reviewer who was answered or who withdrew the point as
    # readily as one whose fix landed.
    settled_count = sum([
        emit(e, pr_comments_fix.RECONCILED_STATUS_TEXT, link_sha) for e in settled_elsewhere
    ])
    human_count = sum([
        emit(e, summary_model.HumanReason.prose_for(e.reason), link_sha, open_thread=True)
        for e in needs_human
    ])
    deferred_count = sum([emit(e, deferred_status, link_sha) for e in deferred])
    rows.extend(carried_over)
    held_count = len(held)
    carried_count = len(carried_over)

    parts = [SUMMARY_MARKER, "## Review Comments Addressed", ""]
    # The verdict wordings and their separator belong to `pr.comments_fix`,
    # which prints the same line on the state dashboard. Two of the counts
    # below name no verdict — a row a human rewrote and a row carried over
    # from a round this checkout cannot account for — so they are passed as
    # extras rather than invented as outcomes no fix pass can produce.
    extras = []
    if held_count:
        extras.append(f"{held_count} hand-written")
    if carried_count:
        extras.append(f"{carried_count} carried over")
    parts.append(pr_comments_fix.count_line({
        FixOutcome.FIXED: fixed_count,
        FixOutcome.ALREADY_ADDRESSED: addressed_count,
        FixOutcome.DISMISSED: dismissed_count,
        FixOutcome.SETTLED_ELSEWHERE: settled_count,
        FixOutcome.DEFERRED: deferred_count,
        FixOutcome.NEEDS_HUMAN: human_count,
    }, extras))
    parts.append("")

    if rows:
        parts.append(summary_model.TABLE_HEADER)
        parts.append(summary_model.TABLE_DIVIDER)
        parts.extend(rows)
        parts.append("")

    parts.extend(_table_note(
        held_count, "row", "kept as published — the Action cell was written by "
        "hand, so this round did not re-render it."))
    parts.extend(_table_note(
        carried_count, "row", "carried over from an earlier round that this "
        "checkout's state file does not cover."))
    parts.extend(_table_note(
        len(open_earlier), "thread", "still open and awaiting an answer — raised "
        "in an earlier round and quiet since, left as published there rather than "
        "restated here."))
    parts.extend(_table_note(
        len(settled_earlier), "thread", "settled in an earlier round and quiet "
        "since — left as published there rather than restated here."))

    if not has_comment_items:
        parts.extend(_render_raw_comment_sections(review_body_comments, issue_comments))

    parts.extend(_render_round_chain(chain))

    return "\n".join(parts)


def _table_note(count: int, noun: str, rest: str) -> list[str]:
    """A blockquote under the table for `count` rows, or nothing when it is zero.

    Every note the table carries reports rows the render left out of it, so they
    are one shape: a count, what was counted, and what became of them. The
    trailing blank keeps the notes separate paragraphs when several appear.
    """
    if not count:
        return []
    return [f"> {count} {noun}{text.plural(count)} {rest}", ""]


def _render_round_chain(chain: list[summary_rounds.SummaryRound]) -> list[str]:
    """The footer linking every earlier summary comment on the PR, oldest first.

    What makes a scoped summary safe to read: the round it describes is the only
    one it holds, so the reader needs somewhere to go for the rest. Only the
    newest comment ever gains links — an edit notifies nobody, so rewriting the
    older comments to point forward would cost a request each and be seen by
    no one.
    """
    if not chain:
        return []
    links = " · ".join(f"[{r.number}]({r.url})" for r in chain)
    return ["---", "", f"**Earlier rounds:** {links}", ""]


def _render_raw_comment_sections(
    review_body_comments: list[dict],
    issue_comments: list[dict],
) -> list[str]:
    """Render unseen comments as summary sections (fallback when items weren't decomposed).

    Each kind gets its own section, so the two lists are filtered apart here
    rather than read off `summary_model.RoundContent.unseen`. What must not drift is the
    predicate, not the partition: a comment this prints is one that made
    `has_content` true, and a round whose only content was an unread comment
    would otherwise publish a table with nothing under it.
    """
    parts: list[str] = []
    unseen_reviews = summary_model.unseen_comments(review_body_comments)
    if unseen_reviews:
        parts.extend(["### Review-Level Comments", ""])
        parts.extend(_format_review_body_items(unseen_reviews))
        parts.append("")

    unseen_issue = summary_model.unseen_comments(issue_comments)
    if unseen_issue:
        parts.extend(["### Discussion Comments", ""])
        parts.extend(_format_issue_comment_items(unseen_issue))
        parts.append("")

    return parts


def _format_review_body_items(comments: list[dict]) -> list[str]:
    lines = []
    for c in comments:
        user = c.get("user", "unknown")
        state = c.get("state", "")
        summary = summarize_comment_body(c.get("body", ""))
        state_badge = f" ({state})" if state else ""
        lines.append(f"- **@{user}**{state_badge}: {summary}")
    return lines


def _format_issue_comment_items(comments: list[dict]) -> list[str]:
    lines = []
    for c in comments:
        user = c.get("user", "unknown")
        summary = summarize_comment_body(c.get("body", ""))
        lines.append(f"- **@{user}**: {summary}")
    return lines
