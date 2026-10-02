"""Tests for core.cli_reference — usage lines and flag tables from argparse."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "ai" / "lib"))

import core.cli_reference  # noqa: E402
from core.tool_parser import ToolParser  # noqa: E402


def _parser(prog: str = "tool") -> argparse.ArgumentParser:
    return argparse.ArgumentParser(prog=prog, add_help=False)


# ── Synopsis ───────────────────────────────────────────────────────────────


class TestSynopsis:
    def test_an_optional_flag_is_bracketed_and_a_required_one_is_not(self):
        p = _parser()
        p.add_argument("--fix", action="store_true")
        p.add_argument("--rule", required=True)
        assert core.cli_reference.synopsis(p) == "[--fix] --rule <rule>"

    def test_every_option_string_is_shown(self):
        """An alias is documented the moment it is declared — `--base` on rebase."""
        p = _parser()
        p.add_argument("--onto", "--base", dest="onto", metavar="REF")
        assert core.cli_reference.synopsis(p) == "[--onto|--base <ref>]"

    def test_a_suppressed_flag_stays_out(self):
        p = _parser()
        p.add_argument("--no-issue", action="store_true", help=argparse.SUPPRESS)
        p.add_argument("--draft", action="store_true")
        assert core.cli_reference.synopsis(p) == "[--draft]"

    def test_framework_flags_stay_out(self):
        p = ToolParser(prog="tool")
        p.add_argument("--debug", action="store_true")
        p.add_argument("--fix", action="store_true")
        assert core.cli_reference.synopsis(p) == "[--fix]"

    def test_a_mutually_exclusive_group_is_one_bracket(self):
        p = _parser()
        group = p.add_mutually_exclusive_group()
        group.add_argument("--body", metavar="TEXT")
        group.add_argument("--body-file", metavar="PATH")
        assert core.cli_reference.synopsis(p) == "[--body <text> | --body-file <path>]"

    def test_choices_spell_the_value(self):
        p = _parser()
        p.add_argument("--effort", choices=["low", "high"])
        assert core.cli_reference.synopsis(p) == "[--effort <low|high>]"

    @pytest.mark.parametrize(("nargs", "shown"), [
        (None, "<ref>"),
        ("?", "[<ref>]"),
        ("*", "[<ref> ...]"),
        ("+", "<ref> ..."),
    ])
    def test_a_positional_is_bracketed_by_its_nargs(self, nargs, shown):
        p = _parser()
        p.add_argument("ref", nargs=nargs)
        assert core.cli_reference.synopsis(p) == shown


# ── Shapes ─────────────────────────────────────────────────────────────────


def _dispatcher() -> argparse.ArgumentParser:
    p = _parser("disp")
    p.add_argument("--repo-dir", metavar="PATH", help="Worktree")
    sub = p.add_subparsers(dest="command")
    first = sub.add_parser("first", help="Do the first thing", add_help=False)
    first.add_argument("--fast", action="store_true", help="Go fast")
    first.add_argument("--repo-dir", metavar="PATH", help="Worktree")
    sub.add_parser("second", help="Do the second thing", add_help=False)
    return p


class TestShapes:
    def test_subparsers_become_commands_in_declaration_order(self):
        shape = core.cli_reference.shape_of(_dispatcher(), "disp")
        assert [c.name for c in shape.commands] == ["first", "second"]
        assert shape.commands[0].help == "Do the first thing"

    def test_an_alias_does_not_document_a_command_twice(self):
        p = _parser("disp")
        sub = p.add_subparsers(dest="command")
        sub.add_parser("status", aliases=["st"], add_help=False)
        assert [c.name for c in core.cli_reference.shape_of(p, "disp").commands] == ["status"]

    def test_the_usage_line_has_one_invocation_per_command(self):
        line = core.cli_reference.usage_line(core.cli_reference.shape_of(_dispatcher(), "disp"))
        assert line == "disp first [--fast]  |  disp second"

    def test_a_global_flag_a_command_redeclares_is_documented_once(self):
        """Delegates redeclare the dispatcher's context flags to run alone."""
        shape = core.cli_reference.shape_of(_dispatcher(), "disp")
        out = core.cli_reference.tables(shape)
        assert out.count("`--repo-dir`") == 1
        assert "--repo-dir" not in core.cli_reference.usage_line(shape)

    def test_a_cli_with_no_commands_is_one_invocation(self):
        p = _parser("scan")
        p.add_argument("--json", action="store_true")
        shape = core.cli_reference.shape_of(p, "scan")
        assert core.cli_reference.usage_line(shape) == "scan [--json]"
        assert core.cli_reference.tables(shape).startswith("**Flags**")

    def test_a_command_with_nothing_to_document_says_so(self):
        out = core.cli_reference.tables(core.cli_reference.shape_of(_dispatcher(), "disp"))
        assert "**`disp second`** — Do the second thing\n\nTakes no flags." in out


