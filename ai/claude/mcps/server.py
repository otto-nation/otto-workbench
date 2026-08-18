"""Dynamic MCP server for otto-workbench tools.

Discovers tools by scanning configured directories for scripts that
support ``--tool-schema``. A candidate is only executed if its source
carries one of ``DECLARATION_MARKERS`` — probing runs the script, and
scripts that ignore unknown flags would do their real work instead of
answering. Any MCP client can connect via stdio transport.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import stat
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "lib"))

import workbench_paths  # noqa: E402

# MCP SDK imports are deferred to create_server() / main() so that the
# discovery and extraction utilities can be tested without the SDK installed.

logger = logging.getLogger("otto-mcp")

# Hand-authored, so it belongs to the config root rather than the state root.
CONFIG_PATH = workbench_paths.config_dir() / "mcp-tools.json"
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


# ── Tool Discovery ────────────────────────────────────────────────────────


def _load_config() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {}


def _load_plugin_dir(plugin_file: Path) -> Path | None:
    """Read a plugin JSON file and return its tool directory, or None."""
    try:
        plugin = json.loads(plugin_file.read_text())
        td = Path(plugin["tool_dir"]).expanduser()
        return td if td.is_dir() else None
    except (json.JSONDecodeError, KeyError, OSError):
        logger.warning("Skipping invalid plugin: %s", plugin_file)
        return None


def _scan_plugin_dir(plugin_dir: Path) -> list[Path]:
    """Return tool directories referenced by JSON files in *plugin_dir*."""
    if not plugin_dir.is_dir():
        return []
    results = []
    for entry in sorted(plugin_dir.iterdir()):
        if entry.suffix != ".json" or not entry.is_file():
            continue
        td = _load_plugin_dir(entry)
        if td is not None:
            results.append(td)
    return results


def _default_tool_dirs() -> list[str]:
    """Where to look when the config names no directories.

    Nothing writes ``mcp-tools.json``, so an absent ``tool_dirs`` is the
    ordinary case: without a default the server resolved to no directories and
    exposed no tools on any install.

    The default is the workbench's own script directory rather than the
    ``~/.local/bin`` those scripts are symlinked into. Both hold the same tools,
    but ``~/.local/bin`` also holds everything else a user has installed, and
    discovery probes by *executing* a candidate — narrowing the default keeps
    that off third-party binaries whose help text happens to carry a marker.
    Reaching them through ``~/.local/bin`` is a ``tool_dirs`` away.

    An explicit ``tool_dirs`` replaces this rather than extending it, so the
    key keeps the meaning a typed default will give it in #724.
    """
    return [str(Path(__file__).resolve().parent.parent / "bin")]


def _resolve_dirs(config: dict) -> list[Path]:
    dirs = []
    for d in config.get("tool_dirs", _default_tool_dirs()):
        p = Path(d).expanduser()
        if p.is_dir():
            dirs.append(p)

    for d in config.get("plugin_dirs", []):
        dirs.extend(_scan_plugin_dir(Path(d).expanduser()))
    return dirs


def _is_executable(path: Path) -> bool:
    try:
        return path.is_file() and (path.stat().st_mode & stat.S_IXUSR)
    except OSError:
        return False


def _declares_tool_schema(script: Path) -> bool:
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


def _probe_tool(script: Path) -> dict | None:
    """Run ``script --tool-schema`` and return the JSON, or None."""
    if not _declares_tool_schema(script):
        return None
    try:
        result = subprocess.run(
            [str(script), TOOL_SCHEMA_FLAG],
            capture_output=True,
            text=True,
            timeout=DISCOVERY_TIMEOUT,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        if result.returncode != 0:
            return None
        schema = json.loads(result.stdout)
        if any(key not in schema for key in REQUIRED_SCHEMA_KEYS):
            return None
        schema["_script"] = str(script)
        return schema
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as exc:
        logger.debug("Skipping %s: %s", script, exc)
        return None


def _scan_tool_dir(d: Path) -> list[dict]:
    """Return tool schemas from executable scripts in *d*."""
    try:
        entries = sorted(d.iterdir())
    except OSError as exc:
        logger.warning("Skipping inaccessible directory %s: %s", d, exc)
        return []
    candidates = [e for e in entries if _is_executable(e) and not e.name.startswith((".", "_"))]
    results = []
    for entry in candidates:
        schema = _probe_tool(entry)
        if schema is not None:
            results.append(schema)
    return results


def discover_tools(config: dict | None = None) -> dict[str, dict]:
    """Scan directories and return {tool_name: schema_dict}."""
    if config is None:
        config = _load_config()
    dirs = _resolve_dirs(config)
    tools: dict[str, dict] = {}

    all_schemas = [s for d in dirs for s in _scan_tool_dir(d)]
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

    async def handle_list_tools(params):
        tool_list = []
        for name, schema in tools.items():
            tool_list.append(Tool(
                name=name,
                description=schema.get("description", ""),
                inputSchema=schema.get("input_schema", {"type": "object", "properties": {}}),
                outputSchema=schema.get("output_schema"),
            ))
        return ListToolsResult(tools=tool_list)

    async def handle_call_tool(params):
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
