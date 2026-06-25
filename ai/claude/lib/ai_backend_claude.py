"""Claude Code CLI backend for ai_backend.

Implements prompt(), invoke_agent(), and invoke_fix() by building
`claude -p` commands and running them as subprocesses.
"""

from __future__ import annotations

import subprocess
import sys

from ai_backend_events import parse_claude_event
from review_common import ANSI_DIM, ANSI_RESET, _print_lock


# ── Stream progress ───────────────────────────────────────────────────────────


def _display_event(raw_line: str, prev_tool: str, prefix: str) -> str:
    event = parse_claude_event(raw_line)
    if not event or event.tool_label == prev_tool:
        return prev_tool
    with _print_lock:
        print(f"{prefix}  {ANSI_DIM}▸ {event.tool_label}{ANSI_RESET}", flush=True)
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


def _build_agent_cmd(
    add_dirs: list[str],
    agent: str | None = None,
    max_turns: int | None = None,
    max_budget: float | None = None,
    model: str | None = None,
    thinking_level: str | None = None,
) -> list[str]:
    add_dir_args = []
    for d in add_dirs:
        add_dir_args += ["--add-dir", d]
    cmd = [
        "claude", "-p", "--bare", "--verbose", "--output-format", "stream-json",
        "--permission-mode", "acceptEdits",
        "--allowedTools", "Bash(*)",
        "--disable-slash-commands",
        *add_dir_args,
    ]
    if agent:
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
    model: str | None = None,
    thinking_level: str | None = None,
) -> list[str]:
    add_dir_args = []
    for d in add_dirs:
        add_dir_args += ["--add-dir", d]
    cmd = [
        "claude", "-p", "--bare", "--verbose",
        "--permission-mode", "acceptEdits",
        "--allowedTools", "Bash(*)",
        "--disable-slash-commands",
        *add_dir_args,
    ]
    if max_turns is not None:
        cmd += ["--max-turns", str(max_turns)]
    if model:
        cmd += ["--model", model]
    return cmd


def _build_prompt_cmd(model: str | None = None) -> list[str]:
    cmd = ["claude", "-p", "--bare"]
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


# ── Public interface ──────────────────────────────────────────────────────────


def prompt(text: str, *, model: str | None = None) -> tuple[str, int]:
    """Stateless text-in/text-out via claude -p."""
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
    proc.stdin.write(prompt)
    proc.stdin.close()
    stream_progress(proc, session_log, label=label)
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
    cmd = _build_fix_cmd(
        add_dirs=add_dirs, max_turns=max_turns,
        model=model, thinking_level=thinking_level,
    )
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=sys.stderr,
        text=True,
    )
    proc.stdin.write(prompt)
    proc.stdin.close()
    for line in proc.stdout:
        print(line, end="", file=sys.stderr)
    proc.wait()
    return proc.returncode
