"""Pi CLI backend for ai_backend.

Implements prompt(), invoke_agent(), and invoke_fix() by building
`pi` commands and running them as subprocesses.

invoke_agent uses RPC mode (--mode rpc) for bidirectional control:
  - Budget enforcement via accumulated message_end costs + get_session_stats
  - Clean abort via {"type": "abort"} instead of SIGTERM
  - Claude-compatible result records written to session logs

prompt() and invoke_fix() use print mode (pi -p) for simplicity.

Pi CLI reference:
  -p / --print     Prompt mode (non-interactive, like claude -p)
  --mode rpc       Bidirectional JSONL over stdin/stdout
  --approve        Auto-accept project trust (like claude --permission-mode acceptEdits)
  --no-session     Ephemeral mode (don't persist session)
  --tools <list>   Allowlist specific tools
  --model <id>     Model selection
  --thinking <lvl> Thinking depth: off, minimal, low, medium, high, xhigh
  --append-system-prompt <text>  Inject additional system prompt
  --verbose        Verbose output

Gaps vs Claude Code CLI:
  --max-turns      Not available; counted via turn_end events, abort via RPC
  --max-budget-usd Not available; tracked via message_end costs, abort via RPC
  --add-dir        Not available; directories passed in prompt text
  --agent          Not available; use --append-system-prompt with agent file contents
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from ai_backend_events import parse_pi_cost, parse_pi_event
from review_common import ANSI_DIM, ANSI_RESET, _print_lock

PI_TOOLS = "bash,read,write,edit,grep,find,ls"

AGENTS_DIR = Path.home() / ".claude" / "agents"


def _read_agent_prompt(agent: str) -> str | None:
    """Read an agent's system prompt from ~/.claude/agents/<name>.md."""
    agent_file = AGENTS_DIR / f"{agent}.md"
    if agent_file.is_file():
        return agent_file.read_text()
    print(f"  warning: agent file not found: {agent_file}", file=sys.stderr)
    return None


# ── Command builders ──────────────────────────────────────────────────────────


def _build_prompt_cmd(model: str | None = None) -> list[str]:
    cmd = ["pi", "-p", "--no-session", "--approve"]
    if model:
        cmd += ["--model", model]
    return cmd


def _build_agent_cmd(
    agent: str | None = None,
    model: str | None = None,
    thinking_level: str | None = None,
) -> list[str]:
    cmd = [
        "pi", "--mode", "rpc", "--no-session", "--approve", "--verbose",
        "--tools", PI_TOOLS,
    ]
    if agent:
        agent_prompt = _read_agent_prompt(agent)
        if agent_prompt:
            cmd += ["--append-system-prompt", agent_prompt]
    if model:
        cmd += ["--model", model]
    if thinking_level:
        cmd += ["--thinking", thinking_level]
    return cmd


def _build_fix_cmd(
    model: str | None = None,
    thinking_level: str | None = None,
) -> list[str]:
    cmd = [
        "pi", "-p", "--no-session", "--approve", "--verbose",
        "--tools", PI_TOOLS,
    ]
    if model:
        cmd += ["--model", model]
    if thinking_level:
        cmd += ["--thinking", thinking_level]
    return cmd


def _log_stderr_on_failure(proc: subprocess.Popen, session_log: str):
    if proc.returncode == 0:
        return
    stderr_output = proc.stderr.read()
    if not stderr_output:
        return
    with open(session_log, "a") as f:
        f.write(f"\n--- stderr (exit {proc.returncode}) ---\n{stderr_output}\n")


# ── RPC protocol helpers ─────────────────────────────────────────────────────


def _send(proc: subprocess.Popen, command: dict):
    """Write a JSONL command to the RPC process's stdin."""
    proc.stdin.write(json.dumps(command) + "\n")
    proc.stdin.flush()


def _read_rpc_response(proc: subprocess.Popen, command_type: str) -> dict:
    """Read lines until we get a response for the given command type.

    Skips any interleaved events (shouldn't occur after agent_end,
    but handles gracefully).
    """
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if data.get("type") == "response" and data.get("command") == command_type:
            return data
    return {}


def _get_stats_after_agent_end(proc: subprocess.Popen) -> dict:
    """Query get_session_stats after the agent has finished.

    Safe to call only after agent_end — no event interleaving.
    """
    _send(proc, {"type": "get_session_stats"})
    resp = _read_rpc_response(proc, "get_session_stats")
    return resp


# ── Stream progress (Pi RPC JSONL) ───────────────────────────────────────────


def _display_event(raw_line: str, prev_tool: str, prefix: str) -> str:
    event = parse_pi_event(raw_line)
    if not event or event.tool_label == prev_tool:
        return prev_tool
    with _print_lock:
        print(f"{prefix}  {ANSI_DIM}▸ {event.tool_label}{ANSI_RESET}", flush=True)
    return event.tool_label


def _parse_event_type(raw_line: str) -> tuple[str, dict]:
    """Parse a line and return (event_type, parsed_data)."""
    try:
        data = json.loads(raw_line)
    except (json.JSONDecodeError, ValueError):
        return "", {}
    return data.get("type", ""), data


def _check_limits(
    process: subprocess.Popen,
    turn_count: int, accumulated_cost: float,
    max_turns: int | None, max_budget: float | None,
) -> str | None:
    """Check turn and budget limits after a turn_end. Returns stop reason or None."""
    if max_turns is not None and turn_count >= max_turns:
        _send(process, {"type": "abort"})
        return "max_turns"
    if max_budget is not None and accumulated_cost > max_budget:
        _send(process, {"type": "abort"})
        return "max_budget"
    return None


