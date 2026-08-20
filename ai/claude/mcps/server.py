"""Dynamic MCP server for otto-workbench tools.

Discovers tools by scanning the workbench's own component script directories
for scripts that support ``--tool-schema``. A candidate is only executed if its
source carries one of ``DECLARATION_MARKERS`` — probing runs the script, and
scripts that ignore unknown flags would do their real work instead of
answering. Any MCP client can connect via stdio transport.

The directories come from the component layout and nothing else. There is no
configuration file: the server exposes the workbench's own tools, so what to
scan is a fact about the checkout rather than a question to ask the user.

Which of them a client is offered comes from the registries — see
``ai/lib/tool_registry.py``. Carrying the marker makes a script probeable, not
public: a hidden or unregistered one is skipped before it is ever run.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

# MCP SDK imports are deferred to create_server() / main() so that the
# discovery and extraction utilities can be tested without the SDK installed.

logger = logging.getLogger("otto-mcp")

# ai/claude/mcps/server.py — three levels down from the checkout root.
WORKBENCH_DIR = Path(__file__).resolve().parents[3]

# A client spawns this file by path, with no package around it and its own
# project as the working directory, so the workbench's Python has to be named
# before it can be imported.
sys.path.insert(0, str(WORKBENCH_DIR / "ai" / "lib"))
from tool_registry import RegistryEntry, load_registry_entries  # noqa: E402

COMPONENT_BIN_GLOBS = ("bin", "*/bin", "*/*/bin")

# ceiling: candidates are probed one at a time, so the worst case is this
# timeout times the number of marker-bearing scripts. Four of them today, well
# under a second — probe concurrently if a component ever adds enough that
# server startup becomes noticeable.
DISCOVERY_TIMEOUT = 2.0
TOOL_SCHEMA_FLAG = "--tool-schema"

# Keys every tool-schema document must carry. bin/local/validate-skills asserts
# the same pair against declared output_schema tools.
REQUIRED_SCHEMA_KEYS = ("name", "input_schema")

# The two ways a script can implement the protocol: parse the flag itself, or
# inherit it from ai/lib/tool_parser.py's ToolParser. A prose mention of the flag
# also matches — the scan is a cheap filter, not a guarantee, which is why tools
# that take positional arguments must reject unknown flags on their own.
DECLARATION_MARKERS = (TOOL_SCHEMA_FLAG.encode(), b"ToolParser")

# Bytes of a candidate read when looking for a marker. Scripts declare the
# protocol in their imports or argument parsing, well inside this bound.
# ceiling: a compiled binary carrying a marker past this offset is skipped —
# raise the cap if a tool dir ever holds one.
DECLARATION_SCAN_BYTES = 256 * 1024

# How much of a tool's output an error message quotes back. Enough to recognise
# a usage line or a stack trace, short enough not to bury the sentence above it.
ERROR_EXCERPT_CHARS = 500


# ── Tool Discovery ────────────────────────────────────────────────────────


def discover_tool_dirs(root: Path | None = None) -> list[Path]:
    """Return the workbench's own script directories.

    A component keeps its scripts in ``<component>/bin`` and the root ``bin/``
    holds the workbench's own — so the directories are derived from the layout
    rather than listed. The glob is the two-level one ``lib/components.sh``
    uses for ``steps.sh`` and ``migrations``, plus the root, which means a new
    component tier such as ``editors/zed/bin`` is picked up without editing
    this file or hand-authoring config.

    This derivation is the whole of where the server looks. An earlier design
    read the directories from ``~/.config/workbench/mcp-tools.json``, which no
    setup step, migration or shipped default ever wrote — so discovery resolved
    to nothing and every install ran a registered server exposing zero tools.
    The layout is the answer because the server hosts the workbench's own
    tools; a directory outside the checkout has no tools of this kind in it.

    *root* defaults to the running checkout. ``bin/local/validate-tool-schema``
    passes one so its tests can point the same derivation at a fixture tree.
    """
    base = WORKBENCH_DIR if root is None else Path(root)
    dirs = {d for pattern in COMPONENT_BIN_GLOBS for d in base.glob(pattern) if d.is_dir()}
    return sorted(dirs)


def _is_executable(path: Path) -> bool:
    try:
        return path.is_file() and (path.stat().st_mode & stat.S_IXUSR)
    except OSError:
        return False


def declares_tool_schema(script: Path) -> bool:
    """True if *script* carries a protocol marker in its source.

    Probing means executing, and a script that ignores unknown flags runs its
    default action instead of answering — ``build-otto-ai-tools-tarball`` read
    the flag as a version string and wrote a release archive into the CWD.
    Reading the source first limits execution to scripts that could respond.
    """
    try:
        with script.open("rb") as f:
            head = f.read(DECLARATION_SCAN_BYTES)
    except OSError as exc:
        logger.debug("Cannot read %s: %s", script, exc)
        return False
    return any(marker in head for marker in DECLARATION_MARKERS)


@dataclass(frozen=True)
class ProbeResult:
    """What ``script --tool-schema`` answered, or why it did not.

    The reason travels with the result rather than going straight to the log,
    so a caller that is not the server — ``bin/local/validate-tool-schema`` —
    can report the same failure to whoever broke the script.
    """

    script: Path
    schema: dict | None = None
    reason: str | None = None

    @property
    def ok(self) -> bool:
        return self.schema is not None


def probe_tool(script: Path) -> ProbeResult:
    """Run ``script --tool-schema`` and return its schema or a failure reason.

    The marker check is re-applied here rather than left to the caller.
    ``tool_candidates`` already filters, but probing is execution: a script that
    ignores unknown flags does its real work instead of answering, and
    ``build-otto-ai-tools-tarball`` wrote a release archive into the CWD that
    way. The invariant travels with the function that would break it.
    """
    if not declares_tool_schema(script):
        return ProbeResult(script, reason="no protocol marker in its source")
    try:
        result = subprocess.run(
            [str(script), TOOL_SCHEMA_FLAG],
            capture_output=True,
            text=True,
            timeout=DISCOVERY_TIMEOUT,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        if result.returncode != 0:
            return ProbeResult(script, reason=(
                f"{TOOL_SCHEMA_FLAG} exited {result.returncode}: "
                f"{result.stderr.strip() or '(no stderr)'}"
            ))
        schema = json.loads(result.stdout)
        missing = [key for key in REQUIRED_SCHEMA_KEYS if key not in schema]
        if missing:
            return ProbeResult(script, reason=f"schema is missing {', '.join(missing)}")
        schema["_script"] = str(script)
        return ProbeResult(script, schema=schema)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as exc:
        # Name the exception type rather than trusting its str() to say which
        # of the three it was — docs/tools.md tells readers these are distinct.
        return ProbeResult(script, reason=f"{type(exc).__name__}: {exc}")


def tool_candidates(d: Path) -> list[Path]:
    """Return the scripts in *d* that discovery would probe.

    Executable, not hidden or underscore-prefixed, and carrying a protocol
    marker. This is the whole of "what is a tool here" up to running it, so the
    validator asks this rather than restating the filter.
    """
    try:
        entries = sorted(d.iterdir())
    except OSError as exc:
        logger.warning("Skipping inaccessible directory %s: %s", d, exc)
        return []
    return [e for e in entries
            if _is_executable(e)
            and not e.name.startswith((".", "_"))
            and declares_tool_schema(e)]


def offered_candidates(d: Path, registry: dict[Path, RegistryEntry]) -> dict[Path, RegistryEntry]:
    """The scripts in *d* a client may be offered, each with its registry entry.

    The filter runs before ``probe_tool``, so a hidden or unregistered script is
    never executed. Reversing the two would run every marker-bearing script on
    every startup to build a list most of them are then dropped from.

    A candidate no registry names is a warning: carrying the marker says it
    meant to be a tool, and the entry that would offer it is one stanza in the
    ``registry.yml`` whose ``meta.source`` is this directory. Being hidden is a
    decision somebody already made, so it is only worth a debug line.
    """
    offers: dict[Path, RegistryEntry] = {}
    for script in tool_candidates(d):
        entry = registry.get(script.resolve())
        if entry is None:
            logger.warning("Skipping %s: it declares %s but no registry entry names it",
                           script, TOOL_SCHEMA_FLAG)
        elif entry.offered:
            offers[script] = entry
        else:
            logger.debug("Not offering %s: registry visibility is %s",
                         script, entry.visibility.value)
    return offers


def _described(schema: dict, entry: RegistryEntry) -> dict:
    """*schema* with the registry's description in place of the script's.

    The registries own tool documentation, so the description a client reads is
    the one a reader of the rules gets — plus the ``when_to_use`` and ``usage``
    lines a ``full`` entry carries. A script's own line is written for its
    ``--help`` and has already drifted shorter: ``pr`` answers the probe with
    "Unified PR lifecycle CLI — CI, review, comments, rebase" and says nothing
    about when to reach for it.
    """
    return {**schema, "description": entry.tool_description}


def _scan_tool_dir(d: Path, registry: dict[Path, RegistryEntry]) -> list[dict]:
    """Return tool schemas from the offered scripts in *d*.

    A script that carries a marker meant to be a tool, so every way it can then
    fail to answer is logged at warning level. Silence here reads as "no tool
    here" and leaves nothing to debug — the scan covers every component's
    ``bin/``, so the author of a broken tool is rarely the person reading these
    logs. Executables with no marker are not tools and stay quiet.
    """
    results = []
    for script, entry in offered_candidates(d, registry).items():
        probed = probe_tool(script)
        if probed.ok:
            results.append(_described(probed.schema, entry))
        else:
            logger.warning("Skipping %s: %s", script, probed.reason)
    return results


def discover_tools(dirs: list[Path] | None = None,
                   registry: dict[Path, RegistryEntry] | None = None) -> dict[str, dict]:
    """Scan directories and return {tool_name: schema_dict}.

    *dirs* defaults to the derived set and *registry* to the running checkout's
    entries. Passing them is how a test points the scan at a fixture directory
    without writing scripts into the checkout; an empty *registry* offers
    nothing, which is what an unregistered tree amounts to.
    """
    if dirs is None:
        dirs = discover_tool_dirs()
    if registry is None:
        registry = load_registry_entries(WORKBENCH_DIR)
    tools: dict[str, dict] = {}

    all_schemas = [s for d in dirs for s in _scan_tool_dir(d, registry)]
    for schema in all_schemas:
        if schema["name"] not in tools:
            tools[schema["name"]] = schema
            logger.info("Discovered tool: %s (%s)", schema["name"], schema["_script"])

    return tools


# ── Argument Mapping ──────────────────────────────────────────────────────


def _args_to_cli(arguments: dict, input_schema: dict) -> list[str]:
    """Convert MCP tool arguments to CLI flags."""
    cli_args = []
    props = input_schema.get("properties", {})

    for key, value in arguments.items():
        if value is None:
            continue
        flag = f"--{key.replace('_', '-')}"
        prop_type = props.get(key, {}).get("type", "string")

        if prop_type == "boolean" and value:
            cli_args.append(flag)
        elif prop_type != "boolean":
            cli_args.extend([flag, str(value)])

    return cli_args


# ── JSON Extraction ───────────────────────────────────────────────────────


def _extract_json(text: str) -> str | None:
    """Extract JSON from mixed output (dashboard on stderr bleeds into stdout)."""
    text = text.strip()
    if not text:
        return None

    # Try the whole thing first
    if text.startswith("{") or text.startswith("["):
        try:
            json.loads(text)
            return text
        except json.JSONDecodeError:
            pass

    # Find the first line starting with { or [ that begins valid JSON
    lines = text.split("\n")
    candidates = [i for i, line in enumerate(lines) if line.strip()[:1] in ("{", "[")]
    for i in candidates:
        remainder = "\n".join(lines[i:])
        try:
            json.loads(remainder)
            return remainder
        except json.JSONDecodeError:
            continue

    return None


def _first_chars(text: str) -> str:
    """The head of *text*, for quoting a tool's output back inside an error."""
    if not text:
        return "(no output)"
    if len(text) <= ERROR_EXCERPT_CHARS:
        return text
    return text[:ERROR_EXCERPT_CHARS] + "…"


