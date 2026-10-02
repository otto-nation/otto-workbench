"""NDJSON progress events for live consumers of `pr batch run`.

The state file is authoritative; events only save a consumer from polling it.
"""

# doc-group: batch

from __future__ import annotations

import json
import sys

from batch.store import now_iso

SCHEMA_VERSION = 1
EVENT_KINDS = frozenset({
    "run_started", "item_queued", "admission_wait", "step_started", "step_log",
    "step_finished", "decision_created", "decision_resolved", "item_finished",
    "run_waiting", "run_finished",
})


def emit(kind: str, **fields) -> None:
    if kind not in EVENT_KINDS:
        raise ValueError(f"unknown batch event: {kind}")
    line = {"schema_version": SCHEMA_VERSION, "kind": kind, "at": now_iso(), **fields}
    sys.stdout.write(json.dumps(line) + "\n")
    sys.stdout.flush()
