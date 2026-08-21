"""Pi CLI backend for ai_backend.

Implements preflight(), prompt(), invoke_agent(), and invoke_fix() by
building `pi` commands and running them as subprocesses.

invoke_agent and invoke_fix use RPC mode (--mode rpc) for bidirectional control:
  - Budget enforcement via accumulated message_end costs + get_session_stats
  - Clean abort via {"type": "abort"} instead of SIGTERM
  - Claude-compatible result records written to session logs

prompt() uses print mode (pi -p) for simplicity.

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
import os
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

import ai_usage
import log
import timeouts
from ai_backend import AgentInvocation
from ai_backend_events import (
    _log_stderr_on_failure, parse_pi_cost, parse_pi_event, pi_write_tool_used,
)
from log import ANSI_DIM, ANSI_RESET, _print_lock

PI_TOOLS = "bash,read,write,edit,grep,find,ls"

AGENTS_DIR = Path.home() / ".claude" / "agents"
PI_SKILLS_DIR = Path.home() / ".pi" / "agent" / "skills"
REVIEW_EXTENSION = Path(__file__).resolve().parent.parent / "claude" / "pi" / "extensions" / "review-guard.ts"


def _read_agent_prompt(agent: str) -> str | None:
    """Read an agent's system prompt from ~/.claude/agents/<name>.md."""
    agent_file = AGENTS_DIR / f"{agent}.md"
    if agent_file.is_file():
        return agent_file.read_text()
    log.warn(f"agent file not found: {agent_file}")
    return None


AGENT_PROTOCOL_PLACEHOLDER = "AGENT_PROTOCOL_PLACEHOLDER"


def _resolve_skill_path(agent: str) -> Path | None:
    """Check if a Pi-format SKILL.md exists for the given agent name.

    Returns None if the file is missing or still contains the unresolved placeholder.
    """
    skill_file = PI_SKILLS_DIR / agent / "SKILL.md"
    if not skill_file.is_file():
        return None
    if AGENT_PROTOCOL_PLACEHOLDER in skill_file.read_text():
        return None
    return skill_file


# ── Command builders ──────────────────────────────────────────────────────────


def _build_prompt_cmd(model: str | None = None, provider: str | None = None) -> list[str]:
    cmd = ["pi", "-p", "--no-session", "--approve"]
    if provider:
        cmd += ["--provider", provider]
    if model:
        cmd += ["--model", model]
    return cmd


def _build_agent_cmd(inv: AgentInvocation, extension: str | None = None) -> list[str]:
    cmd = [
        "pi", "--mode", "rpc", "--no-session", "--approve", "--verbose",
        "--tools", PI_TOOLS,
    ]
    if inv.agent:
        skill_path = _resolve_skill_path(inv.agent)
        if skill_path:
            cmd += ["--skill", str(skill_path)]
        elif (agent_prompt := _read_agent_prompt(inv.agent)) is not None:
            cmd += ["--append-system-prompt", agent_prompt]
        else:
            raise FileNotFoundError(f"Agent file not found: {AGENTS_DIR / f'{inv.agent}.md'}")
    if inv.provider:
        cmd += ["--provider", inv.provider]
    if inv.model:
        cmd += ["--model", inv.model]
    if inv.thinking:
        cmd += ["--thinking", inv.thinking]
    if extension:
        cmd += ["--extension", extension]
    return cmd


def _build_fix_cmd(inv: AgentInvocation, extension: str | None = None) -> list[str]:
    # ceiling: no `gh` deny here, unlike the Claude backend's FIX_DENIED_TOOLS —
    # `--tools` allowlists tool *names*, so barring one bash command is not
    # expressible. A fix agent has no GitHub business and the fix templates say
    # so, but here that is trusted rather than enforced; the outward writes that
    # matter are gated at the write instead (see `publishing`). Upgrade when pi
    # grows per-command bash permissions.
    cmd = [
        "pi", "--mode", "rpc", "--no-session", "--approve", "--verbose",
        "--tools", PI_TOOLS,
    ]
    if inv.provider:
        cmd += ["--provider", inv.provider]
    if inv.model:
        cmd += ["--model", inv.model]
    if inv.thinking:
        cmd += ["--thinking", inv.thinking]
    if extension:
        cmd += ["--extension", extension]
    return cmd


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


