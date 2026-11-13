"""otto-queue's CommandContract covers the parser and the codes main returns."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import cli.otto_queue  # noqa: E402
import core.cli_reference  # noqa: E402
import core.command_contract  # noqa: E402
from core.exit_codes import ExitCode  # noqa: E402
from core.tool_parser import subparsers  # noqa: E402

# CLI-owned codes main itself returns. run also exits with COMMAND's status.
_CLI_EXITS = frozenset({
    int(ExitCode.OK), int(ExitCode.FAILURE), int(ExitCode.USAGE),
    int(ExitCode.TEMPFAIL), int(ExitCode.CANCELLED),
})


def _documented_keys() -> list[str]:
    parser = cli.otto_queue.build_parser()
    keys = [core.command_contract.flag_key(a)
            for a in core.cli_reference.documented(parser)]
    for sub in subparsers(parser).values():
        keys.extend(core.command_contract.flag_key(a)
                    for a in core.cli_reference.documented(sub))
    return keys


def test_coverage_problems_is_empty():
    assert core.command_contract.coverage_problems(
        cli.otto_queue.CONTRACT, _documented_keys()) == []


def test_declared_exits_are_the_codes_main_returns():
    assert {int(o.code) for o in cli.otto_queue.CONTRACT.exits} == _CLI_EXITS


def test_unknown_cancel_exits_1():
    """Pinned in tests/otto_queue_cli_test.py::test_cancel_of_an_unknown_entry_exits_1."""
    assert cli.otto_queue.main(["cancel", "99"]) == ExitCode.FAILURE
    assert cli.otto_queue.CONTRACT.outcome(1) is not None


def test_no_subcommand_exits_2():
    assert cli.otto_queue.main([]) == ExitCode.USAGE
    assert cli.otto_queue.CONTRACT.outcome(2) is not None


def test_busy_and_cancelled_are_declared():
    """75 and 130 are pinned in otto_queue_cli_test (busy ledger, Ctrl-C on join)."""
    assert cli.otto_queue.CONTRACT.outcome(ExitCode.TEMPFAIL) is not None
    assert cli.otto_queue.CONTRACT.outcome(ExitCode.CANCELLED) is not None


def test_help_shows_exit_codes():
    text = cli.otto_queue.build_parser().format_help()
    assert "Exit codes:" in text
    assert "Examples:" in text


def test_reference_shape_emits_the_cli_contract():
    text = core.cli_reference.contracts(cli.otto_queue.reference_shape())
    assert text.startswith("#### otto-queue contract")


def test_every_example_parses_and_runs_a_real_command():
    """An example that does not parse, or hands `--` to run as the command, is a lie in --help."""
    parser = cli.otto_queue.build_parser()
    for example in cli.otto_queue.CONTRACT.examples:
        argv = example.command.split()[1:]
        args = parser.parse_args(argv)
        if args.command == "run":
            command = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
            assert command and command[0] != "--", example.command
