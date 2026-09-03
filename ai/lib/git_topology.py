"""Which directory holds which branch, and creating one when there is none.

Worktree and bare-repo topology, split out of `pr_context` because the resolver
needs it rather than because it is part of resolving: nothing here reads a
`ResolvedContext`, and every read goes to git or to worktrunk. `pr_sync` is the
other half of that split and points the other way — it takes a resolved context
and acts on it.

The transport is plain `subprocess`: these are local reads with a `timeouts.LOCAL`
bound, and the one unbounded call is `wt switch`, which creates a checkout.
"""

# doc-group: platform

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import log
import timeouts
from proc import failure_message

# `git_remote` is a workbench-wide module rather than an `ai/lib` one, because
# the pre-push hooks and the surface gate resolve the same default branch. In a
# checkout that is one directory up; in the otto-ai-tools tarball, which
# flattens both into one `lib/`, it is already beside this file and the path
# below does not exist.
_WORKBENCH_LIB = Path(__file__).resolve().parent.parent.parent / "lib"
if _WORKBENCH_LIB.is_dir() and str(_WORKBENCH_LIB) not in sys.path:
    sys.path.insert(0, str(_WORKBENCH_LIB))
import git_remote  # noqa: E402

RESOLVE_BRANCH = Path(__file__).resolve().parent.parent.parent / "bin" / "resolve-branch"


@dataclass(frozen=True)
class WorktreeEntry:
    """One checkout git reported: where it is, and what is checked out in it.

    `branch` is None for a detached HEAD, which is a distinct answer from "no
    entry" — `find_worktree_for_branch` matches a directory name only against
    an entry that reported no branch, so a worktree named `main/` holding
    someone else's branch is never mistaken for the main worktree.
    """

    path: Path
    branch: str | None


def is_bare_repo(cwd: str | None = None) -> bool:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--is-bare-repository"],
            capture_output=True, text=True, cwd=cwd, timeout=timeouts.LOCAL,
        )
        return r.stdout.strip() == "true"
    except Exception:
        return False


def _parse_worktree_block(block: str) -> WorktreeEntry | None:
    """One ``--porcelain`` record as ``(path, branch)``, or None if not a checkout.

    Branch is None for a detached HEAD. The bare repo is not a checkout and is
    dropped — handing it back as one would point callers at the .git directory.
    """
    path: Path | None = None
    branch: str | None = None
    for line in block.splitlines():
        if line == "bare":
            return None
        if line.startswith("worktree "):
            path = Path(line.removeprefix("worktree "))
        elif line.startswith("branch refs/heads/"):
            branch = line.removeprefix("branch refs/heads/")
    return WorktreeEntry(path, branch) if path else None


def worktree_entries(cwd: str | None = None) -> list[WorktreeEntry]:
    """Every non-bare worktree as ``(path, branch)``; branch None when detached.

    Parses ``--porcelain`` rather than the human listing, which packs path,
    SHA and ``[branch]`` onto one whitespace-separated line: a path containing
    a space gets truncated by a naive split, and one containing a bracket reads
    as a branch tag. Porcelain gives the path verbatim on its own line.
    """
    try:
        r = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True, text=True, cwd=cwd, timeout=timeouts.LOCAL,
        )
    except Exception:
        return []
    parsed = map(_parse_worktree_block, r.stdout.split("\n\n"))
    return [entry for entry in parsed if entry]


def find_worktree_for_branch(
    branch: str, cwd: str | None = None,
) -> Path | None:
    """Find the worktree git reports as checked out on *branch*.

    Falls back to a worktree whose directory name matches the sanitized branch
    (slashes to dashes) so detached-HEAD worktrees are still found — but only
    when git reports no branch for it. A worktree named ``main/`` with someone
    else's branch checked out is not the main worktree, and callers that reset
    or check out what they get back would clobber that branch.

    Use find_worktree_dir_named when you only need a working directory.
    """
    entries = worktree_entries(cwd)
    for entry in entries:
        if entry.branch == branch:
            return entry.path
    sanitized = branch.replace("/", "-")
    for entry in entries:
        if entry.branch is None and entry.path.name == sanitized:
            return entry.path
    return None


def find_worktree_dir_named(
    branch: str, cwd: str | None = None,
) -> Path | None:
    """Any worktree whose directory is named after *branch*, whatever is in it.

    Answers "which directory is this repo's <branch> checkout" rather than
    "which worktree is on <branch>". Only for callers that need somewhere to
    run read-only commands — never for ones that reset or check out the result,
    which is how a feature branch parked in main/ got hard-reset away.
    """
    sanitized = branch.replace("/", "-")
    for entry in worktree_entries(cwd):
        if entry.path.name == sanitized:
            return entry.path
    return None


def create_worktree_for_branch(
    branch: str, cwd: str | None = None,
) -> Path | None:
    """Create a worktree for *branch*, or None if it can't be created.

    Delegates to ``wt switch`` so the worktree lands wherever worktrunk's
    path template puts it, keeping tooling-created worktrees in the same
    layout as hand-created ones.
    """
    path = wt_switch(branch, cwd)
    if not path:
        # wt_switch names the cause on every failure path it has; a second,
        # vaguer line here would only bury the one that says something.
        return None
    log.info(f"Created worktree for {branch} at {path}")
    return Path(path)


