"""Agent invocation, cost tracking, model selection, and diagnostics.

Delegates actual AI invocation to ai_backend (which dispatches to
Claude Code CLI or Pi CLI based on AI_BACKEND env var). This module
adds cost tracking, failure diagnosis, and output recovery on top.
"""

from __future__ import annotations

import json
import os
import threading
import time
from enum import StrEnum
from pathlib import Path

import ai_backend
import log
from ai_backend_events import is_write_tool
from review_common import AgentKind, preserve_log, restore_preserved

DEFAULT_MAX_TURNS = 10
DEFAULT_MAX_BUDGET_PER_AGENT = 5.0
CONSECUTIVE_FAIL_THRESHOLD = 3

DIAG_NO_SESSION_LOG = "no session log found"
DIAG_NO_RESULT_RECORD = "no result record in session log"
DIAG_QUOTA_EXHAUSTED = "quota exhausted (429)"
DIAG_NO_WRITE_TOOL_CALL = "never called a file-writing tool"

# Load-bearing: both the no-write diagnosis and the transient-error check key
# off this prefix to tell a crash apart from a run that ended on its own terms.
AGENT_ERROR_PREFIX = "agent error:"

_TRANSIENT_ERROR_MARKERS = (
    "FailedToOpenSocket",
    "ConnectionRefused",
    "ConnectionReset",
    "Connection to the API was lost",
    "ECONNREFUSED",
    "ECONNRESET",
    "ETIMEDOUT",
    "socket hang up",
)


# ── Cost tracking ────────────────────────────────────────────────────────────

def _try_parse_json(line: str) -> dict | None:
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def _read_jsonl(log_path: str) -> list[dict]:
    """Every parseable record in the log, in order.

    Callers that need more than one record type should read once and filter
    with `_of_type` rather than making a pass per type.
    """
    with open(log_path) as f:
        parsed = (_try_parse_json(line) for line in f)
        return [d for d in parsed if d is not None]


def _of_type(records: list[dict], record_type: str) -> list[dict]:
    return [d for d in records if d.get("type") == record_type]


def _parse_jsonl_records(log_path: str, record_type: str) -> list[dict]:
    return _of_type(_read_jsonl(log_path), record_type)


def _parse_session_cost(log_path: str) -> float:
    if not Path(log_path).exists():
        return 0.0
    results = _parse_jsonl_records(log_path, "result")
    return sum(r.get("total_cost_usd", 0.0) for r in results)


def _diagnose_result_type(result: dict) -> str:
    subtype = result.get("subtype", "")
    if "max_turns" in subtype:
        num_turns = result.get("num_turns", "?")
        return f"agent hit max turns ({num_turns})"
    if result.get("is_error"):
        errors = result.get("errors", [])
        detail = errors[0] if errors else result.get("result", result.get("error", "unknown"))
        return f"{AGENT_ERROR_PREFIX} {detail}"
    return f"agent completed (subtype={subtype}) but did not write output"


def _tool_names_used(records: list[dict]) -> set[str]:
    """Names of every tool the agent invoked, per the session log.

    Only the Claude backend writes `assistant` records with `tool_use` blocks;
    for other backends this is empty and callers must not read that as "no
    tools were used".
    """
    return {
        block.get("name", "")
        for record in _of_type(records, "assistant")
        for block in record.get("message", {}).get("content", [])
        if block.get("type") == "tool_use"
    }


def diagnose_missing_output(log_path: str) -> str:
    """Why an agent run left no output, read from its session log.

    Public because `agent_retry` decides retryability from the returned reason;
    the DIAG_* constants above are the labels it matches on.
    """
    if not Path(log_path).exists():
        return DIAG_NO_SESSION_LOG
    records = _read_jsonl(log_path)
    results = _of_type(records, "result")
    if not results:
        if _has_quota_retry(records):
            return DIAG_QUOTA_EXHAUSTED
        return DIAG_NO_RESULT_RECORD
    reason = _diagnose_result_type(results[-1])
    # An agent that ran to its own conclusion without ever calling a write tool
    # was thrashing, not working — say so instead of reporting a bare turn
    # count. A crash is excluded: the error already explains the missing output,
    # and a retry would most likely reproduce it.
    tools_used = _tool_names_used(records)
    crashed = reason.startswith(AGENT_ERROR_PREFIX)
    if not crashed and tools_used and not any(is_write_tool(name) for name in tools_used):
        reason += f" — {DIAG_NO_WRITE_TOOL_CALL}"
    return reason


def is_transient_error(reason: str) -> bool:
    if not reason.startswith(AGENT_ERROR_PREFIX):
        return False
    return any(marker in reason for marker in _TRANSIENT_ERROR_MARKERS)


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


def try_recover_output(log_path: str, output_path: str) -> bool:
    """Salvage a document the agent wrote but was denied permission to save.

    Public because `agent_retry` runs this before writing a run off as
    unproductive — the content is in the denial record either way.
    """
    if not Path(log_path).exists():
        return False
    for content in _collect_denied_contents(log_path):
        if "## " not in content:
            continue
        Path(output_path).write_text(content + "\n")
        log.warn(f"Recovered review from denied write — saved to {output_path}")
        return True
    return False


