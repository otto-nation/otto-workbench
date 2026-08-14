"""Where the workbench keeps things.

Three user-level roots — config, state, and cache — each resolving through the
same chain:

    WORKBENCH_<ROOT>_DIR  →  XDG_<ROOT>_HOME/workbench  →  built-in default

plus one that is scoped to a worktree rather than to the user:
``worktree_state_dir()``, under the worktree's own git dir.

This module is the Python owner of the three user-level roots. Two other
definitions express the same chain and must stay in step: ``lib/constants.sh``
for shell, and ``zsh/config.d/aliases/docker.zsh``, which cannot source
``constants.sh`` at shell startup. ``tests/workbench_roots.bats``
cross-validates all three.

Roots are resolved per call rather than frozen into module constants: the
environment is routinely set after import — by tests, and by callers that
re-point a root before invoking a subprocess — and an import-time constant
would capture whichever value happened to be live when the first importer
loaded this module.
"""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
from pathlib import Path

import log


def _root(env_var: str, xdg_var: str, fallback: str) -> Path:
    override = os.environ.get(env_var)
    if override:
        return Path(override)
    xdg_home = os.environ.get(xdg_var)
    if xdg_home:
        return Path(xdg_home) / "workbench"
    return Path(os.path.expanduser(fallback))


def _subdir(base: Path, name: str | None) -> Path:
    """One consumer's subtree of a root, or the root itself when unnamed.

    ``name`` is a bare directory name, not a path — an absolute value or one
    holding ``..`` would resolve outside the tree the root's owner globs over,
    so the data would simply never be found again.
    """
    if not name:
        return base
    # `Path("..").name` is ".." — a bare name by that test, but still an escape.
    if name == os.pardir or name != Path(name).name:
        raise ValueError(f"subdirectory must be a bare name, got {name!r}")
    return base / name


def config_dir() -> Path:
    """Hand-authored settings: overrides/, mcp-tools.json, review.yml, reuse-level."""
    return _root("WORKBENCH_CONFIG_DIR", "XDG_CONFIG_HOME", "~/.config/workbench")


def state_dir() -> Path:
    """Generated, machine-local data: reviews/, logs/, usage/, install.yml.

    The move off the old ``~/.config/workbench`` default is a hard cut: nothing
    falls back to the legacy path, and ``lib/migrations.sh`` carries the data
    across once.
    """
    return _root("WORKBENCH_STATE_DIR", "XDG_STATE_HOME", "~/.local/state/workbench")


def cache_dir(consumer: str | None = None) -> Path:
    """Recomputable data, safe to delete at any time: ``vertex-quota/``.

    ``consumer`` selects one consumer's subtree. Without it this is the root
    itself, which is what a wipe-the-cache operation wants.
    """
    root = _root("WORKBENCH_CACHE_DIR", "XDG_CACHE_HOME", "~/.cache/workbench")
    return _subdir(root, consumer)


def logs_dir(tool: str | None = None) -> Path:
    """Trail and log artifacts for a standalone tool run.

    Without ``tool`` this is the parent that ``otto-log`` globs over.
    """
    return _subdir(state_dir() / "logs", tool)


# ── Per-worktree state ──────────────────────────────────────────────────────

WORKTREE_STATE_DIRNAME = "workbench"

# Where this state lived before #624. Adopted on first resolve; see _adopt().
LEGACY_WORKTREE_STATE_DIRNAME = ".workbench"


class NotAWorktree(RuntimeError):
    """Raised when a path has no git dir to hang per-worktree state from."""


