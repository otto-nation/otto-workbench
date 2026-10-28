"""NDJSON progress events for live consumers of `pr batch run`.

The state file is authoritative; events only save a consumer from polling it.
`step_log` is emitted only under `--verbose`; `run_summary` is the last line of every `run`/`resume` that settles.
`generated_dirt_restored` names tracked generated files the batch restored rather than raising `dirty_worktree`.
`item_skipped_held` is a PR a new `run` left out because an unfinished run still holds it.
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
    "cancel_requested", "item_skipped_held",
    "run_waiting", "run_finished", "run_summary", "generated_dirt_restored",
})


def emit(kind: str, **fields) -> None:
    if kind not in EVENT_KINDS:
        raise ValueError(f"unknown batch event: {kind}")
    line = {"schema_version": SCHEMA_VERSION, "kind": kind, "at": now_iso(), **fields}
    sys.stdout.write(json.dumps(line) + "\n")
    sys.stdout.flush()
