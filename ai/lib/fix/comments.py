"""The comments pass: what the fix engine is handed, and what it owes after.

`fix.engine` runs a pass; this says what the review-comment domain hands it and
what that domain does once the work has landed. `fix.ci` is the same shape for
CI, and the other side of the boundary is `fix.types.FixItem` — the translation
into one happens here so that what the engine sees is the same for every domain
and what the comments pass reasons about stays `pr`'s own types.

Layer 5 because the adapter reads `pr` and nothing above it: the dispositions
are `pr.triage_round`'s, the replies `pr.thread_replies`', the summary
`pr.summary_publish`'s, the state write `pr.fix_state`'s. The placement rule is
`rebase.prepush`'s docstring — an adapter's home is the lowest layer its own
imports permit, and `fix.engine.run()` taking it as an argument is what makes
that free.

**One tail, whether or not the agent ran.** `fix.engine.run` declines a pass
with no items and never calls `record`, so a round with nothing fixable would
have no way to publish the table for what triage settled or to write its
outcomes. `run_pass` calls `record` itself in that case rather than keeping a
second copy of the tail, which is what the two copies that used to exist here
kept drifting apart over.
"""

# doc-group: pipeline

from __future__ import annotations

import dataclasses
import functools
from pathlib import Path

import core.publishing
from core.phases import Phase
from core.trail import Trail
import fix.comment_checklist
import fix.comment_replies
import fix.engine
import fix.gate
import fix.suite
import fix.types
import fix.verify
import git.client
import git.topology
from git.land import CommitStatus
import pr.attribution
import pr.comments
import pr.comments_fix
import pr.context
import pr.fix_state
import pr.state
import pr.summary_model
import pr.summary_publish
import pr.triage_round
from pr.fix import FixOutcome, ItemOutcome
from pr.thread_models import (
    CommentFixResult, CommentItem, PRReport, ReplyOutcome, TrackingResult,
    TriageResult,
)


