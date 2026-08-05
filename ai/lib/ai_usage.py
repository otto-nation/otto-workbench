"""AI usage accounting.

Parses cost and token usage out of backend session logs. Backend-neutral: the
Claude Code CLI and the Pi CLI both emit `result` records, in slightly different
spellings, and this module is the single place that reconciles them.

Lives below the review layer so ai_backend can depend on it without inverting
the dependency.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import log

# One file per month: rotation falls out of the filename, --since can select files
# without reading them, and nothing needs a pruning job.
LEDGER_DIR = Path.home() / ".config" / "workbench" / "usage"


@dataclass(frozen=True)
class SessionUsage:
    cost: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    duration_ms: int = 0
    cost_by_model: dict[str, float] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.cache_read_tokens + self.cache_write_tokens

    @property
    def billed_input(self) -> int:
        """Input tokens the provider bills for, at their respective rates."""
        return self.input_tokens + self.cache_read_tokens + self.cache_write_tokens

    @property
    def cache_read_ratio(self) -> float:
        """Share of billed input served from cache, 0.0-1.0."""
        return self.cache_read_tokens / self.billed_input if self.billed_input else 0.0


# The CLI emits two spellings for the same fields: modelUsage entries are camelCase,
# the top-level usage block is snake_case. Accept both at the boundary rather than
# picking one — backends differ, and a mismatch silently reports zero tokens.
_USAGE_KEYS = {
    "input_tokens": ("input_tokens", "inputTokens"),
    "output_tokens": ("output_tokens", "outputTokens"),
    "cache_read_tokens": ("cache_read_input_tokens", "cacheReadInputTokens"),
    "cache_write_tokens": ("cache_creation_input_tokens", "cacheCreationInputTokens"),
}


def normalize_usage(src: dict) -> dict[str, int]:
    """Map a usage dict in either CLI spelling onto canonical snake_case keys."""
    out = {}
    for canonical, aliases in _USAGE_KEYS.items():
        value = next((src[a] for a in aliases if src.get(a) is not None), 0)
        out[canonical] = value or 0
    return out


def _extract_token_sources(rec: dict) -> list[tuple[str, dict]]:
    """Return (model, usage) pairs, preferring modelUsage over usage.

    The top-level usage block reflects only the last model that ran, so modelUsage
    is the only accurate source when a session spans several models.
    """
    model_usage = rec.get("modelUsage")
    if model_usage:
        return list(model_usage.items())
    return [("", rec.get("usage", {}))]


def parse_session_log(path: str) -> SessionUsage:
    """Parse a session JSONL log file and return aggregated usage."""
    cost = 0.0
    input_tokens = 0
    output_tokens = 0
    cache_read = 0
    cache_write = 0
    duration_ms = 0
    cost_by_model: dict[str, float] = {}
    try:
        lines = Path(path).read_text().splitlines()
    except OSError:
        return SessionUsage()
    for line in lines:
        if '"type":"result"' not in line and '"type": "result"' not in line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("type") != "result":
            continue
        cost += rec.get("total_cost_usd", 0) or 0
        for model, src in _extract_token_sources(rec):
            tokens = normalize_usage(src)
            input_tokens += tokens["input_tokens"]
            output_tokens += tokens["output_tokens"]
            cache_read += tokens["cache_read_tokens"]
            cache_write += tokens["cache_write_tokens"]
            if model:
                cost_by_model[model] = cost_by_model.get(model, 0.0) + (src.get("costUSD", 0) or 0)
        duration_ms += rec.get("duration_ms", 0) or 0
    return SessionUsage(
        cost=cost,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        duration_ms=duration_ms,
        cost_by_model=cost_by_model,
    )


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# Telemetry must never break the call it is measuring, but a silent failure is what
# let zeroed token counts survive unnoticed. Warn once per process: loud enough to
# notice, quiet enough not to spam a pipeline running dozens of agents.
_warned = False


def record(
    *,
    script: str,
    entry_point: str,
    backend: str,
    model: str | None,
    usage: SessionUsage,
    exit_code: int,
    task: str | None = None,
    repo: str | None = None,
    pr: str | None = None,
) -> None:
    """Append one usage record to the global ledger. Never raises."""
    global _warned
    rec = {
        "ts": _iso_now(),
        "script": script,
        "entry_point": entry_point,
        "backend": backend,
        "model": model,
        "cost": usage.cost,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_read_tokens": usage.cache_read_tokens,
        "cache_write_tokens": usage.cache_write_tokens,
        "duration_ms": usage.duration_ms,
        "exit_code": exit_code,
    }
    if usage.cost_by_model:
        rec["cost_by_model"] = usage.cost_by_model
    for key, value in (("task", task), ("repo", repo), ("pr", pr)):
        if value:
            rec[key] = value
    try:
        LEDGER_DIR.mkdir(parents=True, exist_ok=True)
        path = LEDGER_DIR / f"{datetime.now(timezone.utc):%Y-%m}.jsonl"
        # One write of one sub-PIPE_BUF line: concurrent pipeline agents append to
        # this file, and a single append-mode write is atomic at this size.
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
    except (OSError, TypeError, ValueError):
        if not _warned:
            _warned = True
            log.warn(f"usage ledger unavailable at {LEDGER_DIR} — telemetry not recorded")


def read_ledger() -> list[dict]:
    """Read every ledger record, oldest month first. Skips unreadable lines."""
    out: list[dict] = []
    try:
        files = sorted(LEDGER_DIR.glob("*.jsonl"))
    except OSError:
        return out
    for path in files:
        try:
            lines = path.read_text().splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def merge(usages: list[SessionUsage]) -> SessionUsage:
    """Sum a list of usages, preserving per-model cost attribution."""
    if not usages:
        return SessionUsage()
    cost_by_model: dict[str, float] = {}
    for u in usages:
        for model, c in u.cost_by_model.items():
            cost_by_model[model] = cost_by_model.get(model, 0.0) + c
    return SessionUsage(
        cost=sum(u.cost for u in usages),
        input_tokens=sum(u.input_tokens for u in usages),
        output_tokens=sum(u.output_tokens for u in usages),
        cache_read_tokens=sum(u.cache_read_tokens for u in usages),
        cache_write_tokens=sum(u.cache_write_tokens for u in usages),
        duration_ms=sum(u.duration_ms for u in usages),
        cost_by_model=cost_by_model,
    )
