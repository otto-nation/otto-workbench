"""Rebuild review.md from group finding files.

Reads group-N.md files from the review directory, merges findings,
post-processes them, and writes a new review.md. Used to recover from
synthesis agent formatting drift or corrupted review files.
"""

# doc-group: cli

from __future__ import annotations

import argparse
from pathlib import Path

import review.rebuild
from core.trail import add_trail_args

# The binary a user runs and the trail records, which is not this module's own
# name. Spelled out rather than derived, so the shim can be renamed only by
# changing the name in both places at once.
SCRIPT = "review-rebuild"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=SCRIPT,
                                     description="Rebuild review.md from group findings")
    parser.add_argument("--review-dir", required=True, help="Path to the review directory")
    parser.add_argument("--pr", required=True, help="PR number")
    add_trail_args(parser)
    args = parser.parse_args(argv)
    return review.rebuild.rebuild(Path(args.review_dir), args.pr, script=SCRIPT, debug=args.debug)