class CommentFixAdapter(fix.engine.FixAdapter):
    """The comments pass, in the terms `fix.engine` runs one in.

    Everything before the agent arrives here settled: triage has classified the
    threads, the supersession and contested holds have been placed, and the
    replies triage itself owed have gone out. What is left is the two halves the
    engine cannot supply — the checklist entries, and everything this pass owes a
    reviewer once its work has landed.

    Only the fixable entries reach `items`. The rest are carried so `record` can
    account for them: a summary that named only what the agent saw would read as
    if the dismissed and the contested were never triaged.

    **A round with nothing fixable builds one of these too.** `fix.engine.run`
    declines to run a pass with no items and never calls `record`, which is
    right — there is nothing to commit — but the round still owes a reviewer the
    table for what triage settled and the state file its outcomes. That tail
    used to be a second copy in the entry function, and the two drifted: one
    forgot `has_comment_items`, and they disagreed about whether an unaccounted
    thread suppressed the summary. Here the caller runs the engine or calls
    `record` itself, and there is one tail either way.
    """

    phase = Phase.COMMENTS_FIX
    # Every domain that runs the verify gate declares the gate's phase. Without
    # it the gate is sized and prompted as the fix pass, which hands a checking
    # agent a template telling it to edit source.
    verify_phase = Phase.COMMENTS_VERIFY
    action = "applying review comment suggestions"
    item_noun = "thread"

    def __init__(
        self,
        report: PRReport,
        ctx: pr.context.ResolvedContext,
        wt_path: Path,
        round_: pr.triage_round.TriagedRound,
        *,
        trail: Trail | None = None,
    ) -> None:
        self.workdir = wt_path
        # The run's target directory, not the worktree. What this pass writes —
        # the tracking file, the session log, the PR description draft — is its
        # own bookkeeping, and a target repo that does not gitignore `ignore/`
        # had all three swept into the commit the pass then pushed.
        self.artifacts = pr.comments.artifacts_dir(ctx.target_dir)
        self.title = f"Comment Fix Tracking — PR #{report.pr_number}"
        self.branch = ctx.branch
        self.repo = ctx.repo
        self.pr = str(report.pr_number)
        self.report = report
        self.ctx = ctx
        self.trail = trail
        self.threads_by_id = {t.id: t for t in report.threads}
        self.round = round_
        # `record` builds this and `_run_comment_fix` returns it.
        self.result = CommentFixResult()

    @property
    def ran(self) -> bool:
        """Whether the agent was given anything to do.

        `record` runs either way — see the class docstring — so the two acts
        that only make sense after an agent edited the tree ask this rather
        than assuming it: delivering the description draft, and reading HEAD
        for a commit that may not exist.
        """
        return self.round.has_fixables

    @functools.cached_property
    def main_wt(self) -> Path | None:
        """The default-branch checkout, fetched and reset, or None if there is none.

        Resolved on first use rather than at construction: finding it updates a
        second worktree from the remote, which a pass that turns out to have
        nothing to fix has no business doing.
        """
        return fix.comment_checklist.find_and_update_main_worktree(self.workdir)

    def add_dirs(self) -> list[Path]:
        """The base grant, plus the default-branch worktree when there is one.

        Extends rather than replaces: the base grant carries the artifacts
        directory the tracking file lives in, and listing only the worktrees
        here is what left this pass unable to write the one file it is judged
        on.
        """
        return super().add_dirs() + ([self.main_wt] if self.main_wt else [])

    def items(self) -> list[fix.types.FixItem]:
        return fix.comment_checklist.fix_items(
            self.round.fixable, self.threads_by_id, self.workdir,
            fixable_items=self.round.fixable_items,
            default_branch=git.topology.default_branch_cached(self.workdir),
        )

    def after_verify(self, outcomes: list[ItemOutcome]) -> None:
        """Hold publishing over what the gate and the agent decided.

        The comments pass is the one domain with a reviewer waiting on the
        other end, so it is the one that must not report a round as done while
        part of it needs a person. Triage placed the same hold over its own
        verdicts before the agent ran; this covers the three that arrive after
        — see `pr.triage_round.hold_after_verify`.
        """
        pr.triage_round.hold_after_verify(outcomes, self.trail)

    def template_vars(self) -> dict[str, str]:
        return {
            "pr_body_file": str(pr.comments.pr_body_draft(self.artifacts)),
            "main_worktree": fix.comment_checklist.main_worktree_block(self.main_wt),
        }

    def landing(
        self, outcomes: list[ItemOutcome], changed: set[str] | None,
    ) -> fix.engine.LandSpec:
        """Commit what the agent touched, under one static subject.

        Not the whole tree: this pass runs in a contributor's worktree on a
        branch under review, and anything else dirty there would be swept into
        a commit that then goes out to the PR.

        `recover` because this agent edits a repo whose own conventions it
        follows, and a fix pass in a repo that commits its work leaves the
        landing nothing to stage — see `git.replay.replayed_commit`, which is what finds
        that commit again after a rebase. An agent that committed its own work
        leaves an empty scope, which is the same nothing-to-stage the recovery
        already answers.
        """
        fixed = sum(1 for o in outcomes if o.outcome.counts_as_fixed)
        deferred = sum(1 for o in outcomes if o.outcome is FixOutcome.DEFERRED)
        msg = "fix: address review comments"
        if fixed:
            msg += "\n\n" + fix.suite.qualify_tally(
                f"{fixed} fixed, {deferred} deferred", self.suite)
        detail = fix.suite.detail_lines(self.suite)
        if detail:
            msg += "\n\n" + "\n".join(detail)
        return fix.engine.LandSpec(
            message=msg,
            regen="chore: regenerate after review comment fixes",
            recover=True,
            paths=changed if changed else set(),
        )

    def record(self, run: fix.engine.FixRun) -> None:
        """Everything this pass owes once its work is committed.

        The replies, the resolutions, the PR description the agent may have
        rewritten, the reviewer-facing summary and the state file, in that order:
        each of the outward acts is gated, and the state file has to record which
        of them actually went out.

        Runs whether or not the agent did — see the class docstring. Everything
        below is written to be true of a round with no outcomes as well, which
        is what lets there be one tail instead of two.
        """
        cp = pr.attribution.pass_commit(self.workdir, run.landed)
        tracking = TrackingResult.from_outcomes(
            run.outcomes, self.round.fixable,
            fixable_items=self.round.fixable_items,
        )
        # Stamped here rather than left for the pass envelope to imply: from
        # this point on, "no SHA on the entry" means the entry did not land in
        # this commit, which is what lets a later round tell its own rows from
        # an earlier round's.
        pr.attribution.stamp_pass_commit(tracking.both(FixOutcome.FIXED), cp.sha or "")

        fixed_bucket = tracking.bucket(FixOutcome.FIXED)
        replies = self.round.replies.plus(fix.comment_replies.settle_fixed(
            fixed_bucket, self.threads_by_id, self.repo,
            self.report.pr_number, cp, self.workdir, self.ctx.host,
        ))
        content = pr.summary_model.RoundContent(
            by_outcome=self.round.by_outcome(tracking),
            issue_comments=self.report.issue_comments,
            review_body_comments=self.report.review_body_comments,
        )
        summary = pr.summary_publish.publish(
            content, cp, self.repo, self.report.pr_number, self.threads_by_id,
            self.report,
            has_comment_items=self.round.has_items,
            has_unaccounted=self.round.has_unaccounted,
            head_sha=cp.sha or self.ctx.head_sha,
            wt_path=self.workdir,
            host=self.ctx.host,
        )

        pr.fix_state.persist(
            self._state_for(content, cp, replies, summary, tracking),
            self.workdir, self.ctx, self.trail, resolved=list(replies.resolved),
            summary_posted_url=summary.url or "",
            replies_delivered=self._replies_delivered(fixed_bucket, cp),
        )
        self.result = _result_for(content, cp, replies, summary, run)

    def _state_for(
        self,
        content: pr.summary_model.RoundContent,
        cp: pr.attribution.CommitPushResult,
        replies: ReplyOutcome,
        summary: pr.summary_publish.SummaryOutcome,
        tracking: TrackingResult,
    ) -> pr.comments_fix.FixSummary:
        """What this round persists.

        Reads `content.by_outcome` rather than rebuilding the mapping: the
        record keeps `DECLINED` apart from `NEEDS_HUMAN` and the summary folds
        them together, and that disagreement is only safe while both are
        readings of one dict.

        The PR description is delivered here, and only when the agent ran: a
        round that handed it nothing has no description to deliver, and asking
        would send the draft an earlier round left on disk, which `--finish`
        owns.
        """
        return pr.comments_fix.FixSummary(
            fix=pr.fix_state.fix_record_for(
                content.by_outcome,
                commit_sha=cp.sha or "",
                commit_status=cp.status,
                head_sha=self._snapshot_sha(),
            ),
            reviewers=pr.fix_state.reviewers_for(content.by_outcome),
            replies_posted=replies.posted,
            replies_pending=self._replies_pending(tracking),
            pr_body_pending=self.ran and pr.comments.deliver_pr_body(
                self.artifacts, self.repo, self.report.pr_number,
            ),
            summary_url=summary.recorded_url,
            summary_deferred=summary.deferred,
            has_comment_items=self.round.has_items,
            updated_at=pr.state.now_iso(),
        )

    def _snapshot_sha(self) -> str:
        """The commit this round's outcomes were measured against.

        HEAD after the pass, not `ctx.head_sha`: the fix pass commits, so the
        snapshot has to name the commit it made. A round that ran no agent
        committed nothing, so HEAD has not moved and the context already holds
        the same answer without the subprocess.
        """
        if not self.ran:
            return self.ctx.head_sha
        return git.client.head_sha(short=True, cwd=self.workdir)

    def _replies_pending(self, tracking: TrackingResult) -> bool:
        """Whether a reply this round rendered is still owed to a reviewer.

        Two queues, and either one owes. A fixed thread's reply waits on the
        publishing gate, which a hold or a draft run leaves shut; the triage
        replies were rendered before the pass and answer for themselves.
        """
        if tracking.bucket(FixOutcome.FIXED) and not core.publishing.enabled():
            return True
        return fix.comment_replies.replies_drafted(
            self.round.already_addressed, self.round.dismissed)

    def _replies_delivered(
        self,
        fixed_bucket: list[CommentItem],
        cp: pr.attribution.CommitPushResult,
    ) -> bool:
        """Whether this round sent its replies for real, discharging the debt.

        `_replies_pending` is False whenever publishing is on — both of its
        conditions require the gate to be shut — so the gate being open is
        exactly the case where `settle_fixed` and `post_triage_replies` sent
        whatever this round's triage found rather than queuing it. A round
        re-triages every thread still open, so a debt an earlier round left
        behind is retried here too; nothing to send is nothing owed, and
        either way the queue this round closes has nothing left in it.

        That holds for the triage half unconditionally, but `settle_fixed`
        (`ai/lib/fix/comment_replies.py`) has a second gate of its own: it
        refuses to send anything for the fixed bucket unless `cp.status` is
        `PUSHED`. A round with fixed items but a commit that failed or held
        at push time delivered nothing for that bucket, so reporting delivery
        here would discharge a reply queue — this round's or a stale `True`
        carried over from an earlier one — that was never actually sent.
        """
        if fixed_bucket and cp.status is not CommitStatus.PUSHED:
            return False
        return core.publishing.enabled()


