"""Tests for MCP server rediscovery: the fingerprint, the baseline and the tool-change watcher."""

from __future__ import annotations

import asyncio
import logging
import sys
import textwrap
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "claude" / "mcps"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import server
from server import (
    _log_lost_tools,
    discover_with_baseline,
    discovery_fingerprint,
    watch_for_tool_changes,
)


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
