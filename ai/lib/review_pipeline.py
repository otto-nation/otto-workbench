"""Pipeline orchestration for claude-review.

Drives the single-agent and multi-phase runs end to end: sequencing the phases
review_steps defines, deciding what a resumed run may skip, consolidating the
session logs, and fetching the PR metadata a run starts from.

The run ends when the review file is written — what happens to the findings
afterwards belongs to review_fix, and removing what the run left behind belongs
to review_gc, which the orchestrator runs once every phase is done.
"""

# doc-group: pipeline

from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path

import git_client
import log
from agent_diagnosis import Diagnosis, DiagnosisKind
from agent_types import EFFORT_PRESETS, Mode, Phase
from review_paths import phase_log_path
from review_collect import fetch_branch_metadata
from review_github import (
    PRData, fetch_pr_context, fetch_pr_data, fetch_pr_metadata,
)
from review_grouping import (
    GROUP_TIER3, group_files, merge_smallest_groups,
)
from review_outcome import _post_process_review, _write_review_sidecar, is_complete_review
from review_types import PRContext, PRMetadata, ReviewJob, ReviewType
from review_prompt import PromptTooLarge
from review_prompt_sections import _is_incremental
from review_registry import build_prompt
from review_phases import PhaseResult, PhaseRunner, _should_disprove, _touch
from review_steps import (
    _build_group_skips, _carry_forward_prior_findings, _identify_incremental_skips,
    _phase_disprove, _phase_merge, _run_disprove_gate, _run_group_phase,
    _run_holistic_phase, _run_synthesis_or_fallback,
)
from review_retry import GroupFailure, _has_output, _render_reason, _retry_missing_output
from review_state import PipelineState, _resolve_recovery, _write_pipeline_state

DEFAULT_MAX_COST = 20.0

# One group review at a time by default. Concurrency here multiplies the agent
# spend against a budget the run checks between phases, so raising it is the
# caller's call rather than the pipeline's.
DEFAULT_MAX_PARALLEL = 1


# ── Review pipelines ──────────────────────────────────────────────────────────


def run_single_agent(job: ReviewJob, disprove: bool | None = None):
    runner = PhaseRunner(job, Phase.SINGLE)
    max_turns = runner.max_turns
    try:
        prompt = build_prompt(Phase.SINGLE, job, max_turns=max_turns)
    except PromptTooLarge as exc:
        # The single-agent run is the whole review — there is no second phase to
        # fall back on and nothing written yet to salvage, so this exits rather
        # than degrading. A PR this size wants the multi-phase path, which splits
        # it into groups small enough to prompt.
        log.error(f"Review cannot be prompted: {exc}")
        log.dim("Re-run at an effort level that reviews this PR in groups.")
        sys.exit(1)
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


def run_multi_phase(
    job: ReviewJob, max_parallel: int = DEFAULT_MAX_PARALLEL,
    max_cost: float = DEFAULT_MAX_COST,
    max_groups: int | None = None,
    disprove: bool | None = None,
):
    groups = group_files(job.pr)
    effective_max_groups = max_groups or EFFORT_PRESETS[job.effort].max_groups
    groups = merge_smallest_groups(groups, effective_max_groups)

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
        group_phase = _run_group_phase(
            job, groups, group_count, holistic.content, max_parallel,
            group_skips, state,
        )
        group_outputs, failed_groups = group_phase.outputs, group_phase.failures
        cost_so_far += group_phase.cost

    # ── Phase 3: Merge ───────────────────────────────────────────────────────
    merged_content = _phase_merge(group_outputs[:], failed_groups)

    if carried_forward:
        merged_content += "\n" + carried_forward

    # ── Phase 4: Synthesis ───────────────────────────────────────────────────
    n_skipped = len(incremental_skips)
    if recovery.resume_at_gate and is_complete_review(job.review_file):
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


@dataclass(frozen=True)
class RunContext:
    """What a review run starts from: the PR, its narrative, and its raw data.

    `data` is None for a self-review, which has no PR behind it to fetch.
    """

    pr: PRMetadata
    context: PRContext
    data: "PRData | None"


def _fetch_metadata(
    repo: str, pr_number: str, mode: Mode, wt_path: str, pin_sha: str = "",
) -> RunContext:
    if mode == Mode.SELF and not pr_number:
        log.info("Gathering branch metadata...")
        return RunContext(fetch_branch_metadata(wt_path), PRContext(), None)
    log.info("Fetching PR data...")
    if mode == Mode.SELF:
        # Sequential: the local read needs the PR's base branch to pick its range.
        pr = fetch_pr_metadata(repo, pr_number)
        return RunContext(
            _with_local_diff(pr, fetch_branch_metadata(wt_path, pr.base)), PRContext(), None,
        )
    with ThreadPoolExecutor(max_workers=2) as pool:
        pr_future = pool.submit(fetch_pr_metadata, repo, pr_number, pin_sha, wt_path)
        pd_future = pool.submit(fetch_pr_data, repo, pr_number)
        pr_data = pd_future.result()
        ctx = fetch_pr_context(repo, pr_number, pr_data)
        return RunContext(pr_future.result(), ctx, pr_data)
