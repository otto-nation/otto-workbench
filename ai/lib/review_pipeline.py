"""Pipeline orchestration for claude-review.

Drives the single-agent and multi-phase runs end to end: sequencing the phases
review_phases defines, deciding what a resumed run may skip, assembling the
review document (synthesis, mechanical fallback, meta header), consolidating
the session logs, and fetching the PR metadata a run starts from.

The run ends when the review file is written — what happens to the findings
afterwards belongs to review_fix, and removing what the run left behind belongs
to review_gc, which the orchestrator runs once every phase is done.
"""

# doc-group: pipeline

from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import date
from pathlib import Path

import git_client
import log
from agent_diagnosis import Diagnosis, DiagnosisKind
from agent_registry import PHASES, SCAN_PHASES
from agent_types import EFFORT_PRESETS, Mode, Phase
from pr_domains import ReviewStatus
from review_common import (
    count_severities,
    phase_log_path,
    phase_output_path,
    phase_skip_argv,
    write_review_meta,
)
from review_document import ReviewHeader
from review_findings import (
    _MECHANICAL_NOTE,
    _has_findings,
    annotate_prior_with_stable_ids,
    build_mechanical_review,
    post_process_findings,
)
from review_github import PRData, fetch_pr_data
from review_preflight import (
    DEFAULT_MAX_PARALLEL, FALLBACK_SUMMARY,
    GROUP_TIER3, HOLISTIC_MIN_GROUPS,
    _merge_smallest_groups,
    fetch_branch_metadata, fetch_pr_context, fetch_pr_metadata,
    group_files,
)
from review_prior import record_prior_findings
from review_types import (
    SEVERITY_MUST, SEVERITY_SHOULD,
    Group, GroupSkip, PRContext, PRMetadata, ReviewJob, ReviewMeta, ReviewType,
)
from review_prompt import (
    _is_incremental, _scope_prior_review,
    build_prompt,
)
from review_agent import _parse_session_cost
from review_phases import (
    PhaseResult, PhaseRunner,
    _phase_disprove, _phase_group_reviews,
    _phase_merge, _should_disprove, _synthesis_max_turns, _touch,
    read_scan, run_phase,
)
from review_retry import (
    GroupFailure,
    _has_output, _render_reason,
    _retry_missing_output,
)
from review_state import (
    PipelineState,
    _inject_failures_and_status,
    _resolve_recovery,
    _write_pipeline_state,
    build_failures_section,
    pipeline_status,
)

DEFAULT_MAX_COST = 20.0

DISPROVE_MIN_FINDINGS = 3


# ── Review pipelines ──────────────────────────────────────────────────────────

def _job_meta(job: ReviewJob) -> ReviewMeta:
    """This run reduced to the record of what it is reviewing.

    The single place a live `ReviewJob` becomes the review's attribution. Both
    things that state it — the `meta.json` sidecar and the document's metadata
    header — are derived from here rather than from the job, so the header
    cannot claim one head SHA while the sidecar beside it claims another.

    `pr_number` is a string on the job because that is what an argument parser
    hands over, and an int here because that is what it means; a self-review,
    which has no PR, records none.
    """
    incremental = _is_incremental(job)
    pf = job.preflight
    return ReviewMeta(
        repo=job.repo,
        pr_number=int(job.pr_number) if str(job.pr_number).isdigit() else None,
        head_sha=job.pr.head_sha,
        head_ref=job.pr.head,
        base_ref=job.pr.base,
        title=job.pr.title,
        changed_files=job.pr.changed_files,
        generator_version=job.generator_version,
        review_type=ReviewType.of(incremental),
        mode=job.mode,
        prior_sha=pf.prior_head_sha if incremental else "",
        delta_files=tuple(pf.delta_files) if incremental else (),
        started_at=job.started_at,
    )


def _write_review_sidecar(job: ReviewJob):
    """Write the sidecar recording what this run is reviewing.

    Called from every branch that reaches a review file, so the only timestamp
    it can honestly stamp is the run's own start, which it carries rather than
    takes. That a review came of the run is a separate claim, made once at the
    end by `review_common.stamp_reviewed` and only when the run got there.
    """
    write_review_meta(Path(job.artifact_dir), _job_meta(job))


