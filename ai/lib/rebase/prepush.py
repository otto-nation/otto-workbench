"""Repairing the pre-push checks a rebased branch failed, then landing the fix.

The second rung of ``land``'s recovery ladder: the hook rewrote what it could,
pushed again, and still reported failures. What is left is a complaint an agent
can act on — except for generated files, which are rebuilt rather than edited,
because prompting one costs a whole call to produce what the generator emits in
milliseconds.

ceiling: a fourth hand-rolled fix loop that has not adopted ``fix.engine`` — it
has no outcome records, no retry on unparsed output, and no tracking artifact,
all of which the engine already owns. Rewrite this module as a
``PrePushFixAdapter`` when the engine grows a ``LandSpec.args`` to carry
``--force-with-lease`` and a ``PhaseShape.FIX`` phase for pre-push work; until
both exist the adoption cannot preserve the force-push this needs.
"""

# doc-group: platform

from __future__ import annotations

from pathlib import Path

from agent import backend as ai_backend
from agent import invoke as agent_invoke
from core import conventions
from core import log
from core.phases import Phase
from core.trail import Trail, billed_to, terr, tfail, tinfo
from git import client as git_client
from git import land
from git import regenerate as regen

from . import conflicts as rebase_conflicts
from . import repo_regen
from . import types as rebase_types

GeneratedFix = rebase_types.GeneratedFix
RegenQueue = regen.RegenQueue

FORCE_PUSH_ARGS = rebase_types.FORCE_PUSH_ARGS
REGEN_MESSAGE = rebase_types.REGEN_MESSAGE


# One budget for every chunk of context pasted into a fix prompt — check output
# or staged diff. Both are truncated on a plain slice, so a hunk can be cut
# mid-line; these prompts are best-effort and a partial tail still reads fine.
FIX_ERROR_MAX_CHARS = 4000
FALLBACK_FIX_SUBJECT = "fix: resolve pre-push check failures after rebase"


def _generated_subject(reply: str) -> str:
    """The subject line out of a generation, stripped of fencing and quoting."""
    lines = reply.strip().splitlines()
    return lines[0].strip().strip("`").strip() if lines else ""


def _fix_commit_message(
    cwd: str, fixed: list[str], *, trail: Trail | None = None,
) -> str:
    """Describe what the pre-push fix actually changed.

    The branch is squash-merged with COMMIT_MESSAGES, so this subject lands
    verbatim in the commit body on main, where a generic line says nothing.
    Any unusable generation falls back to that generic subject.

    A subject that breaks the repo's conventions is what the retry is for: the
    rules are in the prompt, so a second ask is the cheapest way to get one
    that follows them, and only then does the generic line stand.
    """
    diff = git_client.run("diff", "--cached", cwd=cwd).stdout[:FIX_ERROR_MAX_CHARS]
    if not diff.strip():
        return FALLBACK_FIX_SUBJECT

    answer = agent_invoke.run_prompt(
        Phase.REBASE,
        "Write one conventional-commit subject line for the diff below. It "
        "repairs a check failure left behind by an automated rebase conflict "
        "resolution.\n\n"
        f"Files: {', '.join(fixed)}\n"
        f"Format: type(scope): subject — at most {conventions.COMMIT_HEADER_MAX} "
        "characters, no trailing period. Say what changed in the code, not "
        "that a check failed. Output only the subject line.\n\n"
        f"--- DIFF ---\n{diff}",
        cwd=cwd,
        usable=lambda reply: conventions.valid_commit_header(_generated_subject(reply)),
        label="pre-push fix commit message",
        task="push-check-fix-message",
        **billed_to(trail),
    )
    return _generated_subject(answer.text) if answer.ok else FALLBACK_FIX_SUBJECT


def fix_one_file(
    filepath: str, cwd: str, check_output: str, *, trail: Trail | None = None,
) -> None:
    """Prompt the AI to fix a single file based on pre-push check errors.

    The fix is written in place and left unstaged — staging belongs to the
    caller, which sweeps the whole worktree so that edits the agent makes
    outside this marker protocol are committed too.
    """
    full_path = Path(cwd) / filepath
    if not full_path.exists():
        tinfo(
            trail, "fix_push_failures", f"skipped fix, file missing: {filepath}",
            data={"filepath": filepath},
        )
        return
    try:
        content = full_path.read_text()
    except OSError as exc:
        terr(
            trail, "fix_push_failures", f"skipped fix, file unreadable: {filepath}",
            data={"filepath": filepath, "error": str(exc)},
        )
        return

    prompt = (
        "A pre-push check failed after resolving merge conflicts during a "
        "git rebase. Fix any formatting, build, or lint issues in this file "
        "that are reported in the check output below.\n\n"
        f"File: {filepath}\n\n"
        f"--- CHECK OUTPUT ---\n{check_output}\n--- END CHECK OUTPUT ---\n\n"
        f"Output the fixed file between {rebase_conflicts.RESOLVE_BEGIN} and {rebase_conflicts.RESOLVE_END} "
        "markers. If the file has no issues, output it unchanged.\n\n"
        f"{rebase_conflicts.RESOLVE_BEGIN}\n(your fixed content here)\n{rebase_conflicts.RESOLVE_END}\n\n"
        f"--- FILE CONTENT ---\n{content}"
    )

    answer = agent_invoke.run_prompt(
        Phase.REBASE, prompt, cwd=cwd, label=f"pre-push fix for {filepath}",
        usable=rebase_conflicts.resolution_parses, task="push-check-fix", **billed_to(trail),
    )
    if answer.exit_code != 0:
        log.error(f"AI fix failed for {filepath} (exit {answer.exit_code})")
        terr(
            trail, "fix_push_failures", f"AI fix failed for {filepath}",
            data={"filepath": filepath, "exit_code": answer.exit_code},
        )
        return

    fixed_content, failure_reason = rebase_conflicts.parse_resolved_content(answer.text)
    if fixed_content is None:
        tfail(
            trail, "fix_push_failures", f"failed to parse fix for {filepath}",
            output=answer.text,
            data={"filepath": filepath, "reason": failure_reason},
        )
        return

    if fixed_content == content:
        return

    full_path.write_text(fixed_content)
    log.ok(f"Fixed: {filepath}")


