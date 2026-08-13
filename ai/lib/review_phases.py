"""Phase registry and executors for the review pipeline.

A review is a sequence of agent phases, and this module owns what a phase *is*:
the built-in spec (`PhaseSpec`, `PHASES`), the resolution of a spec plus an
effort preset into the seven values an invocation needs (`PhaseRunner`), the
turn budgets, and the executors that actually run each phase.

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
    FILENAME_DISPROVE,
    FILENAME_GROUP, FILENAME_HOLISTIC, FILENAME_SCOUT,
    AgentKind, Diagnosis, DiagnosisKind, Effort, Phase, Thinking,
    EFFORT_PRESETS,
    TEMPLATE_DISPROVE, TEMPLATE_GROUP, TEMPLATE_HOLISTIC, TEMPLATE_SCOUT,
    _derive_path,
    phase_log_path,
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

    ``scales_with_omitted`` says whether ``max_turns`` grows with the files
    preflight had to leave out of the prompt: a phase that reads branch source
    must open those itself, and that costs turns. It defaults to on because the
    opposite default is what #653 fixed — a phase that silently misses the bump
    is under-budgeted, where one that takes it needlessly just finishes early.
    A phase that reasons only over text already in its prompt opts out.
    """

    phase: Phase
    model: str = "sonnet"
    thinking: Thinking | None = None
    max_turns: int = 15
    agent: AgentKind | None = None
    edits: bool = False
    scales_with_omitted: bool = True


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
    # Synthesis and disprove are handed the findings they judge, so an omitted
    # file costs them nothing. Fix takes its budget from _fix_turn_budget, which
    # scales with unchecked findings instead.
    Phase.SYNTHESIS: PhaseSpec(
        Phase.SYNTHESIS, thinking=Thinking.MEDIUM, max_turns=15,
        scales_with_omitted=False,
    ),
    Phase.DISPROVE: PhaseSpec(
        Phase.DISPROVE, thinking=Thinking.MEDIUM, max_turns=15,
        agent=AgentKind.REVIEWER_LITE,
        scales_with_omitted=False,
    ),
    Phase.FIX: PhaseSpec(
        Phase.FIX, thinking=Thinking.LOW, max_turns=20,
        edits=True,
        scales_with_omitted=False,
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


# ── Phase turn budgets ───────────────────────────────────────────────────────


def _omitted_turns(job: ReviewJob) -> int:
    """What this job's omitted files cost a phase that has to open them."""
    if EFFORT_PRESETS[job.effort].skip_omitted_files:
        return 0
    if not job.preflight or not job.preflight.omitted_files:
        return 0
    return len(job.preflight.omitted_files) * OMITTED_FILE_TURNS


def _omitted_bump(phase: Phase, job: ReviewJob) -> int:
    """The omitted-file turns this phase is owed — zero unless its spec opts in."""
    return _omitted_turns(job) if PHASES[phase].scales_with_omitted else 0


def phase_turns(phase: Phase, job: ReviewJob) -> int:
    """A phase's turn budget for this job: registry default plus its bump."""
    return PHASES[phase].max_turns + _omitted_bump(phase, job)


class PhaseRunner:
    """The per-phase values, resolved once.

    Every phase needs the same six — model, thinking level, provider, budget,
    agent, and max turns — resolved from the phase spec, the effort preset,
    and the environment. Resolving them here means one place to read rather
    than seven blocks that must be kept in step. The session log joins them:
    a runner belongs to one phase of one review, so it derives the one log
    that phase writes rather than being told.
    """

    def __init__(self, job: ReviewJob, phase: Phase, index: int | None = None):
        spec = PHASES[phase]
        preset = EFFORT_PRESETS[job.effort]
        self.job = job
        # A phase that names no log of its own logs to the job's — that is
        # where the single-agent path already sends every record, and the
        # caller may have pointed it outside the review directory.
        self.session_log = phase_log_path(job.review_file, phase, index) or job.session_log
        self.model = phase_model(phase, job.model)
        self.thinking = _resolve_thinking_level(
            None, phase.thinking_env_key, _phase_thinking(job.effort, phase),
        )
        self.provider = _resolve_provider()
        self.budget = preset.agent_budget
        self.agent = None if spec.edits else (
            spec.agent if spec.agent is not None else preset.agent
        )
        self.max_turns = phase_turns(phase, job)

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


@dataclass(frozen=True)
class PhaseResult:
    """What a phase reports back: its session log, its spend, and its scan.

    The log and the cost travel together everywhere: the log is what the run
    consolidates and cleans up, the cost is what the budget gates read, and the
    second is only ever derived from the first. Naming the pair keeps that
    derivation in one place instead of at each call site, and gives a phase
    that never invoked an agent something to return — the default is the honest
    report that nothing ran and nothing was spent.

    `content` and `output` carry a scan the phases after it read. Phase 1 is
    the only phase that writes for its successors rather than into the review
    file, so it is the only one that fills them.
    """

    log: str = ""
    cost: float = 0.0
    content: str = ""
    output: str = ""

    @classmethod
    def of(cls, log: str, content: str = "", output: str = "") -> "PhaseResult":
        """Priced from the log the phase just wrote.

        A phase that wrote no log reads as free: `_parse_session_cost` takes a
        missing file as zero.
        """
        return cls(log, _parse_session_cost(log), content, output)


def _touch(path: str) -> None:
    """Pre-create an empty output file without truncating existing content."""
    Path(path).touch(exist_ok=True)


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
    pipeline_state: PipelineState | None = None,
    max_turns: int | None = None,
    retry_hint: str = "",
) -> tuple[int, str, GroupFailure | None]:
    group_output = _derive_path(job.review_file, FILENAME_GROUP.format(i))

    if skip:
        if _has_output(group_output):
            log.info(f"Phase 2: Group {i}/{group_count} — {grp.name} skipped (exists)")
            return (i, group_output, None)
        log.warn(f"Group {i} ({grp.name}) marked skip but output missing — reporting failure")
        return (i, group_output, GroupFailure(
            grp.name, Diagnosis(DiagnosisKind.OUTPUT_MISSING),
        ))

    runner = PhaseRunner(job, Phase.GROUP, i)
    group_log = runner.session_log
    # Resolved here, not in the signature: a default argument is evaluated once
    # at import, which both freezes the registry value and hides the fact that
    # the budget depends on the job's omitted files. Only the retry paths, which
    # escalate past the phase default, pass a budget of their own.
    if max_turns is None:
        max_turns = runner.max_turns

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


