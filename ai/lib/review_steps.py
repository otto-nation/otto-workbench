"""One function per phase of a multi-phase review.

Each takes the job and the run's state, does that phase's work, and reports
what it spent. They are ordered here as the run orders them, but none of them
calls the next: sequencing is `review_pipeline`'s, so a phase can be skipped,
resumed, or budgeted out without any other phase knowing.

`review_phases` owns the phase runner, the group fan-out, and the disprove and
synthesis rules this module drives without reimplementing: invoking an agent,
running the group phase (`_phase_group_reviews`), deciding whether the
disprove gate runs (`_should_disprove`), and sizing the synthesis turn budget
(`_synthesis_max_turns`) all read from there. Writing the result to the review
file is `review_outcome`'s.
"""

# doc-group: pipeline

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import log
from agent_diagnosis import Diagnosis, DiagnosisKind
from agent_registry import PHASES, SCAN_PHASES, phase_skip_argv
from phases import Phase
from review_agent import _parse_session_cost
from review_document import ReviewDocument
from review_grouping import HOLISTIC_MIN_GROUPS
from review_merge import annotate_prior_with_stable_ids, merge_reviews
from review_outcome import (
    _build_mechanical_fallback, _document, _no_synthesis_body,
    _post_process_review, _post_processed_body, _write_clean_review,
    _write_mechanical_fallback, _write_review_sidecar, is_complete_review,
)
from review_paths import phase_log_path, phase_output_path
from review_phases import (
    PhaseResult, PhaseRunner,
    _phase_group_reviews, _should_disprove, _synthesis_max_turns,
    read_scan, run_phase,
)
from review_prompt import PromptTooLarge
from review_prompt_prior import _scope_prior_review
from review_reconcile import passed_over
from review_registry import build_prompt
from review_retry import GroupFailure, _has_output, _retry_missing_output
from review_state import (
    PipelineState, _inject_failures_and_status, _write_pipeline_state,
)
from review_types import (
    SEVERITY_MUST, SEVERITY_SHOULD, Group, GroupSkip, ReviewJob,
)
from review_verdict import BUDGET_SUMMARY, SKIPPED_SUMMARY, open_counts
from review_verify import apply_disprove_results, parse_disprove_output
from text import plural

DISPROVE_MIN_FINDINGS = 3


@dataclass(frozen=True)
class GroupPhaseResult:
    """What the group phase produced and what its agents cost."""

    outputs: list[str]
    failures: list[GroupFailure]
    cost: float


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
) -> GroupPhaseResult:
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

    return GroupPhaseResult(outputs=group_outputs, failures=failed_groups, cost=cost)


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

    if not ReviewDocument(body=merged_content).findings and not failed_groups:
        log.info("No findings from any group — writing clean review")
        _write_clean_review(
            job, group_count, merged_content, skipped_groups=n_skipped,
        )
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
        _build_mechanical_fallback(
            job, group_count, merged_content, skipped_groups=n_skipped,
            pipeline_state=state,
        ).write(job.review_file)
        _write_review_sidecar(job)
        return PhaseResult()

    if Phase.SYNTHESIS in job.skipped:
        log.info("Synthesis skipped — using mechanical merge")
        _document(
            job,
            _no_synthesis_body(
                job, _post_processed_body(job, merged_content),
                group_count, SKIPPED_SUMMARY,
            ),
            skipped_groups=n_skipped, total_groups=group_count,
        ).write(job.review_file)
        _write_review_sidecar(job)
        state.done.add(Phase.SYNTHESIS)
        _write_pipeline_state(job, state)
        _inject_failures_and_status(job.review_file, state)
        return PhaseResult()

    if cost_so_far > max_cost:
        log.warn("Using merged group output as final review (synthesis skipped due to budget)")
        # Post-processed like every other path that ships group output: the
        # budget that ran out buys agent turns, and evidence verification and
        # prior-finding reconciliation are local reads that cost none of it.
        # Skipping them here shipped findings nothing had checked against the
        # tree, on the run least able to afford an unchecked claim.
        _document(
            job,
            _no_synthesis_body(
                job, _post_processed_body(job, merged_content),
                group_count, BUDGET_SUMMARY,
            ),
            skipped_groups=n_skipped, total_groups=group_count,
        ).write(job.review_file)
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
    if result.diagnosis:
        state.failed[Phase.SYNTHESIS] = result.diagnosis
    _write_pipeline_state(job, state)
    _inject_failures_and_status(job.review_file, state)
    return result


