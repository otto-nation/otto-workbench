"""Tests for MCP server — discovery, arg mapping, JSON extraction, and transport."""

from __future__ import annotations

import json
import logging
import queue
import re
import shutil
import stat
import subprocess
import sys
import textwrap
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "claude" / "mcps"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import server
from server import (
    WORKBENCH_DIR,
    _args_to_cli,
    _extract_json,
    declares_tool_schema,
    discover_tool_dirs,
    discover_tools,
)
from tool_registry import RegistryEntry, Visibility


# ── JSON Extraction ───────────────────────────────────────────────────────


class TestExtractJson:
    def test_pure_json(self):
        text = '{"status": "ok", "count": 5}'
        assert json.loads(_extract_json(text)) == {"status": "ok", "count": 5}

    def test_json_after_dashboard(self):
        text = textwrap.dedent("""\
            ═══ CI Status ═══
            ✓ All checks passed

            {
              "status": "ok",
              "failures": []
            }
        """)
        result = _extract_json(text)
        assert result is not None
        parsed = json.loads(result)
        assert parsed["status"] == "ok"

    def test_empty_input(self):
        assert _extract_json("") is None
        assert _extract_json("   ") is None

    def test_no_json(self):
        assert _extract_json("just some text\nno json here") is None

    def test_json_array(self):
        text = '[{"name": "a"}, {"name": "b"}]'
        result = _extract_json(text)
        assert result is not None
        assert len(json.loads(result)) == 2

    def test_partial_json_skipped(self):
        text = textwrap.dedent("""\
            {broken
            {"valid": true}
        """)
        result = _extract_json(text)
        assert result is not None
        assert json.loads(result) == {"valid": True}


# ── Argument Mapping ──────────────────────────────────────────────────────


class TestArgsToCLI:
    def test_boolean_true(self):
        schema = {"properties": {"fix": {"type": "boolean"}}}
        args = _args_to_cli({"fix": True}, schema)
        assert args == ["--fix"]

    def test_boolean_false(self):
        schema = {"properties": {"fix": {"type": "boolean"}}}
        args = _args_to_cli({"fix": False}, schema)
        assert args == []

    def test_string_arg(self):
        schema = {"properties": {"branch": {"type": "string"}}}
        args = _args_to_cli({"branch": "main"}, schema)
        assert args == ["--branch", "main"]

    def test_integer_arg(self):
        schema = {"properties": {"count": {"type": "integer"}}}
        args = _args_to_cli({"count": 5}, schema)
        assert args == ["--count", "5"]

    def test_underscore_to_dash(self):
        schema = {"properties": {"repo_dir": {"type": "string"}}}
        args = _args_to_cli({"repo_dir": "/tmp/repo"}, schema)
        assert args == ["--repo-dir", "/tmp/repo"]

    def test_multiple_args(self):
        schema = {"properties": {
            "fix": {"type": "boolean"},
            "branch": {"type": "string"},
        }}
        args = _args_to_cli({"fix": True, "branch": "feat"}, schema)
        assert "--fix" in args
        assert "--branch" in args
        assert "feat" in args

    def test_none_value_skipped(self):
        schema = {"properties": {"branch": {"type": "string"}}}
        args = _args_to_cli({"branch": None}, schema)
        assert args == []


# ── Tool Discovery ────────────────────────────────────────────────────────


def _registered(*scripts: Path, visibility: Visibility = Visibility.BRIEF,
                description: str = "", when_to_use: str = "",
                usage: str = "") -> dict[Path, RegistryEntry]:
    """A registry offering each of *scripts* under an entry of its own.

    Discovery takes the registry as an argument for the same reason it takes
    the directories: a case can then describe a tree the checkout does not
    have. The real mapping — script path to entry, read out of the
    ``registry.yml`` files — is tests/test_tool_registry.py's subject.
    """
    return {script.resolve(): RegistryEntry(
        name=script.name,
        description=description or f"the {script.name} tool",
        visibility=visibility,
        when_to_use=when_to_use,
        usage=usage,
    ) for script in scripts}


def _side_effect_of(script: Path) -> Path:
    """The file _write_destructive_script's subject touches when it runs."""
    return script.parent / "side-effect"