def _display_event(data: dict, prev_tool: str, prefix: str) -> str:
    event = parse_pi_event(data)
    if not event or event.tool_label == prev_tool:
        return prev_tool
    with _print_lock:
        print(f"{prefix}  {ANSI_DIM}▸ {event.tool_label}{ANSI_RESET}", file=sys.stderr, flush=True)
    return event.tool_label


def _parse_event_type(raw_line: str) -> tuple[str, dict]:
    """Parse a line and return (event_type, parsed_data)."""
    try:
        data = json.loads(raw_line)
    except (json.JSONDecodeError, ValueError):
        return "", {}
    return data.get("type", ""), data


BUDGET_WARN_THRESHOLD = 0.8

# An agent that has already written its file just needs to finish. One that
# has not is about to run out with nothing to show, so name the mechanism
# rather than telling it to hurry.
_WRAP_UP = "Wrap up your current analysis and write your output."
_WRITE_FIRST = (
    "You have NOT written your output file yet. Do that now: Read it — it "
    "exists and is empty — then Edit it with an empty `old_string` to insert "
    "your complete output. Refine it afterwards only if turns remain."
)


def _steer_message(warning: str, wrote_output: bool) -> str:
    return f"{warning} {_WRAP_UP if wrote_output else _WRITE_FIRST}"


def _check_limits(
    process: subprocess.Popen,
    turn_count: int, accumulated_cost: float,
    max_turns: int | None, max_budget: float | None,
    steered: bool = False,
    wrote_output: bool = False,
) -> tuple[str | None, bool]:
    """Check turn and budget limits after a turn_end.

    Returns (stop_reason, steered) where stop_reason is None if not aborting.
    Sends steer at 80% of either limit (once), abort + follow_up when exceeded.
    """
    if max_turns is not None and turn_count >= max_turns:
        _send(process, {"type": "abort"})
        _send(process, {"type": "follow_up", "message": "You were stopped due to turn limit. Summarize what you found and what remains."})
        return "max_turns", steered
    if max_budget is not None and accumulated_cost > max_budget:
        _send(process, {"type": "abort"})
        _send(process, {"type": "follow_up", "message": "You were stopped due to budget limit. Summarize what you found and what remains."})
        return "max_budget", steered

    if not steered:
        if max_budget is not None and accumulated_cost >= max_budget * BUDGET_WARN_THRESHOLD:
            warning = f"Budget warning: {accumulated_cost:.2f}/{max_budget:.2f} USD consumed."
            _send(process, {"type": "steer", "message": _steer_message(warning, wrote_output)})
            steered = True
        elif max_turns is not None and turn_count >= int(max_turns * BUDGET_WARN_THRESHOLD):
            warning = f"Turn warning: {turn_count}/{max_turns} turns used."
            _send(process, {"type": "steer", "message": _steer_message(warning, wrote_output)})
            steered = True

    return None, steered


