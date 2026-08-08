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
from pathlib import Path

import log
import serde
from review_agent import _parse_session_cost
from review_common import (
    FILENAME_GROUP_LOG, FILENAME_HOLISTIC_LOG, FILENAME_PIPELINE_STATE,
    META_STATUS,
    Diagnosis,
    _derive_path,
    hydrate_failures,
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
    path = Path(_pipeline_state_path(job))
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        groups_failed = hydrate_failures(data.get("groups_failed", {}))
        return PipelineState(
            head_sha=data["head_sha"],
            group_names=data["group_names"],
            holistic_done=data.get("holistic_done", False),
            groups_done=data.get("groups_done", []),
            groups_failed=groups_failed,
            synthesis_done=data.get("synthesis_done", False),
            synthesis_failed=data.get("synthesis_failed", ""),
            review_type=data.get("review_type", "full"),
            prior_sha=data.get("prior_sha", ""),
            skipped_groups=data.get("skipped_groups", []),
            angles_done=data.get("angles_done", False),
        )
    except (json.JSONDecodeError, KeyError):
        return None


def _sum_existing_costs(job: ReviewJob, state: PipelineState) -> float:
    total = 0.0
    if state.holistic_done:
        log = _derive_path(job.review_file, FILENAME_HOLISTIC_LOG)
        total += _parse_session_cost(log)
    for idx in state.groups_done:
        log = _derive_path(job.review_file, FILENAME_GROUP_LOG.format(idx))
        total += _parse_session_cost(log)
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


def build_failures_section(
    state: "PipelineState", groups: "list[Group]",
) -> str:
    """Build a markdown Agent Failures section from pipeline state."""
    rows: list[tuple[str, str, str]] = []

    for idx, diagnosis in sorted(state.groups_failed.items(), key=lambda x: int(x[0])):
        idx = int(idx) if isinstance(idx, str) else idx
        name = state.group_names[idx - 1] if idx <= len(state.group_names) else f"group-{idx}"
        rows.append((f"group-{idx}: {name}", diagnosis.message, "failed"))

    if state.synthesis_failed:
        status = "fallback" if state.synthesis_failed == "mechanical fallback" else "failed"
        rows.append(("synthesis", state.synthesis_failed, status))

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
    # Synthesis carries no diagnosis — its failures are pipeline outcomes and a
    # re-run can always do better, so it never suppresses the hint.
    if state.synthesis_failed:
        recoverable.append(True)
    if any(recoverable):
        lines.append("")
        lines.append("Run `pr review --recover` to retry failed agents.")

    return "\n".join(lines) + "\n"


def _inject_failures_and_status(
    review_file: str, state: "PipelineState", groups: "list[Group]",
) -> None:
    """Insert Agent Failures section and status metadata into an existing review."""
    path = Path(review_file)
    if not path.exists():
        return
    content = path.read_text()

    status = read_pipeline_status(path.parent)

    failures = build_failures_section(state, groups)
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


def _resolve_recovery(
    job: ReviewJob, groups: list[Group],
) -> tuple[float, "set[int] | None", bool, "PipelineState | None"]:
    state = _read_pipeline_state(job)
    if not state:
        return 0.0, None, False, None
    if not _validate_resume_state(state, job.pr.head_sha, groups):
        log.warn("Pipeline state is stale (SHA or groups changed) — starting fresh")
        Path(_pipeline_state_path(job)).unlink(missing_ok=True)
        return 0.0, None, False, None

    has_failed_groups = bool(state.groups_failed)
    has_failed_synthesis = bool(state.synthesis_failed)
    is_complete = state.synthesis_done

    if is_complete and not has_failed_groups and not has_failed_synthesis:
        log.info("Prior review completed successfully — nothing to recover")
        return 0.0, None, False, None

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
            state.synthesis_failed = ""
            log.info("  Re-running synthesis")
        return cost_so_far, skip_groups, state.holistic_done, state

    # Incomplete pipeline — resume from where it left off
    log.info("Resuming incomplete pipeline")
    skip_holistic = state.holistic_done
    skip_groups = set(state.groups_done) if state.groups_done else None
    return cost_so_far, skip_groups, skip_holistic, state
