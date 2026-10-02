"""Session-log builders shared by the review_agent_diagnostics_* suites."""

import json
from pathlib import Path

# Arbitrary — the diagnosis echoes whatever num_turns the result record carries,
# so the value only has to be distinguishable from the pipeline's turn defaults.
_TURNS = 16
_NO_WRITE_SUFFIX = " — never called a file-writing tool"


def _write_log(tmp_path: Path, *lines: str) -> str:
    path = tmp_path / "session.jsonl"
    path.write_text("\n".join(lines) + "\n")
    return str(path)


def _pi_tool(name: str, **args) -> str:
    return json.dumps({"type": "tool_execution_start", "toolName": name, "args": args})


def _pi_result(subtype: str = "error_max_turns", num_turns: int = _TURNS) -> str:
    """A Pi run's result record, alongside the RPC events that mark the shape."""
    return json.dumps({"type": "result", "subtype": subtype, "num_turns": num_turns})


def _pi_text(text: str, *, role: str = "assistant") -> str:
    """An assistant turn carrying text and no tool call — a narrated write."""
    return json.dumps({
        "type": "turn_end",
        "message": {"role": role, "content": [{"type": "text", "text": text}]},
    })
