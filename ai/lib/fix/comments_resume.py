"""Whether an interrupted ``pr comments --fix`` pass should be resumed.

The checkpoint is the in-flight record ``pr.fix_checkpoint`` writes. This
module is the decision: auto-detect, require, or discard, keyed on the PR,
the branch, HEAD, and the agent's file blobs. Nothing here writes a
checkpoint; ``fix.comments.run_pass`` and the adapter's ``advance`` do that.
"""

# doc-group: pipeline

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import core.log
import fix.pass_resume
import git.client
import pr.context
import pr.fix_checkpoint
import pr.owed_feedback
from pr.comments_state import ThreadState
from pr.fix_checkpoint import CheckpointPhase, CommentsFixCheckpoint
from pr.thread_models import PRReport


class ResumeMode(StrEnum):
    """How the operator asked to treat an interrupted comments-fix pass."""

    AUTO = "auto"
    REQUIRE = "require"
    FRESH = "fresh"


def mode_for(resume: bool, no_resume: bool) -> ResumeMode:
    """The mode ``--resume`` / ``--no-resume`` select. Neither is AUTO."""
    if no_resume:
        return ResumeMode.FRESH
    if resume:
        return ResumeMode.REQUIRE
    return ResumeMode.AUTO


@dataclass(frozen=True)
class ResumeDecision:
    """What ``decide`` concluded. At most one of ``checkpoint`` and ``refusal``."""

    checkpoint: CommentsFixCheckpoint | None = None
    refusal: str = ""

    @property
    def resumes(self) -> bool:
        """Whether the caller should continue the recorded pass."""
        return self.checkpoint is not None and not self.refusal

    @property
    def refused(self) -> bool:
        """Whether the caller must stop (``--resume`` with nothing to resume)."""
        return bool(self.refusal)


def decide(
    ctx: pr.context.ResolvedContext,
    wt_path: Path,
    mode: ResumeMode,
    report: PRReport,
) -> ResumeDecision:
    """The checkpoint to resume, an empty decision, or a refusal.

    Order is the rule. ``FRESH`` discards and never resumes. A missing,
    terminal, or (repo, PR, branch) mismatch is a REQUIRE refusal and an
    AUTO no-op. HEAD must still be the recorded key, with the CHECKED and
    LANDED exceptions below. New feedback before ``AGENT_RUNNING`` starts
    AUTO fresh and leaves REQUIRE on the recorded pass. Operator edits to
    the agent's files always refuse.
    """
    if mode is ResumeMode.FRESH:
        pr.fix_checkpoint.discard(ctx.target_dir)
        return ResumeDecision()

    ckpt = pr.fix_checkpoint.load(ctx.target_dir)
    if not _usable(ckpt, ctx):
        if mode is ResumeMode.REQUIRE:
            return ResumeDecision(refusal=_no_pass(ctx))
        return ResumeDecision()
    assert ckpt is not None

    if not _head_matches(ckpt, ctx, wt_path):
        if mode is ResumeMode.REQUIRE:
            return ResumeDecision(
                refusal=(
                    f"HEAD has moved since the interrupted `pr comments --fix` "
                    f"pass for PR #{ctx.pr_number}"
                ),
            )
        core.log.warn("HEAD moved")
        files = ckpt.engine.agent_files
        if files:
            core.log.warn(
                "Uncommitted edits from the interrupted pass will stay "
                "uncommitted: " + ", ".join(files)
            )
        return ResumeDecision()

    if (
        not ckpt.phase.reached(CheckpointPhase.AGENT_RUNNING)
        and _new_feedback(ckpt, report)
    ):
        if mode is ResumeMode.AUTO:
            core.log.info(
                "starting a fresh fix pass; new feedback arrived before "
                "the agent ran"
            )
            return ResumeDecision()
        # REQUIRE continues on the recorded pass; new feedback is the next round.

    edited = fix.pass_resume.edited_since(
        wt_path, ckpt.engine.file_shas, set(ckpt.engine.dirty_before),
    )
    if edited is None:
        return ResumeDecision(
            refusal=(
                "cannot read the worktree to resume the interrupted "
                f"`pr comments --fix` pass for PR #{ctx.pr_number}"
            ),
        )
    if edited:
        backup = ckpt.engine.backup_dir
        restore = (
            f"restore them from {backup}" if backup
            else "restore them from the pass backup"
        )
        return ResumeDecision(
            refusal=(
                "Agent files edited since the interrupted pass: "
                + ", ".join(edited)
                + f". {restore.capitalize()} or re-run with "
                "`pr comments --fix --no-resume`."
            ),
        )

    core.log.info(
        f"Resuming the interrupted fix pass after {ckpt.phase.value}"
    )
    return ResumeDecision(checkpoint=ckpt)


