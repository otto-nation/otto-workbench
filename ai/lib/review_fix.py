"""Fix pass for claude-review.

Runs after a review is written and `--fix` is set: hands the review document to
an agent that applies what it can, reconciles the checkboxes against the files
the agent changed, then commits and pushes the result.

What the agent changed is a snapshot difference: the worktree's dirty set is
recorded before the agent runs and again after, and only the paths that appear
in the second and not the first are attributed to it. Without that first
snapshot the pass cannot tell its own work from whatever was already sitting in
the worktree, and it both commits and takes credit for the difference.

It sits downstream of the pipeline rather than inside it — nothing here runs
during a review, and a fix pass needs only a finished review file to work from.
"""

# doc-group: findings

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import git_client
import log
import proc
import push
from push import PushStatus, Refusal
from review_agent import diagnose_missing_output
from review_common import (
    Phase,
    TEMPLATE_FIX,
    preserve_log, restore_preserved,
)
from review_findings import (
    Finding, extract_skip_reasons, match_skip, parse_findings,
)
from review_phases import PHASES, PhaseRunner
from review_preflight import ReviewJob
from review_prompt import build_prompt
from review_retry import _FIX_RETRY_HINT, _has_output, _is_retryable

MAX_TURNS_FIX_CAP = 60
RETRY_MAX_TURNS_FIX = 40


def _commit_fixes(
    job: ReviewJob, paths: set[str], fixed: int, skipped: int, summary: str = "",
):
    """Commit the source files the fix-pass agent changed.

    `paths` is the snapshot difference, so a file the agent created is in it and
    anything that was already dirty is not. Staging it by name rather than with
    `git add -A` is what keeps a build artifact or unrelated work in progress
    out of a commit the pass then pushes.
    """
    if not paths:
        return

    ordered = sorted(paths)
    # `:(literal)` because git reads these as pathspecs, not filenames. A file
    # actually named `report[1].md` would otherwise match as a character class
    # and stage whichever unrelated paths it happened to glob — the opposite of
    # what staging by name is here to guarantee.
    pathspecs = [f":(literal){path}" for path in ordered]
    staged = git_client.run("add", "--", *pathspecs, cwd=job.wt_path)
    if not staged.ok:
        raise RuntimeError(proc.failure_message("Failed to stage fixes", staged))

    msg = "fix: self-review findings"
    if fixed:
        msg += f"\n\n{fixed} fixed, {skipped} skipped"
    if summary:
        msg += f"\n\n{summary}"

    # Pathspec commit, so content the operator staged before the pass started
    # stays staged instead of riding along in the index.
    result = git_client.run("commit", "-m", msg, "--", *pathspecs, cwd=job.wt_path)
    if not result.ok:
        log.warn(f"Failed to commit fixes: {result.detail}")
        return

    log.info(f"Committed fixes ({fixed} fixed, {skipped} skipped)")
    _push_fixes(job)


def _push_fixes(job: ReviewJob):
    """Push committed fixes, and confirm they reached the remote.

    The owner classifies the failure and answers whether the commit landed; the
    advice for each kind of refusal stays here, because what to do about a
    diverged branch differs by the pass that hit it.
    """
    result = push.push(job.wt_path, gated=False)

    if result.status is not PushStatus.REFUSED:
        push.report(result, job.wt_path)
        return

    if result.refusal is Refusal.DIVERGED:
        log.error(
            f"push failed — branch diverged. Run:\n"
            f"  git -C '{job.wt_path}' push --force-with-lease\n"
            f"stderr: {result.output.strip()}"
        )
        return

    if result.refusal is Refusal.HOOK:
        log.error(
            f"fixes failed this repo's pre-push checks — commit "
            f"{result.sha[:7] or 'HEAD'} is local only and needs repair.\n\n"
            f"{push.output_tail(result.output, indent='  ')}\n\n"
            f"  Repair, then: git -C '{job.wt_path}' push"
        )
        return

    log.error(
        f"push failed — fixes are committed locally but not pushed:\n"
        f"stderr: {result.output.strip()}"
    )


def _changed_source_files(wt_path: str) -> set[str]:
    """Return set of changed files (staged, unstaged, and untracked).

    Called on both sides of the fix agent's run. `--exclude-standard` keeps
    gitignored paths out of either snapshot, so they cannot reach the
    difference and cannot be staged from it.
    """
    changed: set[str] = set()
    # Untracked files count: a fix that only adds a test file still fixed the
    # finding, and diff-only detection would report it as skipped.
    for args in (("diff", "HEAD", "--name-only"),
                 ("ls-files", "--others", "--exclude-standard")):
        changed.update(git_client.lines(*args, cwd=wt_path))
    return changed


