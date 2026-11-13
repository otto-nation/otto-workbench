"""wiki's CommandContract covers the parser and the codes main returns."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import cli.wiki  # noqa: E402
import core.cli_reference  # noqa: E402
import core.command_contract  # noqa: E402
from core.exit_codes import ExitCode  # noqa: E402
from core.tool_parser import subparsers  # noqa: E402

# CLI-owned codes: 0/1/2 from main, 130 from main_entry on KeyboardInterrupt.
_CLI_EXITS = frozenset({
    int(ExitCode.OK), int(ExitCode.FAILURE), int(ExitCode.USAGE),
    int(ExitCode.CANCELLED),
})


def _documented_keys() -> list[str]:
    parser = cli.wiki.build_parser()
    keys = [core.command_contract.flag_key(a)
            for a in core.cli_reference.documented(parser)]
    for sub in subparsers(parser).values():
        keys.extend(core.command_contract.flag_key(a)
                    for a in core.cli_reference.documented(sub))
    return keys


def test_coverage_problems_is_empty():
    assert core.command_contract.coverage_problems(
        cli.wiki.CONTRACT, _documented_keys()) == []


def test_declared_exits_are_the_codes_main_returns():
    assert {int(o.code) for o in cli.wiki.CONTRACT.exits} == _CLI_EXITS


def test_no_subcommand_exits_2():
    try:
        cli.wiki.main([])
    except SystemExit as exc:
        assert exc.code == ExitCode.USAGE
    else:
        raise AssertionError("main([]) should be argparse usage")
    assert cli.wiki.CONTRACT.outcome(2) is not None


def test_cancelled_is_declared():
    """130 is core.proc.INTERRUPT_RETURNCODE, which main_entry uses on Ctrl-C."""
    assert cli.wiki.CONTRACT.outcome(ExitCode.CANCELLED) is not None


def test_help_shows_exit_codes():
    text = cli.wiki.build_parser().format_help()
    assert "Exit codes:" in text
    assert "Examples:" in text


def test_reference_shape_emits_the_cli_contract():
    text = core.cli_reference.contracts(cli.wiki.reference_shape())
    assert text.startswith("#### wiki contract")


def test_every_example_parses_as_a_real_command():
    """An example that does not parse is a lie in --help."""
    parser = cli.wiki.build_parser()
    for example in cli.wiki.CONTRACT.examples:
        argv = example.command.split()[1:]
        parser.parse_args(argv)