def _result_for(
    content: pr.summary_model.RoundContent,
    cp: pr.attribution.CommitPushResult,
    replies: ReplyOutcome,
    summary: pr.summary_publish.SummaryOutcome,
    run: fix.engine.FixRun,
) -> CommentFixResult:
    """What this round reports on stdout.

    Read off the content rather than rebuilt beside it: the five bucket fields
    are the round's own, flattened for the JSON shape `pr-comments` documents,
    so the content owns them and this projects them. A free function rather
    than a classmethod on `CommentFixResult`, which is layer 4 and must not
    learn what a `FixRun` is.
    """
    return CommentFixResult(
        fixed=content.of(FixOutcome.FIXED),
        needs_human=content.needs_a_person,
        dismissed=content.of(FixOutcome.DISMISSED),
        already_addressed=content.of(FixOutcome.ALREADY_ADDRESSED),
        deferred=content.of(FixOutcome.DEFERRED),
        commit_sha=cp.sha,
        commit_status=cp.status,
        replies_posted=replies.posted,
        summary_url=summary.url,
        summary_deferred=summary.deferred,
        max_turns=run.max_turns,
        max_budget=run.max_budget,
        batches=run.batches,
    )


def _at_evidence(entries: list[CommentItem]) -> list[CommentItem]:
    """Entries re-anchored at the line triage cited, where it cited one.

    The gate is asked about the code that is said to already do the work, so
    that is the code it is shown context for and pointed at — not the line the
    reviewer happened to comment on.
    """
    return [
        dataclasses.replace(
            e, file=e.evidence_file or e.file, line=e.evidence_line or e.line,
        )
        for e in entries
    ]


