"""NDJSON progress events for live consumers of `pr batch run`, and the run's history.

The state file is authoritative; events only save a consumer from polling it.
Every event `emit` prints is also appended, as the identical JSON line, to the
run's `events.jsonl` through `core.run_events`. That history is what a run
session shows; nothing reads it to decide what a run does. An append that fails
warns once per run on stderr and never fails the run. An event naming no run
(`item_skipped_held`, printed before the run exists) is printed only.
`run_summary`'s `run` is the run view, filed under its `id`.

`step_log` is emitted only under `--verbose`; `run_summary` is the last line of every `run`/`resume` that settles.
`step_started` and `step_finished` carry the attempt's `log_path`.
`generated_dirt_restored` names tracked generated files the batch restored rather than raising `dirty_worktree`.
`item_skipped_held` is a PR a new `run` left out because an unfinished run still holds it.
`finding_created` is a schema-2 recording (`run`, `item`, `reason`, `step`, `detail`).
`commits_unrecorded` is a step whose new commits git would not list; the attempt's tip is stored instead.
`decision` is an operator verdict `pr batch note` appends to the history (`item`,
`finding` as `<reason>:<step>`, `verdict`, `reason`, `commands`); it is never
printed by a run and changes no item state. The latest per (`item`, `finding`) wins.
"""

# doc-group: batch

from __future__ import annotations

import json
import sys

import batch.store
import core.run_events
from batch.store import now_iso

SCHEMA_VERSION = 1
EVENT_KINDS = frozenset({
    "run_started", "item_queued", "admission_wait", "step_started", "step_log",
    "step_finished", "item_finished",
    "cancel_requested", "item_skipped_held",
    "run_finished", "run_summary", "generated_dirt_restored",
    "finding_created", "commits_unrecorded", "decision",
})

# Runs whose history this process has already failed to append to, so the
# warning is printed once per run rather than once per event.
_warned: set[str] = set()


def record(kind: str, **fields) -> dict:
    """The envelope every batch event is printed and filed as."""
    if kind not in EVENT_KINDS:
        raise ValueError(f"unknown batch event: {kind}")
    return {"schema_version": SCHEMA_VERSION, "kind": kind, "at": now_iso(), **fields}


def emit(kind: str, **fields) -> None:
    line = record(kind, **fields)
    sys.stdout.write(json.dumps(line) + "\n")
    sys.stdout.flush()
    _append(line)


def _run_id(line: dict) -> str:
    run = line.get("run")
    if isinstance(run, dict):
        run = run.get("id")
    return run if isinstance(run, str) else ""


def _append(line: dict) -> None:
    run_id = _run_id(line)
    if not run_id:
        return
    try:
        core.run_events.append(batch.store.run_dir(run_id), line)
    except OSError as exc:
        # The history only saves a session from rebuilding what happened;
        # state.json stays authoritative, so a full disk or a reaped run
        # directory costs the history, never the run.
        if run_id in _warned:
            return
        _warned.add(run_id)
        print(f"pr batch: could not record events for run {run_id} "
              f"({exc}); the run continues", file=sys.stderr)


def latest_decisions(events: list[dict]) -> dict[tuple[str, str], dict]:
    """The last `decision` per (item, finding) in *events*, read in file order."""
    latest: dict[tuple[str, str], dict] = {}
    for event in events:
        item, finding = event.get("item"), event.get("finding")
        if event.get("kind") == "decision" and isinstance(item, str) and isinstance(finding, str):
            latest[(item, finding)] = event
    return latest