def run_single_agent(job: ReviewJob, disprove: bool | None = None):
    runner = PhaseRunner(job, Phase.SINGLE)
    max_turns = runner.max_turns
    prompt = build_prompt(Phase.SINGLE, job, max_turns=max_turns)
    label = f"branch {job.pr.head}" if job.mode == Mode.SELF else f"PR #{job.pr_number} ({job.pr.title})"
    log.info(f"Running review agent on {label}...")
    log.blank()
    _touch(job.review_file)

    # `rc` tracks the latest attempt so the failure message below reports the
    # retry's exit code, not the first attempt's.
    rc = 0

    def invoke(text: str, turns: int) -> int:
        nonlocal rc
        rc = runner.invoke(text, turns)
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


def _build_meta_header(
    job: ReviewJob,
    skipped_groups: int = 0, total_groups: int = 0,
    status: ReviewStatus | None = None,
) -> str:
    meta = _job_meta(job)
    header = ReviewHeader.from_meta(
        meta,
        date=date.today().isoformat(),
        status=status,
    )
    if meta.review_type != ReviewType.INCREMENTAL:
        return header.render()

    # The prior review's own header is where its date comes from — a re-review
    # states what it is a delta against, and only that document knows.
    prior_date = ReviewHeader.parse(job.prior_review).date if job.prior_review else ""
    return replace(
        header,
        prior_date=prior_date or "unknown",
        skipped_groups=skipped_groups,
        total_groups=total_groups,
    ).render()


def _is_complete_review(review_file: str) -> bool:
    if not Path(review_file).exists():
        return False
    content = Path(review_file).read_text()
    return "## Summary" in content or "## Verdict" in content


def _build_mechanical_fallback(
    job: ReviewJob, group_count: int, merged_content: str,
    skipped_groups: int = 0,
    pipeline_state: "PipelineState | None" = None,
) -> str:
    review_dir = Path(job.review_file).parent
    status = pipeline_status(review_dir) if review_dir.exists() else ReviewStatus.ERROR
    meta = _build_meta_header(
        job, skipped_groups=skipped_groups, total_groups=group_count,
        status=status,
    )
    if job.mode == Mode.SELF:
        title = f"# Self-Review: {job.repo} — {job.pr.head}"
    else:
        title = f"# Review: {job.repo}#{job.pr_number} — {job.pr.title}"

    failures_section = build_failures_section(pipeline_state) if pipeline_state else ""

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
    # Reconciliation reads the ledger, which post-processing then strips.
    record_prior_findings(job.review_file, job.prior_review, job.wt_path)
    job.verification = post_process_findings(job.review_file, job.wt_path)


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
) -> PhaseResult:
    Path(job.review_file).write_text("")

    max_turns = _synthesis_max_turns(merged_content)
    prompt = build_prompt(
        Phase.SYNTHESIS, job, max_turns=max_turns,
        holistic_content=holistic_content, group_count=group_count,
        merged_content=merged_content,
    )
    runner = PhaseRunner(job, Phase.SYNTHESIS)
    synthesis_log = runner.session_log
    log.info(f"Phase 4: Synthesis ({max_turns} turns)...")
    log.blank()

    # `rc` tracks the latest attempt so the fallback warning below reports the
    # retry's exit code, not the first attempt's.
    rc = 0

    def invoke(text: str, turns: int) -> int:
        nonlocal rc
        rc = runner.invoke(text, turns)
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
    # Charged whether the agent produced a review or the mechanical fallback
    # did: a synthesis that fell back still spent whatever its log records.
    return PhaseResult.of(synthesis_log)


def _group_log_paths(job: ReviewJob, group_count: int) -> list[str]:
    return [
        phase_log_path(job.review_file, Phase.GROUP, i)
        for i in range(1, group_count + 1)
    ]


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


def _build_group_skips(
    incremental_skips: set[int], recovery_skips: set[int] | None,
) -> dict[int, GroupSkip]:
    """Why each skipped group is being skipped, by 1-based group index.

    Recovery wins where the two overlap: that group's output is already on
    disk, so reusing it beats re-deriving its findings from the prior review.
    No path produces that overlap today — a carried group never runs, so it
    never reaches `state.groups_done` — but the precedence is stated rather
    than assumed, because a dict merge silently picks one either way.
    """
    skips: dict[int, GroupSkip] = {i: GroupSkip.CARRIED for i in incremental_skips}
    skips.update({i: GroupSkip.RECOVERY for i in recovery_skips or ()})
    return skips