def _addressed_outcome(entry: CommentItem) -> ItemOutcome:
    """An already-addressed verdict in the shape the verify gate checks."""
    anchored = _at_evidence([entry])[0]
    return ItemOutcome(
        id=entry.id,
        outcome=FixOutcome.ALREADY_ADDRESSED,
        summary=entry.summary,
        reason=entry.reasoning or entry.reason,
        file=anchored.file,
        line=anchored.line,
        read_sha=entry.read_sha,
        evidence_file=entry.evidence_file,
        evidence_line=entry.evidence_line,
    )


def check_addressed(
    round_: pr.triage_round.TriagedRound,
    adapter: CommentFixAdapter,
    verify: fix.gate.VerifyFn | None,
    trail: Trail | None = None,
) -> pr.triage_round.TriagedRound:
    """Hold triage's already-addressed verdicts against the behaviour they claim.

    The verdict is the least-checked outcome the pass produces and the one with
    the most reach: it tells a reviewer their point was moot and resolves their
    thread under the author's name. Triage has already refused any whose cited
    line does not resolve; this asks the existing verify gate the question that
    leaves open — does the code there do what was asked?

    Through `fix.gate.verify_claims` rather than a mechanism of its own: the
    gate's meaning of silence (unverified, not falsified) and of `broken`
    (NEEDS_HUMAN) is the one every other claim is held to. A falsified verdict
    is moved to `needs_human` on the returned round and placed under the same
    hold a falsified fix earns, so the replies that follow go out — or wait —
    accordingly.

    `verify` None (`--no-verify`) and a round with nothing already addressed
    return the round unchanged.
    """
    if verify is None or not round_.already_addressed:
        return round_
    outcomes = [_addressed_outcome(e) for e in round_.already_addressed]
    items = fix.comment_checklist.fix_items(
        _at_evidence(round_.threads.already_addressed), adapter.threads_by_id,
        adapter.workdir,
        fixable_items=_at_evidence(round_.items.already_addressed),
        default_branch=git.topology.default_branch_cached(adapter.workdir),
    )
    fix.gate.verify_claims(
        outcomes, verify, adapter, {i.id: i for i in items}, trail,
    )
    settled = pr.triage_round.settle_addressed(round_, outcomes)
    adapter.after_verify(outcomes)
    return settled


