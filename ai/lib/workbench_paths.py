"""Where the workbench keeps things.

Three user-level roots — config, state, and cache — each resolving through the
same chain:

    WORKBENCH_<ROOT>_DIR  →  XDG_<ROOT>_HOME/workbench  →  built-in default

This module is the Python owner of those roots. Two other definitions express
the same chain and must stay in step: ``lib/constants.sh`` for shell, and
``zsh/config.d/aliases/docker.zsh``, which cannot source ``constants.sh`` at
shell startup. ``tests/workbench_roots.bats`` cross-validates all three.

Roots are resolved per call rather than frozen into module constants: the
environment is routinely set after import — by tests, and by callers that
re-point a root before invoking a subprocess — and an import-time constant
would capture whichever value happened to be live when the first importer
loaded this module.
"""

# doc-group: platform

from __future__ import annotations

import os
from pathlib import Path

# The directory the workbench claims inside each XDG home, and the built-in
# default for each root when no XDG home is set. lib/constants.sh spells the
# same four; tests/workbench_roots.bats fails when a pair drifts.
WORKBENCH_DIRNAME = "workbench"
DEFAULT_CONFIG_DIR = "~/.config/workbench"
DEFAULT_STATE_DIR = "~/.local/state/workbench"
DEFAULT_CACHE_DIR = "~/.cache/workbench"

# Subtrees of the state root that more than one tool has to agree on.
TRAIL_DIRNAME = "trail"
REVIEWS_DIRNAME = "reviews"

# The registry of repos on this machine that use the workbench. ``lib/
# constants.sh`` spells the same filename as PROJECTS_REGISTRY_NAME;
# ``tests/workbench_roots.bats`` fails when the two drift.
PROJECTS_REGISTRY_NAME = "projects.registry"


def _root(env_var: str, xdg_var: str, fallback: str) -> Path:
    override = os.environ.get(env_var)
    if override:
        return Path(override)
    xdg_home = os.environ.get(xdg_var)
    if xdg_home:
        return Path(xdg_home) / WORKBENCH_DIRNAME
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
    """Hand-authored settings: config.yml, overrides/."""
    return _root("WORKBENCH_CONFIG_DIR", "XDG_CONFIG_HOME", DEFAULT_CONFIG_DIR)


def state_dir() -> Path:
    """Generated, machine-local data: reviews/, trail/, usage/, install.yml, migrations.applied.

    The move off the old ``~/.config/workbench`` default is a hard cut: nothing
    falls back to the legacy path, and ``lib/migrations.sh`` carries the data
    across once.
    """
    return _root("WORKBENCH_STATE_DIR", "XDG_STATE_HOME", DEFAULT_STATE_DIR)


def cache_dir(consumer: str | None = None) -> Path:
    """Recomputable data, safe to delete at any time: ``vertex-quota/``.

    ``consumer`` selects one consumer's subtree. Without it this is the root
    itself, which is what a wipe-the-cache operation wants.
    """
    root = _root("WORKBENCH_CACHE_DIR", "XDG_CACHE_HOME", DEFAULT_CACHE_DIR)
    return _subdir(root, consumer)


def trail_dir() -> Path:
    """Every trail, one root: ``<state>/trail/YYYY-MM.jsonl``.

    Mirrors ``ai_usage.ledger_dir`` and for the same reasons — rotation falls
    out of the filename and nothing needs a pruning job. The monthly naming
    also lets ``otto-log --since`` skip whole files by name instead of
    opening every one of them.
    """
    return state_dir() / TRAIL_DIRNAME


def reviews_dir() -> Path:
    """One directory per review, holding its deliverable and its artifacts.

    The owner of the join for Python, so the review system and the tools that
    read its output — ``otto-log`` for the trails, ``retro-scan`` for the
    findings — cannot disagree about where a review is. Bash reaches the same
    directory through ``REVIEWS_DIR`` in ``lib/constants.sh``, which
    ``tests/workbench_roots.bats`` holds to this value.
    """
    return state_dir() / REVIEWS_DIRNAME


def projects_registry() -> Path:
    """The repos that use the workbench, one absolute path per line.

    ``lib/projects.sh`` is the shell owner of the same file — it holds the
    membership rules, the backfill, and the CLI, and this side holds the
    writes the Python tools make. ``workbench_projects`` is where those reads
    and writes live; this function only names the path, so the two languages
    cannot disagree about it.
    """
    return state_dir() / PROJECTS_REGISTRY_NAME


# Where per-worktree state lived before the roots were split. Nothing writes
# it any more; `pr._sweep_legacy_state` still reclaims what earlier versions
# left behind.
LEGACY_WORKTREE_STATE_DIRNAME = ".workbench"
