"""Check that `--add-dir` still loads the operator's coding rules.

Usage:
  rules-canary [--json] [--floor N]

Runs one trivial prompt twice against the same planted fixture directory,
differing only in whether `--add-dir` is passed, and fails when the difference
in billed input tokens falls below the floor. Why the check is a difference of
two runs rather than one absolute reading is on `eval/rules_canary.py`.

Spends two real model calls — a few cents cold, near-free warm. It is not a
pull-request gate: it runs in the weekly Eval workflow, before the corpus run
that spends the real money.

Exit codes:
  0  the rules still arrive
  1  they do not — every agent is running without coding rules
  2  the canary could not measure (no CLI, no credentials, a run billing zero)
"""

# doc-group: cli

from __future__ import annotations

import argparse

import core.version
import eval.rules_canary

SCRIPT = "rules-canary"


def build_parser() -> argparse.ArgumentParser:
    """The rules-canary parser, for `main` and for rendering its usage and flag tables."""
    parser = argparse.ArgumentParser(
        description="Check that --add-dir still loads coding rules into an agent.",
        usage="rules-canary [--json] [--floor N]",
    )
    parser.add_argument("-V", "--version", action="store_true", help="print version and exit")
    parser.add_argument("--json", action="store_true", help="emit the measurement as JSON")
    parser.add_argument(
        "--floor", type=int, default=eval.rules_canary.RULES_PREFIX_FLOOR,
        help=f"minimum token delta to accept (default: {eval.rules_canary.RULES_PREFIX_FLOOR})",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.version:
        print(core.version.version_string(SCRIPT))
        return 0

    return eval.rules_canary.check(args.floor, as_json=args.json)
