"""Pipeline orchestration for claude-review.

Handles single-phase and multi-phase review pipelines, pipeline state
persistence for recovery, phase functions (holistic, group, synthesis),
and review post-processing.
"""

from __future__ import annotations

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import date
from pathlib import Path

from review_common import (
    FILE_STAT_FMT,
    FINDING_PREFIX_IDIOMS, FINDING_PREFIX_MUST,
    FINDING_PREFIX_NIT, FINDING_PREFIX_SHOULD,
    FILENAME_ANGLES, FILENAME_ANGLES_LOG,
    FILENAME_FIX_LOG,
    FILENAME_GROUP, FILENAME_GROUP_LOG, FILENAME_HOLISTIC,
    FILENAME_HOLISTIC_LOG, FILENAME_META, FILENAME_PIPELINE_STATE,
    FILENAME_SYNTHESIS_LOG,
    META_DATE, META_DELTA_FILES, META_GENERATOR, META_HEAD_SHA,
    META_PRIOR_DATE, META_PRIOR_SHA, META_REVIEW_TYPE, META_SKIPPED_GROUPS,
    MODE_SELF,
    PRIOR_DATE_RE,
    TEMPLATE_ANGLES, TEMPLATE_FIX,
    TEMPLATE_GROUP, TEMPLATE_HOLISTIC, TEMPLATE_SELF_REVIEW,
    TEMPLATE_SELF_SYNTHESIS, TEMPLATE_SINGLE, TEMPLATE_SYNTHESIS,
    _derive_path, _info, _warn,
)
from review_findings import (
    _check_orphaned_prior_ids, _count_findings, _has_findings,
    _validate_group_output,
    annotate_prior_with_stable_ids, merge_reviews,
    renumber_findings, strip_evidence_blocks, strip_stable_ids,
    verify_findings,
)
from review_preflight import (
    DEFAULT_MAX_GROUPS, DEFAULT_MAX_PARALLEL, FALLBACK_SUMMARY,
    HOLISTIC_MIN_GROUPS,
    Group, PRContext, PRMetadata, PipelineState, ReviewJob,
    _merge_smallest_groups,
    fetch_branch_metadata, fetch_pr_context, fetch_pr_metadata,
    group_files,
)
from review_prompt import (
    _is_incremental, _scope_prior_review,
    build_prompt,
)
from review_agent import (
    CONSECUTIVE_FAIL_THRESHOLD,
    DIAG_NO_RESULT_RECORD, DIAG_NO_SESSION_LOG,
    _diagnose_missing_output, _is_model_error, _parse_session_cost,
    _resolve_model, _try_recover_output, _try_recover_review,
    invoke_agent,
)

DEFAULT_MAX_COST = 20.0
DEFAULT_MAX_TURNS_GROUP = 15
DEFAULT_MAX_TURNS_HOLISTIC = 15
DEFAULT_MAX_TURNS_SYNTHESIS = 15
DEFAULT_MAX_TURNS_SINGLE = 15

RETRY_MAX_TURNS_GROUP = 30
_MAX_TURNS_REASON = "agent hit max turns"

DEFAULT_MODEL_GROUP = "sonnet"
DEFAULT_MODEL_HOLISTIC = "opus"
DEFAULT_MODEL_SYNTHESIS = "opus"
DEFAULT_MODEL_SINGLE = "opus"
DEFAULT_MODEL_ANGLES = "sonnet"
DEFAULT_MODEL_FIX = "sonnet"

DEFAULT_MAX_TURNS_ANGLES = 15
DEFAULT_MAX_TURNS_FIX = 20


def _synthesis_max_turns(merged_content: str) -> int:
    counts = _count_findings(merged_content)
    total = sum(counts.values())
    scaled = DEFAULT_MAX_TURNS_SYNTHESIS + max(0, total - 20) // 10
    return min(scaled, RETRY_MAX_TURNS_GROUP)


# ── Pipeline state (resume/retry) ────────────────────────────────────────────

_state_lock = threading.Lock()


def _pipeline_state_path(job: ReviewJob) -> str:
    return _derive_path(job.review_file, FILENAME_PIPELINE_STATE)


def _write_pipeline_state(job: ReviewJob, state: PipelineState):
    dest = _pipeline_state_path(job)
    tmp = dest + ".tmp"
    Path(tmp).write_text(json.dumps(asdict(state)))
    os.replace(tmp, dest)