def _write_destructive_script(directory: Path) -> Path:
    """Write an unmarked executable whose only act is a visible side effect.

    Nothing here answers the protocol, so anything that runs it has skipped the
    marker check — which is the whole assertion.
    """
    script = directory / "destructive-script"
    script.write_text(f"#!/bin/bash\ntouch '{_side_effect_of(script)}'\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


def _write_marked_script(directory: Path, name: str, **schema) -> Path:
    """An executable answering the probe with *schema*, defaults filled in."""
    document = {"name": name, "description": f"{name}, as the script tells it",
                "input_schema": {"type": "object", "properties": {}}, **schema}
    script = directory / name
    script.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import json, sys
        if "--tool-schema" in sys.argv:
            json.dump({document!r}, sys.stdout)
            sys.exit(0)
        open({str(_side_effect_of(script))!r}, "w").close()
    """))
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


class TestDiscovery:
    """What a scan of a given directory turns up.

    Every case names its directories and its registry explicitly. Which
    directories the server picks when it is not told is TestWorkbenchToolDirs'
    subject — leaving the derived set in would make each "nothing was
    discovered" assertion also assert that the workbench ships no tools.
    """

    def test_discovers_tool_schema_scripts(self, tmp_path):
        script = _write_marked_script(tmp_path, "my-tool")

        tools = discover_tools([tmp_path], _registered(script))

        assert "my-tool" in tools
        assert tools["my-tool"]["input_schema"] == {"type": "object", "properties": {}}

    def test_skips_non_tool_scripts(self, tmp_path):
        script = tmp_path / "plain-script"
        script.write_text("#!/bin/bash\necho hello\n")
        script.chmod(script.stat().st_mode | stat.S_IXUSR)

        tools = discover_tools([tmp_path], _registered(script))

        assert len(tools) == 0

    def test_script_without_the_flag_is_never_executed(self, tmp_path):
        """A script that ignores unknown flags must not run during discovery."""
        script = _write_destructive_script(tmp_path)

        tools = discover_tools([tmp_path], _registered(script))

        assert tools == {}
        assert not _side_effect_of(script).exists()

    def test_probing_an_unmarked_script_refuses_to_run_it(self, tmp_path):
        """The guard travels with probe_tool, not with the caller that filters.

        ``tool_candidates`` screens for the marker, but probe_tool is importable
        on its own and running an unmarked script is what wrote a release
        archive into the CWD.
        """
        script = _write_destructive_script(tmp_path)

        result = server.probe_tool(script)

        assert result.ok is False
        assert "no protocol marker" in result.reason
        assert not _side_effect_of(script).exists()

    def test_tool_parser_import_counts_as_a_declaration(self, tmp_path):
        """ToolParser-based scripts inherit the flag without naming it."""
        script = tmp_path / "framework-tool"
        script.write_text("#!/usr/bin/env python3\nfrom tool_parser import ToolParser\n")

        assert declares_tool_schema(script) is True

    def test_tarball_builder_is_not_a_probe_candidate(self):
        """The script that motivated the guard must stay out of the probe path.

        A prose mention of the flag matches the scan, so its comments deliberately
        avoid the literal.
        """
        builder = (
            Path(__file__).resolve().parent.parent
            / "ai" / "claude" / "bin" / "build-otto-ai-tools-tarball"
        )
        if not builder.exists():
            pytest.skip("builder not found")

        assert declares_tool_schema(builder) is False

    def test_launcher_is_not_a_probe_candidate(self):
        """Probing the launcher would exec the server and hang until the timeout.

        It sits in one of the directories always scanned, so like the tarball
        builder its help text describes the protocol without spelling the
        literal.
        """
        launcher = (
            Path(__file__).resolve().parent.parent
            / "ai" / "claude" / "bin" / "otto-mcp-server"
        )
        if not launcher.exists():
            pytest.skip("launcher not found")

        assert declares_tool_schema(launcher) is False

    def test_skips_unreadable_script(self, tmp_path):
        script = tmp_path / "unreadable"
        script.write_text("#!/bin/bash\necho --tool-schema\n")
        script.chmod(stat.S_IXUSR)

        assert declares_tool_schema(script) is False

    def test_skips_hidden_files(self, tmp_path):
        script = tmp_path / ".hidden-tool"
        script.write_text(textwrap.dedent("""\
            #!/usr/bin/env python3
            import json, sys
            if "--tool-schema" in sys.argv:
                json.dump({"name": "hidden", "input_schema": {}}, sys.stdout)
                sys.exit(0)
        """))
        script.chmod(script.stat().st_mode | stat.S_IXUSR)

        tools = discover_tools([tmp_path], _registered(script))
        assert len(tools) == 0

    def test_a_tool_that_exits_nonzero_is_reported(self, tmp_path, caplog):
        """Carrying a marker means it meant to be a tool, so failing is news.

        The scan covers every component's bin/, so whoever reads these logs is
        rarely the person who broke the script — a silent skip is indexed under
        "no tool here" and leaves nothing to debug.
        """
        script = tmp_path / "broken-tool"
        script.write_text("#!/bin/bash\n# answers --tool-schema\necho boom >&2\nexit 3\n")
        script.chmod(script.stat().st_mode | stat.S_IXUSR)

        with caplog.at_level(logging.WARNING, logger="otto-mcp"):
            assert discover_tools([tmp_path], _registered(script)) == {}

        assert "broken-tool" in caplog.text
        assert "exited 3" in caplog.text
        assert "boom" in caplog.text

    def test_a_tool_with_an_incomplete_schema_names_the_missing_key(self, tmp_path, caplog):
        script = tmp_path / "partial-tool"
        script.write_text(textwrap.dedent("""\
            #!/usr/bin/env python3
            import json, sys
            if "--tool-schema" in sys.argv:
                json.dump({"name": "partial-tool"}, sys.stdout)
                sys.exit(0)
        """))
        script.chmod(script.stat().st_mode | stat.S_IXUSR)

        with caplog.at_level(logging.WARNING, logger="otto-mcp"):
            assert discover_tools([tmp_path], _registered(script)) == {}

        assert "partial-tool" in caplog.text
        assert "input_schema" in caplog.text

    def test_a_tool_emitting_invalid_json_is_reported(self, tmp_path, caplog):
        script = tmp_path / "garbled-tool"
        script.write_text("#!/bin/bash\n# answers --tool-schema\necho not json\n")
        script.chmod(script.stat().st_mode | stat.S_IXUSR)

        with caplog.at_level(logging.WARNING, logger="otto-mcp"):
            assert discover_tools([tmp_path], _registered(script)) == {}

        assert "garbled-tool" in caplog.text
        # One except branch covers three failure modes, so the log has to name
        # which one rather than leave it to the exception's str().
        assert "JSONDecodeError" in caplog.text

    def test_a_script_with_no_marker_is_skipped_quietly(self, tmp_path, caplog):
        """Most executables are not tools — warning on each would drown the rest."""
        script = tmp_path / "plain-script"
        script.write_text("#!/bin/bash\necho hello\n")
        script.chmod(script.stat().st_mode | stat.S_IXUSR)

        with caplog.at_level(logging.WARNING, logger="otto-mcp"):
            assert discover_tools([tmp_path], _registered(script)) == {}

        assert caplog.text == ""

    def test_no_directories_yields_no_tools(self):
        assert discover_tools([], {}) == {}

    def test_discovers_real_tools(self):
        """Verify discovery works with actual ToolParser-enabled scripts."""
        bin_dir = WORKBENCH_DIR / "ai" / "claude" / "bin"
        if not (bin_dir / "pr").exists():
            pytest.skip("scripts not found")

        tools = discover_tools([bin_dir])

        assert "pr" in tools
        assert "input_schema" in tools["pr"]
        assert tools["pr"]["output_schema"]["type"] == "object"


class TestRegistryVisibility:
    """Which of the marked scripts a client is offered.

    Carrying the marker makes a script probeable, not public. Every one of them
    used to be listed, which put ``ci-check`` and ``pr-rebase`` beside ``pr`` —
    the CLI whose ``pr ci`` and ``pr rebase`` subcommands run them.
    """

    def test_a_hidden_tool_is_neither_offered_nor_run(self, tmp_path):
        """The filter runs before the probe, so a hidden script is never executed."""
        script = _write_marked_script(tmp_path, "inner-tool")

        tools = discover_tools([tmp_path], _registered(script, visibility=Visibility.HIDDEN))

        assert tools == {}
        assert not _side_effect_of(script).exists()

    def test_an_unregistered_tool_is_neither_offered_nor_run(self, tmp_path, caplog):
        """A marked script nothing documents is as absent as a broken one.

        It is a warning rather than a silent skip: the marker says it meant to
        be a tool, and bin/local/validate-tool-schema fails the build on it.
        """
        script = _write_marked_script(tmp_path, "stray-tool")

        with caplog.at_level(logging.WARNING, logger="otto-mcp"):
            assert discover_tools([tmp_path], {}) == {}

        assert "stray-tool" in caplog.text
        assert "no registry entry" in caplog.text
        assert not _side_effect_of(script).exists()

    def test_the_registry_owns_the_description_a_client_reads(self, tmp_path):
        """The two have already drifted: the script's line is written for --help."""
        script = _write_marked_script(tmp_path, "my-tool")

        tools = discover_tools([tmp_path], _registered(script, description="what it is for"))

        assert tools["my-tool"]["description"] == "what it is for"

    def test_a_full_entry_answers_when_to_use_it_and_how(self, tmp_path):
        """A client has no access to the rule files those two fields render into."""
        script = _write_marked_script(tmp_path, "my-tool")

        tools = discover_tools([tmp_path], _registered(
            script, visibility=Visibility.FULL, description="what it is for",
            when_to_use="the moment arises", usage="my-tool --now"))

        assert tools["my-tool"]["description"] == (
            "what it is for\n\nWhen to use: the moment arises\n\nUsage: my-tool --now")

    def test_the_pr_subcommands_are_not_offered_beside_pr(self):
        """The case that motivated this: hidden in the registry, hidden here."""
        bin_dir = WORKBENCH_DIR / "ai" / "claude" / "bin"
        if not (bin_dir / "pr-rebase").exists():
            pytest.skip("scripts not found")

        tools = discover_tools([bin_dir])

        assert "pr" in tools
        assert {"pr-rebase", "ci-check", "pr-describe"}.isdisjoint(tools)


class TestWorkbenchToolDirs:
    """The component layout is the whole of where the server looks.

    An earlier design read the directories from a config file no install ever
    wrote, so discovery resolved to nothing and every machine ran a registered
    server exposing zero tools. Deriving them from the layout is what fixed
    that; these cases hold the derivation to the tiers it has to reach.
    """

    def test_derived_dirs_span_every_component_level(self):
        """The root, a one-level component, and a nested one."""
        dirs = discover_tool_dirs()

        assert WORKBENCH_DIR / "bin" in dirs
        assert WORKBENCH_DIR / "git" / "bin" in dirs
        assert WORKBENCH_DIR / "terminals" / "ghostty" / "bin" in dirs

    def test_every_tracked_bin_dir_is_covered(self):
        """Drift guard: a component tier deeper than the glob reaches fails here.

        The two-level glob mirrors lib/components.sh. If a bin/ ever lands at a
        depth it does not reach, its tools go silently undiscovered — so make
        that a test failure rather than an absence nobody notices.
        """
        listing = subprocess.run(
            ["git", "ls-files", "--", "*bin/*"],
            cwd=WORKBENCH_DIR,
            capture_output=True,
            text=True,
            check=True,
        )
        tracked = {
            WORKBENCH_DIR / re.sub(r"(^|/)bin/.*", r"\1bin", line)
            for line in listing.stdout.splitlines()
        }

        assert tracked <= set(discover_tool_dirs())

    def test_another_root_can_be_named(self, tmp_path):
        """bin/local/validate-tool-schema points this at a fixture tree.

        Re-deriving the layout there would be a second copy of the rule the
        server owns, which is the drift this parameter exists to prevent.
        """
        (tmp_path / "git" / "bin").mkdir(parents=True)
        (tmp_path / "bin").mkdir()

        assert discover_tool_dirs(tmp_path) == [tmp_path / "bin", tmp_path / "git" / "bin"]

    def test_an_untold_scan_discovers_the_workbench_tools(self):
        """The regression guard: a config-only server yielded no tools at all."""
        if not (WORKBENCH_DIR / "ai" / "claude" / "bin" / "pr").exists():
            pytest.skip("scripts not found")

        assert "pr" in discover_tools()

    def test_the_derived_set_is_the_only_source(self):
        """Asked with no directories, the server scans the derived ones only.

        The equality is the assertion the deleted config keys used to break: a
        second source of directories would put a tool in the left-hand side
        that naming the layout cannot produce.
        """
        assert discover_tools() == discover_tools(discover_tool_dirs())


# ── Client Transport ──────────────────────────────────────────────────────


MCP_PROTOCOL_VERSION = "2025-06-18"

# One id per turn of the conversation the module fixture drives, named so a
# failing assertion says which request it was reading the answer to.
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
    reason="the server runs under `uv run --with mcp`, as ai/claude/bin/otto-mcp-server does",
)

