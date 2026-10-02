"""Where a `pr batch` run lives on disk; the only module that touches it.

    <state_dir>/batch/<run-id>/state.json       authoritative run state
    <state_dir>/batch/<run-id>/run.lock         held by the live scheduler
    <state_dir>/batch/<run-id>/requests/*.json  decisions waiting to be applied
    <state_dir>/batch/<run-id>/cancel           {"kill": bool} once cancel is asked
    <state_dir>/batch/<run-id>/logs/*.log       stderr of each step attempt
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import core.run_lock
import core.serde
import core.workbench_paths
from batch.model import Run

STATE_FILE = "state.json"
CANCEL_FILE = "cancel"


class RunNotFound(LookupError):
    pass


@dataclass(frozen=True)
class CancelRequest:
    requested: bool
    kill: bool


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def batch_root() -> Path:
    return core.workbench_paths.state_dir() / "batch"


def new_run_id(now: datetime) -> str:
    return f"{now.astimezone(timezone.utc):%Y%m%dT%H%M%S}-{secrets.token_hex(2)}"


def run_dir(run_id: str) -> Path:
    return batch_root() / run_id


def logs_dir(run_id: str) -> Path:
    path = run_dir(run_id) / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def save(run: Run) -> None:
    path = run_dir(run.id) / STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    core.serde.write_json(path, core.serde.to_dict(run))


def load(run_id: str) -> Run:
    path = run_dir(run_id) / STATE_FILE
    if not path.is_file():
        raise RunNotFound(run_id)
    return core.serde.from_dict(Run, json.loads(path.read_text()))


def _run_ids() -> list[str]:
    root = batch_root()
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if (p / STATE_FILE).is_file())


def latest_run_id() -> str | None:
    ids = _run_ids()
    return ids[-1] if ids else None


def active_run_id() -> str | None:
    for run_id in reversed(_run_ids()):
        if core.run_lock.is_held(run_dir(run_id)):
            return run_id
    return None


def write_request(run_id: str, request: dict) -> Path:
    reqs = run_dir(run_id) / "requests"
    reqs.mkdir(parents=True, exist_ok=True)
    stamp = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S%f}"
    path = reqs / f"{stamp}-{secrets.token_hex(2)}.json"
    core.serde.write_json(path, request)
    return path


def take_requests(run_id: str) -> list[dict]:
    reqs = run_dir(run_id) / "requests"
    if not reqs.is_dir():
        return []
    taken = []
    for path in sorted(reqs.glob("*.json")):
        taken.append(json.loads(path.read_text()))
        path.unlink()
    return taken


def request_cancel(run_id: str, *, kill: bool) -> None:
    core.serde.write_json(run_dir(run_id) / CANCEL_FILE, {"kill": kill})


def cancel_requested(run_id: str) -> CancelRequest:
    path = run_dir(run_id) / CANCEL_FILE
    if not path.is_file():
        return CancelRequest(False, False)
    return CancelRequest(True, bool(json.loads(path.read_text()).get("kill")))
