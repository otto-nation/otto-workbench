"""Pipeline orchestration for claude-review.

Drives the single-agent and multi-phase runs end to end: sequencing the phases
review.steps defines, deciding what a resumed run may skip, consolidating the
session logs, and fetching the PR metadata a run starts from.

The run ends when the review file is written — what happens to the findings
afterwards belongs to review.fix, and removing what the run left behind belongs
to review.gc, which the orchestrator runs once every phase is done.
"""

# doc-group: pipeline

from __future__ import annotations

import os
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path

from git import client as git_client
from core import log
from agent.diagnosis import Diagnosis, DiagnosisKind
from agent.types import EFFORT_PRESETS
from gh.types import PRContext, PRMetadata
from core.phases import Mode, Phase
from review.paths import phase_log_path
from review.collect import fetch_branch_metadata
from gh.pr_reads import (
    PRData, fetch_pr_context, fetch_pr_data, fetch_pr_metadata,
)
from review.grouping import (
    GROUP_TIER3, group_files, merge_smallest_groups,
)
from review.outcome import _post_process_review, _write_review_sidecar, is_complete_review
from review.types import ReviewJob, ReviewType
from review.prompt import PromptTooLarge
from review.prompt_sections import _is_incremental
from review.registry import build_prompt
from review.phases import PhaseResult, PhaseRunner, _should_disprove, _touch
from review.steps import (
    _build_group_skips, _carry_forward_prior_findings, _identify_incremental_skips,
    _phase_disprove, _phase_merge, _run_disprove_gate, _run_group_phase,
    _run_holistic_phase, _run_synthesis_or_fallback,
)
from review.retry import GroupFailure, _has_output, _render_reason, _retry_missing_output
from review.state import PipelineState, _resolve_recovery, _write_pipeline_state

DEFAULT_MAX_COST = 20.0

# Concurrent group agents used to hit 429 rate limits, so the default became
# one. Concurrency does not multiply spend: every group in a phase launches
# before the next gate regardless, so raising it only changes when the money
# is spent, not how much. The count is now derived from free capacity unless
# --max-parallel pins it. A second pipeline raises the load average, so the
# clamp yields to it.
DEFAULT_MAX_PARALLEL = None

# ceiling: cap of 4 group agents. Upgrade trigger: once the trail shows quota
# diagnoses staying rare at 4 on a busy machine, raise the cap toward the
# core count.
MAX_PARALLEL_CAP = 4
MAX_PARALLEL_FLOOR = 1


def parallel_worker_count(
    group_count: int, cores: int, load: float,
    cap: int = MAX_PARALLEL_CAP,
) -> int:
    """How many group agents free capacity can take, given a measured machine.

    ``min(group_count, clamp(cores - load, 1, cap))``. Truncates toward zero
    so a fraction of a free core does not round up into another agent.
    """
    free = int(cores - load)
    return min(group_count, max(MAX_PARALLEL_FLOOR, min(cap, free)))


def resolve_max_parallel(
    group_count: int, requested: int | None = None,
    *,
    cores: int | None = None,
    load: float | None = None,
) -> int:
    """The worker count this run will use, and a line saying why.

    An explicit ``--max-parallel`` wins, like ``TEST_JOBS`` on the test runner.
    Otherwise free capacity: cores minus the one-minute load average, clamped
    to 1..4. Prints the choice because an invisible wait is indistinguishable
    from a slow model, and a run sized down by another process looks exactly
    like a slow one unless something says so.
    """
    if requested is not None:
        workers = min(requested, group_count)
        log.info(f"Group parallelism: {workers} worker(s) — set by --max-parallel")
        return workers
    if cores is None:
        cores = os.cpu_count() or 1
    if load is None:
        try:
            load = os.getloadavg()[0]
        except (OSError, AttributeError):
            # Unreadable load is treated as a full box so we stay serial rather
            # than fan out on a machine we cannot size.
            load = float(cores)
    workers = parallel_worker_count(group_count, cores, load)
    log.info(
        f"Group parallelism: {workers} worker(s) "
        f"({cores} cores, load {load:.2f}, capped at {MAX_PARALLEL_CAP})"
    )
    return workers


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
    job: ReviewJob, max_parallel: int | None = DEFAULT_MAX_PARALLEL,
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
        workers = resolve_max_parallel(group_count, max_parallel)
        group_phase = _run_group_phase(
            job, groups, group_count, holistic.content, workers,
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
    # `review.gc.cleaned_on_success`.
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


def fetch_metadata(
    repo: str, pr_number: str, mode: Mode, wt_path: str, pin_sha: str = "",
    base: str = "",
) -> RunContext:
    """Everything a review run needs to know about what it is reviewing.

    `mode` decides where that comes from: a self-review of an unopened branch
    reads the work tree at `wt_path` alone, and every other case reads the PR
    `repo`/`pr_number` names, pinned to `pin_sha` when one is given.

    `base` is the branch to measure against, already resolved by the caller
    through `pr.context.base_branch`, and it wins over the `baseRefName` read
    here. Normally the two agree: the ladder's second rung *is* GitHub's base,
    read by whichever call resolved the context. Where they differ, the
    caller's is the one the supersession gate already measured against, and a
    run whose gate and diff disagree about the base is worse than either answer
    on its own — it refuses over commits it then declines to review.

    Empty means the caller resolved nothing, and each path falls back to what
    it did before there was a ladder to consult.
    """
    if mode == Mode.SELF and not pr_number:
        log.info("Gathering branch metadata...")
        return RunContext(fetch_branch_metadata(wt_path, base or None), PRContext(), None)
    log.info("Fetching PR data...")
    if mode == Mode.SELF:
        # Sequential: the local read needs the PR's base branch to pick its range.
        pr = fetch_pr_metadata(repo, pr_number)
        pr = replace(pr, base=base) if base else pr
        return RunContext(
            _with_local_diff(pr, fetch_branch_metadata(wt_path, pr.base)), PRContext(), None,
        )
    with ThreadPoolExecutor(max_workers=2) as pool:
        pr_future = pool.submit(fetch_pr_metadata, repo, pr_number, pin_sha, wt_path)
        pd_future = pool.submit(fetch_pr_data, repo, pr_number)
        pr_data = pd_future.result()
        ctx = fetch_pr_context(repo, pr_number, pr_data)
        pr = pr_future.result()
        return RunContext(replace(pr, base=base) if base else pr, ctx, pr_data)
