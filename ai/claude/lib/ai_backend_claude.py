"""Claude Code CLI backend for ai_backend.

Implements prompt(), invoke_agent(), and invoke_fix() by building
`claude -p` commands and running them as subprocesses.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import log
from ai_backend_events import _log_stderr_on_failure, parse_claude_event
from log import ANSI_DIM, ANSI_RESET, _print_lock


# ── Agent resolution ─────────────────────────────────────────────────────────

_AGENTS_DIR = Path.home() / ".claude" / "agents"


def _load_agent_def(name: str) -> dict | None:
    """Load a custom agent definition from ~/.claude/agents/{name}.md.

    --bare mode skips auto-discovery of custom agents, so we read the file
    and pass it via --agents JSON to make it available to the CLI.
    """
    path = _AGENTS_DIR / f"{name}.md"
    if not path.exists():
        return None

    content = path.read_text()
    if not content.startswith("---"):
        return {"description": name, "prompt": content}

    parts = content.split("---", 2)
    if len(parts) < 3:
        return {"description": name, "prompt": content}

    description = name
    for line in parts[1].strip().splitlines():
        if line.startswith("description:"):
            description = line.split(":", 1)[1].strip()
            break

    return {"description": description, "prompt": parts[2].strip()}


# ── Stream progress ───────────────────────────────────────────────────────────


def _display_event(raw_line: str, prev_tool: str, prefix: str) -> str:
    event = parse_claude_event(raw_line)
    if not event or event.tool_label == prev_tool:
        return prev_tool
    with _print_lock:
        print(f"{prefix}  {ANSI_DIM}▸ {event.tool_label}{ANSI_RESET}", file=sys.stderr, flush=True)
    return event.tool_label


def stream_progress(process: subprocess.Popen, session_log: str, label: str = ""):
    prev_tool = ""
    prefix = f"  {ANSI_DIM}[{label}]{ANSI_RESET} " if label else ""
    with open(session_log, "w") as log:
        for raw_line in process.stdout:
            log.write(raw_line)
            log.flush()
            prev_tool = _display_event(raw_line, prev_tool, prefix)


# ── Command builders ──────────────────────────────────────────────────────────


def _base_cmd() -> list[str]:
    """Shared flags used by both agent and fix commands."""
    return [
        "claude", "-p", "--bare", "--verbose",
        "--permission-mode", "acceptEdits",
        "--allowedTools", "Bash(*)",
        "--disable-slash-commands",
    ]


def _build_agent_cmd(
    add_dirs: list[str],
    agent: str | None = None,
    max_turns: int | None = None,
    max_budget: float | None = None,
    model: str | None = None,
    # thinking_level is accepted for API compatibility but intentionally unused:
    # Claude Code CLI has no --thinking flag.
    thinking_level: str | None = None,
) -> list[str]:
    add_dir_args = []
    for d in add_dirs:
        add_dir_args += ["--add-dir", d]
    cmd = [*_base_cmd(), "--output-format", "stream-json", *add_dir_args]
    if agent:
        agent_def = _load_agent_def(agent)
        if agent_def:
            cmd += ["--agents", json.dumps({agent: agent_def})]
        cmd += ["--agent", agent]
    if max_turns is not None:
        cmd += ["--max-turns", str(max_turns)]
    if max_budget is not None:
        cmd += ["--max-budget-usd", str(max_budget)]
    if model:
        cmd += ["--model", model]
    return cmd


def _build_fix_cmd(
    add_dirs: list[str],
    max_turns: int | None = None,
    max_budget: float | None = None,
    model: str | None = None,
    # thinking_level is accepted for API compatibility but intentionally unused:
    # Claude Code CLI has no --thinking flag.
    thinking_level: str | None = None,
) -> list[str]:
    add_dir_args = []
    for d in add_dirs:
        add_dir_args += ["--add-dir", d]
    cmd = [*_base_cmd(), *add_dir_args]
    if max_turns is not None:
        cmd += ["--max-turns", str(max_turns)]
    if max_budget is not None:
        cmd += ["--max-budget-usd", str(max_budget)]
    if model:
        cmd += ["--model", model]
    return cmd


def _build_prompt_cmd(model: str | None = None) -> list[str]:
    cmd = ["claude", "-p", "--bare"]
    if model:
        cmd += ["--model", model]
    return cmd


# ── Subprocess helpers ───────────────────────────────────────────────────────


def _send_stdin(proc: subprocess.Popen, text: str) -> None:
    """Write text to subprocess stdin and close, tolerating early exit."""
    try:
        proc.stdin.write(text)
    except BrokenPipeError:
        pass
    try:
        proc.stdin.close()
    except BrokenPipeError:
        pass


# ── Public interface ──────────────────────────────────────────────────────────


def prompt(text: str, *, model: str | None = None) -> tuple[str, int]:
    """Stateless text-in/text-out via claude -p."""
    cmd = _build_prompt_cmd(model=model)
    result = subprocess.run(cmd, input=text, capture_output=True, text=True)
    if result.returncode != 0 and result.stderr:
        log.dim(result.stderr.strip())
    return result.stdout, result.returncode


def invoke_agent(
    prompt: str, session_log: str, *,
    add_dirs: list[str],
    agent: str | None = None,
    max_turns: int | None = None,
    max_budget: float | None = None,
    model: str | None = None,
    thinking_level: str | None = None,
    # provider is accepted for interface parity with the Pi backend but intentionally
    # unused — Claude Code CLI has no --provider flag.
    provider: str | None = None,
    label: str = "",
) -> int:
    """Full agent with JSONL streaming to session log. Returns exit code."""
    cmd = _build_agent_cmd(
        add_dirs=add_dirs, agent=agent,
        max_turns=max_turns, max_budget=max_budget,
        model=model, thinking_level=thinking_level,
    )
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    _send_stdin(proc, prompt)
    stream_progress(proc, session_log, label=label)
    proc.wait()
    _log_stderr_on_failure(proc, session_log)
    return proc.returncode


def invoke_fix(
    prompt: str, *,
    session_log: str = "",
    add_dirs: list[str],
    max_turns: int | None = None,
    max_budget: float | None = None,
    model: str | None = None,
    thinking_level: str | None = None,
    # provider is accepted for interface parity with the Pi backend but intentionally
    # unused — Claude Code CLI has no --provider flag.
    provider: str | None = None,
) -> int:
    """Agent with workspace write access, raw output echoed to stderr. Returns exit code."""
    # session_log and provider are accepted for interface parity with the Pi backend
    # but are not used — Claude Code CLI has no --provider flag.
    cmd = _build_fix_cmd(
        add_dirs=add_dirs, max_turns=max_turns, max_budget=max_budget,
        model=model, thinking_level=thinking_level,
    )
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=sys.stderr,
        text=True,
    )
    _send_stdin(proc, prompt)
    for line in proc.stdout:
        print(line, end="", file=sys.stderr)
    proc.wait()
    return proc.returncode
