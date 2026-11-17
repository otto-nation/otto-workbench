"""Reading pi's session files: the header, the tail, every entry, and lookup by id.

A session is ``<root>/<cwd-slug>/<timestamp>_<id>.jsonl``. Its first line is the
header (``type: session``, ``id``, ``cwd``, ``timestamp``); every other line is
an entry. ``sessions_root(env)`` is ``$PI_CODING_AGENT_SESSION_DIR`` when set and
non-empty, else ``~/.pi/agent/sessions``. ``session_files(root)`` lists every
session file, sorted. ``read_header(path)`` returns a ``Header`` or None.
``read_tail(path, limit=)`` scans backwards from the end, at most *limit* bytes,
to the last ``session-brief`` custom entry and returns a ``Tail``: that entry's
data, and the session name — a ``session_info`` after the brief wins (a cleared
one is None), else the name the brief recorded; with no brief in reach, the
latest ``session_info`` seen. ``iter_entries(path)`` yields every entry, skipping
the header and lines that do not parse. ``find_session(root, key)`` takes an
absolute path or a session id.
"""

# ceiling: pi's `sessionDir` setting is not read, only the environment variable
# and the default; upgrade when an agent host sets `sessionDir` in settings.json.

# doc-group: platform

from __future__ import annotations

import json
import os
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

BRIEF_TYPE = "session-brief"
TAIL_BLOCK = 64 * 1024
TAIL_LIMIT = 1024 * 1024


@dataclass(frozen=True)
class Header:
    """The header line's identity fields."""

    id: str
    cwd: str
    timestamp: str


@dataclass(frozen=True)
class Tail:
    """What the end of a session file says: its current name and its latest brief."""

    name: str | None
    brief: dict | None


def sessions_root(env: Mapping[str, str]) -> Path:
    override = env.get("PI_CODING_AGENT_SESSION_DIR", "")
    return Path(override) if override else Path.home() / ".pi" / "agent" / "sessions"


def session_files(root: Path) -> list[Path]:
    return sorted(root.glob("*/*.jsonl"))


def _parse(line: bytes | str) -> dict | None:
    try:
        value = json.loads(line)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def read_header(path: Path) -> Header | None:
    try:
        with path.open("rb") as fh:
            first = fh.readline()
    except OSError:
        return None
    value = _parse(first)
    if not value or value.get("type") != "session":
        return None
    sid, cwd = value.get("id"), value.get("cwd")
    if not isinstance(sid, str) or not isinstance(cwd, str):
        return None
    return Header(id=sid, cwd=cwd, timestamp=str(value.get("timestamp") or ""))


def _lines_from_end(path: Path, limit: int) -> Iterator[bytes]:
    """Complete lines of *path*, last first, reading at most *limit* bytes."""
    with path.open("rb") as fh:
        pos = fh.seek(0, os.SEEK_END)
        carry = b""
        read = 0
        while pos > 0 and read < limit:
            size = min(TAIL_BLOCK, pos, limit - read)
            pos -= size
            read += size
            fh.seek(pos)
            lines = (fh.read(size) + carry).split(b"\n")
            carry = lines[0]
            yield from (line for line in reversed(lines[1:]) if line.strip())
        # Only a carry that reached the start of the file is a whole line.
        if pos == 0 and carry.strip():
            yield carry


def _name_of(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _scan_tail(path: Path, limit: int) -> Tail:
    seen_info = False
    name: str | None = None
    for line in _lines_from_end(path, limit):
        entry = _parse(line)
        if entry is None:
            continue
        kind = entry.get("type")
        if kind == "session_info" and not seen_info:
            seen_info = True
            name = _name_of(entry.get("name"))
            continue
        if kind == "custom" and entry.get("customType") == BRIEF_TYPE:
            data = entry.get("data") if isinstance(entry.get("data"), dict) else {}
            return Tail(name=name if seen_info else _name_of(data.get("name")), brief=data)
    return Tail(name=name, brief=None)


def read_tail(path: Path, *, limit: int = TAIL_LIMIT) -> Tail:
    try:
        return _scan_tail(path, limit)
    except OSError:
        return Tail(name=None, brief=None)


def _entries_after_header(fh) -> Iterator[dict]:
    fh.readline()
    for line in fh:
        entry = _parse(line)
        if entry is not None:
            yield entry


def iter_entries(path: Path) -> Iterator[dict]:
    try:
        with path.open("rb") as fh:
            yield from _entries_after_header(fh)
    except OSError:
        return


def find_session(root: Path, key: str) -> Path | None:
    candidate = Path(key)
    if candidate.is_absolute() and candidate.is_file():
        return candidate
    if not key or "/" in key:
        return None
    found = sorted(root.glob(f"*/*_{key}.jsonl"))
    return found[0] if found else None
