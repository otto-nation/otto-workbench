"""Normalized event parsing for AI backend JSONL streams.

Provides a common StreamEvent and parsers for both Claude Code's
stream-json format and Pi's --mode json format, so stream_progress()
works identically regardless of backend.
"""

# doc-group: backend

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Only under the type checker: agent.usage imports nothing from here, but
    # keeping the runtime import inside pi_prompt_result keeps this module free
    # of a module-level dependency the streaming parsers do not need.
    from agent import usage as ai_usage


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
# agent.session need to ask the same question.

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
    """Extract a tool label from a Pi tool_execution_start event.

    The argument bag is ``args`` and a path in it is ``path`` — Pi's own shape,
    per `tool_execution_start` in its docs/json.md and the capture in
    tests/fixtures/pi_prompt_session.jsonl. ``arguments``/``input`` and
    ``file_path`` are Claude's spelling; reading those against a real Pi event
    finds nothing and every label degrades to the bare tool name. Both spellings
    are accepted so a fixture written either way still labels.
    """
    tool_name = data.get("toolName", "") or data.get("name", "")
    args = data.get("args") or data.get("arguments") or data.get("input") or {}
    if not tool_name:
        return ""
    if args.get("description"):
        return args["description"]
    path = args.get("path") or args.get("file_path", "")
    name_lower = tool_name.lower()
    if name_lower == "read":
        return "Read " + (path.split("/")[-1] or "")
    if name_lower == "grep":
        return "Grep " + args.get("pattern", "")
    if name_lower in ("find", "glob", "ls"):
        return tool_name.capitalize() + " " + args.get("pattern", path)
    if name_lower in ("write", "edit"):
        return tool_name.capitalize() + " " + (path.split("/")[-1] or "")
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


def pi_prompt_result(stdout: str) -> tuple[str, ai_usage.SessionUsage | None]:
    """The reply text and the usage from one `pi -p --mode json` run.

    Print mode without `--mode json` emits the assistant's prose and nothing
    else, so a prompt call measured that way reaches the ledger as no row at
    all — not a zero row, no row. The JSON stream carries the same prose plus
    the usage, which is why the prompt command asks for it.

    **Text comes from `agent_end`, not from the deltas.** Its `messages` array
    is the finished transcript, so the last assistant message is the reply;
    accumulating `text_delta` would also collect the intermediate turns a
    tool-using prompt produces, and `message_update` carries a *cumulative*
    usage snapshot that double-counts if summed.

    **Cost is the sum of every `message_end`, not the last one.** One prompt
    that calls a tool has two assistant messages with two separate costs —
    tests/fixtures/pi_prompt_session.jsonl is exactly that shape, so a
    last-one-wins reading loses a turn's spend.

    Stdout that is not the JSON stream comes back as `(stdout, None)`: a Pi
    output-format change should cost the measurement, not the call. This mirrors
    the Claude backend's raw-stdout fallback.
    """
    from agent import usage as ai_usage

    events = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    if not events:
        return stdout, None

    text = ""
    for event in events:
        if event.get("type") != "agent_end":
            continue
        for message in event.get("messages", []):
            if message.get("role") != "assistant":
                continue
            blocks = message.get("content", [])
            if not isinstance(blocks, list):
                continue
            joined = "".join(
                b.get("text", "") for b in blocks
                if isinstance(b, dict) and b.get("type") == "text"
            )
            if joined:
                text = joined

    totals = {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}
    cost = 0.0
    cost_by_model: dict[str, float] = {}
    measured = False
    for event in events:
        message_cost = parse_pi_cost(event)
        if message_cost is None:
            continue
        measured = True
        cost += message_cost
        message = event.get("message", {})
        usage = message.get("usage", {})
        for key in totals:
            value = usage.get(key)
            if isinstance(value, (int, float)):
                totals[key] += int(value)
        model = message.get("model")
        if model:
            cost_by_model[model] = cost_by_model.get(model, 0.0) + message_cost

    if not measured:
        # Parseable JSON that carries no message_end is a stream shape this does
        # not understand. Report the text and no usage rather than a zero row,
        # which would read as a call that genuinely cost nothing.
        return (text or stdout), None

    return text, ai_usage.SessionUsage(
        cost=cost,
        input_tokens=totals["input"],
        output_tokens=totals["output"],
        cache_read_tokens=totals["cacheRead"],
        cache_write_tokens=totals["cacheWrite"],
        cost_by_model=cost_by_model,
    )
