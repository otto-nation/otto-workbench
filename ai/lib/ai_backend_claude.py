"""Claude Code CLI backend for ai_backend.

Implements preflight(), prompt(), invoke_agent(), and invoke_fix() by
building `claude -p` commands and running them as subprocesses.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import ai_usage
import log
import vertex_quota
from ai_backend import AgentInvocation
from ai_backend_events import _log_stderr_on_failure, claude_display_text, parse_claude_event
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


def _build_agent_cmd(inv: AgentInvocation) -> list[str]:
    add_dir_args = []
    for d in inv.add_dirs:
        add_dir_args += ["--add-dir", d]
    cmd = [*_base_cmd(), "--output-format", "stream-json", *add_dir_args]
    if inv.agent:
        agent_def = _load_agent_def(inv.agent)
        if agent_def:
            cmd += ["--agents", json.dumps({inv.agent: agent_def})]
        cmd += ["--agent", inv.agent]
    if inv.max_turns is not None:
        cmd += ["--max-turns", str(inv.max_turns)]
    if inv.max_budget is not None:
        cmd += ["--max-budget-usd", str(inv.max_budget)]
    if inv.model:
        cmd += ["--model", inv.model]
    return cmd


def _build_fix_cmd(inv: AgentInvocation) -> list[str]:
    add_dir_args = []
    for d in inv.add_dirs:
        add_dir_args += ["--add-dir", d]
    cmd = [*_base_cmd(), "--output-format", "stream-json", *add_dir_args]
    if inv.max_turns is not None:
        cmd += ["--max-turns", str(inv.max_turns)]
    if inv.max_budget is not None:
        cmd += ["--max-budget-usd", str(inv.max_budget)]
    if inv.model:
        cmd += ["--model", inv.model]
    return cmd


def _build_prompt_cmd(model: str | None = None) -> list[str]:
    # --output-format needs --print, which -p already supplies. Without it the reply
    # carries no usage and every prompt() call goes unmeasured.
    cmd = ["claude", "-p", "--bare", "--output-format", "json"]
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


def preflight(models: Mapping[str, Sequence[str]], trail) -> bool:
    """Check Vertex AI quota when the CLI is pointed at Vertex.

    No-ops on the first-party API, where model availability is not a
    per-project allocation the client can inspect ahead of time.
    """
    return vertex_quota.run_preflight(models, trail)


def prompt(
    text: str, *, model: str | None = None,
) -> tuple[str, int, ai_usage.SessionUsage | None]:
    """Stateless text-in/text-out via claude -p. Returns (text, exit_code, usage).

    usage is None when the reply carried no envelope to measure.
    """
    cmd = _build_prompt_cmd(model=model)
    result = subprocess.run(cmd, input=text, capture_output=True, text=True)
    if result.returncode != 0 and result.stderr:
        log.dim(result.stderr.strip())
    reply, usage = _unwrap_prompt_output(result.stdout)
    return reply, result.returncode, usage


def _unwrap_prompt_output(stdout: str) -> tuple[str, ai_usage.SessionUsage | None]:
    """Pull the reply text and usage out of a --output-format json envelope.

    Falls back to the raw stdout if it is not the expected envelope, so a CLI
    output change degrades to the previous behavior instead of breaking callers.
    An unparseable reply yields no usage rather than a zeroed one — nothing was
    measured, and a zero row would read as a free call.
    """
    try:
        envelope = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return stdout, None
    if not isinstance(envelope, dict):
        return stdout, None
    usage = ai_usage.usage_from_records([envelope])
    reply = envelope.get("result")
    return (reply if isinstance(reply, str) else stdout), usage


def invoke_agent(inv: AgentInvocation) -> int:
    """Full agent with JSONL streaming to session log. Returns exit code."""
    cmd = _build_agent_cmd(inv)
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    _send_stdin(proc, inv.prompt)
    stream_progress(proc, inv.session_log, label=inv.label)
    proc.wait()
    _log_stderr_on_failure(proc, inv.session_log)
    return proc.returncode


def invoke_fix(inv: AgentInvocation) -> int:
    """Agent with workspace write access, progress echoed to stderr. Returns exit code."""
    cmd = _build_fix_cmd(inv)
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=sys.stderr,
        text=True,
    )
    _send_stdin(proc, inv.prompt)
    _stream_fix_output(proc, inv.session_log)
    proc.wait()
    return proc.returncode


def _stream_fix_output(proc: subprocess.Popen, session_log: str) -> None:
    """Echo readable progress to stderr while capturing the raw stream for accounting.

    stdout is stream-json now, so the raw lines it used to print would be unreadable.
    """
    sink = open(session_log, "w") if session_log else None
    try:
        for raw_line in proc.stdout:
            if sink:
                sink.write(raw_line)
                sink.flush()
            display = claude_display_text(raw_line)
            if display:
                print(display, file=sys.stderr, flush=True)
    finally:
        if sink:
            sink.close()
