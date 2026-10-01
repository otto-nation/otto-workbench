"""AI-backed conflict resolution — prompts, parsing, dispatch.

Builds on the pure analysis in ``conflicts`` to drive agent-based file
resolution during a rebase.  Every function that calls the AI backend
accepts a ``trail`` parameter for audit logging.
"""
# doc-group: platform

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import agent.invoke
import agent.retry
import core.log
from core.phases import Phase
from core.trail import Trail, billed_to, terr, tfail, tinfo, tspan
import git.regenerate

from . import conflicts
from . import repo_regen
from . import survival
from . import types as rebase_types

ConflictBlock = rebase_types.ConflictBlock
ConflictPlan = rebase_types.ConflictPlan
ConflictStrategy = rebase_types.ConflictStrategy
GeneratedSignal = rebase_types.GeneratedSignal
ParseFailure = rebase_types.ParseFailure
Regenerator = git.regenerate.Regenerator
RegenQueue = git.regenerate.RegenQueue
Resolution = rebase_types.Resolution


# ── Prompt construction ──────────────────────────────────────────────────

# Bounds "keep both", which on its own is satisfied by emitting a declaration
# twice: two commits that each add the same field or kwarg at different offsets
# are non-overlapping additions by its letter. One rebase of this repo landed a
# dataclass field declared twice and a repeated keyword argument — a hard
# `SyntaxError` — both from resolutions that followed the instruction exactly.
# Kept beside the sentence it qualifies in both builders, since a model reads
# the caveat with the rule rather than a paragraph away.
_DUPLICATE_CAVEAT = (
    " But an addition is only non-overlapping if the result declares each thing"
    " once: when both sides add the same field, parameter, import, or key at"
    " different offsets, that is one declaration written twice, so keep a"
    " single copy rather than both."
)


def build_resolve_prompt(
    filepath: str, content: str, sha: str, subject: str,
    *, target_ref: str,
    ours_content: str | None = None, commit_diff: str | None = None,
) -> str:
    """Build a prompt to resolve a merge conflict."""
    parts = [
        f"You are resolving a merge conflict during a git rebase onto {target_ref}.\n",
        f"\nFile: {filepath}\n",
        f"Commit being applied: {sha} — {subject}\n",
        "\nIn a rebase, conflict markers mean:\n",
        f"- <<<<<<< HEAD = the base side ({target_ref} or previously rebased commits)\n",
        "- ======= separates the two sides\n",
        f"- >>>>>>> {sha} = the branch's changes being replayed\n",
    ]

    if ours_content is not None:
        normalized = ours_content if ours_content.endswith("\n") else ours_content + "\n"
        parts.append(
            "\n--- BASE VERSION (target side before this commit) ---\n"
            f"{normalized}"
            "--- END BASE VERSION ---\n"
        )

    if commit_diff is not None:
        parts.append(
            "\n--- COMMIT DIFF (what this commit intended to change) ---\n"
            f"{commit_diff}\n"
            "--- END COMMIT DIFF ---\n"
        )

    merge_instructions = (
        "\nMerge both sides, preserving the intent of the commit being applied "
        "onto the current base-side structure."
    )
    if ours_content is not None:
        merge_instructions += (
            " The base version above shows the current state of identifiers, "
            "types, and structure — if anything was renamed or restructured on "
            "the base side, use the base-side names."
        )
    merge_instructions += (
        f" If both sides add non-overlapping content, keep both.{_DUPLICATE_CAVEAT}"
        " If they modify the same lines, combine them sensibly.\n"
    )
    parts.append(merge_instructions)

    parts.append(
        f"\nOutput the resolved file between {conflicts.RESOLVE_BEGIN} and "
        f"{conflicts.RESOLVE_END} markers.\n"
        "No explanation, no markdown fencing, no commentary outside the markers.\n"
        f"\n{conflicts.RESOLVE_BEGIN}\n"
        "(your resolved content here)\n"
        f"{conflicts.RESOLVE_END}\n"
        "\n--- FILE CONTENT WITH CONFLICT MARKERS ---\n"
        f"{content}"
    )

    return "".join(parts)