def _usable(
    ckpt: CommentsFixCheckpoint | None, ctx: pr.context.ResolvedContext,
) -> bool:
    if ckpt is None or ckpt.phase.terminal:
        return False
    return (
        ckpt.repo == ctx.repo
        and ckpt.branch == ctx.branch
        and ckpt.pr == (ctx.pr_number or 0)
    )


def _no_pass(ctx: pr.context.ResolvedContext) -> str:
    return (
        f"no interrupted `pr comments --fix` pass to resume for PR "
        f"#{ctx.pr_number}"
    )


def _head_matches(
    ckpt: CommentsFixCheckpoint,
    ctx: pr.context.ResolvedContext,
    wt_path: Path,
) -> bool:
    """Whether live HEAD is still the resume key.

    Matches when the context's HEAD equals the recorded `head_sha`, or the
    resolved live SHA does. ``LANDED`` also matches when live is the landed
    commit. ``CHECKED`` matches when live is the one commit on top of
    `head_sha` — landing committed, then the process died before LANDED.
    """
    if ctx.head_sha and ctx.head_sha == ckpt.head_sha:
        return True
    live = git.client.head_sha(cwd=wt_path)
    live_full = _rev(wt_path, live)
    if live_full and live_full == _rev(wt_path, ckpt.head_sha):
        return True
    if ckpt.phase is CheckpointPhase.LANDED:
        landed = ckpt.engine.commit_sha
        if landed and live_full and live_full == _rev(wt_path, landed):
            return True
    if (
        ckpt.phase is CheckpointPhase.CHECKED
        and fix.pass_resume.head_is_one_past(wt_path, ckpt.head_sha)
    ):
        return True
    return False


def _rev(wt_path: Path, sha: str) -> str:
    if not sha:
        return ""
    return git.client.out("rev-parse", "--verify", sha, cwd=wt_path)


def _new_feedback(ckpt: CommentsFixCheckpoint, report: PRReport) -> bool:
    """Open threads or unseen comments this checkpoint did not triage."""
    triaged_ids = {t.id for t in ckpt.triage.threads if t.id}
    triaged_ids.update(ckpt.triage.settled)
    for thread in report.threads:
        if thread.is_resolved or thread.state is ThreadState.RESOLVED:
            continue
        if thread.id and thread.id not in triaged_ids:
            return True
    item_sources = {str(i.source_id) for i in ckpt.triage.comment_items if i.source_id}
    for comment, seen in (
        (c, ckpt.seen_issue_comments) for c in report.issue_comments
    ):
        if _unseen_comment(comment, seen, item_sources):
            return True
    for comment, seen in (
        (c, ckpt.seen_review_body_comments) for c in report.review_body_comments
    ):
        if _unseen_comment(comment, seen, item_sources):
            return True
    return False


def _unseen_comment(comment: dict, seen: dict[int, str], item_sources: set[str]) -> bool:
    cid = comment.get("id")
    if cid is None:
        return False
    if pr.owed_feedback.is_seen(comment, seen):
        return False
    return str(cid) not in item_sources
