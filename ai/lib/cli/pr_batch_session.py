"""Shared helpers for `pr target session` / `pr target note`.

`finding_arg` parses `--finding REASON:STEP`. `exec_recipe` starts a
session recipe's pi, replacing this process.
"""

# doc-group: cli

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable

import batch.run_session


def _nothing() -> None:
    return None


def exec_recipe(recipe: batch.run_session.SessionRecipe, fail: Callable[[str], int],
                before_exec: Callable[[], None] = _nothing) -> int:
    """Start *recipe*'s pi in its cwd, replacing this process; *fail* reports why it could not."""
    if not os.path.isdir(recipe.cwd):
        return fail(f"checkout {recipe.cwd} does not exist")
    sys.stdout.flush()
    sys.stderr.flush()
    os.chdir(recipe.cwd)
    # exec never returns to the caller's finally, so a caller holding a trail
    # finishes it here; `pr batch` records none and passes nothing.
    before_exec()
    try:
        os.execvpe(recipe.argv[0], list(recipe.argv), {**os.environ, **recipe.env})
    except OSError as exc:
        return fail(f"could not start {recipe.argv[0]}: {exc}")


def finding_arg(text: str) -> str:
    reason, sep, step = text.partition(":")
    if not (reason and sep and step):
        raise argparse.ArgumentTypeError(f"--finding wants REASON:STEP, got {text!r}")
    return text