def _consume_stream(
    process: subprocess.Popen, log_file, prefix: str,
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
    steered = False
    aborted = False
    wrote_output = False

    for raw_line in process.stdout:
        log_file.write(raw_line)
        log_file.flush()

        event_type, data = _parse_event_type(raw_line)

        if event_type == "response":
            continue

        prev_tool = _display_event(data, prev_tool, prefix)
        wrote_output = wrote_output or pi_write_tool_used(data)

        msg_cost = parse_pi_cost(data)
        if msg_cost is not None:
            accumulated_cost += msg_cost

        if event_type == "turn_end":
            turn_count += 1
            stop, steered = _check_limits(process, turn_count, accumulated_cost, max_turns, max_budget, steered, wrote_output) if not aborted else (None, steered)
            stop_reason, aborted = (stop, True) if stop else (stop_reason, aborted)

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
        "subtype": "success" if stop_reason == "completed" else stop_reason,
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


def preflight(models: Mapping[str, Sequence[str]], trail) -> bool:
    """No pre-run checks — Pi resolves models and provider routing itself.

    ``models`` and ``trail`` are accepted for interface parity with the
    Claude backend.
    """
    return True


def prompt(
    text: str, *, cwd: str, model: str | None = None,
) -> tuple[str, int, ai_usage.SessionUsage | None]:
    """Stateless text-in/text-out via pi -p. Returns (text, exit_code, usage).

    Pi's -p mode reports no usage, so the third element is always None — the
    ledger records nothing rather than a zeroed row that reads as a free call.
    """
    cmd = _build_prompt_cmd(model=model)
    result = subprocess.run(cmd, input=text, capture_output=True, text=True, cwd=cwd,
                            timeout=timeouts.UNBOUNDED)
    return result.stdout, result.returncode, None


def invoke_agent(inv: AgentInvocation) -> int:
    """Full agent with RPC streaming to session log. Returns exit code.

    Uses Pi's RPC mode for bidirectional control:
    - add_dirs are injected into the prompt text (Pi has no --add-dir flag)
    - max_turns is enforced by counting turn_end events and sending abort
    - max_budget is enforced by accumulating message_end costs and sending abort
    - A Claude-compatible result record is written at the end for cost tracking
    """
    dir_context = ""
    if inv.add_dirs:
        dir_lines = "\n".join(f"  - {d}" for d in inv.add_dirs)
        dir_context = f"\nAccessible directories:\n{dir_lines}\n\n"

    full_prompt = dir_context + inv.prompt if dir_context else inv.prompt

    ext = str(REVIEW_EXTENSION) if REVIEW_EXTENSION.is_file() else None
    cmd = _build_agent_cmd(inv, extension=ext)
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=inv.cwd,
        env=inv.env,
    )

    prefix = f"  {ANSI_DIM}[{inv.label}]{ANSI_RESET} " if inv.label else ""
    start_time = time.monotonic()

    # Send the prompt via RPC
    _send(proc, {"type": "prompt", "message": full_prompt})

    # Stream events with budget and turn enforcement
    with open(inv.session_log, "w") as log_fh:
        turn_count, accumulated_cost, stop_reason = _consume_stream(
            proc, log_fh, prefix,
            max_turns=inv.max_turns, max_budget=inv.max_budget,
        )

    duration_ms = int((time.monotonic() - start_time) * 1000)

    # Query authoritative stats after agent is done
    stats = _get_stats_after_agent_end(proc)

    # Write Claude-compatible result record
    _write_result_record(
        inv.session_log, stop_reason, turn_count,
        accumulated_cost, duration_ms, stats,
    )

    # Close stdin to terminate the RPC process — tolerate early exit
    try:
        proc.stdin.close()
    except BrokenPipeError:
        pass
    proc.wait()
    _log_stderr_on_failure(proc, inv.session_log)
    return proc.returncode


def invoke_fix(inv: AgentInvocation) -> int:
    """Agent with workspace write access via RPC. Returns exit code.

    Uses RPC mode for budget tracking and turn limits, same as invoke_agent.
    If session_log is empty, events are consumed but not persisted.
    """
    dir_context = ""
    if inv.add_dirs:
        dir_lines = "\n".join(f"  - {d}" for d in inv.add_dirs)
        dir_context = f"\nAccessible directories:\n{dir_lines}\n\n"

    full_prompt = dir_context + inv.prompt if dir_context else inv.prompt

    ext = str(REVIEW_EXTENSION) if REVIEW_EXTENSION.is_file() else None
    cmd = _build_fix_cmd(inv, extension=ext)
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=inv.cwd,
        env=inv.env,
    )

    start_time = time.monotonic()

    _send(proc, {"type": "prompt", "message": full_prompt})

    log_path = inv.session_log if inv.session_log else os.devnull
    with open(log_path, "w") as log_file:
        turn_count, accumulated_cost, stop_reason = _consume_stream(
            proc, log_file, "",
            max_turns=inv.max_turns, max_budget=inv.max_budget,
        )

    duration_ms = int((time.monotonic() - start_time) * 1000)

    if inv.session_log:
        stats = _get_stats_after_agent_end(proc)
        _write_result_record(
            inv.session_log, stop_reason, turn_count,
            accumulated_cost, duration_ms, stats,
        )

    try:
        proc.stdin.close()
    except BrokenPipeError:
        pass
    proc.wait()
    _log_stderr_on_failure(proc, inv.session_log)
    return proc.returncode
