"""Shared memory-state scan for dream and promote.

Both scans read every registered repo's memory forward from the registry
and format a last-run stamp plus topic-file rows. The reports stay
byte-identical; only the stamp name, the dict key, and whether topic
bodies are included differ.

Not: the store (`core.memory`), argument parsing, or the report layout
each scan owns.
"""

# doc-group: platform

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import config.workbench_projects
import core.memory

DATETIME_FMT = "%Y-%m-%d %H:%M"
BODY_PREVIEW_LENGTH = 500


def read_stamp(repo_path: Path, name: str) -> str | None:
    """When this repo last ran `name`, read from the gate stamp.

    The stamp is regenerable state and sits under the gates root with the
    other cooldowns, rather than among the authored topic files.
    """
    stamp = core.memory.gate_stamp_file(repo_path, name)
    try:
        ts = int(stamp.read_text().strip())
    except (ValueError, OSError):
        return None
    return datetime.fromtimestamp(ts).strftime(DATETIME_FMT)


def topic_row(tf: core.memory.TopicFile, *, with_body: bool = False) -> dict:
    row = {
        "filename": tf.filename,
        "name": tf.name,
        "description": tf.description,
        "type": tf.type,
        "modified": tf.modified,
        "stale": tf.stale,
        "age_days": tf.age_days,
    }
    if with_body:
        row["body"] = (tf.body or "")[:BODY_PREVIEW_LENGTH]
    return row


def scan_memory_state(stamp: str, stamp_key: str, *, with_body: bool = False) -> list[dict]:
    """Every registered repo's memory, read forward from the registry.

    Forward rather than by globbing the memory root and working back: the key
    is a truncated slug plus a digest, so a directory name cannot say which
    repo it belongs to.
    """
    states = []
    for repo_path in config.workbench_projects.registered():
        try:
            directory = core.memory.memory_dir(repo_path)
        except ValueError:
            continue
        state = core.memory.state_of(directory, with_body=with_body)
        if state is None:
            continue
        states.append({
            "project_id": state.repo_key,
            "line_count": state.line_count,
            stamp_key: read_stamp(repo_path, stamp),
            "topic_files": [topic_row(tf, with_body=with_body) for tf in state.topic_files],
        })
    return states
