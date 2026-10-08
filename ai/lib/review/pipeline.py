"""Pipeline orchestration for review.

Drives the single-agent and multi-phase runs end to end: sequencing the phases
review.steps defines, deciding what a resumed run may skip, consolidating the
session logs, and fetching the PR metadata a run starts from.

The run ends when the review file is written — what happens to the findings
afterwards belongs to review.fix, and removing what the run left behind belongs
to review.gc, which the orchestrator runs once every phase is done.

Two ways every group ends up unreviewed without a single agent running, and
each group carries the reason rather than an absence: ``--no-group`` (or the
effort preset that includes it) and a budget already blown after phase 1. The
merge still runs and reports every group as skipped; the status header says
``partial``. ``--no-synthesis`` is the same claim one phase later: the mechanical
merge is written, synthesis is marked done-and-skipped, and the header must not
read as a clean review of nothing. ``review.outcome`` and ``review.verdict``
word that header; this module is the path that takes it.

Self-review vs PR mode is decided here too, at ``fetch_metadata`` /
``_with_local_diff``. A self-review reads the worktree: the head SHA and the
changed-file list come from git, never from GitHub. Taking them from the PR
silently drops every unpushed commit — the diff is local but the file list is
not, so the review never opens the files those commits touched. When the branch
already has a PR, its title, body and labels supply context but do not define
the diff; the run logs the local head whenever it differs from the PR's. PR
mode reviews the pushed commits only.
"""

# doc-group: pipeline

from __future__ import annotations

import contextlib
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path

import git.client
import core.job_slots
import core.log
from agent.diagnosis import Diagnosis, DiagnosisKind
from agent.types import EFFORT_PRESETS
from gh.types import PRContext, PRMetadata
from core.phases import Mode, Phase
from review.paths import phase_log_path
from gh.pr_data import PRData, fetch_pr_data
from gh.pr_reads import fetch_pr_context, fetch_pr_metadata
from agent.backend import selected_backend
from agent.phases import phase_model
from review.budget import (
    MIN_DIFF_BYTES, TEMPLATE_OVERHEAD_BYTES, fixed_preflight_bytes,
    ladder_target_bytes,
)
from review.collect import diff_section_sizes, fetch_branch_metadata
from review.grouping import (
    GROUP_TIER3, Group, estimate_group_diff_bytes, group_files, merge_smallest_groups,
)
from review.overflow import run_with_overflow_recovery
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
# is spent, not how much. The count is now taken from the machine's slot pool
# unless --max-parallel pins it, so a second pipeline sees the first one's
# held slots rather than a load average that has not caught up with them.
DEFAULT_MAX_PARALLEL = None

# ceiling: cap of 4 group agents. Upgrade trigger: once the trail shows quota
# diagnoses staying rare at 4 on a busy machine, raise the cap toward the
# core count.
MAX_PARALLEL_CAP = 4
MAX_PARALLEL_FLOOR = 1


@contextlib.contextmanager
def hold_group_workers(
    group_count: int, requested: int | None = None, *, cores: int | None = None,
):
    """Hold the machine slots this group phase runs on, yielding the count.

    A context manager rather than a function returning a number, because the
    slots are *held* for the body: ``job_slots.claim`` releases its flocks in a
    ``finally``, so computing a count and leaving the ``with`` would free the
    capacity before a single agent launched. That is the whole difference from
    the load average this replaces — a reading cannot lag when there is no
    reading, but only while the claim is open.

    Wrapped once around the entire fan-out, never per worker. ``claim`` mutates
    ``os.environ`` and is not thread-safe, so two workers claiming would race
    the marker, and the first to finish would drop flocks its siblings were
    still running on.

    ``want`` stays at the cap rather than the group count or the pool size: an
    idle 18-core machine would otherwise grant 17, and the ``ceiling:`` on
    ``MAX_PARALLEL_CAP`` would be a lie. An explicit ``--max-parallel`` skips
    the pool entirely, the way ``TEST_JOBS`` does on the test runner.

    Prints the choice because an invisible wait is indistinguishable from a
    slow model, and a run sized down by another process looks exactly like a
    slow one unless something says so.
    """
    if requested is not None:
        workers = min(requested, group_count)
        core.log.info(f"Group parallelism: {workers} worker(s) — set by --max-parallel")
        yield workers
        return

    if cores is None:
        cores = os.cpu_count() or 1
    want = min(group_count, MAX_PARALLEL_CAP)
    with core.job_slots.claim(
        want, MAX_PARALLEL_FLOOR, cores, command="pr review (group phase)",
    ) as granted:
        workers = min(granted, want)
        core.log.info(
            f"Group parallelism: {workers} worker(s) "
            f"(pool granted {granted} of {want}, capped at {MAX_PARALLEL_CAP})"
        )
        yield workers


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
        core.log.error(f"Review cannot be prompted: {exc}")
        core.log.dim("Re-run at an effort level that reviews this PR in groups.")
        sys.exit(1)
    label = f"branch {job.pr.head}" if job.mode == Mode.SELF else f"PR #{job.pr_number} ({job.pr.title})"
    core.log.info(f"Running review agent on {label}...")
    core.log.blank()
    _touch(job.review_file)

    # `rc` tracks the latest attempt so the failure message below reports the
    # retry's exit code, not the first attempt's.
    rc = 0

    def invoke(text: str, turns: int) -> int:
        nonlocal rc
        rc = runner.invoke(text, turns)
        return rc

    diagnosis = run_with_overflow_recovery(
        prompt,
        invoke=lambda text: invoke(text, max_turns),
        after=lambda text: _retry_missing_output(
            invoke, text, job.session_log, job.review_file,
            label="Review agent", max_turns=max_turns,
        ),
        has_output=lambda: _has_output(job.review_file),
        rebuild=lambda ladder: build_prompt(
            Phase.SINGLE, job, max_turns=max_turns, ladder_bytes=ladder,
        ),
    ).diagnosis

    if not _has_output(job.review_file):
        detail = f"exited with code {rc}" if rc != 0 else "completed"
        core.log.error(
            f"review agent {detail} and produced no review file "
            f"({_render_reason(diagnosis)})"
        )
        core.log.dim(f"Session log: {job.session_log}")
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