# ── MCP Server ────────────────────────────────────────────────────────────


def create_server():
    """Create the MCP server and discover tools.

    Returns (server, tools_dict). Requires the ``mcp`` package.
    """
    from mcp.server.lowlevel import Server
    from mcp.types import (
        CallToolRequestParams,
        CallToolResult,
        ListToolsResult,
        PaginatedRequestParams,
        TextContent,
        Tool,
    )

    tools = discover_tools()
    server = Server("otto-workbench")

    # Both handlers take (ctx, params): the SDK calls them with the request
    # context first, and a handler that omits it raises TypeError inside the
    # runner, which reaches the client as an internal error with no tools and
    # no call ever succeeding.
    async def handle_list_tools(ctx, params):
        tool_list = []
        for name, schema in tools.items():
            tool_list.append(Tool(
                name=name,
                description=schema.get("description", ""),
                inputSchema=schema.get("input_schema", {"type": "object", "properties": {}}),
                outputSchema=schema.get("output_schema"),
            ))
        return ListToolsResult(tools=tool_list)

    async def handle_call_tool(ctx, params):
        name = params.name
        arguments = params.arguments or {}

        if name not in tools:
            return CallToolResult(
                content=[TextContent(type="text", text=f"Unknown tool: {name}")],
                isError=True,
            )

        schema = tools[name]
        script = schema["_script"]
        cli_args = _args_to_cli(arguments, schema.get("input_schema", {}))

        try:
            result = await asyncio.to_thread(
                subprocess.run,
                [script] + cli_args,
                capture_output=True,
                text=True,
                timeout=300,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
        except subprocess.TimeoutExpired:
            return CallToolResult(
                content=[TextContent(type="text", text="Tool execution timed out (300s)")],
                isError=True,
            )

        ok_codes = {0} | set(schema.get("ok_exit_codes", []))
        if result.returncode not in ok_codes:
            error_text = result.stderr.strip() or f"Exit code {result.returncode}"
            return CallToolResult(
                content=[TextContent(type="text", text=error_text)],
                isError=True,
            )

        json_output = _extract_json(result.stdout)
        parsed = json.loads(json_output) if json_output else None

        if schema.get("output_schema") is not None:
            # A client validates the answer against the schema tools/list
            # advertised and raises before the caller sees any of it, so a tool
            # that declares one has to answer with structured content or with
            # an error that names itself.
            if not isinstance(parsed, dict):
                return CallToolResult(
                    content=[TextContent(type="text", text=(
                        f"{name} declares an output schema but printed no JSON object: "
                        f"{_first_chars(result.stdout.strip() or result.stderr.strip())}"
                    ))],
                    isError=True,
                )
            return CallToolResult(
                content=[TextContent(type="text", text=json_output)],
                structuredContent=parsed,
            )

        if json_output:
            return CallToolResult(
                content=[TextContent(type="text", text=json_output)],
            )

        output = result.stdout.strip() or result.stderr.strip()
        return CallToolResult(
            content=[TextContent(type="text", text=output or "(no output)")],
        )

    server.add_request_handler("tools/list", PaginatedRequestParams, handle_list_tools)
    server.add_request_handler("tools/call", CallToolRequestParams, handle_call_tool)

    return server, tools


# ── Entry Point ───────────────────────────────────────────────────────────


async def main():
    from mcp.server.stdio import stdio_server
    from mcp.types import ServerCapabilities, ToolsCapability

    server, tools = create_server()
    logger.info("Starting otto-workbench MCP server with %d tools", len(tools))

    init_options = server.create_initialization_options(
        notification_options=None,
        experimental_capabilities=None,
    )
    if init_options.capabilities is None:
        init_options.capabilities = ServerCapabilities()
    init_options.capabilities.tools = ToolsCapability()

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, init_options)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    asyncio.run(main())
