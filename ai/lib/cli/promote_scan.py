"""Scan memories and workbench artifacts for promotion evaluation.

Replaces Phase 1 of the `promote` skill (`ai/skills/promote`): Orient. Reads
every registered repo's memory plus the workbench checkout's rules, scripts,
hooks, and agents.

```
promote-scan [--home DIR] [--workbench DIR]
```

`--workbench` defaults to `$OTTO_WORKBENCH`, else the checkout this runs from.
"""

# doc-group: cli

from __future__ import annotations

import argparse
import os

import core.log
import core.version
import core.workbench_paths
import memory.promote
from core.trail import add_trail_args


def build_parser() -> argparse.ArgumentParser:
    """The promote-scan parser, for `main` and for rendering its usage and flag tables."""
    default_wb = core.workbench_paths.default_workbench()
    parser = argparse.ArgumentParser(
        prog=memory.promote.SCRIPT,
        description="Scan memories and workbench artifacts for promotion evaluation.",
    )
    parser.add_argument("-V", "--version", action="store_true", help="print version and exit")
    parser.add_argument("--home", type=str, default=os.environ.get("HOME", ""), metavar="DIR",
                        help="home directory override (for testing; default: `$HOME`)")
    parser.add_argument("--workbench", type=str, default=default_wb, metavar="DIR",
                        help=f"workbench directory (default: ${core.workbench_paths.WORKBENCH_ENV_VAR}, else the checkout this runs from)")
    add_trail_args(parser)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.version:
        print(core.version.version_string(memory.promote.SCRIPT))
        return 0

    if not args.workbench:
        core.log.error(
            f"no workbench checkout found: pass --workbench or set "
            f"{core.workbench_paths.WORKBENCH_ENV_VAR}"
        )
        return 2

    return memory.promote.run_scan(args.home, args.workbench, debug=args.debug)
