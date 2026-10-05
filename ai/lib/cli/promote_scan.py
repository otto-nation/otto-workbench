"""Scan memories and workbench artifacts for promotion evaluation.

Replaces Phase 1 of the `promote` skill (`ai/skills/promote`): Orient. Reads
every registered repo's memory plus the workbench checkout's rules, scripts,
hooks, and agents.

```
promote-scan [--home DIR] [--workbench DIR]
```

`--workbench` defaults to `$OTTO_WORKBENCH` or the machine's main workbench
checkout.
"""

# doc-group: cli

from __future__ import annotations

import argparse
import os

import core.version
import memory.promote
from core.trail import add_trail_args

DEFAULT_WORKBENCH = "~/git/personal/otto-nation/otto-workbench/main"
WORKBENCH_ENV_VAR = "OTTO_WORKBENCH"


def build_parser() -> argparse.ArgumentParser:
    """The promote-scan parser, for `main` and for rendering its usage and flag tables."""
    default_wb = os.environ.get(WORKBENCH_ENV_VAR, DEFAULT_WORKBENCH)
    parser = argparse.ArgumentParser(
        prog=memory.promote.SCRIPT,
        description="Scan memories and workbench artifacts for promotion evaluation.",
    )
    parser.add_argument("-V", "--version", action="store_true", help="print version and exit")
    parser.add_argument("--home", type=str, default=os.environ.get("HOME", ""), metavar="DIR",
                        help="home directory override (for testing; default: `$HOME`)")
    parser.add_argument("--workbench", type=str, default=default_wb, metavar="DIR",
                        help=f"workbench directory (default: ${WORKBENCH_ENV_VAR} or {DEFAULT_WORKBENCH})")
    add_trail_args(parser)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.version:
        print(core.version.version_string(memory.promote.SCRIPT))
        return 0

    return memory.promote.run_scan(args.home, args.workbench, debug=args.debug)
