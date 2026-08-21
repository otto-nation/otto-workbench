"""Agent invocation, cost tracking, model selection, and diagnostics.

Delegates actual AI invocation to ai_backend (which dispatches to
Claude Code CLI or Pi CLI based on AI_BACKEND env var). This module
adds cost tracking, failure diagnosis, and output recovery on top.

**Which model a phase uses.** Every review phase resolves its model through one
chain, most specific first:

1. an explicit ``--model`` on the command
2. the phase's own key — ``CLAUDE_REVIEW_GROUP_MODEL``,
   ``CLAUDE_REVIEW_HOLISTIC_MODEL``, ``CLAUDE_REVIEW_SINGLE_MODEL``,
   ``CLAUDE_REVIEW_SCOUT_MODEL``, ``CLAUDE_REVIEW_DISPROVE_MODEL``,
   ``CLAUDE_REVIEW_FIX_MODEL``, ``CLAUDE_REVIEW_SYNTHESIS_MODEL``
3. ``CLAUDE_REVIEW_MODEL``, which covers every phase at once
4. the phase's built-in default

Whichever wins, a bare tier alias (``sonnet``, ``opus``, ``haiku``) is then
resolved through ``ANTHROPIC_DEFAULT_SONNET_MODEL`` /
``ANTHROPIC_DEFAULT_OPUS_MODEL`` / ``ANTHROPIC_DEFAULT_HAIKU_MODEL``. An alias
names a tier, not a deployment — on Vertex and Bedrock the account provisions a
specific model ID, and that is where it lives. A concrete model ID anywhere in
the chain passes through untouched.

The Claude CLI does this resolution itself; the Pi backend does not, so the
resolution happens here before dispatch and both backends land on the same
model.
"""

# doc-group: pipeline

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import replace
from enum import StrEnum
from pathlib import Path

import ai_backend
import log
from ai_backend import AgentInvocation
from ai_backend_events import is_write_tool
from review_common import (
    Diagnosis, DiagnosisKind,
    preserve_log, restore_preserved,
)

CONSECUTIVE_FAIL_THRESHOLD = 3

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


def _diagnose_result_type(result: dict) -> Diagnosis:
    subtype = result.get("subtype", "")
    if "max_turns" in subtype:
        return Diagnosis(DiagnosisKind.MAX_TURNS, num_turns=result.get("num_turns"))
    if result.get("is_error"):
        errors = result.get("errors", [])
        detail = errors[0] if errors else result.get("result", result.get("error", "unknown"))
        detail = str(detail)
        kind = (
            DiagnosisKind.TRANSIENT
            if _detail_is_transient(detail)
            else DiagnosisKind.AGENT_ERROR
        )
        return Diagnosis(kind, detail=detail)
    return Diagnosis(DiagnosisKind.COMPLETED, detail=subtype)


def _tool_names_used(records: list[dict]) -> set[str]:
    """Names of every tool the agent invoked, per the session log.

    Only the Claude backend writes `assistant` records with `tool_use` blocks;
    for other backends this is empty and callers must not read that as "no
    tools were used". Guard with `_tool_use_is_observable` before drawing that
    conclusion.
    """
    return {
        block.get("name", "")
        for record in _of_type(records, "assistant")
        for block in record.get("message", {}).get("content", [])
        if block.get("type") == "tool_use"
    }


def _tool_use_is_observable(records: list[dict]) -> bool:
    """Whether an empty `_tool_names_used` means "no tools" or "cannot tell".

    Derived from the log rather than the active backend, because a log can
    outlive the run that wrote it. Any `assistant` record means the log is in
    the Claude backend's shape, where every tool call is recorded — so an empty
    tool set is a real absence. The Pi backend emits RPC events instead and
    tracks writes via `pi_write_tool_used`, so its logs stay unreadable here.
    """
    return bool(_of_type(records, "assistant"))


