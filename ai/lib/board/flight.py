"""Single-flight for concurrent `pr board` of the same checkouts.

Herdr windows and copies poll the same board; without a lock each process
recomputes listing, status, or owed on its own. Waiters take one flock per
`(checkouts, phase, offline)` and serve the leader's memo when the request
matches, so a second poll is a wait rather than a second GitHub round-trip.
"""

# doc-group: board

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import core.flock_wait
import core.serde
import core.timeouts
import core.workbench_paths
from board.model import Phase

_WAIT_S = core.timeouts.TRANSFER


@dataclass(frozen=True)
class Request:
    """One `pr board` invocation, keyed for the flock and compared in the memo."""

    phase: Phase | str
    checkouts: tuple[str, ...]
    prs: tuple[int, ...] | None
    offline: bool
    if_changed: str | None


def _phase_value(phase: Phase | str) -> str:
    return phase.value if isinstance(phase, Phase) else str(phase)


def lock_key(req: Request) -> str:
    """First 16 hex of sha256 over phase, offline, and resolved checkouts."""
    payload = [
        _phase_value(req.phase),
        req.offline,
        sorted(str(Path(c).resolve()) for c in req.checkouts),
    ]
    return hashlib.sha256(json.dumps(payload).encode()).hexdigest()[:16]


def lock_path(req: Request) -> Path:
    return core.workbench_paths.state_dir() / "board-flight" / f"{lock_key(req)}.lock"


def memo_path(req: Request) -> Path:
    return core.workbench_paths.cache_dir("board") / "flight" / f"{lock_key(req)}.json"


def _request_fields(req: Request) -> dict:
    prs = list(req.prs) if req.prs is not None else None
    return {
        "checkouts": list(req.checkouts),
        "prs": prs,
        "if_changed": req.if_changed,
    }


def _load_memo(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if isinstance(data, dict):
        return data
    return None


def _leader_answer(memo: dict | None, req: Request, started: float) -> dict | None:
    if memo is None:
        return None
    at = memo.get("at")
    if not isinstance(at, (int, float)) or at < started:
        return None
    if memo.get("request") != _request_fields(req):
        return None
    answer = memo.get("answer")
    if isinstance(answer, dict):
        return answer
    return None


def _cached(req: Request, started: float) -> dict | None:
    return _leader_answer(_load_memo(memo_path(req)), req, started)


def _store(req: Request, doc: dict) -> None:
    core.serde.write_json(
        memo_path(req),
        {"at": time.time(), "request": _request_fields(req), "answer": doc},
    )


# ceiling: a waiter whose --if-changed or --prs differs from the leader's
# recomputes after waiting; upgrade to answering it from the leader's full
# document if serialized recomputes show in board timings.
def serve(req: Request, build: Callable[[], dict]) -> dict:
    """Return *build*'s document, or the in-flight leader's when the request matches."""
    started = time.time()
    with core.flock_wait.held(lock_path(req), wait_s=_WAIT_S) as locked:
        cached = _cached(req, started) if locked else None
        if cached is not None:
            return cached
        doc = build()
        if locked:
            _store(req, doc)
        return doc
