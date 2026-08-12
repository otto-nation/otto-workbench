"""Fix pass for claude-review.

Runs after a review is written and `--fix` is set: hands the review document to
an agent that applies what it can, reconciles the checkboxes against the files
that actually changed, then commits and pushes the result.

It sits downstream of the pipeline rather than inside it — nothing here runs
during a review, and a fix pass needs only a finished review file to work from.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import log
from review_agent import diagnose_missing_output
from review_common import (
    Phase,
    TEMPLATE_FIX,
    has_uncommitted_changes,
    preserve_log, restore_preserved,
)
from review_findings import Finding, extract_skip_reasons, parse_findings
from review_phases import PHASES, PhaseRunner
from review_preflight import ReviewJob
from review_prompt import build_prompt
from review_retry import _FIX_RETRY_HINT, _has_output, _is_retryable

MAX_TURNS_FIX_CAP = 60
RETRY_MAX_TURNS_FIX = 40


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
    fix_log = runner.session_log
    log.info("Fix pass — applying review findings...")
    log.blank()
    runner.invoke(prompt, max_turns)
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
            runner.invoke(retry_prompt, retry_turns)
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

