"""Tests for MCP server — discovery, arg mapping, JSON extraction, and transport."""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import shutil
import stat
import subprocess
import sys
import textwrap
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "claude" / "mcps"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import core.proc
import server
from server import (
    WORKBENCH_DIR,
    _args_to_cli,
    _described,
    _extract_json,
    _log_lost_tools,
    _tool_schema,
    discover_tools,
    discover_with_baseline,
    discovery_fingerprint,
    watch_for_tool_changes,
)
from config.tool_registry import RegistryEntry, Visibility


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


class TestSpawnIsolation:
    """What `_run_script` asks `proc.run` for that a plain spawn does not give.

    Tool calls go through it. `proc.run` owns the mechanism and
    `tests/proc_test.py` covers it in its own right; what is asserted here is
    that this server asks for it.
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

    def test_it_asks_for_the_whole_group_to_be_killed(self):
        """A tool spawns agents — `pr review` is the case.

        Signalling only the direct child leaves them running against the
        account with nothing holding a handle to them. That the group kill
        reaches a grandchild is `proc.run`'s guarantee and is tested there;
        what this asserts is that the server asks for it on the tool-call path.
        """
        with mock.patch.object(core.proc, "run") as run:
            server._run_script(["/bin/true"], 30)

        assert run.call_args.kwargs["kill_process_group"] is True

    def test_a_timed_out_script_comes_back_as_a_result(self, tmp_path):
        """Both call sites branch on the code; neither has an except clause."""
        script = _write_executable(tmp_path / "sleeper", """\
            #!/usr/bin/env bash
            sleep 5
        """)

        result = server._run_script([str(script)], 0.3)

        assert result.returncode == core.proc.TIMEOUT_RETURNCODE

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


# ── Tool Discovery ────────────────────────────────────────────────────────


def _entry(script: Path, visibility: Visibility = Visibility.BRIEF,
           description: str = "", when_to_use: str = "",
           usage: str = "") -> RegistryEntry:
    return RegistryEntry(
        name=script.name,
        description=description or f"the {script.name} tool",
        visibility=visibility,
        when_to_use=when_to_use,
        usage=usage,
    )


def _registered(*scripts: Path, visibility: Visibility = Visibility.BRIEF,
                description: str = "", when_to_use: str = "",
                usage: str = "") -> dict[Path, RegistryEntry]:
    """A registry offering each of *scripts* under an entry of its own.

    Discovery takes the registry as an argument so a case can describe a tree
    the checkout does not have. The real mapping is
    tests/tool_registry_test.py's subject.
    """
    return {script: _entry(script, visibility=visibility, description=description,
                           when_to_use=when_to_use, usage=usage)
            for script in scripts}


def _schema_for(script: Path, name: str = "", description: str = "from the schema",
                **extra) -> dict:
    """A schema `_tool_schema` would return for *script* — no subprocess."""
    document = {
        "name": name or script.name,
        "description": description,
        "input_schema": {"type": "object", "properties": {}},
        "_script": str(script),
    }
    document.update(extra)
    return document


class TestToolSchema:
    """The import that replaced the probe. None is how a non-tool is declined."""

    def test_pr_is_the_one_script_that_has_a_schema(self):
        schema = _tool_schema(Path("/does/not/need/to/exist/pr"))

        assert schema is not None
        assert schema["name"] == "pr"
        assert schema["_script"].endswith("/pr")
        assert "input_schema" in schema

    def test_a_registered_non_tool_returns_none_rather_than_raising(self):
        """wt and otto-log are in the registry; they are not MCP tools."""
        assert _tool_schema(Path("/usr/bin/wt")) is None
        assert _tool_schema(Path("/usr/bin/otto-log")) is None


class TestDescribed:
    """The registry's words win over whatever the schema carried."""

    def test_the_registry_description_replaces_the_schema_line(self):
        schema = _schema_for(Path("/bin/pr"), description="what --help says")
        entry = _entry(Path("/bin/pr"), description="what a client should read")

        described = _described(schema, entry)

        assert described["description"] == "what a client should read"
        assert described["name"] == "pr"
        assert schema["description"] == "what --help says"

    def test_a_full_entry_answers_when_to_use_it_and_how(self):
        """A client has no access to the rule files those two fields render into."""
        schema = _schema_for(Path("/bin/pr"))
        entry = _entry(
            Path("/bin/pr"), visibility=Visibility.FULL,
            description="what it is for", when_to_use="the moment arises",
            usage="pr --now")

        assert _described(schema, entry)["description"] == (
            "what it is for\n\nWhen to use: the moment arises\n\nUsage: pr --now")


