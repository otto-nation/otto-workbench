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
import stat
import subprocess
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
import timeouts  # noqa: E402
from tool_registry import RegistryEntry, load_registry_entries, registry_files  # noqa: E402

COMPONENT_BIN_GLOBS = ("bin", "*/bin", "*/*/bin")

# A probe prints a schema the script already holds, so it belongs in the QUICK
# tier and a breach is a wedged process or a machine with nothing left to
# schedule. This was a local 2.0 for as long as the table was thought to be out
# of reach here — under the cost of starting a Python interpreter on a loaded
# machine, and a probe that outran it dropped the tool for the whole session.
DISCOVERY_TIMEOUT = timeouts.QUICK

# Seconds a tool call gets before the client is told it timed out. Not a tier
# from `timeouts`: those bound a subprocess that should already have answered,
# while this is a budget for whichever tool the client asked for — `pr review`
# drives agents for minutes. Same carve-out as `eval_task.EVAL_CASE_BUDGET`.
TOOL_CALL_BUDGET = 300

TOOL_SCHEMA_FLAG = "--tool-schema"

# Candidates are probed together, so the bound above is the wait for all of
# them rather than for each in turn — which is what lets the bound be generous
# enough to survive a loaded machine without startup paying per tool.
#
# ceiling: one fixed cap for every machine. Make it a function of
# `os.cpu_count()` if the workbench is ever installed somewhere with fewer
# cores than this, where the spawns are themselves the contention.
PROBE_WORKERS = 8

# How many times a candidate is probed before discovery gives up on it this
# scan. A second try costs one extra bound for the whole round rather than one
# per tool, because that round is concurrent too, and it is only ever paid when
# something already went wrong. It is worth paying: re-discovery runs when the
# scanned directories change, so a tool dropped here is missing until somebody
# edits the tree rather than until the next poll.
PROBE_ATTEMPTS = 2

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

# Seconds between fingerprints of what discovery reads. A poll that finds
# nothing costs one stat per file in the scanned directories and nothing else,
# so this is a bound on how stale a client's tool list gets rather than a cost
# to trade against. Re-discovery only runs when the fingerprint moves.
POLL_INTERVAL = 2.0


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


class ProbeFailure(Enum):
    """Why a probe did not answer, split by what would put it right.

    A script that exits non-zero, prints something other than JSON, or omits a
    required key is broken, and its author is who fixes it. One that never
    answers inside the bound is a wedged process or a machine with nothing left
    to schedule — far more often a fact about the machine than about the
    script. Reporting the two the same way sends whoever reads it after the
    wrong thing, so the distinction travels with the result.

    A candidate with no marker is neither: nothing ran, and most executables in
    a ``bin/`` are not tools.
    """

    UNMARKED = "unmarked"
    TIMED_OUT = "timed out"
    BROKEN = "broken"


@dataclass(frozen=True)
class ProbeResult:
    """What ``script --tool-schema`` answered, or why it did not.

    The reason travels with the result rather than going straight to the log,
    so a caller that is not the server — ``bin/local/validate-tool-schema`` —
    can report the same failure to whoever broke the script. ``failure`` says
    which kind of failure it was, so that caller can also decline to call a
    slow machine a broken tool.
    """

    script: Path
    schema: dict | None = None
    reason: str | None = None
    failure: ProbeFailure | None = None

    @property
    def ok(self) -> bool:
        return self.schema is not None

    @property
    def timed_out(self) -> bool:
        return self.failure is ProbeFailure.TIMED_OUT


