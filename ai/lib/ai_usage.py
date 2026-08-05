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
from pathlib import Path


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
