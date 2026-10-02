"""What `pr` tells a machine consumer about itself.

Three related documents, all of them derived rather than written down twice:

* the **tool schema** an MCP client reads to know what `pr` accepts;
* the **schema contracts** — which invocations serve a versioned document
  rather than a human table;
* the **per-subcommand schema**, which is a delegate's own parser answering
  for itself.

That last one is the part D2 specified and nothing built. `pr --tool-schema`
answers for the whole command and has no `output_schema`, because one of the
ten subcommands prints a `PRState` document and the other nine print prose
— declaring one schema for all ten made the MCP server reject the nine. So
the honest per-command contract is the delegate's, and `subcommand_schema`
is how a consumer asks for it.

A consumer that wants the union asks `pr --tool-schema`; one that wants to
know what `pr ci` returns asks for that subcommand by name. Neither is a
second declaration: the enum comes from `cli.registry.COMMANDS`, the output
schema from the delegate's own `ToolParser`, and a subcommand that grows a
flag says so in both without anyone editing this module.

`SCRIPT` is the literal `"pr"` rather than anything derived from `__file__`.
This module is `schema.py`, and a tool name taken from its own filename would
advertise the wrong command.
"""

# doc-group: cli

from __future__ import annotations

import sys

import core.log
import cli.review_modes
from cli.dispatch import PARSER_FACTORIES, resolve
from cli.registry import COMMANDS
from core.tool_parser import ToolParser

# The command this module describes. Not `Path(__file__).name`: the document
# names the binary a consumer runs, not the module that built it.
SCRIPT = "pr"

# argparse's own exit code for a usage error: the caller asked for something
# in terms this build cannot answer in. Distinct from 1, so a consumer can
# tell "we cannot answer that" from "the command ran and failed".
EXIT_USAGE = 2


def tool_schema() -> dict:
    """The MCP input schema for `pr` as a whole.

    No top-level ``output_schema``. One of the ten subcommands prints a
    ``PRState`` document, and not even that one before a state file exists,
    so declaring a schema for every invocation made the MCP server reject the
    other nine for printing no JSON object. `subcommand_schema` is where a
    consumer gets the honest per-command answer.

    The `command` enum is `COMMANDS` in declaration order, which is the same
    order `pr --help` lists and the registry's tuple fixes.
    """
    return {
        "name": SCRIPT,
        "description": "Unified PR lifecycle CLI — CI, review, comments, rebase",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "enum": list(COMMANDS),
                    "description": "Subcommand to run",
                },
                "repo_dir": {"type": "string", "description": "Git worktree directory", "x-context": True},
                "branch": {"type": "string", "description": "Branch name", "x-context": True},
                "pr": {"type": "string", "description": "PR number or URL", "x-context": True},
            },
            "required": ["command"],
        },
    }


def subcommand_schema(command: str) -> dict | None:
    """What `pr <command>` accepts and returns, or None if it cannot say.

    The delegate's own `ToolParser` answers, so `pr ci` reports `CIDomain`
    and `pr rebase` reports `RebaseSummary` with its two non-failing exit
    codes — the contracts those commands already declare and that
    `pr --tool-schema` cannot carry, because it answers for all ten at once.

    None has two causes, and they are the same answer to a consumer. Four
    commands `pr` runs itself have no delegate parser at all. Three more —
    `review`, `comments`, and `batch` — have one, but it is a plain
    `ArgumentParser`: those commands print prose, not a document, so there is
    no output schema to report and a `ToolParser` would advertise a contract
    they do not keep. Converting them to say nothing more loudly is not worth
    a wire format.

    Renamed from the document's own `name` so it reads as the invocation a
    user types. A consumer reading `"pr ci"` can run that string; reading
    `"ci-check"` it would have to know that `ai/bin/ci-check` exists, which
    is the coupling the shims are being deleted to remove.
    """
    factory = PARSER_FACTORIES.get(command)
    if factory is None:
        return None
    parser = resolve(factory)()
    if not isinstance(parser, ToolParser):
        return None
    schema = parser.build_schema()
    schema["name"] = f"{SCRIPT} {command}"
    return schema


def schema_contracts() -> list[str]:
    """Every invocation that serves a versioned document, as a user types it.

    Derived from the mode table so the error naming them cannot list an
    invocation that no longer serves one. `review` is the only command with
    modes today; a second one gets a branch here rather than a second table.
    """
    return [f"{SCRIPT} review {flag}" for flag, mode in cli.review_modes.MODES.items()
            if mode.schema_versions]


def served_schema_versions(command: str, argv: list[str]) -> tuple[int, ...]:
    """The row-schema versions *command* can serve for this argv, if any."""
    if command == "batch":
        return (1,)
    if command != "review":
        return ()
    modes = cli.review_modes.flags_given(argv)
    return cli.review_modes.MODES[modes[0]].schema_versions if modes else ()


def checked_schema_version(value: str, command: str, argv: list[str]) -> int:
    """The declared schema version, or exit naming what this build does serve.

    Checked on every call, so a consumer whose vendored version this build
    has dropped fails at the handshake rather than on the first field that
    moved under it. Never ignored: silently serving a human table to a caller
    that asked for a contract is the failure the whole change exists to
    remove.

    Exits rather than raising, which is what it did in the binary and what
    every other refusal on this path does. A library that exits is the
    ordinary case here — `review.posting`, `pr.context` and `gh.pr_reads` all
    do — and `publishing.call_entry_point` turns it back into a returncode
    for an in-process caller.
    """
    served = served_schema_versions(command, argv)
    if not served:
        core.log.error(f"{SCRIPT}: --schema-version is served by "
                  f"{', '.join(schema_contracts())}, not by this command")
        sys.exit(EXIT_USAGE)
    if value not in {str(v) for v in served}:
        core.log.error(f"{SCRIPT}: unsupported --schema-version {value!r} — "
                  f"this build serves {', '.join(str(v) for v in served)}")
        sys.exit(EXIT_USAGE)
    return int(value)