def _phase_holistic(job: ReviewJob, group_count: int) -> PhaseResult:
    holistic_output = _derive_path(job.review_file, FILENAME_HOLISTIC)

    _touch(holistic_output)

    runner = PhaseRunner(job, Phase.HOLISTIC)
    holistic_log = runner.session_log
    max_turns = runner.max_turns
    prompt = build_prompt(
        TEMPLATE_HOLISTIC, job, max_turns=max_turns, holistic_output=holistic_output,
    )

    log.info(f"Phase 1/{group_count}: Holistic scan...")
    log.blank()

    runner.invoke(prompt)
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

    return PhaseResult.of(holistic_log, holistic_content, holistic_output)


def _phase_scout(job: ReviewJob, group_count: int) -> PhaseResult:
    scout_output = _derive_path(job.review_file, FILENAME_SCOUT)

    _touch(scout_output)

    runner = PhaseRunner(job, Phase.SCOUT)
    scout_log = runner.session_log
    max_turns = runner.max_turns
    prompt = build_prompt(
        TEMPLATE_SCOUT, job, max_turns=max_turns, scout_output=scout_output,
    )

    log.info(f"Phase 1/{group_count}: Lead scout scan...")
    log.blank()

    runner.invoke(prompt)
    log.blank()

    diagnosis = _retry_missing_output(
        runner.invoke, prompt, scout_log, scout_output,
        label="Scout", max_turns=max_turns,
    )

    if _has_output(scout_output):
        raw = Path(scout_output).read_text()
        leads, no_scrutiny = parse_scout_output(raw)
        log.info(f"Scout found {len(leads)} investigation leads, {len(no_scrutiny)} no-scrutiny files")
        return PhaseResult.of(
            scout_log, format_leads_block(leads, no_scrutiny), scout_output,
        )

    log.warn(f"Scout produced no output ({_render_reason(diagnosis)}) — continuing without leads")
    return PhaseResult.of(scout_log, output=scout_output)


