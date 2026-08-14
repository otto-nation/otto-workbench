"""The three workbench roots — config, state, and cache.

Each resolves through the same chain:

    WORKBENCH_<ROOT>_DIR  →  XDG_<ROOT>_HOME/workbench  →  built-in default

This module is the Python owner. Two other definitions express the same chain
and must stay in step: ``lib/constants.sh`` for shell, and
``zsh/config.d/aliases/docker.zsh``, which cannot source ``constants.sh`` at
shell startup. ``tests/workbench_roots.bats`` cross-validates all three.

Roots are resolved per call rather than frozen into module constants: the
environment is routinely set after import — by tests, and by callers that
re-point a root before invoking a subprocess — and an import-time constant
would capture whichever value happened to be live when the first importer
loaded this module.
"""

from __future__ import annotations

import os
from pathlib import Path


def _root(env_var: str, xdg_var: str | None, fallback: str) -> Path:
    override = os.environ.get(env_var)
    if override:
        return Path(override)
    xdg_home = os.environ.get(xdg_var) if xdg_var else None
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
    """Hand-authored settings: install.yml, overrides/, mcp-tools.json."""
    return _root("WORKBENCH_CONFIG_DIR", "XDG_CONFIG_HOME", "~/.config/workbench")


def state_dir() -> Path:
    """Generated, machine-local data: reviews/, logs/, usage/, applied migrations.

    No ``XDG_STATE_HOME`` rung yet, and the fallback is still the legacy config
    path — see ``lib/constants.sh`` for why. #624 phase 4 adds the rung and
    flips the fallback alongside the migration that carries the data.
    """
    return _root("WORKBENCH_STATE_DIR", None, "~/.config/workbench")


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
