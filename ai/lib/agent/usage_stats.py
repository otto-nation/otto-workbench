"""The usage ledger rolled up into one row per group, rendered as a table or
as JSON, for `otto-log stats`.

`otto-log stats` reads the ledger back. Its `--by model` breakdown shows cost
only, because the CLI reports cost per model but tokens per session — leaving the
token columns blank beats counting one session's tokens against every model it
used.

`--by phase` is the one breakdown that reports turns, and the only one it could
be: a turn budget is set per phase, so a distribution rolled up by script or by
day mixes a 15-turn review agent with an 80-turn fix pass and describes neither.
Its `AT CAP` column is the share of a phase's runs that spent their whole
budget, which is the reading that says whether the budget is calibrated — a
phase hitting its cap on a third of runs is one whose constant is too low.

That column is blank, not zero, for a phase whose records never said what they
were allowed. Spent turns and the allocated budget shared one key until
`record` split them, so every record written before that carries a number with
no way to tell which it is; counting those as under-cap would report every
phase as comfortably sized on the strength of records that cannot say. The
column fills in as new runs land.

Not: reading or writing the ledger (`agent.usage`), parsing the window
(`core.trail_query`), argument parsing (`cli.otto_log`).
"""

# doc-group: backend

from __future__ import annotations

import json
import math
import statistics
from collections.abc import Callable
from dataclasses import asdict, dataclass
from enum import Enum, auto

import agent.usage
import core.trail_query
import core.trail_view

# Durations are recorded in milliseconds. Every line that prints one abbreviates
# to the largest unit that still reads as a number rather than a digit count.
_MS_PER_MINUTE = 60 * core.trail_view.MS_PER_SECOND

# ── Usage stats ───────────────────────────────────────────────────────────

STATS_GROUPINGS = ("script", "task", "model", "day", "phase")

# The grouping that earns the turn columns. Turn budgets are set per phase, so
# a turn distribution is only readable against one: rolled up by script or by
# day it mixes a 15-turn review agent with an 80-turn fix pass and the median
# describes neither.
#
# Must be one of STATS_GROUPINGS above: `stats_columns` compares `by` against
# this literal, so a value missing from that tuple is unreachable from the
# CLI's `--by` choices and the turn columns would silently never render.
# Pinned by `test_the_turn_grouping_is_one_the_cli_accepts` rather than by an
# `assert` here, which `python -O` strips — leaving the invariant unenforced
# in exactly the runs that matter.
_TURN_GROUPING = "phase"

# A session can span several models. The CLI reports cost per model but tokens only
# per session, so grouping by model splits the cost and leaves the token columns
# unattributed rather than counting one session's tokens against every model it used.
_UNATTRIBUTED_TOKENS = "model"

_NO_GROUP = "—"
_BILLED_KEYS = ("input_tokens", "cache_read_tokens", "cache_write_tokens")

# The day a ledger record is grouped under. `ai_usage` stamps its `ts` in the
# same format the trail uses, so the date is the stamp's leading field.
_LEDGER_TS_DATE = slice(0, 10)

@dataclass(frozen=True)
class CostShare:
    """The cost one ledger record contributes to one group."""

    group: str
    cost: float


@dataclass(frozen=True)
class UsageRow:
    """One group's rolled-up usage — a row of the stats table and of `--json`.

    The field order is the JSON key order, so it is a wire format: append to the
    end, do not reorder. A None token count means the grouping cannot attribute
    tokens (see `_UNATTRIBUTED_TOKENS`), which is not the same as a zero.
    """

    group: str
    calls: int
    cost: float
    billed_input: int | None
    output_tokens: int | None
    cache_read_tokens: int | None
    cache_read_ratio: float | None
    median_duration_ms: int | None
    # Turn distribution against the budget, for `--by phase`. None in every
    # other grouping, and None here too until records carrying both counts
    # accumulate: a ledger written before the two were separate keys cannot
    # say what any run was allowed, and reporting that as a zero would show a
    # well-sized phase as never reaching its cap.
    p50_turns: int | None = None
    p95_turns: int | None = None
    at_cap_ratio: float | None = None


