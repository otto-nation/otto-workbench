"""Scan session transcripts and memory state for dream consolidation.

Replaces Phases 1+2 of the `dream` skill (`ai/skills/dream`): Orient + Gather
Signal. The `architecture` skill also uses the path-discovery flags rather than
globbing a harness tree.

```
dream-scan [--days N] [--home DIR]
dream-scan [--days N] [--home DIR] --list-transcripts
dream-scan --memory-dir REPO
```

`--list-transcripts` prints every transcript path in the window, one per line,
and exits. `--memory-dir REPO` prints that repo's memory directory and exits.
"""

# doc-group: cli

from __future__ import annotations

import argparse
import os
from pathlib import Path

import core.version
import memory.dream
from core.trail import add_trail_args


def build_parser() -> argparse.ArgumentParser:
    """The dream-scan parser, for `main` and for rendering its usage and flag tables."""
    parser = argparse.ArgumentParser(
        prog=memory.dream.SCRIPT,
        description="Scan session transcripts and memory state for dream consolidation.",
    )
    parser.add_argument("-V", "--version", action="store_true", help="print version and exit")
    parser.add_argument("--days", type=int, default=memory.dream.DEFAULT_DAYS, metavar="N",
                        help=f"scan sessions from last N days (default: {memory.dream.DEFAULT_DAYS})")
    parser.add_argument("--home", type=str, default=os.environ.get("HOME", ""), metavar="DIR",
                        help="home directory override (for testing; default: `$HOME`)")
    exclusive = parser.add_mutually_exclusive_group()
    exclusive.add_argument(
        "--list-transcripts",
        action="store_true",
        help="print transcript paths for the window, one per line, and exit",
    )
    exclusive.add_argument(
        "--memory-dir",
        metavar="REPO",
        help="print the memory directory for the repo at REPO, and exit",
    )
    add_trail_args(parser)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.version:
        print(core.version.version_string(memory.dream.SCRIPT))
        return 0

    home = Path(args.home)

    if args.list_transcripts:
        return memory.dream.list_transcripts(home, args.days)

    if args.memory_dir is not None:
        return memory.dream.print_memory_dir(args.memory_dir)

    return memory.dream.run_scan(home, args.days, debug=args.debug)