def _read_pipeline_state(job: ReviewJob) -> "PipelineState | None":
    path = Path(_pipeline_state_path(job))
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        # JSON serializes dict keys as strings — convert back to int
        groups_failed_raw = data.get("groups_failed", {})
        groups_failed = {int(k): v for k, v in groups_failed_raw.items()}
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


def _update_group_failed(job: ReviewJob, group_idx: int, reason: str, state: PipelineState):
    with _state_lock:
        state.groups_failed[group_idx] = reason
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
    }
    if job.preflight and job.preflight.diff:
        meta["diff"] = job.preflight.diff

    incremental = _is_incremental(job)
    meta["review_type"] = "incremental" if incremental else "full"
    if incremental:
        pf = job.preflight
        meta["prior_sha"] = pf.prior_head_sha
        meta["delta_files"] = pf.delta_files
        meta["delta_file_count"] = len(pf.delta_files)

    Path(sidecar_path).write_text(json.dumps(meta))


def run_single_agent(job: ReviewJob):
    template = TEMPLATE_SELF_REVIEW if job.mode == MODE_SELF else TEMPLATE_SINGLE
    prompt = build_prompt(
        template, job, max_turns=DEFAULT_MAX_TURNS_SINGLE, branch_name=job.pr.head,
    )
    label = f"branch {job.pr.head}" if job.mode == MODE_SELF else f"PR #{job.pr_number} ({job.pr.title})"
    _info(f"Running review agent on {label}...")
    print()
    model = _resolve_model(job.model, "CLAUDE_REVIEW_SINGLE_MODEL", DEFAULT_MODEL_SINGLE)
    rc = invoke_agent(prompt, job.session_log, job.wt_path, job.reviews_dir, review_file=job.review_file, model=model, max_turns=DEFAULT_MAX_TURNS_SINGLE)
    print()

    reason = ""
    if not Path(job.review_file).exists():
        reason = _diagnose_missing_output(job.session_log)
        _try_recover_review(job)

    if not Path(job.review_file).exists():
        detail = f"exited with code {rc}" if rc != 0 else "completed"
        reason = reason or "unknown"
        print(f"error: review agent {detail} and produced no review file ({reason})", file=sys.stderr)
        print(f"  Session log: {job.session_log}", file=sys.stderr)
        sys.exit(1)

    _post_process_review(job)
    _write_review_sidecar(job)


def _review_group(
    i: int, grp: Group, job: ReviewJob,
    group_count: int, holistic_content: str,
    skip: bool = False,
    pipeline_state: "PipelineState | None" = None,
    max_turns: int = DEFAULT_MAX_TURNS_GROUP,
) -> tuple[int, str, "tuple[str, str] | None"]:
    group_output = _derive_path(job.review_file, FILENAME_GROUP.format(i))
    group_log = _derive_path(job.review_file, FILENAME_GROUP_LOG.format(i))

    if skip:
        if Path(group_output).exists():
            _info(f"Phase 2: Group {i}/{group_count} — {grp.name} skipped (exists)")
            return (i, group_output, None)
        _warn(f"Group {i} ({grp.name}) marked skip but output missing — reporting failure")
        return (i, group_output, (grp.name, "output missing"))

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
    model = _resolve_model(job.model, "CLAUDE_REVIEW_GROUP_MODEL", DEFAULT_MODEL_GROUP)
    _info(f"Phase 2: Group {i}/{group_count} — {grp.name} ({grp.lines} lines)...")
    invoke_agent(group_prompt, group_log, job.wt_path, job.reviews_dir, label=grp.name, model=model, max_turns=max_turns)

    failed = None
    if not Path(group_output).exists():
        _try_recover_output(group_log, group_output)
    if not Path(group_output).exists():
        reason = _diagnose_missing_output(group_log)
        _warn(f"Group {i} ({grp.name}) produced no output ({reason})")
        failed = (grp.name, reason)
        if pipeline_state is not None:
            _update_group_failed(job, i, reason, pipeline_state)
    else:
        _validate_group_output(group_output, grp.name)
        _info(f"Phase 2: Group {i}/{group_count} — {grp.name} done")
        if pipeline_state is not None:
            _update_group_done(job, i, pipeline_state)

    return (i, group_output, failed)


