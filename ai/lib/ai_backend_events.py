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


# ── Write-tool recognition ───────────────────────────────────────────────────
#
# Owned here rather than in each backend: an agent that never calls one of
# these produced nothing, and both the Pi steer and the post-hoc diagnosis in
# review_agent need to ask the same question.

WRITE_TOOL_NAMES = frozenset({"edit", "multiedit", "notebookedit", "write"})


def is_write_tool(name: str) -> bool:
    """Whether a tool can put content into a file.

    Compared lowercased — Claude reports `Edit`, Pi reports `edit`.
    """
    return name.lower() in WRITE_TOOL_NAMES


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


def claude_display_text(raw_line: str) -> str:
    """Render a Claude stream-json line as the text a human should see.

    Assistant prose is returned verbatim, tool use as its progress label. Used by
    invoke_fix, which echoed raw stdout before it asked for structured output.
    """
    try:
        data = json.loads(raw_line)
    except (json.JSONDecodeError, ValueError):
        return ""
    if data.get("type") != "assistant":
        return ""
    parts = []
    for block in data.get("message", {}).get("content", []):
        if block.get("type") == "text" and block.get("text"):
            parts.append(block["text"])
            continue
        label = _claude_tool_label(block)
        if label:
            parts.append(f"▸ {label}")
    return "\n".join(parts)


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


def parse_pi_event(data: dict) -> StreamEvent | None:
    """Extract a StreamEvent from a parsed Pi event, or None.

    The Pi consumers all take an already-parsed event: the stream loop parses
    each line once and hands the same dict to every one of them.
    """
    event_type = data.get("type", "")
    if event_type == "tool_execution_start":
        label = _pi_tool_label(data)
        return StreamEvent(tool_label=label) if label else None
    if event_type == "message_update":
        return _parse_message_update_tool(data)
    return None


def pi_write_tool_used(data: dict) -> bool:
    """Whether a parsed Pi event shows a file-writing tool being invoked."""
    event_type = data.get("type", "")
    if event_type == "tool_execution_start":
        return is_write_tool(data.get("toolName", "") or data.get("name", ""))
    if event_type != "message_update":
        return False
    content = data.get("content", [])
    if not isinstance(content, list):
        return False
    return any(
        block.get("type") == "toolCall" and is_write_tool(block.get("name", ""))
        for block in content
    )


def parse_pi_cost(data: dict) -> float | None:
    """Extract per-message cost from a parsed Pi message_end event.

    Returns the message's total cost in USD, or None if not a message_end.
    """
    if data.get("type") != "message_end":
        return None
    cost_obj = data.get("message", {}).get("usage", {}).get("cost", {})
    total = cost_obj.get("total")
    if isinstance(total, (int, float)):
        return float(total)
    return None
