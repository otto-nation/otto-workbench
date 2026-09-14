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

from core import publishing
from core.phases import Phase
from core.trail import Trail
from fix import comment_checklist
from fix import comment_replies
from fix import engine as fix_engine
from fix import types as fix_types
from git import client as git_client
from git import topology as git_topology
from pr import attribution
from pr import comments as pc
from pr import comments_fix as pr_comments_fix
from pr import context as pr_context
from pr import fix_state
from pr import state as pr_state
from pr import summary_model
from pr import summary_publish
from pr import triage_round
from pr.fix import FixOutcome, ItemOutcome
from pr.thread_models import (
    CommentFixResult, PRReport, ReplyOutcome, TrackingResult, TriageResult,
)


class CommentFixAdapter(fix_engine.FixAdapter):
    """The comments pass, in the terms `fix_engine` runs one in.

    Everything before the agent arrives here settled: triage has classified the
    threads, the supersession and contested holds have been placed, and the
    replies triage itself owed have gone out. What is left is the two halves the
    engine cannot supply — the checklist entries, and everything this pass owes a
    reviewer once its work has landed.

    Only the fixable entries reach `items`. The rest are carried so `record` can
    account for them: a summary that named only what the agent saw would read as
    if the dismissed and the contested were never triaged.

    **A round with nothing fixable builds one of these too.** `fix_engine.run`
    declines to run a pass with no items and never calls `record`, which is
    right — there is nothing to commit — but the round still owes a reviewer the
    table for what triage settled and the state file its outcomes. That tail
    used to be a second copy in the entry function, and the two drifted: one
    forgot `has_comment_items`, and they disagreed about whether an unaccounted
    thread suppressed the summary. Here the caller runs the engine or calls
    `record` itself, and there is one tail either way.
    """

    phase = Phase.COMMENTS_FIX
    action = "applying review comment suggestions"
    item_noun = "thread"

    def __init__(
        self,
        report: PRReport,
        ctx: pr_context.ResolvedContext,
        wt_path: Path,
        round_: triage_round.TriagedRound,
        *,
        trail: Trail | None = None,
    ) -> None:
        self.workdir = wt_path
        # The run's target directory, not the worktree. What this pass writes —
        # the tracking file, the session log, the PR description draft — is its
        # own bookkeeping, and a target repo that does not gitignore `ignore/`
        # had all three swept into the commit the pass then pushed.
        self.artifacts = pc.artifacts_dir(ctx.target_dir)
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
        return comment_checklist.find_and_update_main_worktree(self.workdir)

    def add_dirs(self) -> list[Path]:
        """The branch worktree, plus the default-branch one when it exists."""
        return [self.workdir] + ([self.main_wt] if self.main_wt else [])

    def items(self) -> list[fix_types.FixItem]:
        return comment_checklist.fix_items(
            self.round.fixable, self.threads_by_id, self.workdir,
            fixable_items=self.round.fixable_items,
            default_branch=git_topology.default_branch_cached(self.workdir),
        )

    def template_vars(self) -> dict[str, str]:
        return {
            "pr_body_file": str(pc.pr_body_draft(self.artifacts)),
            "main_worktree": comment_checklist.main_worktree_block(self.main_wt),
        }

    def landing(
        self, outcomes: list[ItemOutcome], changed: set[str] | None,
    ) -> fix_engine.LandSpec:
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
            msg += f"\n\n{fixed} fixed, {deferred} deferred"
        return fix_engine.LandSpec(
            message=msg,
            regen="chore: regenerate after review comment fixes",
            recover=True,
            paths=changed if changed else set(),
        )

    def record(self, run: fix_engine.FixRun) -> None:
        """Everything this pass owes once its work is committed.

        The replies, the resolutions, the PR description the agent may have
        rewritten, the reviewer-facing summary and the state file, in that order:
        each of the outward acts is gated, and the state file has to record which
        of them actually went out.

        Runs whether or not the agent did — see the class docstring. Everything
        below is written to be true of a round with no outcomes as well, which
        is what lets there be one tail instead of two.
        """
        cp = attribution.pass_commit(self.workdir, run.landed)
        tracking = TrackingResult.from_outcomes(
            run.outcomes, self.round.fixable,
            fixable_items=self.round.fixable_items,
        )
        # Stamped here rather than left for the pass envelope to imply: from
        # this point on, "no SHA on the entry" means the entry did not land in
        # this commit, which is what lets a later round tell its own rows from
        # an earlier round's.
        attribution.stamp_pass_commit(tracking.both(FixOutcome.FIXED), cp.sha or "")

        replies = self.round.replies.plus(comment_replies.settle_fixed(
            tracking.bucket(FixOutcome.FIXED), self.threads_by_id, self.repo,
            self.report.pr_number, cp, self.workdir,
        ))
        content = summary_model.RoundContent(
            by_outcome=self.round.by_outcome(tracking),
            issue_comments=self.report.issue_comments,
            review_body_comments=self.report.review_body_comments,
        )
        summary = summary_publish.publish(
            content, cp, self.repo, self.report.pr_number, self.threads_by_id,
            self.report,
            has_comment_items=self.round.has_items,
            has_unaccounted=self.round.has_unaccounted,
            head_sha=cp.sha or self.ctx.head_sha,
            wt_path=self.workdir,
        )

        fix_state.persist(
            self._state_for(content, cp, replies, summary, tracking),
            self.workdir, self.ctx, self.trail, resolved=list(replies.resolved),
        )
        self.result = _result_for(content, cp, replies, summary, run)

    def _state_for(
        self,
        content: summary_model.RoundContent,
        cp: attribution.CommitPushResult,
        replies: ReplyOutcome,
        summary: summary_publish.SummaryOutcome,
        tracking: TrackingResult,
    ) -> pr_comments_fix.FixSummary:
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
        return pr_comments_fix.FixSummary(
            fix=fix_state.fix_record_for(
                content.by_outcome,
                commit_sha=cp.sha or "",
                commit_status=cp.status,
                head_sha=self._snapshot_sha(),
            ),
            reviewers=fix_state.reviewers_for(content.by_outcome),
            replies_posted=replies.posted,
            replies_pending=self._replies_pending(tracking),
            pr_body_pending=self.ran and pc.deliver_pr_body(
                self.artifacts, self.repo, self.report.pr_number,
            ),
            summary_url=summary.recorded_url,
            summary_deferred=summary.deferred,
            has_comment_items=self.round.has_items,
            updated_at=pr_state.now_iso(),
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
        return git_client.head_sha(short=True, cwd=self.workdir)

    def _replies_pending(self, tracking: TrackingResult) -> bool:
        """Whether a reply this round rendered is still owed to a reviewer.

        Two queues, and either one owes. A fixed thread's reply waits on the
        publishing gate, which a hold or a draft run leaves shut; the triage
        replies were rendered before the pass and answer for themselves.
        """
        if tracking.bucket(FixOutcome.FIXED) and not publishing.enabled():
            return True
        return comment_replies.replies_drafted(
            self.round.already_addressed, self.round.dismissed)


def _result_for(
    content: summary_model.RoundContent,
    cp: attribution.CommitPushResult,
    replies: ReplyOutcome,
    summary: summary_publish.SummaryOutcome,
    run: fix_engine.FixRun,
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


def run_pass(
    triage_result: TriageResult,
    report: PRReport,
    wt_path: Path,
    ctx: pr_context.ResolvedContext,
    trail: Trail | None = None,
) -> CommentFixResult:
    """Apply mechanical fixes for the threads and items triage found actionable.

    The pass's entry point, in the shape `review.fix.run_fix_pass` and
    `rebase.prepush.fix_push_failures` established: build the domain's half,
    hand it to the engine, and return what the domain recorded.
    """
    threads_by_id = {t.id: t for t in report.threads}

    round_ = triage_round.triage_the_round(
        triage_result, report, wt_path, ctx, trail=trail,
    )

    # Before the fix pass touches the tree: every line these entries carry —
    # the review comment's own and triage's citation alike — was read against
    # this head, and a permalink built after the fix commit needs to know that
    # to decide whether its anchor still points at the reviewer's code.
    attribution.stamp_read_sha(
        round_.fixable + round_.fixable_items + round_.needs_human
        + round_.dismissed + round_.already_addressed,
        git_client.head_sha(short=True, cwd=wt_path),
    )

    round_ = dataclasses.replace(
        round_, replies=comment_replies.post_triage_replies(round_, threads_by_id, report, ctx, wt_path),
    )

    adapter = CommentFixAdapter(report, ctx, wt_path, round_, trail=trail)
    if round_.has_fixables:
        # The engine batches the entries, runs the agent, lands the commit and
        # calls `record` itself.
        fix_engine.run(adapter, trail=trail)
    else:
        # `fix_engine.run` returns an empty `FixRun` for a pass with no items
        # and never reaches `record`, which is the right contract — there is
        # nothing to commit. The round still owes its table and its state
        # write, so the tail is called here with the run that did not happen.
        # One tail either way: the second copy this replaces had drifted from
        # the first in three ways before anyone noticed.
        adapter.record(fix_engine.FixRun())
    return adapter.result
