"""The append-only history of one run: `<run-dir>/events.jsonl`.

The only Python that opens the file. Each line is one JSON object; its caller
builds the envelope (`schema_version`, `kind`, `at`, `run`, then the event's own
fields), and `append` requires only a string `kind`. A history is a record of
what happened, never state: nothing reads it to decide what a run does next.

`append` writes the whole line with one `O_APPEND` write, so a run's driver and
a `pr batch note` appending beside it never interleave inside a line. It never
creates the run directory, so an append racing a reap fails instead of
resurrecting a deleted run. `read` returns the parsed events in file order and
counts what it skipped: a torn line, a line that is not a JSON object, or one
with no string `kind`. An unknown `kind` is returned like any other, so a
history written by a newer build still reads.
"""

# doc-group: platform

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

EVENTS_FILE = "events.jsonl"


def events_path(run_dir: Path) -> Path:
    return Path(run_dir) / EVENTS_FILE


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