def _phase_holistic(job: ReviewJob, group_count: int) -> tuple[str, str, str]:
    holistic_output = _derive_path(job.review_file, FILENAME_HOLISTIC)
    holistic_log = _derive_path(job.review_file, FILENAME_HOLISTIC_LOG)

    prompt = build_prompt(
        TEMPLATE_HOLISTIC, job, max_turns=DEFAULT_MAX_TURNS_HOLISTIC, holistic_output=holistic_output,
    )
    model = _resolve_model(job.model, "CLAUDE_REVIEW_HOLISTIC_MODEL", DEFAULT_MODEL_HOLISTIC)
    _info(f"Phase 1/{group_count}: Holistic scan...")
    print()
    invoke_agent(prompt, holistic_log, job.wt_path, job.reviews_dir, model=model, max_turns=DEFAULT_MAX_TURNS_HOLISTIC)
    print()

    holistic_content = ""
    if Path(holistic_output).exists():
        holistic_content = Path(holistic_output).read_text()
    else:
        reason = _diagnose_missing_output(holistic_log)
        _warn(f"Holistic scan produced no output ({reason}) — continuing without it")

    return holistic_content, holistic_output, holistic_log


def _phase_angles(job: ReviewJob, holistic_content: str) -> tuple[str, str, str]:
    angles_output = _derive_path(job.review_file, FILENAME_ANGLES)
    angles_log = _derive_path(job.review_file, FILENAME_ANGLES_LOG)

    prompt = build_prompt(
        TEMPLATE_ANGLES, job, max_turns=DEFAULT_MAX_TURNS_ANGLES,
        angles_output=angles_output, holistic_content=holistic_content,
    )
    model = _resolve_model(job.model, "CLAUDE_REVIEW_ANGLES_MODEL", DEFAULT_MODEL_ANGLES)
    _info("Angles scan (7 review angles)...")
    invoke_agent(prompt, angles_log, job.wt_path, job.reviews_dir, model=model, max_turns=DEFAULT_MAX_TURNS_ANGLES)

    angles_content = ""
    if Path(angles_output).exists():
        angles_content = Path(angles_output).read_text()
    else:
        reason = _diagnose_missing_output(angles_log)
        _warn(f"Angles scan produced no output ({reason}) — continuing without it")

    return angles_content, angles_output, angles_log


def run_fix_pass(job: ReviewJob):
    if not Path(job.review_file).exists():
        _warn("No review file to fix — skipping fix pass")
        return
    fix_log = _derive_path(job.review_file, FILENAME_FIX_LOG)
    prompt = build_prompt(
        TEMPLATE_FIX, job, max_turns=DEFAULT_MAX_TURNS_FIX,
    )
    model = _resolve_model(job.model, "CLAUDE_REVIEW_FIX_MODEL", DEFAULT_MODEL_FIX)
    _info("Fix pass — applying review findings...")
    print()
    invoke_agent(prompt, fix_log, job.wt_path, job.reviews_dir, review_file=job.review_file, model=model, max_turns=DEFAULT_MAX_TURNS_FIX)
    print()


def _check_serial_abort(
    i: int, group_count: int, reason: str, log_path: str,
    consecutive: int, last_reason: str,
) -> tuple[str, int, str]:
    if _is_model_error(log_path):
        return f"Model not available — aborting remaining {group_count - i} groups", 0, ""
    consecutive = consecutive + 1 if reason == last_reason else 1
    if consecutive >= CONSECUTIVE_FAIL_THRESHOLD:
        return f"{CONSECUTIVE_FAIL_THRESHOLD} consecutive failures ({reason}) — aborting remaining {group_count - i} groups", 0, ""
    return "", consecutive, reason


def _run_serial_reviews(
    groups: list[Group], job: ReviewJob,
    group_count: int, holistic_content: str,
    skip_groups: "set[int] | None",
    pipeline_state: "PipelineState | None",
) -> "list[tuple[str, str]]":
    failed_groups: list[tuple[str, str]] = []
    consecutive_same_reason = 0
    last_reason = ""
    for i, grp in enumerate(groups, 1):
        skip = skip_groups is not None and i in skip_groups
        _, _, failed = _review_group(
            i, grp, job, group_count, holistic_content,
            skip=skip, pipeline_state=pipeline_state,
        )
        if not failed:
            consecutive_same_reason = 0
            last_reason = ""
            continue
        failed_groups.append(failed)
        _, reason = failed
        group_log = _derive_path(job.review_file, FILENAME_GROUP_LOG.format(i))
        abort_msg, consecutive_same_reason, last_reason = _check_serial_abort(
            i, group_count, reason, group_log, consecutive_same_reason, last_reason,
        )
        if not abort_msg:
            continue
        _warn(abort_msg)
        failed_groups.extend((remaining.name, f"skipped: {abort_msg}") for remaining in groups[i:])
        break
    return failed_groups


