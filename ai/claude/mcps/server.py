"""Dynamic MCP server for otto-workbench tools.

Discovers tools by scanning the workbench's own component script directories,
plus any the config adds, for scripts that support ``--tool-schema``. A
candidate is only executed if its source
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
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "lib"))

import workbench_paths  # noqa: E402

# MCP SDK imports are deferred to create_server() / main() so that the
# discovery and extraction utilities can be tested without the SDK installed.

logger = logging.getLogger("otto-mcp")

# Hand-authored, so it belongs to the config root rather than the state root.
CONFIG_PATH = workbench_paths.config_dir() / "mcp-tools.json"

# ai/claude/mcps/server.py — three levels down from the checkout root.
WORKBENCH_DIR = Path(__file__).resolve().parents[3]
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


# ── Tool Discovery ────────────────────────────────────────────────────────


def _load_config() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text())
    return {}


def _load_plugin_dir(plugin_file: Path) -> Path | None:
    """Read a plugin JSON file and return its tool directory, or None."""
    try:
        plugin = json.loads(plugin_file.read_text())
        td = Path(plugin["tool_dir"]).expanduser().resolve()
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


def discover_tool_dirs(root: Path | None = None) -> list[Path]:
    """Return the workbench's own script directories.

    A component keeps its scripts in ``<component>/bin`` and the root ``bin/``
    holds the workbench's own — so the directories are derived from the layout
    rather than listed. The glob is the two-level one ``lib/components.sh``
    uses for ``steps.sh`` and ``migrations``, plus the root, which means a new
    component tier such as ``editors/zed/bin`` is picked up without editing
    this file or hand-authoring config.

    These are always scanned: nothing in the workbench writes
    ``mcp-tools.json``, so a server that only looked at that file exposed no
    tools on any install. ``tool_dirs`` names directories to scan *in
    addition*.

    *root* defaults to the running checkout. ``bin/local/validate-tool-schema``
    passes one so its tests can point the same derivation at a fixture tree.
    """
    base = WORKBENCH_DIR if root is None else Path(root)
    dirs = {d for pattern in COMPONENT_BIN_GLOBS for d in base.glob(pattern) if d.is_dir()}
    return sorted(dirs)


def _resolve_dirs(config: dict) -> list[Path]:
    dirs = discover_tool_dirs()
    # Additive rather than an override: the workbench's own directories come
    # from its layout, so the config only says what *else* to scan.
    for d in config.get("tool_dirs", []):
        p = Path(d).expanduser().resolve()
        if p.is_dir():
            dirs.append(p)

    for d in config.get("plugin_dirs", []):
        dirs.extend(_scan_plugin_dir(Path(d).expanduser()))

    # Now that the config adds to the derived set rather than replacing it, a
    # path can arrive twice — a tool_dirs entry naming a component bin, or two
    # plugins pointing at one directory. Scanning it twice means probing every
    # script in it twice for a result the name check then discards. Config
    # paths are resolved above so a symlink or a `..` segment dedups too; the
    # derived ones already are.
    return list(dict.fromkeys(dirs))


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


def _scan_tool_dir(d: Path) -> list[dict]:
    """Return tool schemas from executable scripts in *d*.

    A script that carries a marker meant to be a tool, so every way it can then
    fail to answer is logged at warning level. Silence here reads as "no tool
    here" and leaves nothing to debug — the scan covers every component's
    ``bin/``, so the author of a broken tool is rarely the person reading these
    logs. Executables with no marker are not tools and stay quiet.
    """
    results = []
    for entry in tool_candidates(d):
        probed = probe_tool(entry)
        if probed.ok:
            results.append(probed.schema)
        else:
            logger.warning("Skipping %s: %s", entry, probed.reason)
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
