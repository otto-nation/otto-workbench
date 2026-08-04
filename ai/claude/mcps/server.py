"""Dynamic MCP server for otto-workbench tools.

Discovers tools by scanning configured directories for scripts that
support ``--tool-schema``. Any MCP client can connect via stdio transport.
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


# MCP SDK imports are deferred to create_server() / main() so that the
# discovery and extraction utilities can be tested without the SDK installed.

logger = logging.getLogger("otto-mcp")

CONFIG_PATH = Path("~/.config/workbench/mcp-tools.json").expanduser()
DISCOVERY_TIMEOUT = 2.0


# ── Tool Discovery ────────────────────────────────────────────────────────


def _load_config() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {}


def _resolve_dirs(config: dict) -> list[Path]:
    dirs = []
    for d in config.get("tool_dirs", []):
        p = Path(d).expanduser()
        if p.is_dir():
            dirs.append(p)
    for d in config.get("plugin_dirs", []):
        p = Path(d).expanduser()
        if p.is_dir():
            for entry in sorted(p.iterdir()):
                if entry.suffix == ".json" and entry.is_file():
                    try:
                        plugin = json.loads(entry.read_text())
                        td = Path(plugin["tool_dir"]).expanduser()
                        if td.is_dir():
                            dirs.append(td)
                    except (json.JSONDecodeError, KeyError):
                        logger.warning("Skipping invalid plugin: %s", entry)
    return dirs


def _is_executable(path: Path) -> bool:
    try:
        return path.is_file() and (path.stat().st_mode & stat.S_IXUSR)
    except OSError:
        return False


def _probe_tool(script: Path) -> dict | None:
    """Run ``script --tool-schema`` and return the JSON, or None."""
    try:
        result = subprocess.run(
            [str(script), "--tool-schema"],
            capture_output=True,
            text=True,
            timeout=DISCOVERY_TIMEOUT,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        if result.returncode != 0:
            return None
        schema = json.loads(result.stdout)
        if "name" not in schema or "input_schema" not in schema:
            return None
        schema["_script"] = str(script)
        return schema
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as exc:
        logger.debug("Skipping %s: %s", script, exc)
        return None


def discover_tools(config: dict | None = None) -> dict[str, dict]:
    """Scan directories and return {tool_name: schema_dict}."""
    if config is None:
        config = _load_config()
    dirs = _resolve_dirs(config)
    tools: dict[str, dict] = {}

    for d in dirs:
        try:
            entries = sorted(d.iterdir())
        except OSError as exc:
            logger.warning("Skipping inaccessible directory %s: %s", d, exc)
            continue
        for entry in entries:
            if not _is_executable(entry):
                continue
            if entry.name.startswith(".") or entry.name.startswith("_"):
                continue
            schema = _probe_tool(entry)
            if schema and schema["name"] not in tools:
                tools[schema["name"]] = schema
                logger.info("Discovered tool: %s (%s)", schema["name"], entry)

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

        if prop_type == "boolean":
            if value:
                cli_args.append(flag)
        else:
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

    # Find the first line starting with { or [
    lines = text.split("\n")
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("{") or stripped.startswith("["):
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
