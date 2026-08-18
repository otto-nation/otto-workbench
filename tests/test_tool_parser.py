"""Tests for tool_parser — ToolParser argparse extension."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

from tool_parser import (
    VALUE_FLAGS_FLAG, ToolParser, handle_value_flags, subparsers,
    value_taking_options,
)


# ── Fixtures ───────────────────────────────────────────────────────────────


@dataclass
class RebaseResult:
    status: str
    commits_replayed: int = 0
    force_pushed: bool = False


def _make_parser(output_schema=None):
    parser = ToolParser(
        prog="test-tool",
        description="A test tool",
        output_schema=output_schema,
    )
    parser.add_argument("--repo-dir", dest="repo_dir", help="Git worktree directory")
    parser.add_argument("--branch", help="Branch name")
    parser.add_argument("--pr", help="PR number or URL")
    parser.add_argument("--fix", action="store_true", help="Auto-resolve conflicts")
    parser.add_argument("--count", type=int, default=10, help="Number of items")
    parser.add_argument("--effort", choices=["low", "medium", "high"], default="medium")
    return parser


# ── Tests ──────────────────────────────────────────────────────────────────


def test_tool_schema_flag_exits(capsys):
    parser = _make_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["--tool-schema"])
    assert exc_info.value.code == 0

    captured = capsys.readouterr()
    schema = json.loads(captured.out)
    assert schema["name"] == "test-tool"
    assert schema["description"] == "A test tool"


def test_input_schema_properties(capsys):
    parser = _make_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--tool-schema"])

    schema = json.loads(capsys.readouterr().out)
    props = schema["input_schema"]["properties"]

    assert props["fix"]["type"] == "boolean"
    assert props["count"]["type"] == "integer"
    assert props["count"]["default"] == 10
    assert props["effort"]["type"] == "string"
    assert props["effort"]["enum"] == ["low", "medium", "high"]


def test_context_args_tagged(capsys):
    parser = _make_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--tool-schema"])

    schema = json.loads(capsys.readouterr().out)
    props = schema["input_schema"]["properties"]

    assert props["repo_dir"].get("x-context") is True
    assert props["branch"].get("x-context") is True
    assert props["pr"].get("x-context") is True
    assert "x-context" not in props["fix"]
    assert "x-context" not in props["count"]
    assert "x-context" not in props["effort"]


def test_framework_args_excluded(capsys):
    parser = _make_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--tool-schema"])

    schema = json.loads(capsys.readouterr().out)
    props = schema["input_schema"]["properties"]

    assert "help" not in props
    assert "tool_schema" not in props
    assert "debug" not in props


def test_output_schema_dataclass(capsys):
    parser = _make_parser(output_schema=RebaseResult)
    with pytest.raises(SystemExit):
        parser.parse_args(["--tool-schema"])

    schema = json.loads(capsys.readouterr().out)
    out = schema["output_schema"]
    assert out["type"] == "object"
    assert "status" in out["properties"]
    assert out["properties"]["status"]["type"] == "string"
    assert "status" in out["required"]


def test_output_schema_dict(capsys):
    custom_schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
    parser = _make_parser(output_schema=custom_schema)
    with pytest.raises(SystemExit):
        parser.parse_args(["--tool-schema"])

    schema = json.loads(capsys.readouterr().out)
    assert schema["output_schema"] == custom_schema


def test_no_output_schema(capsys):
    parser = _make_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--tool-schema"])

    schema = json.loads(capsys.readouterr().out)
    assert "output_schema" not in schema


def test_normal_parsing_unaffected():
    parser = _make_parser()
    args = parser.parse_args(["--fix", "--count", "5"])
    assert args.fix is True
    assert args.count == 5


def test_description_in_properties(capsys):
    parser = _make_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--tool-schema"])

    schema = json.loads(capsys.readouterr().out)
    props = schema["input_schema"]["properties"]
    assert props["fix"]["description"] == "Auto-resolve conflicts"
    assert props["count"]["description"] == "Number of items"


def test_store_false_action(capsys):
    parser = ToolParser(prog="test", description="test")
    parser.add_argument("--push", action="store_true", default=True)
    parser.add_argument("--no-push", action="store_false", dest="push")
    with pytest.raises(SystemExit):
        parser.parse_args(["--tool-schema"])

    schema = json.loads(capsys.readouterr().out)
    props = schema["input_schema"]["properties"]
    assert props["push"]["type"] == "boolean"
    assert "no_push" not in props


# ── value-flags probe ──────────────────────────────────────────────────────


def test_value_taking_options_lists_only_value_options():
    assert value_taking_options(_make_parser()) == [
        "--branch", "--count", "--effort", "--pr", "--repo-dir",
    ]


def test_value_taking_options_lists_every_alias():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-dir", "--worktree", dest="repo_dir")
    assert value_taking_options(parser) == ["--repo-dir", "--worktree"]


def test_value_taking_options_covers_append_and_hidden_options():
    """append takes a value, and a SUPPRESSed option is still a real flag."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--track", action="append", default=[])
    parser.add_argument("--secret", help=argparse.SUPPRESS)
    parser.add_argument("--verbose", action="count")
    assert value_taking_options(parser) == ["--secret", "--track"]


