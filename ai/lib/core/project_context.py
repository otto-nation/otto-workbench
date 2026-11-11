"""Which file holds a repo's agent instructions — resolved in one place.

``AGENTS.md`` is the only name. Pi reads it, and Claude Code reads it natively
from ``CLAUDE_CODE_MIN_VERSION`` — but only when no ``CLAUDE.md`` exists in the
directory chain. A ``CLAUDE.md`` anywhere in that chain, even a pointer file,
shadows the sibling ``AGENTS.md`` for Claude Code, so the workbench never
creates one and treats every tracked one as something to remove.

Every caller that reads or appends to the file — review preflight,
``workbench-rules project``, the SessionStart hook, ``ai sync``'s report —
asks here rather than carrying its own candidate list. Bash reads through the
CLI below, the way it asks ``pr_template.py`` for a template. A ``CLAUDE.md`` is
never read as instructions: it is only detected, so it can be reported.

The CLI prints one record:

    <relative path or empty><newline><state>

where state is ``agents``, ``legacy`` (a CLAUDE.md and no AGENTS.md), ``split``
(both) or ``none`` (``ContextState``). Exit 0 for every repo state; non-zero
only for a root that is not a directory. ``--shadows`` lists the CLAUDE.md
files instead, one per line.
"""

# doc-group: platform

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

# Bash runs this file by path, which puts ai/lib/core on sys.path instead of
# ai/lib, so the package import below needs the parent added in that case.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import core.timeouts  # noqa: E402

# The only instructions file, and the one the workbench writes.
AGENTS_FILE = "AGENTS.md"

# Root-level names Claude Code reads as project instructions ahead of AGENTS.md.
# `.claude/CLAUDE.md` is where the scaffold once put it.
SHADOW_FILES = ("CLAUDE.md", ".claude/CLAUDE.md")

# The workbench's own source for the user-level ~/.claude/CLAUDE.md. It is
# installed, never read as this repo's project instructions, and its name is
# defined by Claude Code, so it is not a shadow.
MANAGED_CLAUDE_MD = ("ai/claude/CLAUDE.md",)

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

    ``path`` is ``AGENTS.md``, or empty when the repo has none — a CLAUDE.md is
    never an answer. ``shadows`` names every CLAUDE.md that makes Claude Code
    ignore an AGENTS.md, and is empty for a repo that has none.
    """

    path: str
    state: ContextState
    shadows: tuple[str, ...] = ()

    @property
    def found(self) -> bool:
        """Whether the repo has an AGENTS.md."""
        return bool(self.path)


def tracked_shadows(root: Path) -> tuple[str, ...]:
    """Every CLAUDE.md under ``root``, tracked at any depth or sitting at the root.

    Tracked nested files are found through ``git ls-files`` so a monorepo's
    per-directory CLAUDE.md files are named, not only the root one. A root
    that is not a git work tree is read from disk alone.
    """
    found = {name for name in SHADOW_FILES if (root / name).is_file()}
    try:
        listing = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--", ":(glob)**/CLAUDE.md"],
            capture_output=True, text=True, check=False,
            timeout=core.timeouts.LOCAL,
        )
    except (OSError, subprocess.TimeoutExpired):
        listing = None
    if listing is not None and listing.returncode == 0:
        found.update(name for name in listing.stdout.split("\0") if name)
    return tuple(sorted(found - set(MANAGED_CLAUDE_MD)))


def resolve(root: Path) -> ProjectContext:
    """Resolve the instructions file for the checkout at ``root``.

    ``root`` must be the top of the work tree: ``AGENTS.md`` is looked up
    root-relative, so a subdirectory reads as a repo with none.
    """
    shadows = tracked_shadows(root)
    if (root / AGENTS_FILE).is_file():
        state = ContextState.SPLIT if shadows else ContextState.AGENTS
        return ProjectContext(AGENTS_FILE, state, shadows)
    if shadows:
        return ProjectContext("", ContextState.LEGACY, shadows)
    return ProjectContext("", ContextState.NONE)


def migration_hint(ctx: ProjectContext) -> str:
    """One sentence naming the CLAUDE.md files to rename or fold into AGENTS.md, or "" when none exist.

    Suggests, never performs: the file is tracked in a repo the workbench does
    not own, and renaming it is the repo's decision.
    """
    if not ctx.shadows:
        return ""
    names = ", ".join(ctx.shadows[:3]) + (" …" if len(ctx.shadows) > 3 else "")
    if ctx.state is ContextState.LEGACY:
        move = (f"git mv {ctx.shadows[0]} {Path(ctx.shadows[0]).parent.parent / AGENTS_FILE}"
                if ctx.shadows[0] == ".claude/CLAUDE.md"
                else f"git mv {ctx.shadows[0]} {Path(ctx.shadows[0]).with_name(AGENTS_FILE)}")
        return (f"{names} — {AGENTS_FILE} is the only instructions file name; "
                f"rename it: {move}"
                + (" and fold the others into "
                   f"{AGENTS_FILE}, then delete them" if len(ctx.shadows) > 1 else ""))
    return (f"{names} shadows {AGENTS_FILE} — Claude Code (>= {CLAUDE_CODE_MIN_VERSION}) "
            f"reads {AGENTS_FILE} only when no CLAUDE.md is in the directory chain; "
            f"fold it into {AGENTS_FILE} and delete it")


def main(argv: list[str] | None = None) -> int:
    """Print the record documented in the module docstring, for bash."""
    parser = argparse.ArgumentParser(
        description="Resolve a repo's agent instructions file (AGENTS.md) and report CLAUDE.md shadows.")
    parser.add_argument(
        "--root", default=".",
        help="repo root to resolve against (default: the working directory)")
    parser.add_argument(
        "--hint", action="store_true",
        help="print the CLAUDE.md removal suggestion instead, or nothing when none is due")
    parser.add_argument(
        "--shadows", action="store_true",
        help="print every CLAUDE.md that shadows AGENTS.md, one per line")
    ns = parser.parse_args(argv)

    root = Path(ns.root)
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 1

    ctx = resolve(root)
    if ns.shadows:
        for name in ctx.shadows:
            print(name)
        return 0
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
