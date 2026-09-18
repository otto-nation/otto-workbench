"""Reading back what `core.trail` wrote — discovery, parsing, filtering.

`trail.py` owns the write path and the file naming; this owns the read path
over the same root. They are split because the readers are no longer only
`otto-log`: a scan that reports on the machine's own tooling — what ran, what
failed, how often — answers that from the trail, and shelling out to the CLI to
parse its rendered lines back into records is a worse version of importing the
three functions that produced them.

A window is applied twice on purpose. Once at the filename, which is what lets
a year of history stay unopened, and once per record in `filter_events`, which
is what makes the boundary exact.
"""

# doc-group: platform

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core import log
from core import workbench_paths
from core.trail import MONTH_STEM, TS_FORMAT


# What a window with no recognizable unit suffix is read as. A query is not
# worth failing over a typo, and the narrowest useful window shows less rather
# than the wrong thing — the same hour `otto-log recent` defaults to.
FALLBACK_WINDOW = timedelta(hours=1)


# ── Discovery ─────────────────────────────────────────────────────────────

def discover_trails(since: datetime | None = None) -> list[Path]:
    """Every trail file a *since* window can reach, oldest month first."""
    try:
        files = sorted(workbench_paths.trail_dir().glob("*.jsonl"))
    except OSError:
        # A missing trail root is not an error — glob on a directory that
        # does not exist yet raises nothing, so the only thing this catches
        # is a real I/O failure (e.g. permissions), and an empty result is
        # the right answer either way: there is nothing to query.
        return []
    if since is None:
        return files
    cutoff = f"{since:%Y-%m}"
    # A stem that does not name a month cannot be placed in time by its name,
    # so no window excludes it — see `trail.MONTH_STEM`.
    return [p for p in files if not MONTH_STEM.match(p.stem) or p.stem >= cutoff]


# ── Loading ───────────────────────────────────────────────────────────────

def _parse_trail_file(path: str | Path) -> list[dict]:
    try:
        text = Path(path).read_text()
    except OSError:
        return []
    events = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def load_events(trail_paths: list[str | Path]) -> list[dict]:
    events = []
    for path in trail_paths:
        events.extend(_parse_trail_file(path))
    events.sort(key=lambda e: e.get("ts", ""))
    return events


def load_trail_events(since: str | None) -> list[dict]:
    """Every event in the files *since* can reach, oldest first."""
    return load_events(discover_trails(parse_since(since) if since else None))


# ── Filtering ─────────────────────────────────────────────────────────────

def root_of(event: dict) -> str:
    """The command *event* belongs to — its `root`, or its own invocation.

    A root's own events carry no `root` field, and neither does anything written
    before the field existed, so both read as a command of one process. Every
    grouping goes through this rather than the raw key, which is what lets one
    query span records from either side of the cutover.
    """
    return event.get("root") or event.get("invocation", "?")


def parse_since(since: str, *, strict: bool = False) -> datetime:
    """The cutoff *since* names, as an absolute time.

    A malformed value falls back to `FALLBACK_WINDOW` for an interactive query,
    where showing the last hour beats failing over a typo. `strict` turns the
    same value into an exit instead, for a caller whose window is a scan
    parameter rather than a glance — silently reading `--days 30` as one hour
    would make a report claim the machine was quiet.
    """
    now = datetime.now(timezone.utc)
    suffixes = {"h": "hours", "d": "days", "m": "minutes"}
    suffix = since[-1:] if since else ""
    unit = suffixes.get(suffix)
    if not unit:
        if strict:
            log.error(f"Invalid time window: {since!r} (expected e.g. 2h, 7d, 30m)")
            raise SystemExit(1)
        return now - FALLBACK_WINDOW
    try:
        return now - timedelta(**{unit: int(since[:-1])})
    except ValueError:
        log.error(f"Invalid --since value: {since!r}")
        raise SystemExit(1)


def filter_events(
    events: list[dict],
    script: str | None = None,
    level: str | None = None,
    event_type: str | None = None,
    invocation: str | None = None,
    root: str | None = None,
    pr: int | str | None = None,
    repo: str | None = None,
    since: str | None = None,
) -> list[dict]:
    result = events
    if script:
        result = [e for e in result if e.get("script") == script]
    if level:
        result = [e for e in result if e.get("level") == level]
    if event_type:
        result = [e for e in result if e.get("event_type") == event_type]
    if invocation:
        result = [e for e in result if e.get("invocation") == invocation]
    if root:
        result = [e for e in result if root_of(e) == root]
    if pr is not None:
        pr_str = str(pr)
        result = [e for e in result if str(e.get("context", {}).get("pr", "")) == pr_str]
    if repo:
        result = [e for e in result if e.get("context", {}).get("repo") == repo]
    if since:
        cutoff_str = parse_since(since).strftime(TS_FORMAT)
        result = [e for e in result if e.get("ts", "") >= cutoff_str]
    return result
