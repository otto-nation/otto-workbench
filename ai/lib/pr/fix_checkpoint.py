"""A comments-fix pass checkpoint, so settle and finish can adopt a partial snapshot.

`pr comments --fix` writes `state.json` only at the end of a successful pass.
An interrupted run therefore has no snapshot for `--settle` or `--finish` to
read. This file is the in-flight record of that pass: phase, triage, engine
progress, and the answers collected so far. Adopting a partial snapshot turns
unanswered fixable items into needs-human rows and unlanded fixes into
needs-human rows, then marks the checkpoint adopted so it cannot be resumed.

The file lives under `pr.comments.artifacts_dir`, beside the tracking file,
not in the worktree.
"""

# doc-group: pr-state

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field, replace as dataclass_replace
from enum import Enum, StrEnum
from pathlib import Path

import core.log
import core.serde
from agent.diagnosis import Diagnosis
from git.land import CommitStatus
import pr.comments
import pr.comments_fix
import pr.context
import pr.fix_state
import pr.state
import pr.triage_round
from pr.fix import FixOutcome, FixRecord, ItemOutcome
from pr.thread_models import (
    CommentItem, ReplyOutcome, TrackingResult, TriageResult,
)


CHECKPOINT_FILENAME = "fix-checkpoint.json"
CHECKPOINT_VERSION = 1

INTERRUPTED_REASON = "interrupted: the fix pass did not finish this item"
UNLANDED_REASON = "interrupted: the fix was not committed"


class CheckpointPhase(StrEnum):
    """How far a comments-fix pass got before it stopped.

    Declaration order is progress order. `reached` compares against that, and
    `terminal` is a completed or already-adopted pass that must not be resumed.
    """

    TRIAGED = "triaged"
    ADDRESSED = "addressed"
    REPLIED = "replied"
    AGENT_RUNNING = "agent_running"
    AGENT_DONE = "agent_done"
    VERIFIED = "verified"
    CHECKED = "checked"
    LANDED = "landed"
    COMPLETE = "complete"
    ADOPTED = "adopted"

    def reached(self, phase: CheckpointPhase) -> bool:
        """Whether this phase is *phase* or one that follows it."""
        return _PHASE_ORDER[self] >= _PHASE_ORDER[phase]

    @property
    def terminal(self) -> bool:
        """Whether a later run must not resume this checkpoint."""
        return self in _TERMINAL


_PHASE_ORDER = {phase: n for n, phase in enumerate(CheckpointPhase)}
_TERMINAL = frozenset({CheckpointPhase.COMPLETE, CheckpointPhase.ADOPTED})


@dataclass
class EngineProgress:
    """What the fix engine had recorded when the checkpoint was written.

    `suite` is an opaque dict so this layer does not import `fix.suite`; a
    later resume reconstructs the typed result from it. `answers` are the
    outcomes the agent had ticked so far, which a partial summary folds in.

    Identity and backup fields (`head_before`, `branch`, `dirty_before`,
    `baseline_ref`, `backup_dir`, `backed_up`) are what a resumed engine pass
    reuses instead of taking a new baseline. `file_shas` is path → blob sha
    for the agent's files; `agent_files` is those paths in sorted order.
    """

    answers: list[ItemOutcome] = field(default_factory=list)
    agent_files: list[str] = field(default_factory=list)
    file_shas: dict[str, str] = field(default_factory=dict)
    suite: dict = field(default_factory=dict)
    diagnosis: Diagnosis | None = None
    commit_sha: str = ""
    commit_status: CommitStatus | None = None
    land_error: str = ""
    head_before: str = ""
    branch: str = ""
    dirty_before: list[str] = field(default_factory=list)
    baseline_ref: str = ""
    backup_dir: str = ""
    backed_up: list[str] = field(default_factory=list)
    batches: int = 0
    max_turns: int = 0
    max_budget: float = 0.0
    exit_code: int = 0


@dataclass
class CommentsFixCheckpoint:
    """The whole of ``fix-checkpoint.json``.

    Identity fields (`repo`, `pr`, `branch`) are what `adopt_partial` matches
    against the live context, so a leftover file from another PR is ignored
    rather than merged. `dirty_before` / `head_before` are the pass's original
    baselines, reused on resume so the agent's uncommitted edits stay
    committable.
    """

    version: int = 0
    phase: CheckpointPhase = CheckpointPhase.TRIAGED
    repo: str = ""
    pr: int = 0
    branch: str = ""
    updated_at: str = ""
    head_sha: str = ""
    head_before: str = ""
    dirty_before: list[str] = field(default_factory=list)
    baseline_ref: str = ""
    triage: TriageResult = field(default_factory=TriageResult)
    outcomes: list[ItemOutcome] = field(default_factory=list)
    replies: ReplyOutcome = field(default_factory=ReplyOutcome)
    engine: EngineProgress = field(default_factory=EngineProgress)
    agent_files: list[str] = field(default_factory=list)
    seen_issue_comments: dict[int, str] = field(default_factory=dict)
    seen_review_body_comments: dict[int, str] = field(default_factory=dict)
    posted_reply_ids: list[str] = field(default_factory=list)


