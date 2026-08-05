"""Tests for MCP server — tool discovery, arg mapping, and JSON extraction."""

from __future__ import annotations

import json
import stat
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "claude" / "mcps"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

from server import _args_to_cli, _declares_tool_schema, _extract_json, discover_tools


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


class TestDiscovery:
    def test_discovers_tool_schema_scripts(self, tmp_path):
        script = tmp_path / "my-tool"
        script.write_text(textwrap.dedent("""\
            #!/usr/bin/env python3
            import json, sys
            if "--tool-schema" in sys.argv:
                json.dump({"name": "my-tool", "description": "A test tool",
                           "input_schema": {"type": "object", "properties": {}}}, sys.stdout)
                sys.exit(0)
        """))
        script.chmod(script.stat().st_mode | stat.S_IXUSR)

        config = {"tool_dirs": [str(tmp_path)], "plugin_dirs": []}
        tools = discover_tools(config)

        assert "my-tool" in tools
        assert tools["my-tool"]["description"] == "A test tool"

    def test_skips_non_tool_scripts(self, tmp_path):
        script = tmp_path / "plain-script"
        script.write_text("#!/bin/bash\necho hello\n")
        script.chmod(script.stat().st_mode | stat.S_IXUSR)

        config = {"tool_dirs": [str(tmp_path)], "plugin_dirs": []}
        tools = discover_tools(config)

        assert len(tools) == 0

    def test_script_without_the_flag_is_never_executed(self, tmp_path):
        """A script that ignores unknown flags must not run during discovery."""
        marker = tmp_path / "side-effect"
        script = tmp_path / "destructive-script"
        script.write_text(f"#!/bin/bash\ntouch '{marker}'\n")
        script.chmod(script.stat().st_mode | stat.S_IXUSR)

        config = {"tool_dirs": [str(tmp_path)], "plugin_dirs": []}
        tools = discover_tools(config)

        assert tools == {}
        assert not marker.exists()

    def test_tool_parser_import_counts_as_a_declaration(self, tmp_path):
        """ToolParser-based scripts inherit the flag without naming it."""
        script = tmp_path / "framework-tool"
        script.write_text("#!/usr/bin/env python3\nfrom tool_parser import ToolParser\n")

        assert _declares_tool_schema(script) is True

    def test_skips_unreadable_script(self, tmp_path):
        script = tmp_path / "unreadable"
        script.write_text("#!/bin/bash\necho --tool-schema\n")
        script.chmod(stat.S_IXUSR)

        assert _declares_tool_schema(script) is False

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

        config = {"tool_dirs": [str(tmp_path)], "plugin_dirs": []}
        tools = discover_tools(config)
        assert len(tools) == 0

    def test_plugin_directory(self, tmp_path):
        plugin_dir = tmp_path / "plugins"
        plugin_dir.mkdir()
        tool_dir = tmp_path / "project-tools"
        tool_dir.mkdir()

        plugin_file = plugin_dir / "my-project.json"
        plugin_file.write_text(json.dumps({
            "name": "my-project",
            "tool_dir": str(tool_dir),
        }))

        script = tool_dir / "project-tool"
        script.write_text(textwrap.dedent("""\
            #!/usr/bin/env python3
            import json, sys
            if "--tool-schema" in sys.argv:
                json.dump({"name": "project-tool", "description": "From plugin",
                           "input_schema": {"type": "object", "properties": {}}}, sys.stdout)
                sys.exit(0)
        """))
        script.chmod(script.stat().st_mode | stat.S_IXUSR)

        config = {"tool_dirs": [], "plugin_dirs": [str(plugin_dir)]}
        tools = discover_tools(config)

        assert "project-tool" in tools
        assert tools["project-tool"]["description"] == "From plugin"

    def test_empty_config(self):
        tools = discover_tools({"tool_dirs": [], "plugin_dirs": []})
        assert tools == {}

    def test_discovers_real_tools(self):
        """Verify discovery works with actual ToolParser-enabled scripts."""
        bin_dir = Path(__file__).resolve().parent.parent / "ai" / "claude" / "bin"
        if not (bin_dir / "pr-rebase").exists():
            pytest.skip("scripts not found")

        config = {"tool_dirs": [str(bin_dir)], "plugin_dirs": []}
        tools = discover_tools(config)

        for name in ("pr-rebase", "ci-check", "pr"):
            assert name in tools, f"{name} not discovered"
            assert "input_schema" in tools[name]

        assert "output_schema" in tools["pr-rebase"]
        assert tools["pr-rebase"]["output_schema"]["type"] == "object"
        assert "output_schema" in tools["ci-check"]
        assert "output_schema" in tools["pr"]
