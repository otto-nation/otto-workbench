"""Phase registry and executors for the review pipeline.

A review is a sequence of agent phases, and this module owns what a phase *is*:
the built-in spec (`PhaseSpec`, `PHASES`), the resolution of a spec plus an
effort preset into the six values an invocation needs (`PhaseRunner`), the turn
budgets, and the executors that actually run each phase.

The group fan-out lives here too — serial, parallel, retry and the
previously-skipped sweep are all ways of running the group phase, and they
share the executor and its budget rules.

What a phase *produces* is somebody else's problem: the review document, the
synthesis and the run drivers stay in review_pipeline.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import agent_retry
import log
from review_agent import (
    CONSECUTIVE_FAIL_THRESHOLD,
    AgentInvocation, _parse_session_cost, _resolve_model,
    _resolve_provider, _resolve_thinking_level, build_add_dirs,
    diagnose_missing_output, invoke_agent, try_recover_output,
)
from review_common import (
    FILE_STAT_FMT,
    FILENAME_DISPROVE, FILENAME_DISPROVE_LOG,
    FILENAME_GROUP, FILENAME_GROUP_LOG, FILENAME_HOLISTIC,
    FILENAME_HOLISTIC_LOG, FILENAME_SCOUT, FILENAME_SCOUT_LOG,
    AgentKind, Diagnosis, DiagnosisKind, Effort, Phase, Thinking,
    EFFORT_PRESETS,
    TEMPLATE_DISPROVE, TEMPLATE_GROUP, TEMPLATE_HOLISTIC, TEMPLATE_SCOUT,
    _derive_path,
)
from review_disprove import apply_disprove_results, parse_disprove_output
from review_findings import _count_findings, _validate_group_output, merge_reviews
from review_preflight import Group, PipelineState, ReviewJob
from review_prompt import build_prompt
from review_retry import (
    GroupFailure,
    _check_serial_abort, _has_output, _is_retryable, _render_reason,
    _retry_hint_for, _retry_missing_output, _was_skipped,
)
from review_scout import format_leads_block, parse_scout_output
from review_state import _update_group_done, _update_group_failed

RETRY_MAX_TURNS_GROUP = agent_retry.RETRY_MAX_TURNS

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


def _phase_thinking(effort: Effort, phase: Phase) -> Thinking | None:
    """The effort override if the preset sets one, else the phase's own default."""
    override = EFFORT_PRESETS[effort].thinking
    return override if override is not None else PHASES[phase].thinking


class PhaseRunner:
    """The per-phase values, resolved once.

    Every phase needs the same six — model, thinking level, provider, budget,
    agent, and max turns — resolved from the phase spec, the effort preset,
    and the environment. Resolving them here means one place to read rather
    than seven blocks that must be kept in step. The session log joins them:
    a runner belongs to one phase of one review, and that phase writes to
    exactly one log.
    """

    def __init__(self, job: ReviewJob, phase: Phase, session_log: str = ""):
        spec = PHASES[phase]
        preset = EFFORT_PRESETS[job.effort]
        self.job = job
        # A phase that writes no log of its own logs to the job's — that is
        # where the single-agent path already sends every record.
        self.session_log = session_log or job.session_log
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
        self, prompt: str, max_turns: int | None = None, *, label: str = "",
    ) -> AgentInvocation:
        return AgentInvocation(
            prompt=prompt,
            cwd=str(self.job.wt_path),
            session_log=self.session_log,
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
        self, prompt: str, max_turns: int | None = None, *, label: str = "",
    ) -> int:
        """Run one attempt. Positional `(prompt, max_turns)` is the shape
        `agent_retry.retry_missing_output` calls its callback with, so a
        runner can be handed to it directly."""
        return invoke_agent(
            self.invocation(prompt, max_turns, label=label),
            throttle=self.job.throttle,
        )


def _touch(path: str) -> None:
    """Pre-create an empty output file without truncating existing content."""
    Path(path).touch(exist_ok=True)


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


# ── Phase executors ──────────────────────────────────────────────────────────


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
    runner = PhaseRunner(job, Phase.GROUP, group_log)
    log.info(f"Phase 2: Group {i}/{group_count} — {grp.name} ({grp.lines} lines)...")
    runner.invoke(group_prompt, max_turns, label=grp.name)

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
    runner = PhaseRunner(job, Phase.HOLISTIC, holistic_log)
    log.info(f"Phase 1/{group_count}: Holistic scan...")
    log.blank()

    runner.invoke(prompt, max_turns)
    log.blank()

    diagnosis = _retry_missing_output(
        runner.invoke, prompt, holistic_log, holistic_output,
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
    runner = PhaseRunner(job, Phase.SCOUT, scout_log)
    log.info(f"Phase 1/{group_count}: Lead scout scan...")
    log.blank()

    runner.invoke(prompt, max_turns)
    log.blank()

    diagnosis = _retry_missing_output(
        runner.invoke, prompt, scout_log, scout_output,
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
    runner = PhaseRunner(job, Phase.DISPROVE, disprove_log)
    log.info(f"Disprove gate — challenging {ms_count} must-fix/should-fix findings...")
    log.blank()

    runner.invoke(prompt, max_turns)
    log.blank()

    diagnosis = _retry_missing_output(
        runner.invoke, prompt, disprove_log, disprove_output,
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


# ── Group fan-out ────────────────────────────────────────────────────────────


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


def _retry_turns(diagnosis: Diagnosis, job: "ReviewJob") -> int:
    extra = _omitted_turns(job)
    if diagnosis.kind is DiagnosisKind.MAX_TURNS:
        return RETRY_MAX_TURNS_GROUP + extra
    return PHASES[Phase.GROUP].max_turns + extra


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