def build_chunked_prompt(
    filepath: str,
    blocks: list[ConflictBlock],
    sha: str,
    subject: str,
    commit_diff: str | None = None,
    *,
    target_ref: str,
) -> str:
    """Build a prompt containing only conflict blocks with context."""
    parts = [
        f"You are resolving merge conflicts during a git rebase onto {target_ref}.\n",
        f"\nFile: {filepath}\n",
        f"Commit being applied: {sha} — {subject}\n",
        "\nIn a rebase, conflict markers mean:\n",
        f"- <<<<<<< HEAD = the base side ({target_ref} or previously rebased commits)\n",
        "- ======= separates the two sides\n",
        f"- >>>>>>> {sha} = the branch's changes being replayed\n",
    ]

    if commit_diff is not None:
        parts.append(
            "\n--- COMMIT DIFF (what this commit intended to change) ---\n"
            f"{commit_diff}\n"
            "--- END COMMIT DIFF ---\n"
        )

    parts.append(
        "\nMerge both sides, preserving the intent of the commit being applied "
        "onto the current base-side structure. "
        "The HEAD side of each conflict shows the current state of identifiers, "
        "types, and structure — if anything was renamed or restructured on the "
        "base side, use the base-side names. "
        f"If both sides add non-overlapping content, keep both.{_DUPLICATE_CAVEAT}"
        " If they modify the same lines, combine them sensibly.\n"
    )

    for block in blocks:
        n = block.index
        parts.append(f"\n--- CONFLICT {n} ---\n")
        if block.context_before:
            parts.append(block.context_before)
        parts.append(block.conflict)
        if block.context_after:
            parts.append(block.context_after)
        parts.append(f"--- END CONFLICT {n} ---\n")

    parts.append(
        "\nFor each conflict, output ONLY the resolved replacement for the "
        "conflict markers (everything from <<<<<<< through >>>>>>>). "
        "Do NOT include the surrounding context lines.\n"
        "No explanation, no markdown fencing, no commentary outside the markers.\n\n"
    )
    for block in blocks:
        n = block.index
        parts.append(
            f"{conflicts.RESOLVE_BEGIN}_{n}\n"
            f"(resolved content for conflict {n})\n"
            f"{conflicts.RESOLVE_END}_{n}\n"
        )

    return "".join(parts)


# ── Retry hints ──────────────────────────────────────────────────────────

# Which correction each parse failure earns. Keyed on the failure's own enum
# value, which the parsers put at the front of every reason string, so a new
# failure mode that is not listed falls through to the generic wording rather
# than silently inheriting another failure's correction.
_HINT_FOR_FAILURE = {
    ParseFailure.WHOLLY_ECHOED: agent.retry.ECHOED_CONTEXT_HINT,
    ParseFailure.SURVIVING_CONFLICT_MARKER: agent.retry.SURVIVING_MARKER_HINT,
}


def hint_for_reason(reason: str) -> str:
    """The retry correction that names *reason*, or the generic marker wording.

    A reason is the failure's enum value with the block index and the offending
    text appended, so the match is on the prefix — anchored at the start and at
    a separator, not a bare substring, since ``echoed_context`` and
    ``wholly_echoed_context`` would otherwise match each other.
    """
    head = reason.split(":", 1)[0].split("_in_block_", 1)[0]
    for failure, hint in _HINT_FOR_FAILURE.items():
        if head == failure.value or head.startswith(f"{failure.value}_"):
            return hint
    return agent.retry.BLANK_RESPONSE_HINT


# ── Survival of the clean changes ────────────────────────────────────────

@dataclass(frozen=True)
class AnswerVerdict:
    """What one whole-file answer amounts to: its content, or why there is none."""
    # The resolved file, or None when the answer does not parse.
    resolved: str | None
    # The parser's failure reason; empty when the answer parsed.
    reason: str
    # Changes git had merged cleanly that the answer discards. Empty for an
    # answer that does not parse: that is the parser's failure to report, and
    # its own hint is the better correction.
    losses: tuple[survival.Loss, ...]

    @property
    def usable(self) -> bool:
        return self.resolved is not None and not self.losses


def judge_answer(filepath: str, stages: conflicts.StageTexts, text: str) -> AnswerVerdict:
    """Parse a whole-file answer and audit it against the conflict's three stages."""
    resolved, reason = conflicts.parse_resolved_content(text)
    losses = () if resolved is None else survival.audit(
        filepath, base=stages.base, target=stages.target,
        replayed=stages.replayed, resolved=resolved,
    ).blocking
    return AnswerVerdict(resolved, reason, losses)


def dropped_change_hint(losses: tuple[survival.Loss, ...]) -> str:
    """The retry correction naming each change the previous answer threw away."""
    return agent.retry.DROPPED_CHANGE_HINT + "".join(
        f"- {loss.describe()}\n" for loss in losses
    ) + "\n"


# ── Resolution paths ─────────────────────────────────────────────────────

