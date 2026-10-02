"""Tests for MCP server transport — a client speaking JSON-RPC to a running server."""

from __future__ import annotations

import json
import queue
import shutil
import stat
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "claude" / "mcps"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

from server import WORKBENCH_DIR
from config.tool_registry import Visibility

# ── Client Transport ──────────────────────────────────────────────────────


MCP_PROTOCOL_VERSION = "2025-06-18"

# One id per turn of the conversation the module fixture drives, named so a
# failing assertion says which request it was reading the answer to.
INITIALIZE_ID = 1
LIST_ID = 2
ECHO_ID = 3
PLAIN_ID = 4
SILENT_ID = 5

# Generous because the first run resolves and downloads `mcp`; later runs hit
# uv's cache and finish in about a second.
TRANSPORT_TIMEOUT = 300

# The readers are daemons draining pipes that have already reached EOF, so this
# only bounds a join that should return at once.
READER_JOIN_TIMEOUT = 5

# How often the reply loop wakes to ask whether the server is still alive. Short
# enough that a crash fails the test at once, long enough not to spin.
REPLY_POLL_INTERVAL = 0.1

uv_required = pytest.mark.skipif(
    shutil.which("uv") is None,
    reason="the server runs under `uv run --with mcp`, as ai/bin/otto-mcp-server does",
)


# Production `_tool_schema` only answers for a script named `pr`. The transport
# cases need several tools with different schema shapes (output_schema vs not,
# a contract broken on purpose), so the fake checkout patches `_tool_schema` to
# read a sidecar JSON next to each script. That is a fixture seam, not a second
# discovery protocol: handle_call_tool still spawns the script.
_ECHO_TOOL = '''\
#!/usr/bin/env python3
import json, sys
json.dump({"word": sys.argv[sys.argv.index("--word") + 1]}, sys.stdout)
'''

_PLAIN_TOOL = '''\
#!/usr/bin/env python3
print("plain output")
'''

_SILENT_TOOL = '''\
#!/usr/bin/env python3
print("no json here")
'''

_INNER_TOOL = '''\
#!/usr/bin/env python3
print("inner output")
'''

_ECHO_SCHEMA = {
    "name": "echo-tool",
    "description": "Echo a word back",
    "input_schema": {"type": "object", "properties": {"word": {"type": "string"}}},
    "output_schema": {"type": "object", "properties": {"word": {"type": "string"}}},
}

_PLAIN_SCHEMA = {
    "name": "plain-tool",
    "description": "Print a line of text",
    "input_schema": {"type": "object", "properties": {}},
}

_SILENT_SCHEMA = {
    "name": "silent-tool",
    "description": "Promise JSON and print prose",
    "input_schema": {"type": "object", "properties": {}},
    "output_schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}},
}

_INNER_SCHEMA = {
    "name": "inner-tool",
    "description": "An implementation detail of another tool",
    "input_schema": {"type": "object", "properties": {}},
}


@dataclass(frozen=True)
class _FixtureTool:
    """A script in the fake checkout, and the registry entry that names it."""

    name: str
    source: str
    schema: dict
    visibility: Visibility = Visibility.BRIEF


_FIXTURE_TOOLS = (
    _FixtureTool("echo-tool", _ECHO_TOOL, _ECHO_SCHEMA),
    _FixtureTool("plain-tool", _PLAIN_TOOL, _PLAIN_SCHEMA),
    _FixtureTool("silent-tool", _SILENT_TOOL, _SILENT_SCHEMA),
    _FixtureTool("inner-tool", _INNER_TOOL, _INNER_SCHEMA, visibility=Visibility.HIDDEN),
)

_FIXTURE_REGISTRY_META = """\
meta:
  section: "Fixture Tools"
  validation: bindir
  source: bin

tools:
"""

_SIDECAR_LAUNCHER = '''\
#!/usr/bin/env python3
"""Run the copied server with fixture schemas read from sidecar JSON files."""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import server  # noqa: E402


def _tool_schema(script: Path) -> dict | None:
    sidecar = Path(str(script) + ".schema.json")
    if not sidecar.is_file():
        return None
    schema = json.loads(sidecar.read_text())
    schema["_script"] = str(script)
    return schema


server._tool_schema = _tool_schema

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    asyncio.run(server.main())
'''


def _fixture_registry() -> str:
    return _FIXTURE_REGISTRY_META + "".join(
        f"  - name: {tool.name}\n"
        f"    permission: false\n"
        f"    visibility: {tool.visibility.value}\n"
        f'    description: "The {tool.name} fixture"\n'
        for tool in _FIXTURE_TOOLS)


def _write_fixture_tool(bin_dir: Path, tool: _FixtureTool) -> None:
    script = bin_dir / tool.name
    script.write_text(tool.source)
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    Path(str(script) + ".schema.json").write_text(json.dumps(tool.schema))