_ECHO_TOOL = '''\
#!/usr/bin/env python3
"""Answers with the JSON object its output schema promises."""
import json, sys

if "--tool-schema" in sys.argv:
    json.dump({
        "name": "echo-tool",
        "description": "Echo a word back",
        "input_schema": {"type": "object", "properties": {"word": {"type": "string"}}},
        "output_schema": {"type": "object", "properties": {"word": {"type": "string"}}},
    }, sys.stdout)
    sys.exit(0)

json.dump({"word": sys.argv[sys.argv.index("--word") + 1]}, sys.stdout)
'''

_PLAIN_TOOL = '''\
#!/usr/bin/env python3
"""Declares no output schema, so its stdout is prose and that is fine."""
import json, sys

if "--tool-schema" in sys.argv:
    json.dump({
        "name": "plain-tool",
        "description": "Print a line of text",
        "input_schema": {"type": "object", "properties": {}},
    }, sys.stdout)
    sys.exit(0)

print("plain output")
'''

_SILENT_TOOL = '''\
#!/usr/bin/env python3
"""Promises a JSON object and prints prose — the contract this tool breaks."""
import json, sys

if "--tool-schema" in sys.argv:
    json.dump({
        "name": "silent-tool",
        "description": "Promise JSON and print prose",
        "input_schema": {"type": "object", "properties": {}},
        "output_schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}},
    }, sys.stdout)
    sys.exit(0)

print("no json here")
'''