def _holistic_skip_reason(
    job: ReviewJob, incremental: bool, group_count: int,
) -> str | None:
    # Phase 1 is one scan chosen from two candidates, so it drops out only when
    # both are off — `--no-scout` alone falls back to the holistic scan, which
    # is what that flag has always meant.
    if incremental:
        return "incremental review"
    if SCAN_PHASES <= job.skip_phases:
        return " ".join(phase_skip_argv(SCAN_PHASES))
    if SCAN_PHASES <= job.skipped:
        return f"effort={job.effort}"
    if group_count < HOLISTIC_MIN_GROUPS:
        return f"{group_count} groups < {HOLISTIC_MIN_GROUPS} threshold"
    return None


def _scan_phase(job: ReviewJob) -> Phase:
    """Which of phase 1's two candidates this job runs."""
    return Phase.HOLISTIC if Phase.SCOUT in job.skipped else Phase.SCOUT


def _run_holistic_phase(
    job: ReviewJob, group_count: int, state: PipelineState, incremental: bool,
) -> PhaseResult:
    """Phase 1's scan, the log it wrote, and what that scan spent.

    A branch that reuses a prior attempt's scan reports the log at no cost:
    `_resolve_recovery` already charged the resumed run for it, so pricing it
    again here would bill that scan twice. Whether there is a prior attempt to
    reuse is `state.scanned` — the state is already here, so nothing needs to
    carry the answer in from the recovery plan.

    The chosen scan is resolved before the skip check because the skip branch
    records it too: a run that scouts and then skips phase 1 has to say scout,
    which a state field named for the holistic scan could never say.
    """
    phase = _scan_phase(job)
    reason = _holistic_skip_reason(job, incremental, group_count)
    if reason:
        log.info(f"Holistic/scout phase skipped ({reason})")
        if not state.scanned:
            state.done.add(phase)
            _write_pipeline_state(job, state)
        return PhaseResult()

    label = PHASES[phase].label
    output = phase_output_path(job.review_file, phase)
    if state.scanned and _has_output(output):
        log.info(f"Phase 1: {label} skipped (exists)")
        return PhaseResult(
            log=phase_log_path(job.review_file, phase),
            content=read_scan(phase, Path(output).read_text()),
            output=output,
        )

    result = run_phase(job, phase, f"Phase 1/{group_count}: {label}...")
    state.done.add(phase)
    _write_pipeline_state(job, state)
    return result


def _run_group_phase(
    job: ReviewJob, groups: list[Group], group_count: int,
    holistic_content: str, max_parallel: int,
    skip_groups: "dict[int, GroupSkip]", state: PipelineState,
) -> "tuple[list[str], list[GroupFailure], float]":
    group_outputs, failed_groups = _phase_group_reviews(
        groups, job, group_count, holistic_content, max_parallel,
        skip_groups=skip_groups, pipeline_state=state,
    )

    cost = 0.0
    new_group_indices = [
        i for i in range(1, len(group_outputs) + 1)
        if i not in skip_groups
    ]
    for i in new_group_indices:
        cost += _parse_session_cost(phase_log_path(job.review_file, Phase.GROUP, i))

    return group_outputs, failed_groups, cost


