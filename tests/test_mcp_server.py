"""Tests for MCP server — discovery, arg mapping, JSON extraction, and transport."""

from __future__ import annotations

import asyncio
import json
import logging
import os
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

from conftest import git_out

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "claude" / "mcps"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import server
from server import (
    PROBE_ATTEMPTS,
    PROBE_WORKERS,
    WORKBENCH_DIR,
    ProbeFailure,
    _args_to_cli,
    _extract_json,
    _log_lost_tools,
    declares_tool_schema,
    discover_tool_dirs,
    discover_tools,
    discover_with_baseline,
    discovery_fingerprint,
    probe_tools,
    watch_for_tool_changes,
)
from tool_registry import RegistryEntry, Visibility


# ── JSON Extraction ───────────────────────────────────────────────────────


class TestExtractJson:
    def test_pure_json(self):
        text = '{"status": "ok", "count": 5}'
        result = _extract_json(text)
        assert result is not None
        _, parsed = result
        assert parsed == {"status": "ok", "count": 5}

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
        _, parsed = result
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
        _, parsed = result
        assert len(parsed) == 2

    def test_partial_json_skipped(self):
        text = textwrap.dedent("""\
            {broken
            {"valid": true}
        """)
        result = _extract_json(text)
        assert result is not None
        _, parsed = result
        assert parsed == {"valid": True}


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


# ── Spawning ──────────────────────────────────────────────────────────────


def _write_executable(path: Path, body: str) -> Path:
    path.write_text(textwrap.dedent(body))
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


# Long enough that a fixture still holding the pid is a fixture the kill
# missed, rather than one that was about to exit anyway.
GRANDCHILD_LIFETIME = 20


