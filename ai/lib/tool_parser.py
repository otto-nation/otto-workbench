"""ToolParser — drop-in argparse replacement with self-description.

Scripts that use ToolParser automatically support ``--tool-schema``,
which emits a JSON document describing the tool's name, description,
input schema (derived from argparse actions), and output schema
(explicitly annotated).
"""

from __future__ import annotations

import dataclasses
import json
import sys
from argparse import SUPPRESS, ArgumentParser


# Args that are injected by the pr dispatcher / context system, not user-facing.
_CONTEXT_ARGS = frozenset({"repo_dir", "branch", "pr"})

# Args managed by the framework, not the tool.
_FRAMEWORK_ARGS = frozenset({"help", "tool_schema", "debug"})


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

    def __init__(self, *posargs, output_schema=None, **kwargs):
        super().__init__(*posargs, **kwargs)
        self._output_schema = output_schema
        self.add_argument("--tool-schema", action="store_true", help=SUPPRESS)

    def parse_args(self, args=None, namespace=None):
        if args is None:
            args = sys.argv[1:]

        if "--tool-schema" in args:
            json.dump(self._build_schema(), sys.stdout, indent=2)
            sys.stdout.write("\n")
            sys.exit(0)

        return super().parse_args(args, namespace)

    def _build_schema(self) -> dict:
        schema: dict = {
            "name": self.prog or "",
            "description": self.description or "",
            "input_schema": self._build_input_schema(),
        }
        if self._output_schema is not None:
            schema["output_schema"] = self._resolve_output_schema()
        return schema

    def _build_input_schema(self) -> dict:
        properties = {}
        required = []

        for action in self._actions:
            if action.dest in _FRAMEWORK_ARGS:
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