# ── Tables ─────────────────────────────────────────────────────────────────


def _row(parser: argparse.ArgumentParser, flag: str) -> str:
    out = core.cli_reference.tables(core.cli_reference.CLIShape(prog="tool", globals=parser))
    return next(line for line in out.splitlines() if line.startswith(f"| `{flag}`"))


class TestTables:
    def test_a_default_the_help_does_not_state_is_added(self):
        p = _parser()
        p.add_argument("--wait", type=int, default=30, help="Seconds to wait")
        assert _row(p, "--wait") == "| `--wait` `<wait>` | Seconds to wait. Default: `30`. |"

    def test_a_default_the_help_already_states_is_not_repeated(self):
        p = _parser()
        p.add_argument("--wait", type=int, default=30, help="Seconds (default: 30)")
        assert "Default:" not in _row(p, "--wait")

    def test_an_unset_default_says_nothing(self):
        p = _parser()
        p.add_argument("--title", default="", help="Title")
        assert _row(p, "--title") == "| `--title` `<title>` | Title. |"

    def test_an_append_flag_is_repeatable(self):
        p = _parser()
        p.add_argument("--closes", action="append", help="Issue to close")
        assert "Repeatable." in _row(p, "--closes")

    def test_exclusive_flags_name_each_other(self):
        p = _parser()
        group = p.add_mutually_exclusive_group()
        group.add_argument("--body", help="Body")
        group.add_argument("--body-file", help="File")
        assert "Not with `--body-file`." in _row(p, "--body")

    def test_a_pipe_in_a_cell_is_escaped(self):
        p = _parser()
        p.add_argument("--pr", metavar="num|url", help="PR number | URL")
        assert _row(p, "--pr") == "| `--pr` `<num\\|url>` | PR number \\| URL. |"

    def test_percent_fields_in_help_are_expanded(self):
        p = _parser()
        p.add_argument("--n", type=int, default=4, help="Count (default: %(default)s)")
        assert "Count (default: 4)" in _row(p, "--n")


# ── Loading a registry `parser:` ───────────────────────────────────────────


