"""AI usage accounting.

Parses cost and token usage out of backend session logs. Backend-neutral: the
Claude Code CLI and the Pi CLI both emit `result` records, in slightly different
spellings, and this module is the single place that reconciles them.

Lives below the review layer so agent.backend can depend on it without inverting
the dependency.

Every AI call made through the workbench appends one record to a monthly JSONL
file under `~/.local/state/workbench/usage/` — cost, tokens, cache hit rate, the
task that made the call, the trail invocation of the command it ran under, and
the repo, PR and branch where the caller knows them. Python entry points record
automatically through `agent.backend`; the shell caller that cannot use it —
`run-auto-task`, which needs slash commands — goes through `ai-usage-log`.

A call that reports no usage records nothing rather than a zero row. An
unmeasured call is then visibly absent instead of looking free, which a zeroed
row cannot be told apart from.

`otto-log stats` reads the ledger back; how it groups and what it leaves blank is
`agent.usage_stats`.
"""

# doc-group: backend

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import core.log
import core.workbench_paths

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
    return core.workbench_paths.state_dir() / LEDGER_DIRNAME


@dataclass(frozen=True)
class SessionUsage:
    cost: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    duration_ms: int = 0
    cost_by_model: dict[str, float] = field(default_factory=dict)
    # Turns the session actually spent, from the result record. None when the
    # log did not say — an absent measurement, not a zero-turn run.
    num_turns: int | None = None
    # The largest billed input any one request of the session sent: the most
    # context the session ever carried. None when the log holds no per-request
    # usage — the `--output-format json` envelope carries only the result.
    peak_context_tokens: int | None = None
    # What served the session's last request, read from the log rather than
    # inferred from settings: Pi names the provider and thinking level on every
    # assistant message. None when the log does not say; Claude's names neither.
    served_provider: str | None = None
    served_thinking: str | None = None

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


@dataclass(frozen=True)
class FirstTurnUsage:
    """One served request of a session: the model that served it, and its input.

    `input_tokens` is billed input — raw input plus cache read and cache write
    — because that is what occupies the context window. `served_model` is the
    id the backend reported, which can differ from the alias the phase asked
    for. Named for its first use, the session's opening request; `served_turns`
    yields one per request. `provider` and `thinking` are what the log says
    served it, None when the log does not say.
    """

    served_model: str
    input_tokens: int
    provider: str | None = None
    thinking: str | None = None


def served_turns(records: list[dict]) -> list[FirstTurnUsage]:
    """Every request a session JSONL shows a model serving, in order.

    Claude stream-json: the first `{type:system, subtype:init}` line names the
    model, and each assistant message's `usage` is billed as
    `input_tokens + cache_read_input_tokens + cache_creation_input_tokens`. One
    API message streams as one line per content block, so a message id seen
    already is the same request; a line with a `parent_tool_use_id` is a
    subagent's request, not this session's.

    Pi `--mode json`: each `message_end` whose message role is assistant
    carries `message.model`, `message.provider`, `message.thinkingLevel` and
    `usage` as `input + cacheRead + cacheWrite`.

    A record with zero billed input is not a served request — a synthetic
    API-error message, an errored or aborted turn — and is left out.
    """
    return _served_turns_claude(records) or _served_turns_pi(records)


def first_turn_usage(records: list[dict]) -> FirstTurnUsage | None:
    """First-request input and served model from a session JSONL, or None."""
    turns = served_turns(records)
    return turns[0] if turns else None


def _served_turns_claude(records: list[dict]) -> list[FirstTurnUsage]:
    model = ""
    for rec in records:
        if rec.get("type") == "system" and rec.get("subtype") == "init":
            model = str(rec.get("model") or "")
            break
    turns = []
    seen: set[str] = set()
    for rec in records:
        if rec.get("type") != "assistant" or rec.get("parent_tool_use_id"):
            continue
        message = rec.get("message") or {}
        message_id = message.get("id")
        if message_id and message_id in seen:
            continue
        if message_id:
            seen.add(message_id)
        usage = message.get("usage") or {}
        billed = (
            int(usage.get("input_tokens") or 0)
            + int(usage.get("cache_read_input_tokens") or 0)
            + int(usage.get("cache_creation_input_tokens") or 0)
        )
        if billed:
            served = str(message.get("model") or model)
            turns.append(FirstTurnUsage(served_model=served, input_tokens=billed))
    return turns


