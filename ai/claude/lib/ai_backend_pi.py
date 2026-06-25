"""Pi CLI backend for ai_backend.

Implements prompt(), invoke_agent(), and invoke_fix() by building
`pi` commands and running them as subprocesses.

Pi CLI reference:
  -p / --print     Prompt mode (non-interactive, like claude -p)
  --mode json      Stream JSONL events (like claude --output-format stream-json)
  --approve        Auto-accept project trust (like claude --permission-mode acceptEdits)
  --no-session     Ephemeral mode (don't persist session)
  --tools <list>   Allowlist specific tools
  --model <id>     Model selection
  --append-system-prompt <text>  Inject additional system prompt
  --verbose        Verbose output

Gaps vs Claude Code CLI:
  --max-turns      Not available in Pi; counted via turn_end events
  --max-budget-usd Not available in Pi; tracked via usage in message_end events
  --add-dir        Not available in Pi; directories passed in prompt text
  --agent          Not available in Pi; use --append-system-prompt with agent file contents
"""

from __future__ import annotations

import json
import signal
import subprocess
import sys
from pathlib import Path

from ai_backend_events import parse_pi_event
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
) -> list[str]:
    cmd = [
        "pi", "--mode", "json", "--no-session", "--approve", "--verbose",
        "--tools", PI_TOOLS,
    ]
    if agent:
        agent_prompt = _read_agent_prompt(agent)
        if agent_prompt:
            cmd += ["--append-system-prompt", agent_prompt]
    if model:
        cmd += ["--model", model]
    return cmd


def _build_fix_cmd(model: str | None = None) -> list[str]:
    cmd = [
        "pi", "-p", "--no-session", "--approve", "--verbose",
        "--tools", PI_TOOLS,
    ]
    if model:
        cmd += ["--model", model]
    return cmd


def _log_stderr_on_failure(proc: subprocess.Popen, session_log: str):
    if proc.returncode == 0:
        return
    stderr_output = proc.stderr.read()
    if not stderr_output:
        return
    with open(session_log, "a") as f:
        f.write(f"\n--- stderr (exit {proc.returncode}) ---\n{stderr_output}\n")


# ── Stream progress (Pi JSONL) ────────────────────────────────────────────────


def _display_event(raw_line: str, prev_tool: str, prefix: str) -> str:
    event = parse_pi_event(raw_line)
    if not event or event.tool_label == prev_tool:
        return prev_tool
    with _print_lock:
        print(f"{prefix}  {ANSI_DIM}▸ {event.tool_label}{ANSI_RESET}", flush=True)
    return event.tool_label


def _check_turn_limit(raw_line: str, turn_count: int, max_turns: int) -> tuple[int, bool]:
    """Check if a line is a turn_end event and if max_turns is reached."""
    try:
        data = json.loads(raw_line)
    except (json.JSONDecodeError, ValueError):
        return turn_count, False
    if data.get("type") != "turn_end":
        return turn_count, False
    turn_count += 1
    return turn_count, turn_count >= max_turns


def _consume_stream(process: subprocess.Popen, log, prefix: str,
                    max_turns: int | None = None):
    prev_tool = ""
    turn_count = 0
    for raw_line in process.stdout:
        log.write(raw_line)
        log.flush()
        prev_tool = _display_event(raw_line, prev_tool, prefix)
        if max_turns is None:
            continue
        turn_count, exceeded = _check_turn_limit(raw_line, turn_count, max_turns)
        if not exceeded:
            continue
        process.send_signal(signal.SIGTERM)
        process.stdout.close()
        break


def _stream_progress_pi(process: subprocess.Popen, session_log: str, label: str = "",
                        max_turns: int | None = None):
    """Stream Pi JSONL events to session log and display progress."""
    prefix = f"  {ANSI_DIM}[{label}]{ANSI_RESET} " if label else ""
    with open(session_log, "w") as log:
        _consume_stream(process, log, prefix, max_turns)


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
    label: str = "",
) -> int:
    """Full agent with JSONL streaming to session log. Returns exit code.

    add_dirs are injected into the prompt text since Pi has no --add-dir flag.
    max_turns is enforced by counting turn_end events and sending SIGTERM.
    max_budget is not supported by Pi CLI (ignored with a warning).
    """
    if max_budget is not None:
        print(f"  {ANSI_DIM}(pi backend: --max-budget-usd not supported, ignoring){ANSI_RESET}",
              file=sys.stderr)

    # Inject directory context into prompt since Pi has no --add-dir
    dir_context = ""
    if add_dirs:
        dir_lines = "\n".join(f"  - {d}" for d in add_dirs)
        dir_context = f"\nAccessible directories:\n{dir_lines}\n\n"

    full_prompt = dir_context + prompt if dir_context else prompt

    cmd = _build_agent_cmd(agent=agent, model=model)
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    proc.stdin.write(full_prompt)
    proc.stdin.close()
    _stream_progress_pi(proc, session_log, label=label, max_turns=max_turns)
    proc.wait()
    _log_stderr_on_failure(proc, session_log)
    return proc.returncode


def invoke_fix(
    prompt: str, *,
    add_dirs: list[str],
    max_turns: int | None = None,
    model: str | None = None,
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

    cmd = _build_fix_cmd(model=model)
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
