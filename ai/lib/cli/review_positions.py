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
from dataclasses import asdict, dataclass, field

from review.format import parse_diff_hunks

# The binary a user runs, which is not this module's own name. Spelled out
# rather than derived, so the shim can be renamed only by changing the name in
# both places at once.
SCRIPT = "validate-review-positions"

DEMOTED_EXIT = 1
SKIPPED_EXIT = 2
BAD_INPUT_EXIT = 3


@dataclass(frozen=True)
class Positions:
    """Where each finding can be posted, once the diff has had its say.

    The field names are the payload's keys — the JSON this prints is the
    dataclass, so a caller reading the output and a caller reading the return
    value are looking at one shape.
    """

    valid: list[dict] = field(default_factory=list)
    file_level: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Every finding can be posted where the review put it."""
        return not self.file_level and not self.skipped


def validate(hunks, findings) -> Positions:
    """Classify findings as valid, file_level, or skipped."""
    valid, file_level, skipped = [], [], []

    for f in findings:
        path = f.get("path", "")
        line = f.get("line")

        if path not in hunks:
            skipped.append({**f, "reason": "path not in diff"})
        elif line is None:
            valid.append(f)
        elif not any(hunk.contains(line) for hunk in hunks[path]):
            file_level.append(
                {**f, "reason": f"line {line} not in any diff hunk"}
            )
        else:
            valid.append(f)

    return Positions(valid=valid, file_level=file_level, skipped=skipped)


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

    positions = validate(parse_diff_hunks(diff_text), findings)

    json.dump(asdict(positions), sys.stdout, indent=2)
    print()

    if positions.skipped:
        return SKIPPED_EXIT
    if positions.file_level:
        return DEMOTED_EXIT
    return 0
