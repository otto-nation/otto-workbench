"""Tests for `core.command_contract`: the vocabulary and its four renderings."""

import argparse
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import core.command_contract  # noqa: E402
from core.exit_codes import ExitCode  # noqa: E402


def _contract() -> core.command_contract.CommandContract:
    return core.command_contract.CommandContract(
        summary="Do the thing.",
        groups=(core.command_contract.FlagGroup("Push", ("--go",)), core.command_contract.FlagGroup("Local", ("--dry",))),
        exits=(
            core.command_contract.ExitOutcome(ExitCode.OK, "done"),
            core.command_contract.ExitOutcome(ExitCode.FAILURE, "broke", (
                core.command_contract.NextStep("", ("look at {branch}",)),)),
            core.command_contract.ExitOutcome(ExitCode.USAGE, "bad flags"),
            core.command_contract.ExitOutcome(ExitCode.CONFLICTS, "conflicts", (
                core.command_contract.NextStep("paused", ("resolve in {worktree}", "continue")),
                core.command_contract.NextStep("clean", ("re-run with --fix",)),
            )),
        ),
        examples=(core.command_contract.Example("pr thing --go", "does it"),),
        remote_effects=(core.command_contract.RemoteEffect("--go", "refs/heads/<branch>", "origin",
                                        "git push", "nothing is written"),),
        json_output=core.command_contract.JsonOutput("An object.", (core.command_contract.EnumDoc("kind", (
            core.command_contract.EnumValue("a", "first"),)),)),
        notes=("A note.",),
    )


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pr thing")
    p.add_argument("--go", action="store_true", help="go")
    p.add_argument("--dry", action="store_true", help="dry")
    return p


def test_next_steps_select_by_when_and_fill_fields():
    c = _contract()
    assert c.next_steps(3, when="paused", worktree="/wt") == ["resolve in /wt", "continue"]
    assert c.next_steps(3, when="clean") == ["re-run with --fix"]
    assert c.next_steps(1, when="paused", branch="b") == ["look at b"]
    assert c.next_steps(0) == []


def test_print_next_writes_a_numbered_block_to_stderr(capsys):
    core.command_contract.print_next(_contract(), 3, when="paused", worktree="/wt")
    out, err = capsys.readouterr()
    assert out == ""
    assert err == "Next:\n  1. resolve in /wt\n  2. continue\n"


def test_print_next_is_silent_without_steps(capsys):
    core.command_contract.print_next(_contract(), 0)
    assert capsys.readouterr().err == ""


def test_help_carries_every_section():
    text = core.command_contract.render_help(_contract())
    for needle in ("Writes remotely:", "--go", "git push", "Exit codes:",
                   "  3  conflicts", "Examples:", "pr thing --go", "JSON on stdout:",
                   "kind", "a  first", "A note.", core.command_contract.REFERENCE_ANCHOR):
        assert needle in text, needle


def test_attach_help_regroups_flags_and_keeps_newlines():
    p = _parser()
    core.command_contract.attach_help(p, _contract())
    text = p.format_help()
    # argparse always repeats option strings in the usage line; regrouping
    # only owns the titled sections below it.
    body = text[text.index("\n"):]
    assert body.index("Push:") < body.index("--go") < body.index("Local:") < body.index("--dry")
    assert "Exit codes:\n" in text
    assert p.epilog == core.command_contract.render_help(_contract())


def test_markdown_has_an_anchored_heading_and_tables():
    md = core.command_contract.render_markdown("pr thing", _contract())
    assert md.startswith("#### pr thing contract\n")
    assert "| `--go` | refs/heads/<branch> | origin | git push | nothing is written |" in md
    assert "| `3` | conflicts |" in md
    assert core.command_contract.anchor("pr thing") == "pr-thing-contract"


def test_schema_carries_exit_codes_and_remote_effects():
    doc = core.command_contract.schema(_contract())
    assert doc["exit_codes"][3] == {"code": 3, "name": "conflicts", "meaning": "conflicts"}
    assert doc["remote_effects"][0]["trigger"] == "--go"


def test_coverage_accepts_an_exact_partition():
    assert core.command_contract.coverage_problems(_contract(), ["--go", "--dry"]) == []


@pytest.mark.parametrize("documented, needle", [
    (["--go", "--dry", "--new"], "--new is in no flag group"),
    (["--go"], "--dry is grouped but not declared"),
])
def test_coverage_names_what_drifted(documented, needle):
    assert any(needle in p for p in core.command_contract.coverage_problems(_contract(), documented))


def test_coverage_rejects_a_trigger_naming_an_undeclared_flag():
    c = _contract()
    bad = core.command_contract.CommandContract(c.summary, c.groups, c.exits, c.examples,
                             (core.command_contract.RemoteEffect("--nope", "x", "y", "z", "w"),))
    assert any("--nope" in p for p in core.command_contract.coverage_problems(bad, ["--go", "--dry"]))


def test_coverage_requires_ok_failure_and_usage():
    c = _contract()
    bad = core.command_contract.CommandContract(c.summary, c.groups, c.exits[:1], c.examples)
    problems = core.command_contract.coverage_problems(bad, ["--go", "--dry"])
    assert any("exit 1" in p for p in problems) and any("exit 2" in p for p in problems)