def _group_merge_cap(job: ReviewJob) -> int:
    """The most diff a merged group may carry and still fit its prompt.

    The group prompt's ladder target, less what a group prompt spends before
    its diff: the template and the project context no lever can cut. File
    contents and the delta are levers the ladder pulls first, so they are not
    reserved here. Every profile is charged although a group renders only the
    ones matching its files, which errs toward stopping a merge early.

    Never below `MIN_DIFF_BYTES`: a repo whose fixed context alone fills the
    target still gets groups the ladder can floor, rather than no merges at
    all.
    """
    model = phase_model(Phase.GROUP, job.model or None, job.config)
    pf = job.preflight
    fixed = TEMPLATE_OVERHEAD_BYTES
    if pf is not None:
        fixed += fixed_preflight_bytes(
            pf.instructions_md, pf.architecture_md, pf.review_checklists,
            pf.review_profiles,
        )
    return max(MIN_DIFF_BYTES, ladder_target_bytes(model, selected_backend()) - fixed)


def run_multi_phase(
    job: ReviewJob, max_parallel: int | None = DEFAULT_MAX_PARALLEL,
    max_cost: float = DEFAULT_MAX_COST,
    max_groups: int | None = None,
    disprove: bool | None = None,
):
    groups = group_files(job.pr)
    effective_max_groups = max_groups or EFFORT_PRESETS[job.effort].max_groups
    group_cap = _group_merge_cap(job)
    collected_diff = job.preflight.diff if job.preflight else ""

    # Scanned once: the merge loop asks for every candidate pair each round.
    section_sizes = diff_section_sizes(collected_diff) if collected_diff else None

    def _group_diff_bytes(group: Group) -> int:
        if section_sizes is not None:
            return sum(section_sizes.get(f, 0) for f in group.files)
        return estimate_group_diff_bytes(group)

    groups = merge_smallest_groups(
        groups, effective_max_groups,
        max_diff_bytes=group_cap, group_diff_bytes=_group_diff_bytes,
    )

    if not job.include_generated:
        before = len(groups)
        groups = [g for g in groups if g.name != GROUP_TIER3]
        if len(groups) < before:
            core.log.info("Skipping tier3-generated group (use --generated to include)")

    group_count = len(groups)

    core.log.info(f"Large PR ({job.pr.total_lines} lines, {job.pr.changed_files} files) — {group_count} file groups")

    incremental = _is_incremental(job)
    incremental_skips: set[int] = set()
    carried_forward = ""

    if incremental:
        incremental_skips = _identify_incremental_skips(
            groups, job.preflight.delta_files,
        )
        if incremental_skips:
            affected = group_count - len(incremental_skips)
            core.log.info(
                f"Incremental: {affected}/{group_count} groups affected, "
                f"{len(incremental_skips)} unchanged (findings carried forward)"
            )
            carried_forward = _carry_forward_prior_findings(
                job.prior_review, groups, incremental_skips,
            )

    recovery = _resolve_recovery(job, groups)
    if recovery.already_complete:
        core.log.info("Review already complete — use --force to re-run from scratch")
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
        core.log.warn("Group phase skipped (--no-group) — the review will be partial")
        unrun = Diagnosis(DiagnosisKind.SKIPPED, detail="--no-group")
    elif cost_so_far > max_cost:
        core.log.warn(f"Budget exceeded after holistic phase (${cost_so_far:.2f}/${max_cost:.2f}) — skipping groups")
        unrun = Diagnosis(DiagnosisKind.BUDGET_EXCEEDED)

    if unrun:
        group_outputs: list[str] = []
        failed_groups: list[GroupFailure] = [
            GroupFailure(g.name, unrun) for g in groups
        ]
    else:
        # The claim spans the whole phase, retries included: _run_group_phase
        # re-runs failed groups after its pool exits, and those agents are the
        # same load as the first attempt.
        with hold_group_workers(group_count, max_parallel) as workers:
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
        core.log.info("Phase 4: Synthesis — the prior run's review stands, resuming at the gate")
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
        core.log.info(
            f"Reviewing local HEAD {git.client.abbrev(local.head_sha)} "
            f"(PR head is {git.client.abbrev(pr.head_sha)})"
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
        core.log.info("Gathering branch metadata...")
        return RunContext(fetch_branch_metadata(wt_path, base or None), PRContext(), None)
    core.log.info("Fetching PR data...")
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