class TestDiscovery:
    """What a registry dict turns into a served set.

    Every case names its registry explicitly. Defaulting to the checkout is
    TestWorkbenchDiscovery's subject — leaving that in would make each
    "nothing was discovered" assertion also assert that the workbench ships
    no tools.
    """

    def test_discovers_an_offered_pr(self, monkeypatch):
        script = Path("/bin/pr")
        monkeypatch.setattr(server, "_tool_schema",
                            lambda s: _schema_for(s, name="pr"))

        tools = discover_tools(_registered(script))

        assert "pr" in tools
        assert tools["pr"]["input_schema"] == {"type": "object", "properties": {}}
        assert tools["pr"]["_script"] == str(script)

    def test_an_empty_registry_offers_nothing(self):
        assert discover_tools({}) == {}

    def test_a_hidden_entry_is_not_offered(self, monkeypatch):
        script = Path("/bin/pr")
        monkeypatch.setattr(server, "_tool_schema",
                            lambda s: _schema_for(s, name="pr"))

        tools = discover_tools(_registered(script, visibility=Visibility.HIDDEN))

        assert tools == {}

    def test_a_hidden_entry_is_not_even_asked_for_a_schema(self, monkeypatch):
        """visibility is decided before the import, so a hidden pr is never read."""
        asked: list[Path] = []

        def tracking(script: Path):
            asked.append(script)
            return _schema_for(script, name="pr")

        monkeypatch.setattr(server, "_tool_schema", tracking)

        tools = discover_tools(
            _registered(Path("/bin/pr"), visibility=Visibility.HIDDEN))

        assert tools == {}
        assert asked == []

    def test_a_registered_non_tool_is_skipped_rather_than_raising(self):
        """Most registered scripts are not tools — wt, otto-log, the scans."""
        registry = _registered(Path("/usr/bin/wt"), Path("/usr/bin/otto-log"))

        assert discover_tools(registry) == {}

    def test_discovery_does_not_spawn_a_script(self, monkeypatch):
        """The probe is gone: an offered pr is an import, not a subprocess."""
        monkeypatch.setattr(server, "_tool_schema",
                            lambda s: _schema_for(s, name="pr"))
        with mock.patch.object(server, "_run_script") as run:
            discover_tools(_registered(Path("/bin/pr")))

        run.assert_not_called()

    def test_the_description_comes_from_the_registry_not_the_schema(self, monkeypatch):
        script = Path("/bin/pr")
        monkeypatch.setattr(
            server, "_tool_schema",
            lambda s: _schema_for(s, name="pr", description="schema line"))

        tools = discover_tools(_registered(script, description="registry line"))

        assert tools["pr"]["description"] == "registry line"

    def test_a_full_entry_is_what_the_client_reads(self, monkeypatch):
        script = Path("/bin/pr")
        monkeypatch.setattr(
            server, "_tool_schema",
            lambda s: _schema_for(s, name="pr", description="schema line"))

        tools = discover_tools(_registered(
            script, visibility=Visibility.FULL, description="what it is for",
            when_to_use="the moment arises", usage="pr --now"))

        assert tools["pr"]["description"] == (
            "what it is for\n\nWhen to use: the moment arises\n\nUsage: pr --now")


class TestWorkbenchDiscovery:
    """The running checkout, with no registry argument."""

    def test_the_workbench_offers_pr(self):
        if not (WORKBENCH_DIR / "ai" / "bin" / "pr").exists():
            pytest.skip("scripts not found")

        tools = discover_tools()

        assert "pr" in tools
        assert "input_schema" in tools["pr"]

    def test_pr_declares_no_output_schema(self):
        """Declaring one made eight of nine subcommands come back `isError`.

        A tool that advertises an output schema has to answer with a JSON
        object every time, and only `pr status` prints one — not even that one
        before a state file exists. The honest contract is none until the
        schema is per-subcommand.
        """
        if not (WORKBENCH_DIR / "ai" / "bin" / "pr").exists():
            pytest.skip("scripts not found")

        assert discover_tools()["pr"].get("output_schema") is None

    def test_the_pr_subcommands_are_not_offered_beside_pr(self):
        """The case that motivated visibility: hidden in the registry, hidden here."""
        if not (WORKBENCH_DIR / "ai" / "bin" / "pr-rebase").exists():
            pytest.skip("scripts not found")

        tools = discover_tools()

        assert "pr" in tools
        assert {"pr-rebase", "ci-check", "pr-describe"}.isdisjoint(tools)


