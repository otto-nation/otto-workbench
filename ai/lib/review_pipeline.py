"""Pipeline orchestration for claude-review.

Handles single-phase and multi-phase review pipelines, pipeline state
persistence for recovery, phase functions (holistic, group, synthesis),
and review post-processing.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

import agent_retry
import log
import serde
from review_common import (
    FILE_STAT_FMT, count_severity,
    FILENAME_DISPROVE, FILENAME_DISPROVE_LOG,
    FILENAME_FIX_LOG,
    FILENAME_GROUP, FILENAME_GROUP_LOG, FILENAME_HOLISTIC,
    FILENAME_HOLISTIC_LOG, FILENAME_META, FILENAME_PIPELINE_STATE,
    FILENAME_PROMPT_STATS, FILENAME_SCOUT, FILENAME_SCOUT_LOG,
    FILENAME_SYNTHESIS_LOG,
    META_DATE, META_DELTA_FILES, META_GENERATOR, META_HEAD_SHA,
    META_PRIOR_DATE, META_PRIOR_SHA, META_REVIEW_TYPE, META_SKIPPED_GROUPS,
    META_STATUS,
    AgentKind, Diagnosis, DiagnosisKind, Effort, Mode, Phase, Thinking,
    PRIOR_DATE_RE,
    TEMPLATE_DISPROVE, TEMPLATE_FIX,
    TEMPLATE_GROUP, TEMPLATE_HOLISTIC, TEMPLATE_SCOUT, TEMPLATE_SELF_REVIEW,
    TEMPLATE_SELF_SYNTHESIS, TEMPLATE_SINGLE, TEMPLATE_SYNTHESIS,
    _derive_path,
    has_uncommitted_changes,
    hydrate_failures,
    preserve_log, restore_preserved,
    read_pipeline_status,
)
from review_findings import (
    Finding,
    _MECHANICAL_NOTE,
    _count_findings, _has_findings,
    _validate_group_output,
    annotate_prior_with_stable_ids,
    build_mechanical_review,
    extract_skip_reasons,
    merge_reviews, parse_findings, post_process_findings,
)
from review_github import PRData, fetch_pr_data
from review_preflight import (
    DEFAULT_MAX_PARALLEL, FALLBACK_SUMMARY,
    GROUP_TIER3, HOLISTIC_MIN_GROUPS,
    Group, PRContext, PRMetadata, PipelineState, ReviewJob,
    _merge_smallest_groups,
    fetch_branch_metadata, fetch_pr_context, fetch_pr_metadata,
    group_files,
)
from review_prompt import (
    _is_incremental, _scope_prior_review,
    build_prompt,
)
from review_disprove import apply_disprove_results, parse_disprove_output
from review_scout import format_leads_block, parse_scout_output
from review_agent import (
    CONSECUTIVE_FAIL_THRESHOLD, DEFAULT_MAX_BUDGET_PER_AGENT,
    AgentInvocation, _is_model_error, _parse_session_cost, _resolve_model,
    _resolve_provider, _resolve_thinking_level, build_add_dirs,
    diagnose_missing_output, invoke_agent, try_recover_output,
)

DEFAULT_MAX_COST = 20.0

RETRY_MAX_TURNS_GROUP = agent_retry.RETRY_MAX_TURNS

DISPROVE_MIN_FINDINGS = 3
MAX_TURNS_FIX_CAP = 60
RETRY_MAX_TURNS_FIX = 40

OMITTED_FILE_TURNS = 2


# ── Phase registry ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PhaseSpec:
    """Built-in defaults for one pipeline phase.

    ``agent=None`` means the phase takes whichever agent the effort preset
    selects. A concrete ``AgentKind`` pins the phase regardless of effort:
    those phases are handed everything they need up front and do no context
    gathering, so a higher effort has nothing to buy them.

    ``edits=True`` marks a phase that writes to the branch. Every ``AgentKind``
    is a reviewer persona instructed never to modify source files, so such a
    phase runs with no agent at all — the default agent, which can edit.
    """

    phase: Phase
    model: str = "sonnet"
    thinking: Thinking | None = None
    max_turns: int = 15
    agent: AgentKind | None = None
    edits: bool = False


PHASES: dict[Phase, PhaseSpec] = {
    Phase.SINGLE: PhaseSpec(
        Phase.SINGLE, thinking=Thinking.MEDIUM, max_turns=15,
    ),
    Phase.HOLISTIC: PhaseSpec(
        Phase.HOLISTIC, thinking=Thinking.MEDIUM, max_turns=15,
    ),
    Phase.SCOUT: PhaseSpec(
        Phase.SCOUT, thinking=Thinking.LOW, max_turns=10,
        agent=AgentKind.REVIEWER_LITE,
    ),
    Phase.GROUP: PhaseSpec(
        Phase.GROUP, thinking=Thinking.LOW, max_turns=15,
        agent=AgentKind.REVIEWER_LITE,
    ),
    Phase.SYNTHESIS: PhaseSpec(
        Phase.SYNTHESIS, thinking=Thinking.MEDIUM, max_turns=15,
    ),
    Phase.DISPROVE: PhaseSpec(
        Phase.DISPROVE, thinking=Thinking.MEDIUM, max_turns=15,
        agent=AgentKind.REVIEWER_LITE,
    ),
    Phase.FIX: PhaseSpec(
        Phase.FIX, thinking=Thinking.LOW, max_turns=20,
        edits=True,
    ),
}


# ── Phase model resolution ───────────────────────────────────────────────────


def phase_model(phase: Phase, explicit: str | None) -> str:
    """Resolve the model for a pipeline phase (explicit > env > default)."""
    phase = Phase(phase)
    return _resolve_model(
        explicit,
        phase.model_env_key,
        PHASES[phase].model,
    )


def collect_phase_models(explicit: str | None) -> dict[str, list[Phase]]:
    """Map each model the pipeline would use to the phases that requested it.

    Callers use this to check every distinct model once up front and to name
    the env keys worth changing when one of them is unusable.
    """
    models: dict[str, list[Phase]] = {}
    for phase in PHASES:
        models.setdefault(phase_model(phase, explicit), []).append(phase)
    return models


# ── Effort presets ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EffortPreset:
    """Budgets, thresholds, and phase skips selected by ``--effort``.

    ``thinking=None`` means the phase's own default stands; a level here
    flattens every phase to it, matching what CLAUDE_REVIEW_THINKING does.
    """

    thinking: Thinking | None
    agent_budget: float
    max_groups: int
    multi_phase_line_threshold: int
    multi_phase_file_threshold: int
    skip_synthesis: bool
    skip_holistic: bool
    skip_scout: bool
    skip_disprove: bool
    skip_omitted_files: bool
    agent: AgentKind


EFFORT_PRESETS: dict[Effort, EffortPreset] = {
    Effort.LOW: EffortPreset(
        thinking=Thinking.LOW,
        agent_budget=3.0,
        max_groups=6,
        multi_phase_line_threshold=1000,
        multi_phase_file_threshold=15,
        skip_synthesis=True,
        skip_holistic=True,
        skip_scout=True,
        skip_disprove=True,
        skip_omitted_files=True,
        agent=AgentKind.REVIEWER_LITE,
    ),
    Effort.MEDIUM: EffortPreset(
        thinking=None,
        agent_budget=DEFAULT_MAX_BUDGET_PER_AGENT,
        max_groups=8,
        multi_phase_line_threshold=500,
        multi_phase_file_threshold=10,
        skip_synthesis=False,
        skip_holistic=False,
        skip_scout=False,
        skip_disprove=False,
        skip_omitted_files=False,
        agent=AgentKind.REVIEWER,
    ),
    Effort.HIGH: EffortPreset(
        thinking=Thinking.HIGH,
        agent_budget=8.0,
        max_groups=16,
        multi_phase_line_threshold=500,
        multi_phase_file_threshold=10,
        skip_synthesis=False,
        skip_holistic=False,
        skip_scout=False,
        skip_disprove=False,
        skip_omitted_files=False,
        agent=AgentKind.REVIEWER,
    ),
}


def _phase_thinking(effort: Effort, phase: Phase) -> Thinking | None:
    """The effort override if the preset sets one, else the phase's own default."""
    override = EFFORT_PRESETS[effort].thinking
    return override if override is not None else PHASES[phase].thinking