def _cost_shares(rec: dict, by: str) -> list[CostShare]:
    """The shares one ledger record contributes, one per group it lands in."""
    cost = rec.get("cost") or 0.0
    if by != _UNATTRIBUTED_TOKENS:
        group = rec.get("ts", "")[_LEDGER_TS_DATE] if by == "day" else rec.get(by)
        return [CostShare(group or _NO_GROUP, cost)]
    by_model = rec.get("cost_by_model") or {}
    if by_model:
        return [CostShare(model, model_cost) for model, model_cost in by_model.items()]
    return [CostShare(rec.get("model") or _NO_GROUP, cost)]


def _new_group() -> dict:
    return {
        "calls": 0, "cost": 0.0, "billed_input": 0,
        "output_tokens": 0, "cache_read_tokens": 0, "durations": [],
        "turns": [], "at_cap": [],
    }


def _accumulate(group: dict, rec: dict, cost: float, attribute_tokens: bool) -> None:
    group["calls"] += 1
    group["cost"] += cost or 0.0
    duration = rec.get("duration_ms") or 0
    if duration:
        group["durations"].append(duration)
    _accumulate_turns(group, rec)
    if not attribute_tokens:
        return
    group["billed_input"] += sum(rec.get(k) or 0 for k in _BILLED_KEYS)
    group["output_tokens"] += rec.get("output_tokens") or 0
    group["cache_read_tokens"] += rec.get("cache_read_tokens") or 0


def _accumulate_turns(group: dict, rec: dict) -> None:
    """Collect the turn readings one record carries, and only those it carries.

    A record with no `num_turns` contributes to neither list rather than a
    zero, and one with no `max_turns` contributes to the distribution but not
    to the cap ratio: it says what the run spent and nothing about what it was
    allowed, so counting it as under its cap would be an answer the record
    cannot support.
    """
    spent = rec.get("num_turns")
    if spent is None:
        return
    group["turns"].append(spent)
    allowed = rec.get("max_turns")
    if allowed:
        group["at_cap"].append(spent >= allowed)


def _percentile(values: list[int], fraction: float) -> int | None:
    """The value at *fraction* through a sorted list, by nearest rank.

    Nearest rank rather than `statistics.quantiles`, which interpolates and
    raises below two data points. A turn count is a whole number of turns that
    some run actually took, and a p95 of 30.5 is not one of them.
    """
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(fraction * len(ordered)) - 1)
    return ordered[index]


def _finalize_group(name: str, group: dict, attribute_tokens: bool) -> UsageRow:
    durations = group["durations"]
    billed = group["billed_input"] if attribute_tokens else None
    cache_read = group["cache_read_tokens"] if attribute_tokens else None
    at_cap = group["at_cap"]
    return UsageRow(
        group=name,
        calls=group["calls"],
        cost=group["cost"],
        billed_input=billed,
        output_tokens=group["output_tokens"] if attribute_tokens else None,
        cache_read_tokens=cache_read,
        cache_read_ratio=(cache_read / billed) if billed else None,
        median_duration_ms=int(statistics.median(durations)) if durations else None,
        p50_turns=_percentile(group["turns"], 0.50),
        p95_turns=_percentile(group["turns"], 0.95),
        at_cap_ratio=(sum(at_cap) / len(at_cap)) if at_cap else None,
    )


def aggregate_usage(records: list[dict], by: str) -> list[UsageRow]:
    """Roll ledger records into one row per group, most expensive first."""
    attribute_tokens = by != _UNATTRIBUTED_TOKENS
    groups: dict[str, dict] = {}
    for rec in records:
        for share in _cost_shares(rec, by):
            _accumulate(
                groups.setdefault(share.group, _new_group()),
                rec, share.cost, attribute_tokens,
            )
    rows = [_finalize_group(name, g, attribute_tokens) for name, g in groups.items()]
    if by == "day":
        return sorted(rows, key=lambda r: r.group)
    return sorted(rows, key=lambda r: r.cost, reverse=True)


def _fmt_tokens(n: int | None) -> str:
    if n is None:
        return _NO_GROUP
    return agent.usage.format_tokens(n)


def _fmt_duration(ms: int | None) -> str:
    if ms is None:
        return _NO_GROUP
    if ms >= _MS_PER_MINUTE:
        return f"{ms / _MS_PER_MINUTE:.1f}m"
    return f"{ms / core.trail_view.MS_PER_SECOND:.1f}s"