def _phase_synthesis(
    job: ReviewJob, holistic_content: str,
    group_count: int, merged_content: str,
    skipped_groups: int = 0,
) -> PhaseResult:
    Path(job.review_file).write_text("")

    max_turns = _synthesis_max_turns(merged_content)
    runner = PhaseRunner(job, Phase.SYNTHESIS)
    synthesis_log = runner.session_log
    # The same reconciliation the run ends with, asked one phase early, while
    # there is still an agent that can act on the answer. Afterwards the only
    # thing left to do about a prior finding nobody accounted for is report it.
    unaccounted = passed_over(job.prior_review, merged_content, job.wt_path, job.pr.head_sha)
    if unaccounted:
        log.dim(
            f"{len(unaccounted)} prior finding{plural(len(unaccounted))} unaccounted for "
            "— synthesis is asked to settle them"
        )
    try:
        prompt = build_prompt(
            Phase.SYNTHESIS, job, max_turns=max_turns,
            holistic_content=holistic_content, group_count=group_count,
            merged_content=merged_content, unaccounted_prior=unaccounted,
        )
    except PromptTooLarge as exc:
        # The group findings are already on disk; the synthesis agent only
        # writes them up. Merging them mechanically loses the prose and keeps
        # every finding, which is the same trade the fallback below makes for
        # an agent that failed — and it is strictly better than discarding a
        # phase's worth of work because its cover letter would not fit.
        log.warn(f"Synthesis cannot be prompted ({exc}) — falling back to mechanical merge")
        _write_mechanical_fallback(
            job, group_count, merged_content, skipped_groups=skipped_groups,
        )
        _write_review_sidecar(job)
        return PhaseResult.of(
            synthesis_log, diagnosis=Diagnosis(DiagnosisKind.MECHANICAL_FALLBACK),
        )
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

    if is_complete_review(job.review_file):
        _post_process_review(job)
        diagnosis = None
    else:
        reason = "no output" if not _has_output(job.review_file) else "incomplete output"
        detail = f"exited with code {rc} ({reason})" if rc != 0 else reason
        log.warn(f"Synthesis agent {detail} — falling back to mechanical merge")
        _write_mechanical_fallback(
            job, group_count, merged_content, skipped_groups=skipped_groups,
        )
        diagnosis = Diagnosis(DiagnosisKind.MECHANICAL_FALLBACK)

    _write_review_sidecar(job)
    # Charged whether the agent produced a review or the mechanical fallback
    # did: a synthesis that fell back still spent whatever its log records.
    return PhaseResult.of(synthesis_log, diagnosis=diagnosis)


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


def _phase_disprove(job: ReviewJob) -> PhaseResult:
    review_content = Path(job.review_file).read_text() if Path(job.review_file).exists() else ""
    counts = ReviewDocument(body=review_content).open_counts
    ms_count = counts[SEVERITY_MUST] + counts[SEVERITY_SHOULD]
    label = PHASES[Phase.DISPROVE].label
    if ms_count == 0:
        log.info(f"{label} skipped — no must-fix or should-fix findings")
        return PhaseResult()

    result = run_phase(
        job, Phase.DISPROVE,
        f"{label} — challenging {ms_count} must-fix/should-fix findings...",
        review_content=review_content,
    )
    if not result.content:
        return result

    results = parse_disprove_output(result.content)
    updated_text, summary = apply_disprove_results(review_content, results)
    falsified = summary.get("falsified", 0)
    if falsified > 0:
        Path(job.review_file).write_text(updated_text)
        log.info(f"{label}: {summary['survived']} survived, {falsified} falsified")
        _log_disprove_falsified(summary)
    else:
        log.info(f"{label}: all {summary['survived']} findings survived")

    return result


def _log_disprove_falsified(summary: dict) -> None:
    for fid in summary.get("falsified_ids", []):
        reason = summary.get("reasons", {}).get(fid, "")
        log.dim(f"  Falsified [{fid}]: {reason}")


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
        counts = open_counts(ReviewDocument.read(job.review_file))
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


def _phase_merge(group_outputs: list[str], failed_groups: list[GroupFailure]) -> str:
    log.info("Phase 3: Merging findings...")
    merged_content = merge_reviews(group_outputs)

    if failed_groups:
        merged_content += "\n## Review gaps\n"
        merged_content += "The following file groups were not reviewed due to agent failure:\n"
        for failed in failed_groups:
            merged_content += f"- {failed.group}: {failed.diagnosis.message}\n"

    return merged_content
