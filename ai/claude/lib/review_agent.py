"""Agent invocation, streaming, cost tracking, and model selection.

Handles spawning Claude reviewer agents, streaming their output,
parsing session costs, and diagnosing failures.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from review_common import (
    ANSI_DIM, ANSI_RESET,
    _info, _print_lock, _warn,
)

DEFAULT_MAX_TURNS = 10
DEFAULT_MAX_BUDGET_PER_AGENT = 5.0
CONSECUTIVE_FAIL_THRESHOLD = 3


# ── Stream progress ───────────────────────────────────────────────────────────

def _tool_label_from_block(block: dict) -> str:
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


def _process_stream_line(raw_line: str, prev_tool: str, prefix: str) -> str:
    try:
        data = json.loads(raw_line)
    except (json.JSONDecodeError, ValueError):
        return prev_tool
    if data.get("type") != "assistant":
        return prev_tool
    for block in data.get("message", {}).get("content", []):
        tool_label = _tool_label_from_block(block)
        if not tool_label or tool_label == prev_tool:
            continue
        with _print_lock:
            print(f"{prefix}  {ANSI_DIM}▸ {tool_label}{ANSI_RESET}", flush=True)
        prev_tool = tool_label
    return prev_tool


def stream_progress(process: subprocess.Popen, session_log: str, label: str = ""):
    prev_tool = ""
    prefix = f"  {ANSI_DIM}[{label}]{ANSI_RESET} " if label else ""
    with open(session_log, "w") as log:
        for raw_line in process.stdout:
            log.write(raw_line)
            log.flush()
            prev_tool = _process_stream_line(raw_line, prev_tool, prefix)


# ── Cost tracking ────────────────────────────────────────────────────────────

def _try_parse_json(line: str) -> dict | None:
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def _parse_jsonl_records(log_path: str, record_type: str) -> list[dict]:
    with open(log_path) as f:
        lines = f.readlines()
    parsed = (_try_parse_json(line) for line in lines)
    return [d for d in parsed if d is not None and d.get("type") == record_type]


def _parse_session_cost(log_path: str) -> float:
    if not Path(log_path).exists():
        return 0.0
    results = _parse_jsonl_records(log_path, "result")
    return sum(r.get("total_cost_usd", 0.0) for r in results)


def _check_budget(logs: list[str], max_cost: float) -> tuple[float, bool]:
    total = sum(_parse_session_cost(log) for log in logs)
    return total, total > max_cost


def _diagnose_result_type(result: dict) -> str:
    subtype = result.get("subtype", "")
    if "max_turns" in subtype:
        num_turns = result.get("num_turns", "?")
        return f"agent hit max turns ({num_turns})"
    if result.get("is_error"):
        errors = result.get("errors", [])
        detail = errors[0] if errors else result.get("result", result.get("error", "unknown"))
        return f"agent error: {detail}"
    return f"agent completed (subtype={subtype}) but did not write output"


def _diagnose_missing_output(log_path: str) -> str:
    if not Path(log_path).exists():
        return "no session log found"
    results = _parse_jsonl_records(log_path, "result")
    if not results:
        return "no result record in session log"
    return _diagnose_result_type(results[-1])


def _is_model_error(log_path: str) -> bool:
    if not Path(log_path).exists():
        return False
    results = _parse_jsonl_records(log_path, "result")
    if not results:
        return False
    result = results[-1]
    if result.get("api_error_status") == 404:
        return True
    text = result.get("result", "")
    return isinstance(text, str) and "not available" in text.lower()


def _extract_heredoc(cmd: str) -> str:
    lines = cmd.split("\n")
    start = next((i for i, l in enumerate(lines) if "<<" in l), -1)
    if start < 0:
        return ""
    end = next((i for i in range(len(lines) - 1, start, -1) if lines[i].strip() in ("REVIEW_EOF", "EOF")), -1)
    if end < 0:
        return ""
    return "\n".join(lines[start + 1:end])


def _extract_denied_content(denial: dict) -> str:
    tool_input = denial.get("tool_input", {})
    content = tool_input.get("content", "")
    if content:
        return content
    cmd = tool_input.get("command", "")
    if "REVIEW_EOF" not in cmd and "EOF" not in cmd:
        return ""
    return _extract_heredoc(cmd)


def _collect_denied_contents(log_path: str) -> list[str]:
    results = _parse_jsonl_records(log_path, "result")
    denials = [d for r in results for d in r.get("permission_denials", [])]
    return [_extract_denied_content(d) for d in denials]


def _try_recover_output(log_path: str, output_path: str) -> bool:
    if not Path(log_path).exists():
        return False
    for content in _collect_denied_contents(log_path):
        if "## " not in content:
            continue
        Path(output_path).write_text(content + "\n")
        _warn(f"Recovered review from denied write — saved to {output_path}")
        return True
    return False


def _try_recover_review(job: "ReviewJob"):
    _try_recover_output(job.session_log, job.review_file)


# ── Model selection ───────────────────────────────────────────────────────────

def _resolve_model(explicit: str | None, env_key: str, default: str) -> str:
    if explicit:
        return explicit
    from_env = os.environ.get(env_key)
    if from_env:
        return from_env
    global_env = os.environ.get("CLAUDE_REVIEW_MODEL")
    if global_env:
        return global_env
    return default


# ── Agent invocation ──────────────────────────────────────────────────────────

def _build_agent_cmd(
    reviews_dir: str, wt_path: str,
    review_file: str = "",
    max_turns: int | None = None,
    max_budget: float | None = None,
    model: str | None = None,
) -> list[str]:
    add_dir_args = ["--add-dir", reviews_dir, "--add-dir", wt_path]
    if review_file:
        review_dir = str(Path(review_file).parent)
        if review_dir not in (reviews_dir, wt_path):
            add_dir_args += ["--add-dir", review_dir]
    cmd = [
        "claude", "-p", "--bare", "--verbose", "--output-format", "stream-json",
        "--permission-mode", "acceptEdits",
        "--allowedTools", "Bash(*)",
        "--disable-slash-commands",
        *add_dir_args,
        "--agent", "reviewer",
    ]
    if max_turns is not None:
        cmd += ["--max-turns", str(max_turns)]
    if max_budget is not None:
        cmd += ["--max-budget-usd", str(max_budget)]
    if model:
        cmd += ["--model", model]
    return cmd


def _log_stderr_on_failure(proc: subprocess.Popen, session_log: str):
    stderr_output = proc.stderr.read()
    if not stderr_output:
        return
    with open(session_log, "a") as f:
        f.write(f"\n--- stderr (exit {proc.returncode}) ---\n{stderr_output}\n")


def invoke_agent(
    prompt: str, session_log: str, wt_path: str, reviews_dir: str,
    label: str = "", review_file: str = "",
    max_turns: int | None = DEFAULT_MAX_TURNS,
    max_budget: float | None = DEFAULT_MAX_BUDGET_PER_AGENT,
    model: str | None = None,
) -> int:
    cmd = _build_agent_cmd(
        reviews_dir, wt_path, review_file=review_file,
        max_turns=max_turns, max_budget=max_budget, model=model,
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
    if proc.returncode != 0:
        _log_stderr_on_failure(proc, session_log)
    return proc.returncode