def probe_tool(script: Path) -> ProbeResult:
    """Run ``script --tool-schema`` and return its schema or a failure reason.

    The marker check is re-applied here rather than left to the caller.
    ``tool_candidates`` already filters, but probing is execution: a script that
    ignores unknown flags does its real work instead of answering, and
    ``build-otto-ai-tools-tarball`` wrote a release archive into the CWD that
    way. The invariant travels with the function that would break it.

    One probe and one script. ``probe_tools`` is what discovery and the
    validator call, because it runs the round concurrently and retries the
    probes that ran out of time.
    """
    if not declares_tool_schema(script):
        return ProbeResult(script, reason="no protocol marker in its source",
                           failure=ProbeFailure.UNMARKED)
    try:
        result = subprocess.run(
            [str(script), TOOL_SCHEMA_FLAG],
            capture_output=True,
            text=True,
            timeout=DISCOVERY_TIMEOUT,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        if result.returncode != 0:
            return ProbeResult(script, failure=ProbeFailure.BROKEN, reason=(
                f"{TOOL_SCHEMA_FLAG} exited {result.returncode}: "
                f"{result.stderr.strip() or '(no stderr)'}"
            ))
        schema = json.loads(result.stdout)
        missing = [key for key in REQUIRED_SCHEMA_KEYS if key not in schema]
        if missing:
            return ProbeResult(script, failure=ProbeFailure.BROKEN,
                               reason=f"schema is missing {', '.join(missing)}")
        schema["_script"] = str(script)
        return ProbeResult(script, schema=schema)
    except subprocess.TimeoutExpired:
        return ProbeResult(
            script, failure=ProbeFailure.TIMED_OUT,
            reason=f"{TOOL_SCHEMA_FLAG} did not answer within {DISCOVERY_TIMEOUT:g}s")
    except (json.JSONDecodeError, OSError) as exc:
        # Name the exception type rather than trusting its str() to say which
        # of the two it was — docs/tools.md tells readers these are distinct.
        return ProbeResult(script, reason=f"{type(exc).__name__}: {exc}",
                           failure=ProbeFailure.BROKEN)


def _probe_round(scripts: list[Path]) -> list[ProbeResult]:
    """Probe every script at once, answering in the order they were given.

    Threads rather than tasks or processes: a probe is a subprocess spawn, so
    the interpreter is waiting on ``wait4`` for all but a sliver of it.
    """
    with ThreadPoolExecutor(max_workers=min(len(scripts), PROBE_WORKERS)) as pool:
        return list(pool.map(probe_tool, scripts))


def probe_tools(scripts: list[Path]) -> list[ProbeResult]:
    """Probe every script in *scripts*, returning results in the given order.

    The order is the caller's rather than completion's. Discovery and
    ``bin/local/validate-tool-schema`` both report in path order, and a list
    that reshuffled under load would make two runs over the same tree disagree
    about nothing.

    A probe that ran out of time is tried again, up to ``PROBE_ATTEMPTS`` in
    all. Only the ones that timed out are re-run, and they are re-run together,
    so the retry costs one more bound for the round rather than one per tool. A
    script that answered — with a schema or with a mistake in one — is left
    alone: running it again would cost the same wait to be told the same thing.
    """
    if not scripts:
        return []
    results = _probe_round(scripts)
    for _ in range(PROBE_ATTEMPTS - 1):
        retry = [result.script for result in results if result.timed_out]
        if not retry:
            break
        logger.warning("Probing %d script(s) again, they did not answer in time: %s",
                       len(retry), ", ".join(str(script) for script in retry))
        again = {result.script: result for result in _probe_round(retry)}
        results = [again.get(result.script, result) if result.timed_out else result
                   for result in results]
    return results


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


def _offered_scripts(dirs: list[Path],
                     registry: dict[Path, RegistryEntry]) -> dict[Path, RegistryEntry]:
    """Every offered candidate under *dirs*, in directory-then-path order."""
    offers: dict[Path, RegistryEntry] = {}
    for d in dirs:
        offers.update(offered_candidates(d, registry))
    return offers


def _scan_offered(dirs: list[Path], registry: dict[Path, RegistryEntry]) -> list[dict]:
    """Return tool schemas from the offered scripts under *dirs*.

    Every directory's candidates go into one probing round rather than a round
    per directory, so what a client waits for at startup is one probe and not
    one per tool.

    A script that carries a marker meant to be a tool, so every way it can then
    fail to answer is logged. Silence here reads as "no tool here" and leaves
    nothing to debug — the scan covers every component's ``bin/``, so the
    author of a broken tool is rarely the person reading these logs.
    Executables with no marker are not tools and stay quiet.

    A probe that never answered is logged at error level rather than warning,
    and worded so it does not read as a broken tool: it outlived a bound a
    script answering from memory cannot plausibly need, which is a wedged
    process or a machine under load. The two failures want different people to
    look at them, so they do not share a line.
    """
    offers = _offered_scripts(dirs, registry)
    schemas = []
    for result in probe_tools(list(offers)):
        if result.ok:
            schemas.append(_described(result.schema, offers[result.script]))
        elif result.timed_out:
            logger.error(
                "Not offering %s this scan: %s, on %d attempts. A probe answers "
                "with a schema the script already holds, so this is a loaded "
                "machine or a wedged script rather than a broken tool. Discovery "
                "runs again when something under the scanned directories changes.",
                result.script, result.reason, PROBE_ATTEMPTS)
        else:
            logger.warning("Skipping %s: %s", result.script, result.reason)
    return schemas


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

    for schema in _scan_offered(dirs, registry):
        if schema["name"] not in tools:
            tools[schema["name"]] = schema
            logger.info("Discovered tool: %s (%s)", schema["name"], schema["_script"])

    return tools


# ── Re-discovery ──────────────────────────────────────────────────────────


def _stamp(path: Path) -> tuple | None:
    """What a file would have to change for discovery to answer differently.

    Size and modification time cover an edited script and an edited registry.
    The mode is here because ``chmod +x`` is the whole of what turns a file in
    a scanned directory into a candidate, and it moves neither of the others.
    """
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size, st.st_mode)


def _dir_entries(d: Path) -> list[Path]:
    try:
        return sorted(d.iterdir())
    except OSError:
        return []


def discovery_fingerprint(root: Path | None = None) -> tuple:
    """A comparable value over every input ``discover_tools`` reads.

    Every file in every scanned directory rather than only the candidates:
    reading each one's source to decide whether it carries a marker is the
    expensive half of discovery, and a fingerprint that ran it would cost as
    much as the re-scan it exists to avoid. The directories themselves are
    stamped too, so a script added or deleted is a change even when the file
    that appeared is one this stamp would otherwise ignore.

    The registries are inputs as much as the scripts are — an entry going
    ``hidden`` withdraws a tool without touching a line of its script.
    """
    base = WORKBENCH_DIR if root is None else Path(root)
    dirs = discover_tool_dirs(base)
    watched = [*dirs, *(e for d in dirs for e in _dir_entries(d)), *registry_files(base)]
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
    """Why a tool that used to answer no longer does."""
    if not script.exists():
        return "its script is gone"
    return probe_tool(script).reason or "its registry entry no longer offers it"


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

        try:
            result = await asyncio.to_thread(
                subprocess.run,
                [script] + cli_args,
                capture_output=True,
                text=True,
                timeout=TOOL_CALL_BUDGET,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
        except subprocess.TimeoutExpired:
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
