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
import retro.scan
from core.trail import add_trail_args

DEFAULT_WORKBENCH = "~/git/personal/otto-nation/otto-workbench/main"
WORKBENCH_ENV_VAR = "OTTO_WORKBENCH"


def main(argv: list[str] | None = None) -> int:
    default_wb = os.environ.get(WORKBENCH_ENV_VAR, DEFAULT_WORKBENCH)

    parser = argparse.ArgumentParser(
        description="Scan PR review comments and cross-reference against coding rules.",
        usage="retro-scan [--home DIR] [--workbench DIR] [--since DURATION] [--consume]",
    )
    parser.add_argument("-V", "--version", action="store_true", help="print version and exit")
    parser.add_argument("--home", type=str, default=str(Path.home()), help="home directory override")
    parser.add_argument("--workbench", type=str, default=default_wb, help=f"workbench directory (default: ${WORKBENCH_ENV_VAR} or {DEFAULT_WORKBENCH})")
    parser.add_argument("--since", type=str, default=None, help="override scan window (e.g. 7d, 24h, 30m)")
    parser.add_argument(
        "--consume", action="store_true",
        help="record the local reviews read, so the retro may delete them when it completes",
    )
    add_trail_args(parser)
    args = parser.parse_args(argv)

    if args.version:
        print(core.version.version_string(retro.scan.SCRIPT))
        return 0

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