def checkpoint_path(target_dir: Path) -> Path:
    """Where the comments-fix checkpoint sits for this run's target."""
    return pr.comments.artifacts_dir(target_dir) / CHECKPOINT_FILENAME


def load(target_dir: Path) -> CommentsFixCheckpoint | None:
    """The recorded checkpoint, or None when missing, corrupt, or the wrong version.

    A version mismatch is not corruption: it is a schema this code does not
    read, so the file is ignored rather than warned about. An unreadable file
    is warned and discarded, matching `serde.load_file`.
    """
    path = checkpoint_path(target_dir)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        core.log.warn(f"{path} is unreadable — discarding it")
        return None
    if not isinstance(raw, dict) or raw.get("version") != CHECKPOINT_VERSION:
        return None
    try:
        return core.serde.from_dict(CommentsFixCheckpoint, raw)
    except (TypeError, ValueError):
        core.log.warn(f"{path} is unreadable — discarding it")
        return None


def save(target_dir: Path, ckpt: CommentsFixCheckpoint) -> bool:
    """Write *ckpt* atomically, stamping `updated_at`. Never raises.

    False when the write cannot land; the previous file is left intact because
    `serde.write_json` renames over it only after the temp file is complete.
    """
    path = checkpoint_path(target_dir)
    stamped = dataclass_replace(ckpt, updated_at=pr.state.now_iso())
    try:
        core.serde.write_json(path, _jsonable(core.serde.to_dict(stamped)))
        return True
    except Exception as exc:
        core.log.warn(f"{path} could not be written: {exc}")
        return False


def discard(target_dir: Path) -> None:
    """Remove the checkpoint if it is there. Missing is a no-op."""
    checkpoint_path(target_dir).unlink(missing_ok=True)


def start(ctx: pr.context.ResolvedContext) -> CommentsFixCheckpoint:
    """A fresh TRIAGED checkpoint for *ctx*, written to disk."""
    ckpt = CommentsFixCheckpoint(
        version=CHECKPOINT_VERSION,
        phase=CheckpointPhase.TRIAGED,
        repo=ctx.repo,
        pr=ctx.pr_number or 0,
        branch=ctx.branch,
        head_sha=ctx.head_sha,
    )
    save(ctx.target_dir, ckpt)
    return ckpt


def advance(
    ckpt: CommentsFixCheckpoint, phase: CheckpointPhase,
) -> CommentsFixCheckpoint:
    """*ckpt* with *phase*, or *ckpt* unchanged when *phase* would move backwards."""
    if _PHASE_ORDER[phase] < _PHASE_ORDER[ckpt.phase]:
        return ckpt
    return dataclass_replace(ckpt, phase=phase)


def partial_summary(
    ckpt: CommentsFixCheckpoint,
    answers: Sequence[ItemOutcome] = (),
) -> pr.comments_fix.FixSummary:
    """A fix snapshot from an interrupted pass, with no publishing holds placed.

    Unanswered fixable items become `NEEDS_HUMAN` with `INTERRUPTED_REASON`.
    A `FIXED` outcome recorded before `LANDED` becomes `NEEDS_HUMAN` with
    `UNLANDED_REASON`. Dismissed and already-addressed triage verdicts are
    kept. Caller *answers* overlay checkpointed outcomes by id when they are
    ticked and not `DEFERRED`.
    """
    threads = pr.triage_round.classify_entries(ckpt.triage.threads, trail=None)
    items = pr.triage_round.classify_entries(ckpt.triage.comment_items, trail=None)
    round_ = pr.triage_round.TriagedRound(threads=threads, items=items)
    folded = _rewrite_for_partial(
        _fold_answers(ckpt.outcomes, ckpt.engine.answers, answers),
        ckpt.phase,
        {e.id for e in round_.fixable + round_.fixable_items if e.id},
    )
    round_ = pr.triage_round.settle_addressed(round_, folded)
    tracking = TrackingResult.from_outcomes(
        folded, round_.fixable, round_.fixable_items,
    )
    _add_unanswered(tracking, round_, folded)
    by_outcome = round_.by_outcome(tracking)
    landed = ckpt.phase.reached(CheckpointPhase.LANDED)
    record = pr.fix_state.fix_record_for(
        by_outcome,
        commit_sha=ckpt.engine.commit_sha if landed else "",
        commit_status=ckpt.engine.commit_status if landed else None,
        head_sha=ckpt.head_sha,
    )
    summary = pr.comments_fix.FixSummary(
        reviewers=pr.fix_state.reviewers_for(by_outcome),
        has_comment_items=round_.has_items,
        updated_at=pr.state.now_iso(),
        fix=record,
    )
    summary.rearm_closeout()
    return summary


