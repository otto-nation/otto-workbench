"""Ask `core.run_lock` whether a live run holds a checkout, for a caller that is not Python.

Usage: python3 lib/run_lock_cli.py held-tree PATH

`core.run_lock` owns where a checkout's lock lives (`<git-dir>/
workbench-run-tree.lock`) and the only honest way to ask about it, a probe from
a fresh descriptor. `bin/wt-cleanup` needs the answer before it removes a
worktree, and bash cannot ask it: macOS ships no flock(1), and spelling the
path a second time in bash is a second owner that drifts. So there is one
prober, and this is how the other language reaches it — as `lib/config_cli.py`
is for the config.

Exit status:

    0   PATH does not exist, or git resolved a git dir and the lock is not held
    75  a live run holds it (`core.run_lock.EXIT_BUSY`, the code a refused run
        exits with)
    2   PATH exists and git names no git dir, or git fails or times out — no
        answer, and a caller about to delete the tree treats it as held

A usage error is also 2. A crash is 1. Either way a caller about to delete the
tree treats it as held.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# The same preamble as lib/config_cli.py: ai/lib on the path, found from this file.
_AI_LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "ai", "lib")
if _AI_LIB not in sys.path:
    sys.path.insert(0, _AI_LIB)

import core.run_lock  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="run_lock_cli.py", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    held = sub.add_parser(
        "held-tree",
        help="Exit 75 when held, 0 when free, 2 when PATH exists and git cannot answer",
    )
    held.add_argument("path", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    hold = core.run_lock.tree_hold(args.path)
    if hold is core.run_lock.TreeHold.HELD:
        return int(core.run_lock.EXIT_BUSY)
    if hold is core.run_lock.TreeHold.UNKNOWN:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