def _phase_disprove(job: ReviewJob) -> PhaseResult:
    review_content = Path(job.review_file).read_text() if Path(job.review_file).exists() else ""
    counts = _count_findings(review_content)
    ms_count = counts.get("M", 0) + counts.get("S", 0)
    if ms_count == 0:
        log.info("Disprove gate skipped — no must-fix or should-fix findings")
        return PhaseResult()

    disprove_output = _derive_path(job.review_file, FILENAME_DISPROVE)

    _touch(disprove_output)

    runner = PhaseRunner(job, Phase.DISPROVE)
    disprove_log = runner.session_log
    max_turns = runner.max_turns
    prompt = build_prompt(
        TEMPLATE_DISPROVE, job, max_turns=max_turns,
        disprove_output=disprove_output, review_content=review_content,
    )

    log.info(f"Disprove gate — challenging {ms_count} must-fix/should-fix findings...")
    log.blank()

    runner.invoke(prompt)
    log.blank()

    diagnosis = _retry_missing_output(
        runner.invoke, prompt, disprove_log, disprove_output,
        label="Disprove gate", max_turns=max_turns,
    )

    result = PhaseResult.of(disprove_log)

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

    return result


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
    skip_groups: set[int] | None,
    pipeline_state: PipelineState | None,
) -> list[GroupFailure]:
    failed_groups: list[GroupFailure] = []
    consecutive_same_reason = 0
    last: Diagnosis | None = None
    for i, grp in enumerate(groups, 1):
        skip = skip_groups is not None and i in skip_groups
        _, _, failed = _review_group(
            i, grp, job, group_count, holistic_content,
            skip=skip, pipeline_state=pipeline_state,
        )
        if not failed:
            consecutive_same_reason = 0
            last = None
            continue
        failed_groups.append(failed)
        group_log = phase_log_path(job.review_file, Phase.GROUP, i)
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
    skip_groups: set[int] | None,
    pipeline_state: PipelineState | None,
) -> list[GroupFailure]:
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


def _retry_turns(diagnosis: Diagnosis, job: ReviewJob) -> int:
    # The escalated ceiling is not a registry default, so it cannot come from
    # phase_turns — but its bump still goes through the group spec, so the spec
    # remains the only thing that decides whether the group scales at all.
    if diagnosis.kind is DiagnosisKind.MAX_TURNS:
        return RETRY_MAX_TURNS_GROUP + _omitted_bump(Phase.GROUP, job)
    return phase_turns(Phase.GROUP, job)


def _retry_failed_groups(
    failed_groups: list[GroupFailure],
    groups: list[Group], job: ReviewJob,
    group_count: int, holistic_content: str,
    pipeline_state: PipelineState | None,
) -> list[GroupFailure]:
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
    skipped: list[GroupFailure],
    group_by_name: dict[str, tuple[int, Group]],
    job: ReviewJob,
    group_count: int,
    holistic_content: str,
    pipeline_state: PipelineState | None,
) -> list[GroupFailure]:
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
        )
        if failure:
            failures.append(failure)
    return failures


def _phase_group_reviews(
    groups: list[Group], job: ReviewJob,
    group_count: int, holistic_content: str, max_parallel: int,
    skip_groups: set[int] | None = None,
    pipeline_state: PipelineState | None = None,
) -> tuple[list[str], list[GroupFailure]]:
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


def _phase_merge(group_outputs: list[str], failed_groups: list[GroupFailure]) -> str:
    log.info("Phase 3: Merging findings...")
    merged_content = merge_reviews(group_outputs)

    if failed_groups:
        merged_content += "\n## Review gaps\n"
        merged_content += "The following file groups were not reviewed due to agent failure:\n"
        for failed in failed_groups:
            merged_content += f"- {failed.group}: {failed.diagnosis.message}\n"

    return merged_content