def _fmt_ratio(ratio: float | None) -> str:
    if ratio is None:
        return _NO_GROUP
    return f"{ratio * 100:.0f}%"


class Align(Enum):
    """Which side of its column a cell is padded on."""

    LEFT = auto()
    RIGHT = auto()


@dataclass(frozen=True)
class Column:
    """One column of the stats table: its heading, its cell, and its padding."""

    label: str
    render: Callable[[UsageRow], str]
    align: Align = Align.RIGHT


# The table's schema. Heading and cell come from the same entry, so a column
# cannot be added to one and forgotten in the other, and the order here is the
# order printed. Numbers read right-aligned; the group name is the only prose.
_STATS_COLUMNS = (
    Column("GROUP", lambda r: r.group, Align.LEFT),
    Column("CALLS", lambda r: str(r.calls)),
    Column("COST", lambda r: f"${r.cost:.4f}"),
    Column("BILLED IN", lambda r: _fmt_tokens(r.billed_input)),
    Column("OUTPUT", lambda r: _fmt_tokens(r.output_tokens)),
    Column("CACHE", lambda r: _fmt_ratio(r.cache_read_ratio)),
    Column("MED TIME", lambda r: _fmt_duration(r.median_duration_ms)),
)

# Appended only under `--by phase`. Carried separately rather than always
# printed because every other grouping would show three columns of "—": the
# reading is honest there (a turn distribution across phases describes
# nothing) but it is three columns of honest noise to serve one grouping.
_TURN_COLUMNS = (
    Column("P50 TURNS", lambda r: _fmt_count(r.p50_turns)),
    Column("P95 TURNS", lambda r: _fmt_count(r.p95_turns)),
    Column("AT CAP", lambda r: _fmt_ratio(r.at_cap_ratio)),
)


def stats_columns(by: str) -> tuple[Column, ...]:
    """The table's schema for one grouping."""
    if by == _TURN_GROUPING:
        return _STATS_COLUMNS + _TURN_COLUMNS
    return _STATS_COLUMNS


def _fmt_count(n: int | None) -> str:
    return _NO_GROUP if n is None else str(n)


def _totals_row(rows: list[UsageRow]) -> UsageRow:
    billed = sum(r.billed_input or 0 for r in rows)
    cache_read = sum(r.cache_read_tokens or 0 for r in rows)
    return UsageRow(
        group="TOTAL",
        calls=sum(r.calls for r in rows),
        cost=sum(r.cost for r in rows),
        billed_input=billed or None,
        output_tokens=sum(r.output_tokens or 0 for r in rows) or None,
        cache_read_tokens=cache_read or None,
        cache_read_ratio=(cache_read / billed) if billed else None,
        # A median of per-group medians is not a median of anything. The same
        # holds for the turn percentiles, and for a cap ratio whose groups
        # were each measured against a different cap.
        median_duration_ms=None,
    )


def _pad(cell: str, width: int, align: Align) -> str:
    return cell.ljust(width) if align is Align.LEFT else cell.rjust(width)


def format_stats_table(rows: list[UsageRow], by: str = "script") -> str:
    columns = stats_columns(by)
    header = tuple(col.label for col in columns)
    body = [tuple(col.render(row) for col in columns)
            for row in (*rows, _totals_row(rows))]
    cells = [header, *body]
    widths = [max(len(c) for c in column) for column in zip(*cells)]
    lines = [
        "  ".join(_pad(cell, width, col.align)
                  for cell, width, col in zip(cell_row, widths, columns))
        for cell_row in cells
    ]
    lines[0] = f"{core.trail_view.ANSI_BOLD}{lines[0]}{core.trail_view.ANSI_RESET}"
    return "\n".join(lines)


def stats(since: str, by: str, *, as_json: bool = False) -> None:
    records = agent.usage.read_ledger(since=core.trail_query.parse_since(since))
    rows = aggregate_usage(records, by)
    if not rows:
        print(f"No AI usage recorded in the last {since}.")
        return
    if as_json:
        for row in rows:
            print(json.dumps(asdict(row), separators=(",", ":")))
        return
    print(f"AI usage — last {since}, by {by}")
    print(format_stats_table(rows, by))