_INNER_TOOL = '''\
#!/usr/bin/env python3
"""Registered hidden, the way the subcommands `pr` runs are."""
import json, sys

if "--tool-schema" in sys.argv:
    json.dump({
        "name": "inner-tool",
        "description": "An implementation detail of another tool",
        "input_schema": {"type": "object", "properties": {}},
    }, sys.stdout)
    sys.exit(0)

print("inner output")
'''


@dataclass(frozen=True)
class _FixtureTool:
    """A script in the fake checkout, and the registry entry that names it."""

    name: str
    source: str
    visibility: str = "brief"


_FIXTURE_TOOLS = (
    _FixtureTool("echo-tool", _ECHO_TOOL),
    _FixtureTool("plain-tool", _PLAIN_TOOL),
    _FixtureTool("silent-tool", _SILENT_TOOL),
    _FixtureTool("inner-tool", _INNER_TOOL, visibility="hidden"),
)

# The registry the running server reads is the one on disk, so the fixture
# writes YAML rather than handing the server a dict — the file is the half of
# the path a unit test cannot reach.
_FIXTURE_REGISTRY_META = """\
meta:
  section: "Fixture Tools"
  validation: bindir
  source: bin

tools:
"""


def _fixture_registry() -> str:
    return _FIXTURE_REGISTRY_META + "".join(
        f"  - name: {tool.name}\n"
        f"    permission: false\n"
        f"    visibility: {tool.visibility}\n"
        f'    description: "The {tool.name} fixture"\n'
        for tool in _FIXTURE_TOOLS)


