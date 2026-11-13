"""Every `pr` command declares a CommandContract, and the contract agrees with its parser."""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import cli.contract_notes  # noqa: E402
import cli.pr  # noqa: E402
from cli.needs import NO_LOCK  # noqa: E402
from cli.registry import COMMANDS, contract_for, need_for  # noqa: E402
import core.command_contract  # noqa: E402
from core.exit_codes import ExitCode  # noqa: E402
from core.tool_parser import ToolParser  # noqa: E402

# (command, trigger) pairs known to write outside the machine. A pinned list on
# purpose: which flags touch the remote cannot be read off a parser.
KNOWN_REMOTE = {
    "rebase": {"", "--fix", "--push-only", "--push-only --expect", "--no-verify"},
    "push": {"", "--no-verify"},
    "create": {"", "--no-verify"},
    "describe": {"--post"},
    "ci": {"--fix --post", "--no-verify"},
    "comments": {"--post", "--finish --post", "--fix --post"},
    "review": {"", "--self --fix --push", "--post", "--track"},
    "fix": {"--post"},
    "batch": {"run --auto-publish", "run --no-verify"},
}


def test_batch_flag_keys_include_run_auto_publish():
    assert "run --auto-publish" in cli.pr.documented_flag_keys("batch")


@pytest.mark.parametrize("name", sorted(COMMANDS))
def test_every_command_declares_a_contract(name):
    assert isinstance(contract_for(COMMANDS[name]),
                      core.command_contract.CommandContract)


@pytest.mark.parametrize("name", sorted(COMMANDS))
def test_the_contract_covers_the_parser(name):
    assert core.command_contract.coverage_problems(contract_for(COMMANDS[name]),
                                cli.pr.documented_flag_keys(name)) == []


@pytest.mark.parametrize("name", sorted(COMMANDS))
def test_a_locking_command_declares_the_busy_exit(name):
    if need_for(COMMANDS[name], []).locks != NO_LOCK:
        assert contract_for(COMMANDS[name]).outcome(ExitCode.TEMPFAIL) is not None


@pytest.mark.parametrize("name", sorted(COMMANDS))
def test_the_parser_s_ok_exit_codes_are_declared(name):
    parser = cli.pr.help_parser(name)
    if isinstance(parser, ToolParser):
        contract = contract_for(COMMANDS[name])
        for code in parser._ok_exit_codes:
            assert contract.outcome(code) is not None, code


@pytest.mark.parametrize("name", sorted(KNOWN_REMOTE))
def test_known_remote_triggers_have_an_effect(name):
    triggers = {e.trigger for e in contract_for(COMMANDS[name]).remote_effects}
    assert KNOWN_REMOTE[name] <= triggers


@pytest.mark.parametrize("name", sorted(COMMANDS))
def test_help_names_the_command_as_typed(name):
    assert cli.pr.help_parser(name).prog == f"pr {name}"


@pytest.mark.parametrize("name", sorted(COMMANDS))
def test_help_shows_the_rendered_contract(name):
    rendered = core.command_contract.render_help(contract_for(COMMANDS[name]))
    assert cli.pr.help_parser(name).epilog.endswith(rendered)


@pytest.mark.parametrize("name", ["status", "gc", "fix"])
def test_a_command_without_a_factory_still_answers_help(name, capsys):
    assert cli.pr.main([name, "--help"], bin_dir=REPO_ROOT / "ai" / "bin") == 0
    out = capsys.readouterr().out
    assert f"usage: pr {name}" in out
    if name == "fix":
        assert "--post" in out


def _pushes_a_branch(contract) -> bool:
    return any("refs/heads/" in effect.writes or "git push" in effect.how
               for effect in contract.remote_effects)


@pytest.mark.parametrize("name", sorted(COMMANDS))
def test_every_pushing_command_says_how_to_tell_hung_from_slow(name):
    """A contract that declares a branch push carries the one slow-or-hung note."""
    contract = contract_for(COMMANDS[name])
    if _pushes_a_branch(contract):
        assert cli.contract_notes.PUSH_WAIT in contract.notes, name


def test_the_pushing_set_is_not_empty():
    """Guards the predicate: if no contract matched, the check above would hold nothing."""
    pushing = {n for n, s in COMMANDS.items() if _pushes_a_branch(contract_for(s))}
    assert {"push", "rebase", "create", "fix"} <= pushing