class PhaseRunner:
    """The six per-phase values, resolved once.

    Every phase needs the same six — model, thinking level, provider, budget,
    agent, and max turns — resolved from the phase spec, the effort preset,
    and the environment. Resolving them here means one place to read rather
    than seven blocks that must be kept in step.
    """

    def __init__(self, job: ReviewJob, phase: Phase):
        spec = PHASES[phase]
        preset = EFFORT_PRESETS[job.effort]
        self.job = job
        self.model = phase_model(phase, job.model)
        self.thinking = _resolve_thinking_level(
            None, phase.thinking_env_key, _phase_thinking(job.effort, phase),
        )
        self.provider = _resolve_provider()
        self.budget = preset.agent_budget
        self.agent = None if spec.edits else (
            spec.agent if spec.agent is not None else preset.agent
        )
        self.max_turns = spec.max_turns

    def invocation(
        self, prompt: str, session_log: str, *,
        max_turns: int | None = None, label: str = "",
    ) -> AgentInvocation:
        return AgentInvocation(
            prompt=prompt,
            session_log=session_log,
            add_dirs=build_add_dirs(self.job.wt_path, self.job.artifact_dir),
            agent=self.agent,
            max_turns=self.max_turns if max_turns is None else max_turns,
            max_budget=self.budget,
            model=self.model,
            thinking=self.thinking,
            provider=self.provider,
            label=label,
        )

    def invoke(
        self, prompt: str, session_log: str, *,
        max_turns: int | None = None, label: str = "",
    ) -> int:
        return invoke_agent(
            self.invocation(prompt, session_log, max_turns=max_turns, label=label),
            throttle=self.job.throttle,
        )


def _touch(path: str) -> None:
    """Pre-create an empty output file without truncating existing content."""
    Path(path).touch(exist_ok=True)


_has_output = agent_retry.has_output


def _omitted_turns(job: "ReviewJob") -> int:
    if EFFORT_PRESETS[job.effort].skip_omitted_files:
        return 0
    if not job.preflight or not job.preflight.omitted_files:
        return 0
    return len(job.preflight.omitted_files) * OMITTED_FILE_TURNS


def _synthesis_max_turns(merged_content: str) -> int:
    counts = _count_findings(merged_content)
    total = sum(counts.values())
    scaled = PHASES[Phase.SYNTHESIS].max_turns + max(0, total - 20) // 10
    return min(scaled, RETRY_MAX_TURNS_GROUP)


# ── Pipeline state (resume/retry) ────────────────────────────────────────────

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


# ── Review pipelines ──────────────────────────────────────────────────────────

def _write_review_sidecar(job: ReviewJob):
    sidecar_path = _derive_path(job.review_file, FILENAME_META)
    meta: dict = {
        "repo": job.repo,
        "pr_number": job.pr_number,
        "head_sha": job.pr.head_sha,
        "head_ref": job.pr.head,
        "base_ref": job.pr.base,
        "title": job.pr.title,
        "changed_files": job.pr.changed_files,
        "mode": job.mode,
    }
    if job.generator_version:
        meta["generator_version"] = job.generator_version

    incremental = _is_incremental(job)
    meta["review_type"] = "incremental" if incremental else "full"
    if incremental:
        pf = job.preflight
        meta["prior_sha"] = pf.prior_head_sha
        meta["delta_files"] = pf.delta_files
        meta["delta_file_count"] = len(pf.delta_files)

    Path(sidecar_path).write_text(json.dumps(meta))


def run_single_agent(job: ReviewJob, disprove: bool | None = None):
    template = TEMPLATE_SELF_REVIEW if job.mode == Mode.SELF else TEMPLATE_SINGLE
    max_turns = PHASES[Phase.SINGLE].max_turns + _omitted_turns(job)
    prompt = build_prompt(
        template, job, max_turns=max_turns, branch_name=job.pr.head,
    )
    label = f"branch {job.pr.head}" if job.mode == Mode.SELF else f"PR #{job.pr_number} ({job.pr.title})"
    log.info(f"Running review agent on {label}...")
    log.blank()
    _touch(job.review_file)
    runner = PhaseRunner(job, Phase.SINGLE)

    # `rc` tracks the latest attempt so the failure message below reports the
    # retry's exit code, not the first attempt's.
    rc = 0

    def invoke(text: str, turns: int) -> int:
        nonlocal rc
        rc = runner.invoke(text, job.session_log, max_turns=turns)
        return rc

    invoke(prompt, max_turns)
    log.blank()

    diagnosis = _retry_missing_output(
        invoke, prompt, job.session_log, job.review_file,
        label="Review agent", max_turns=max_turns,
    )

    if not _has_output(job.review_file):
        detail = f"exited with code {rc}" if rc != 0 else "completed"
        log.error(
            f"review agent {detail} and produced no review file "
            f"({_render_reason(diagnosis)})"
        )
        log.dim(f"Session log: {job.session_log}")
        sys.exit(1)

    if _should_disprove(job, disprove):
        _phase_disprove(job)

    _post_process_review(job)
    _write_review_sidecar(job)


# The hints, the retryability test and the retry driver are shared with the
# other `pr` scripts — see agent_retry. Aliased here so this module's internals
# keep reading the way they always have.
_RETRY_HINT = agent_retry.RETRY_HINT
_FIX_RETRY_HINT = agent_retry.FIX_RETRY_HINT
_NO_WRITE_HINT = agent_retry.NO_WRITE_HINT

_retry_hint_for = agent_retry.hint_for
_retry_turns_for = agent_retry.turns_for
_retry_missing_output = agent_retry.retry_missing_output


def _render_reason(diagnosis: "Diagnosis | None") -> str:
    """The rendered reason for a phase that produced nothing.

    A `None` only reaches here when the output disappeared after the retry
    driver had already confirmed it — worth reporting, not worth crashing on.
    """
    return diagnosis.message if diagnosis else "unknown"


@dataclass(frozen=True)
class GroupFailure:
    """One group review that produced nothing, and why.

    Carried rather than the rendered message because the retry pass, the
    consecutive-failure abort, and the circuit breaker all decide from the
    diagnosis; only the merge and the failures table render it.
    """

    group: str
    diagnosis: Diagnosis


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