def _run_parallel_reviews(
    groups: list[Group], job: ReviewJob,
    group_count: int, holistic_content: str, workers: int,
    skip_groups: "set[int] | None",
    pipeline_state: "PipelineState | None",
) -> "list[tuple[str, str]]":
    _info(f"Phase 2: Reviewing {group_count} groups ({workers} parallel)...")
    print()
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
    print()
    return [failure for _, _, failure in results if failure]


def _is_retryable(reason: str) -> bool:
    if reason.startswith("skipped: "):
        return False
    if _MAX_TURNS_REASON in reason:
        return True
    if reason in (DIAG_NO_RESULT_RECORD, DIAG_NO_SESSION_LOG):
        return True
    return False


def _retry_turns(reason: str) -> int:
    if _MAX_TURNS_REASON in reason:
        return RETRY_MAX_TURNS_GROUP
    return DEFAULT_MAX_TURNS_GROUP


def _retry_failed_groups(
    failed_groups: "list[tuple[str, str]]",
    groups: list[Group], job: ReviewJob,
    group_count: int, holistic_content: str,
    pipeline_state: "PipelineState | None",
) -> "list[tuple[str, str]]":
    retryable = [(name, reason) for name, reason in failed_groups if _is_retryable(reason)]
    skipped = [(n, r) for n, r in failed_groups if r.startswith("skipped: ")]
    non_retryable = [
        (n, r) for n, r in failed_groups
        if not _is_retryable(r) and not r.startswith("skipped: ")
    ]
    if not retryable:
        return failed_groups

    group_by_name = {g.name: (idx, g) for idx, g in enumerate(groups, 1)}

    _info(f"Retrying {len(retryable)} failed groups...")
    still_failed: list[tuple[str, str]] = []
    for name, reason in retryable:
        if name not in group_by_name:
            still_failed.append((name, reason))
            continue
        idx, grp = group_by_name[name]
        turns = _retry_turns(reason)
        _info(f"  Retry: {name} (max_turns={turns})")
        _, _, failure = _review_group(
            idx, grp, job, group_count, holistic_content,
            pipeline_state=pipeline_state,
            max_turns=turns,
        )
        if failure:
            still_failed.append(failure)

    # If all retries succeeded, run previously-skipped groups too
    if not still_failed and skipped:
        _info(f"All retries succeeded — running {len(skipped)} previously-skipped groups...")
        for name, reason in skipped:
            if name not in group_by_name:
                still_failed.append((name, reason))
                continue
            idx, grp = group_by_name[name]
            _info(f"  Running skipped group: {name}")
            _, _, failure = _review_group(
                idx, grp, job, group_count, holistic_content,
                pipeline_state=pipeline_state,
            )
            if failure:
                still_failed.append(failure)
    elif skipped:
        still_failed.extend(skipped)

    return non_retryable + still_failed


def _phase_group_reviews(
    groups: list[Group], job: ReviewJob,
    group_count: int, holistic_content: str, max_parallel: int,
    skip_groups: "set[int] | None" = None,
    pipeline_state: "PipelineState | None" = None,
) -> "tuple[list[str], list[tuple[str, str]]]":
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


def _phase_merge(group_outputs: list[str], failed_groups: "list[tuple[str, str]]") -> str:
    _info("Phase 3: Merging findings...")
    merged_content = merge_reviews(group_outputs)

    if failed_groups:
        merged_content += "\n## Review gaps\n"
        merged_content += "The following file groups were not reviewed due to agent failure:\n"
        for name, reason in failed_groups:
            merged_content += f"- {name}: {reason}\n"

    return merged_content


