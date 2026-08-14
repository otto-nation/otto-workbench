"""ToolParser — drop-in argparse replacement with self-description.

Scripts that use ToolParser automatically support ``--tool-schema``,
which emits a JSON document describing the tool's name, description,
input schema (derived from argparse actions), and output schema
(explicitly annotated).

MCP discovery only probes scripts whose source names ``ToolParser`` or
``--tool-schema`` (see ``ai/claude/mcps/server.py``). A tool that implements
the protocol some other way will not be discovered.

This module also provides ``handle_value_flags``, a lighter probe that answers
which of a parser's options take a value.  ToolParser scripts inherit it;
plain-``argparse`` scripts opt in with one call.  See its docstring for why the
arity question is not answered out of ``--tool-schema``.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from argparse import SUPPRESS, ArgumentParser


# Args that are injected by the pr dispatcher / context system, not user-facing.
_CONTEXT_ARGS = frozenset({"repo_dir", "branch", "pr"})

# Args managed by the framework, not the tool.
# "debug" is registered by add_trail_args() in the pr dispatcher, not by ToolParser.
_FRAMEWORK_ARGS = frozenset({"help", "tool_schema", "debug"})

# Hidden probe asking a script which of its options consume a following value.
VALUE_FLAGS_FLAG = "--value-flags"

# The nargs values that mean "this option consumes exactly one token", which is
# the only arity the --value-flags answer can express.  argparse spells the
# default two ways: None (plain store) and a literal 1.
_SINGLE_VALUE_NARGS = (None, 1)


class ToolParser(ArgumentParser):
    """ArgumentParser subclass that supports --tool-schema introspection.

    Usage::

        parser = ToolParser(
            prog="pr-rebase",
            description="Rebase branch onto origin/main",
            output_schema=RebaseSummary,  # dataclass or dict
        )
        parser.add_argument("--fix", action="store_true", help="Auto-resolve conflicts")
        args = parser.parse_args()
    """

    def __init__(self, *posargs, output_schema=None, ok_exit_codes=None, **kwargs):
        super().__init__(*posargs, **kwargs)
        self._output_schema = output_schema
        self._ok_exit_codes = ok_exit_codes or []
        self.add_argument("--tool-schema", action="store_true", help=SUPPRESS)

    def parse_args(self, args=None, namespace=None):
        if args is None:
            args = sys.argv[1:]

        if "--tool-schema" in args:
            json.dump(self._build_schema(), sys.stdout, indent=2)
            sys.stdout.write("\n")
            sys.exit(0)

        handle_value_flags(self, args)

        return super().parse_args(args, namespace)

    def _build_schema(self) -> dict:
        schema: dict = {
            "name": self.prog or "",
            "description": self.description or "",
            "input_schema": self._build_input_schema(),
        }
        if self._output_schema is not None:
            schema["output_schema"] = self._resolve_output_schema()
        if self._ok_exit_codes:
            schema["ok_exit_codes"] = self._ok_exit_codes
        return schema

    def _build_input_schema(self) -> dict:
        properties = {}
        required = []

        for action in self._actions:
            if action.dest in _FRAMEWORK_ARGS:
                continue
            if action.help is SUPPRESS:
                continue

            prop = _action_to_property(action)
            if prop is None:
                continue

            name = action.dest
            is_context = name in _CONTEXT_ARGS
            prop_entry = prop.copy()
            if is_context:
                prop_entry["x-context"] = True

            properties[name] = prop_entry

            if action.required and not is_context:
                required.append(name)

        schema: dict = {"type": "object", "properties": properties}
        if required:
            schema["required"] = required
        return schema

    def _resolve_output_schema(self) -> dict:
        if isinstance(self._output_schema, dict):
            return self._output_schema

        if dataclasses.is_dataclass(self._output_schema):
            from schema_gen import dataclass_to_schema
            return dataclass_to_schema(self._output_schema)

        raise TypeError(
            f"output_schema must be a dict or dataclass, got {type(self._output_schema)}"
        )


def value_taking_options(parser: ArgumentParser) -> list[str]:
    """Every option string of *parser* that consumes a following value.

    Aliases are listed individually — ``--repo-dir`` and ``--worktree`` are
    two answers to the same question, and a caller matching raw argv tokens
    needs both.  Store-true/count/help actions declare ``nargs=0`` and are
    excluded; everything else (``store``, ``append``, typed options) takes one.

    The answer is a flat set of option strings, so it can only say "this option
    eats the next token" — it has no way to say how many.  An option declared
    ``nargs='?'``, ``'+'``, ``'*'``, ``REMAINDER`` or an int above 1 therefore
    raises ``ValueError`` instead of being reported as single-valued, which
    would make the caller skip the wrong number of tokens and silently
    misclassify the one after them.  Positionals are exempt because they never
    appear in the answer — ``claude-review`` declares ``args`` with ``nargs='*'``.
    """
    options = set()
    for action in parser._actions:
        if not action.option_strings or action.nargs == 0:
            continue
        if action.nargs not in _SINGLE_VALUE_NARGS:
            raise ValueError(
                f"{'/'.join(action.option_strings)} declares nargs={action.nargs!r}, "
                f"but {VALUE_FLAGS_FLAG} can only describe options that consume exactly "
                "one value (nargs=None or 1). Callers skip a fixed one token after such "
                "an option, so answering for this one would misclassify the next. Give "
                "the option a single value, or teach both this function and "
                "_positional_index in ai/claude/bin/pr to carry a count."
            )
        options.update(action.option_strings)
    return sorted(options)


def handle_value_flags(parser: ArgumentParser, args=None) -> None:
    """Answer the ``--value-flags`` probe, printing one option per line.

    A wrapper CLI that classifies a bare positional (``ai/claude/bin/pr``)
    cannot tell a target from a flag's value without knowing the delegate's
    arity.  The delegate's own parser is the single source of truth for that,
    so the wrapper asks rather than mirroring a list that would rot.

    This is deliberately separate from ``--tool-schema``: that document is
    keyed by ``dest``, drops ``help=SUPPRESS`` actions, and loses option
    aliases, so arity cannot be recovered from it faithfully.  Declaring
    ``--tool-schema`` also enrolls a script in MCP tool discovery
    (``ai/claude/mcps/server.py``), which is not a side effect an arity probe
    should carry.

    A parser this protocol cannot describe (see ``value_taking_options``) is
    reported on stderr and exits 2 rather than raising: the probe runs as a
    subprocess, so a traceback would reach nobody, while a one-line diagnosis
    is reprinted by the caller that captured it.
    """
    if VALUE_FLAGS_FLAG not in (sys.argv[1:] if args is None else args):
        return
    try:
        options = value_taking_options(parser)
    except ValueError as exc:
        sys.stderr.write(f"{parser.prog}: {VALUE_FLAGS_FLAG}: {exc}\n")
        sys.exit(2)
    sys.stdout.write("".join(f"{opt}\n" for opt in options))
    sys.exit(0)


def _action_to_property(action) -> dict | None:
    """Convert an argparse action to a JSON Schema property."""
    # Skip positional args that are just subcommand names
    if not action.option_strings:
        if action.dest == "args":
            return None
        return {"type": "string", "description": action.help or ""}

    if action.const is True and action.default is False:
        prop: dict = {"type": "boolean", "default": False}
    elif action.const is False and action.default is True:
        prop = {"type": "boolean", "default": True}
    elif action.type is int:
        prop = {"type": "integer"}
        if action.default is not None:
            prop["default"] = action.default
    elif action.type is float:
        prop = {"type": "number"}
        if action.default is not None:
            prop["default"] = action.default
    elif action.choices:
        prop = {"type": "string", "enum": list(action.choices)}
        if action.default is not None:
            prop["default"] = action.default
    else:
        prop = {"type": "string"}
        if action.default is not None:
            prop["default"] = action.default

    if action.help and action.help != SUPPRESS:
        prop["description"] = action.help

    return prop
