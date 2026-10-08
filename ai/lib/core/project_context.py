"""Which file holds a repo's agent instructions — resolved in one place.

``AGENTS.md`` is the project instructions file: Pi prefers it over ``CLAUDE.md``
(first match per directory) and Claude Code reads it natively from
``CLAUDE_CODE_MIN_VERSION``. ``CLAUDE.md`` is still honoured, as a fallback for
repos that have not moved, and the legacy ``.claude/CLAUDE.md`` after it.

Every caller that reads or appends to the file — review preflight,
``workbench-rules project``, the SessionStart hook, ``ai sync``'s migration
nudge — asks here rather than carrying its own candidate list, so the order is
spelled once. Bash reads through the CLI below, the way it asks
``pr_template.py`` for a template.

A repo with both ``AGENTS.md`` and ``CLAUDE.md`` is reported as split: by
default Claude Code then reads ``CLAUDE.md`` only while Pi reads ``AGENTS.md``,
so the two harnesses follow different instructions. The resolver answers
``AGENTS.md`` (what the workbench writes to) and says the other is there.

The CLI prints one record:

    <relative path or empty><newline><state>

where state is ``agents``, ``legacy``, ``split`` or ``none`` (``ContextState``).
Exit 0 for every repo state; non-zero only for a root that is not a directory.
"""

# doc-group: platform

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

# The file the workbench writes, and reads first.
AGENTS_FILE = "AGENTS.md"

# Read when there is no AGENTS.md, in this order. `.claude/CLAUDE.md` is where
# the scaffold used to put it; Claude Code still reads it there.
LEGACY_FILES = ("CLAUDE.md", ".claude/CLAUDE.md")

# The first Claude Code release that reads AGENTS.md as project instructions
# without a CLAUDE.md import. otto-workbench requires it; `ai sync` warns below
# it. lib/constants.sh carries the same value for bash, and
# tests/project_context_test.py holds the two together.
CLAUDE_CODE_MIN_VERSION = "2.1.277"


class ContextState(Enum):
    """What a repo's instructions look like. Values are the CLI's state line."""

    AGENTS = "agents"
    LEGACY = "legacy"
    SPLIT = "split"
    NONE = "none"


@dataclass(frozen=True)
class ProjectContext:
    """A repo's instructions file, relative to its root, and what kind it is.

    ``path`` is empty when the repo has none. ``shadowed`` names a legacy file
    sitting beside ``AGENTS.md`` (the split case), and is empty otherwise.
    """

    path: str
    state: ContextState
    shadowed: str = ""

    @property
    def found(self) -> bool:
        """Whether the repo has an instructions file at all."""
        return bool(self.path)

    @property
    def needs_migration(self) -> bool:
        """Whether a CLAUDE.md should be folded into AGENTS.md."""
        return self.state in (ContextState.LEGACY, ContextState.SPLIT)


def resolve(root: Path) -> ProjectContext:
    """Resolve the instructions file for the checkout at ``root``.

    ``root`` must be the top of the work tree: every candidate is
    root-relative, so a subdirectory reads as a repo with none.
    """
    legacy = next((name for name in LEGACY_FILES if (root / name).is_file()), "")
    if (root / AGENTS_FILE).is_file():
        if legacy:
            return ProjectContext(AGENTS_FILE, ContextState.SPLIT, shadowed=legacy)
        return ProjectContext(AGENTS_FILE, ContextState.AGENTS)
    if legacy:
        return ProjectContext(legacy, ContextState.LEGACY)
    return ProjectContext("", ContextState.NONE)


def migration_hint(ctx: ProjectContext) -> str:
    """One sentence suggesting the move to AGENTS.md, or "" when none is due.

    Suggests, never performs: the file is tracked in a repo the workbench does
    not own, and renaming it is the repo's decision.
    """
    if ctx.state is ContextState.LEGACY:
        return (f"{ctx.path} — Claude Code (>= {CLAUDE_CODE_MIN_VERSION}) and Pi both read "
                f"{AGENTS_FILE}; consider renaming it: git mv {ctx.path} {AGENTS_FILE}")
    if ctx.state is ContextState.SPLIT:
        return (f"{AGENTS_FILE} and {ctx.shadowed} both exist — Claude Code reads only "
                f"{ctx.shadowed} and Pi only {AGENTS_FILE}; fold {ctx.shadowed} into "
                f"{AGENTS_FILE} and delete it")
    return ""


def main(argv: list[str] | None = None) -> int:
    """Print the record documented in the module docstring, for bash."""
    parser = argparse.ArgumentParser(
        description="Resolve a repo's agent instructions file (AGENTS.md, else CLAUDE.md).")
    parser.add_argument(
        "--root", default=".",
        help="repo root to resolve against (default: the working directory)")
    parser.add_argument(
        "--hint", action="store_true",
        help="print the migration suggestion instead, or nothing when none is due")
    ns = parser.parse_args(argv)

    root = Path(ns.root)
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 1

    ctx = resolve(root)
    if ns.hint:
        hint = migration_hint(ctx)
        if hint:
            print(hint)
        return 0
    print(ctx.path)
    print(ctx.state.value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
