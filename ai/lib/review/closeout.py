"""What `--finish` owes the PR after the fix pass has run.

The fix pass stops at the point a person has to read something. It holds the
push while a thread is still being discussed, queues the replies it drafted but
could not send, and defers the summary until the needs-human threads have been
answered — so by the time it returns, four separate things may be owed to a PR
that looks, from the outside, finished. This is the phase that pays them.

Order is the whole design here, and it is not incidental. The push goes first,
because every surface below cites a commit and a reviewer cannot follow a SHA
that is not on the remote. The replies go before the summary, because the
summary reports what the replies say. The tracking issue goes before the
summary too, for a subtler reason named on `deferred_issue.finalize_deferred`:
the summary renders the issue link out of state that call writes. Reordering
any of those drops a link or publishes a claim about a commit nobody can see.

Layer 6, not 4, and the reason is worth stating because it was got wrong once:
`finish_deferred_work` calls into `review.deferred_issue`, which reaches
`review.issue` for the tracking issue. A `pr/closeout.py` at layer 4 cannot
import either, and the validator would have said so — but only after the code
was written.
"""

# doc-group: publishing

from __future__ import annotations

from pathlib import Path

from core import log
from core import publishing
from core.trail import Trail
from git import client as git_client
from git import land
from git import push
from git.land import CommitStatus
from pr import attribution
from pr import comments as pc
from pr import context as pr_context
from pr import history_rewrite
from pr import settlement
from pr import state as pr_state
from pr import summary_publish
from pr import thread_replies
from pr.comments_state import ThreadState
from pr.fix import FixOutcome
from pr.thread_models import CommentItem, PRReport, ReportThread
from review import deferred_issue


def warn_if_snapshot_stale(state: pr_state.PRState, wt_path: Path) -> None:
    """Say so when the snapshot describes a tree that is no longer checked out.

    A warning rather than a refusal: reconciliation below re-derives every
    deferral from GitHub, and refusing outright would break the ordinary case
    of committing and then closing out. A snapshot with no recorded HEAD
    predates the field and cannot be vouched for either way, so it counts.
    """
    if not state.fix.fix.items:
        return
    current = git_client.head_sha(short=True, cwd=wt_path)
    if state.fix.fix.head_sha and state.fix.fix.head_sha == current:
        return
    log.warn(
        f"Fix snapshot was taken at {state.fix.fix.head_sha or '(unrecorded)'} "
        f"but HEAD is {current or '(unknown)'} — reconciling against GitHub"
    )


def finish_deferred_work(
    ctx: pr_context.ResolvedContext,
    report: PRReport,
    trail: Trail | None = None,
    *,
    track=frozenset(),
) -> None:
    """Close out what the fix pass held back: replies, tracking issue, summary.

    A phase of its own rather than a tail of `--fix`, because the summary is
    withheld until the needs_human threads have been discussed — which by
    definition has not happened while the fix pass is still running.

    Reads state from disk rather than taking the caller's copy: the fix pass
    writes its outcomes there, so a copy read before it ran would be missing
    exactly the threads this phase exists to close out.
    """
    wt_path = ctx.require_worktree()
    if not ctx.pr_number:
        return
    state = pr_state.load_state(ctx.target_dir)
    if state is None:
        return
    threads_by_id = {t.id: t for t in report.threads}
    history_rewrite.follow_history_rewrite(state, wt_path)
    push_held_commit(state, wt_path, trail)
    if state.fix.pr_body_pending:
        state.fix.pr_body_pending = pc.deliver_pr_body(
            pc.artifacts_dir(ctx.target_dir), ctx.repo, ctx.pr_number,
        )
    post_pending_fix_replies(state, ctx.repo, ctx.pr_number, threads_by_id)
    warn_if_snapshot_stale(state, wt_path)
    flipped = settlement.reconcile_fix_snapshot(state, threads_by_id, settlement.answered_comment_sources(
        state.fix.fix.items, ctx.repo, ctx.pr_number, report.my_login,
    ))
    # After reconciliation, not before: a row this writes is already settled, so
    # reconciling over it would re-examine a thread nothing owes and make the
    # flip count report work it did not do. Before the summary, because these
    # rows are exactly what that render is being re-armed for.
    adopted = settlement.adopt_settled_threads(state, threads_by_id)
    if flipped or adopted:
        state.fix.summary_deferred = True
        state.fix.updated_at = pr_state.now_iso()
    # Order is load-bearing twice over: `validate_track` inside the first call
    # exits on a typo'd --track before the second prints a list the operator
    # would read as the whole story, and the summary below renders the issue
    # link from the ids the first call writes into `state.fix`.
    deferred_issue.finalize_deferred(state, ctx, threads_by_id, trail=trail, track=track)
    deferred_issue.report_unfiled_deferrals(state, track)
    summary_publish.render_deferred_summary(
        state, report, ctx.repo, ctx.pr_number, threads_by_id,
    )
    pr_state.save_state(ctx.target_dir, state)


