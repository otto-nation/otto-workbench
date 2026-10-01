"""Auto-stash and auto-unstash around a rebase.

A rebase tolerates uncommitted changes; the pre-push hooks that run after it do
not, so anything left in the worktree is stashed for the duration and restored
after. Restoring can conflict, which is why the AI resolution path is here too.
"""

# doc-group: platform

from __future__ import annotations

from pathlib import Path

import agent.backend
import agent.invoke
import core.log
from core.phases import Phase
from core.trail import Trail, billed_to, tfail, tinfo
import git.client

from . import conflicts as rebase_conflicts
from . import inspect as rebase_inspect
from . import types as rebase_types

RunMode = rebase_types.RunMode

STASH_MSG = "pr-rebase: auto-stash"

# The same hold-off the rebase steps run under, for the same reason. A pop is a
# merge of the stashed worktree onto the rewritten branch, so rerere replays
# into it exactly as it would into a rebase step — which means a cached
# resolution could be applied to the user's uncommitted work, and this run's
# unreviewed AI resolutions could be recorded into the cache every later plain
# `git rebase` reads. `lifecycle` passes this to all three of its `git rebase`
# calls and this call was the gap: one doorway held shut and a second left
# open is the same regression, not a smaller one.
RERERE_CONFIG = rebase_types.RERERE_CONFIG


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
        core.log.error("Cannot tell whether the worktree is dirty — not rebasing.")
        return None
    if not dirty:
        return False

    core.log.info("Stashing uncommitted changes...")
    r = git.client.run("stash", "push", "-u", "-m", STASH_MSG, cwd=cwd)
    if not r.ok:
        core.log.error(f"git stash failed: {r.stderr.strip()}")
        return None
    tinfo(trail, "stash", "auto-stashed uncommitted changes")
    return True


def auto_stash_ref(cwd: str) -> str:
    """The stash entry this tool pushed, as a ref, or "" if there is none.

    Resolved by message rather than assumed to be ``stash@{0}``. The fresh path
    pops moments after pushing and would be right either way, but a run that
    held its stash across a paused rebase pops it in a *later* process, by
    which time the operator may well have stashed something of their own on
    top — and popping the top entry then restores the wrong work into a
    rebased tree.

    Matched on the whole message after git's ``On <branch>: `` prefix, not on a
    substring of the line: an operator's own stash named "before pr-rebase:
    auto-stash experiment" is not this one.
    """
    for line in git.client.lines(
        "stash", "list", "--format=%gd%x1f%gs", cwd=cwd,
    ):
        ref, _, subject = line.partition("\x1f")
        _, _, message = subject.partition(": ")
        if ref and (message == STASH_MSG or subject == STASH_MSG):
            return ref
    return ""


def restore(cwd: str, mode: RunMode, *, trail: Trail | None = None) -> None:
    """Put the auto-stash back, unless a rebase is still holding the index.

    The guard is the whole point. A run that stops at exit 3 leaves the rebase
    in progress *by design*, and popping into that index cannot work: git
    refuses the pop outright, and the failure path then read the rebase's own
    unmerged files as stash conflicts and told the operator to resolve them and
    run ``git stash drop`` — dropping a stash that was never applied, which is
    the one instruction here that destroys work outright.

    Holding the stash costs nothing. The entry stays on the stack, named, and
    the run that finishes the rebase restores it.
    """
    stash_ref = auto_stash_ref(cwd)
    if not stash_ref:
        return
    if rebase_inspect.rebase_in_progress(cwd):
        tinfo(trail, "stash", "held the auto-stash — rebase still in progress")
        core.log.warn(f"Your uncommitted changes stay stashed as '{STASH_MSG}' — "
                 "the rebase is still in progress.")
        core.log.dim("They are restored by the run that finishes it: re-run "
                "`pr rebase --fix`, or `pr rebase --abort` then `git stash pop`.")
        return
    auto_unstash(cwd, mode, ref=stash_ref, trail=trail)


def auto_unstash(
    cwd: str, mode: RunMode, *, ref: str = "", trail: Trail | None = None,
) -> None:
    """Pop stashed changes, resolving conflicts if needed.

    A pop can fail without leaving conflict markers — most often because the
    stash holds an untracked file the rebased branch now tracks, which git
    refuses to overwrite. Nothing is lost either way: a failed pop keeps the
    stash entry, so the message names it rather than only echoing git.

    Callers want `restore`, which adds the in-progress-rebase guard. This is
    the unguarded half, kept separate so the guard has one owner rather than
    being repeated at each call site.

    *ref* is looked up fresh when not given — `restore` already has it from its
    own guard check and passes it through rather than asking `git stash list`
    the same question twice.
    """
    ref = ref or auto_stash_ref(cwd)
    if not ref:
        return
    r = git.client.run("stash", "pop", ref, cwd=cwd, config=RERERE_CONFIG)
    if r.ok:
        core.log.ok("Restored stashed changes.")
        return

    # Defense in depth behind `restore`'s guard: unmerged files during a rebase
    # belong to the rebase, not to a pop that git refused to even attempt, and
    # reading them as stash conflicts is what produced advice to drop a stash
    # that had never been applied.
    conflicts = (
        [] if rebase_inspect.rebase_in_progress(cwd)
        else rebase_inspect.detect_conflicts(cwd)
    )
    if not conflicts:
        tfail(trail, "unstash", "stash pop failed", output=r.combined_output)
        core.log.error(f"git stash pop failed: {r.stderr.strip()}")
        core.log.warn(f"Your changes are still stashed as '{STASH_MSG}' — "
                 "clear the blocking paths, then `git stash pop`.")
        return

    if not mode.resolves_conflicts or not agent.backend.is_available():
        core.log.warn(f"Stash pop has {len(conflicts)} conflict(s) — resolve manually, then `git stash drop`.")
        return

    core.log.info(f"Resolving {len(conflicts)} stash conflict(s)...")
    for filepath in conflicts:
        full_path = Path(cwd) / filepath
        if rebase_conflicts.is_binary(full_path):
            core.log.warn(f"Cannot resolve binary stash conflict: {filepath}")
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
        answer = agent.invoke.run_prompt(
            Phase.REBASE, prompt, cwd=cwd,
            label=f"stash conflict resolution for {filepath}",
            usable=rebase_conflicts.resolution_parses, task="stash-conflict-resolve",
            **billed_to(trail),
        )
        if answer.exit_code != 0:
            core.log.error(f"AI resolution failed for stash conflict: {filepath}")
            continue
        stdout = answer.text
        resolved_content, failure_reason = rebase_conflicts.parse_resolved_content(stdout)
        if resolved_content is None:
            tfail(
                trail, "resolve_stash_conflicts",
                f"failed to parse stash resolution for {filepath}",
                output=stdout,
                data={"filepath": filepath, "reason": failure_reason},
            )
            core.log.error(f"Failed to parse stash resolution for {filepath} ({failure_reason})")
            continue
        full_path.write_text(resolved_content)
        if not rebase_conflicts.git_add(filepath, cwd):
            core.log.error(f"Failed to stage resolved stash conflict: {filepath}")
            continue
        core.log.ok(f"Resolved stash conflict: {filepath}")

    remaining = rebase_inspect.detect_conflicts(cwd)
    if remaining:
        core.log.warn(f"{len(remaining)} stash conflict(s) remain — resolve manually, "
                 f"then `git stash drop {ref}`.")
        return
    r = git.client.run("stash", "drop", ref, cwd=cwd)
    if r.ok:
        core.log.ok("Restored stashed changes.")
    else:
        core.log.warn(f"git stash drop failed: {r.stderr.strip()}")
