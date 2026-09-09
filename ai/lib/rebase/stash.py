"""Auto-stash and auto-unstash around a rebase.

A rebase tolerates uncommitted changes; the pre-push hooks that run after it do
not, so anything left in the worktree is stashed for the duration and restored
after. Restoring can conflict, which is why the AI resolution path is here too.
"""

# doc-group: platform

from __future__ import annotations

from pathlib import Path

from agent import backend as ai_backend
from agent import invoke as agent_invoke
from core import log
from core.phases import Phase
from core.trail import Trail, billed_to, tfail, tinfo
from git import client as git_client

from . import conflicts as rebase_conflicts
from . import inspect as rebase_inspect
from . import types as rebase_types

RunMode = rebase_types.RunMode

STASH_MSG = "pr-rebase: auto-stash"


def auto_stash(cwd: str, *, trail: Trail | None = None) -> bool | None:
    """Stash uncommitted changes if the working tree is dirty.

    Untracked files are stashed too (``-u``). A rebase tolerates them, but the
    pre-push hooks run against the worktree, so anything the user left lying
    around silently joins what the hooks validate — and the push recovery's
    whole-tree stage would then commit it. Ignored files stay put; ``-u`` is
    not ``-a``.

    Returns True if stashed, False if the tree was clean, None if the tree
    could not be read or the stash failed.
    """
    dirty = rebase_inspect.status_lines(cwd)
    if dirty is None:
        log.error("Cannot tell whether the worktree is dirty — not rebasing.")
        return None
    if not dirty:
        return False

    log.info("Stashing uncommitted changes...")
    r = git_client.run("stash", "push", "-u", "-m", STASH_MSG, cwd=cwd)
    if not r.ok:
        log.error(f"git stash failed: {r.stderr.strip()}")
        return None
    tinfo(trail, "stash", "auto-stashed uncommitted changes")
    return True


def auto_unstash(
    cwd: str, mode: RunMode, *, trail: Trail | None = None,
) -> None:
    """Pop stashed changes, resolving conflicts if needed.

    A pop can fail without leaving conflict markers — most often because the
    stash holds an untracked file the rebased branch now tracks, which git
    refuses to overwrite. Nothing is lost either way: a failed pop keeps the
    stash entry, so the message names it rather than only echoing git.
    """
    r = git_client.run("stash", "pop", cwd=cwd)
    if r.ok:
        log.ok("Restored stashed changes.")
        return

    conflicts = rebase_inspect.detect_conflicts(cwd)
    if not conflicts:
        tfail(trail, "unstash", "stash pop failed", output=r.combined_output)
        log.error(f"git stash pop failed: {r.stderr.strip()}")
        log.warn(f"Your changes are still stashed as '{STASH_MSG}' — "
                 "clear the blocking paths, then `git stash pop`.")
        return

    if not mode.resolves_conflicts or not ai_backend.is_available():
        log.warn(f"Stash pop has {len(conflicts)} conflict(s) — resolve manually, then `git stash drop`.")
        return

    log.info(f"Resolving {len(conflicts)} stash conflict(s)...")
    for filepath in conflicts:
        full_path = Path(cwd) / filepath
        if rebase_conflicts.is_binary(full_path):
            log.warn(f"Cannot resolve binary stash conflict: {filepath}")
            continue
        try:
            content = full_path.read_text()
        except OSError:
            continue
        prompt = (
            "You are resolving a conflict after restoring uncommitted changes "
            "(git stash pop) onto a rebased branch.\n\n"
            f"File: {filepath}\n\n"
            "<<<<<<< Updated upstream = the rebased branch state\n"
            "======= separates the two sides\n"
            ">>>>>>> Stashed changes = the user's uncommitted work\n\n"
            "Preserve the user's uncommitted changes where possible, merged "
            "with any structural changes from the rebase.\n\n"
            f"Output the resolved file between {rebase_conflicts.RESOLVE_BEGIN} and {rebase_conflicts.RESOLVE_END} markers.\n"
            "No explanation, no markdown fencing.\n\n"
            f"{rebase_conflicts.RESOLVE_BEGIN}\n(your resolved content here)\n{rebase_conflicts.RESOLVE_END}\n\n"
            f"--- FILE CONTENT WITH CONFLICT MARKERS ---\n{content}"
        )
        answer = agent_invoke.run_prompt(
            Phase.REBASE, prompt, cwd=cwd,
            label=f"stash conflict resolution for {filepath}",
            usable=rebase_conflicts.resolution_parses, task="stash-conflict-resolve",
            **billed_to(trail),
        )
        if answer.exit_code != 0:
            log.error(f"AI resolution failed for stash conflict: {filepath}")
            continue
        stdout = answer.text
        resolved_content, failure_reason = rebase_conflicts.parse_resolved_content(stdout)
        if resolved_content is None:
            tfail(trail, 
                "resolve_stash_conflicts",
                f"failed to parse stash resolution for {filepath}",
                output=stdout,
                data={"filepath": filepath, "reason": failure_reason},
            )
            log.error(f"Failed to parse stash resolution for {filepath} ({failure_reason})")
            continue
        full_path.write_text(resolved_content)
        if not rebase_conflicts.git_add(filepath, cwd):
            log.error(f"Failed to stage resolved stash conflict: {filepath}")
            continue
        log.ok(f"Resolved stash conflict: {filepath}")

    remaining = rebase_inspect.detect_conflicts(cwd)
    if remaining:
        log.warn(f"{len(remaining)} stash conflict(s) remain — resolve manually, then `git stash drop`.")
    else:
        r = git_client.run("stash", "drop", cwd=cwd)
        if r.ok:
            log.ok("Restored stashed changes.")
        else:
            log.warn(f"git stash drop failed: {r.stderr.strip()}")