@dataclass
class FixPassResult:
    fixed: list[Finding]
    skipped: list[Finding]
    unchanged: list[Finding]
    # Kept apart from `skipped`: a skip is work the pass could not do and the
    # next run should retry, a decline is work nobody is going to do.
    declined: list[Finding] = field(default_factory=list)

    @property
    def fixed_count(self) -> int:
        return len(self.fixed)

    @property
    def skipped_count(self) -> int:
        return len(self.skipped)


def _reconcile_checkboxes(review_file: str, changed: set[str]) -> None:
    """Auto-check findings whose files were modified but checkboxes weren't updated.

    The fix agent sometimes edits source files without updating the review
    markdown.  This reconciles by matching changed file paths to finding paths.

    `changed` is the agent's own delta, not the worktree's dirty set: a finding
    on a path that was already dirty when the pass started would otherwise be
    checked off as fixed by a pass that never touched it.
    """
    if not changed:
        return

    text = Path(review_file).read_text()
    findings = parse_findings(text)
    updated = False
    for f in findings:
        # A declined or skipped finding is never checked off: nobody claimed to
        # fix it, and an incidental edit to the same file is not a fix for it.
        # Attribution is by path, so one file's other findings would otherwise
        # check off every skip in it — and `_diff_findings` reads that as fixed,
        # putting work that never happened in the review file, the counts, and
        # the commit message, with the skip reason dropped on the way.
        if f.checked or f.declined or match_skip(f) or not f.path:
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
    declined: list[Finding] = []

    for fid, bf in before_by_id.items():
        af = after_by_id.get(fid)
        if af is None:
            unchanged.append(bf)
            continue
        if af.declined:
            declined.append(af)
        elif not bf.checked and af.checked:
            fixed.append(af)
        elif not bf.checked and not af.checked:
            skipped.append(af)
        else:
            unchanged.append(af)

    return FixPassResult(
        fixed=fixed, skipped=skipped, unchanged=unchanged, declined=declined,
    )


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
    if result.declined:
        lines.append("Declined:")
        for f in result.declined:
            reason = f.decline_reason if f.decline_reason else "adjudicated, not a defect"
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
    # A declined finding is not work: it was considered and rejected, so it is
    # out of the work set and out of the turn budget it would otherwise buy.
    before_unchecked = sum(
        1 for f in before_findings if not f.checked and not f.declined
    )

    if before_unchecked == 0:
        log.info("No findings left to fix — skipping fix pass")
        return

    max_turns = _fix_turn_budget(before_unchecked)

    prompt = build_prompt(
        TEMPLATE_FIX, job, max_turns=max_turns,
    )
    runner = PhaseRunner(job, Phase.FIX)
    fix_log = runner.session_log
    log.info("Fix pass — applying review findings...")
    log.blank()
    # ceiling: attribution is by path, so a file already dirty when the pass
    # starts is never credited to the agent — edits it makes to that file are
    # neither staged nor auto-checked. Upgrade to comparing each path's content
    # hash across the snapshot once fix passes routinely run against trees that
    # are dirty in the very files the review has findings on.
    before_changed = _changed_source_files(job.wt_path)
    runner.invoke(prompt, max_turns)
    log.blank()

    agent_changed = _changed_source_files(job.wt_path) - before_changed
    _reconcile_checkboxes(job.review_file, agent_changed)

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
            # Still measured against the pre-pass snapshot: the retry's work and
            # the first attempt's are both the agent's, and both must commit.
            agent_changed = _changed_source_files(job.wt_path) - before_changed
            _reconcile_checkboxes(job.review_file, agent_changed)
            after_text = Path(job.review_file).read_text()
            after_findings = parse_findings(after_text)
            extract_skip_reasons(after_findings)
            result = _diff_findings(before_findings, after_findings)

    summary = _format_fix_summary(result)
    if summary:
        log.info("Fix summary:")
        for line in summary.splitlines():
            print(f"  {line}", file=sys.stderr)

    _commit_fixes(job, agent_changed, fixed=result.fixed_count,
                  skipped=result.skipped_count, summary=summary)

