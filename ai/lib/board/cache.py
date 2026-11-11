"""Per-repo board cache: listing, status and owed JSON under cache_dir("board")."""

# ceiling: last writer wins between concurrent processes. Upgrade to
# per-repo locking if boards clobber each other's cache.

# doc-group: board

from __future__ import annotations

import json
import time
from pathlib import Path

import core.serde
import core.workbench_paths
import gh.client
from board.model import Phase

VIEWER_TTL_S = 3600


STATUS_KEYS = (
    "additions",
    "deletions",
    "changed_files",
    "in_merge_queue",
    "mergeable",
    "ci_status",
    "ci_unknown",
    "review_decision",
    "latest_reviews",
    "requested_reviewers",
)


def path_for(repo_key: str, phase: Phase) -> Path:
    return core.workbench_paths.cache_dir("board") / repo_key / f"{phase.value}.json"


def write(repo_key: str, phase: Phase, payload: dict) -> None:
    core.serde.write_json(path_for(repo_key, phase), payload)


def read(repo_key: str, phase: Phase) -> dict | None:
    path = path_for(repo_key, phase)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def read_all(repo_key: str) -> dict[Phase, dict]:
    out: dict[Phase, dict] = {}
    for phase in (Phase.LISTING, Phase.STATUS, Phase.OWED):
        payload = read(repo_key, phase)
        if payload is not None:
            out[phase] = payload
    return out


def _rows(payload: dict | None) -> list[dict]:
    if not payload:
        return []
    rows = payload.get("rows") or []
    return [row for row in rows if isinstance(row, dict) and "number" in row]


def merged(repo_key: str) -> dict[int, dict]:
    """Overlay listing, matching-SHA status, then owed, keyed by PR number.

    Status facts apply only when `head_sha` matches the listing row. Owed is
    joined by number. A PR that exists only in status still appears, hydrated,
    unless its state is OPEN — an open PR missing from the listing was closed.
    """
    listing = read(repo_key, Phase.LISTING)
    status = read(repo_key, Phase.STATUS)
    owed = read(repo_key, Phase.OWED)
    by_number: dict[int, dict] = {}
    for row in _rows(listing):
        number = row["number"]
        by_number[number] = {**row, "hydrated": False}
    status_by_number = {row["number"]: row for row in _rows(status)}
    for number, row in list(by_number.items()):
        extra = status_by_number.get(number)
        if extra is None:
            continue
        if extra.get("head_sha") != row.get("head_sha"):
            continue
        overlay = {key: extra[key] for key in STATUS_KEYS if key in extra}
        by_number[number] = {**row, **overlay, "hydrated": True}
    for number, extra in status_by_number.items():
        if number in by_number:
            continue
        if extra.get("state") == "OPEN":
            continue
        overlay = {key: extra[key] for key in STATUS_KEYS if key in extra}
        by_number[number] = {**extra, **overlay, "hydrated": True}
    for row in _rows(owed):
        number = row["number"]
        reason = row.get("reason")
        head_sha = row.get("head_sha", "")
        if number in by_number:
            by_number[number] = {
                **by_number[number],
                "owed": reason,
                "owed_known": True,
                "owed_head_sha": head_sha,
            }
        else:
            by_number[number] = {
                "number": number,
                "owed": reason,
                "owed_known": True,
                "owed_head_sha": head_sha,
                "hydrated": False,
            }
    return by_number


def viewer_path() -> Path:
    return core.workbench_paths.cache_dir("board") / "viewer.json"


def read_viewer() -> str | None:
    path = viewer_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    login = data.get("login")
    if not isinstance(login, str):
        return None
    return login


def write_viewer(login: str) -> None:
    core.serde.write_json(viewer_path(), {"login": login})


def _viewer_stale() -> bool:
    try:
        age = time.time() - viewer_path().stat().st_mtime
    except OSError:
        return True
    return age >= VIEWER_TTL_S


def viewer(*, refresh: bool = True) -> str:
    """GitHub login from cache, refreshed online when older than 1 h.

    With ``refresh=False`` (offline) a missing cache returns ``""``: that means
    cache-miss, not a real empty login.
    """
    cached = read_viewer()
    if not refresh:
        return cached or ""
    if cached is not None and not _viewer_stale():
        return cached
    login = gh.client.login()
    write_viewer(login)
    return login