def _run_synthesis_or_fallback(
    job: ReviewJob, state: PipelineState,
    holistic_content: str, group_count: int,
    merged_content: str, failed_groups: "list[GroupFailure]",
    n_skipped: int, cost_so_far: float, max_cost: float,
) -> PhaseResult:
    """The synthesis session log and what synthesis spent.

    Every branch below that reaches the review file without an agent — a clean
    review, a mechanical fallback, a skip — spends nothing and says so, so the
    caller's running total means the same thing at the disprove gate as it did
    here.
    """
    all_groups_failed = len(failed_groups) == group_count

    if not _has_findings(merged_content) and not failed_groups:
        log.info("No findings from any group — writing clean review")
        _write_clean_review(job, group_count, skipped_groups=n_skipped)
        state.done.add(Phase.SYNTHESIS)
        _write_pipeline_state(job, state)
        return PhaseResult()

    if all_groups_failed:
        if Phase.GROUP in job.skipped:
            log.warn("Group phase skipped — the review reports what it has")
            why = Diagnosis(DiagnosisKind.SKIPPED, detail="--no-group")
        else:
            log.warn("All group agents failed — skipping synthesis")
            why = Diagnosis(DiagnosisKind.ALL_GROUPS_FAILED)
        # Recorded before the fallback is built, not after: the meta header the
        # fallback carries reads the status back off this file, so a verdict
        # written afterwards leaves the document claiming the run completed.
        state.done.add(Phase.SYNTHESIS)
        state.failed[Phase.SYNTHESIS] = why
        _write_pipeline_state(job, state)
        fallback = _build_mechanical_fallback(
            job, group_count, merged_content, skipped_groups=n_skipped,
            pipeline_state=state,
        )
        Path(job.review_file).write_text(fallback)
        _write_review_sidecar(job)
        return PhaseResult()

    if Phase.SYNTHESIS in job.skipped:
        log.info("Synthesis skipped — using mechanical merge")
        Path(job.review_file).write_text(merged_content)
        _post_process_review(job)
        _write_review_sidecar(job)
        state.done.add(Phase.SYNTHESIS)
        _write_pipeline_state(job, state)
        _inject_failures_and_status(job.review_file, state)
        return PhaseResult()

    if cost_so_far > max_cost:
        log.warn("Using merged group output as final review (synthesis skipped due to budget)")
        Path(job.review_file).write_text(merged_content)
        _write_review_sidecar(job)
        state.done.add(Phase.SYNTHESIS)
        state.failed[Phase.SYNTHESIS] = Diagnosis(DiagnosisKind.BUDGET_EXCEEDED)
        _write_pipeline_state(job, state)
        _inject_failures_and_status(job.review_file, state)
        return PhaseResult()

    result = _phase_synthesis(
        job, holistic_content, group_count, merged_content,
        skipped_groups=n_skipped,
    )
    state.done.add(Phase.SYNTHESIS)
    review_content = Path(job.review_file).read_text() if Path(job.review_file).exists() else ""
    if _MECHANICAL_NOTE in review_content or FALLBACK_SUMMARY in review_content:
        state.failed[Phase.SYNTHESIS] = Diagnosis(DiagnosisKind.MECHANICAL_FALLBACK)
    _write_pipeline_state(job, state)
    _inject_failures_and_status(job.review_file, state)
    return result


def _run_disprove_gate(
    job: ReviewJob, state: PipelineState,
    disprove: bool | None, cost_so_far: float, max_cost: float,
) -> PhaseResult:
    """The disprove gate's session log and what the gate spent.

    Every path out of here records the gate done, the ones that decline to run
    it included. `_resolve_recovery` reads a state file with no disprove entry
    as a run that died inside the gate, and that reading only holds if a gate
    which reached a conclusion always says so.

    Only the two paths that wanted the gate and did not get it record a
    failure, so only those two reach the Agent Failures table and the recover
    hint: declining for a stated reason is a decision, and offering to retry a
    decision would send `--recover` after work nothing went wrong with.
    """
    result = PhaseResult()
    failure: Diagnosis | None = None

    if not _should_disprove(job, disprove):
        log.info("Disprove gate off — keeping all findings")
    elif cost_so_far > max_cost:
        log.warn(
            f"Budget exceeded before the disprove gate "
            f"(${cost_so_far:.2f}/${max_cost:.2f}) — findings go unchallenged"
        )
        failure = Diagnosis(DiagnosisKind.BUDGET_EXCEEDED)
    else:
        counts = count_severities(Path(job.review_file))
        ms_count = counts[SEVERITY_MUST] + counts[SEVERITY_SHOULD]
        if disprove is True or ms_count >= DISPROVE_MIN_FINDINGS:
            result = _phase_disprove(job)
            # None when the gate declined inside `_phase_disprove` for want of
            # findings to challenge; a diagnosis only when its agent ran and
            # came back with nothing.
            failure = result.diagnosis
        else:
            log.info(
                f"Skipping disprove — only {ms_count} M/S findings "
                f"(threshold: {DISPROVE_MIN_FINDINGS})"
            )

    state.done.add(Phase.DISPROVE)
    if failure:
        state.failed[Phase.DISPROVE] = failure
    _write_pipeline_state(job, state)
    _inject_failures_and_status(job.review_file, state)
    return result


