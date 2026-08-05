"""Tests for write-tool recognition shared by the Pi steer and the diagnosis."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import ai_backend_events as events


class TestIsWriteTool:
    def test_claude_casing(self):
        assert events.is_write_tool("Edit")
        assert events.is_write_tool("Write")
        assert events.is_write_tool("MultiEdit")

    def test_pi_casing(self):
        assert events.is_write_tool("edit")
        assert events.is_write_tool("write")

    def test_read_only_tools_are_not_writes(self):
        assert not events.is_write_tool("Read")
        assert not events.is_write_tool("grep")
        assert not events.is_write_tool("")

    def test_bash_is_not_a_write_tool(self):
        """A heredoc write is indistinguishable from `ls` at the tool-name level."""
        assert not events.is_write_tool("Bash")


class TestPiWriteToolUsed:
    def test_tool_execution_start_with_edit(self):
        line = json.dumps({"type": "tool_execution_start", "toolName": "edit"})
        assert events.pi_write_tool_used(line)

    def test_tool_execution_start_with_name_key(self):
        line = json.dumps({"type": "tool_execution_start", "name": "write"})
        assert events.pi_write_tool_used(line)

    def test_tool_execution_start_with_read(self):
        line = json.dumps({"type": "tool_execution_start", "toolName": "read"})
        assert not events.pi_write_tool_used(line)

    def test_message_update_with_edit_tool_call(self):
        line = json.dumps({
            "type": "message_update",
            "content": [
                {"type": "text", "text": "writing"},
                {"type": "toolCall", "name": "edit", "arguments": {"file_path": "/tmp/a"}},
            ],
        })
        assert events.pi_write_tool_used(line)

    def test_message_update_without_write_tool_call(self):
        line = json.dumps({
            "type": "message_update",
            "content": [{"type": "toolCall", "name": "grep", "arguments": {}}],
        })
        assert not events.pi_write_tool_used(line)

    def test_message_update_with_non_list_content(self):
        line = json.dumps({"type": "message_update", "content": "thinking"})
        assert not events.pi_write_tool_used(line)

    def test_unrelated_event(self):
        assert not events.pi_write_tool_used(json.dumps({"type": "turn_end"}))

    def test_malformed_line(self):
        assert not events.pi_write_tool_used("not json")
