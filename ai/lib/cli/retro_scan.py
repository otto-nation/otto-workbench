"""Scan PR review comments and cross-reference against coding rules.

Replaces Phase 1 of the retro skill (Orient).
Outputs a structured markdown report to stdout.

Usage:
  retro-scan [--home DIR] [--workbench DIR] [--since DURATION] [--consume]
"""

# doc-group: cli

from __future__ import annotations

import argparse
import os
from pathlib import Path

import core.log
import core.version
import core.workbench_paths
import retro.scan
from core.trail import add_trail_args

WORKBENCH_ENV_VAR = "OTTO_WORKBENCH"


def build_parser() -> argparse.ArgumentParser:
    """The retro-scan parser, for `main` and for rendering its usage and flag tables."""
    checkout = core.workbench_paths.source_checkout()
    default_wb = os.environ.get(WORKBENCH_ENV_VAR) or (str(checkout) if checkout else None)
    parser = argparse.ArgumentParser(
        prog=retro.scan.SCRIPT,
        description="Scan PR review comments and cross-reference against coding rules.",
    )
    parser.add_argument("-V", "--version", action="store_true", help="print version and exit")
    parser.add_argument("--home", type=str, default=str(Path.home()), metavar="DIR",
                        help="home directory override (default: `$HOME`)")
    parser.add_argument("--workbench", type=str, default=default_wb, metavar="DIR",
                        help=f"workbench directory (default: ${WORKBENCH_ENV_VAR}, else the checkout this runs from)")
    parser.add_argument("--since", type=str, default=None, metavar="DURATION",
                        help="override scan window (e.g. 7d, 24h, 30m)")
    parser.add_argument(
        "--consume", action="store_true",
        help="record the local reviews read, so the retro may delete them when it completes",
    )
    add_trail_args(parser)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.version:
        print(core.version.version_string(retro.scan.SCRIPT))
        return 0

    if not args.workbench:
        core.log.error(f"no workbench checkout found: pass --workbench or set {WORKBENCH_ENV_VAR}")
        return 2

    if args.consume and args.since:
        # --since is the debugging and historical-analysis window, and a scan
        # over an arbitrary window is not the scan the retro is completing.
        # Refused rather than silently not recording: a run that asked to
        # consume and was quietly ignored would look like it had armed the
        # cleanup, and the retro would delete nothing with no reason given.
        core.log.error("--consume cannot be combined with --since: a debug window is not a retro's scan")
        return 2

    return retro.scan.run_scan(
        args.home,
        args.workbench,
        since=args.since,
        consume=args.consume,
        debug=args.debug,
    )
