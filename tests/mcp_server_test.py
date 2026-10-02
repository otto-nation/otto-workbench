"""Tests for MCP server — JSON extraction, arg mapping, spawn isolation and tool discovery."""

from __future__ import annotations

import json
import logging
import stat
import sys
import textwrap
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
    _tool_schema,
    discover_tools,
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
