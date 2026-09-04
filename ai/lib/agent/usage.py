"""AI usage accounting.

Parses cost and token usage out of backend session logs. Backend-neutral: the
Claude Code CLI and the Pi CLI both emit `result` records, in slightly different
spellings, and this module is the single place that reconciles them.

Lives below the review layer so agent.backend can depend on it without inverting
the dependency.

Every AI call made through the workbench appends one record to a monthly JSONL
file under `~/.local/state/workbench/usage/` — cost, tokens, cache hit rate, and
the task that made the call. Python entry points record automatically through
`agent.backend`; the two shell paths that cannot use it — `run-auto-task`, which
needs slash commands, and `AI_COMMAND`, which is pluggable — go through
`ai-usage-log`.

A call that reports no usage records nothing rather than a zero row. An
unmeasured call is then visibly absent instead of looking free, which a zeroed
row cannot be told apart from.

`otto-log stats` reads the ledger back. Its `--by model` breakdown shows cost
only, because the CLI reports cost per model but tokens per session — leaving the
token columns blank beats counting one session's tokens against every model it
used.
"""

# doc-group: backend

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from core import log
from core import workbench_paths

LEDGER_DIRNAME = "usage"

# Where a token count stops being written out in full. A cache-read total runs
# to eight digits, which no column and no summary line has room for, so it is
# abbreviated at the first threshold that keeps it narrow.
_TOKENS_PER_M = 1_000_000
_TOKENS_PER_K = 1_000


def format_tokens(n: int) -> str:
    """A token count abbreviated for display: `1.2M`, `4.5k`, or the number.

    One rendering for every surface that reports usage, so a figure read off
    `otto-log stats` and the same figure in a review's summary line are the
    same figure. A caller with its own placeholder for "not counted" handles
    that before calling — this renders a number it was given.
    """
    if n >= _TOKENS_PER_M:
        return f"{n / _TOKENS_PER_M:.1f}M"
    if n >= _TOKENS_PER_K:
        return f"{n / _TOKENS_PER_K:.1f}k"
    return str(n)


def ledger_dir() -> Path:
    """Where the usage ledger lives: ``<state>/usage/YYYY-MM.jsonl``.

    One file per month, so rotation falls out of the filename, ``--since`` can
    select files without reading them, and nothing needs a pruning job.

    Resolved per call rather than at import, for the reason `workbench_paths`
    gives: the state root is routinely re-pointed after this module loads, and
    a constant would capture whichever value was live for the first importer.
    """
    return workbench_paths.state_dir() / LEDGER_DIRNAME


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


def _fold_record(rec: dict, totals: dict[str, int], cost_by_model: dict[str, float]) -> None:
    """Fold one result record's per-model tokens and cost into the running totals."""
    for model, src in _extract_token_sources(rec):
        tokens = normalize_usage(src)
        for key in totals:
            totals[key] += tokens[key]
        if model:
            cost_by_model[model] = cost_by_model.get(model, 0.0) + (src.get("costUSD", 0) or 0)


def usage_from_records(records: list[dict]) -> SessionUsage:
    """Aggregate usage across `result` records.

    Shared by the streamed session log and the single-envelope `--output-format json`
    response, which carry the same record shape.
    """
    cost = 0.0
    duration_ms = 0
    totals = dict.fromkeys(_USAGE_KEYS, 0)
    cost_by_model: dict[str, float] = {}
    for rec in records:
        if rec.get("type") != "result":
            continue
        cost += rec.get("total_cost_usd", 0) or 0
        _fold_record(rec, totals, cost_by_model)
        duration_ms += rec.get("duration_ms", 0) or 0
    return SessionUsage(
        cost=cost,
        duration_ms=duration_ms,
        cost_by_model=cost_by_model,
        **totals,
    )


def parse_session_log(path: str) -> SessionUsage:
    """Parse a session JSONL log file and return aggregated usage."""
    try:
        lines = Path(path).read_text().splitlines()
    except OSError:
        return SessionUsage()
    records = []
    for line in lines:
        if '"type":"result"' not in line and '"type": "result"' not in line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return usage_from_records(records)


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
    ledger = ledger_dir()
    try:
        ledger.mkdir(parents=True, exist_ok=True)
        path = ledger / f"{datetime.now(timezone.utc):%Y-%m}.jsonl"
        # One write of one sub-PIPE_BUF line: concurrent pipeline agents append to
        # this file, and a single append-mode write is atomic at this size.
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
    except (OSError, TypeError, ValueError):
        if not _warned:
            _warned = True
            log.warn(f"usage ledger unavailable at {ledger} — telemetry not recorded")


def _ledger_record(line: str, cutoff_ts: str) -> dict | None:
    """Parse one ledger line, or None if it is unreadable or older than the cutoff."""
    try:
        rec = json.loads(line)
    except json.JSONDecodeError:
        return None
    ts = rec.get("ts", "")
    if cutoff_ts and ts and ts < cutoff_ts:
        return None
    return rec


def _read_month(path: Path, cutoff_ts: str) -> list[dict]:
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return []
    parsed = (_ledger_record(line, cutoff_ts) for line in lines)
    return [rec for rec in parsed if rec is not None]


def read_ledger(since: datetime | None = None) -> list[dict]:
    """Read ledger records, oldest month first. Skips unreadable lines.

    `since` drops whole month files by filename before any of them are opened —
    that is what the monthly split bought — then drops older records within the
    boundary month. A record with no timestamp is kept: it predates nothing
    knowable, and dropping it would silently hide cost.
    """
    out: list[dict] = []
    try:
        files = sorted(ledger_dir().glob("*.jsonl"))
    except OSError:
        return out
    cutoff_ts = f"{since:%Y-%m-%dT%H:%M:%SZ}" if since else ""
    if since:
        files = [p for p in files if p.stem >= f"{since:%Y-%m}"]
    for path in files:
        out.extend(_read_month(path, cutoff_ts))
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