def test_value_taking_options_ignores_positionals():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("args", nargs="*")
    assert value_taking_options(parser) == []


# ── subparser lookup ───────────────────────────────────────────────────────


def test_subparsers_returns_each_command_by_name():
    parser = argparse.ArgumentParser(add_help=False)
    sub = parser.add_subparsers(dest="command")
    review = sub.add_parser("review")
    create = sub.add_parser("create", add_help=False)
    assert subparsers(parser) == {"review": review, "create": create}


def test_subparsers_returns_the_parser_that_declares_the_flags():
    """The point of the lookup: arity is read off the declaring subparser."""
    parser = argparse.ArgumentParser(add_help=False)
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("status", add_help=False).add_argument("--draft", action="store_true")
    sub.add_parser("create", add_help=False).add_argument("--title")
    assert value_taking_options(subparsers(parser)["status"]) == []
    assert value_taking_options(subparsers(parser)["create"]) == ["--title"]


def test_subparsers_lists_every_alias():
    parser = argparse.ArgumentParser(add_help=False)
    sub = parser.add_subparsers(dest="command")
    comments = sub.add_parser("comments", aliases=["threads"])
    assert subparsers(parser) == {"comments": comments, "threads": comments}


def test_subparsers_is_empty_for_a_parser_with_no_commands():
    assert subparsers(_make_parser()) == {}


def test_subparsers_is_a_copy_the_caller_cannot_corrupt():
    """The result is handed out, so mutating it must not unregister a command."""
    parser = argparse.ArgumentParser(add_help=False)
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("status", add_help=False)
    subparsers(parser).clear()
    assert parser.parse_args(["status"]).command == "status"


def test_handle_value_flags_prints_and_exits(capsys):
    parser = _make_parser()
    with pytest.raises(SystemExit) as exc_info:
        handle_value_flags(parser, [VALUE_FLAGS_FLAG])
    assert exc_info.value.code == 0
    assert capsys.readouterr().out.split() == [
        "--branch", "--count", "--effort", "--pr", "--repo-dir",
    ]


def test_handle_value_flags_is_a_noop_without_the_flag(capsys):
    handle_value_flags(_make_parser(), ["--fix"])
    assert capsys.readouterr().out == ""


def test_handle_value_flags_reads_sys_argv_by_default(capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["test-tool", VALUE_FLAGS_FLAG])
    with pytest.raises(SystemExit):
        handle_value_flags(_make_parser())
    assert "--branch" in capsys.readouterr().out


def test_tool_parser_answers_the_probe(capsys):
    """ToolParser scripts inherit the probe the same way they inherit --tool-schema."""
    parser = _make_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args([VALUE_FLAGS_FLAG])
    assert exc_info.value.code == 0
    assert "--repo-dir" in capsys.readouterr().out


# ── multi-value options the protocol cannot describe ───────────────────────


@pytest.mark.parametrize("nargs", ["?", "+", "*", 2, argparse.REMAINDER])
def test_value_taking_options_rejects_a_multi_value_option(nargs):
    """A flat option list cannot say how many tokens an option eats — so refuse."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--track", nargs=nargs)
    with pytest.raises(ValueError, match="--track declares nargs="):
        value_taking_options(parser)


def test_value_taking_options_accepts_an_explicit_nargs_of_one():
    """nargs=1 and the nargs=None default both mean exactly one token."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--reply", nargs=1)
    assert value_taking_options(parser) == ["--reply"]


def test_value_taking_options_still_ignores_multi_value_positionals():
    """claude-review's `args` positional is nargs='*' and must keep working."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--reply")
    parser.add_argument("args", nargs="*")
    assert value_taking_options(parser) == ["--reply"]


def test_handle_value_flags_reports_a_multi_value_option_on_stderr(capsys):
    """Loud, not silent: the probe names the flag it cannot describe and exits 2."""
    parser = argparse.ArgumentParser(prog="bad-delegate", add_help=False)
    parser.add_argument("--track", nargs="+")
    with pytest.raises(SystemExit) as exc_info:
        handle_value_flags(parser, [VALUE_FLAGS_FLAG])
    assert exc_info.value.code == 2

    captured = capsys.readouterr()
    assert captured.out == "", "a refusal must not look like an empty answer"
    assert captured.err.startswith(f"bad-delegate: {VALUE_FLAGS_FLAG}: ")
    assert "--track declares nargs='+'" in captured.err
    assert "_positional_index" in captured.err, "the message must name the fix"


def test_a_multi_value_option_does_not_break_normal_parsing(capsys):
    """The constraint is on the probe, not on the delegate's own CLI."""
    parser = argparse.ArgumentParser(prog="bad-delegate", add_help=False)
    parser.add_argument("--track", nargs="+")
    handle_value_flags(parser, ["--track", "a", "b"])
    assert parser.parse_args(["--track", "a", "b"]).track == ["a", "b"]
    assert capsys.readouterr().err == ""