def _build_meta_header(
    job: ReviewJob,
    skipped_groups: int = 0, total_groups: int = 0,
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

    if job.generator_version:
        lines.append(META_GENERATOR.format(generator_version=job.generator_version))

    return "\n".join(lines) + "\n"


def _is_complete_review(review_file: str) -> bool:
    if not Path(review_file).exists():
        return False
    content = Path(review_file).read_text()
    return "## Summary" in content or "## Verdict" in content


_VERDICT_LABELS = [
    (FINDING_PREFIX_MUST, "must-fix"),
    (FINDING_PREFIX_SHOULD, "should-fix"),
    (FINDING_PREFIX_NIT, "nit"),
    (FINDING_PREFIX_IDIOMS, "idiom"),
]

_MECHANICAL_NOTE = "(mechanically merged, not synthesized)"


def _mechanical_verdict(counts: dict[str, int]) -> str:
    parts = [f"{counts[p]} {label}" for p, label in _VERDICT_LABELS if counts.get(p)]
    must = counts.get(FINDING_PREFIX_MUST, 0)
    should = counts.get(FINDING_PREFIX_SHOULD, 0)

    if must:
        action = "Request changes"
    elif should:
        action = "Needs discussion"
    elif parts:
        action = "Approve"
    else:
        return f"Approve — no findings {_MECHANICAL_NOTE}.\n"

    suffix = " only" if not must and not should else ""
    return f"{action} — {', '.join(parts)}{suffix} {_MECHANICAL_NOTE}.\n"


def _build_mechanical_fallback(
    job: ReviewJob, group_count: int, merged_content: str,
    skipped_groups: int = 0,
) -> str:
    meta = _build_meta_header(
        job, skipped_groups=skipped_groups, total_groups=group_count,
    )
    counts = _count_findings(merged_content)
    total = sum(counts.values())
    count_summary = f"{total} finding{'s' if total != 1 else ''}" if total else "No findings"

    if job.mode == MODE_SELF:
        title = f"# Self-Review: {job.repo} — {job.pr.head}"
    else:
        title = f"# Review: {job.repo}#{job.pr_number} — {job.pr.title}"

    summary = (
        f"{title}\n{meta}\n"
        f"## Summary\n"
        f"{count_summary} across {job.pr.changed_files} files in {group_count} groups. "
        f"{FALLBACK_SUMMARY}\n\n"
        f"{merged_content}\n"
    )
    if job.mode == MODE_SELF:
        return summary
    return f"{summary}\n## Verdict\n{_mechanical_verdict(counts)}"


def _post_process_review(job: ReviewJob) -> None:
    """Run verification and cleanup passes on the review file."""
    if job.prior_review:
        orphaned = _check_orphaned_prior_ids(
            annotate_prior_with_stable_ids(job.prior_review),
            Path(job.review_file).read_text(),
        )
        if orphaned:
            _warn(f"{len(orphaned)} prior findings neither carried forward nor resolved: {', '.join(orphaned)}")
    dropped = verify_findings(job.review_file, job.wt_path)
    if dropped:
        _info(f"Dropped {len(dropped)} unverified findings: {', '.join(dropped)}")
    strip_evidence_blocks(job.review_file)
    strip_stable_ids(job.review_file)
    renumber_findings(job.review_file)


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
    synthesis_template = TEMPLATE_SELF_SYNTHESIS if job.mode == MODE_SELF else TEMPLATE_SYNTHESIS

    max_turns = _synthesis_max_turns(merged_content)
    prompt = build_prompt(
        synthesis_template, job, max_turns=max_turns,
        holistic_content=holistic_content, group_count=group_count,
        merged_content=merged_content, branch_name=job.pr.head,
    )
    model = _resolve_model(job.model, "CLAUDE_REVIEW_SYNTHESIS_MODEL", DEFAULT_MODEL_SYNTHESIS)
    _info(f"Phase 4: Synthesis ({max_turns} turns)...")
    print()
    rc = invoke_agent(prompt, synthesis_log, job.wt_path, job.reviews_dir, review_file=job.review_file, model=model, max_turns=max_turns)
    print()

    if not Path(job.review_file).exists():
        _try_recover_output(synthesis_log, job.review_file)

    if _is_complete_review(job.review_file):
        _post_process_review(job)
    else:
        reason = "no output" if not Path(job.review_file).exists() else "incomplete output"
        detail = f"exited with code {rc} ({reason})" if rc != 0 else reason
        _warn(f"Synthesis agent {detail} — falling back to mechanical merge")
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
    angles_log: str = "",
):
    group_logs = _group_log_paths(job, group_count)
    all_logs = group_logs[:]
    if holistic_log:
        all_logs.insert(0, holistic_log)
    if angles_log:
        all_logs.append(angles_log)
    if synthesis_log:
        all_logs.append(synthesis_log)

    try:
        Path(job.session_log).write_text(_read_existing_logs(all_logs))
    except OSError:
        pass


