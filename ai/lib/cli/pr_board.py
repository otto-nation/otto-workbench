"""`pr board`: the PR board document as versioned JSON.

Exit codes, remote writes and examples are `CONTRACT` below.
"""

# doc-group: cli

from __future__ import annotations

import argparse

import board.document
import board.flight
import core.command_contract
import core.log
import core.report
import pr.context
from board.model import SCHEMA_VERSIONS, Phase
from core.exit_codes import ExitCode
from core.tool_parser import EXIT_USAGE, ToolParser, enum_arg

SCRIPT = "pr board"
_BOARD_VERSIONS = ", ".join(str(v) for v in SCHEMA_VERSIONS)
_BOARD_LATEST = str(SCHEMA_VERSIONS[-1])

CONTRACT = core.command_contract.CommandContract(
    summary=(
        f"The PR board document (JSON). Requires the global --schema-version "
        f"(the versions in board.model.SCHEMA_VERSIONS: {_BOARD_VERSIONS}); "
        f"without it exits 2."
    ),
    groups=(
        core.command_contract.FlagGroup("Scope", ("--checkout", "--prs")),
        core.command_contract.FlagGroup(
            "Phase", ("--phase", "--offline", "--if-changed")),
    ),
    remote_effects=(),
    exits=(
        core.command_contract.ExitOutcome(
            ExitCode.OK,
            "a document printed; per-repo failures live in repos[].error"),
        core.command_contract.ExitOutcome(
            ExitCode.FAILURE, "a checkout could not be read"),
        core.command_contract.ExitOutcome(
            ExitCode.USAGE,
            "no --schema-version; --prs without --phase status; "
            "--offline without --phase listing",
        ),
    ),
    examples=(
        core.command_contract.Example(
            f"pr --schema-version {_BOARD_LATEST} board "
            f"--checkout ~/git/o/r/main --phase listing",
            "print the listing document",
        ),
        core.command_contract.Example(
            f"pr --schema-version {_BOARD_LATEST} board "
            f"--checkout ~/git/o/r/main --phase status --prs 12,15",
            "status for named PRs",
        ),
    ),
    notes=(
        "Writes nothing remotely. --schema-version is a global flag. "
        "complete is false for status --prs and owed (clients merge by "
        "(repo, number)). Clients hide a row whose effective team lane "
        "is landed.",
    ),
)


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
