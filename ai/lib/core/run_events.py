"""The append-only history of one run or one PR target: `<dir>/events.jsonl`.

The only Python that opens the file. Each line is one JSON object built by
`record`: `schema_version`, `kind`, `at` (`now_iso`, the UTC second), then the
event's own fields. A history is a record of what happened, never state:
nothing reads it to decide what a run does next.

`append` writes the whole line with one `O_APPEND` write, so writers appending
side by side never interleave inside a line. It never creates the directory,
so an append racing a reap fails instead of resurrecting a deleted run.
`append_or_warn` is the policy every command writer uses: an append that fails
warns once per key on stderr and never fails the caller, whose own state stays
authoritative. `read` returns the parsed events in file order and counts what
it skipped: a torn line, a line that is not a JSON object, or one with no
string `kind`. An unknown `kind` is returned like any other, so a history
written by a newer build still reads. `latest_decisions` is the last
`decision` per (`item`, `finding`): the latest operator verdict wins.
"""

# doc-group: platform

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

EVENTS_FILE = "events.jsonl"
SCHEMA_VERSION = 1


def events_path(run_dir: Path) -> Path:
    return Path(run_dir) / EVENTS_FILE


def now_iso() -> str:
    """The current UTC second, the form every history's `at` is written in."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def record(kind: str, at: str, **fields) -> dict:
    """The envelope every history line carries: `schema_version`, `kind`, `at`, then *fields*."""
    if not isinstance(kind, str) or not kind:
        raise ValueError("a run event needs a non-empty string kind")
    return {"schema_version": SCHEMA_VERSION, "kind": kind, "at": at, **fields}


def append(run_dir: Path, record: dict) -> None:
    """Append *record* to *run_dir*'s history as one line; raise OSError when it cannot."""
    kind = record.get("kind")
    if not isinstance(kind, str) or not kind:
        raise ValueError("a run event needs a non-empty string kind")
    data = memoryview((json.dumps(record) + "\n").encode("utf-8"))
    fd = os.open(events_path(run_dir), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        # A short write is finished by the next one rather than lost; the line
        # stays whole unless another writer lands between the two.
        while data:
            data = data[os.write(fd, data):]
    finally:
        os.close(fd)


def append_or_warn(run_dir: Path, line: dict, *, warned: set[str], key: str,
                   subject: str, consequence: str) -> None:
    """Append *line*; on OSError print `subject (error); consequence` once per *key*, never raise."""
    try:
        append(run_dir, line)
    except OSError as exc:
        # A history only saves a session from rebuilding what happened; the
        # caller's own state stays authoritative, so a full disk or a reaped
        # directory costs the history, never the command.
        if key in warned:
            return
        warned.add(key)
        print(f"{subject} ({exc}); {consequence}", file=sys.stderr)


@dataclass(frozen=True)
class History:
    """A run's events in file order, and how many lines were skipped as unreadable."""

    events: list[dict] = field(default_factory=list)
    skipped: int = 0


def read(run_dir: Path) -> History:
    try:
        text = events_path(run_dir).read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return History()
    events: list[dict] = []
    skipped = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue
        if not isinstance(obj, dict) or not isinstance(obj.get("kind"), str):
            skipped += 1
            continue
        events.append(obj)
    return History(events, skipped)


def latest_decisions(events: list[dict]) -> dict[tuple[str, str], dict]:
    """The last `decision` per (item, finding) in *events*, read in file order."""
    latest: dict[tuple[str, str], dict] = {}
    for event in events:
        item, finding = event.get("item"), event.get("finding")
        if event.get("kind") == "decision" and isinstance(item, str) and isinstance(finding, str):
            latest[(item, finding)] = event
    return latest
