"""Session-log usage cached per review directory, keyed by each log's size and mtime.

A log grows only while its run is live and never after it is archived, so its
usage holds until its stat moves; this is what keeps `pr review --list` from
re-parsing every log.
"""

# doc-group: pipeline

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path

import agent.usage
import core.serde
from agent.usage import SessionUsage, parse_session_log

FILENAME_USAGE_CACHE = "usage-cache.json"
_CACHE_VERSION = 1


class UsageCache:
    """Per-review-directory cache of `parse_session_log` results, keyed by stat."""

    def __init__(self, review_dir: Path) -> None:
        self._dir = review_dir
        self._path = review_dir / FILENAME_USAGE_CACHE
        self._logs: dict = {}
        self._dirty = False
        # The cache is only an optimization; a bad one is rebuilt.
        try:
            payload = json.loads(self._path.read_text())
        except (OSError, json.JSONDecodeError):
            return
        if (
            not isinstance(payload, dict)
            or payload.get("version") != _CACHE_VERSION
            or not isinstance(payload.get("logs"), dict)
        ):
            return
        self._logs = {k: v for k, v in payload["logs"].items() if _well_formed(v)}

    def usage(self, logs: list[Path]) -> SessionUsage:
        """The summed usage of those *logs* that exist, as `agent.usage.merge` sums them."""
        return agent.usage.merge([self._one(p) for p in logs if p.is_file()])

    def _one(self, log: Path) -> SessionUsage:
        key = log.relative_to(self._dir).as_posix()
        try:
            st = log.stat()
        except OSError:
            return SessionUsage()
        stat = [st.st_size, st.st_mtime_ns]
        entry = self._logs.get(key)
        if entry is not None and entry["stat"] == stat:
            try:
                return SessionUsage(**entry["usage"])
            except TypeError:
                pass
        usage = parse_session_log(str(log))
        self._logs[key] = {"stat": stat, "usage": dataclasses.asdict(usage)}
        self._dirty = True
        return usage

    def save(self) -> None:
        """Write back what this cache parsed, dropping the entries of logs since removed."""
        kept = {}
        for key, entry in self._logs.items():
            if (self._dir / key).is_file():
                kept[key] = entry
            else:
                self._dirty = True
        self._logs = kept
        if not self._dirty:
            return
        tmp = self._dir / f".usage-cache.json.{os.getpid()}.tmp"
        try:
            tmp.write_text(json.dumps({"version": _CACHE_VERSION, "logs": self._logs}))
            mtimes = [entry["stat"][1] for entry in self._logs.values()]
            # pr gc judges a directory stale by every file's age, so the cache
            # must not look newer than the logs it describes.
            if mtimes:
                newest = max(mtimes)
                os.utime(tmp, ns=(newest, newest))
            core.serde.replace_file(tmp, self._path)
        except OSError:
            # A reviews root the lister cannot write still lists; it only re-parses.
            tmp.unlink(missing_ok=True)
            return
        self._dirty = False


def _well_formed(entry) -> bool:
    """Whether a loaded entry has the `stat` pair and `usage` mapping `_one` reads."""
    if not isinstance(entry, dict) or not isinstance(entry.get("usage"), dict):
        return False
    stat = entry.get("stat")
    return isinstance(stat, list) and len(stat) == 2 and all(isinstance(n, int) for n in stat)
