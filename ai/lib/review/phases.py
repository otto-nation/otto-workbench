"""Phase executors for the review pipeline.

A review is a sequence of agent phases. What a phase *is* — its built-in spec,
and how that spec resolves against the config file and the environment — is
`agent.registry` and `agent_phases`, which the whole workbench shares. This module
is the review pipeline's half: `PhaseRunner`, which binds a resolved phase to
one review's worktree, session log and throttle, and `run_phase`, which drives
one of them end to end.

Running an agent phase is the same nine steps whichever phase it is, so there is
one function rather than one per phase. What differs is what its artifact means
afterwards, and that is `review.registry`'s table, read through `read_scan` so
the resume path and the run path cannot disagree about it.

The group fan-out lives here too — serial, parallel, retry and the
previously-skipped sweep are all ways of running the group phase, and they
share the executor and its budget rules.

What a phase *produces* is somebody else's problem: sequencing the phases into
a run is `review.pipeline`'s, deciding what each phase's output means for the
run is `review.steps`', and writing the result to the review file is
`review.outcome`'s.
"""

# doc-group: pipeline

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from agent import phases as agent_phases
from agent import retry as agent_retry
from core import log
from agent.diagnosis import Diagnosis, DiagnosisKind
from agent.registry import PHASES
from agent.types import EFFORT_PRESETS
from gh.types import FILE_STAT_FMT
from core.phases import Phase, PhaseShape
from agent.invoke import run_agent
from agent.backend import AgentInvocation
from agent.session import (
    CONSECUTIVE_FAIL_THRESHOLD,
    _parse_session_cost, build_add_dirs,
    diagnose_missing_output, try_recover_output,
)
from review.paths import (
    phase_log_path,
    phase_output_path,
)
from review.document import SECTION_FILE_TRIAGE, SECTION_PRIOR_FINDINGS, ReviewDocument
from review.prompt import PromptTooLarge
from review.registry import PhaseScan, build_prompt, for_phase
from review.retry import (
    GroupFailure,
    _check_serial_abort, _has_output, _is_retryable,
    _retry_hint_for, _retry_missing_output, _was_skipped,
)
from review.state import PipelineState, _update_group_done, _update_group_failed
from review.types import SEVERITIES, Group, GroupSkip, ReviewJob

# The group phase's own retry ceiling, off its registry entry. Synthesis borrows
# it as an upper bound for the same reason it always has: both are review phases
# sized against the same 15-turn default.
RETRY_MAX_TURNS_GROUP = PHASES[Phase.GROUP].retry.ceiling


# ── Phase turn budgets ───────────────────────────────────────────────────────


def _omitted_files(job: ReviewJob) -> int:
    """How many files preflight left out of this job's prompt."""
    if not job.preflight or not job.preflight.omitted_files:
        return 0
    return len(job.preflight.omitted_files)


def _omitted_bump(phase: Phase, job: ReviewJob) -> int:
    """This job's omitted-file bump for a phase, zero when its spec opts out."""
    return agent_phases.phase_omitted_bump(phase, job.effort, _omitted_files(job))