def wt_switch(ref: str, cwd: str | None = None) -> str | None:
    """Path of the worktree ``wt switch`` lands on for *ref*, or None.

    *ref* is anything worktrunk accepts — a branch name or a ``pr:<n>`` ref.
    Non-interactive and hook-free so it is safe to call from tooling.
    """
    try:
        r = subprocess.run(
            ["wt", "switch", ref, "--no-cd", "--no-hooks", "--format", "json", "-y"]
            + (["-C", cwd] if cwd else []),
            capture_output=True, text=True, timeout=timeouts.UNBOUNDED,
        )
    except FileNotFoundError:
        log.warn("worktrunk (wt) is not installed — cannot switch worktrees")
        return None
    except OSError as e:
        log.warn(f"Cannot run worktrunk (wt) — cannot switch worktrees: {e}")
        return None
    path = parse_wt_switch_path(r.stdout)
    if not path:
        log.warn(failure_message(f"wt switch {ref} reported no worktree path", r))
    return path


def parse_wt_switch_path(stdout: str) -> str | None:
    """Pull the ``path`` field out of ``wt switch --format json`` output."""
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            path = json.loads(line).get("path", "")
        except json.JSONDecodeError:
            continue
        if path:
            return path
    return None


def default_branch(cwd: str | Path | None = None) -> str:
    """The repo's default branch name, via lib/git_remote.py's shared ladder.

    Falls back to "main" whenever git cannot answer — an unfetched clone has no
    origin/HEAD, and every caller needs a base ref more than it needs an error.
    Before the shared ladder this stopped at the symref, so a repository whose
    trunk is `master` was told its default branch was one it does not have.

    Deliberately uncached: this is imported by every `pr` script, and a
    module-level cache here would outlive the tests that set up their own repos.
    Call sites that need it per-file wrap it in their own cache.
    """
    return git_remote.resolve_default_branch(str(cwd) if cwd is not None else None)


def find_bare_repo_worktree(
    cwd: str | None, branch: str | None,
) -> Path | None:
    """Worktree discovery for bare repos, creating nothing.

    Tries the requested branch (with fuzzy resolution). Only falls back to the
    default branch's worktree when no branch was requested at all.

    Never substitutes another branch's worktree for an explicitly requested
    branch: callers check the branch out, so handing back the default branch's
    worktree makes them displace it.
    """
    if branch:
        return find_worktree_by_branch(branch, cwd)

    # With no branch requested this is "give me a working directory for this
    # repo", not "is this worktree on <default>" — so the directory named after
    # the default branch will do even when something else is checked out there.
    # Returning None instead would strand ~10 callers that dereference
    # worktree_root without a guard.
    default = default_branch(cwd)
    return (
        find_worktree_for_branch(default, cwd)
        or find_worktree_dir_named(default, cwd)
    )


def resolve_bare_repo_worktree(
    cwd: str | None, branch: str | None,
) -> Path | None:
    """The same discovery, creating a worktree for *branch* when it is missing.

    Creating a checkout is a side effect a caller has to ask for, which is why
    it lives here rather than in find_bare_repo_worktree: a command that only
    reads state should not leave a worktree behind. See create_worktree_for_branch.
    """
    wt = find_bare_repo_worktree(cwd, branch)
    if wt or not branch:
        return wt
    return create_worktree_for_branch(branch, cwd)


def find_worktree_by_branch(
    branch_hint: str, cwd: str,
) -> Path | None:
    """Find a worktree for branch_hint, trying exact then fuzzy resolution."""
    wt = find_worktree_for_branch(branch_hint, cwd)
    if wt:
        return wt
    resolved = resolve_branch(branch_hint, cwd)
    if resolved != branch_hint:
        return find_worktree_for_branch(resolved, cwd)
    return None


def resolve_branch(hint: str, cwd: str | None = None) -> str:
    try:
        r = subprocess.run(
            [str(RESOLVE_BRANCH), hint],
            capture_output=True, text=True, cwd=cwd, timeout=timeouts.LOCAL,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
        # resolve-branch exited non-zero or returned nothing — use hint as-is
        # rather than silently discarding the user's explicit --branch value
        log.warn(failure_message(f"resolve-branch could not resolve {hint!r}", r))
        log.dim(f"using {hint!r} as-is")
        return hint
    # A resolver that hangs is a resolver that did not answer, which this
    # function already knows how to survive.
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return hint if hint else current_branch(cwd)


def current_branch(cwd: str | None = None) -> str:
    r = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, cwd=cwd, timeout=timeouts.LOCAL,
    )
    branch = r.stdout.strip()
    if r.returncode != 0 or not branch:
        log.error(failure_message("Cannot determine current branch", r))
        sys.exit(1)
    if branch == "HEAD":
        log.error("Cannot determine current branch — HEAD is detached")
        sys.exit(1)
    return branch


def current_branch_quiet(cwd: str | None = None) -> str | None:
    """Return current branch name, or None on failure (e.g. detached HEAD)."""
    r = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, cwd=cwd, timeout=timeouts.LOCAL,
    )
    if r.returncode != 0 or not r.stdout.strip() or r.stdout.strip() == "HEAD":
        return None
    return r.stdout.strip()
