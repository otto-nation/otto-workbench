"""Delete the local reviews a retro consumed, if the record answers to it.

Phase 4 of the retro skill. `retro-scan --consume` recorded which reviews it
read and stamped the record with its own scan ID; this presents that ID back
and deletes only what that scan claimed, and only where the review on disk is
still the one it read.

A record that names a different scan is refused rather than honoured: it
belongs to a retro that was never completed, or to a debug run, and the
reviews it lists are not this retro's to delete.

Usage:
  retro-consume --scan-id ID [--dry-run]
"""

# doc-group: cli

from __future__ import annotations

import argparse

import core.log
import core.version
import retro.consumed

SCRIPT = "retro-consume"


def build_parser() -> argparse.ArgumentParser:
    """The retro-consume parser, for `main` and for rendering its usage and flag tables."""
    parser = argparse.ArgumentParser(
        prog=SCRIPT,
        description="Delete the local reviews a retro consumed.",
    )
    parser.add_argument("-V", "--version", action="store_true", help="print version and exit")
    parser.add_argument("--scan-id", type=str, default=None, metavar="ID",
                        help="the scan ID retro-scan --consume reported")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be deleted, delete nothing")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.version:
        print(core.version.version_string(SCRIPT))
        return 0

    if not args.scan_id:
        core.log.error("--scan-id is required: quote back the ID retro-scan --consume reported")
        return 2

    return retro.consumed.consume(args.scan_id, dry_run=args.dry_run)
