"""Pipeline orchestration for claude-review.

Drives the single-agent and multi-phase runs end to end: sequencing the phases
review_phases defines, deciding what a resumed run may skip, assembling the
review document (synthesis, mechanical fallback, meta header), consolidating
the session logs, and fetching the PR metadata a run starts from.
"""

from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

import log
from review_common import (
    count_severity,
    FILENAME_FIX_LOG,
    FILENAME_GROUP_LOG, FILENAME_HOLISTIC,
    FILENAME_HOLISTIC_LOG, FILENAME_META,
    FILENAME_PROMPT_STATS, FILENAME_SCOUT, FILENAME_SCOUT_LOG,
    FILENAME_SYNTHESIS_LOG,
    META_DATE, META_DELTA_FILES, META_GENERATOR, META_HEAD_SHA,
    META_PRIOR_DATE, META_PRIOR_SHA, META_REVIEW_TYPE, META_SKIPPED_GROUPS,
    META_STATUS,
    Diagnosis, DiagnosisKind, Effort, Mode, Phase,
    EFFORT_PRESETS,
    PRIOR_DATE_RE,
    TEMPLATE_FIX,
    TEMPLATE_SELF_REVIEW,
    TEMPLATE_SELF_SYNTHESIS, TEMPLATE_SINGLE, TEMPLATE_SYNTHESIS,
    _derive_path,
    has_uncommitted_changes,
    preserve_log, restore_preserved,
    read_pipeline_status,
)
from review_findings import (
    Finding,
    _MECHANICAL_NOTE,
    _has_findings,
    annotate_prior_with_stable_ids,
    build_mechanical_review,
    extract_skip_reasons,
    parse_findings, post_process_findings,
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
from review_scout import format_leads_block, parse_scout_output
from review_agent import (
    _parse_session_cost,
    diagnose_missing_output,
)
from review_phases import (
    PHASES, PhaseRunner,
    _omitted_turns, _phase_disprove, _phase_group_reviews, _phase_holistic,
    _phase_merge, _phase_scout, _should_disprove, _synthesis_max_turns, _touch,
)
from review_retry import (
    GroupFailure,
    _FIX_RETRY_HINT,
    _has_output, _is_retryable, _render_reason,
    _retry_missing_output,
)
from review_state import (
    _inject_failures_and_status, _pipeline_state_path, _read_pipeline_state,
    _resolve_recovery,
    _write_pipeline_state,
    build_failures_section,
)

DEFAULT_MAX_COST = 20.0

DISPROVE_MIN_FINDINGS = 3
MAX_TURNS_FIX_CAP = 60
RETRY_MAX_TURNS_FIX = 40


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


_TRANSPORT_MARKERS = (
    "could not read from remote repository",
    "could not resolve host",
    "connection refused",
    "connection timed out",
    "authentication failed",
    "permission denied",
    "repository not found",
)

_PUSH_REFUSED = "failed to push some refs"


def _is_local_hook_rejection(stderr: str) -> bool:
    """Whether the pre-push hook killed the push before git reached the remote.

    A failing hook aborts the push locally, so git prints the generic
    "failed to push some refs" with no per-ref "! [rejected]" line and none of
    the transport or auth diagnostics a real network failure carries.
    """
    lowered = stderr.lower()
    if _PUSH_REFUSED not in lowered or "! [rejected]" in lowered:
        return False
    return not any(marker in lowered for marker in _TRANSPORT_MARKERS)


_HOOK_OUTPUT_LINES = 20


def _hook_output(result: subprocess.CompletedProcess) -> str:
    """The tail of what the hook printed, indented under the error.

    The hook splits itself across both streams — check names on stdout, the
    failure summary on stderr — so reporting one alone loses which gate failed.
    """
    merged = f"{result.stdout or ''}\n{result.stderr or ''}"
    lines = [line.rstrip() for line in merged.splitlines() if line.strip()]
    return "\n".join(f"  {line}" for line in lines[-_HOOK_OUTPUT_LINES:])


def _head_sha(wt_path: str) -> str:
    """Short SHA of the commit left stranded locally, for the repair message."""
    result = subprocess.run(
        ["git", "-C", wt_path, "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else "HEAD"


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

    if _is_local_hook_rejection(stderr):
        log.error(
            f"fixes failed this repo's pre-push checks — commit "
            f"{_head_sha(job.wt_path)} is local only and needs repair.\n\n"
            f"{_hook_output(result)}\n\n"
            f"  Repair, then: git -C '{job.wt_path}' push"
        )
        return

    log.error(
        f"push failed — fixes are committed locally but not pushed:\n"
        f"stderr: {stderr}"
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