@functools.lru_cache(maxsize=None)
def _git_dir(worktree_root: Path) -> Path:
    """The worktree's own git dir, absolute.

    ``--absolute-git-dir`` reports ``<common>/worktrees/<name>`` for a linked
    worktree and ``<repo>/.git`` for the main one, which is what scopes the
    state to a single worktree rather than to the repository.

    Cached for the life of the process: a single ``pr`` run resolves the state
    dir once for the lock, once for the trail, and once per state read or
    write, and a worktree does not move out from under a run. Failures are not
    cached — ``lru_cache`` does not remember a raise — so a path that becomes a
    worktree later still resolves.

    The cache never expires for the life of the process, so it would go stale
    if a worktree were removed and a new, unrelated directory created at the
    same path within one process's lifetime. A non-issue for the short-lived
    CLI processes this targets; worth revisiting before reusing this in a
    long-running process.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(worktree_root), "rev-parse", "--absolute-git-dir"],
            capture_output=True, text=True,
        )
    except OSError as exc:
        # git missing, not executable, or the path itself unusable — all the
        # same answer here: there is no git dir to hang state from.
        raise NotAWorktree(f"no git available for {worktree_root}") from exc
    if result.returncode != 0:
        raise NotAWorktree(f"not inside a git worktree: {worktree_root}")
    git_dir = Path(result.stdout.strip())
    # The flag asks for an absolute path, so a relative answer is not git
    # answering. Taking it at its word would write the worktree's state into
    # whatever directory the process happens to be sitting in.
    if not git_dir.is_absolute():
        raise NotAWorktree(f"git reported a relative git dir for {worktree_root}")
    return git_dir


def _adopt(legacy: Path, target: Path) -> None:
    """Carry a pre-#624 ``.workbench/`` into the git dir, once.

    The directory holds ``state.json``, the run's ``trail.jsonl``, and the run
    lock, so it moves whole rather than file by file — anything left behind
    would be read by nothing afterwards.

    Every failure mode here is survivable: the state is rebuilt by whichever
    command next writes it, so a warning and an empty target beat aborting a
    run over a scoreboard. A concurrent run winning the race is not a failure
    at all — the data arrived either way.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(legacy, target)
        return
    except OSError:
        pass
    if target.exists():
        return
    try:
        # The git dir of a linked worktree can sit on another filesystem, which
        # os.rename refuses; shutil.move falls back to a copy.
        shutil.move(str(legacy), str(target))
    except (OSError, shutil.Error) as exc:
        # A concurrent adopt winning the race was already ruled out by the
        # target.exists() check above, so anything reaching here is a real
        # I/O failure (permissions, disk full) rather than the expected race.
        log.warn(f"could not move {legacy} to {target} ({exc}) — starting fresh")


def worktree_state_dir(worktree_root: Path | str) -> Path:
    """Live per-worktree state: the PR scoreboard, its trail, and the run lock.

    Kept in the worktree's own git dir rather than in the working tree, so
    ``wt remove`` deletes it along with the worktree it describes. Nothing here
    outlives its worktree — ``state.json`` is a scoreboard every command
    rebuilds — and nothing here is written where the consumer repo can see it,
    so there is no ``.gitignore`` entry to maintain.

    Raises ``NotAWorktree`` when the path is not inside a git worktree. Callers
    that treat missing state as "nothing yet" catch it; callers that were told
    to write should not swallow it.
    """
    root = Path(worktree_root)
    target = _git_dir(root) / WORKTREE_STATE_DIRNAME
    legacy = root / LEGACY_WORKTREE_STATE_DIRNAME
    if legacy.is_dir() and not target.exists():
        _adopt(legacy, target)
    return target


def trail_dir(worktree_root: Path | str, tool: str) -> Path:
    """Where a run's trail belongs: alongside the state it is a trail of.

    A directory git does not claim has no state dir to sit beside, and a run
    still has a trail to write, so it falls back to the tool's own logs — the
    other place ``otto-log`` looks. Callers with no worktree at all call
    ``logs_dir`` directly; ``validate-worktree-guards`` wants that branch
    written out at the call site rather than hidden behind an optional
    parameter here.
    """
    try:
        return worktree_state_dir(worktree_root)
    except NotAWorktree:
        return logs_dir(tool)