def run_pass(
    triage_result: TriageResult,
    report: PRReport,
    wt_path: Path,
    ctx: pr.context.ResolvedContext,
    trail: Trail | None = None,
    verify: bool = True,
) -> CommentFixResult:
    """Apply mechanical fixes for the threads and items triage found actionable.

    The pass's entry point, in the shape `review.fix.run_fix_pass` and
    `rebase.prepush.fix_push_failures` established: build the domain's half,
    hand it to the engine, and return what the domain recorded.

    `verify` runs the gate that holds each claimed fix against what actually
    runs before anything is committed or replied to. On by default: a fix pass
    publishes a claim about behaviour under the operator's name, and the gate is
    what makes that claim worth something. The same gate checks triage's
    already-addressed verdicts first, before their replies are sent — see
    `check_addressed` — including on a round with nothing for the agent to fix.
    """
    threads_by_id = {t.id: t for t in report.threads}

    round_ = pr.triage_round.triage_the_round(
        triage_result, report, wt_path, ctx, trail=trail,
    )

    # Before the fix pass touches the tree: every line these entries carry —
    # the review comment's own and triage's citation alike — was read against
    # this head, and a permalink built after the fix commit needs to know that
    # to decide whether its anchor still points at the reviewer's code.
    pr.attribution.stamp_read_sha(
        round_.fixable + round_.fixable_items + round_.needs_human
        + round_.dismissed + round_.already_addressed,
        git.client.head_sha(short=True, cwd=wt_path),
    )

    adapter = CommentFixAdapter(report, ctx, wt_path, round_, trail=trail)
    gate = fix.verify.run if verify else None

    # Before any reply goes out: an already-addressed reply resolves the thread
    # the moment it is posted, so a verdict the gate falsifies has to have left
    # that bucket — and the hold it places has to be shut — by the time
    # `post_triage_replies` reads either.
    round_ = check_addressed(round_, adapter, gate, trail)
    round_ = dataclasses.replace(
        round_, replies=fix.comment_replies.post_triage_replies(round_, threads_by_id, report, ctx, wt_path),
    )
    adapter.round = round_

    if round_.has_fixables:
        # The engine batches the entries, runs the agent, lands the commit and
        # calls `record` itself.
        fix.engine.run(adapter, trail=trail, verify=gate)
    else:
        # `fix.engine.run` returns an empty `FixRun` for a pass with no items
        # and never reaches `record`, which is the right contract — there is
        # nothing to commit. The round still owes its table and its state
        # write, so the tail is called here with the run that did not happen.
        # One tail either way: the second copy this replaces had drifted from
        # the first in three ways before anyone noticed.
        adapter.record(fix.engine.FixRun())
    return adapter.result