def _review_group(
    i: int, grp: Group, job: ReviewJob,
    group_count: int, holistic_content: str,
    skip: bool = False,
    pipeline_state: "PipelineState | None" = None,
    max_turns: int = PHASES[Phase.GROUP].max_turns,
    retry_hint: str = "",
) -> tuple[int, str, "GroupFailure | None"]:
    group_output = _derive_path(job.review_file, FILENAME_GROUP.format(i))
    group_log = _derive_path(job.review_file, FILENAME_GROUP_LOG.format(i))

    if skip:
        if _has_output(group_output):
            log.info(f"Phase 2: Group {i}/{group_count} — {grp.name} skipped (exists)")
            return (i, group_output, None)
        log.warn(f"Group {i} ({grp.name}) marked skip but output missing — reporting failure")
        return (i, group_output, GroupFailure(
            grp.name, Diagnosis(DiagnosisKind.OUTPUT_MISSING),
        ))

    _touch(group_output)

    group_files_formatted = "\n".join(
        FILE_STAT_FMT.format(**f)
        for f in job.pr.files if f["path"] in grp.files
    )

    group_prompt = build_prompt(
        TEMPLATE_GROUP, job, max_turns=max_turns,
        group_idx=i, group_count=group_count, group_name=grp.name,
        group_files_formatted=group_files_formatted,
        group_file_paths=grp.files,
        group_output=group_output, holistic_content=holistic_content,
    )
    group_prompt = retry_hint + group_prompt
    runner = PhaseRunner(job, Phase.GROUP)
    log.info(f"Phase 2: Group {i}/{group_count} — {grp.name} ({grp.lines} lines)...")
    runner.invoke(group_prompt, group_log, max_turns=max_turns, label=grp.name)

    failed = None
    if not _has_output(group_output):
        try_recover_output(group_log, group_output)
    if not _has_output(group_output):
        diagnosis = diagnose_missing_output(group_log)
        log.warn(f"Group {i} ({grp.name}) produced no output ({diagnosis.message})")
        failed = GroupFailure(grp.name, diagnosis)
        if pipeline_state is not None:
            _update_group_failed(job, i, diagnosis, pipeline_state)
    else:
        _validate_group_output(group_output, grp.name)
        log.info(f"Phase 2: Group {i}/{group_count} — {grp.name} done")
        if pipeline_state is not None:
            _update_group_done(job, i, pipeline_state)

    return (i, group_output, failed)


def _phase_holistic(job: ReviewJob, group_count: int) -> tuple[str, str, str]:
    holistic_output = _derive_path(job.review_file, FILENAME_HOLISTIC)
    holistic_log = _derive_path(job.review_file, FILENAME_HOLISTIC_LOG)

    _touch(holistic_output)

    max_turns = PHASES[Phase.HOLISTIC].max_turns + _omitted_turns(job)
    prompt = build_prompt(
        TEMPLATE_HOLISTIC, job, max_turns=max_turns, holistic_output=holistic_output,
    )
    runner = PhaseRunner(job, Phase.HOLISTIC)
    log.info(f"Phase 1/{group_count}: Holistic scan...")
    log.blank()

    def invoke(text: str, turns: int) -> int:
        return runner.invoke(text, holistic_log, max_turns=turns)

    invoke(prompt, max_turns)
    log.blank()

    diagnosis = _retry_missing_output(
        invoke, prompt, holistic_log, holistic_output,
        label="Holistic scan", max_turns=max_turns,
    )

    holistic_content = ""
    if _has_output(holistic_output):
        holistic_content = Path(holistic_output).read_text()
    else:
        log.warn(
            f"Holistic scan produced no output ({_render_reason(diagnosis)}) "
            "— continuing without it"
        )

    return holistic_content, holistic_output, holistic_log


def _phase_scout(job: ReviewJob, group_count: int) -> tuple[str, str, str]:
    scout_output = _derive_path(job.review_file, FILENAME_SCOUT)
    scout_log = _derive_path(job.review_file, FILENAME_SCOUT_LOG)

    _touch(scout_output)

    max_turns = PHASES[Phase.SCOUT].max_turns + _omitted_turns(job)
    prompt = build_prompt(
        TEMPLATE_SCOUT, job, max_turns=max_turns, scout_output=scout_output,
    )
    runner = PhaseRunner(job, Phase.SCOUT)
    log.info(f"Phase 1/{group_count}: Lead scout scan...")
    log.blank()

    def invoke(text: str, turns: int) -> int:
        return runner.invoke(text, scout_log, max_turns=turns)

    invoke(prompt, max_turns)
    log.blank()

    diagnosis = _retry_missing_output(
        invoke, prompt, scout_log, scout_output,
        label="Scout", max_turns=max_turns,
    )

    if _has_output(scout_output):
        raw = Path(scout_output).read_text()
        leads, no_scrutiny = parse_scout_output(raw)
        log.info(f"Scout found {len(leads)} investigation leads, {len(no_scrutiny)} no-scrutiny files")
        return format_leads_block(leads, no_scrutiny), scout_output, scout_log

    log.warn(f"Scout produced no output ({_render_reason(diagnosis)}) — continuing without leads")
    return "", scout_output, scout_log


def _phase_disprove(job: ReviewJob) -> tuple[str, float]:
    review_content = Path(job.review_file).read_text() if Path(job.review_file).exists() else ""
    counts = _count_findings(review_content)
    ms_count = counts.get("M", 0) + counts.get("S", 0)
    if ms_count == 0:
        log.info("Disprove gate skipped — no must-fix or should-fix findings")
        return "", 0.0

    disprove_output = _derive_path(job.review_file, FILENAME_DISPROVE)
    disprove_log = _derive_path(job.review_file, FILENAME_DISPROVE_LOG)

    _touch(disprove_output)

    max_turns = PHASES[Phase.DISPROVE].max_turns
    prompt = build_prompt(
        TEMPLATE_DISPROVE, job, max_turns=max_turns,
        disprove_output=disprove_output, review_content=review_content,
    )
    runner = PhaseRunner(job, Phase.DISPROVE)
    log.info(f"Disprove gate — challenging {ms_count} must-fix/should-fix findings...")
    log.blank()

    def invoke(text: str, turns: int) -> int:
        return runner.invoke(text, disprove_log, max_turns=turns)

    invoke(prompt, max_turns)
    log.blank()

    diagnosis = _retry_missing_output(
        invoke, prompt, disprove_log, disprove_output,
        label="Disprove gate", max_turns=max_turns,
    )

    cost = _parse_session_cost(disprove_log) if disprove_log else 0.0

    if _has_output(disprove_output):
        raw = Path(disprove_output).read_text()
        results = parse_disprove_output(raw)
        updated_text, summary = apply_disprove_results(review_content, results)
        falsified = summary.get("falsified", 0)
        if falsified > 0:
            Path(job.review_file).write_text(updated_text)
            log.info(f"Disprove gate: {summary['survived']} survived, {falsified} falsified")
            _log_disprove_falsified(summary)
        else:
            log.info(f"Disprove gate: all {summary['survived']} findings survived")
    else:
        log.warn(
            f"Disprove gate produced no output ({_render_reason(diagnosis)}) "
            "— keeping all findings"
        )

    return disprove_log, cost


def _log_disprove_falsified(summary: dict) -> None:
    for fid in summary.get("falsified_ids", []):
        reason = summary.get("reasons", {}).get(fid, "")
        log.dim(f"  Falsified [{fid}]: {reason}")


def _should_disprove(job: ReviewJob, explicit_disprove: bool | None = None) -> bool:
    if explicit_disprove is True:
        return True
    if explicit_disprove is False:
        return False
    return not EFFORT_PRESETS[job.effort].skip_disprove