def resolve_full_file(
    filepath: str, full_path: Path, content: str,
    sha: str, subject: str, cwd: str, *, target_ref: str,
    trail: Trail | None = None,
) -> str | None:
    """Resolve via full-file prompt (small files or heavily conflicted).

    The whole-file answer is the one shape that can silently drop a change git
    had already merged: the model rewrites every line, and copying one side's
    file through parses perfectly. So an answer is usable only once it parses
    *and* keeps every clean change — the same judgement the commit hook makes,
    applied before the file is written rather than after it is staged. The
    chunked path needs no such check: it splices answers into the conflict
    blocks alone, and the merged lines around them are never in its hands.
    """
    stages = conflicts.stage_texts(filepath, cwd)

    # One answer is judged by `usable`, by `retry_hint`, and once more after
    # the prompt returns; `survival.audit` is diff-based, so each distinct
    # answer is judged once and the verdict shared.
    verdicts: dict[str, AnswerVerdict] = {}

    def judge(text: str) -> AnswerVerdict:
        if text not in verdicts:
            verdicts[text] = judge_answer(filepath, stages, text)
        return verdicts[text]

    def retry_hint(text: str) -> str:
        verdict = judge(text)
        if verdict.losses:
            return dropped_change_hint(verdict.losses)
        return hint_for_reason(verdict.reason)

    ours_content = stages.target
    commit_diff = conflicts.get_commit_diff(filepath, cwd)
    prompt = build_resolve_prompt(
        filepath, content, sha, subject, target_ref=target_ref,
        ours_content=ours_content, commit_diff=commit_diff,
    )
    answer = agent.invoke.run_prompt(
        Phase.REBASE, prompt, cwd=cwd,
        label=f"conflict resolution for {filepath}",
        usable=lambda text: judge(text).usable, task="conflict-resolve",
        # The retry is told what this answer got wrong rather than the generic
        # marker wording: a resolution that copied the conflict markers through
        # needs to be told to merge them, not to emit markers it already did,
        # and one that dropped a merged change needs to be told which.
        retry_hint=retry_hint,
        **billed_to(trail),
    )
    if answer.exit_code != 0:
        terr(trail, "resolve_conflicts", f"AI prompt failed for {filepath}",
              data={"filepath": filepath, "exit_code": answer.exit_code})
        core.log.error(f"ai prompt failed for {filepath} (exit {answer.exit_code})")
        return None

    stdout = answer.text
    verdict = judge(stdout)
    resolved_content, failure_reason, losses = verdict.resolved, verdict.reason, verdict.losses
    if resolved_content is None:
        tfail(
            trail, "resolve_conflicts",
            f"failed to parse resolution for {filepath}",
            output=stdout,
            data={"filepath": filepath, "reason": failure_reason},
        )
        core.log.error(f"Failed to parse resolution for {filepath} ({failure_reason})")
        return None

    if losses:
        tfail(
            trail, "resolve_conflicts",
            f"resolution for {filepath} discards merged changes",
            output=stdout,
            data={"filepath": filepath,
                  "losses": [loss.describe() for loss in losses]},
        )
        core.log.error(f"Resolution for {filepath} throws away {len(losses)} change(s) "
                       "git had merged cleanly — not writing it")
        return None

    full_path.write_text(resolved_content)
    if not conflicts.git_add(filepath, cwd):
        return None
    core.log.ok(f"Resolved: {filepath}")
    return filepath


