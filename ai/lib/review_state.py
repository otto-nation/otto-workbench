"""Pipeline run state for the review pipeline.

The multi-phase pipeline writes a `pipeline-state.json` sidecar as it goes so a
crashed run can be resumed rather than repeated. Everything that reads, writes,
validates or renders that state lives here: the persistence itself, the resume
decision (`_resolve_recovery`), and the Agent Failures table the state feeds
into the review document.

Kept apart from the phases so the state is describable without running one —
the recovery path, the tests and the phase executors all reach the same
functions.
"""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path

import log
import serde
from review_agent import _parse_session_cost
from review_common import (
    FILENAME_GROUP_LOG, FILENAME_HOLISTIC_LOG, FILENAME_PIPELINE_STATE,
    META_STATUS,
    Diagnosis, DiagnosisKind,
    _derive_path,
    read_pipeline_status,
)
from review_preflight import Group, PipelineState, ReviewJob

_state_lock = threading.Lock()


def _pipeline_state_path(job: ReviewJob) -> str:
    return _derive_path(job.review_file, FILENAME_PIPELINE_STATE)


def _write_pipeline_state(job: ReviewJob, state: PipelineState):
    dest = _pipeline_state_path(job)
    tmp = dest + ".tmp"
    Path(tmp).write_text(json.dumps(serde.to_dict(state)))
    os.replace(tmp, dest)


def _read_pipeline_state(job: ReviewJob) -> "PipelineState | None":
    return PipelineState.load(Path(_pipeline_state_path(job)).parent)


def _sum_existing_costs(job: ReviewJob, state: PipelineState) -> float:
    total = 0.0
    if state.holistic_done:
        log_path = _derive_path(job.review_file, FILENAME_HOLISTIC_LOG)
        total += _parse_session_cost(log_path)
    for idx in state.groups_done:
        log_path = _derive_path(job.review_file, FILENAME_GROUP_LOG.format(idx))
        total += _parse_session_cost(log_path)
    return total


def _validate_resume_state(
    state: PipelineState, head_sha: str, groups: list[Group],
) -> bool:
    if state.head_sha != head_sha:
        return False
    current_names = [g.name for g in groups]
    if state.group_names != current_names:
        return False
    return True


def _update_group_done(job: ReviewJob, group_idx: int, state: PipelineState):
    with _state_lock:
        if group_idx not in state.groups_done:
            state.groups_done.append(group_idx)
            state.groups_done.sort()
        # Clear stale failure entry if this group succeeded on retry
        state.groups_failed.pop(group_idx, None)
        _write_pipeline_state(job, state)


def _update_group_failed(
    job: ReviewJob, group_idx: int, diagnosis: Diagnosis, state: PipelineState,
):
    with _state_lock:
        state.groups_failed[group_idx] = diagnosis
        _write_pipeline_state(job, state)


def build_failures_section(state: "PipelineState") -> str:
    """Build a markdown Agent Failures section from pipeline state."""
    rows: list[tuple[str, str, str]] = []

    for idx, diagnosis in sorted(state.groups_failed.items()):
        rows.append((f"group-{idx}: {state.group_label(idx)}", diagnosis.message, "failed"))

    if state.synthesis_failed:
        fell_back = state.synthesis_failed.kind is DiagnosisKind.MECHANICAL_FALLBACK
        rows.append((
            "synthesis",
            state.synthesis_failed.message,
            "fallback" if fell_back else "failed",
        ))

    if not rows:
        return ""

    lines = [
        "## Agent Failures",
        "",
        "| Agent | Reason | Status |",
        "|-------|--------|--------|",
    ]
    for agent, reason, status in rows:
        lines.append(f"| {agent} | {reason} | {status} |")

    recoverable = [d.recoverable for d in state.groups_failed.values()]
    if state.synthesis_failed:
        recoverable.append(state.synthesis_failed.recoverable)
    if any(recoverable):
        lines.append("")
        lines.append("Run `pr review --recover` to retry failed agents.")

    return "\n".join(lines) + "\n"


def _inject_failures_and_status(review_file: str, state: "PipelineState") -> None:
    """Insert Agent Failures section and status metadata into an existing review."""
    path = Path(review_file)
    if not path.exists():
        return
    content = path.read_text()

    status = read_pipeline_status(path.parent)

    failures = build_failures_section(state)
    if failures and "## Agent Failures" not in content:
        content = content.replace("## Summary", f"{failures}\n## Summary", 1)

    status_line = META_STATUS.format(status=status)
    if "<!-- status:" in content:
        # Replace existing status line — may be stale (e.g. completed written before
        # synthesis_failed was set, now needs to become partial).
        content = re.sub(r"<!-- status: [^>]+ -->", status_line, content, count=1)
    else:
        content = content.replace("<!-- generator:", f"{status_line}\n<!-- generator:", 1)
        if status_line not in content:
            # generator line not found — insert before first ## heading
            content = content.replace("## ", f"{status_line}\n\n## ", 1)

    path.write_text(content)


@dataclass
class RecoveryPlan:
    """What a new run should reuse from the prior attempt in this review directory.

    A `state` of None means there is nothing to resume, which happens two ways:
    no prior run, or one whose state no longer describes this branch. A prior run
    that finished cleanly is the third case and needs telling apart from those,
    because the caller aborts on it rather than starting over — that is what
    `already_complete` is for.
    """

    state: "PipelineState | None" = None
    cost_so_far: float = 0.0
    # None is not the empty set. Empty says "resume, skipping nothing"; None says
    # "no resume opinion", which lets the caller substitute its own incremental
    # skips rather than merge with a set that was never a decision.
    skip_groups: "set[int] | None" = None
    skip_holistic: bool = False
    already_complete: bool = False


def _resolve_recovery(job: ReviewJob, groups: list[Group]) -> RecoveryPlan:
    state = _read_pipeline_state(job)
    if not state:
        return RecoveryPlan()
    if not _validate_resume_state(state, job.pr.head_sha, groups):
        log.warn("Pipeline state is stale (SHA or groups changed) — starting fresh")
        Path(_pipeline_state_path(job)).unlink(missing_ok=True)
        return RecoveryPlan()

    has_failed_groups = bool(state.groups_failed)
    has_failed_synthesis = bool(state.synthesis_failed)
    is_complete = state.synthesis_done

    if is_complete and not has_failed_groups and not has_failed_synthesis:
        log.info("Prior review completed successfully — nothing to recover")
        return RecoveryPlan(already_complete=True)

    cost_so_far = _sum_existing_costs(job, state)

    if is_complete and (has_failed_groups or has_failed_synthesis):
        log.info("Prior review had failures — recovering")
        skip_groups = set(state.groups_done)
        if has_failed_groups:
            failed_count = len(state.groups_failed)
            state.groups_failed.clear()
            log.info(f"  Re-running {failed_count} failed groups")
        if has_failed_synthesis:
            state.synthesis_done = False
            state.synthesis_failed = None
            log.info("  Re-running synthesis")
        return RecoveryPlan(
            state=state, cost_so_far=cost_so_far, skip_groups=skip_groups,
            skip_holistic=state.holistic_done,
        )

    # Incomplete pipeline — resume from where it left off
    log.info("Resuming incomplete pipeline")
    return RecoveryPlan(
        state=state, cost_so_far=cost_so_far,
        skip_groups=set(state.groups_done) if state.groups_done else None,
        skip_holistic=state.holistic_done,
    )