def files_named_in(check_output: str, candidates: list[str]) -> list[str]:
    """Pick the candidates the failing check actually complained about.

    A long rebase resolves conflicts in dozens of files while the check that
    then fails names two of them. Prompting the AI for every resolved file
    spends a whole-file call per entry and hands an agent with edit access a
    file the check had no complaint about — which is how a file the branch
    never touched ends up rewritten.

    Matching on the bare name as well as the path catches checks that report a
    basename only. That can pull in a same-named file from another directory;
    one extra file is the acceptable side of this trade.
    """
    return [
        path for path in candidates
        if path in check_output or Path(path).name in check_output
    ]


def stage_worktree(cwd: str) -> list[str]:
    """Stage every uncommitted path and hand back what is now staged.

    ``-A`` rather than ``-u``: the pre-push hooks validate the whole worktree,
    untracked files included, so staging anything narrower commits something
    other than what the hooks passed.
    """
    if not git_client.ok("add", "-A", cwd=cwd):
        log.error("Failed to stage fixes.")
        return []
    return git_client.lines("diff", "--cached", "--name-only", cwd=cwd)


def _regenerate_generated(
    targets: list[str], cwd: str, *, trail: Trail | None = None,
) -> GeneratedFix:
    """Rebuild the generated files a failing check named, sparing them the AI.

    A generated file's content is a function of its sources, so a check failing
    on one means the sources moved and the artifact did not — a rebuild is the
    only correct repair. Hand-editing one is wrong even when the edit parses:
    the next regeneration overwrites it, and the drift check that rejected the
    push is comparing against generator output, not against plausibility.

    It is also ruinous to attempt. These files run to serialized descriptor
    blobs and hash manifests, and whole-file prompting one costs an entire
    call to produce something the generator emits in milliseconds.

    Every generated file is excluded whether or not a rebuild was available,
    because "we cannot rebuild it" is not a reason to edit it by hand.
    """
    queue = RegenQueue()
    excluded: list[str] = []
    stale: list[str] = []

    for filepath in targets:
        if rebase_conflicts.is_generated_file(filepath, Path(cwd) / filepath, cwd) is None:
            continue
        excluded.append(filepath)
        if not repo_regen.queue_repo_regeneration(filepath, cwd, queue):
            stale.append(filepath)

    if stale:
        log.warn(f"No regeneration command for {len(stale)} generated file(s)")
        tinfo(
            trail, "fix_push_failures", "generated files with no regeneration command",
            data={"files": stale},
        )

    rebuilt = False
    for job in queue:
        if regen.run_regeneration(job, cwd=cwd, trail=trail):
            rebuilt = True
            continue
        stale.extend(f for f in job.files if f not in stale)

    return GeneratedFix(excluded=excluded, rebuilt=rebuilt, stale=stale)


def fix_push_failures(
    cwd: str, error_output: str, resolved_files: list[str],
    *, trail: Trail | None = None,
) -> land.LandResult | None:
    """Fix pre-push check errors, then land the repair.

    Generated files are rebuilt from their sources; everything else goes to the
    AI, which interprets the check output and fixes formatting, build, or lint
    issues. Language-agnostic — the project's own checks serve as the oracle.

    The backend runs with ``acceptEdits`` and ``Bash(*)``, so a fix can land
    anywhere in the worktree, not only in the file whose content came back
    between the markers. Committing the whole tree rather than that narrower
    list is what keeps a direct edit from being validated by the retry's hooks
    and then left out of the pushed commit.

    None when no fix was attempted or nothing came of one — nothing to rebuild
    and no backend, or neither pass changed anything worth staging. The caller
    then reports the push that sent it here, which is still the honest answer
    about the branch.
    """
    truncated = error_output[:FIX_ERROR_MAX_CHARS]
    # A file that conflicted in several replayed commits is listed once per
    # conflict; fixing it once is enough.
    candidates = list(dict.fromkeys(resolved_files))
    # Matched against the truncated output on purpose: it is what the fix prompt
    # shows the AI, so a file named only past the cut has no visible complaint
    # to act on.
    targets = files_named_in(truncated, candidates)
    if not targets:
        # A check can fail without naming a path, and then every resolved file
        # is still a suspect. Recorded because it is the expensive path.
        targets = candidates
        tinfo(
            trail, "fix_push_failures", "check output named no resolved file",
            data={"files": candidates},
        )

    generated = _regenerate_generated(targets, cwd, trail=trail)
    editable = [f for f in targets if f not in generated.excluded]

    use_ai = bool(editable) and ai_backend.is_available()
    if not use_ai and not generated.rebuilt:
        return None

    if use_ai:
        for filepath in editable:
            fix_one_file(filepath, cwd, truncated, trail=trail)

    # Staged here rather than left to `land`, which would stage the same tree a
    # moment later: the message is generated from the staged diff, so the index
    # has to hold the fix before there is anything to describe.
    staged = stage_worktree(cwd)
    if not staged:
        return None

    landed = land.land(
        cwd, message=_fix_commit_message(cwd, staged, trail=trail), gated=True,
        args=FORCE_PUSH_ARGS, trail=trail,
    )
    if landed.sha:
        tinfo(
            trail, "fix_push_failures", "committed check-failure fixes",
            data={"files": staged, "sha": landed.sha},
        )
    return landed