def _alive(pid: int) -> bool:
    """Whether *pid* names a live process, without signalling it."""
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def _wait_until_gone(pid: int, timeout: float = 5.0) -> bool:
    """Poll for *pid* to disappear; the kill is a signal, not a synchronous death."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.02)
    return False


class TestSpawnIsolation:
    """What `_run_script` guarantees that `subprocess.run` does not.

    Both spawn sites go through it, so these hold for a discovery probe and a
    tool call alike.
    """

    def test_a_script_that_reads_stdin_gets_eof(self, tmp_path):
        """The server's stdin is the JSON-RPC transport; a script must not reach it.

        A byte taken out of the stream kills the session on a parse error that
        names no tool, so the assertion is that the read returned nothing at
        all rather than that it returned something harmless.
        """
        script = _write_executable(tmp_path / "reader", """\
            #!/usr/bin/env python3
            import sys
            sys.stdout.write("READ:" + repr(sys.stdin.read()))
        """)

        result = server._run_script([str(script)], 30)

        assert result.stdout == "READ:''"

    def test_a_timed_out_script_takes_its_grandchildren_with_it(self, tmp_path):
        """`subprocess.run`'s timeout signals one process; a tool spawns agents.

        The fixture stands in for `pr review`: it backgrounds a process that
        outlives it and then hangs, so an implementation that killed only the
        direct child would leave the background one running with nothing
        holding a handle to it.
        """
        pidfile = tmp_path / "grandchild.pid"
        script = _write_executable(tmp_path / "spawner", f"""\
            #!/usr/bin/env bash
            sleep {GRANDCHILD_LIFETIME} &
            echo "$!" > {str(pidfile)!r}
            sleep {GRANDCHILD_LIFETIME}
        """)

        with pytest.raises(subprocess.TimeoutExpired):
            server._run_script([str(script)], 1.0)

        grandchild = int(pidfile.read_text())
        assert _wait_until_gone(grandchild), (
            f"pid {grandchild} outlived the tool call that spawned it")

    def test_the_direct_child_dies_too(self, tmp_path):
        script = _write_executable(tmp_path / "sleeper", f"""\
            #!/usr/bin/env bash
            echo "$$" > {str(tmp_path / 'child.pid')!r}
            sleep {GRANDCHILD_LIFETIME}
        """)

        with pytest.raises(subprocess.TimeoutExpired):
            server._run_script([str(script)], 1.0)

        assert _wait_until_gone(int((tmp_path / "child.pid").read_text()))

    def test_it_answers_with_what_the_script_printed(self, tmp_path):
        """The callers read `.returncode`, `.stdout` and `.stderr` off the result."""
        script = _write_executable(tmp_path / "talker", """\
            #!/usr/bin/env bash
            echo out
            echo err >&2
            exit 3
        """)

        result = server._run_script([str(script)], 30)

        assert (result.returncode, result.stdout, result.stderr) == (3, "out\n", "err\n")

    def test_a_probe_of_a_script_that_reads_stdin_still_answers(self, tmp_path):
        """The probe is the likeliest reader of all.

        A script that does not recognise `--tool-schema` falls through to its
        real work, and that work may read stdin — so this is the path where an
        inherited transport is most likely to be consumed.
        """
        script = _write_executable(tmp_path / "hungry-tool", """\
            #!/usr/bin/env python3
            import json, sys
            data = sys.stdin.read()
            if "--tool-schema" in sys.argv:
                json.dump({"name": "hungry-tool", "input_schema": {}, "read": data},
                          sys.stdout)
        """)

        result = server.probe_tool(script)

        assert result.ok, result.reason
        assert result.schema["read"] == ""


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


def _write_marked_script(directory: Path, name: str, tool_name: str = "") -> Path:
    """An executable at *name* answering the probe with a minimal schema.

    The schema names *tool_name* when given, which is how two scripts are made
    to claim one tool — the filename and the name a script answers with are
    independent, and discovery keys on the latter.
    """
    tool_name = tool_name or name
    document = {"name": tool_name, "description": f"{tool_name}, as the script tells it",
                "input_schema": {"type": "object", "properties": {}}}
    directory.mkdir(parents=True, exist_ok=True)
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
            / "ai" / "bin" / "build-otto-ai-tools-tarball"
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
            / "ai" / "bin" / "otto-mcp-server"
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
        bin_dir = WORKBENCH_DIR / "ai" / "bin"
        if not (bin_dir / "pr").exists():
            pytest.skip("scripts not found")

        tools = discover_tools([bin_dir])

        assert "pr" in tools
        assert "input_schema" in tools["pr"]

    def test_pr_declares_no_output_schema(self):
        """Declaring one made eight of nine subcommands come back `isError`.

        A tool that advertises an output schema has to answer with a JSON
        object every time, and only `pr status` prints one — not even that one
        before a state file exists. The honest contract is none until the
        schema is per-subcommand.
        """
        bin_dir = WORKBENCH_DIR / "ai" / "bin"
        if not (bin_dir / "pr").exists():
            pytest.skip("scripts not found")

        assert discover_tools([bin_dir])["pr"].get("output_schema") is None


class TestDuplicateNames:
    """Two scripts answering to one name.

    Runtime keeps the first the scan reached, because raising in discovery
    would run in the watcher thread as well as at startup — one ambiguity
    would either take the server down or stop re-discovery for the session.
    What it must not do is stay quiet: which of the two a client reaches is
    decided by directory order. `bin/local/validate-tool-schema` is where the
    collision fails.
    """

    def test_the_first_script_scanned_wins(self, tmp_path):
        first = _write_marked_script(tmp_path / "a", "alpha", tool_name="shared")
        second = _write_marked_script(tmp_path / "b", "beta", tool_name="shared")

        tools = discover_tools([tmp_path / "a", tmp_path / "b"],
                               _registered(first, second))

        assert list(tools) == ["shared"]
        assert tools["shared"]["_script"] == str(first)

    def test_the_loser_is_logged_at_error_naming_both(self, tmp_path, caplog):
        first = _write_marked_script(tmp_path / "a", "alpha", tool_name="shared")
        second = _write_marked_script(tmp_path / "b", "beta", tool_name="shared")

        with caplog.at_level(logging.ERROR, logger="otto-mcp"):
            discover_tools([tmp_path / "a", tmp_path / "b"], _registered(first, second))

        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(errors) == 1
        assert str(first) in errors[0].getMessage()
        assert str(second) in errors[0].getMessage()


# ── Probing ───────────────────────────────────────────────────────────────


# The bound a case runs under when every tool in it is meant to time out.
# Nothing there turns on a fixture being scheduled inside the bound — a script
# the kernel has not started yet and a script sleeping far past it both produce
# the timeout the case is about — so it can stay short enough that a breach
# costs tenths of a second.
#
# A case where some tool must *answer* takes the shipped bound instead. An
# answer does turn on the fixture being scheduled, and half a second is not
# reliably enough to fork and start a shell on a machine running this suite in
# parallel. Pinning such a case short reproduced the defect this section exists
# to check for: the machine decided the result, not the tool.
PROBE_BOUND = 0.5

# What a fixture runs when it must not answer this attempt. `exec` so the sleep
# takes over the pid the prober kills — a sleep left running under a killed
# parent outlives its case by half a minute, and that is load the next case pays.
NEVER_ANSWERS = "exec sleep 30\n"

# Sleeping tools enough that a serial round would be plainly slower than a
# concurrent one, and fewer than PROBE_WORKERS so they all go out together.
SLOW_TOOLS = 6

assert SLOW_TOOLS <= PROBE_WORKERS, "the cases below assume one round holds them all"


def _attempts_of(script: Path) -> Path:
    """The file a fixture tool appends a byte to each time it is run.

    Only a fixture given room to answer can be trusted to have written it: a
    probe that breaches its bound is SIGKILLed, and the kill can land before
    bash has reached the script's first line. Cases counting how many times a
    script was probed use the ``probes`` fixture, which counts on the side of
    the fence that cannot be killed.
    """
    return script.parent / f"{script.name}.attempts"


def _tool_body(name: str) -> str:
    """The line that answers the probe with a minimal schema for *name*."""
    document = {"name": name, "input_schema": {"type": "object", "properties": {}}}
    return f"printf '%s' '{json.dumps(document)}'\n"


def _write_probe_fixture(directory: Path, name: str, middle: str) -> Path:
    """A marked script that records the attempt, runs *middle*, then answers.

    Real subprocesses rather than a patched ``subprocess.run``: a probe that
    outruns its bound is what these cases are about, and a sleeping script is
    the honest way to produce one.
    """
    script = directory / name
    script.write_text("#!/bin/bash\n"
                      "# answers --tool-schema\n"
                      f"printf 'x' >> '{_attempts_of(script)}'\n"
                      f"{middle}"
                      f"{_tool_body(name)}")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


def _write_sleeping_tool(directory: Path, name: str) -> Path:
    """A marked script that never answers inside any bound worth waiting."""
    return _write_probe_fixture(directory, name, NEVER_ANSWERS)


def _write_flaky_tool(directory: Path, name: str) -> Path:
    """Outruns the bound the first time it is run, and answers after that.

    Which attempt it is on comes from the stamp file, so it only tells the truth
    under a bound its first line is certain to be reached inside — the case
    using it takes the shipped bound rather than PROBE_BOUND.
    """
    script = directory / name
    return _write_probe_fixture(
        directory, name,
        f"[ \"$(wc -c < '{_attempts_of(script)}' | tr -d ' ')\" -lt 2 ] "
        f"&& {NEVER_ANSWERS}")


def _write_dawdling_tool(directory: Path, name: str, pause: float) -> Path:
    """Answers, but only after *pause* seconds — so completion order is known."""
    return _write_probe_fixture(directory, name, f"sleep {pause}\n")


@pytest.fixture
def short_probe_bound(monkeypatch):
    """The server's bound, shortened so a breach costs the suite tenths.

    Only for a case where every tool is meant to time out — see PROBE_BOUND.
    """
    monkeypatch.setattr(server, "DISCOVERY_TIMEOUT", PROBE_BOUND)


@pytest.fixture
def probes(monkeypatch):
    """Every script the prober spawned a probe for, in the order it did.

    Counted here rather than by the fixture scripts themselves: a timed-out
    probe is SIGKILLed, and on a busy machine that lands before the script has
    recorded anything, so a stamp the child writes undercounts exactly the
    attempts these cases exist to check. The real probe still runs.
    """
    spawned: list[Path] = []
    probe = server.probe_tool

    def counting(script: Path):
        spawned.append(script)
        return probe(script)

    monkeypatch.setattr(server, "probe_tool", counting)
    return spawned


class TestProbeFailure:
    """A wedged probe and a wrong answer are different problems.

    Both used to arrive as one ``reason`` string off one ``except`` clause, so
    a tool dropped because the machine had nothing left to schedule read
    exactly like a tool whose author broke it.
    """

    def test_a_probe_that_ran_out_of_time_says_so(self, tmp_path, short_probe_bound):
        script = _write_sleeping_tool(tmp_path, "sleeping-tool")

        result = probe_tools([script])[0]

        assert result.failure is ProbeFailure.TIMED_OUT
        assert result.timed_out is True
        assert "did not answer within" in result.reason

    def test_a_non_zero_exit_is_a_broken_tool_not_a_slow_one(self, tmp_path):
        script = tmp_path / "broken-tool"
        script.write_text("#!/bin/bash\n# answers --tool-schema\nexit 3\n")
        script.chmod(script.stat().st_mode | stat.S_IXUSR)

        result = probe_tools([script])[0]

        assert result.failure is ProbeFailure.BROKEN
        assert result.timed_out is False

    def test_malformed_json_is_a_broken_tool(self, tmp_path):
        script = tmp_path / "garbled-tool"
        script.write_text("#!/bin/bash\n# answers --tool-schema\necho not json\n")
        script.chmod(script.stat().st_mode | stat.S_IXUSR)

        result = probe_tools([script])[0]

        assert result.failure is ProbeFailure.BROKEN
        assert "JSONDecodeError" in result.reason

    def test_a_schema_missing_a_key_is_a_broken_tool(self, tmp_path):
        script = tmp_path / "partial-tool"
        script.write_text(textwrap.dedent("""\
            #!/usr/bin/env python3
            import json, sys
            if "--tool-schema" in sys.argv:
                json.dump({"name": "partial-tool"}, sys.stdout)
                sys.exit(0)
        """))
        script.chmod(script.stat().st_mode | stat.S_IXUSR)

        assert probe_tools([script])[0].failure is ProbeFailure.BROKEN

    def test_an_unmarked_script_is_neither(self, tmp_path):
        """Nothing ran, so there is no tool here to call slow or broken."""
        script = _write_destructive_script(tmp_path)

        assert probe_tools([script])[0].failure is ProbeFailure.UNMARKED

    def test_a_tool_that_answers_carries_no_failure(self, tmp_path):
        script = _write_marked_script(tmp_path, "my-tool")

        result = probe_tools([script])[0]

        assert result.ok is True
        assert result.failure is None


class TestConcurrentProbing:
    """Candidates go out together, which is what pays for a generous bound."""

    def test_results_come_back_in_the_order_they_were_asked_for(self, tmp_path):
        """Two runs over the same tree must not disagree about nothing.

        The pauses run counter to the order asked for, so a list assembled as
        the probes finished would come back reversed.
        """
        scripts = [_write_dawdling_tool(tmp_path, f"tool-{i}", pause)
                   for i, pause in enumerate((0.4, 0.3, 0.2, 0.1, 0.0))]

        results = probe_tools(scripts)

        assert [r.script for r in results] == scripts
        assert all(r.ok for r in results)

    def test_the_wait_is_one_probes_and_not_one_per_tool(self, tmp_path,
                                                         short_probe_bound):
        """The bound can only be generous if startup does not pay it per tool.

        Probed one at a time these would cost SLOW_TOOLS bounds, twice over
        with the retry. Together they cost two.
        """
        scripts = [_write_sleeping_tool(tmp_path, f"sleeping-{i}")
                   for i in range(SLOW_TOOLS)]

        started = time.monotonic()
        results = probe_tools(scripts)
        elapsed = time.monotonic() - started

        assert all(r.timed_out for r in results)
        assert elapsed < SLOW_TOOLS * PROBE_BOUND, (
            f"{SLOW_TOOLS} probes of {PROBE_BOUND}s took {elapsed:.1f}s — "
            f"that is a serial round, not a concurrent one")

    def test_no_thread_is_asked_for_when_there_is_nothing_to_probe(self):
        assert probe_tools([]) == []


class TestProbeRetry:
    """A probe that lost a race with the scheduler gets one more chance.

    Re-discovery runs when the scanned directories change, so a tool dropped at
    startup is missing until somebody edits the tree — not until the next poll.
    That is what makes a second attempt worth its cost, and the cost is one more
    bound for the round rather than one per tool.
    """

    def test_a_tool_that_answers_on_the_second_try_is_discovered(
            self, tmp_path, probes):
        """The shipped bound, because the second attempt has to be able to answer.

        It is also the one case that pays the bound in full — the first attempt
        sleeps through it — which is what a real transient stall costs.
        """
        script = _write_flaky_tool(tmp_path, "flaky-tool")

        result = probe_tools([script])[0]

        assert result.ok is True
        assert probes == [script] * PROBE_ATTEMPTS

    def test_a_tool_that_never_answers_is_run_the_attempt_count_and_no_more(
            self, tmp_path, short_probe_bound, probes):
        script = _write_sleeping_tool(tmp_path, "sleeping-tool")

        assert probe_tools([script])[0].timed_out is True
        assert probes == [script] * PROBE_ATTEMPTS

    def test_a_tool_that_answered_wrongly_is_not_run_again(self, tmp_path, probes):
        """Re-running it would cost the same wait to be told the same thing."""
        script = _write_probe_fixture(tmp_path, "broken-tool", "exit 3\n")

        assert probe_tools([script])[0].failure is ProbeFailure.BROKEN
        assert probes == [script]


class TestTimeoutIsReportedApart:
    """What an operator reading the server's stderr is sent to look at."""

    def test_a_timed_out_probe_is_an_error_that_blames_the_machine(
            self, tmp_path, short_probe_bound, caplog):
        script = _write_sleeping_tool(tmp_path, "sleeping-tool")

        with caplog.at_level(logging.WARNING, logger="otto-mcp"):
            assert discover_tools([tmp_path], _registered(script)) == {}

        dropped = [r for r in caplog.records if "Not offering" in r.getMessage()]
        assert len(dropped) == 1
        assert dropped[0].levelno == logging.ERROR
        assert "loaded machine or a wedged script" in dropped[0].getMessage()
        assert "sleeping-tool" in dropped[0].getMessage()

    def test_a_broken_tool_stays_a_warning_about_the_tool(self, tmp_path, caplog):
        script = _write_probe_fixture(tmp_path, "broken-tool", "exit 3\n")

        with caplog.at_level(logging.WARNING, logger="otto-mcp"):
            assert discover_tools([tmp_path], _registered(script)) == {}

        skipped = [r for r in caplog.records if "Skipping" in r.getMessage()]
        assert len(skipped) == 1
        assert skipped[0].levelno == logging.WARNING
        assert "exited 3" in skipped[0].getMessage()

    def test_a_slow_tool_does_not_stop_the_others_being_offered(
            self, tmp_path, caplog):
        """One dropped tool is one tool, not a scan that gave up.

        The shipped bound, because the round holds a tool that has to answer and
        the two share one bound — shortening it to hurry the sleeper along is
        how the quick tool starts timing out too.
        """
        slow = _write_sleeping_tool(tmp_path, "sleeping-tool")
        quick = _write_marked_script(tmp_path, "my-tool")

        with caplog.at_level(logging.ERROR, logger="otto-mcp"):
            tools = discover_tools([tmp_path], _registered(slow, quick))

        assert set(tools) == {"my-tool"}
        assert "sleeping-tool" in caplog.text


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
        bin_dir = WORKBENCH_DIR / "ai" / "bin"
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
        listing = git_out(WORKBENCH_DIR, "ls-files", "--", "*bin/*")
        tracked = {
            WORKBENCH_DIR / re.sub(r"(^|/)bin/.*", r"\1bin", line)
            for line in listing.splitlines()
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
        if not (WORKBENCH_DIR / "ai" / "bin" / "pr").exists():
            pytest.skip("scripts not found")

        assert "pr" in discover_tools()

    def test_the_derived_set_is_the_only_source(self):
        """Asked with no directories, the server scans the derived ones only.

        The equality is the assertion the deleted config keys used to break: a
        second source of directories would put a tool in the left-hand side
        that naming the layout cannot produce.
        """
        assert discover_tools() == discover_tools(discover_tool_dirs())


# ── Re-discovery ──────────────────────────────────────────────────────────


def _fingerprint_tree(root: Path) -> Path:
    """A checkout with one registered, marked script in its bin/."""
    bin_dir = root / "bin"
    bin_dir.mkdir()
    script = _write_marked_script(bin_dir, "watched-tool")
    (bin_dir / "registry.yml").write_text(textwrap.dedent("""\
        meta:
          validation: bindir
          source: bin

        tools:
          - name: watched-tool
            permission: false
            visibility: brief
            description: "The watched tool"
    """))
    return script


class TestDiscoveryFingerprint:
    """What has to move before the server pays for a re-scan."""

    def test_an_untouched_tree_fingerprints_the_same_twice(self, tmp_path):
        _fingerprint_tree(tmp_path)

        assert discovery_fingerprint(tmp_path) == discovery_fingerprint(tmp_path)

    def test_an_edited_script_changes_it(self, tmp_path):
        script = _fingerprint_tree(tmp_path)
        before = discovery_fingerprint(tmp_path)

        script.write_text(script.read_text() + "\n# a new line\n")

        assert discovery_fingerprint(tmp_path) != before

    def test_a_new_file_in_a_scanned_directory_changes_it(self, tmp_path):
        _fingerprint_tree(tmp_path)
        before = discovery_fingerprint(tmp_path)

        _write_marked_script(tmp_path / "bin", "later-tool")

        assert discovery_fingerprint(tmp_path) != before

    def test_a_deleted_script_changes_it(self, tmp_path):
        script = _fingerprint_tree(tmp_path)
        before = discovery_fingerprint(tmp_path)

        script.unlink()

        assert discovery_fingerprint(tmp_path) != before

    def test_an_edited_registry_changes_it(self, tmp_path):
        """A tool withdrawn by going hidden touches no line of its script."""
        _fingerprint_tree(tmp_path)
        registry = tmp_path / "bin" / "registry.yml"
        before = discovery_fingerprint(tmp_path)

        registry.write_text(registry.read_text().replace("brief", "hidden"))

        assert discovery_fingerprint(tmp_path) != before

    def test_making_a_script_executable_changes_it(self, tmp_path):
        """chmod +x is the whole of what turns a file into a candidate."""
        _fingerprint_tree(tmp_path)
        plain = tmp_path / "bin" / "not-yet-a-tool"
        plain.write_text("#!/bin/sh\n# --tool-schema\n")
        before = discovery_fingerprint(tmp_path)

        plain.chmod(plain.stat().st_mode | stat.S_IXUSR)

        assert discovery_fingerprint(tmp_path) != before

    def test_a_new_component_directory_changes_it(self, tmp_path):
        _fingerprint_tree(tmp_path)
        before = discovery_fingerprint(tmp_path)

        (tmp_path / "editors" / "zed" / "bin").mkdir(parents=True)

        assert discovery_fingerprint(tmp_path) != before


class TestDiscoverWithBaseline:
    """The baseline has to describe a tree no newer than the scan it pairs with."""

    def test_the_scan_it_returns_is_the_one_it_ran(self, tmp_path, monkeypatch):
        monkeypatch.setattr(server, "WORKBENCH_DIR", tmp_path)
        _fingerprint_tree(tmp_path)

        discovered = discover_with_baseline()

        assert set(discovered.tools) == {"watched-tool"}
        assert discovered.fingerprint == discovery_fingerprint(tmp_path)

    def test_a_tool_landing_during_the_scan_still_looks_new_afterwards(
            self, tmp_path, monkeypatch):
        """Stamped after the scan, that tool is in the baseline and never arrives.

        No poll sees the file appear, so the client is offered the startup list
        for the rest of the session — and the client owns this process, so
        nothing outside it can restart the server either.
        """
        monkeypatch.setattr(server, "WORKBENCH_DIR", tmp_path)
        _fingerprint_tree(tmp_path)

        def scan_while_a_tool_lands(dirs=None, registry=None):
            _write_marked_script(tmp_path / "bin", "later-tool")
            return {}

        monkeypatch.setattr(server, "discover_tools", scan_while_a_tool_lands)

        discovered = discover_with_baseline()

        assert discovered.fingerprint != discovery_fingerprint(tmp_path)


# The bound on a case whose *done* signal never arrives, not the wait a passing
# case makes: every case below names a signal, so this is only how long a
# regression takes to fail.
WATCH_SETTLE = 1.0

# Enough polls to have re-scanned had the fingerprint moved. A case asserting
# that nothing happened has no event to wait for, so it waits for the watcher to
# have had the chance.
SETTLED_POLLS = 3


class _Recorder:
    """Stands in for discovery, answering whatever the case sets up next.

    Each list holds the answers in order and repeats its last one forever, so a
    watcher polling on a zero interval settles instead of cycling.
    """

    def __init__(self, fingerprints, tool_sets, failures=0):
        self.fingerprints = list(fingerprints)
        self.tool_sets = list(tool_sets)
        self.failures = failures
        self.polls = 0
        self.scans = 0
        self.announcements = 0

    def _next(self, answers):
        return answers.pop(0) if len(answers) > 1 else answers[0]

    def fingerprint(self, root=None):
        self.polls += 1
        return self._next(self.fingerprints)

    def discover(self, dirs=None, registry=None):
        self.scans += 1
        if self.scans <= self.failures:
            raise OSError("a scan that could not finish")
        return self._next(self.tool_sets)

    async def notify(self):
        self.announcements += 1


def _schema(name: str) -> dict:
    return {"name": name, "input_schema": {}, "_script": f"/bin/{name}"}


async def _watch_until(recorder, tools, ready, baseline, done) -> None:
    """Run the watcher until *done*, then stop it the way a cancel would."""
    task = asyncio.create_task(
        watch_for_tool_changes(tools, recorder.notify, ready, baseline, interval=0))
    deadline = time.monotonic() + WATCH_SETTLE
    while not done() and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


class TestWatchForToolChanges:
    """The poll that makes a merged tool usable without a client restart."""

    def _run(self, monkeypatch, fingerprints, tool_sets, tools,
             done=None, ready_now=True, failures=0):
        # The first entry is the baseline the startup scan was stamped with, the
        # way create_server hands it over; the rest are what the polls read.
        baseline, *polled = fingerprints
        recorder = _Recorder(polled or [baseline], tool_sets, failures=failures)
        monkeypatch.setattr(server, "discovery_fingerprint", recorder.fingerprint)
        monkeypatch.setattr(server, "discover_tools", recorder.discover)
        ready = asyncio.Event()
        if ready_now:
            ready.set()
        settled = done or (lambda _: False)
        asyncio.run(_watch_until(recorder, tools, ready, baseline,
                                 lambda: settled(recorder)))
        return recorder

    def test_a_still_tree_is_never_re_scanned(self, monkeypatch):
        """The stat sweep is the cheap half; the point is not paying for the rest."""
        tools = {"a": _schema("a")}

        recorder = self._run(monkeypatch, ["same"], [tools], tools,
                             done=lambda r: r.polls >= SETTLED_POLLS)

        assert recorder.scans == 0
        assert recorder.announcements == 0

    def test_a_new_tool_is_offered_and_announced(self, monkeypatch):
        tools = {"a": _schema("a")}
        grown = {"a": _schema("a"), "b": _schema("b")}

        recorder = self._run(monkeypatch, ["before", "after"], [grown], tools,
                             done=lambda r: r.announcements)

        assert set(tools) == {"a", "b"}
        assert recorder.announcements == 1

    def test_the_handlers_read_the_same_dict(self, monkeypatch):
        """Rebinding would leave them on the snapshot taken at startup."""
        tools = {"a": _schema("a")}
        held = tools

        self._run(monkeypatch, ["before", "after"], [{"b": _schema("b")}], tools,
                  done=lambda r: r.announcements)

        assert held is tools
        assert set(held) == {"b"}

    def test_a_change_that_leaves_the_tools_alone_announces_nothing(self, monkeypatch):
        """Touching a README under a scanned directory is not a tool change."""
        tools = {"a": _schema("a")}

        recorder = self._run(monkeypatch, ["before", "after"], [dict(tools)], tools,
                             done=lambda r: r.scans and r.polls >= SETTLED_POLLS)

        assert recorder.scans == 1
        assert recorder.announcements == 0

    def test_a_failed_round_costs_only_that_round(self, monkeypatch, caplog):
        """Letting the failure out kills the poll, and nobody is holding the task.

        The client would then show its startup list for the rest of the session
        with nothing said about why.
        """
        tools = {"a": _schema("a")}
        grown = {"a": _schema("a"), "b": _schema("b")}

        with caplog.at_level(logging.ERROR, logger="otto-mcp"):
            recorder = self._run(monkeypatch, ["before", "after"], [grown], tools,
                                 failures=1, done=lambda r: r.announcements)

        assert set(tools) == {"a", "b"}
        assert recorder.announcements == 1
        assert "a scan that could not finish" in caplog.text

    def test_nothing_is_announced_before_a_client_has_spoken(self, monkeypatch):
        """A notification ahead of the handshake reaches a client not yet listening."""
        tools = {"a": _schema("a")}

        recorder = self._run(monkeypatch, ["before", "after"], [{"b": _schema("b")}],
                             tools, done=lambda r: set(tools) == {"b"}, ready_now=False)

        assert set(tools) == {"b"}
        assert recorder.announcements == 0


class TestLostTools:
    """A tool that worked until this scan is a regression, not a work in progress."""

    def test_a_vanished_script_is_an_error_naming_the_tool(self, tmp_path, caplog):
        before = {"gone-tool": {"name": "gone-tool", "_script": str(tmp_path / "gone-tool")}}

        with caplog.at_level(logging.ERROR, logger="otto-mcp"):
            _log_lost_tools(before, {})

        assert "gone-tool" in caplog.text
        assert "its script is gone" in caplog.text

    def test_a_broken_script_is_an_error_carrying_the_probe_reason(self, tmp_path, caplog):
        script = tmp_path / "broken-tool"
        script.write_text("#!/bin/sh\n# --tool-schema\nexit 3\n")
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        before = {"broken-tool": {"name": "broken-tool", "_script": str(script)}}

        with caplog.at_level(logging.ERROR, logger="otto-mcp"):
            _log_lost_tools(before, {})

        assert "broken-tool" in caplog.text
        assert "exited 3" in caplog.text

    def test_a_tool_that_still_answers_says_the_registry_withdrew_it(self, tmp_path, caplog):
        script = _write_marked_script(tmp_path, "hidden-tool")
        before = {"hidden-tool": {"name": "hidden-tool", "_script": str(script)}}

        with caplog.at_level(logging.ERROR, logger="otto-mcp"):
            _log_lost_tools(before, {})

        assert "no longer offers it" in caplog.text

    def test_a_tool_that_survived_the_scan_is_not_reported(self, caplog):
        tools = {"a": _schema("a")}

        with caplog.at_level(logging.ERROR, logger="otto-mcp"):
            _log_lost_tools(tools, tools)

        assert caplog.text == ""


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
    visibility: Visibility = Visibility.BRIEF


_FIXTURE_TOOLS = (
    _FixtureTool("echo-tool", _ECHO_TOOL),
    _FixtureTool("plain-tool", _PLAIN_TOOL),
    _FixtureTool("silent-tool", _SILENT_TOOL),
    _FixtureTool("inner-tool", _INNER_TOOL, visibility=Visibility.HIDDEN),
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
        f"    visibility: {tool.visibility.value}\n"
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
"""Merged while the client was already connected."""
import json, sys

if "--tool-schema" in sys.argv:
    json.dump({
        "name": "later-tool",
        "description": "Arrived after the handshake",
        "input_schema": {"type": "object", "properties": {}},
    }, sys.stdout)
    sys.exit(0)

print("later output")
'''

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
    only place the whole chain runs: a real script appearing in a scanned
    directory, the poll noticing, and a frame reaching the client that did not
    ask for it.
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