def _cleanup_intermediates(
    job: ReviewJob,
    holistic_output: str, holistic_log: str,
    group_outputs: list[str], group_count: int, synthesis_log: str,
    angles_output: str = "", angles_log: str = "",
):
    group_logs = _group_log_paths(job, group_count)
    cleanup = group_outputs + group_logs
    if holistic_output:
        cleanup.append(holistic_output)
    if holistic_log:
        cleanup.append(holistic_log)
    if synthesis_log:
        cleanup.append(synthesis_log)
    if angles_output:
        cleanup.append(angles_output)
    if angles_log:
        cleanup.append(angles_log)
    cleanup.append(_pipeline_state_path(job))

    review_dir = str(Path(job.review_file).parent)
    for p in Path(review_dir).glob("prompt-*"):
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
    if job.mode == MODE_SELF:
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
        _warn("Pipeline state is stale (SHA or groups changed) — starting fresh")
        Path(_pipeline_state_path(job)).unlink(missing_ok=True)
        return 0.0, None, False, None

    has_failed_groups = bool(state.groups_failed)
    has_failed_synthesis = bool(state.synthesis_failed)
    is_complete = state.synthesis_done

    if is_complete and not has_failed_groups and not has_failed_synthesis:
        _info("Prior review completed successfully — nothing to recover")
        return 0.0, None, False, None

    cost_so_far = _sum_existing_costs(job, state)

    if is_complete and (has_failed_groups or has_failed_synthesis):
        _info("Prior review had failures — recovering")
        skip_groups = set(state.groups_done)
        if has_failed_groups:
            failed_count = len(state.groups_failed)
            state.groups_failed.clear()
            _info(f"  Re-running {failed_count} failed groups")
        if has_failed_synthesis:
            state.synthesis_done = False
            state.synthesis_failed = ""
            _info("  Re-running synthesis")
        return cost_so_far, skip_groups, state.holistic_done, state

    # Incomplete pipeline — resume from where it left off
    _info("Resuming incomplete pipeline")
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