def adopt_partial(
    ctx: pr.context.ResolvedContext,
    state: pr.state.PRState | None,
    answers: Sequence[ItemOutcome] = (),
) -> pr.state.PRState | None:
    """Merge a non-terminal matching checkpoint into *state* and mark it adopted.

    Returns *state* unchanged when there is no checkpoint, it is terminal, or
    its (repo, pr, branch) do not match *ctx*. A missing *state* is created.
    Prior `commit_sha` / `commit_status` on `state.fix.fix` are kept when the
    partial snapshot has none — `FixRecord.merge_into` would otherwise replace
    them with empty. The checkpoint is marked `ADOPTED` only after the state
    write lands.
    """
    ckpt = load(ctx.target_dir)
    if ckpt is None or ckpt.phase.terminal or not _matches(ckpt, ctx):
        return state
    summary = partial_summary(ckpt, answers=answers)
    if state is not None:
        summary = _keep_prior_commit(summary, state.fix.fix)
        st = state
    else:
        st = pr.state.load_or_init(
            target_dir=ctx.target_dir,
            repo=ctx.repo,
            branch=ctx.branch,
            pr_number=ctx.pr_number,
            head_sha=ctx.head_sha,
            worktree_root=str(ctx.worktree_root or ""),
        )
    pr.state.apply(st, summary)
    pr.state.save_state(ctx.target_dir, st)
    phase = ckpt.phase
    save(ctx.target_dir, advance(ckpt, CheckpointPhase.ADOPTED))
    _warn_adopted(phase, ckpt)
    return st


def _matches(ckpt: CommentsFixCheckpoint, ctx: pr.context.ResolvedContext) -> bool:
    return (
        ckpt.repo == ctx.repo
        and ckpt.branch == ctx.branch
        and ckpt.pr == (ctx.pr_number or 0)
    )


def _keep_prior_commit(
    summary: pr.comments_fix.FixSummary, prior: FixRecord,
) -> pr.comments_fix.FixSummary:
    if summary.fix.commit_sha:
        return summary
    return dataclass_replace(
        summary,
        fix=dataclass_replace(
            summary.fix,
            commit_sha=prior.commit_sha,
            commit_status=(
                summary.fix.commit_status
                if summary.fix.commit_status is not None
                else prior.commit_status
            ),
        ),
    )


def _fold_answers(
    outcomes: Sequence[ItemOutcome],
    engine: Sequence[ItemOutcome],
    answers: Sequence[ItemOutcome],
) -> list[ItemOutcome]:
    """Overlay later non-DEFERRED answers onto earlier ones, keyed by id."""
    by_id = {o.id: o for o in outcomes if o.id}
    anonymous = [o for o in outcomes if not o.id]
    for group in (engine, answers):
        for answer in group:
            if not answer.id or answer.outcome is FixOutcome.DEFERRED:
                continue
            by_id[answer.id] = answer
    return list(by_id.values()) + anonymous


def _rewrite_for_partial(
    outcomes: list[ItemOutcome],
    phase: CheckpointPhase,
    fixable_ids: set[str],
) -> list[ItemOutcome]:
    rewritten: list[ItemOutcome] = []
    landed = phase.reached(CheckpointPhase.LANDED)
    for outcome in outcomes:
        if outcome.outcome is FixOutcome.FIXED and not landed:
            rewritten.append(dataclass_replace(
                outcome, outcome=FixOutcome.NEEDS_HUMAN, reason=UNLANDED_REASON,
            ))
        elif (
            outcome.outcome is FixOutcome.DEFERRED
            and outcome.id in fixable_ids
        ):
            rewritten.append(dataclass_replace(
                outcome, outcome=FixOutcome.NEEDS_HUMAN, reason=INTERRUPTED_REASON,
            ))
        else:
            rewritten.append(outcome)
    return rewritten


def _add_unanswered(
    tracking: TrackingResult,
    round_: pr.triage_round.TriagedRound,
    outcomes: Sequence[ItemOutcome],
) -> None:
    answered = {o.id for o in outcomes if o.id}
    thread_ids = {e.id for e in round_.fixable}
    for entry in round_.fixable + round_.fixable_items:
        if not entry.id or entry.id in answered:
            continue
        tracking.add(
            FixOutcome.NEEDS_HUMAN,
            dataclass_replace(entry, reason=INTERRUPTED_REASON),
            item=entry.id not in thread_ids,
        )


def _warn_adopted(phase: CheckpointPhase, ckpt: CommentsFixCheckpoint) -> None:
    core.log.warn(
        f"Adopted a partial comments fix snapshot at phase {phase.value}; "
        f"it cannot be resumed"
    )
    files = ckpt.agent_files or ckpt.engine.agent_files
    if files:
        core.log.warn(
            "Uncommitted edits from the interrupted pass: " + ", ".join(files)
        )


def _jsonable(value):
    """JSON-safe form of a serde dict: leftover enums inside tuples become values.

    `serde.to_dict` converts an enum field, but a tuple of enums (ReplyOutcome
    `.resolved`) reaches `json.dump` as enums and is rejected there.
    """
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value
