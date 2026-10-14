"""Answering a review body from `pr comments --reply`.

A review thread takes a reply where it stands; a review body cannot, because
GitHub has no reply API for one. The answer is stored per review in
`FixSummary.source_replies` and published as a marked block in the summary
comment — see `pr.summary_replies` — by the same re-render `--finish` uses,
`summary_publish.render_deferred_summary`.

`route_reply` decides which kind a `--reply` target is, so the command can send
a thread to `pr.thread_replies.run_reply` unchanged and only a review body here.
A thread wins: the same number can be a comment id inside a thread, and that
reading is the one `--reply` always had.

Layer 6 because the report the summary is rendered from is built in
`review.comment_threads`, which a `pr/` module cannot import.
"""

# doc-group: publishing

from __future__ import annotations

from dataclasses import dataclass

import core.log
import core.publishing
from core.trail import Trail
from gh.pr_data import PRData, fetch_pr_data
import pr.comments
import pr.comments_fix
import pr.context
import pr.permalinks
import pr.published_record
import pr.state
import pr.summary_publish
import pr.thread_replies
import review.comment_threads


@dataclass(frozen=True)
class ReplyRoute:
    """What a `--reply` target names, and the PR read that decided it.

    ``thread`` is a review thread match. ``source`` is a review body's source
    key (`rb-<reviewId>`), set only when no thread matched. Neither set is a
    target that names nothing; ``review_body_ids`` are the reviews the error
    then lists. ``pr_data`` is handed on so the reply does not read the PR again.
    """

    pr_data: PRData
    thread: bool = False
    source: str = ""
    review_body_ids: tuple[str, ...] = ()


def route_reply(ctx: pr.context.ResolvedContext, target: str) -> ReplyRoute:
    """Read the PR once and say whether `target` is a thread or a review body.

    A review body is one the PR lists, or one the fix snapshot holds an item
    from — see `pr.thread_replies.reply_source`.
    """
    owner, repo_name = ctx.repo.split("/", 1)
    pr_data = fetch_pr_data(ctx.repo, str(ctx.pr_number))
    fetched = pr.comments.fetch_threads(owner, repo_name, ctx.pr_number, pr_data)
    if pr.thread_replies.find_reply_target(fetched.threads, target, pr_data.viewer_login):
        return ReplyRoute(pr_data, thread=True)
    ids = tuple(
        str(c["id"]) for c in pr_data.review_body_comments(pr_data.viewer_login)
        if c.get("id") is not None
    )
    state = pr.state.load_state(ctx.target_dir)
    triaged = [item.id for item in state.fix.fix.items] if state else []
    source = pr.thread_replies.reply_source(target, ids, triaged) or ""
    return ReplyRoute(pr_data, source=source, review_body_ids=ids)


def run_review_body_reply(
    ctx: pr.context.ResolvedContext, route: ReplyRoute, body: str,
    trail: Trail | None = None,
) -> int:
    """Store the answer to one review body and publish it in the summary.

    ``body`` is the already-read `--body-file` text — the CLI loads it once so
    stdin `-` is not drained twice.

    With publishing on, the reply is written to the state file and the summary
    re-armed before anything is posted, so a publish that does not happen —
    a push still pending, a post GitHub refused — leaves both in place for the
    next `--finish --post`. That case exits 1 and says so: the operator asked
    for a post and did not get one.

    Without it nothing is stored: the summary body the reply would publish is
    printed through the same draft path every other `pr comments` write uses.
    """
    pr.thread_replies.warn_if_uncited(body)
    review_id = pr.permalinks.review_id_of(route.source)
    report = review.comment_threads.read_report(ctx, route.pr_data)
    threads_by_id = {t.id: t for t in report.threads}
    state = pr.state.load_or_init(
        target_dir=ctx.target_dir, repo=ctx.repo, branch=ctx.branch,
        pr_number=ctx.pr_number, head_sha=ctx.head_sha,
        worktree_root=str(ctx.require_worktree()),
    )
    state.fix.source_replies[route.source] = pr.published_record.normalise(body)
    state.fix.updated_at = pr.state.now_iso()
    # Read before re-arming: a summary already held back for `--finish` must stay
    # owed after this publish, or `--finish` skips the re-render that writes the
    # tracking-issue link into its deferred rows.
    was_owed = state.fix.summary_deferred
    # The summary alone, not `rearm_closeout`: that also owes the thread reply
    # queue, and a review-body answer adds nothing to it. Before the publish,
    # so a publish that does not happen leaves the debt for `--finish`.
    state.fix.summary_deferred = True
    publishing = core.publishing.enabled()
    if publishing:
        pr.state.save_state(ctx.target_dir, state)
    url = pr.summary_publish.render_deferred_summary(
        state, report, ctx.repo, ctx.pr_number, threads_by_id, ctx.host,
        force_reply_keys=frozenset({review_id}),
    )
    if not publishing:
        _explain_draft(state, review_id)
        return 0
    if url:
        # Only the debt this reply created is retired by the post.
        state.fix.summary_deferred = was_owed
    pr.state.save_state(ctx.target_dir, state)
    if url:
        core.log.info(f"Replied to review {review_id} in the summary: {url}")
        if trail:
            trail.info("review_body_reply", f"published reply to review {review_id}",
                       data={"review": review_id, "url": url})
        return 0
    if trail:
        trail.warn("review_body_reply",
                   f"stored reply to review {review_id}; summary not published",
                   data={"review": review_id})
    core.log.error(
        f"Stored the reply to review {review_id}, but the summary was not published — "
        f"`{pr.comments_fix.CLOSEOUT_COMMAND}` publishes it once the cause above is cleared")
    return 1


def _explain_draft(state: pr.state.PRState, review_id: str) -> None:
    """Say what a draft `--reply` did, beside the one headline the post path printed.

    A push still pending renders no summary at all, so there is no draft body
    to show — that case says so, and why, rather than leaving the operator a
    headline with nothing under it.
    """
    if pr.summary_publish.push_still_pending(state):
        core.log.info(
            f"No draft body for the reply to review {review_id}: the fix commit's push "
            f"is still pending, and the summary is rendered only once it has landed — "
            f"`{pr.comments_fix.CLOSEOUT_COMMAND}` publishes it after the push")
        return
    core.log.info(f"Nothing stored — rerun with --post to publish the reply to review {review_id}")