class TestDuplicateNames:
    """Two entries answering to one name.

    Runtime keeps the first the scan reached, because raising in discovery
    would run in the watcher thread as well as at startup — one ambiguity
    would either take the server down or stop re-discovery for the session.
    What it must not do is stay quiet: which of the two a client reaches is
    decided by registry order. `bin/local/validate-registries` is where the
    collision fails.
    """

    def test_the_first_script_scanned_wins(self, monkeypatch):
        first = Path("/a/pr")
        second = Path("/b/pr")
        monkeypatch.setattr(server, "_tool_schema",
                            lambda s: _schema_for(s, name="pr"))

        tools = discover_tools(_registered(first, second))

        assert list(tools) == ["pr"]
        assert tools["pr"]["_script"] == str(first)

    def test_the_loser_is_logged_at_error_naming_both(self, monkeypatch, caplog):
        first = Path("/a/pr")
        second = Path("/b/pr")
        monkeypatch.setattr(server, "_tool_schema",
                            lambda s: _schema_for(s, name="pr"))

        with caplog.at_level(logging.ERROR, logger="otto-mcp"):
            discover_tools(_registered(first, second))

        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(errors) == 1
        assert str(first) in errors[0].getMessage()
        assert str(second) in errors[0].getMessage()


# ── The served set ────────────────────────────────────────────────────────


GOLDEN_TOOLS = Path(__file__).resolve().parent / "fixtures" / "mcp_tools.json"


def _served_tools() -> dict:
    """`discover_tools()` with each absolute script path made repo-relative."""
    return {
        name: {**schema,
               "_script": str(Path(schema["_script"]).relative_to(WORKBENCH_DIR))}
        for name, schema in discover_tools().items()
    }


class TestServedSchemas:
    """What the server offers, recorded so a rewrite of discovery can be checked.

    Discovery used to execute every candidate with ``--tool-schema``. It now
    reads the registry and imports the schema — a change of mechanism that
    must not be a change of output. Nothing else in this file compares the
    *whole* served set, so a schema silently gained or lost would pass every
    case above.

    Regenerate the fixture by writing ``json.dumps(_served_tools(), indent=2,
    sort_keys=True)`` to it. A diff here is a change to the MCP surface and is
    read as one.
    """

    def test_the_served_set_matches_the_golden(self):
        if not (WORKBENCH_DIR / "ai" / "bin" / "pr").exists():
            pytest.skip("scripts not found")

        assert _served_tools() == json.loads(GOLDEN_TOOLS.read_text())


# ── Re-discovery ──────────────────────────────────────────────────────────


def _fingerprint_tree(root: Path) -> Path:
    """A checkout whose fingerprint inputs exist: one registry and the two modules."""
    bindir = root / "bin"
    bindir.mkdir()
    registry = bindir / "registry.yml"
    registry.write_text(textwrap.dedent("""\
        meta:
          validation: bindir
          source: bin

        tools:
          - name: pr
            permission: false
            visibility: brief
            description: "The watched tool"
    """))
    cli = root / "ai" / "lib" / "cli"
    cli.mkdir(parents=True)
    (cli / "schema.py").write_text("# schema\n")
    (cli / "registry.py").write_text("# registry\n")
    return registry


