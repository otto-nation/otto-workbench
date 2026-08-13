"""Where a run's bookkeeping lives, keyed by what the run targets.

A run's target is ``(origin repo name, target branch)``. Both components are
readable from a checkout with no network call, which is what lets three
different readers agree on one directory: the ``pr`` CLI resolving a PR it is
about to review, ``workbench-statusline`` rendering a prompt, and ui-code's
server watching a repo it has never checked out.

There is deliberately no second key format. An alternate PR-number key would be
a second source of truth for one target, and a transient ``gh`` failure could
move a live target between the two mid-flight.

The layout is a published interface — ui-code reimplements it in TypeScript::

    <state_dir()>/pr/<repo-name>-<branch-slug>/
        state.json
        run.lock

``state_dir()`` rather than a literal path: #624 phase 4 moves that root to
``XDG_STATE_HOME`` alongside the migration that carries the data, and resolving
through the function is what makes ``pr/`` ride along instead of being stranded.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import workbench_paths

TARGETS_DIR = "pr"

# Runs of anything outside this set collapse to one dash. ui-code mirrors this
# exactly; tests/pr_target_test.py::SLUG_VECTORS is the shared fixture.
_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")

# Trailing ".git", and everything up to the last "/" or ":" — the latter is what
# separates host from path in scp-style remotes (git@github.com:owner/repo.git).
_REPO_TAIL_RE = re.compile(r"[/:]")


def slug(branch: str) -> str:
    """A branch name as a single path component.

    Deliberate ceiling: ``a/b`` and ``a-b`` collide, exactly as they already do
    for worktree directory names.
    """
    return _SLUG_RE.sub("-", branch).strip("-")


def _repo_name(url: str) -> str | None:
    tail = _REPO_TAIL_RE.split(url.rstrip("/"))[-1]
    if tail.endswith(".git"):
        tail = tail[: -len(".git")]
    return tail or None


def repo_name_from_origin(cwd: str | None = None) -> str | None:
    """The repo's short name per its ``origin`` remote, or None if it has none.

    Not ``gh repo view``: the key must be derivable without the network, and two
    sources for one component is how the two derivations below drift apart.
    """
    r = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        capture_output=True, text=True, cwd=cwd,
    )
    if r.returncode != 0 or not r.stdout.strip():
        return None
    return _repo_name(r.stdout.strip())


def target_dir(repo_name: str, branch: str) -> Path:
    """Where a run's state and lock live, keyed by what the run targets."""
    return workbench_paths.state_dir() / TARGETS_DIR / f"{repo_name}-{slug(branch)}"


def target_dir_for_checkout(path: Path) -> Path | None:
    """The same directory, read out of a checkout's git config.

    For readers holding a directory and nothing else. None on a detached HEAD or
    a repo without an ``origin`` — callers render that as "no state" rather than
    guessing a key.
    """
    repo_name = repo_name_from_origin(str(path))
    if not repo_name:
        return None
    # Not `rev-parse --abbrev-ref`: that needs HEAD to resolve to a commit, so
    # it fails on a freshly-init'd branch with nothing committed yet.
    # `symbolic-ref` reads the ref HEAD points at, unborn or not, and fails
    # cleanly on detached HEAD instead of resolving to the literal name "HEAD".
    r = subprocess.run(
        ["git", "symbolic-ref", "--short", "HEAD"],
        capture_output=True, text=True, cwd=str(path),
    )
    branch = r.stdout.strip()
    if r.returncode != 0 or not branch:
        return None
    return target_dir(repo_name, branch)
