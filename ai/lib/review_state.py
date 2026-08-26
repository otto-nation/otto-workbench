"""Pipeline run state for the review pipeline.

The multi-phase pipeline writes a `pipeline.json` sidecar as it goes so a
crashed run can be resumed rather than repeated. Everything that reads, writes,
validates or renders that state lives here: `PipelineState` itself, the
persistence around it, the facts other layers ask of it (`read_pipeline_status`,
`read_pipeline_warnings`, `build_failure_detail`), the resume decision
(`_resolve_recovery`), and the Agent Failures table the state feeds into the
review document.

Kept apart from the phases so the state is describable without running one —
the recovery path, the tests and the phase executors all reach the same
functions.
"""

# doc-group: pipeline

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

import log
import serde
from agent_diagnosis import Diagnosis, DiagnosisKind
from agent_types import Phase
from pr_domains import ReviewStatus
from review_agent import _parse_session_cost
from review_common import (
    FILENAME_PIPELINE_STATE,
    META_STATUS,
    ReviewType,
    _derive_path,
    phase_log_path,
    plural,
)
from review_preflight import Group, ReviewJob

_state_lock = threading.Lock()


# ── The state file ───────────────────────────────────────────────────────────


@dataclass
class PipelineState:
    """The `pipeline.json` sidecar, and the only thing that knows its schema.

    Every reader goes through `load`, so the field names and defaults declared
    here are the ones on disk. Facts derived from those fields — the status
    verdict, whether every group failed, what to call a group — are answered by
    this class rather than recomputed by each caller, which is how the two
    copies of the all-failed rule came to disagree.
    """

    # Every field carries a default because a state file on disk may predate the
    # field. An absent `head_sha` reads as "" and so never matches the run being
    # resumed, which is the right answer for state written before SHA tracking.
    head_sha: str = ""
    group_names: list[str] = field(default_factory=list)
    holistic_done: bool = False
    groups_done: list[int] = field(default_factory=list)
    groups_failed: dict[int, Diagnosis] = field(default_factory=dict)
    synthesis_done: bool = False
    # Synthesis's failures are diagnoses like any other, just reached without a
    # session log — see the pipeline-outcome kinds on `DiagnosisKind`.
    synthesis_failed: Diagnosis | None = None
    review_type: ReviewType = ReviewType.FULL
    prior_sha: str = ""
    skipped_groups: list[int] = field(default_factory=list)

    @classmethod
    def load(cls, review_dir: Path | None) -> "PipelineState | None":
        """Read a review directory's state file, or None if there isn't a usable one.

        A missing file and an unreadable one both come back as None. The one
        caller that must tell those apart is `--recover`, which stats the file
        first so it can say "nothing to recover" rather than "state is corrupt".
        """
        if not review_dir:
            return None
        return serde.load_file(cls, review_dir / FILENAME_PIPELINE_STATE)

    @property
    def group_count(self):
        return len(self.group_names)

    def group_label(self, idx: int) -> str:
        """A group's name by its 1-based index, falling back to its number."""
        if 1 <= idx <= len(self.group_names):
            return self.group_names[idx - 1]
        return f"group-{idx}"

    @property
    def all_groups_failed(self) -> bool:
        """Whether the run produced no usable group output at all."""
        if self.synthesis_failed and self.synthesis_failed.kind is DiagnosisKind.ALL_GROUPS_FAILED:
            return True
        return bool(self.groups_failed) and len(self.groups_failed) >= self.group_count > 0

    @property
    def status(self) -> ReviewStatus:
        """The verdict this state implies for the review it describes."""
        if not self.groups_failed and not self.synthesis_failed:
            return ReviewStatus.COMPLETED
        if self.all_groups_failed:
            return ReviewStatus.ERROR
        return ReviewStatus.PARTIAL

    @property
    def warnings(self) -> list[str]:
        """Human-readable notes about phases that did not complete."""
        notes = []
        if not self.holistic_done and not self.synthesis_done:
            notes.append("holistic phase")
        if self.groups_failed:
            n = len(self.groups_failed)
            notes.append(f"{n} group{plural(n)} failed")
        if self.synthesis_failed:
            notes.append("synthesis")
        return notes


# ── What a review directory's state says ─────────────────────────────────────


def read_pipeline_status(review_dir: Path | None) -> str:
    """Derive review status from pipeline state.

    complete — all phases succeeded, no failures
    partial  — review produced but with failures (groups or synthesis fallback)
    error    — all groups failed, no usable output
    """
    state = PipelineState.load(review_dir)
    if state is None:
        return ReviewStatus.COMPLETED.value
    return state.status.value


def read_pipeline_warnings(review_dir: Path | None) -> list[str]:
    """Return human-readable warnings for incomplete pipeline phases."""
    state = PipelineState.load(review_dir)
    if state is None:
        return []
    return state.warnings


def build_failure_detail(review_dir: Path | None) -> str:
    """Build a human-readable failure detail string from pipeline state."""
    state = PipelineState.load(review_dir)
    if state is None:
        return ""
    if not state.groups_failed and not state.synthesis_failed:
        return ""

    parts = []
    if state.groups_failed:
        reasons = ", ".join(sorted({d.message for d in state.groups_failed.values()}))
        if state.all_groups_failed:
            parts.append(f"all groups failed: {reasons}")
        else:
            n_failed, n_total = len(state.groups_failed), state.group_count
            parts.append(f"{n_failed}/{n_total} groups failed: {reasons}")

    # ALL_GROUPS_FAILED restates what the groups line already said.
    if state.synthesis_failed and state.synthesis_failed.kind is not DiagnosisKind.ALL_GROUPS_FAILED:
        parts.append(f"synthesis: {state.synthesis_failed.message}")

    return "; ".join(parts)


# ── Persistence and recovery ─────────────────────────────────────────────────


def _pipeline_state_path(job: ReviewJob) -> str:
    return _derive_path(job.review_file, FILENAME_PIPELINE_STATE)


def _write_pipeline_state(job: ReviewJob, state: PipelineState):
    serde.write_json(Path(_pipeline_state_path(job)), serde.to_dict(state))


def _read_pipeline_state(job: ReviewJob) -> "PipelineState | None":
    return PipelineState.load(Path(_pipeline_state_path(job)).parent)


def _sum_existing_costs(job: ReviewJob, state: PipelineState) -> float:
    """What the prior attempt spent, read from the session logs it left behind.

    Derived from the logs rather than from the state flags, because a flag can
    disagree with them in both directions. `holistic_done` means "phase 1 is
    finished" — the scout branch sets it too, and then there is no
    `holistic.jsonl` to find — while a phase that crashed mid-flight spent what
    its log records and set no flag at all. A phase that never ran leaves no
    log, and `_parse_session_cost` reads a missing file as zero, so listing
    every log a pipeline run can write needs no guard.

    Every such log is listed. Both phase-1 scans appear because a run resumed at
    a different effort can leave one of each, and a re-run overwrites its own
    log, so nothing here is counted twice. `Phase.SINGLE` names no log and
    `Phase.FIX` belongs to a separate `--fix` pass, so neither is a pipeline
    cost.
    """
    logs = [(Phase.HOLISTIC, None), (Phase.SCOUT, None)]
    logs += [(Phase.GROUP, idx) for idx in range(1, state.group_count + 1)]
    logs += [(Phase.SYNTHESIS, None), (Phase.DISPROVE, None)]
    return sum(
        _parse_session_cost(phase_log_path(job.review_file, phase, idx))
        for phase, idx in logs
    )


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