def run_multi_phase(
    job: ReviewJob, max_parallel: int = DEFAULT_MAX_PARALLEL,
    max_cost: float = DEFAULT_MAX_COST,
    max_groups: int | None = None,
    disprove: bool | None = None,
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

    recovery = _resolve_recovery(job, groups)
    if recovery.already_complete:
        log.info("Review already complete — use --force to re-run from scratch")
        return

    cost_so_far = recovery.cost_so_far
    skip_groups = recovery.skip_groups
    state = recovery.state

    if state is None:
        state = PipelineState(
            head_sha=job.pr.head_sha,
            group_names=[g.name for g in groups],
            review_type=ReviewType.of(incremental),
            prior_sha=job.preflight.prior_head_sha if incremental else "",
            skipped_groups=sorted(incremental_skips),
        )
        _write_pipeline_state(job, state)

    group_skips = _build_group_skips(incremental_skips, skip_groups)

    # ── Phase 1: Scout/Holistic ─────────────────────────────────────────────
    holistic = _run_holistic_phase(job, group_count, state, incremental)
    cost_so_far += holistic.cost

    # ── Phase 2: Groups ───────────────────────────────────────────────────────
    # Two ways every group ends up unreviewed without a single agent running,
    # and each group carries the reason rather than an absence: the merge and
    # the failures table both report what happened, and "no output" from a
    # deliberate skip reads the same as one from a crash.
    unrun: Diagnosis | None = None
    if Phase.GROUP in job.skipped:
        log.warn("Group phase skipped (--no-group) — the review will be partial")
        unrun = Diagnosis(DiagnosisKind.SKIPPED, detail="--no-group")
    elif cost_so_far > max_cost:
        log.warn(f"Budget exceeded after holistic phase (${cost_so_far:.2f}/${max_cost:.2f}) — skipping groups")
        unrun = Diagnosis(DiagnosisKind.BUDGET_EXCEEDED)

    if unrun:
        group_outputs: list[str] = []
        failed_groups: list[GroupFailure] = [
            GroupFailure(g.name, unrun) for g in groups
        ]
    else:
        group_outputs, failed_groups, groups_cost = _run_group_phase(
            job, groups, group_count, holistic.content, max_parallel,
            group_skips, state,
        )
        cost_so_far += groups_cost

    # ── Phase 3: Merge ───────────────────────────────────────────────────────
    merged_content = _phase_merge(group_outputs[:], failed_groups)

    if carried_forward:
        merged_content += "\n" + carried_forward

    # ── Phase 4: Synthesis ───────────────────────────────────────────────────
    n_skipped = len(incremental_skips)
    if recovery.resume_at_gate and _is_complete_review(job.review_file):
        # The prior run synthesised cleanly and only the gate is outstanding, so
        # the review on disk is the one this run would write again. Re-checking
        # the file keeps the plan honest about a review deleted between runs.
        log.info("Phase 4: Synthesis — the prior run's review stands, resuming at the gate")
        synthesis = PhaseResult()
    else:
        synthesis = _run_synthesis_or_fallback(
            job, state, holistic.content, group_count,
            merged_content, failed_groups, n_skipped, cost_so_far, max_cost,
        )
    cost_so_far += synthesis.cost

    # ── Phase 4.5: Disprove-it gate ─────────────────────────────────────────
    disprove_result = _run_disprove_gate(job, state, disprove, cost_so_far, max_cost)

    # ── Consolidate logs ─────────────────────────────────────────────────────
    # Removing what this run leaves behind is the orchestrator's, not the
    # pipeline's: phases run after this function returns. See
    # `review_gc.cleaned_on_success`.
    _consolidate_logs(
        job, holistic.log, group_count, synthesis.log,
        disprove_log=disprove_result.log,
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
            f"Reviewing local HEAD {git_client.abbrev(local.head_sha)} "
            f"(PR head is {git_client.abbrev(pr.head_sha)})"
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