def run_multi_phase(
    job: ReviewJob, max_parallel: int = DEFAULT_MAX_PARALLEL,
    skip_holistic: bool = False, max_cost: float = DEFAULT_MAX_COST,
):
    groups = group_files(job.pr)
    groups = _merge_smallest_groups(groups, DEFAULT_MAX_GROUPS)
    group_count = len(groups)

    _info(f"Large PR ({job.pr.total_lines} lines, {job.pr.changed_files} files) — {group_count} file groups")

    incremental = _is_incremental(job)
    incremental_skips: set[int] = set()
    carried_forward = ""

    if incremental:
        incremental_skips = _identify_incremental_skips(
            groups, job.preflight.delta_files,
        )
        if incremental_skips:
            affected = group_count - len(incremental_skips)
            _info(
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
        _info("Review already complete — use --force to re-run from scratch")
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

    skip_holistic_incremental = incremental and not skip_holistic
    if incremental:
        skip_holistic = True

    run_holistic = not skip_holistic and group_count >= HOLISTIC_MIN_GROUPS
    holistic_output = _derive_path(job.review_file, FILENAME_HOLISTIC)
    holistic_log = _derive_path(job.review_file, FILENAME_HOLISTIC_LOG)

    if run_holistic and skip_holistic_phase and Path(holistic_output).exists():
        _info("Phase 1: Holistic scan skipped (exists)")
        holistic_content = Path(holistic_output).read_text()
    elif run_holistic:
        holistic_content, holistic_output, holistic_log = _phase_holistic(job, group_count)
        if holistic_log:
            cost_so_far += _parse_session_cost(holistic_log)
        state.holistic_done = True
        _write_pipeline_state(job, state)
    else:
        if skip_holistic_incremental:
            reason = "incremental review"
        elif skip_holistic:
            reason = "--no-holistic"
        else:
            reason = f"{group_count} groups < {HOLISTIC_MIN_GROUPS} threshold"
        _info(f"Holistic phase skipped ({reason})")
        holistic_content, holistic_output, holistic_log = "", "", ""

    run_angles = job.mode == MODE_SELF and not getattr(state, "angles_done", False)
    angles_content, angles_output, angles_log = "", "", ""

    if cost_so_far > max_cost:
        _warn(f"Budget exceeded after holistic phase (${cost_so_far:.2f}/${max_cost:.2f}) — skipping groups")
        group_outputs, failed_groups = [], [(g.name, "budget exceeded") for g in groups]
    else:
        if run_angles:
            with ThreadPoolExecutor(max_workers=2) as pool:
                angles_future = pool.submit(_phase_angles, job, holistic_content)
                groups_future = pool.submit(
                    _phase_group_reviews,
                    groups, job, group_count, holistic_content, max_parallel,
                    skip_groups, state,
                )
                angles_content, angles_output, angles_log = angles_future.result()
                group_outputs, failed_groups = groups_future.result()
            state.angles_done = True
            _write_pipeline_state(job, state)
            if angles_log:
                cost_so_far += _parse_session_cost(angles_log)
        else:
            group_outputs, failed_groups = _phase_group_reviews(
                groups, job, group_count, holistic_content, max_parallel,
                skip_groups=skip_groups, pipeline_state=state,
            )
        new_group_indices = [
            i for i in range(1, len(group_outputs) + 1)
            if skip_groups is None or i not in skip_groups
        ]
        for i in new_group_indices:
            gl = _derive_path(job.review_file, FILENAME_GROUP_LOG.format(i))
            cost_so_far += _parse_session_cost(gl)

    all_merge_inputs = group_outputs[:]
    if angles_output and Path(angles_output).exists():
        all_merge_inputs.append(angles_output)
    merged_content = _phase_merge(all_merge_inputs, failed_groups)

    if carried_forward:
        merged_content += "\n" + carried_forward

    n_skipped = len(incremental_skips)
    all_groups_failed = len(failed_groups) == group_count

    if not _has_findings(merged_content) and not failed_groups:
        _info("No findings from any group — writing clean review")
        _write_clean_review(job, group_count, skipped_groups=n_skipped)
        synthesis_log = ""
        state.synthesis_done = True
        _write_pipeline_state(job, state)
    elif all_groups_failed:
        _warn("All group agents failed — skipping synthesis")
        fallback = _build_mechanical_fallback(
            job, group_count, merged_content, skipped_groups=n_skipped,
        )
        Path(job.review_file).write_text(fallback)
        _write_review_sidecar(job)
        synthesis_log = ""
        state.synthesis_done = True
        state.synthesis_failed = "all groups failed"
        _write_pipeline_state(job, state)
    elif cost_so_far > max_cost:
        _warn("Using merged group output as final review (synthesis skipped due to budget)")
        Path(job.review_file).write_text(merged_content)
        _write_review_sidecar(job)
        synthesis_log = ""
        state.synthesis_done = True
        state.synthesis_failed = "budget exceeded"
        _write_pipeline_state(job, state)
    else:
        synthesis_log = _phase_synthesis(
            job, holistic_content, group_count, merged_content,
            skipped_groups=n_skipped,
        )
        state.synthesis_done = True
        # Detect mechanical fallback vs successful synthesis
        review_content = Path(job.review_file).read_text() if Path(job.review_file).exists() else ""
        if _MECHANICAL_NOTE in review_content:
            state.synthesis_failed = "mechanical fallback"
        _write_pipeline_state(job, state)

    _consolidate_logs(job, holistic_log, group_count, synthesis_log, angles_log=angles_log)

    if not failed_groups:
        _cleanup_intermediates(
            job, holistic_output, holistic_log, group_outputs, group_count, synthesis_log,
            angles_output=angles_output, angles_log=angles_log,
        )


def _fetch_metadata(
    repo: str, pr_number: str, mode: str, wt_path: str,
) -> tuple[PRMetadata, PRContext]:
    if mode == MODE_SELF and not pr_number:
        _info("Gathering branch metadata...")
        return fetch_branch_metadata(wt_path), PRContext()
    _info("Fetching PR data...")
    with ThreadPoolExecutor(max_workers=2) as pool:
        pr_future = pool.submit(fetch_pr_metadata, repo, pr_number)
        if mode == MODE_SELF:
            return pr_future.result(), PRContext()
        ctx_future = pool.submit(fetch_pr_context, repo, pr_number)
        return pr_future.result(), ctx_future.result()