def diagnose_missing_output(log_path: str) -> Diagnosis:
    """Why an agent run left no output, read from its session log.

    Public because `agent_retry` decides retryability from the returned kind.
    """
    if not Path(log_path).exists():
        return Diagnosis(DiagnosisKind.NO_SESSION_LOG)
    records = _read_jsonl(log_path)
    results = _of_type(records, "result")
    if not results:
        if _has_quota_retry(records):
            return Diagnosis(DiagnosisKind.QUOTA_EXHAUSTED)
        return Diagnosis(DiagnosisKind.NO_RESULT_RECORD)
    diagnosis = _diagnose_result_type(results[-1])
    # An agent that ran to its own conclusion without ever calling a write tool
    # was thrashing, not working — say so instead of reporting a bare turn
    # count. An agent that called no tool at all (a one-turn refusal, say) is
    # the clearest case of this, so it counts too. A crash is excluded: the
    # error already explains the missing output, and a retry would most likely
    # reproduce it.
    crashed = diagnosis.kind in (DiagnosisKind.AGENT_ERROR, DiagnosisKind.TRANSIENT)
    if crashed or not _tool_use_is_observable(records):
        return diagnosis
    tools_used = _tool_names_used(records)
    # empty tools_used also satisfies this when observability is confirmed
    wrote = any(is_write_tool(name) for name in tools_used)
    if wrote:
        return diagnosis
    return replace(diagnosis, no_write_tool=True)


def _detail_is_transient(detail: str) -> bool:
    """Whether a backend error's text names a fault a second attempt could clear.

    Only reached from `_diagnose_result_type`, which has already established
    that the run crashed — a marker appearing in the output of a run that ended
    on its own terms is not an error report.
    """
    return any(marker in detail for marker in _TRANSIENT_ERROR_MARKERS)


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

# A tier alias names a tier, not a deployment. On Vertex and Bedrock the account
# provisions a specific model ID, and ANTHROPIC_DEFAULT_* is where that ID lives.
# The Claude CLI resolves these itself; Pi does not, so resolving here gives both
# backends the same answer and keeps the precedence chain in one place.
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
    """Swap a tier alias for the provisioned model ID. Concrete IDs pass through."""
    alias = ModelAlias.parse(model)
    if alias is None:
        return model
    return os.environ.get(alias.env_key) or model


def _select_model(explicit: str | None, env_key: str, default: str) -> str:
    """The winning model name by precedence, before any alias resolution."""
    if explicit:
        return explicit
    from_env = os.environ.get(env_key)
    if from_env:
        return from_env
    global_env = os.environ.get("CLAUDE_REVIEW_MODEL")
    if global_env:
        return global_env
    return default


def _resolve_model(explicit: str | None, env_key: str, default: str) -> str:
    """Pick the model for a phase, then map any tier alias to its provisioned ID.

    Precedence: explicit argument, the phase's own key, CLAUDE_REVIEW_MODEL, the
    built-in default. Whichever wins is resolved through ANTHROPIC_DEFAULT_* — so
    naming a tier anywhere in the chain honors the deployment configured for it.
    """
    return _resolve_alias(_select_model(explicit, env_key, default))


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


def build_add_dirs(wt_path: str, artifact_dir: str) -> list[str]:
    """Directories the agent may read outside its cwd."""
    return [artifact_dir, wt_path]


def _invoke_once(inv: AgentInvocation) -> int:
    prior_log = preserve_log(inv.session_log)
    rc = ai_backend.invoke_agent(inv)
    restore_preserved(inv.session_log, prior_log)
    return rc


def invoke_agent(
    inv: AgentInvocation, *, throttle: QuotaThrottle | None = None,
) -> int:
    if throttle:
        throttle.wait_if_needed()

    rc = ai_backend.invoke_agent(inv)
    if rc != 0 and inv.model and _is_quota_error(inv.session_log):
        if throttle:
            wait = throttle.report_exhausted(inv.model)
            time.sleep(wait)
        else:
            log.warn(f"Quota exhausted on {inv.model} — retrying once after 30s backoff")
            time.sleep(30)
        rc = _invoke_once(inv)
    return rc
