"""Agent invocation, cost tracking, model selection, and diagnostics.

Delegates actual AI invocation to ai_backend (which dispatches to
Claude Code CLI or Pi CLI based on AI_BACKEND env var). This module
adds cost tracking, failure diagnosis, and output recovery on top.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import ai_backend
from review_common import _warn

DEFAULT_MAX_TURNS = 10
DEFAULT_MAX_BUDGET_PER_AGENT = 5.0
CONSECUTIVE_FAIL_THRESHOLD = 3

DIAG_NO_SESSION_LOG = "no session log found"
DIAG_NO_RESULT_RECORD = "no result record in session log"


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
        return DIAG_NO_SESSION_LOG
    results = _parse_jsonl_records(log_path, "result")
    if not results:
        return DIAG_NO_RESULT_RECORD
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


def invoke_agent(
    prompt: str, session_log: str, wt_path: str, reviews_dir: str,
    label: str = "", review_file: str = "",
    max_turns: int | None = DEFAULT_MAX_TURNS,
    max_budget: float | None = DEFAULT_MAX_BUDGET_PER_AGENT,
    model: str | None = None,
    thinking_level: str | None = None,
) -> int:
    add_dirs = [reviews_dir, wt_path]
    if review_file:
        review_dir = str(Path(review_file).parent)
        if review_dir not in (reviews_dir, wt_path):
            add_dirs.append(review_dir)
    return ai_backend.invoke_agent(
        prompt, session_log,
        add_dirs=add_dirs, agent="reviewer",
        max_turns=max_turns, max_budget=max_budget,
        model=model, thinking_level=thinking_level, label=label,
    )