def resolve_chunked(
    filepath: str, full_path: Path, content: str,
    blocks: list[ConflictBlock], sha: str, subject: str, cwd: str,
    *, target_ref: str,
    trail: Trail | None = None,
) -> str | None:
    """Resolve via chunked prompt (large files with small conflicts).

    Writes nothing until every block has parsed, which is what lets
    ``resolve_single_file`` fall back to the whole-file prompt on a None:
    the file on disk is exactly as this found it.
    """
    commit_diff = conflicts.get_commit_diff(filepath, cwd)
    prompt = build_chunked_prompt(
        filepath, blocks, sha, subject, commit_diff, target_ref=target_ref,
    )
    tinfo(trail, "chunked_resolve", f"using chunked resolution for {filepath}",
           data={"blocks": len(blocks), "total_lines": content.count("\n") + 1})
    answer = agent.invoke.run_prompt(
        Phase.REBASE, prompt, cwd=cwd,
        label=f"chunked resolution for {filepath}",
        usable=lambda s: conflicts.parse_chunked_resolutions(s, blocks).ok,
        task="conflict-resolve-chunked",
        # The parser's own failure reason picks the correction. Discarding it
        # and taking the default is what sent a lecture about emitting markers
        # to an answer whose markers were perfect and whose mistake was
        # repeating the context back.
        retry_hint=lambda text: hint_for_reason(
            conflicts.parse_chunked_resolutions(text, blocks).reason,
        ),
        **billed_to(trail),
    )
    if answer.exit_code != 0:
        terr(trail, "resolve_conflicts", f"AI prompt failed for {filepath}",
              data={"filepath": filepath, "exit_code": answer.exit_code})
        core.log.error(f"ai prompt failed for {filepath} (exit {answer.exit_code})")
        return None

    parsed = conflicts.parse_chunked_resolutions(answer.text, blocks)
    if not parsed.ok:
        tfail(
            trail, "resolve_conflicts",
            f"failed to parse chunked resolution for {filepath}",
            output=answer.text,
            data={"filepath": filepath, "reason": parsed.reason},
        )
        core.log.error(f"Failed to parse chunked resolution for {filepath} ({parsed.reason})")
        return None

    if parsed.repaired:
        # Recorded rather than passed over in silence: the trim is exact, but
        # it is still this process editing the model's answer, and a run whose
        # resolutions were mostly repaired is a prompt that needs looking at.
        tinfo(
            trail, "chunked_resolve",
            f"trimmed echoed context from {parsed.repaired} of "
            f"{len(blocks)} block(s) in {filepath}",
            data={"filepath": filepath, "repaired": parsed.repaired,
                  "blocks": len(blocks)},
        )
        core.log.dim(f"Trimmed echoed context from {parsed.repaired} block(s) "
                f"in {filepath}")

    resolved_content = conflicts.splice_resolutions(
        content, blocks, parsed.resolutions,
    )
    full_path.write_text(resolved_content)
    if not conflicts.git_add(filepath, cwd):
        return None
    core.log.ok(f"Resolved: {filepath} ({len(blocks)} conflict(s), chunked)")
    return filepath


def resolve_single_file(
    filepath: str, full_path: Path, sha: str, subject: str, cwd: str,
    *, target_ref: str,
    trail: Trail | None = None,
) -> str | None:
    """Resolve a single conflicted text file via AI. Returns filepath or None."""
    try:
        content = full_path.read_text()
    except OSError as e:
        core.log.error(f"Cannot read {filepath}: {e}")
        return None

    blocks = conflicts.extract_conflict_blocks(content)
    if blocks and conflicts.should_chunk(content, blocks):
        resolved = resolve_chunked(
            filepath, full_path, content, blocks, sha, subject, cwd,
            target_ref=target_ref, trail=trail,
        )
        if resolved is not None:
            return resolved
        # The chunked path writes nothing until it has parsed every block, so
        # a failure leaves the file exactly as it was and the whole-file prompt
        # is a live second option rather than a repeat. It is also the stronger
        # one: the failures that get here are about the chunked format itself —
        # a block's markers missing, or context echoed around the answer — and
        # neither exists in a prompt that asks for the file entire.
        tinfo(
            trail, "chunked_resolve",
            f"falling back to whole-file resolution for {filepath}",
            data={"filepath": filepath, "blocks": len(blocks)},
        )
        core.log.warn(f"Chunked resolution failed for {filepath} — "
                 "retrying as a whole file.")
    return resolve_full_file(
        filepath, full_path, content, sha, subject, cwd,
        target_ref=target_ref, trail=trail,
    )


# ── Dispatch ─────────────────────────────────────────────────────────────

def dispatch_regenerate(
    filepath: str, full_path: Path, cwd: str,
    regenerator: Regenerator, queue: RegenQueue,
    *, trail: Trail | None = None,
) -> bool:
    """Handle the REGENERATE strategy. Returns False on failure."""
    if trail:
        trail.decision(
            "regenerate", f"accepting theirs for {filepath}",
            reason=f"lockfile with known regenerator: {' '.join(regenerator.cmd)}",
        )
    if not conflicts.accept_theirs_and_stage(filepath, cwd):
        return False
    queue.add(full_path.parent, filepath, regenerator.cmd, regenerator.stage_dir)
    return True