def _build_fake_checkout(root: Path) -> Path:
    """Lay out a throwaway checkout around the server and return its launcher.

    ``server.py`` derives ``WORKBENCH_DIR`` from its own resolved path, so the
    copy has to be a real file: a symlink resolves back to this repo and the
    server would discover the workbench's own tools instead of these three.

    The workbench's own Python is reached through that same derived path, so
    ``ai/lib`` is left pointing at this repo — the server imports the registry
    reader from the checkout it was copied out of.

    The launcher patches `_tool_schema` to read sidecar JSON, because
    production only imports a schema for a script named `pr` and these cases
    need several tools with different shapes.
    """
    mcps = root / "ai" / "claude" / "mcps"
    mcps.mkdir(parents=True)
    shutil.copy(WORKBENCH_DIR / "ai" / "claude" / "mcps" / "server.py", mcps / "server.py")
    (root / "ai" / "lib").symlink_to(WORKBENCH_DIR / "ai" / "lib")
    launcher = mcps / "run.py"
    launcher.write_text(_SIDECAR_LAUNCHER)

    bin_dir = root / "bin"
    bin_dir.mkdir()
    for tool in _FIXTURE_TOOLS:
        _write_fixture_tool(bin_dir, tool)

    (bin_dir / "registry.yml").write_text(_fixture_registry())

    return launcher


@dataclass(frozen=True)
class _Exchange:
    """Every frame the server wrote, plus the stderr that explains a missing one."""

    frames: list[dict]
    stderr: str

    def result(self, request_id: int) -> dict:
        for frame in self.frames:
            if frame.get("id") != request_id:
                continue
            assert "error" not in frame, f"request {request_id} failed: {frame['error']}"
            return frame["result"]
        raise AssertionError(f"no reply to request {request_id}; server stderr:\n{self.stderr}")


def _call(request_id: int, name: str, arguments: dict) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