def job_turns(phase: Phase, job: ReviewJob) -> int:
    """A phase's turn budget for this job: registry default plus its bump.

    Named for the job rather than the phase because ``agent_phases.phase_turns``
    is the registry's answer and this is the same answer with this job's omitted
    files folded in — two names so the proxy in ``review-orchestrate`` can patch
    either without the other going with it.
    """
    return agent_phases.phase_turns(phase, job.effort, _omitted_files(job))


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
        # Cached on the job rather than loaded here: the group phase builds one
        # runner per group, and all of them want the same file.
        cfg = job.config
        self.job = job
        # A phase that names no log of its own logs to the job's — that is
        # where the single-agent path already sends every record, and the
        # caller may have pointed it outside the review directory.
        self.session_log = phase_log_path(job.review_file, phase, index) or job.session_log
        self.model = agent_phases.phase_model(phase, job.model, cfg)
        self.thinking = agent_phases.phase_thinking(phase, job.effort, cfg)
        self.provider = agent_phases.phase_provider(cfg)
        self.budget = agent_phases.phase_budget(phase, job.effort)
        self.agent = None if spec.shape is PhaseShape.FIX else (
            spec.agent if spec.agent is not None else preset.agent
        )
        self.max_turns = job_turns(phase, job)

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
        return run_agent(
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

    `diagnosis` says why `content` is empty, or — for synthesis, which never
    fills it — which path wrote the review file instead; either way it tells
    that apart from a phase that declined to run and so has nothing to
    explain. It travels with the result rather than being re-derived, because
    the reason is only reachable while the retry driver is still holding the
    session log — by the time the disprove gate records the outcome, the log
    belongs to whatever ran next.
    """

    log: str = ""
    cost: float = 0.0
    content: str = ""
    output: str = ""
    diagnosis: Diagnosis | None = None

    @classmethod
    def of(
        cls, log: str, content: str = "", output: str = "",
        diagnosis: "Diagnosis | None" = None,
    ) -> "PhaseResult":
        """Priced from the log the phase just wrote.

        A phase that wrote no log reads as free: `_parse_session_cost` takes a
        missing file as zero.
        """
        return cls(log, _parse_session_cost(log), content, output, diagnosis)


def _touch(path: str) -> None:
    """Pre-create an empty output file without truncating existing content."""
    Path(path).touch(exist_ok=True)


def _synthesis_max_turns(merged_content: str) -> int:
    counts = ReviewDocument(body=merged_content).open_counts
    total = sum(counts.values())
    scaled = PHASES[Phase.SYNTHESIS].max_turns + max(0, total - 20) // 10
    return min(scaled, RETRY_MAX_TURNS_GROUP)


# ── One agent phase ──────────────────────────────────────────────────────────


def _scan(phase: Phase) -> PhaseScan:
    """`phase`'s scan entry, or a `ValueError` naming the phase that owes one.

    Raises the way `build_prompt` and `PhaseSpec.template_for` do rather than
    letting a bare `None` through: a phase added to the registry but not to
    `review.registry` is a missing declaration, and the message should say so
    at the table rather than at whichever line first dereferenced it.
    """
    entry = for_phase(phase)
    if entry is None or entry.scan is None:
        raise ValueError(f"{phase} declares no scan of its own")
    return entry.scan


def read_scan(phase: Phase, raw: str) -> str:
    """`phase`'s raw artifact as the content the phases after it read.

    Empty in, empty out — a scan that produced nothing has no leads to render.
    A scan with no `read` of its own hands the raw text back unchanged.

    Exposed alongside `run_phase` because a resumed run reaches the same file
    without running the agent that wrote it. Reading it a second way there is
    how the resume path and the run path came to disagree about what a scout
    scan is.
    """
    if not raw:
        return ""
    scan = _scan(phase)
    return scan.read(raw) if scan.read else raw


def run_phase(
    job: ReviewJob, phase: Phase, announce: str, **prompt_args,
) -> PhaseResult:
    """Run one agent phase over `job` and report its log, spend and content.

    `announce` is the line printed before the agent starts and `prompt_args`
    carries what only this phase supplies to its template; everything else — the
    artifact path, the turn budget, the prompt, the retry and the reading of
    what landed — comes off the phase's registry entry.

    Only for a phase that writes an artifact of its own. `single` and
    `synthesis` write the review document instead, and `review.pipeline` drives
    those, because deciding what the document says when an agent falls short is
    that module's job rather than this one's.

    A phase that produced nothing warns why and comes back with empty `content`
    and the `diagnosis` saying why, rather than raising. Every phase this runs
    is one the pipeline has a path around, so the run continues without it. A
    prompt that will not fit the budget is one of those outcomes and not an
    error: it is reported before any agent starts, so the phase costs nothing.
    """
    output = phase_output_path(job.review_file, phase)
    scan = _scan(phase)
    _touch(output)

    runner = PhaseRunner(job, phase)
    max_turns = runner.max_turns
    try:
        prompt = build_prompt(phase, job, max_turns=max_turns, **prompt_args)
    except PromptTooLarge as exc:
        diagnosis = Diagnosis(DiagnosisKind.PROMPT_TOO_LARGE, detail=str(exc))
        log.warn(f"{PHASES[phase].label} not run ({diagnosis.message}) — {scan.without}")
        return PhaseResult.of(runner.session_log, output=output, diagnosis=diagnosis)

    log.info(announce)
    log.blank()
    runner.invoke(prompt)
    log.blank()

    label = PHASES[phase].label
    diagnosis = _retry_missing_output(
        runner.invoke, prompt, runner.session_log, output,
        label=label, max_turns=max_turns,
    )

    if not _has_output(output):
        # A `None` here is the artifact vanishing after the retry driver had
        # already confirmed it. The phase still failed, so it is named as the
        # missing output it is: a result carrying no diagnosis is how a caller
        # tells a phase that fell short from one that was never asked to run.
        diagnosis = diagnosis or Diagnosis(DiagnosisKind.OUTPUT_MISSING)
        log.warn(f"{label} produced no output ({diagnosis.message}) — {scan.without}")
        return PhaseResult.of(runner.session_log, output=output, diagnosis=diagnosis)

    content = read_scan(phase, Path(output).read_text())
    return PhaseResult.of(runner.session_log, content, output)


# ── Phase executors ──────────────────────────────────────────────────────────

# Every heading a group agent may write. A group's output is merged section by
# section, so one that carries none of these contributes nothing to the review
# however much the agent wrote into it.
_VALID_SECTION_HEADERS = (
    {s.section.lower() for s in SEVERITIES}
    | {SECTION_FILE_TRIAGE.lower(), SECTION_PRIOR_FINDINGS.lower()}
)


def _validate_group_output(output_path: str, group_name: str) -> bool:
    """Whether a group's output carries a heading the merge will read.

    Warns when it does not. An empty output is not a failure here — the caller
    has already established the agent produced something, and a group with
    nothing to say writes an empty file legitimately.
    """
    content = Path(output_path).read_text()
    if not content.strip():
        return True
    has_section = any(
        line.strip()[3:].strip().lower() in _VALID_SECTION_HEADERS
        for line in content.split("\n")
        if line.strip().startswith("## ")
    )
    if not has_section:
        log.warn(f"Group {group_name} output has no recognized sections — findings may be lost")
    return has_section


def _review_group(
    i: int, grp: Group, job: ReviewJob,
    group_count: int, holistic_content: str,
    skip: GroupSkip | None = None,
    pipeline_state: PipelineState | None = None,
    max_turns: int | None = None,
    retry_hint: str = "",
) -> tuple[int, str, GroupFailure | None]:
    group_output = phase_output_path(job.review_file, Phase.GROUP, i)

    if skip is GroupSkip.CARRIED:
        log.info(
            f"Phase 2: Group {i}/{group_count} — {grp.name} skipped "
            f"(unchanged — findings carried forward)"
        )
        return (i, group_output, None)

    if skip is GroupSkip.RECOVERY:
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

    try:
        group_prompt = build_prompt(
            Phase.GROUP, job, max_turns=max_turns,
            group_idx=i, group_count=group_count, group_name=grp.name,
            group_files_formatted=group_files_formatted,
            group_file_paths=grp.files,
            holistic_content=holistic_content,
        )
    except PromptTooLarge as exc:
        diagnosis = Diagnosis(DiagnosisKind.PROMPT_TOO_LARGE, detail=str(exc))
        log.warn(f"Group {i} ({grp.name}) not run ({diagnosis.message})")
        if pipeline_state is not None:
            _update_group_failed(job, i, diagnosis, pipeline_state)
        return (i, group_output, GroupFailure(grp.name, diagnosis))
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


def _should_disprove(job: ReviewJob, explicit_disprove: bool | None = None) -> bool:
    """Whether the gate runs, with `--disprove` beating the effort preset.

    `--no-disprove` is read off `skip_phases` rather than `skipped`, so the two
    flags keep their precedence: `--disprove` buys back the gate a low-effort
    preset dropped, and nothing buys back one the operator switched off.

    `explicit_disprove` is a tri-state a caller supplies, not a flag value: both
    CLIs pass `None` or `True`, since switching the gate off now travels as
    `--no-disprove` on `skip_phases`. `False` is for a library caller running the
    pipeline directly with the gate forced off, and stays reachable for it.
    """
    if Phase.DISPROVE in job.skip_phases:
        return False
    if explicit_disprove is not None:
        return explicit_disprove
    return Phase.DISPROVE not in job.skipped


# ── Group fan-out ────────────────────────────────────────────────────────────


def _run_serial_reviews(
    groups: list[Group], job: ReviewJob,
    group_count: int, holistic_content: str,
    skip_groups: dict[int, GroupSkip],
    pipeline_state: PipelineState | None,
) -> list[GroupFailure]:
    failed_groups: list[GroupFailure] = []
    consecutive_same_reason = 0
    last: Diagnosis | None = None
    for i, grp in enumerate(groups, 1):
        _, _, failed = _review_group(
            i, grp, job, group_count, holistic_content,
            skip=skip_groups.get(i), pipeline_state=pipeline_state,
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
    skip_groups: dict[int, GroupSkip],
    pipeline_state: PipelineState | None,
) -> list[GroupFailure]:
    log.info(f"Phase 2: Reviewing {group_count} groups ({workers} parallel)...")
    log.blank()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(
                _review_group, i, grp, job, group_count, holistic_content,
                skip=skip_groups.get(i),
                pipeline_state=pipeline_state,
            )
            for i, grp in enumerate(groups, 1)
        ]
        results = [f.result() for f in futures]
    log.blank()
    return [failure for _, _, failure in results if failure]


def _retry_turns(diagnosis: Diagnosis, job: ReviewJob) -> int:
    # The escalated ceiling is not a registry default, so it cannot come from
    # job_turns — but its bump still goes through the group spec, so the spec
    # remains the only thing that decides whether the group scales at all.
    if diagnosis.kind is DiagnosisKind.MAX_TURNS:
        return RETRY_MAX_TURNS_GROUP + _omitted_bump(Phase.GROUP, job)
    return job_turns(Phase.GROUP, job)


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
    skip_groups: dict[int, GroupSkip] | None = None,
    pipeline_state: PipelineState | None = None,
) -> tuple[list[str], list[GroupFailure]]:
    skip_groups = skip_groups or {}
    group_outputs = [
        phase_output_path(job.review_file, Phase.GROUP, i)
        for i in range(1, group_count + 1)
    ]

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