def _build_fake_checkout(root: Path) -> Path:
    """Lay out a throwaway checkout around the server and return its script.

    ``server.py`` derives ``WORKBENCH_DIR`` from its own resolved path, so the
    copy has to be a real file: a symlink resolves back to this repo and the
    server would discover the workbench's own tools instead of these three.

    The workbench's own Python is reached through that same derived path, so
    ``ai/lib`` is left pointing at this repo — the server imports the registry
    reader from the checkout it was copied out of.
    """
    mcps = root / "ai" / "claude" / "mcps"
    mcps.mkdir(parents=True)
    shutil.copy(WORKBENCH_DIR / "ai" / "claude" / "mcps" / "server.py", mcps / "server.py")
    (root / "ai" / "lib").symlink_to(WORKBENCH_DIR / "ai" / "lib")

    bin_dir = root / "bin"
    bin_dir.mkdir()
    for tool in _FIXTURE_TOOLS:
        script = bin_dir / tool.name
        script.write_text(tool.source)
        script.chmod(script.stat().st_mode | stat.S_IXUSR)

    (bin_dir / "registry.yml").write_text(_fixture_registry())

    return mcps / "server.py"


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
        self._stderr.append(self._proc.stderr.read())

    @property
    def stderr(self) -> str:
        self._stderr_reader.join(timeout=READER_JOIN_TIMEOUT)
        return "".join(self._stderr)

    @property
    def status(self) -> str:
        """Why the server can no longer be expected to answer."""
        if self._stdout_reader.is_alive():
            return f"it went quiet for {TRANSPORT_TIMEOUT}s"
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
    server = _ServerProcess(script)
    try:
        server.send(messages)
        frames = server.collect(expected)
        answered = sum(1 for frame in frames if "id" in frame)
        assert answered == expected, (
            f"{expected - answered} of {expected} replies never arrived — "
            f"{server.status}; server stderr:\n{server.stderr}"
        )
        server.finish()
        return _Exchange(frames=frames, stderr=server.stderr)
    finally:
        server.close()


@pytest.fixture(scope="module")
def transport(tmp_path_factory):
    """One conversation, reused by every case — each spawn costs a uv resolve."""
    script = _build_fake_checkout(tmp_path_factory.mktemp("checkout"))
    return _talk(script, [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": LIST_ID, "method": "tools/list", "params": {}},
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