def push_held_commit(
    state: pr_state.PRState, wt_path: Path, trail: Trail | None = None,
) -> None:
    """Send the commit the fix pass kept local, now that a run says publish.

    The fix pass holds the push whenever a thread is still awaiting discussion,
    so `--fix --post` alone can never land those fixes on the remote. Clearing
    that takes a second, deliberate `--finish --post` — a human having read the
    open thread and said go. Everything downstream in --finish cites the commit,
    so this runs first: the replies and the summary are only allowed out once
    the SHA they link to is somewhere the reviewer can follow it.
    """
    record = state.fix.fix
    if not record.commit_sha or not attribution.commit_unpushed(record.commit_status):
        return

    if push.holds(wt_path, record.commit_sha):
        record.commit_status = CommitStatus.PUSHED
        return

    # No SHA is handed down: the owner reads full HEAD for itself, and
    # `ls-remote` answers in full SHAs, so comparing one against the abbreviation
    # this state file carries reports every push as lost. `report` names the
    # force-push remedy for a divergence, so nothing is added on top of it.
    result = push.push(wt_path, gated=True, trail=trail)
    push.report(result, wt_path)
    if result.status is push.PushStatus.HELD:
        return
    if not result.ok:
        record.commit_status = land.commit_status(result.status)
        return

    log.info(f"Pushed held fixes ({record.commit_sha})")
    record.commit_status = CommitStatus.PUSHED


def post_pending_fix_replies(
    state: pr_state.PRState,
    repo: str,
    pr_number: int,
    threads_by_id: dict[str, ReportThread],
) -> None:
    """Send the replies the fix pass produced but did not deliver.

    Four ways a reply ends up here: the push failed, the push was held because
    discussion was open, the run was a draft, or the pass had nothing to commit
    because the operator landed the same fixes by hand first. Called during
    --finish, which is the second chance for all four.

    All three reply buckets are drained, not just the fixed one. The
    already-addressed and dismissed replies are sent eagerly during triage,
    before any fix work runs, so a drafted pass renders them and then drops
    them — and the triage-only pass, where nothing was fixable, is exactly the
    shape that leaves no fixed entry to carry them back.

    Whatever it resolves it also records against the persisted thread tally,
    which was written from a snapshot taken before this phase ran.
    """
    fix = state.fix
    record = fix.fix
    if not attribution.commit_unpushed(record.commit_status) and not fix.replies_pending:
        return

    wt_path = Path(state.identity.worktree_root) if state.identity.worktree_root else None
    if not wt_path:
        return

    # Only a pass that made its own commit has to wait for it. One that
    # committed nothing leaves entries citing the commits that did — already on
    # the remote — and the triage buckets cite HEAD rather than a fix commit,
    # so there is nothing for either to wait on.
    if record.commit_sha and not push.holds(wt_path, record.commit_sha):
        log.info("Push still pending — skipping deferred replies")
        return

    resolved: list[ThreadState] = []

    def bucket(outcome: FixOutcome) -> list[CommentItem]:
        # The reason lands in `reasoning`, which is what the reply templates
        # read: a drained dismissal that dropped it tells a reviewer their
        # premise fails and gives them nothing to argue with. The commit and
        # read SHAs travel too — this queue spans rounds, and an entry fixed two
        # commits ago must still cite the commit that fixed it.
        return [
            CommentItem.from_outcome(
                o, fix.reviewers.get(o.id, ""), reason_field="reasoning",
            )
            for o in record.items if o.outcome == outcome
        ]

    # Accumulate rather than discard: these are the same round's replies
    # finally going out, and the fix pass already counted any it sent
    # itself. A draft returns 0 here, so a drafted drain adds nothing.
    fixed = bucket(FixOutcome.FIXED)
    if fixed:
        # The same reconciliation the summary rows get, read through the same
        # resolver. A pass whose commit a hook rejected records no SHA of its
        # own, so an operator who then lands those fixes by hand would
        # otherwise be replied to with "Fixed in ``" over an empty link.
        fix.replies_posted += thread_replies.reply_to_fixed(
            fixed, threads_by_id, repo, pr_number,
            history_rewrite.reconciled_commit(record, record.commit_status, wt_path), wt_path,
        )
        resolved += settlement.resolve_fixed_threads(fixed, threads_by_id)

    addressed = bucket(FixOutcome.ALREADY_ADDRESSED)
    if addressed:
        fix.replies_posted += thread_replies.post_already_addressed_replies(
            addressed, threads_by_id, repo, pr_number, wt_path,
        )
        resolved += settlement.resolve_fixed_threads(addressed, threads_by_id)

    # Not resolved, unlike the other two: telling a reviewer their premise does
    # not hold is the reply most likely to be argued with, so the thread stays
    # open for them to answer. Mirrors the triage phase.
    dismissed = bucket(FixOutcome.DISMISSED)
    if dismissed:
        fix.replies_posted += thread_replies.post_dismissed_replies(
            dismissed, threads_by_id, repo, pr_number, wt_path,
        )

    state.comments.move_to_resolved(resolved, updated_at=pr_state.now_iso())

    # A draft delivered nothing, so the queue has to survive for the next run.
    if publishing.enabled():
        if attribution.commit_unpushed(record.commit_status):
            record.commit_status = CommitStatus.PUSHED
        fix.replies_pending = False
