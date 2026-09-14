"""The replies a comment fix round owes, on both sides of the agent.

Two moments, one subject. Triage's verdicts are answers that do not wait on any
fix — the code already does what the reviewer asked, or their premise does not
hold — so they go out before the agent runs. The fixed threads are answered
after, once the commit is on the remote.

Both are gated on the same question and it belongs to them rather than to their
caller: a reply asserts to a reviewer that something is true of the branch, and
`publishing` decides whether this run is allowed to assert anything. What is
left over is `replies_drafted`, which is how a round that rendered replies it
could not send tells `--finish` they are still owed.

Resolving is paired with replying here because the two are one decision. An
already-addressed or fixed thread is closed as it is answered; a dismissed one
is answered and left open, because telling a reviewer their premise does not
hold is the reply most likely to be argued with.
"""

# doc-group: pipeline

from __future__ import annotations

from pathlib import Path

from core import publishing
from git.land import CommitStatus
from pr import attribution
from pr import context as pr_context
from pr import settlement
from pr import thread_replies
from pr import triage_round
from pr.thread_models import (
    CommentItem, PRReport, ReplyOutcome, ReportThread,
)


def settle_fixed(
    fixed: list[CommentItem],
    threads_by_id: dict[str, ReportThread],
    repo: str,
    pr_number: int,
    cp: attribution.CommitPushResult,
    wt_path: Path,
) -> ReplyOutcome:
    """Tell the reviewers what landed, and close the threads it landed on.

    The two acts are one decision and the gate is theirs rather than the
    caller's: both assert to a reviewer that the fix is on the branch, which is
    only true once the commit is on the remote. A pass that committed locally
    and could not push has fixed the code and told nobody, which is the outcome
    the gate exists to produce — the replies and the resolutions wait for
    `--finish --post`.

    Nothing fixed, or a commit that did not reach the remote, is an empty
    outcome rather than a skipped call, so the caller adds it either way.
    """
    if not fixed or cp.status != CommitStatus.PUSHED:
        return ReplyOutcome()
    return ReplyOutcome(
        posted=thread_replies.reply_to_fixed(
            fixed, threads_by_id, repo, pr_number, cp, wt_path,
        ),
        resolved=tuple(settlement.resolve_fixed_threads(fixed, threads_by_id)),
    )


def replies_drafted(
    already_addressed: list[CommentItem], dismissed: list[CommentItem],
) -> bool:
    """Whether this pass rendered triage replies it did not send.

    The already-addressed and dismissed replies go out during triage, before
    the pass knows whether anything is fixable. A draft renders them to stderr
    and sends nothing, so the queue has to say they are still owed — otherwise
    `--finish --post` finds a drained queue and publishes nothing, and a whole
    approved round goes missing.
    """
    return bool(already_addressed or dismissed) and not publishing.enabled()


def post_triage_replies(
    round_: triage_round.TriagedRound,
    threads_by_id: dict[str, ReportThread],
    report: PRReport,
    ctx: pr_context.ResolvedContext,
    wt_path: Path,
) -> ReplyOutcome:
    """The replies triage owes, sent before the agent runs.

    A dismissal and an already-addressed verdict are both answers to the
    reviewer that do not wait on any fix — the code either already does what
    they asked or their premise does not hold — so they go out now rather than
    after a pass that may have nothing to do.

    Only the thread side of the dismissals is replied to. An entry decomposed
    out of a top-level comment has no thread to reply on; it is reported in the
    summary table instead.

    The already-addressed threads are resolved as well as replied to, and the
    dismissed are not: telling a reviewer their premise does not hold is the
    reply most likely to be argued with, so the thread stays open for them to
    answer.
    """
    replies = ReplyOutcome()
    if round_.threads.dismissed:
        replies = replies.plus(ReplyOutcome(
            posted=thread_replies.post_dismissed_replies(
                round_.threads.dismissed, threads_by_id, ctx.repo,
                report.pr_number, wt_path,
            ),
        ))
    if round_.already_addressed:
        replies = replies.plus(ReplyOutcome(
            posted=thread_replies.post_already_addressed_replies(
                round_.already_addressed, threads_by_id, ctx.repo,
                report.pr_number, wt_path,
            ),
            resolved=tuple(settlement.resolve_fixed_threads(
                round_.already_addressed, threads_by_id)),
        ))
    return replies
