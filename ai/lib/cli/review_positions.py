"""Validate review finding positions against a PR diff.

Checks that each finding's path:line falls within a diff hunk so GitHub
will accept the inline comment. Findings outside diff hunks are demoted
to file-level comments; findings for paths not in the diff are skipped.

Usage:
  validate-review-positions --diff DIFF_FILE --review FINDINGS_JSON
  gh api repos/.../pulls/N -H 'Accept: application/vnd.github.v3.diff' \
    | validate-review-positions --diff - --review findings.json

Exit codes:
  0  All findings valid (in diff hunks)
  1  Some findings demoted to file-level
  2  Some findings skipped (path not in diff)
  3  --review did not contain a JSON array
"""

# doc-group: cli

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

import review.positions
from review.format import parse_diff_hunks

# The binary a user runs, which is not this module's own name. Spelled out
# rather than derived, so the shim can be renamed only by changing the name in
# both places at once.
SCRIPT = "validate-review-positions"

DEMOTED_EXIT = 1
SKIPPED_EXIT = 2
BAD_INPUT_EXIT = 3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog=SCRIPT,
        description="Validate review finding positions against a PR diff.",
    )
    parser.add_argument(
        "--diff",
        required=True,
        help="Unified diff file (use '-' for stdin)",
    )
    parser.add_argument(
        "--review",
        required=True,
        help="JSON file with findings array [{path, line, ...}, ...]",
    )
    args = parser.parse_args(argv)

    if args.diff == "-":
        diff_text = sys.stdin.read()
    else:
        with open(args.diff) as fh:
            diff_text = fh.read()

    with open(args.review) as fh:
        findings = json.load(fh)

    if not isinstance(findings, list):
        print("Error: --review must contain a JSON array", file=sys.stderr)
        return BAD_INPUT_EXIT

    positions = review.positions.validate(parse_diff_hunks(diff_text), findings)

    json.dump(asdict(positions), sys.stdout, indent=2)
    print()

    if positions.ok:
        return 0
    if positions.skipped:
        return SKIPPED_EXIT
    return DEMOTED_EXIT
