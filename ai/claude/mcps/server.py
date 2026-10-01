"""Dynamic MCP server for otto-workbench tools.

Tools are read from the component registries — see
``ai/lib/config/tool_registry.py``. An entry declares the tool, ``visibility``
decides whether a client sees it, and the schema is built by importing it.
Any MCP client can connect via stdio transport.

Discovery used to be a scan: glob nine ``bin`` directories, read the first
256 KiB of every executable looking for a ``--tool-schema`` marker, then
spawn each match under a timeout in an eight-worker pool and parse what it
printed. Nine directories, four probe subprocesses at startup, to learn one
thing — that ``pr`` is a tool and what it accepts. That is the last of the
three cross-process introspection protocols #909 exists to remove, and it is
gone: the offered set is a registry read and the schema is an import.

There is no configuration file. The server exposes the workbench's own
tools, so what is offered is a fact about the checkout rather than a
question to ask the user.

The client owns this process, spawning it over stdio, so nothing outside can
restart it when a tool is added or re-signatured. A poll watches what discovery
reads and re-runs it when that changes, and the client is told with
``notifications/tools/list_changed`` when the tool set differs as a result.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

# MCP SDK imports are deferred to create_server() / main() so that the
# discovery and extraction utilities can be tested without the SDK installed.

logger = logging.getLogger("otto-mcp")

# ai/claude/mcps/server.py — three levels down from the checkout root.
WORKBENCH_DIR = Path(__file__).resolve().parents[3]

# A client spawns this file by path, with no package around it and its own
# project as the working directory, so the workbench's Python has to be named
# before it can be imported.
sys.path.insert(0, str(WORKBENCH_DIR / "ai" / "lib"))
import core.proc  # noqa: E402
import core.timeouts  # noqa: E402
from config.tool_registry import RegistryEntry, load_registry_entries, registry_files  # noqa: E402

# Seconds a tool call gets before the client is told it timed out. Not a tier
# from `timeouts`: those bound a subprocess that should already have answered,
# while this is a budget for whichever tool the client asked for — `pr review`
# drives agents for minutes. Same carve-out as `eval.task.EVAL_CASE_BUDGET`.
TOOL_CALL_BUDGET = 300

# How much of a tool's output an error message quotes back. Enough to recognise
# a usage line or a stack trace, short enough not to bury the sentence above it.
ERROR_EXCERPT_CHARS = 500

# Seconds between fingerprints of what discovery reads. A poll that finds
# nothing costs one stat per file in the scanned directories and nothing else,
# so this is a bound on how stale a client's tool list gets rather than a cost
# to trade against. Re-discovery only runs when the fingerprint moves.
POLL_INTERVAL = 2.0


# ── Spawning ──────────────────────────────────────────────────────────────


def _run_script(argv: list[str], timeout: float) -> core.proc.CmdResult:
    """Run *argv* to completion under *timeout*, isolated from this process.

    Every script this server executes goes through here — the discovery probe
    and the tool call alike — for the two guarantees ``proc.run`` gives a
    caller that asks for them.

    Its stdin is closed. This server's stdin *is* the stdio JSON-RPC stream the
    client writes requests into, so a script that reads a single byte takes
    that byte out of the transport and the session dies on a parse error naming
    no tool. The probe is the likeliest reader: a script that does not
    recognise ``--tool-schema`` falls through to its real work.

    ``kill_process_group`` is what an expired bound needs here. A tool spawns
    agents — ``pr review`` is the case — and signalling only the direct child
    leaves them running against the account with nothing holding a handle to
    them. The probe asks for it on the same grounds: a script that fell through
    to its real work is running something nobody planned for.

    A timeout comes back as ``proc.TIMEOUT_RETURNCODE`` rather than an
    exception, so both callers check for it before reading the exit code as the
    script's own. Nothing in the workbench exits 124 deliberately —
    ``bin/local/validate-magic-values`` holds that code's monopoly under
    ``ai/`` — so the two cannot be confused in practice.
    """
    return core.proc.run(argv, timeout=timeout, kill_process_group=True,
                    env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})


# ── Tool Discovery ────────────────────────────────────────────────────────


def _tool_schema(script: Path) -> dict | None:
    """The schema *script* declares, read in this process.

    `pr` is the one tool the workbench offers, and the document it serves is
    now a function in `ai/lib` rather than a string only the binary can
    print. Reading it directly is what removed the last of #909's three
    cross-process introspection protocols: discovery used to glob nine
    directories, byte-grep each executable for a marker, and spawn every
    match with `--tool-schema` under a timeout, to learn something an import
    answers.

    None for a registered script this server has no schema for. Most
    registered scripts are not tools — `wt`, `otto-log`, the scans — and a
    registry entry is not a claim to be one. Returning None rather than
    raising keeps a component's registry free to list whatever it documents.
    """
    if script.name != "pr":
        return None
    from cli.schema import tool_schema
    schema = dict(tool_schema())
    schema["_script"] = str(script)
    return schema


def _described(schema: dict, entry: RegistryEntry) -> dict:
    """The schema a client sees, described in the registry's words.

    The registry is where a tool's description is maintained and where the
    `when_to_use` and `usage` a caller actually needs are written, so it wins
    over whatever description the schema carries. Everything else in the
    schema — `input_schema`, `output_schema`, `ok_exit_codes` — passes through
    unchanged; only the one key is registry-driven.
    """
    return {**schema, "description": entry.tool_description}


def discover_tools(registry: dict[Path, RegistryEntry] | None = None) -> dict[str, dict]:
    """Every offered tool, as {tool_name: schema_dict}.

    Read from the registry rather than found by scanning: an entry declares
    the tool, `visibility` decides whether a client sees it, and the schema
    comes from an import. Nothing is globbed, byte-grepped or spawned.

    *registry* defaults to the running checkout's entries. Passing it is how
    a test points discovery at a fixture without writing into the checkout;
    an empty one offers nothing, which is what an unregistered tree amounts
    to.

    A name two entries both answer to keeps the first and logs the other.
    `bin/local/validate-registries` is where a collision is meant to be
    caught — raising here would run in the watcher thread as well as at
    startup, so one ambiguity would either take the server down or stop
    re-discovery for the session.
    """
    if registry is None:
        registry = load_registry_entries(WORKBENCH_DIR)

    tools: dict[str, dict] = {}
    for script, entry in sorted(registry.items()):
        if not entry.offered:
            continue
        schema = _tool_schema(script)
        if schema is None:
            continue
        name = schema["name"]
        if name in tools:
            logger.error(
                "Duplicate tool name %s: keeping %s, ignoring %s — which one a "
                "client reaches is decided by registry order, so rename one",
                name, tools[name]["_script"], schema["_script"])
            continue
        tools[name] = _described(schema, entry)
        logger.info("Discovered tool: %s (%s)", name, script)

    return tools


def _stamp(path: Path) -> tuple | None:
    """What a file would have to change for discovery to answer differently."""
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size, st.st_mode)


def discovery_fingerprint(root: Path | None = None) -> tuple:
    """A comparable value over every input ``discover_tools`` reads.

    Two inputs now, where a scan of nine directories used to be the whole of
    it: the registry files, which decide what is offered and how it is
    described, and the modules the schema is built from.

    Narrowing this was not an optimisation. The old fingerprint stamped every
    file in every scanned directory because the marker-grep that decided
    candidacy was too expensive to re-run on a poll — a proxy for the answer
    rather than the inputs to it. Reading the registry has no such cost, so
    the fingerprint can name exactly what discovery reads, which is what
    closes the case it used to miss: a schema that changed without any file
    in a `bin/` directory being touched.
    """
    base = WORKBENCH_DIR if root is None else Path(root)
    lib = base / "ai" / "lib" / "cli"
    watched = [*registry_files(base), lib / "schema.py", lib / "registry.py"]
    return tuple(sorted((str(path), _stamp(path)) for path in watched))


@dataclass(frozen=True)
class Discovery:
    """A tool set and the fingerprint of the tree it was read from."""

    tools: dict[str, dict]
    fingerprint: tuple


def discover_with_baseline() -> Discovery:
    """Scan for tools and stamp the tree they came from.

    The stamp is taken before the scan, not after. A baseline has to describe a
    tree no newer than the tool set it is the baseline for: taken afterwards, a
    tool that landed while discovery was running is already in the fingerprint,
    so no poll ever sees that file appear and the client goes without the tool
    for the life of the session — the process is spawned by the client, so
    nothing outside can restart it either. Taken first, the same tool costs one
    re-scan on the first poll and is then offered.
    """
    fingerprint = discovery_fingerprint()
    return Discovery(tools=discover_tools(), fingerprint=fingerprint)


def _why_gone(script: Path) -> str:
    """Why a tool that used to answer no longer does.

    Two answers now, where the probe used to supply a third. A tool was
    withdrawn because its script went away or because its registry entry
    stopped offering it — those are the only inputs left, so the reason can
    be stated rather than re-derived by running the thing.

    The probe's third answer was "it still exists and is still offered but
    has stopped answering correctly", which a registry read cannot produce:
    a schema that fails to build raises out of `discover_tools` rather than
    quietly dropping one tool. That is the better failure — it happens once,
    at the scan, instead of once per tool at the point somebody notices.
    """
    if not script.exists():
        return "its script is gone"
    return "its registry entry no longer offers it"


def _log_lost_tools(before: dict[str, dict], after: dict[str, dict]) -> None:
    """Say what happened to a tool that was working and now is not.

    At error level, and named: the rest of discovery warns about a script that
    never worked, which a reader can dismiss as a tool somebody is still
    writing. One that answered until this scan is a regression in something
    already in use, and the client is about to stop offering it.
    """
    for name in sorted(before.keys() - after.keys()):
        script = Path(before[name]["_script"])
        logger.error("Tool %s is no longer offered: %s", name, _why_gone(script))


async def watch_for_tool_changes(tools: dict[str, dict], notify, ready: asyncio.Event,
                                 fingerprint: tuple,
                                 interval: float = POLL_INTERVAL) -> None:
    """Keep *tools* current and call *notify* whenever the set changes.

    *tools* is mutated in place because the request handlers close over it —
    rebinding here would leave them reading the snapshot taken at startup,
    which is the whole of what this fixes. It is updated before *notify* runs,
    so a ``tools/list`` racing the notification still answers with the new set.

    *fingerprint* is the baseline *tools* was read against, and is passed in
    rather than stamped here: this coroutine starts whenever the loop first
    schedules it, which is after the handshake on a busy runner, and a baseline
    taken then already holds every change since startup. ``discover_with_baseline``
    is what pairs the two.

    *ready* is set by the first request the server answers. A notification sent
    before then would reach a client that has not finished initialising, and
    the tool list it asks for afterwards is current anyway.

    Discovery runs in a thread: it executes every offered script, and the event
    loop owns the client's connection while it does. So does the report of what
    was lost — naming a reason re-probes each vanished tool, one subprocess
    apiece.
    """
    while True:
        await asyncio.sleep(interval)
        try:
            fingerprint = await _poll_once(tools, notify, ready, fingerprint)
        except asyncio.CancelledError:
            raise
        except Exception:
            # One failed round costs one round. Letting it out instead kills the
            # task, and nobody is holding it — the client would go on showing
            # the list it had at startup for the rest of the session, with the
            # traceback surfacing only if the interpreter got around to it.
            logger.exception("Re-discovery failed, trying again in %ss", interval)


async def _poll_once(tools: dict[str, dict], notify, ready: asyncio.Event,
                     fingerprint: tuple) -> tuple:
    """One turn of the poll, returning the fingerprint to compare next time."""
    current = await asyncio.to_thread(discovery_fingerprint)
    if current == fingerprint:
        return current
    rediscovered = await asyncio.to_thread(discover_tools)
    if rediscovered == tools:
        logger.debug("Something under the scanned directories changed, the tools did not")
        return current
    await asyncio.to_thread(_log_lost_tools, dict(tools), rediscovered)
    tools.clear()
    tools.update(rediscovered)
    logger.info("Tools changed, now offering %d: %s", len(tools), ", ".join(sorted(tools)))
    await ready.wait()
    await notify()
    return current


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


def _extract_json(text: str) -> tuple[str, object] | None:
    """Extract JSON from mixed output (dashboard on stderr bleeds into stdout).

    Returns the matching substring paired with its already-parsed value, so a
    caller that needs both is not left re-parsing what this function just
    validated.
    """
    text = text.strip()
    if not text:
        return None

    # Try the whole thing first
    if text.startswith("{") or text.startswith("["):
        try:
            return text, json.loads(text)
        except json.JSONDecodeError:
            pass

    # Find the first line starting with { or [ that begins valid JSON
    lines = text.split("\n")
    candidates = [i for i, line in enumerate(lines) if line.strip()[:1] in ("{", "[")]
    for i in candidates:
        remainder = "\n".join(lines[i:])
        try:
            return remainder, json.loads(remainder)
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


@dataclass(frozen=True)
class RunningServer:
    """The server, the tool set its handlers read, and its first-request signal.

    ``tools`` is the live dict, not a copy — the handlers close over it and
    ``watch_for_tool_changes`` writes into it. ``ready`` is what tells the
    watcher a client is listening, and ``fingerprint`` is the baseline it
    compares against, stamped with the scan that filled ``tools``.
    """

    server: Any
    tools: dict[str, dict]
    ready: asyncio.Event
    fingerprint: tuple


def create_server() -> RunningServer:
    """Create the MCP server and discover tools. Requires the ``mcp`` package."""
    from mcp.server.lowlevel import Server
    from mcp.types import (
        CallToolRequestParams,
        CallToolResult,
        ListToolsResult,
        PaginatedRequestParams,
        TextContent,
        Tool,
    )

    discovered = discover_with_baseline()
    tools = discovered.tools
    server = Server("otto-workbench")
    ready = asyncio.Event()

    # Both handlers take (ctx, params): the SDK calls them with the request
    # context first, and a handler that omits it raises TypeError inside the
    # runner, which reaches the client as an internal error with no tools and
    # no call ever succeeding.
    async def handle_list_tools(ctx, params):
        ready.set()
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
        ready.set()
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

        result = await asyncio.to_thread(
            _run_script, [script] + cli_args, TOOL_CALL_BUDGET,
        )

        # Ahead of ok_exit_codes, so a schema listing 124 among its own codes
        # cannot turn an expired budget into a success the client acts on.
        if result.returncode == core.proc.TIMEOUT_RETURNCODE:
            return CallToolResult(
                content=[TextContent(type="text", text=(
                    f"Tool execution timed out ({TOOL_CALL_BUDGET}s)"))],
                isError=True,
            )

        ok_codes = {0} | set(schema.get("ok_exit_codes", []))
        if result.returncode not in ok_codes:
            error_text = result.stderr.strip() or f"Exit code {result.returncode}"
            return CallToolResult(
                content=[TextContent(type="text", text=error_text)],
                isError=True,
            )

        extracted = _extract_json(result.stdout)
        json_output, parsed = extracted if extracted else (None, None)

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

    return RunningServer(server=server, tools=tools, ready=ready,
                         fingerprint=discovered.fingerprint)


# ── Entry Point ───────────────────────────────────────────────────────────


def tool_list_changed_frame():
    """The ``notifications/tools/list_changed`` frame, ready to write.

    ceiling: built here and written straight to the transport, because the
    lowlevel ``Server`` keeps its ``ServerSession`` to itself — ``run()``
    returns nothing and the per-request context the SDK hands a handler is
    closed the moment that request finishes, so there is no session to ask.
    Upgrade to ``session.send_tool_list_changed()`` if the SDK ever exposes the
    session behind a connection.
    """
    from mcp.shared.message import SessionMessage
    from mcp.types import JSONRPCNotification

    return SessionMessage(message=JSONRPCNotification(
        jsonrpc="2.0", method="notifications/tools/list_changed"))


async def main():
    import anyio
    from mcp.server.stdio import stdio_server
    from mcp.types import ServerCapabilities, ToolsCapability

    running = create_server()
    logger.info("Starting otto-workbench MCP server with %d tools", len(running.tools))

    init_options = running.server.create_initialization_options(
        notification_options=None,
        experimental_capabilities=None,
    )
    if init_options.capabilities is None:
        init_options.capabilities = ServerCapabilities()
    # listChanged is a promise, not a description: a client that never sees it
    # has no reason to re-list, so the notification the watcher sends lands on
    # something that ignores it.
    init_options.capabilities.tools = ToolsCapability(listChanged=True)

    async with stdio_server() as (read_stream, write_stream):

        async def notify():
            # The serve loop closes the write stream when the client leaves,
            # and the watcher is cancelled a moment later — a notification
            # already in flight when that happens is not worth a traceback.
            try:
                await write_stream.send(tool_list_changed_frame())
            except (anyio.BrokenResourceError, anyio.ClosedResourceError) as exc:
                logger.debug("Could not announce the tool change: %s", exc)

        watcher = asyncio.create_task(
            watch_for_tool_changes(running.tools, notify, running.ready,
                                   running.fingerprint))
        try:
            await running.server.run(read_stream, write_stream, init_options)
        finally:
            watcher.cancel()
            # Awaited so the cancellation is retrieved rather than left for the
            # interpreter to complain about at exit, and so a poll already
            # inside a thread finishes before stdio goes away under it.
            with contextlib.suppress(asyncio.CancelledError):
                await watcher


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    asyncio.run(main())