def dispatch_accept_theirs(
    filepath: str, full_path: Path, cwd: str,
    signal: GeneratedSignal, queue: RegenQueue,
    *, trail: Trail | None = None,
) -> bool:
    """Handle the ACCEPT_THEIRS strategy. Returns False on failure.

    Taking theirs is a placeholder, not the resolution: the incoming side was
    generated from the branch's sources before the base moved, so it is stale
    the moment it is staged. The queued regeneration is what actually resolves
    the file, and a repo that declares no way to rebuild leaves it stale — the
    honest report, and what the caller surfaces as ``files_stale``.
    """
    if trail:
        trail.decision(
            "generated_file", f"accepting theirs for {filepath}",
            reason=f"generated file detected via {signal}",
        )
    if not conflicts.accept_theirs_and_stage(filepath, cwd):
        return False
    if not repo_regen.queue_repo_regeneration(filepath, cwd, queue):
        queue.mark_unrebuildable(filepath)
        core.log.warn(f"No regeneration command for {filepath} — staged stale")
        tinfo(
            trail, "generated_file", f"no regeneration command for {filepath}",
            data={"filepath": filepath, "signal": str(signal)},
        )
    return True


def dispatch_conflict(
    filepath: str, full_path: Path, cwd: str,
    plan: ConflictPlan, sha: str, subject: str, queue: RegenQueue,
    *, target_ref: str,
    trail: Trail | None = None,
) -> bool:
    """Dispatch a single conflict by strategy. Returns False on fatal failure."""
    if plan.strategy is ConflictStrategy.REGENERATE:
        return dispatch_regenerate(
            filepath, full_path, cwd, plan.regenerator, queue, trail=trail,
        )
    if plan.strategy is ConflictStrategy.ACCEPT_THEIRS:
        return dispatch_accept_theirs(
            filepath, full_path, cwd, plan.signal, queue, trail=trail,
        )
    if plan.strategy is ConflictStrategy.DELETE:
        return conflicts.resolve_delete_conflict(
            filepath, sha, cwd, plan.delete_side, trail=trail,
        )
    if plan.strategy is ConflictStrategy.BINARY_ERROR:
        terr(trail, "resolve_conflicts", f"binary file: {filepath}",
              data={"filepath": filepath})
        core.log.error(f"Cannot resolve binary file: {filepath}")
        return False
    if plan.strategy is not ConflictStrategy.AI_MERGE:
        raise ValueError(f"unhandled conflict strategy: {plan.strategy}")
    resolved = resolve_single_file(
        filepath, full_path, sha, subject, cwd,
        target_ref=target_ref, trail=trail,
    )
    if resolved is None:
        return False
    tinfo(trail, "resolve_conflict", f"resolved {filepath}",
           data={"commit": sha, "method": "ai"})
    return True


def _run_deferred_regenerations(
    queue: RegenQueue, cwd: str, *, trail: Trail | None,
) -> list[str]:
    """Run deferred regeneration jobs and return files that failed."""
    failed: list[str] = []
    for job in queue:
        if not git.regenerate.run_regeneration(job, cwd=cwd, trail=trail):
            failed.extend(job.files)
    return failed


def resolve_file_conflicts(
    conflicts_list: list[str], cwd: str, sha: str, subject: str,
    *, target_ref: str, trail: Trail | None = None,
) -> Resolution:
    """Resolve conflicted files via classify → dispatch → deferred regen.

    Every file in the step is attempted, and one that cannot be resolved is
    named in ``Resolution.failed`` rather than ending the step. This used to
    return None at the first failure, which the caller turned into
    ``git rebase --abort`` — so one unparseable answer for one file destroyed
    every resolution the run had already made and every commit it had already
    replayed. Nine files and a completed commit went that way in a single run.

    Carrying on is not a lower standard; it is the same standard applied per
    file. The files that resolve are staged, the ones that do not are reported,
    and the caller stops the rebase *in place* so a human or a later
    ``pr rebase --fix`` picks up from there with the finished work intact.
    """
    resolved = []
    unresolved = []
    queue = RegenQueue()

    for filepath in conflicts_list:
        full_path = Path(cwd) / filepath
        # One timed pair of events per conflicted file, whatever strategy
        # resolves it. The gap between them is the only thing that answers
        # "is this progressing or wedged" while a run is still going, which
        # is what the skill's trail section tells an operator to read.
        with tspan(trail, f"resolve_file:{filepath}"):
            plan = conflicts.classify_conflict(filepath, full_path, cwd)
            ok = dispatch_conflict(
                filepath, full_path, cwd, plan, sha, subject, queue,
                target_ref=target_ref, trail=trail,
            )
        (resolved if ok else unresolved).append(filepath)

    failed = _run_deferred_regenerations(queue, cwd, trail=trail)

    if failed:
        terr(trail, "regenerate", "regeneration failed", data={"files": failed})
        core.log.warn(f"Regeneration failed for: {', '.join(failed)} — lockfiles may be stale")

    return Resolution(
        files=resolved, stale=queue.unrebuildable + failed, failed=unresolved,
    )
