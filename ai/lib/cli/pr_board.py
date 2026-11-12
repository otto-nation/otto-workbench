"""`pr board`: the PR board document as versioned JSON.

Usage:
  pr board --schema-version 1 --checkout DIR --phase listing|status|owed
  pr board --schema-version 1 --checkout DIR --phase status --prs N,N
  pr board --schema-version 1 --checkout DIR --phase listing --offline
  pr board --schema-version 1 --checkout DIR --phase listing --if-changed STAMP
"""

# doc-group: cli

from __future__ import annotations

import argparse

import board.document
import board.flight
import core.log
import core.report
import pr.context
from board.model import SCHEMA_VERSIONS, Phase
from core.tool_parser import EXIT_USAGE, ToolParser, enum_arg

SCRIPT = "pr board"


def _prs(text: str) -> list[int]:
    try:
        return [int(part) for part in text.split(",") if part.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"--prs wants comma-separated numbers, got {text!r}"
        ) from exc


def build_parser() -> ToolParser:
    """This command's parser; `pr` reads it for arity and for `pr board --help`."""
    parser = ToolParser(
        prog=SCRIPT,
        description="PR board document: facts, lanes, badges (JSON)",
    )
    parser.add_argument(
        "--checkout", action="append", required=True, metavar="DIR",
        help="A checkout whose open PRs to include; repeatable",
    )
    parser.add_argument(
        "--phase", required=True, type=enum_arg(Phase), choices=list(Phase),
        metavar="PHASE",
        help="listing, status, or owed",
    )
    parser.add_argument(
        "--prs", type=_prs, default=None, metavar="N,N",
        help="Comma-separated PR numbers; only with --phase status",
    )
    parser.add_argument(
        "--if-changed", dest="if_changed", default=None, metavar="STAMP",
        help="Return the unchanged envelope when this stamp still matches",
    )
    parser.add_argument(
        "--offline", action="store_true",
        help="Listing from cache only; only with --phase listing",
    )
    return parser


def cmd_board(
    argv: list[str],
    ctx: pr.context.ResolvedContext,
    *,
    schema_version: int | None = None,
    **_kw,
) -> int:
    """Parse *argv* and emit the board document for the requested phase."""
    if schema_version is None:
        served = ", ".join(str(v) for v in SCHEMA_VERSIONS)
        core.log.error(f"{SCRIPT} needs --schema-version (serves {served})")
        return EXIT_USAGE
    args = build_parser().parse_args(argv)
    if args.prs is not None and args.phase is not Phase.STATUS:
        core.log.error(f"{SCRIPT}: --prs is only valid with --phase status")
        return EXIT_USAGE
    if args.offline and args.phase is not Phase.LISTING:
        core.log.error(f"{SCRIPT}: --offline is only valid with --phase listing")
        return EXIT_USAGE
    req = board.flight.Request(
        phase=args.phase,
        checkouts=tuple(args.checkout),
        prs=tuple(args.prs) if args.prs is not None else None,
        offline=args.offline,
        if_changed=args.if_changed,
    )
    core.report.emit_json(board.flight.serve(
        req,
        lambda: board.document.build(
            args.phase,
            args.checkout,
            prs=args.prs,
            if_changed=args.if_changed,
            offline=args.offline,
        ),
    ), compact=True)
    return 0