def _count_unchecked(review_file: str) -> int:
    """Count unchecked finding checkboxes in a review file."""
    if not Path(review_file).exists():
        return 0
    return sum(
        1 for line in Path(review_file).read_text().splitlines()
        if re.match(r"^- \[ \] ", line)
    )


def _commit_fixes(job: ReviewJob, fixed: int, skipped: int, summary: str = ""):
    """Commit source-file fixes applied by the fix-pass agent."""
    if not has_uncommitted_changes(job.wt_path):
        return

    # -A, not -u: the fix agent creates new files (tests, fixtures) that -u
    # drops from the commit while the summary still reports them as fixed.
    subprocess.run(
        ["git", "-C", job.wt_path, "add", "-A"],
        capture_output=True, check=True,
    )

    msg = "fix: self-review findings"
    if fixed:
        msg += f"\n\n{fixed} fixed, {skipped} skipped"
    if summary:
        msg += f"\n\n{summary}"

    result = subprocess.run(
        ["git", "-C", job.wt_path, "commit", "-m", msg],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        log.warn(f"Failed to commit fixes: {result.stderr.strip()}")
        return

    log.info(f"Committed fixes ({fixed} fixed, {skipped} skipped)")
    _push_fixes(job)


_DIVERGED_MARKERS = (
    "non-fast-forward",
    "fetch first",
    "updates were rejected",
    "behind its remote",
)


def _is_diverged(stderr: str) -> bool:
    """Whether a push rejection came from divergence rather than a hook."""
    lowered = stderr.lower()
    return any(marker in lowered for marker in _DIVERGED_MARKERS)


def _push_fixes(job: ReviewJob):
    """Push committed fixes to the remote."""
    result = subprocess.run(
        ["git", "-C", job.wt_path, "push"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        log.info("Pushed fixes")
        return

    stderr = result.stderr.strip()
    if _is_diverged(stderr):
        log.error(
            f"push failed — branch diverged. Run:\n"
            f"  git -C '{job.wt_path}' push --force-with-lease\n"
            f"stderr: {stderr}"
        )
        return

    log.error(
        f"push failed — fixes are committed locally but not pushed:\n"
        f"stderr: {stderr}"
    )


def _count_checked(review_file: str) -> int:
    if not Path(review_file).exists():
        return 0
    return sum(
        1 for line in Path(review_file).read_text().splitlines()
        if re.match(r"^- \[x\] ", line, re.IGNORECASE)
    )


def _changed_source_files(wt_path: str) -> set[str]:
    """Return set of changed files (staged, unstaged, and untracked)."""
    changed: set[str] = set()
    # Untracked files count: a fix that only adds a test file still fixed the
    # finding, and diff-only detection would report it as skipped.
    for args in (["diff", "HEAD", "--name-only"],
                 ["ls-files", "--others", "--exclude-standard"]):
        result = subprocess.run(
            ["git", "-C", wt_path, *args],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            continue
        changed.update(f for f in result.stdout.strip().splitlines() if f)
    return changed


def _count_changed_source_files(wt_path: str) -> int:
    return sum(
        1 for f in _changed_source_files(wt_path)
        if not f.endswith("review.md")
    )


def _count_fixed(before_unchecked: int, after_unchecked: int,
                 review_file: str, wt_path: str) -> int:
    """Count fixed findings: unchecked-delta, then checked marks, then changed files."""
    delta = before_unchecked - after_unchecked
    if delta > 0:
        return delta
    checked = _count_checked(review_file)
    if checked > 0:
        return checked
    return _count_changed_source_files(wt_path)


@dataclass
class FixPassResult:
    fixed: list[Finding]
    skipped: list[Finding]
    unchanged: list[Finding]

    @property
    def fixed_count(self) -> int:
        return len(self.fixed)

    @property
    def skipped_count(self) -> int:
        return len(self.skipped)


def _reconcile_checkboxes(review_file: str, wt_path: str) -> None:
    """Auto-check findings whose files were modified but checkboxes weren't updated.

    The fix agent sometimes edits source files without updating the review
    markdown.  This reconciles by matching changed file paths to finding paths.
    """
    changed = _changed_source_files(wt_path)
    if not changed:
        return

    text = Path(review_file).read_text()
    findings = parse_findings(text)
    updated = False
    for f in findings:
        if f.checked or not f.path:
            continue
        old = f"- [ ] **[{f.id}]**"
        new = f"- [x] **[{f.id}]**"
        if f.path in changed and old in text:
            text = text.replace(old, new, 1)
            updated = True

    if updated:
        Path(review_file).write_text(text)


def _diff_findings(before: list[Finding], after: list[Finding]) -> FixPassResult:
    """Diff findings before/after fix pass by ID to classify outcomes."""
    before_by_id = {f.id: f for f in before}
    after_by_id = {f.id: f for f in after}

    fixed: list[Finding] = []
    skipped: list[Finding] = []
    unchanged: list[Finding] = []

    for fid, bf in before_by_id.items():
        af = after_by_id.get(fid)
        if af is None:
            unchanged.append(bf)
            continue
        if not bf.checked and af.checked:
            fixed.append(af)
        elif not bf.checked and not af.checked:
            skipped.append(af)
        else:
            unchanged.append(af)

    return FixPassResult(fixed=fixed, skipped=skipped, unchanged=unchanged)


def _format_fix_summary(result: FixPassResult) -> str:
    """Format a human-readable fix summary for commit message and stderr."""
    lines: list[str] = []
    if result.fixed:
        lines.append("Fixed:")
        for f in result.fixed:
            desc = f.body.split('\n', 1)[0][:80] if f.body else f.path
            lines.append(f"  - [{f.id}] {desc}")
    if result.skipped:
        lines.append("Skipped:")
        for f in result.skipped:
            reason = f.skip_reason if f.skip_reason else "no auto-fix"
            lines.append(f"  - [{f.id}] {reason}")
    return "\n".join(lines)


def _fix_turn_budget(unchecked: int) -> int:
    return min(max(PHASES[Phase.FIX].max_turns, unchecked * 2), MAX_TURNS_FIX_CAP)


def _fix_retry_budget(original_budget: int) -> int:
    return min(max(RETRY_MAX_TURNS_FIX, original_budget + 20), MAX_TURNS_FIX_CAP)


def _fix_pass_made_progress(result: FixPassResult) -> bool:
    if result.fixed_count > 0:
        return True
    return any(f.skip_reason for f in result.skipped)


def run_fix_pass(job: ReviewJob):
    if not _has_output(job.review_file):
        log.warn("No review file to fix — skipping fix pass")
        return
    fix_log = _derive_path(job.review_file, FILENAME_FIX_LOG)

    before_text = Path(job.review_file).read_text()
    before_findings = parse_findings(before_text)
    before_unchecked = sum(1 for f in before_findings if not f.checked)

    if before_unchecked == 0:
        log.info("All findings already checked — skipping fix pass")
        return

    max_turns = _fix_turn_budget(before_unchecked)

    prompt = build_prompt(
        TEMPLATE_FIX, job, max_turns=max_turns,
    )
    runner = PhaseRunner(job, Phase.FIX)
    log.info("Fix pass — applying review findings...")
    log.blank()
    runner.invoke(prompt, fix_log, max_turns=max_turns)
    log.blank()

    _reconcile_checkboxes(job.review_file, job.wt_path)

    after_text = Path(job.review_file).read_text()
    after_findings = parse_findings(after_text)
    extract_skip_reasons(after_findings)

    result = _diff_findings(before_findings, after_findings)

    if not _fix_pass_made_progress(result) and result.skipped_count > 0:
        diagnosis = diagnose_missing_output(fix_log)
        log.warn(f"Fix pass made no progress ({diagnosis.message})")
        if _is_retryable(diagnosis):
            retry_turns = _fix_retry_budget(max_turns)
            retry_prompt = _FIX_RETRY_HINT + build_prompt(
                TEMPLATE_FIX, job, max_turns=retry_turns,
            )
            log.info(f"Retrying fix pass (max_turns={retry_turns})...")
            prior_log = preserve_log(fix_log)
            log.blank()
            runner.invoke(retry_prompt, fix_log, max_turns=retry_turns)
            restore_preserved(fix_log, prior_log)
            log.blank()
            _reconcile_checkboxes(job.review_file, job.wt_path)
            after_text = Path(job.review_file).read_text()
            after_findings = parse_findings(after_text)
            extract_skip_reasons(after_findings)
            result = _diff_findings(before_findings, after_findings)

    summary = _format_fix_summary(result)
    if summary:
        log.info("Fix summary:")
        for line in summary.splitlines():
            print(f"  {line}", file=sys.stderr)

    _commit_fixes(job, fixed=result.fixed_count, skipped=result.skipped_count,
                  summary=summary)


def _check_serial_abort(
    i: int, group_count: int, diagnosis: Diagnosis, log_path: str,
    consecutive: int, last: "Diagnosis | None",
) -> "tuple[str, int, Diagnosis | None]":
    if _is_model_error(log_path):
        return f"Model not available — aborting remaining {group_count - i} groups", 0, None
    consecutive = consecutive + 1 if diagnosis == last else 1
    if consecutive >= CONSECUTIVE_FAIL_THRESHOLD:
        return f"{CONSECUTIVE_FAIL_THRESHOLD} consecutive failures ({diagnosis.message}) — aborting remaining {group_count - i} groups", 0, None
    return "", consecutive, diagnosis


def _run_serial_reviews(
    groups: list[Group], job: ReviewJob,
    group_count: int, holistic_content: str,
    skip_groups: "set[int] | None",
    pipeline_state: "PipelineState | None",
) -> "list[GroupFailure]":
    failed_groups: list[GroupFailure] = []
    consecutive_same_reason = 0
    last: Diagnosis | None = None
    group_turns = PHASES[Phase.GROUP].max_turns + _omitted_turns(job)
    for i, grp in enumerate(groups, 1):
        skip = skip_groups is not None and i in skip_groups
        _, _, failed = _review_group(
            i, grp, job, group_count, holistic_content,
            skip=skip, pipeline_state=pipeline_state,
            max_turns=group_turns,
        )
        if not failed:
            consecutive_same_reason = 0
            last = None
            continue
        failed_groups.append(failed)
        group_log = _derive_path(job.review_file, FILENAME_GROUP_LOG.format(i))
        abort_msg, consecutive_same_reason, last = _check_serial_abort(
            i, group_count, failed.diagnosis, group_log, consecutive_same_reason, last,
        )
        if not abort_msg:
            continue
        log.warn(abort_msg)
        failed_groups.extend(
            GroupFailure(remaining.name, Diagnosis(DiagnosisKind.SKIPPED, detail=abort_msg))
            for remaining in groups[i:]
        )
        break
    return failed_groups


def _run_parallel_reviews(
    groups: list[Group], job: ReviewJob,
    group_count: int, holistic_content: str, workers: int,
    skip_groups: "set[int] | None",
    pipeline_state: "PipelineState | None",
) -> "list[GroupFailure]":
    log.info(f"Phase 2: Reviewing {group_count} groups ({workers} parallel)...")
    log.blank()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(
                _review_group, i, grp, job, group_count, holistic_content,
                skip=(skip_groups is not None and i in skip_groups),
                pipeline_state=pipeline_state,
            )
            for i, grp in enumerate(groups, 1)
        ]
        results = [f.result() for f in futures]
    log.blank()
    return [failure for _, _, failure in results if failure]


_is_retryable = agent_retry.is_retryable


def _retry_turns(diagnosis: Diagnosis, job: "ReviewJob") -> int:
    extra = _omitted_turns(job)
    if diagnosis.kind is DiagnosisKind.MAX_TURNS:
        return RETRY_MAX_TURNS_GROUP + extra
    return PHASES[Phase.GROUP].max_turns + extra


def _was_skipped(failure: "GroupFailure") -> bool:
    return failure.diagnosis.kind is DiagnosisKind.SKIPPED


def _retry_failed_groups(
    failed_groups: "list[GroupFailure]",
    groups: list[Group], job: ReviewJob,
    group_count: int, holistic_content: str,
    pipeline_state: "PipelineState | None",
) -> "list[GroupFailure]":
    retryable = [f for f in failed_groups if _is_retryable(f.diagnosis)]
    skipped = [f for f in failed_groups if _was_skipped(f)]
    non_retryable = [
        f for f in failed_groups
        if not _is_retryable(f.diagnosis) and not _was_skipped(f)
    ]
    if not retryable:
        return failed_groups

    # Circuit breaker: if all groups failed with the same reason, the cause is
    # systemic (wrong credentials, model unavailable) — retries won't help.
    if len(failed_groups) >= CONSECUTIVE_FAIL_THRESHOLD:
        reasons = {f.diagnosis for f in failed_groups if not _was_skipped(f)}
        if len(reasons) == 1:
            reason = reasons.pop()
            log.warn(f"All {len(failed_groups)} groups failed with same error ({reason.message}) — skipping retries")
            return failed_groups

    group_by_name = {g.name: (idx, g) for idx, g in enumerate(groups, 1)}

    log.info(f"Retrying {len(retryable)} failed groups...")
    still_failed: list[GroupFailure] = []
    for failed in retryable:
        name = failed.group
        if name not in group_by_name:
            still_failed.append(failed)
            continue
        idx, grp = group_by_name[name]
        turns = _retry_turns(failed.diagnosis, job)
        log.info(f"  Retry: {name} (max_turns={turns})")
        _, _, failure = _review_group(
            idx, grp, job, group_count, holistic_content,
            pipeline_state=pipeline_state,
            max_turns=turns,
            retry_hint=_retry_hint_for(failed.diagnosis),
        )
        if failure:
            still_failed.append(failure)

    if not still_failed and skipped:
        still_failed.extend(_run_skipped_groups(
            skipped, group_by_name, job, group_count,
            holistic_content, pipeline_state,
        ))
    elif skipped:
        still_failed.extend(skipped)

    return non_retryable + still_failed


def _run_skipped_groups(
    skipped: "list[GroupFailure]",
    group_by_name: dict,
    job: ReviewJob,
    group_count: int,
    holistic_content: str,
    pipeline_state: dict,
) -> "list[GroupFailure]":
    log.info(f"All retries succeeded — running {len(skipped)} previously-skipped groups...")
    failures: list[GroupFailure] = []
    for failed in skipped:
        name = failed.group
        if name not in group_by_name:
            failures.append(failed)
            continue
        idx, grp = group_by_name[name]
        log.info(f"  Running skipped group: {name}")
        _, _, failure = _review_group(
            idx, grp, job, group_count, holistic_content,
            pipeline_state=pipeline_state,
            max_turns=PHASES[Phase.GROUP].max_turns + _omitted_turns(job),
        )
        if failure:
            failures.append(failure)
    return failures


def _phase_group_reviews(
    groups: list[Group], job: ReviewJob,
    group_count: int, holistic_content: str, max_parallel: int,
    skip_groups: "set[int] | None" = None,
    pipeline_state: "PipelineState | None" = None,
) -> "tuple[list[str], list[GroupFailure]]":
    group_outputs = [_derive_path(job.review_file, FILENAME_GROUP.format(i)) for i in range(1, group_count + 1)]

    workers = min(max_parallel, group_count)
    if workers <= 1:
        failed_groups = _run_serial_reviews(
            groups, job, group_count, holistic_content, skip_groups, pipeline_state,
        )
    else:
        failed_groups = _run_parallel_reviews(
            groups, job, group_count, holistic_content, workers, skip_groups, pipeline_state,
        )

    if failed_groups:
        failed_groups = _retry_failed_groups(
            failed_groups, groups, job, group_count, holistic_content, pipeline_state,
        )

    return group_outputs, failed_groups


def _phase_merge(group_outputs: list[str], failed_groups: "list[GroupFailure]") -> str:
    log.info("Phase 3: Merging findings...")
    merged_content = merge_reviews(group_outputs)

    if failed_groups:
        merged_content += "\n## Review gaps\n"
        merged_content += "The following file groups were not reviewed due to agent failure:\n"
        for failed in failed_groups:
            merged_content += f"- {failed.group}: {failed.diagnosis.message}\n"

    return merged_content


def _build_meta_header(
    job: ReviewJob,
    skipped_groups: int = 0, total_groups: int = 0,
    status: str = "",
) -> str:
    today = date.today().isoformat()
    incremental = _is_incremental(job)
    review_type = "incremental" if incremental else "full"

    lines = [
        META_DATE.format(today=today),
        META_HEAD_SHA.format(head_sha=job.pr.head_sha),
        META_REVIEW_TYPE.format(review_type=review_type),
    ]

    if incremental:
        pf = job.preflight
        prior_date_match = PRIOR_DATE_RE.search(job.prior_review) if job.prior_review else None
        prior_date = prior_date_match.group(1) if prior_date_match else "unknown"
        lines.append(META_PRIOR_SHA.format(prior_sha=pf.prior_head_sha))
        lines.append(META_PRIOR_DATE.format(prior_date=prior_date))
        lines.append(META_DELTA_FILES.format(delta_file_count=len(pf.delta_files)))
        if total_groups > 0:
            lines.append(META_SKIPPED_GROUPS.format(
                skipped=skipped_groups, total=total_groups,
            ))

    if status:
        lines.append(META_STATUS.format(status=status))

    if job.generator_version:
        lines.append(META_GENERATOR.format(generator_version=job.generator_version))

    return "\n".join(lines) + "\n"


def _is_complete_review(review_file: str) -> bool:
    if not Path(review_file).exists():
        return False
    content = Path(review_file).read_text()
    return "## Summary" in content or "## Verdict" in content


def _build_mechanical_fallback(
    job: ReviewJob, group_count: int, merged_content: str,
    skipped_groups: int = 0,
    pipeline_state: "PipelineState | None" = None,
    groups: "list[Group] | None" = None,
) -> str:
    status = read_pipeline_status(Path(job.review_file).parent) if Path(job.review_file).parent.exists() else "error"
    meta = _build_meta_header(
        job, skipped_groups=skipped_groups, total_groups=group_count,
        status=status,
    )
    if job.mode == Mode.SELF:
        title = f"# Self-Review: {job.repo} — {job.pr.head}"
    else:
        title = f"# Review: {job.repo}#{job.pr_number} — {job.pr.title}"

    failures_section = build_failures_section(pipeline_state, groups or []) if pipeline_state else ""

    return build_mechanical_review(
        merged_content,
        title=title,
        meta_header=meta,
        group_count=group_count,
        summary_note=FALLBACK_SUMMARY,
        include_verdict=(job.mode != Mode.SELF),
        file_count=job.pr.changed_files,
        failures_section=failures_section,
    )


def _post_process_review(job: ReviewJob) -> None:
    job.verification = post_process_findings(job.review_file, job.wt_path, job.prior_review)


def _write_mechanical_fallback(
    job: ReviewJob, group_count: int, merged_content: str,
    skipped_groups: int = 0,
):
    Path(job.review_file).write_text(merged_content)
    _post_process_review(job)
    processed = Path(job.review_file).read_text()
    fallback = _build_mechanical_fallback(
        job, group_count, processed, skipped_groups=skipped_groups,
    )
    Path(job.review_file).write_text(fallback)


def _phase_synthesis(
    job: ReviewJob, holistic_content: str,
    group_count: int, merged_content: str,
    skipped_groups: int = 0,
) -> str:
    synthesis_log = _derive_path(job.review_file, FILENAME_SYNTHESIS_LOG)
    synthesis_template = TEMPLATE_SELF_SYNTHESIS if job.mode == Mode.SELF else TEMPLATE_SYNTHESIS

    Path(job.review_file).write_text("")

    max_turns = _synthesis_max_turns(merged_content)
    prompt = build_prompt(
        synthesis_template, job, max_turns=max_turns,
        holistic_content=holistic_content, group_count=group_count,
        merged_content=merged_content, branch_name=job.pr.head,
    )
    runner = PhaseRunner(job, Phase.SYNTHESIS)
    log.info(f"Phase 4: Synthesis ({max_turns} turns)...")
    log.blank()

    # `rc` tracks the latest attempt so the fallback warning below reports the
    # retry's exit code, not the first attempt's.
    rc = 0

    def invoke(text: str, turns: int) -> int:
        nonlocal rc
        rc = runner.invoke(text, synthesis_log, max_turns=turns)
        return rc

    invoke(prompt, max_turns)
    log.blank()

    _retry_missing_output(
        invoke, prompt, synthesis_log, job.review_file,
        label="Synthesis", max_turns=max_turns,
    )

    if _is_complete_review(job.review_file):
        _post_process_review(job)
    else:
        reason = "no output" if not _has_output(job.review_file) else "incomplete output"
        detail = f"exited with code {rc} ({reason})" if rc != 0 else reason
        log.warn(f"Synthesis agent {detail} — falling back to mechanical merge")
        _write_mechanical_fallback(
            job, group_count, merged_content, skipped_groups=skipped_groups,
        )

    _write_review_sidecar(job)
    return synthesis_log


def _group_log_paths(job: ReviewJob, group_count: int) -> list[str]:
    return [_derive_path(job.review_file, FILENAME_GROUP_LOG.format(i)) for i in range(1, group_count + 1)]


def _read_existing_logs(log_paths: list[str]) -> str:
    parts = []
    for log_path in log_paths:
        p = Path(log_path)
        if p.exists():
            parts.append(p.read_text())
    return "".join(parts)


def _consolidate_logs(
    job: ReviewJob,
    holistic_log: str, group_count: int, synthesis_log: str,
    disprove_log: str = "",
):
    group_logs = _group_log_paths(job, group_count)
    all_logs = group_logs[:]
    if holistic_log:
        all_logs.insert(0, holistic_log)
    if synthesis_log:
        all_logs.append(synthesis_log)
    if disprove_log:
        all_logs.append(disprove_log)

    try:
        Path(job.session_log).write_text(_read_existing_logs(all_logs))
    except OSError:
        pass


def _cleanup_intermediates(
    job: ReviewJob,
    holistic_output: str, holistic_log: str,
    group_outputs: list[str], group_count: int, synthesis_log: str,
):
    group_logs = _group_log_paths(job, group_count)
    cleanup = group_outputs + group_logs
    if holistic_output:
        cleanup.append(holistic_output)
    if holistic_log:
        cleanup.append(holistic_log)
    if synthesis_log:
        cleanup.append(synthesis_log)
    cleanup.append(_pipeline_state_path(job))

    review_dir = str(Path(job.review_file).parent)
    for p in Path(review_dir).glob("prompt-*"):
        if p.name == FILENAME_PROMPT_STATS:
            continue
        cleanup.append(str(p))

    for path in cleanup:
        try:
            Path(path).unlink()
        except FileNotFoundError:
            pass


def _write_clean_review(job: ReviewJob, group_count: int, skipped_groups: int = 0):
    meta = _build_meta_header(
        job, skipped_groups=skipped_groups, total_groups=group_count,
    )
    if job.mode == Mode.SELF:
        content = (
            f"# Self-Review: {job.repo} — {job.pr.head}\n"
            f"{meta}\n"
            f"## Summary\n"
            f"Multi-phase self-review of {job.pr.changed_files} files across {group_count} groups. "
            f"No issues found.\n"
        )
    else:
        content = (
            f"# Review: {job.repo}#{job.pr_number} — {job.pr.title}\n"
            f"{meta}\n"
            f"## Summary\n"
            f"Multi-phase review of {job.pr.changed_files} files across {group_count} groups. "
            f"No issues found.\n\n"
            f"## Verdict\n"
            f"Approve — clean review.\n"
        )
    Path(job.review_file).write_text(content)
    _write_review_sidecar(job)


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


def _carry_forward_prior_findings(
    prior_review: str, groups: list[Group], skip_indices: set[int],
) -> str:
    """Extract prior findings for skipped groups and return as merged content."""
    if not prior_review or not skip_indices:
        return ""

    annotated = annotate_prior_with_stable_ids(prior_review)
    parts: list[str] = []
    for i, grp in enumerate(groups, 1):
        if i not in skip_indices:
            continue
        scoped = _scope_prior_review(annotated, grp.files)
        if scoped:
            parts.append(f"### Carried forward: {grp.name}\n{scoped}")

    if not parts:
        return ""
    return "\n\n".join(parts) + "\n"


def _identify_incremental_skips(
    groups: list[Group], delta_files: list[str],
) -> set[int]:
    """Return 1-based indices of groups with no files in the delta."""
    delta_set = set(delta_files)
    skips: set[int] = set()
    for i, grp in enumerate(groups, 1):
        if not any(f in delta_set for f in grp.files):
            skips.add(i)
    return skips


def _holistic_skip_reason(
    skip_holistic: bool, incremental: bool, group_count: int,
    effort: Effort = Effort.MEDIUM,
) -> str | None:
    if incremental:
        return "incremental review"
    if skip_holistic:
        return "--no-holistic"
    if EFFORT_PRESETS[effort].skip_holistic:
        return f"effort={effort}"
    if group_count < HOLISTIC_MIN_GROUPS:
        return f"{group_count} groups < {HOLISTIC_MIN_GROUPS} threshold"
    return None


def _use_scout(job: ReviewJob, skip_scout: bool) -> bool:
    if skip_scout:
        return False
    return not EFFORT_PRESETS[job.effort].skip_scout


def _run_holistic_phase(
    job: ReviewJob, group_count: int, state: PipelineState,
    skip_holistic: bool, resume_exists: bool, incremental: bool,
    skip_scout: bool = False,
) -> tuple[str, str, str, float]:
    _empty = ("", "", "", 0.0)

    reason = _holistic_skip_reason(skip_holistic, incremental, group_count, effort=job.effort)
    if reason:
        log.info(f"Holistic/scout phase skipped ({reason})")
        if not state.holistic_done:
            state.holistic_done = True
            _write_pipeline_state(job, state)
        return _empty

    use_scout = _use_scout(job, skip_scout)

    if use_scout:
        scout_output = _derive_path(job.review_file, FILENAME_SCOUT)
        scout_log = _derive_path(job.review_file, FILENAME_SCOUT_LOG)
        if resume_exists and _has_output(scout_output):
            raw = Path(scout_output).read_text()
            leads, no_scrutiny = parse_scout_output(raw)
            content = format_leads_block(leads, no_scrutiny)
            log.info("Phase 1: Scout scan skipped (exists)")
            return content, scout_output, scout_log, 0.0

        content, output, log_path = _phase_scout(job, group_count)
        cost = _parse_session_cost(log_path) if log_path else 0.0
        state.holistic_done = True
        _write_pipeline_state(job, state)
        return content, output, log_path, cost

    holistic_output = _derive_path(job.review_file, FILENAME_HOLISTIC)
    holistic_log = _derive_path(job.review_file, FILENAME_HOLISTIC_LOG)
    if resume_exists and _has_output(holistic_output):
        log.info("Phase 1: Holistic scan skipped (exists)")
        return Path(holistic_output).read_text(), holistic_output, holistic_log, 0.0

    content, holistic_output, holistic_log = _phase_holistic(job, group_count)
    cost = _parse_session_cost(holistic_log) if holistic_log else 0.0
    state.holistic_done = True
    _write_pipeline_state(job, state)
    return content, holistic_output, holistic_log, cost


def _run_group_phase(
    job: ReviewJob, groups: list[Group], group_count: int,
    holistic_content: str, max_parallel: int,
    skip_groups: "set[int] | None", state: PipelineState,
) -> "tuple[list[str], list[GroupFailure], float]":
    group_outputs, failed_groups = _phase_group_reviews(
        groups, job, group_count, holistic_content, max_parallel,
        skip_groups=skip_groups, pipeline_state=state,
    )

    cost = 0.0
    new_group_indices = [
        i for i in range(1, len(group_outputs) + 1)
        if skip_groups is None or i not in skip_groups
    ]
    for i in new_group_indices:
        gl = _derive_path(job.review_file, FILENAME_GROUP_LOG.format(i))
        cost += _parse_session_cost(gl)

    return group_outputs, failed_groups, cost


def _run_synthesis_or_fallback(
    job: ReviewJob, state: PipelineState,
    holistic_content: str, group_count: int,
    merged_content: str, failed_groups: "list[GroupFailure]",
    n_skipped: int, cost_so_far: float, max_cost: float,
    groups: "list[Group] | None" = None,
) -> str:
    all_groups_failed = len(failed_groups) == group_count

    if not _has_findings(merged_content) and not failed_groups:
        log.info("No findings from any group — writing clean review")
        _write_clean_review(job, group_count, skipped_groups=n_skipped)
        state.synthesis_done = True
        _write_pipeline_state(job, state)
        return ""

    if all_groups_failed:
        log.warn("All group agents failed — skipping synthesis")
        fallback = _build_mechanical_fallback(
            job, group_count, merged_content, skipped_groups=n_skipped,
            pipeline_state=state, groups=groups,
        )
        Path(job.review_file).write_text(fallback)
        _write_review_sidecar(job)
        state.synthesis_done = True
        state.synthesis_failed = "all groups failed"
        _write_pipeline_state(job, state)
        return ""

    if EFFORT_PRESETS[job.effort].skip_synthesis:
        log.info("Synthesis skipped (effort=low) — using mechanical merge")
        Path(job.review_file).write_text(merged_content)
        _post_process_review(job)
        _write_review_sidecar(job)
        state.synthesis_done = True
        _write_pipeline_state(job, state)
        _inject_failures_and_status(job.review_file, state, groups or [])
        return ""

    if cost_so_far > max_cost:
        log.warn("Using merged group output as final review (synthesis skipped due to budget)")
        Path(job.review_file).write_text(merged_content)
        _write_review_sidecar(job)
        state.synthesis_done = True
        state.synthesis_failed = "budget exceeded"
        _write_pipeline_state(job, state)
        _inject_failures_and_status(job.review_file, state, groups or [])
        return ""

    synthesis_log = _phase_synthesis(
        job, holistic_content, group_count, merged_content,
        skipped_groups=n_skipped,
    )
    state.synthesis_done = True
    review_content = Path(job.review_file).read_text() if Path(job.review_file).exists() else ""
    if _MECHANICAL_NOTE in review_content or FALLBACK_SUMMARY in review_content:
        state.synthesis_failed = "mechanical fallback"
    _write_pipeline_state(job, state)
    _inject_failures_and_status(job.review_file, state, groups or [])
    return synthesis_log


def run_multi_phase(
    job: ReviewJob, max_parallel: int = DEFAULT_MAX_PARALLEL,
    skip_holistic: bool = False, max_cost: float = DEFAULT_MAX_COST,
    max_groups: int | None = None,
    skip_scout: bool = False, disprove: bool | None = None,
):
    groups = group_files(job.pr)
    effective_max_groups = max_groups or EFFORT_PRESETS[job.effort].max_groups
    groups = _merge_smallest_groups(groups, effective_max_groups)

    if not job.include_generated:
        before = len(groups)
        groups = [g for g in groups if g.name != GROUP_TIER3]
        if len(groups) < before:
            log.info("Skipping tier3-generated group (use --generated to include)")

    group_count = len(groups)

    log.info(f"Large PR ({job.pr.total_lines} lines, {job.pr.changed_files} files) — {group_count} file groups")

    incremental = _is_incremental(job)
    incremental_skips: set[int] = set()
    carried_forward = ""

    if incremental:
        incremental_skips = _identify_incremental_skips(
            groups, job.preflight.delta_files,
        )
        if incremental_skips:
            affected = group_count - len(incremental_skips)
            log.info(
                f"Incremental: {affected}/{group_count} groups affected, "
                f"{len(incremental_skips)} unchanged (findings carried forward)"
            )
            carried_forward = _carry_forward_prior_findings(
                job.prior_review, groups, incremental_skips,
            )

    cost_so_far, skip_groups, skip_holistic_phase, state = _resolve_recovery(
        job, groups,
    )

    if state is None and _read_pipeline_state(job) is not None:
        log.info("Review already complete — use --force to re-run from scratch")
        return

    if state is None:
        state = PipelineState(
            head_sha=job.pr.head_sha,
            group_names=[g.name for g in groups],
            review_type="incremental" if incremental else "full",
            prior_sha=job.preflight.prior_head_sha if incremental else "",
            skipped_groups=sorted(incremental_skips),
        )
        _write_pipeline_state(job, state)

    # Merge incremental skips with any recovery skips
    if skip_groups is None:
        skip_groups = incremental_skips if incremental_skips else None
    elif incremental_skips:
        skip_groups = skip_groups | incremental_skips

    # ── Phase 1: Scout/Holistic ─────────────────────────────────────────────
    holistic_content, holistic_output, holistic_log, holistic_cost = _run_holistic_phase(
        job, group_count, state, skip_holistic, skip_holistic_phase, incremental,
        skip_scout=skip_scout,
    )
    cost_so_far += holistic_cost

    # ── Phase 2: Groups ───────────────────────────────────────────────────────
    if cost_so_far > max_cost:
        log.warn(f"Budget exceeded after holistic phase (${cost_so_far:.2f}/${max_cost:.2f}) — skipping groups")
        group_outputs: list[str] = []
        failed_groups: list[GroupFailure] = [
            GroupFailure(g.name, Diagnosis(DiagnosisKind.BUDGET_EXCEEDED)) for g in groups
        ]
    else:
        group_outputs, failed_groups, groups_cost = _run_group_phase(
            job, groups, group_count, holistic_content, max_parallel,
            skip_groups, state,
        )
        cost_so_far += groups_cost

    # ── Phase 3: Merge ───────────────────────────────────────────────────────
    merged_content = _phase_merge(group_outputs[:], failed_groups)

    if carried_forward:
        merged_content += "\n" + carried_forward

    # ── Phase 4: Synthesis ───────────────────────────────────────────────────
    n_skipped = len(incremental_skips)
    synthesis_log = _run_synthesis_or_fallback(
        job, state, holistic_content, group_count,
        merged_content, failed_groups, n_skipped, cost_so_far, max_cost,
        groups=groups,
    )

    # ── Phase 4.5: Disprove-it gate ─────────────────────────────────────────
    disprove_log = ""
    if _should_disprove(job, disprove) and cost_so_far <= max_cost:
        review_path = Path(job.review_file)
        ms_count = count_severity(review_path, "M") + count_severity(review_path, "S")
        if disprove is True or ms_count >= DISPROVE_MIN_FINDINGS:
            disprove_log, disprove_cost = _phase_disprove(job)
            cost_so_far += disprove_cost
        else:
            log.info(f"Skipping disprove — only {ms_count} M/S findings (threshold: {DISPROVE_MIN_FINDINGS})")

    # ── Cleanup ──────────────────────────────────────────────────────────────
    _consolidate_logs(job, holistic_log, group_count, synthesis_log, disprove_log=disprove_log)

    if not failed_groups:
        _cleanup_intermediates(
            job, holistic_output, holistic_log, group_outputs, group_count, synthesis_log,
        )


def _with_local_diff(pr: PRMetadata, local: PRMetadata) -> PRMetadata:
    """PR narrative over the worktree's own diff surface.

    Self-review reads files out of the worktree, so the SHA and changed-file
    list have to come from git. Taking them from GitHub silently drops every
    unpushed commit: the diff is local but the file list is not, so the review
    never opens the files those commits touched.
    """
    if pr.head_sha != local.head_sha:
        log.info(
            f"Reviewing local HEAD {local.head_sha[:7]} "
            f"(PR head is {pr.head_sha[:7]})"
        )
    return replace(
        pr,
        head=local.head,
        head_sha=local.head_sha,
        additions=local.additions,
        deletions=local.deletions,
        changed_files=local.changed_files,
        files=local.files,
    )


def _fetch_metadata(
    repo: str, pr_number: str, mode: Mode, wt_path: str, pin_sha: str = "",
) -> tuple[PRMetadata, PRContext, PRData | None]:
    if mode == Mode.SELF and not pr_number:
        log.info("Gathering branch metadata...")
        return fetch_branch_metadata(wt_path), PRContext(), None
    log.info("Fetching PR data...")
    if mode == Mode.SELF:
        # Sequential: the local read needs the PR's base branch to pick its range.
        pr = fetch_pr_metadata(repo, pr_number)
        return _with_local_diff(pr, fetch_branch_metadata(wt_path, pr.base)), PRContext(), None
    with ThreadPoolExecutor(max_workers=2) as pool:
        pr_future = pool.submit(fetch_pr_metadata, repo, pr_number, pin_sha, wt_path)
        pd_future = pool.submit(fetch_pr_data, repo, pr_number)
        pr_data = pd_future.result()
        ctx = fetch_pr_context(repo, pr_number, pr_data)
        return pr_future.result(), ctx, pr_data