def _consume_stream(
    process: subprocess.Popen, log, prefix: str,
    max_turns: int | None = None,
    max_budget: float | None = None,
) -> tuple[int, float, str]:
    """Consume the RPC event stream, enforcing turn and budget limits.

    Returns (turn_count, accumulated_cost, stop_reason).
    stop_reason is one of: "completed", "max_turns", "max_budget".
    """
    prev_tool = ""
    turn_count = 0
    accumulated_cost = 0.0
    stop_reason = "completed"

    for raw_line in process.stdout:
        log.write(raw_line)
        log.flush()

        event_type, _ = _parse_event_type(raw_line)

        if event_type == "response":
            continue

        prev_tool = _display_event(raw_line, prev_tool, prefix)

        msg_cost = parse_pi_cost(raw_line)
        if msg_cost is not None:
            accumulated_cost += msg_cost

        if event_type == "turn_end":
            turn_count += 1
            stop_reason = _check_limits(process, turn_count, accumulated_cost, max_turns, max_budget) or stop_reason

        if event_type == "agent_end":
            break

    return turn_count, accumulated_cost, stop_reason


# ── Result record generation ─────────────────────────────────────────────────


def _write_result_record(
    session_log: str,
    stop_reason: str,
    turn_count: int,
    cost: float,
    duration_ms: int,
    stats: dict,
):
    """Write a Claude-compatible result record to the session log.

    Maps Pi's get_session_stats fields to Claude's result record format
    so _parse_session_cost() and parse_session_usage() work without changes.
    """
    tokens = stats.get("tokens", {})

    record = {
        "type": "result",
        "subtype": stop_reason if stop_reason == "success" else stop_reason,
        "is_error": False,
        "total_cost_usd": stats.get("cost", cost),
        "num_turns": turn_count,
        "duration_ms": duration_ms,
        "usage": {
            "input_tokens": tokens.get("input", 0),
            "output_tokens": tokens.get("output", 0),
            "cache_read_input_tokens": tokens.get("cacheRead", 0),
            "cache_creation_input_tokens": tokens.get("cacheWrite", 0),
        },
    }
    with open(session_log, "a") as f:
        f.write(json.dumps(record) + "\n")


# ── Public interface ──────────────────────────────────────────────────────────


def prompt(text: str, *, model: str | None = None) -> tuple[str, int]:
    """Stateless text-in/text-out via pi -p."""
    cmd = _build_prompt_cmd(model=model)
    result = subprocess.run(cmd, input=text, capture_output=True, text=True)
    return result.stdout, result.returncode


def invoke_agent(
    prompt: str, session_log: str, *,
    add_dirs: list[str],
    agent: str | None = None,
    max_turns: int | None = None,
    max_budget: float | None = None,
    model: str | None = None,
    thinking_level: str | None = None,
    label: str = "",
) -> int:
    """Full agent with RPC streaming to session log. Returns exit code.

    Uses Pi's RPC mode for bidirectional control:
    - add_dirs are injected into the prompt text (Pi has no --add-dir flag)
    - max_turns is enforced by counting turn_end events and sending abort
    - max_budget is enforced by accumulating message_end costs and sending abort
    - A Claude-compatible result record is written at the end for cost tracking
    """
    dir_context = ""
    if add_dirs:
        dir_lines = "\n".join(f"  - {d}" for d in add_dirs)
        dir_context = f"\nAccessible directories:\n{dir_lines}\n\n"

    full_prompt = dir_context + prompt if dir_context else prompt

    cmd = _build_agent_cmd(
        agent=agent, model=model, thinking_level=thinking_level,
    )
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    prefix = f"  {ANSI_DIM}[{label}]{ANSI_RESET} " if label else ""
    start_time = time.monotonic()

    # Send the prompt via RPC
    _send(proc, {"type": "prompt", "message": full_prompt})

    # Stream events with budget and turn enforcement
    with open(session_log, "w") as log:
        turn_count, accumulated_cost, stop_reason = _consume_stream(
            proc, log, prefix,
            max_turns=max_turns, max_budget=max_budget,
        )

    duration_ms = int((time.monotonic() - start_time) * 1000)

    # Query authoritative stats after agent is done
    stats = _get_stats_after_agent_end(proc)

    # Write Claude-compatible result record
    _write_result_record(
        session_log, stop_reason, turn_count,
        accumulated_cost, duration_ms, stats,
    )

    # Close stdin to terminate the RPC process
    proc.stdin.close()
    proc.wait()
    _log_stderr_on_failure(proc, session_log)
    return proc.returncode


def invoke_fix(
    prompt: str, *,
    add_dirs: list[str],
    max_turns: int | None = None,
    model: str | None = None,
    thinking_level: str | None = None,
) -> int:
    """Agent with workspace write access, raw output echoed to stderr. Returns exit code."""
    if max_turns is not None:
        print(f"  {ANSI_DIM}(pi backend: --max-turns not supported in fix mode, ignoring){ANSI_RESET}",
              file=sys.stderr)

    dir_context = ""
    if add_dirs:
        dir_lines = "\n".join(f"  - {d}" for d in add_dirs)
        dir_context = f"\nAccessible directories:\n{dir_lines}\n\n"

    full_prompt = dir_context + prompt if dir_context else prompt

    cmd = _build_fix_cmd(model=model, thinking_level=thinking_level)
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=sys.stderr,
        text=True,
    )
    proc.stdin.write(full_prompt)
    proc.stdin.close()
    for line in proc.stdout:
        print(line, end="", file=sys.stderr)
    proc.wait()
    return proc.returncode