class _ServerProcess:
    """The server running under stdio, with a reader thread on each pipe.

    Both pipes need a reader: stderr carries the server's logging and would
    otherwise fill and block the process mid-answer.
    """

    def __init__(self, script: Path):
        self._proc = subprocess.Popen(
            ["uv", "run", "--no-project", "--with", "mcp", "python3", str(script)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self._replies: queue.Queue[str] = queue.Queue()
        self._stderr: list[str] = []
        self._stdout_reader = threading.Thread(target=self._pump_stdout, daemon=True)
        self._stderr_reader = threading.Thread(target=self._pump_stderr, daemon=True)
        self._stdout_reader.start()
        self._stderr_reader.start()

    def _pump_stdout(self) -> None:
        for line in self._proc.stdout:
            self._replies.put(line)

    def _pump_stderr(self) -> None:
        # A line at a time rather than one read() to EOF: the server that goes
        # quiet without dying is the failure hardest to explain, and its log is
        # the explanation. Reading to EOF would hand back nothing until it exits.
        for line in self._proc.stderr:
            self._stderr.append(line)

    @property
    def stderr(self) -> str:
        self._stderr_reader.join(timeout=READER_JOIN_TIMEOUT)
        return "".join(self._stderr)

    @property
    def status(self) -> str:
        """Why the server can no longer be expected to answer."""
        if self._stdout_reader.is_alive():
            return "it is still running and said nothing"
        return f"it exited {self._proc.wait(timeout=READER_JOIN_TIMEOUT)}"

    def send(self, messages: list[dict]) -> None:
        """Write every frame in one go, leaving stdin open for the replies."""
        try:
            self._proc.stdin.write("".join(json.dumps(message) + "\n" for message in messages))
            self._proc.stdin.flush()
        except BrokenPipeError:
            # A server that died before reading needs no report here: stdout is
            # already at EOF, so collect() returns at once and the caller raises
            # with the exit code and stderr attached.
            pass

    def await_notification(self, method: str, timeout: float) -> dict | None:
        """The next frame announcing *method*, or None if it never arrives.

        Frames read on the way are dropped: a caller waiting on a notification
        has already collected the replies it asked for, and re-listing after
        this returns is what it wants the answer to anyway.
        """
        deadline = time.monotonic() + timeout
        while True:
            line = self._next_line(deadline)
            if line is None:
                return None
            frame = json.loads(line) if line.strip() else {}
            if frame.get("method") == method:
                return frame

    def collect(self, expected: int) -> list[dict]:
        """Frames written back, returning early once no more can arrive."""
        frames: list[dict] = []
        deadline = time.monotonic() + TRANSPORT_TIMEOUT
        while sum(1 for frame in frames if "id" in frame) < expected:
            line = self._next_line(deadline)
            if line is None:
                return frames
            if line.strip():
                frames.append(json.loads(line))
        return frames

    def _next_line(self, deadline: float) -> str | None:
        """The next line of stdout, or None once none can arrive.

        Stdout at EOF means the server is gone and the rest of the replies are
        never coming. The regressions this test guards against kill it before
        it writes anything, so blocking until the deadline would turn a clear
        failure into a suite that looks hung.
        """
        while self._stdout_reader.is_alive() and time.monotonic() < deadline:
            try:
                return self._replies.get(timeout=REPLY_POLL_INTERVAL)
            except queue.Empty:
                continue
        return None

    def finish(self) -> None:
        """Close stdin, which is how the server learns the session is over."""
        self._proc.stdin.close()
        self._proc.wait(timeout=TRANSPORT_TIMEOUT)

    def close(self) -> None:
        # Ask before killing. The server is a grandchild — `uv run` execs it
        # under itself — so a kill() reaches the launcher and leaves the server
        # holding both pipes open; the reader joins below then burn their whole
        # timeout on every single case. Closing stdin is the shutdown the server
        # is written to act on, and both pipes reach EOF when it exits.
        if not self._proc.stdin.closed:
            self._proc.stdin.close()
        try:
            self._proc.wait(timeout=READER_JOIN_TIMEOUT)
        except subprocess.TimeoutExpired:
            self._proc.kill()
        self._stdout_reader.join(timeout=READER_JOIN_TIMEOUT)
        self._stderr_reader.join(timeout=READER_JOIN_TIMEOUT)
        for pipe in (self._proc.stdin, self._proc.stdout, self._proc.stderr):
            if not pipe.closed:
                pipe.close()


def _talk(script: Path, messages: list[dict]) -> _Exchange:
    """Write raw JSON-RPC frames at the server over stdio and collect the replies.

    Hand-written rather than driven by the SDK's client: nothing installs
    ``mcp`` into the test interpreter, and these frames are what a client
    actually puts on the wire. ``--no-project`` keeps uv from resolving
    whatever project the cwd belongs to, the same reason the launcher passes
    it.

    Stdin stays open until every reply is in. Closing it early ends the session
    and the server answers each still-running call with "Connection closed" —
    a tool call runs a subprocess, so it is always the one still running.
    """
    expected = sum(1 for message in messages if "id" in message)
    proc = _ServerProcess(script)
    try:
        proc.send(messages)
        frames = proc.collect(expected)
        answered = sum(1 for frame in frames if "id" in frame)
        assert answered == expected, (
            f"{expected - answered} of {expected} replies never arrived — "
            f"{proc.status}; server stderr:\n{proc.stderr}"
        )
        proc.finish()
        return _Exchange(frames=frames, stderr=proc.stderr)
    finally:
        proc.close()


_HANDSHAKE = [
    {
        "jsonrpc": "2.0",
        "id": INITIALIZE_ID,
        "method": "initialize",
        "params": {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "0"},
        },
    },
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
]


def _list(request_id: int) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": "tools/list", "params": {}}


@pytest.fixture(scope="module")
def transport(tmp_path_factory):
    """One conversation, reused by every case — each spawn costs a uv resolve."""
    script = _build_fake_checkout(tmp_path_factory.mktemp("checkout"))
    return _talk(script, [
        *_HANDSHAKE,
        _list(LIST_ID),
        _call(ECHO_ID, "echo-tool", {"word": "hello"}),
        _call(PLAIN_ID, "plain-tool", {}),
        _call(SILENT_ID, "silent-tool", {}),
    ])


@uv_required
class TestClientTransport:
    """What a client gets when it speaks the protocol at a running server.

    Every case above this one calls the discovery helpers directly, so a server
    that scanned correctly and then answered nothing looked fully covered: both
    handlers took the wrong number of arguments and every tools/list and
    tools/call came back an internal error. Spawning the server the way the
    launcher does and writing frames at it is the shape that catches that.
    """

    def test_a_client_can_list_the_tools(self, transport):
        listed = transport.result(LIST_ID)["tools"]

        assert {tool["name"] for tool in listed} == {"echo-tool", "plain-tool", "silent-tool"}

    def test_a_tool_the_registry_hides_never_reaches_the_client(self, transport):
        """The fourth script in the fixture bin/ is registered hidden.

        The unit cases assert the filter over a registry handed to discovery in
        memory; here the server read the same decision out of a registry.yml on
        its own.
        """
        listed = transport.result(LIST_ID)["tools"]

        assert "inner-tool" not in {tool["name"] for tool in listed}

    def test_a_listed_tool_describes_itself_the_way_its_registry_does(self, transport):
        listed = {tool["name"]: tool for tool in transport.result(LIST_ID)["tools"]}

        assert listed["echo-tool"]["description"] == "The echo-tool fixture"

    def test_a_listed_tool_carries_the_schemas_it_declared(self, transport):
        listed = {tool["name"]: tool for tool in transport.result(LIST_ID)["tools"]}

        assert listed["echo-tool"]["inputSchema"]["properties"]["word"]["type"] == "string"
        assert listed["echo-tool"]["outputSchema"]["type"] == "object"
        assert "outputSchema" not in listed["plain-tool"]

    def test_a_client_can_call_a_tool(self, transport):
        result = transport.result(ECHO_ID)

        assert result.get("isError") is not True
        assert json.loads(result["content"][0]["text"]) == {"word": "hello"}

    def test_a_declared_output_schema_arrives_as_structured_content(self, transport):
        """A client validates the answer against the schema the server advertised.

        Advertising one and returning text alone raises inside the client
        before the caller sees any of it, so every tool with an output_schema
        is unusable — which is all of the workbench's real ones.
        """
        assert transport.result(ECHO_ID)["structuredContent"] == {"word": "hello"}

    def test_a_tool_with_no_output_schema_returns_text_alone(self, transport):
        result = transport.result(PLAIN_ID)

        assert result["content"][0]["text"] == "plain output"
        assert "structuredContent" not in result

    def test_a_tool_that_promises_json_and_prints_prose_is_an_error(self, transport):
        """Naming the tool here beats the client's opaque raise on a missing key."""
        result = transport.result(SILENT_ID)

        assert result["isError"] is True
        assert "silent-tool" in result["content"][0]["text"]

    def test_the_server_promises_to_announce_tool_changes(self, transport):
        """listChanged is a promise: without it a client has no reason to re-list."""
        capabilities = transport.result(INITIALIZE_ID)["capabilities"]

        assert capabilities["tools"]["listChanged"] is True


_LATER_TOOL = '''\
#!/usr/bin/env python3
print("later output")
'''

_LATER_SCHEMA = {
    "name": "later-tool",
    "description": "Arrived after the handshake",
    "input_schema": {"type": "object", "properties": {}},
}

_LATER_ENTRY = """\
  - name: later-tool
    permission: false
    visibility: brief
    description: "The later-tool fixture"
"""

# Several poll intervals, so a loaded runner has room without the case passing
# for the wrong reason — the notification is either sent on the next poll or not
# at all.
REDISCOVERY_TIMEOUT = 60

FIRST_LIST_ID = 2
SECOND_LIST_ID = 3


def _tools_of(frames: list[dict], request_id: int) -> list[dict]:
    for frame in frames:
        if frame.get("id") == request_id:
            return frame["result"]["tools"]
    raise AssertionError(f"no tools/list reply with id {request_id} in {frames}")


@uv_required
class TestRediscovery:
    """A tool merged while the client is connected, without restarting it.

    The unit cases drive the watcher with discovery stubbed out. This is the
    only place the whole chain runs: a registry entry appearing, the poll
    noticing, and a frame reaching the client that did not ask for it.
    """

    def test_a_tool_added_after_startup_is_announced_and_then_listed(self, tmp_path):
        script = _build_fake_checkout(tmp_path)
        proc = _ServerProcess(script)
        try:
            proc.send([*_HANDSHAKE, _list(FIRST_LIST_ID)])
            first = proc.collect(2)
            assert {tool["name"] for tool in _tools_of(first, FIRST_LIST_ID)} == {
                "echo-tool", "plain-tool", "silent-tool"}

            later = tmp_path / "bin" / "later-tool"
            later.write_text(_LATER_TOOL)
            later.chmod(later.stat().st_mode | stat.S_IXUSR)
            Path(str(later) + ".schema.json").write_text(json.dumps(_LATER_SCHEMA))
            registry = tmp_path / "bin" / "registry.yml"
            registry.write_text(registry.read_text() + _LATER_ENTRY)

            announced = proc.await_notification(
                "notifications/tools/list_changed", REDISCOVERY_TIMEOUT)
            proc.send([_list(SECOND_LIST_ID)])
            relisted = sorted(tool["name"] for tool in _tools_of(
                proc.collect(1), SECOND_LIST_ID))

            # The list is asked for either way, so a failure says which half
            # broke: a tool discovery never picked up, or one it did pick up and
            # never announced.
            #
            # Built on demand rather than up front: reading proc.stderr joins
            # the reader thread, which on a passing run is still following a
            # live server and costs the join its full timeout for a message
            # nobody reads.
            def evidence() -> str:
                return (f"{proc.status}; a list asked for afterwards holds "
                        f"{relisted}; server stderr:\n{proc.stderr}")

            assert announced is not None, f"the tool change was never announced — {evidence()}"
            assert "later-tool" in relisted, f"the new tool never arrived — {evidence()}"
        finally:
            proc.close()
