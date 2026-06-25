"""Normalized event parsing for AI backend JSONL streams.

Provides a common StreamEvent and parsers for both Claude Code's
stream-json format and Pi's --mode json format, so stream_progress()
works identically regardless of backend.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass


def _log_stderr_on_failure(proc: subprocess.Popen, session_log: str):
    """Append stderr to the session log when a subprocess exits non-zero."""
    if proc.returncode == 0:
        return
    stderr_output = proc.stderr.read()
    if not stderr_output or not session_log:
        return
    with open(session_log, "a") as f:
        f.write(f"\n--- stderr (exit {proc.returncode}) ---\n{stderr_output}\n")


@dataclass
class StreamEvent:
    """A normalized tool-use progress event."""
    tool_label: str


# ── Claude Code parser ────────────────────────────────────────────────────────


def _claude_tool_label(block: dict) -> str:
    if block.get("type") != "tool_use":
        return ""
    inp = block.get("input", {})
    name = block.get("name", "")
    if inp.get("description"):
        return inp["description"]
    if name == "Read":
        return "Read " + (inp.get("file_path", "").split("/")[-1] or "")
    if name == "Grep":
        return "Grep " + inp.get("pattern", "")
    if name == "Glob":
        return "Glob " + inp.get("pattern", "")
    if name == "Write":
        return "Write " + (inp.get("file_path", "").split("/")[-1] or "")
    return name


def parse_claude_event(raw_line: str) -> StreamEvent | None:
    """Parse a Claude stream-json line into a StreamEvent, or None."""
    try:
        data = json.loads(raw_line)
    except (json.JSONDecodeError, ValueError):
        return None
    if data.get("type") != "assistant":
        return None
    for block in data.get("message", {}).get("content", []):
        label = _claude_tool_label(block)
        if label:
            return StreamEvent(tool_label=label)
    return None


# ── Pi parser ─────────────────────────────────────────────────────────────────


def _pi_tool_label(data: dict) -> str:
    """Extract a tool label from a Pi tool_execution_start event."""
    tool_name = data.get("toolName", "") or data.get("name", "")
    args = data.get("arguments", {}) or data.get("input", {})
    if not tool_name:
        return ""
    if args.get("description"):
        return args["description"]
    name_lower = tool_name.lower()
    if name_lower == "read":
        return "Read " + (args.get("file_path", "").split("/")[-1] or "")
    if name_lower == "grep":
        return "Grep " + args.get("pattern", "")
    if name_lower in ("find", "glob", "ls"):
        return tool_name.capitalize() + " " + args.get("pattern", args.get("path", ""))
    if name_lower in ("write", "edit"):
        return tool_name.capitalize() + " " + (args.get("file_path", "").split("/")[-1] or "")
    if name_lower == "bash":
        cmd = args.get("command", "")
        return "Bash " + (cmd[:40] + "..." if len(cmd) > 40 else cmd) if cmd else "Bash"
    return tool_name


def _parse_message_update_tool(data: dict) -> StreamEvent | None:
    """Extract a tool event from a Pi message_update with toolCall blocks."""
    content = data.get("content", [])
    if not isinstance(content, list):
        return None
    for block in content:
        if block.get("type") != "toolCall":
            continue
        name = block.get("name", "")
        if not name:
            continue
        label = _pi_tool_label({"toolName": name, "arguments": block.get("arguments", {})})
        if label:
            return StreamEvent(tool_label=label)
    return None


def parse_pi_event(raw_line: str) -> StreamEvent | None:
    """Parse a Pi JSONL line into a StreamEvent, or None."""
    try:
        data = json.loads(raw_line)
    except (json.JSONDecodeError, ValueError):
        return None
    event_type = data.get("type", "")
    if event_type == "tool_execution_start":
        label = _pi_tool_label(data)
        return StreamEvent(tool_label=label) if label else None
    if event_type == "message_update":
        return _parse_message_update_tool(data)
    return None


def parse_pi_cost(raw_line: str) -> float | None:
    """Extract per-message cost from a Pi message_end event.

    Returns the message's total cost in USD, or None if not a message_end.
    """
    try:
        data = json.loads(raw_line)
    except (json.JSONDecodeError, ValueError):
        return None
    if data.get("type") != "message_end":
        return None
    cost_obj = data.get("message", {}).get("usage", {}).get("cost", {})
    total = cost_obj.get("total")
    if isinstance(total, (int, float)):
        return float(total)
    return None