class TestDiscoveryFingerprint:
    """What has to move before the server pays for a re-scan.

    Narrowed to the inputs discovery actually reads: the registry files, and
    the two modules the schema is built from. A `bin/` script changing is no
    longer a reason to re-import.
    """

    def test_an_untouched_tree_fingerprints_the_same_twice(self, tmp_path):
        _fingerprint_tree(tmp_path)

        assert discovery_fingerprint(tmp_path) == discovery_fingerprint(tmp_path)

    def test_an_edited_registry_changes_it(self, tmp_path):
        """A tool withdrawn by going hidden touches no line of its schema module."""
        registry = _fingerprint_tree(tmp_path)
        before = discovery_fingerprint(tmp_path)

        registry.write_text(registry.read_text().replace("brief", "hidden"))

        assert discovery_fingerprint(tmp_path) != before

    def test_editing_the_schema_module_changes_it(self, tmp_path):
        """The case the old fingerprint could not see: schema.py is not under bin/.

        A flag added to `pr` used to leave every scanned directory untouched,
        so the poll never re-imported and the client kept the startup schema
        for the life of the session.
        """
        _fingerprint_tree(tmp_path)
        schema = tmp_path / "ai" / "lib" / "cli" / "schema.py"
        before = discovery_fingerprint(tmp_path)

        schema.write_text(schema.read_text() + "# a new flag\n")

        assert discovery_fingerprint(tmp_path) != before

    def test_editing_the_cli_registry_module_changes_it(self, tmp_path):
        _fingerprint_tree(tmp_path)
        module = tmp_path / "ai" / "lib" / "cli" / "registry.py"
        before = discovery_fingerprint(tmp_path)

        module.write_text(module.read_text() + "# another command\n")

        assert discovery_fingerprint(tmp_path) != before

    def test_editing_a_bin_script_does_not_change_it(self, tmp_path):
        """Discovery no longer reads the binary; a comment in it is not an input."""
        _fingerprint_tree(tmp_path)
        script = tmp_path / "bin" / "pr"
        script.write_text("#!/bin/sh\n")
        before = discovery_fingerprint(tmp_path)

        script.write_text("#!/bin/sh\n# a new line\n")

        assert discovery_fingerprint(tmp_path) == before


def _pr_checkout(root: Path) -> Path:
    """A throwaway WORKBENCH_DIR whose registry offers `pr`."""
    bindir = root / "ai" / "bin"
    bindir.mkdir(parents=True)
    script = bindir / "pr"
    script.write_text("#!/bin/sh\n")
    (root / "ai" / "registry.yml").write_text(textwrap.dedent("""\
        meta:
          validation: bindir
          source: ai/bin

        tools:
          - name: pr
            permission: false
            visibility: brief
            description: "The watched tool"
    """))
    return script


class TestDiscoverWithBaseline:
    """The baseline has to describe a tree no newer than the scan it pairs with."""

    def test_the_scan_it_returns_is_the_one_it_ran(self, tmp_path, monkeypatch):
        monkeypatch.setattr(server, "WORKBENCH_DIR", tmp_path)
        _pr_checkout(tmp_path)

        discovered = discover_with_baseline()

        assert set(discovered.tools) == {"pr"}
        assert discovered.fingerprint == discovery_fingerprint(tmp_path)

    def test_a_change_during_the_scan_still_looks_new_afterwards(
            self, tmp_path, monkeypatch):
        """Stamped after the scan, that change is in the baseline and never arrives.

        No poll sees the file appear, so the client is offered the startup list
        for the rest of the session — and the client owns this process, so
        nothing outside it can restart the server either.
        """
        monkeypatch.setattr(server, "WORKBENCH_DIR", tmp_path)
        _pr_checkout(tmp_path)
        registry = tmp_path / "ai" / "registry.yml"

        def scan_while_the_registry_changes(registry=None):
            registry_path = tmp_path / "ai" / "registry.yml"
            registry_path.write_text(registry_path.read_text() + "\n# landed\n")
            return {}

        monkeypatch.setattr(server, "discover_tools", scan_while_the_registry_changes)

        discovered = discover_with_baseline()

        assert discovered.fingerprint != discovery_fingerprint(tmp_path)
        # The write above is the change the stamp must have missed.
        assert "landed" in registry.read_text()


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

    def discover(self, registry=None):
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
        """Touching a file discovery reads is not itself a tool change."""
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

    def test_a_withdrawn_tool_whose_script_remains_names_the_registry(
            self, tmp_path, caplog):
        """The branch that used to re-run the tool to ask why it was gone.

        `_why_gone` called `probe_tool` here, which no longer exists — a lost
        tool whose script was still on disk would have raised `NameError` in
        the watcher thread, where nothing is there to catch it. With discovery
        reading the registry there are only two ways for a tool to go, and
        this is the other one.
        """
        script = tmp_path / "pr"
        script.touch()
        before = {"pr": {"name": "pr", "_script": str(script)}}

        with caplog.at_level(logging.ERROR, logger="otto-mcp"):
            _log_lost_tools(before, {})

        assert "pr" in caplog.text
        assert "its registry entry no longer offers it" in caplog.text
        assert "its script is gone" not in caplog.text

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