# ── Quota throttle ─────────────────────────────────────────────────────────


class QuotaThrottle:
    """Thread-safe throttle shared across pipeline agents.

    When any agent hits a 429, all pending agents wait before launching.
    """

    def __init__(self, backoff: float = 30.0, max_backoff: float = 120.0):
        self._lock = threading.Lock()
        self._resume_at: float = 0.0
        self._backoff = backoff
        self._max_backoff = max_backoff

    def report_exhausted(self, model: str) -> float:
        with self._lock:
            wait = self._backoff
            self._resume_at = time.monotonic() + wait
            self._backoff = min(self._backoff * 2, self._max_backoff)
        log.warn(f"Quota exhausted on {model} — backing off {wait:.0f}s")
        return wait

    def wait_if_needed(self) -> None:
        with self._lock:
            resume_at = self._resume_at
        remaining = resume_at - time.monotonic()
        if remaining > 0:
            log.info(f"Throttle: waiting {remaining:.0f}s for quota to recover")
            time.sleep(remaining)


# ── Quota detection ────────────────────────────────────────────────────────

def _has_quota_retry(records: list[dict]) -> bool:
    return any(
        r.get("subtype") == "api_retry" and r.get("error_status") == 429
        for r in _of_type(records, "system")
    )


def _is_quota_error(log_path: str) -> bool:
    if not Path(log_path).exists():
        return False
    return _has_quota_retry(_read_jsonl(log_path))


# ── Model selection ───────────────────────────────────────────────────────────

class ModelAlias(StrEnum):
    """Short model names that resolve to a concrete model id via env override.

    Anything not listed here is already a concrete id and passes through
    untouched.
    """

    SONNET = "sonnet"
    OPUS = "opus"
    HAIKU = "haiku"

    @property
    def env_key(self) -> str:
        return f"ANTHROPIC_DEFAULT_{self.upper()}_MODEL"

    @classmethod
    def parse(cls, model: str) -> ModelAlias | None:
        try:
            return cls(model)
        except ValueError:
            return None


def _resolve_alias(model: str) -> str:
    alias = ModelAlias.parse(model)
    if alias is None:
        return model
    return os.environ.get(alias.env_key) or model


def _resolve_model(explicit: str | None, env_key: str, default: str) -> str:
    if explicit:
        return _resolve_alias(explicit)
    from_env = os.environ.get(env_key)
    if from_env:
        return _resolve_alias(from_env)
    global_env = os.environ.get("CLAUDE_REVIEW_MODEL")
    if global_env:
        return _resolve_alias(global_env)
    return _resolve_alias(default)


def _resolve_thinking_level(explicit: str | None, env_key: str, default: str | None) -> str | None:
    if explicit:
        return explicit
    from_env = os.environ.get(env_key)
    if from_env:
        return from_env
    global_env = os.environ.get("CLAUDE_REVIEW_THINKING")
    if global_env:
        return global_env
    return default


def _resolve_provider() -> str | None:
    return os.environ.get("CLAUDE_REVIEW_PROVIDER")


# ── Agent invocation ──────────────────────────────────────────────────────────


def _invoke_once(prompt, session_log, add_dirs, agent, max_turns, max_budget,
                  model, thinking_level, provider, label):
    prior_log = preserve_log(session_log)
    rc = ai_backend.invoke_agent(
        prompt, session_log,
        add_dirs=add_dirs, agent=agent,
        max_turns=max_turns, max_budget=max_budget,
        model=model, thinking_level=thinking_level,
        provider=provider, label=label,
    )
    restore_preserved(session_log, prior_log)
    return rc


def invoke_agent(
    prompt: str, session_log: str, wt_path: str, reviews_dir: str,
    label: str = "", review_file: str = "",
    max_turns: int | None = DEFAULT_MAX_TURNS,
    max_budget: float | None = DEFAULT_MAX_BUDGET_PER_AGENT,
    model: str | None = None,
    thinking_level: str | None = None,
    provider: str | None = None,
    agent: AgentKind = AgentKind.REVIEWER,
    throttle: QuotaThrottle | None = None,
) -> int:
    add_dirs = [reviews_dir, wt_path]
    if review_file:
        review_dir = str(Path(review_file).parent)
        if review_dir not in (reviews_dir, wt_path):
            add_dirs.append(review_dir)

    if throttle:
        throttle.wait_if_needed()

    rc = ai_backend.invoke_agent(
        prompt, session_log,
        add_dirs=add_dirs, agent=agent,
        max_turns=max_turns, max_budget=max_budget,
        model=model, thinking_level=thinking_level,
        provider=provider, label=label,
    )
    if rc != 0 and model and _is_quota_error(session_log):
        if throttle:
            wait = throttle.report_exhausted(model)
            time.sleep(wait)
        else:
            log.warn(f"Quota exhausted on {model} — retrying once after 30s backoff")
            time.sleep(30)
        rc = _invoke_once(prompt, session_log, add_dirs, agent, max_turns,
                          max_budget, model, thinking_level, provider, label)
    return rc