class TestLoad:
    def test_a_module_attr_loads_and_is_called(self):
        shape = core.cli_reference.load("cli.review_entry:build_parser", "review", REPO_ROOT)
        assert shape.prog == "review"
        assert "--self" in core.cli_reference.usage_line(shape)

    def test_a_path_attr_loads_a_script_without_running_it(self, tmp_path):
        script = tmp_path / "bin" / "my-tool"
        script.parent.mkdir()
        script.write_text(
            "#!/usr/bin/env python3\n"
            "import argparse, sys\n"
            "def build_parser():\n"
            "    p = argparse.ArgumentParser(prog='my-tool')\n"
            "    p.add_argument('--json', action='store_true')\n"
            "    return p\n"
            "if __name__ == '__main__':\n"
            "    sys.exit(3)\n")
        shape = core.cli_reference.load("bin/my-tool:build_parser", "my-tool", tmp_path)
        assert core.cli_reference.usage_line(shape) == "my-tool [--json]"

    def test_a_script_that_fails_to_load_is_not_left_in_sys_modules(self, tmp_path):
        script = tmp_path / "bin" / "broken-tool"
        script.parent.mkdir()
        script.write_text("raise RuntimeError('half-initialised')\n")
        name = "_cli_reference_broken_tool"

        with pytest.raises(RuntimeError, match="half-initialised"):
            core.cli_reference.load("bin/broken-tool:build_parser", "broken-tool", tmp_path)

        assert name not in sys.modules

    def test_a_shape_is_used_as_is(self):
        shape = core.cli_reference.load("cli.pr:reference_shape", "pr", REPO_ROOT)
        assert [c.name for c in shape.commands][0] == "create"

    @pytest.mark.parametrize("spec", ["cli.review_entry", ":build_parser", "cli.review_entry:"])
    def test_a_malformed_spec_raises(self, spec):
        with pytest.raises(ValueError):
            core.cli_reference.load(spec, "x", REPO_ROOT)

    def test_something_that_is_not_a_parser_raises(self):
        with pytest.raises(TypeError):
            core.cli_reference.load("core.cli_reference:USAGE_SEPARATOR", "x", REPO_ROOT)


# ── The pr shape ───────────────────────────────────────────────────────────


class TestPrShape:
    """What `pr`'s rendered reference must hold — the drift this replaced."""

    @pytest.fixture(scope="class")
    def shape(self):
        import cli.pr
        return cli.pr.reference_shape()

    def test_every_registered_command_is_documented(self, shape):
        from cli.registry import COMMANDS
        assert [c.name for c in shape.commands] == list(COMMANDS)

    def test_rebase_documents_both_spellings_of_its_base(self, shape):
        rebase = next(c for c in shape.commands if c.name == "rebase")
        assert "--onto|--base" in core.cli_reference.synopsis(rebase.parser)

    def test_review_documents_the_mode_flags_its_dispatcher_adds(self, shape):
        import cli.review_modes
        review = next(c for c in shape.commands if c.name == "review")
        synopsis = core.cli_reference.synopsis(review.parser)
        for flag in cli.review_modes.MODES:
            assert f"[{flag}]" in synopsis, flag

    def test_the_global_flags_are_the_ones_the_first_pass_consumes(self, shape):
        import cli.pr
        assert shape.globals is not None
        strings = {s for a in shape.globals._actions for s in a.option_strings}
        assert {"--repo-dir", "--worktree", "--branch", "--pr", "--schema-version"} <= strings
        args, _ = cli.pr.build_global_parser().parse_known_args(["--worktree", "/x", "status"])
        assert args.repo_dir == "/x"

    def test_fix_documents_the_flags_it_forwards(self, shape):
        fix = next(c for c in shape.commands if c.name == "fix")
        synopsis = core.cli_reference.synopsis(fix.parser)
        assert "[--post]" in synopsis
        assert "review-or-ci-flag" in synopsis
        section = core.cli_reference.tables(shape).split("**`pr fix`**")[1].split("**`pr rebase`**")[0]
        assert "Takes no flags" not in section

    def test_a_command_with_no_parser_and_no_declaration_fails(self):
        import cli.pr
        with pytest.raises(RuntimeError, match="not declared to take no flags"):
            cli.pr._reference_parser("newcmd", {"newcmd": _parser()})

    def test_no_documented_flag_has_an_empty_description(self, shape):
        out = core.cli_reference.tables(shape)
        empty = [line for line in out.splitlines() if line.endswith("|  |")]
        assert empty == []


# ── Machine-independent output ─────────────────────────────────────────────


@pytest.mark.parametrize("script", ["dream-scan", "promote-scan", "retro-scan"])
def test_a_home_default_is_not_rendered_into_the_tables(script, monkeypatch):
    """The tables are committed; the generating machine's $HOME must not be in them."""
    monkeypatch.setenv("HOME", "/home/a-unique-user")
    shape = core.cli_reference.load(f"ai/bin/{script}:build_parser", script, REPO_ROOT)
    assert "a-unique-user" not in core.cli_reference.tables(shape)
