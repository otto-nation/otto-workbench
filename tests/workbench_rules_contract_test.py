"""workbench-rules' CommandContract covers the parser and the codes main returns."""

import shlex
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import cli.workbench_rules  # noqa: E402
import core.cli_reference  # noqa: E402
import core.command_contract  # noqa: E402
from core.exit_codes import ExitCode  # noqa: E402
from core.tool_parser import subparsers  # noqa: E402

# CLI-owned codes main itself returns. open also exits with EDITOR's status.
_CLI_EXITS = frozenset({
    int(ExitCode.OK), int(ExitCode.FAILURE), int(ExitCode.USAGE),
})


def _documented_keys() -> list[str]:
    parser = cli.workbench_rules.build_parser()
    keys = [core.command_contract.flag_key(a)
            for a in core.cli_reference.documented(parser)]
    for sub in subparsers(parser).values():
        keys.extend(core.command_contract.flag_key(a)
                    for a in core.cli_reference.documented(sub))
        for nested in subparsers(sub).values():
            keys.extend(core.command_contract.flag_key(a)
                        for a in core.cli_reference.documented(nested))
    return keys


def test_coverage_problems_is_empty():
    assert core.command_contract.coverage_problems(
        cli.workbench_rules.CONTRACT, _documented_keys()) == []


def test_declared_exits_are_the_codes_main_returns():
    assert {int(o.code) for o in cli.workbench_rules.CONTRACT.exits} == _CLI_EXITS


def test_no_subcommand_exits_2():
    assert cli.workbench_rules.main([]) == ExitCode.USAGE
    assert cli.workbench_rules.CONTRACT.outcome(2) is not None


def test_missing_add_args_exit_2():
    assert cli.workbench_rules.main(["add"]) == ExitCode.USAGE
    assert cli.workbench_rules.main(["add", "go"]) == ExitCode.USAGE
    assert cli.workbench_rules.main(["project", "add"]) == ExitCode.USAGE


def test_help_shows_exit_codes():
    text = cli.workbench_rules.build_parser().format_help()
    assert "Exit codes:" in text
    assert "Examples:" in text


def test_reference_shape_emits_the_cli_contract():
    text = core.cli_reference.contracts(cli.workbench_rules.reference_shape())
    assert text.startswith("#### workbench-rules contract")


def test_every_example_parses():
    parser = cli.workbench_rules.build_parser()
    for example in cli.workbench_rules.CONTRACT.examples:
        argv = shlex.split(example.command)[1:]
        parser.parse_args(argv)


def test_sync_reports_oserror_as_failure(monkeypatch, capsys):
    def boom():
        raise OSError("read-only")

    monkeypatch.setattr(cli.workbench_rules.core.workbench_rules,
                        "install_workbench_rule", boom)
    assert cli.workbench_rules.main(["sync"]) == ExitCode.FAILURE
    assert "read-only" in capsys.readouterr().err


def test_open_passes_through_editor_status(monkeypatch, tmp_path):
    class Done:
        returncode = 7

    monkeypatch.setenv("EDITOR", "true")
    monkeypatch.setattr(cli.workbench_rules.core.workbench_rules,
                        "user_rules_dir", lambda: tmp_path)
    monkeypatch.setattr(cli.workbench_rules.subprocess, "run",
                        lambda *a, **k: Done())
    assert cli.workbench_rules.main(["open"]) == 7
