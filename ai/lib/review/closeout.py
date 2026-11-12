"""What `--finish` owes the PR after the fix pass has run.

The fix pass stops at the point a person has to read something. It holds the
push while a thread is still being discussed, queues the replies it drafted but
could not send, and defers the summary until the needs-human threads have been
answered — so by the time it returns, four separate things may be owed to a PR
that looks, from the outside, finished. This is the phase that pays them.
`--finish` is a second invocation on purpose — the discussion has to happen in
between. Combining `--fix --finish` works and closes out that run's deferred
set only once each handed-back thread has an answer on GitHub; `--finish`
refuses the summary until then.

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

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import core.log
import core.publishing
from core.trail import Trail
import git.client
import git.land
import git.push
from git.land import CommitStatus
import gh.owner_hooks
import gh.owners
import pr.attribution
import pr.comments
import pr.comments_fix
import pr.context
import pr.history_rewrite
import pr.settlement
import pr.state
import pr.summary_publish
import pr.thread_replies
from pr.comments_state import ThreadState
from pr.fix import FixOutcome, ItemOutcome
from pr.thread_models import CommentItem, CommentSourceKind, PRReport, ReportThread
import review.deferred_issue


def warn_if_snapshot_stale(state: pr.state.PRState, wt_path: Path) -> None:
    """Say so when the snapshot describes a tree that is no longer checked out.

    A warning rather than a refusal: reconciliation below re-derives every
    deferral from GitHub, and refusing outright would break the ordinary case
    of committing and then closing out. A snapshot with no recorded HEAD
    predates the field and cannot be vouched for either way, so it counts.
    """
    if not state.fix.fix.items:
        return
    current = git.client.head_sha(short=True, cwd=wt_path)
    if state.fix.fix.head_sha and git.client.same_commit(
            state.fix.fix.head_sha, current, cwd=wt_path):
        return
    core.log.warn(
        f"Fix snapshot was taken at {state.fix.fix.head_sha or '(unrecorded)'} "
        f"but HEAD is {current or '(unknown)'} — reconciling against GitHub"
    )


def finish_deferred_work(
    ctx: pr.context.ResolvedContext,
    report: PRReport,
    trail: Trail | None = None,
    *,
    track=frozenset(),
    no_push_verify: bool = False,
) -> bool:
    """Close out what the fix pass held back: replies, tracking issue, summary.

    False once a `--track` id naming no deferred thread has been reported, or
    the summary was refused because a handed-back thread still has no answer
    on GitHub; the caller turns that into the exit status.

    A phase of its own rather than a tail of `--fix`, because the summary is
    withheld until the needs_human threads have been discussed — which by
    definition has not happened while the fix pass is still running.

    Reads state from disk rather than taking the caller's copy: the fix pass
    writes its outcomes there, so a copy read before it ran would be missing
    exactly the threads this phase exists to close out.
    """
    wt_path = ctx.require_worktree()
    if not ctx.pr_number:
        return True
    state = pr.state.load_state(ctx.target_dir)
    if state is None:
        return True
    threads_by_id = {t.id: t for t in report.threads}
    pr.history_rewrite.follow_history_rewrite(state, wt_path)
    push_held_commit(state, wt_path, trail, no_push_verify=no_push_verify)
    if state.fix.pr_body_pending:
        state.fix.pr_body_pending = pr.comments.deliver_pr_body(
            pr.comments.artifacts_dir(ctx.target_dir), ctx.repo, ctx.pr_number,
        )
    post_pending_fix_replies(state, ctx.repo, ctx.pr_number, threads_by_id, ctx.host)
    warn_if_snapshot_stale(state, wt_path)
    flipped = pr.settlement.reconcile_fix_snapshot(state, threads_by_id, pr.settlement.answered_comment_sources(
        state.fix.fix.items, ctx.repo, ctx.pr_number, report.my_login,
    ))
    # After reconciliation, not before: a row this writes is already settled, so
    # reconciling over it would re-examine a thread nothing owes and make the
    # flip count report work it did not do. Before the summary, because these
    # rows are exactly what that render is being re-armed for.
    adopted = pr.settlement.adopt_settled_threads(state, threads_by_id)
    _warn_open_threads_without_snapshot(state, threads_by_id)
    if flipped or adopted:
        state.fix.summary_deferred = True
        state.fix.updated_at = pr.state.now_iso()
    # Order is load-bearing twice over: `finalize_deferred` refuses a typo'd
    # --track, via `validate_track`, before `report_unfiled_deferrals` prints a
    # list the operator would read as the whole story; and the summary below
    # renders the issue link from the ids `finalize_deferred` writes into
    # `state.fix`.
    if not review.deferred_issue.finalize_deferred(
            state, ctx, threads_by_id, trail=trail, track=track):
        # It refused and wrote nothing, so both of those would describe a run
        # that did not happen. The state above this line is settlement work
        # that did, and is saved on the way out.
        pr.state.save_state(ctx.target_dir, state)
        return False
    review.deferred_issue.report_unfiled_deferrals(state, track)
    _log_draft_snapshot_items(state.fix.fix.items)
    render = pr.summary_publish.render_deferred_summary(
        state, report, ctx.repo, ctx.pr_number, threads_by_id, ctx.host,
        answered_now=review.deferred_issue.filed_this_run(state, track),
    )
    pr.state.save_state(ctx.target_dir, state)
    return not render.refused


def _log_draft_snapshot_items(items: list[ItemOutcome]) -> None:
    """Name each snapshot item on a draft `--finish`, before the summary renders.

    Self-contained so the Phase 3 factor of `finish_deferred_work` can move it
    with the setup it sits in. Publishing is the posted table; without it the
    operator has only this list.
    """
    if core.publishing.enabled():
        return
    for item in items:
        core.log.info(pr.settlement.format_item_line(item))


def push_held_commit(
    state: pr.state.PRState, wt_path: Path, trail: Trail | None = None,
    *,
    no_push_verify: bool = False,
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
    if not record.commit_sha or not pr.attribution.commit_unpushed(record.commit_status):
        return

    if git.push.holds(wt_path, record.commit_sha):
        record.commit_status = CommitStatus.PUSHED
        return

    # No SHA is handed down: the owner reads full HEAD for itself, and
    # `ls-remote` answers in full SHAs, so comparing one against the abbreviation
    # this state file carries reports every push as lost. `report` names the
    # resume for a refusal (ls-remote for a stale lease, pull --rebase for an
    # un-forced divergence) so nothing is added on top of it.
    nv = ("--no-verify",) if no_push_verify else ()
    result = git.push.push(
        wt_path, gated=True, owner_check=gh.owner_hooks.refuse_outgoing,
        trail=trail, args=nv)
    git.push.report(result, wt_path)
    if result.status is git.push.PushStatus.HELD:
        return
    if not result.ok:
        record.commit_status = git.land.commit_status(result.status)
        return
    if not git.push.holds(wt_path, record.commit_sha):
        return

    core.log.info(f"Pushed held fixes ({record.commit_sha})")
    record.commit_status = CommitStatus.PUSHED


@dataclass(frozen=True)
class PushBlock:
    """Why `--finish --post` must not publish: the commit is not on the remote."""

    sha: str
    remote_sha: str
    orphaned: bool = False

    @property
    def message(self) -> str:
        """The last line `--finish --post` prints when it refuses to publish."""
        if self.orphaned:
            return ("nothing was published: the fix commit was rewritten away, "
                    "see the warning above")
        expect = git.client.abbrev(self.remote_sha) or "<origin tip>"
        return (
            f"nothing was published: push {git.client.abbrev(self.sha)} first — "
            f"pr push --expect {expect} — then re-run "
            f"{pr.comments_fix.CLOSEOUT_COMMAND}"
        )


def unpublished_commit(
    ctx: pr.context.ResolvedContext,
    trail: Trail | None = None,
    *,
    no_push_verify: bool = False,
) -> PushBlock | None:
    """The commit `--finish --post` still owes the remote, if any.

    Attempts the held-fix push first. Operator commits that moved HEAD past the
    snapshot are not pushed from here — they are the operator's, and the block
    names `pr push` instead.
    """
    wt = ctx.require_worktree()
    state = pr.state.load_state(ctx.target_dir)
    if state is None or not state.fix.fix.items:
        return None
    pr.history_rewrite.follow_history_rewrite(state, wt)
    push_held_commit(state, wt, trail, no_push_verify=no_push_verify)
    pr.state.save_state(ctx.target_dir, state)
    record = state.fix.fix
    landing = pr.attribution.landing_of(record, wt)
    if landing is pr.attribution.FixLanding.UNPUSHED:
        sha = record.commit_sha or git.client.head_sha(cwd=wt)
        remote = git.push.remote_head(wt, ctx.branch) or ""
        return PushBlock(sha=sha, remote_sha=remote)
    if landing is pr.attribution.FixLanding.ORPHANED:
        sha = record.commit_sha or git.client.head_sha(cwd=wt)
        remote = git.push.remote_head(wt, ctx.branch) or ""
        return PushBlock(sha=sha, remote_sha=remote, orphaned=True)
    return None


class SkipReason(StrEnum):
    """Why a FIXED or ALREADY_ADDRESSED thread is still open after closeout."""

    PUSH_PENDING = "push pending"
    NEVER_COMMITTED = "never committed"
    ORPHANED = "fix commit rewritten away"
    RESOLVE_FAILED = "resolve failed"
    NOT_ON_PR = "not on the PR"


@dataclass(frozen=True)
class SkippedThread:
    """A closeout item that should have been resolved and was not."""

    id: str
    reason: SkipReason


def _is_review_thread_id(item_id: str) -> bool:
    """True for a real review-thread id, not a synthetic ic-/rb- comment item."""
    if not item_id:
        return False
    prefix = item_id.split("-", 1)[0]
    return CommentSourceKind.from_id_prefix(prefix) is CommentSourceKind.UNSET


def _unanswered_closeout_item(
    item: ItemOutcome, threads_by_id: dict[str, ReportThread],
) -> bool:
    """Whether this row still owes a reply on an open unanswered thread."""
    if item.outcome not in (FixOutcome.FIXED, FixOutcome.ALREADY_ADDRESSED):
        return False
    thread = threads_by_id.get(item.id)
    if thread is None or thread.is_resolved:
        return False
    return pr.settlement.settlement_for(thread) is None


def _skip_reason_for(
    item: ItemOutcome,
    thread: ReportThread | None,
    landing: pr.attribution.FixLanding,
) -> SkipReason | None:
    """Why a settled item keeps closeout debt, or None.

    A resolved thread never does; NOT_ON_PR only for real review-thread ids.
    """
    if item.outcome not in (FixOutcome.FIXED, FixOutcome.ALREADY_ADDRESSED):
        return None
    if not _is_review_thread_id(item.id):
        return None
    if thread is None:
        return SkipReason.NOT_ON_PR
    if thread.is_resolved:
        return None
    if item.outcome is FixOutcome.FIXED:
        if landing is pr.attribution.FixLanding.UNPUSHED:
            return SkipReason.PUSH_PENDING
        if landing is pr.attribution.FixLanding.NEVER_COMMITTED:
            return SkipReason.NEVER_COMMITTED
        if landing is pr.attribution.FixLanding.ORPHANED:
            return SkipReason.ORPHANED
    return SkipReason.RESOLVE_FAILED


def _skipped_after_drain(
    items: list[ItemOutcome],
    threads_by_id: dict[str, ReportThread],
    landing: pr.attribution.FixLanding,
) -> list[SkippedThread]:
    skipped: list[SkippedThread] = []
    for item in items:
        reason = _skip_reason_for(item, threads_by_id.get(item.id), landing)
        if reason is not None:
            skipped.append(SkippedThread(id=item.id, reason=reason))
    return skipped


def _warn_open_threads_without_snapshot(
    state: pr.state.PRState, threads_by_id: dict[str, ReportThread],
) -> None:
    recorded = {o.id for o in state.fix.fix.items if o.id}
    missing = [
        thread.id for thread in threads_by_id.values()
        if thread.id
        and thread.id not in recorded
        and not thread.is_resolved
        and thread.state is not ThreadState.RESOLVED
        and pr.settlement.settlement_for(thread) is None
    ]
    if not missing:
        return
    ids = ", ".join(missing)
    core.log.warn(
        f"{len(missing)} open thread(s) no round recorded: {ids} — "
        f"run `pr comments --fix` or `pr comments --settle <id> --as …`"
    )


def _keep_skipped_debt(
    items: list[ItemOutcome],
    threads_by_id: dict[str, ReportThread],
    landing: pr.attribution.FixLanding,
) -> bool:
    """Warn for each skipped thread; True when any of them still owe closeout."""
    keep_debt = False
    for skipped in _skipped_after_drain(items, threads_by_id, landing):
        core.log.warn(f"{skipped.id}: left open — {skipped.reason}")
        keep_debt = keep_debt or skipped.reason is not SkipReason.NOT_ON_PR
    return keep_debt


def post_pending_fix_replies(
    state: pr.state.PRState,
    repo: str,
    pr_number: int,
    threads_by_id: dict[str, ReportThread],
    host: str = "",
) -> None:
    """Send the replies the fix pass produced but did not deliver.

    Four ways a reply ends up here: the push failed, the push was held because
    discussion was open, the run was a draft, or the operator landed the same
    fixes by hand — HEAD moved past the snapshot and the remote holds it. An
    empty SHA with HEAD unmoved is not a hand-landing: the commit never
    happened, and FIXED replies stay queued. Called during --finish, which is
    the second chance for all four.

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
    owed = any(
        _unanswered_closeout_item(item, threads_by_id) for item in record.items
    )
    if (
        not pr.attribution.commit_unpushed(record.commit_status)
        and not fix.replies_pending
        and not owed
    ):
        return

    wt_path = Path(state.identity.worktree_root) if state.identity.worktree_root else None
    if not wt_path:
        return

    landing = pr.attribution.landing_of(record, wt_path)

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
    # FIXED replies wait for the work to be on the remote; the other buckets
    # cite no fix commit and drain regardless.
    withheld = False
    fixed = bucket(FixOutcome.FIXED)
    if landing is pr.attribution.FixLanding.LANDED and fixed:
        # The same reconciliation the summary rows get, read through the same
        # resolver. A pass whose commit a hook rejected records no SHA of its
        # own, so an operator who then lands those fixes by hand would
        # otherwise be replied to with "Fixed in ``" over an empty link.
        fix.replies_posted += pr.thread_replies.reply_to_fixed(
            fixed, threads_by_id, repo, pr_number,
            pr.history_rewrite.reconciled_commit(record, record.commit_status, wt_path), wt_path,
            host,
        )
        resolved += pr.settlement.resolve_fixed_threads(
            fixed, threads_by_id, repo=gh.owners.boundary_repo(repo, host))
    elif fixed:
        withheld = True
        if landing is pr.attribution.FixLanding.UNPUSHED:
            core.log.info("Push still pending — skipping deferred replies")
        elif landing is pr.attribution.FixLanding.NEVER_COMMITTED:
            core.log.info("Fixes were never committed — skipping deferred replies")
        elif landing is pr.attribution.FixLanding.ORPHANED:
            core.log.info(
                "Fix commit was rewritten away — skipping deferred replies"
            )

    addressed = bucket(FixOutcome.ALREADY_ADDRESSED)
    if addressed:
        fix.replies_posted += pr.thread_replies.post_already_addressed_replies(
            addressed, threads_by_id, repo, pr_number, wt_path, host=host,
        )
        resolved += pr.settlement.resolve_fixed_threads(
            addressed, threads_by_id, repo=gh.owners.boundary_repo(repo, host))

    # Not resolved, unlike the other two: telling a reviewer their premise does
    # not hold is the reply most likely to be argued with, so the thread stays
    # open for them to answer. Mirrors the triage phase.
    dismissed = bucket(FixOutcome.DISMISSED)
    if dismissed:
        fix.replies_posted += pr.thread_replies.post_dismissed_replies(
            dismissed, threads_by_id, repo, pr_number, wt_path, host,
        )

    state.comments.move_to_resolved(resolved, updated_at=pr.state.now_iso())

    # A draft delivered nothing, so the queue has to survive for the next run.
    # Withheld FIXED replies keep the queue; an unlanded commit is never marked
    # pushed just because the other buckets drained. A skipped resolve keeps
    # the same debt, except for ids that are not on the PR.
    keep_debt = False
    if core.publishing.enabled():
        keep_debt = _keep_skipped_debt(record.items, threads_by_id, landing)
        if keep_debt:
            fix.replies_pending = True
        if (landing is pr.attribution.FixLanding.LANDED
                and pr.attribution.commit_unpushed(record.commit_status)):
            record.commit_status = CommitStatus.PUSHED
        if not withheld and not keep_debt:
            fix.replies_sent()
