"""Where a `pr batch` run lives on disk; the only module that touches it.

    <state_dir>/batch/<run-id>/state.json       authoritative run state
    <state_dir>/batch/<run-id>/run.lock         held by the live scheduler
    <state_dir>/locks/batch.lock                held by whichever `pr batch run`/`resume` is live
    <state_dir>/batch/<run-id>/requests/*.json  decisions waiting to be applied
    <state_dir>/batch/<run-id>/cancel           {"kill": bool} once cancel is asked
    <state_dir>/batch/<run-id>/logs/<slug>-<pr>-<step>-<n>.log     stderr of each step attempt
    <state_dir>/batch/<run-id>/logs/<slug>-<pr>-publish-<n>.log    output of each publish attempt
"""

# doc-group: batch

from __future__ import annotations

import json
import secrets
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import core.run_lock
import core.serde
import core.workbench_paths
from batch.model import Run

STATE_FILE = "state.json"
CANCEL_FILE = "cancel"

# The machine-wide lock every `pr batch run` and `resume` holds from before it
# asks whether a run is active until it finishes, so two batches cannot both
# pass that question while planning. Outside run_dir: it guards the choice of
# run, not one run's files.
BATCH_LOCK = core.run_lock.Global("batch")


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


def attempt_log_path(run_id: str, repo: str, pr: int, step: str) -> Path:
    """The log for the next attempt at *step* on PR *pr* of *repo*: `<slug>-<pr>-<step>-<n>.log`.

    *step* is a Step value or "publish". `n` counts the attempts already logged,
    so a caller must create the file before the next call sees it.
    """
    slug = repo.replace("/", "__")
    logs = logs_dir(run_id)
    attempt = sum(1 for _ in logs.glob(f"{slug}-{pr}-{step}-*"))
    return logs / f"{slug}-{pr}-{step}-{attempt}.log"


def save(run: Run) -> None:
    path = run_dir(run.id) / STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    core.serde.write_json(path, core.serde.to_dict(run))


def load(run_id: str) -> Run:
    path = run_dir(run_id) / STATE_FILE
    if not path.is_file():
        raise RunNotFound(run_id)
    return core.serde.from_dict(Run, json.loads(path.read_text()))


def run_ids() -> list[str]:
    """Every saved run, oldest first; a directory with no state file is not a run."""
    root = batch_root()
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if (p / STATE_FILE).is_file())


def latest_run_id() -> str | None:
    ids = run_ids()
    return ids[-1] if ids else None


def active_run_id() -> str | None:
    for run_id in reversed(run_ids()):
        if core.run_lock.is_held(run_dir(run_id)):
            return run_id
    return None


def state_mtime(run_id: str) -> float:
    """When *run_id*'s state was last saved, as a timestamp: its terminal transition once finished."""
    return (run_dir(run_id) / STATE_FILE).stat().st_mtime


def remove(run_id: str) -> None:
    """Delete *run_id*'s directory: state, logs, requests and lock record.

    Raises OSError when any of it cannot be removed; the caller reports it.
    """
    shutil.rmtree(run_dir(run_id))


def write_request(run_id: str, request: dict) -> Path:
    reqs = run_dir(run_id) / "requests"
    reqs.mkdir(parents=True, exist_ok=True)
    stamp = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S%f}"
    path = reqs / f"{stamp}-{secrets.token_hex(2)}.json"
    core.serde.write_json(path, request)
    return path


def has_requests(run_id: str) -> bool:
    reqs = run_dir(run_id) / "requests"
    return reqs.is_dir() and any(reqs.glob("*.json"))


def _reject_request(path: Path) -> None:
    dest_dir = path.parent / "rejected"
    dest_dir.mkdir(parents=True, exist_ok=True)
    path.replace(dest_dir / path.name)


def take_requests(run_id: str) -> list[dict]:
    reqs = run_dir(run_id) / "requests"
    if not reqs.is_dir():
        return []
    taken = []
    for path in sorted(reqs.glob("*.json")):
        try:
            raw = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            _reject_request(path)
            taken.append({"error": f"malformed request {path.name}: {exc}"})
            continue
        if not isinstance(raw, dict):
            _reject_request(path)
            taken.append({"error": f"malformed request {path.name}: not an object"})
            continue
        taken.append(raw)
        path.unlink()
    return taken


def request_cancel(run_id: str, *, kill: bool) -> None:
    core.serde.write_json(run_dir(run_id) / CANCEL_FILE, {"kill": kill})


def cancel_requested(run_id: str) -> CancelRequest:
    path = run_dir(run_id) / CANCEL_FILE
    if not path.is_file():
        return CancelRequest(False, False)
    return CancelRequest(True, bool(json.loads(path.read_text()).get("kill")))


def clear_cancel(run_id: str) -> None:
    (run_dir(run_id) / CANCEL_FILE).unlink(missing_ok=True)