def _served_turns_pi(records: list[dict]) -> list[FirstTurnUsage]:
    turns = []
    for rec in records:
        if rec.get("type") != "message_end":
            continue
        message = rec.get("message") or {}
        if message.get("role") != "assistant":
            continue
        usage = message.get("usage") or rec.get("usage") or {}
        billed = (
            int(usage.get("input") or 0)
            + int(usage.get("cacheRead") or 0)
            + int(usage.get("cacheWrite") or 0)
        )
        if billed:
            turns.append(FirstTurnUsage(
                served_model=str(message.get("model") or ""), input_tokens=billed,
                provider=message.get("provider") or None,
                thinking=message.get("thinkingLevel") or None,
            ))
    return turns


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
    """Aggregate usage across `result` records, and the peak per-request input.

    Shared by the streamed session log and the single-envelope `--output-format json`
    response, which carry the same record shape. Only the streamed log carries
    per-request usage, so the envelope's peak is None.

    Records with no `result` among them are a session that died before it
    reported, and come back as the empty usage — unmeasured, never a zeroed
    measurement that happens to know its peak.
    """
    cost = 0.0
    duration_ms = 0
    num_turns = 0
    saw_turns = False
    totals = dict.fromkeys(_USAGE_KEYS, 0)
    cost_by_model: dict[str, float] = {}
    saw_result = False
    for rec in records:
        if rec.get("type") != "result":
            continue
        saw_result = True
        cost += rec.get("total_cost_usd", 0) or 0
        _fold_record(rec, totals, cost_by_model)
        duration_ms += rec.get("duration_ms", 0) or 0
        if rec.get("num_turns") is not None:
            saw_turns = True
            num_turns += rec["num_turns"] or 0
    if not saw_result:
        return SessionUsage()
    turns = served_turns(records)
    last = turns[-1] if turns else None
    return SessionUsage(
        cost=cost,
        duration_ms=duration_ms,
        cost_by_model=cost_by_model,
        num_turns=num_turns if saw_turns else None,
        peak_context_tokens=max(t.input_tokens for t in turns) if turns else None,
        served_provider=last.provider if last else None,
        served_thinking=last.thinking if last else None,
        **totals,
    )


# The record types `usage_from_records` reads, in both JSON spacings.
_READ_MARKERS = (
    '"type":"result"', '"type": "result"',
    '"type":"assistant"', '"type": "assistant"',
    '"type":"message_end"', '"type": "message_end"',
)


def parse_session_log(path: str) -> SessionUsage:
    """Parse a session JSONL log file and return aggregated usage.

    Only the lines `usage_from_records` reads are decoded: the `result`
    records, Claude's assistant messages and Pi's `message_end` records. Not
    every line naming `usage` — Pi's `message_update` deltas each carry one,
    and decoding them all is the cost this filter exists to avoid.
    """
    try:
        lines = Path(path).read_text().splitlines()
    except OSError:
        return SessionUsage()
    records = []
    for line in lines:
        if not any(marker in line for marker in _READ_MARKERS):
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
    phase: str | None = None,
    max_turns: int | None = None,
    branch: str | None = None,
    invocation: str | None = None,
    provider: str | None = None,
    measured: bool = True,
    thinking: str | None = None,
) -> None:
    """Append one usage record to the global ledger. Never raises.

    `provider` and `thinking` come from *usage*'s own reading of the log, which
    names what served the call; else from the argument, the backend's answer
    for a log that cannot. For `thinking` that answer is the level the call
    asked for, which a model lacking it runs below. `peak_context_tokens` comes
    from *usage* alone, and a key nothing supplied is left out rather than
    guessed.

    `measured=False` is a call that failed before reporting any usage. It is
    recorded so the failure counts, with `measured: false` and no cost or token
    keys: a zero there would read as a free call rather than an unknown one.

    `num_turns` and `max_turns` are separate keys because they are separate
    quantities: what the run spent, and what it was allowed. They shared one
    key when both were first recorded — the budget written only when the
    session log had not reported real turns — which made the ledger's turn
    count unreadable. A row saying 40 was either a run that spent forty or a
    run allowed forty and measured at nothing, and no field said which, so the
    one question the pair exists to answer — is this phase's budget calibrated
    — could not be asked of it at all.

    Either may be absent. A backend that reports no turn count leaves
    `num_turns` off rather than writing the budget in its place, and a caller
    with no budget to declare leaves `max_turns` off. A reader must treat a
    missing key as unknown rather than as zero; `otto-log stats --by phase`
    does.
    """
    global _warned
    rec = {
        "ts": _iso_now(),
        "script": script,
        "entry_point": entry_point,
        "backend": backend,
        "model": model,
        "exit_code": exit_code,
    }
    if measured:
        rec.update({
            "cost": usage.cost,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cache_read_tokens": usage.cache_read_tokens,
            "cache_write_tokens": usage.cache_write_tokens,
            "duration_ms": usage.duration_ms,
        })
    else:
        rec["measured"] = False
    if usage.cost_by_model:
        rec["cost_by_model"] = usage.cost_by_model
    for key, value in (
        ("task", task), ("repo", repo), ("pr", pr), ("branch", branch),
        ("invocation", invocation),
        ("phase", phase), ("num_turns", usage.num_turns), ("max_turns", max_turns),
        ("provider", usage.served_provider or provider),
        ("thinking", usage.served_thinking or thinking),
        ("peak_context_tokens", usage.peak_context_tokens),
    ):
        if value is not None and value != "":
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
            core.log.warn(f"usage ledger unavailable at {ledger} — telemetry not recorded")


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
    turns = [u.num_turns for u in usages if u.num_turns is not None]
    peaks = [u.peak_context_tokens for u in usages if u.peak_context_tokens is not None]
    served = [u for u in usages if u.served_provider or u.served_thinking]
    return SessionUsage(
        cost=sum(u.cost for u in usages),
        input_tokens=sum(u.input_tokens for u in usages),
        output_tokens=sum(u.output_tokens for u in usages),
        cache_read_tokens=sum(u.cache_read_tokens for u in usages),
        cache_write_tokens=sum(u.cache_write_tokens for u in usages),
        duration_ms=sum(u.duration_ms for u in usages),
        cost_by_model=cost_by_model,
        num_turns=sum(turns) if turns else None,
        peak_context_tokens=max(peaks) if peaks else None,
        served_provider=served[-1].served_provider if served else None,
        served_thinking=served[-1].served_thinking if served else None,
    )
