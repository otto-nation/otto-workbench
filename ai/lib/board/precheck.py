"""Stat-only pre-check for an offline board document.

Mirrors ``review.listing.document``: a fingerprint of every input is taken
before the reviews walk, so a change during the walk shows on the next poll
instead of being masked. The document stamp still hashes the document; this
module only decides whether that walk can be skipped.
"""

# ceiling: the offline document's inputs are listed here by hand; if board.rows
# or board presentation gains an input outside them, add a term or drop the
# pre-check. The code term covers only board/*.py; a change confined to
# review.listing (row_for) or batch.store (busy_prs) is not fingerprinted and
# is picked up once another input changes.

# doc-group: board

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import batch.store
import board.cache
import core.serde
import core.workbench_paths
import review.listing
from board.model import SCHEMA_VERSIONS, Phase

_BOARD_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Precheck:
    """A fingerprint of the offline document's inputs, and where it is memoised."""

    path: Path
    fingerprint: str

    def answers(self, if_changed: str | None) -> bool:
        """True when *if_changed* matches the memo for this fingerprint."""
        if not if_changed:
            return False
        memo = _read_memo(self.path)
        if memo is None:
            return False
        return (
            memo.get("fingerprint") == self.fingerprint
            and memo.get("stamp") == if_changed
        )

    def remember(self, stamp: str) -> None:
        memo = _read_memo(self.path)
        if (
            memo is not None
            and memo.get("fingerprint") == self.fingerprint
            and memo.get("stamp") == stamp
        ):
            return
        core.serde.write_json(self.path, {
            "fingerprint": self.fingerprint,
            "stamp": stamp,
        })


def take(
    checkouts: Sequence[str],
    resolved: Sequence[tuple[str, object]],
    viewer: str,
    phase: Phase,
) -> Precheck:
    """Fingerprint the offline inputs for *phase* over *checkouts*, resolved and sorted as ``board.flight.lock_key`` does.

    The phase is in the memo key and the fingerprint: offline phases share
    their inputs, so without it a stamp from one phase could answer another.
    """
    blob = json.dumps([phase.value, sorted(str(Path(c).resolve()) for c in checkouts)], separators=(",", ":"))
    digest = hashlib.sha256(blob.encode()).hexdigest()[:16]
    path = core.workbench_paths.cache_dir("board") / "precheck" / f"{digest}.json"
    payload = json.dumps(
        _payload(resolved, viewer, phase),
        sort_keys=True,
        separators=(",", ":"),
    )
    fingerprint = hashlib.sha256(payload.encode()).hexdigest()
    return Precheck(path=path, fingerprint=fingerprint)


def _payload(
    resolved: Sequence[tuple[str, object]], viewer: str, phase: Phase,
) -> dict:
    return {
        "schema_version": SCHEMA_VERSIONS[-1],
        "phase": phase.value,
        "viewer": viewer,
        "reviews_root": str(core.workbench_paths.reviews_dir()),
        "reviews_stamp": review.listing.stamp(),
        "batch": _batch_states(),
        "resolved": _resolved_terms(resolved),
        "code": _board_py_files(),
    }


def _read_memo(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if not isinstance(data.get("fingerprint"), str):
        return None
    if not isinstance(data.get("stamp"), str):
        return None
    return data


def _resolved_terms(resolved: Sequence[tuple[str, object]]) -> list[dict]:
    out = []
    for path, entry in resolved:
        out.append({"path": path, "found": _found_term(entry)})
    return out


def _found_term(entry: object) -> dict:
    data = core.serde.to_dict(entry)
    key = getattr(entry, "key", None)
    if not isinstance(key, str):
        return data
    data["caches"] = _cache_terms(key)
    return data


def _cache_terms(repo_key: str) -> list[dict]:
    out = []
    for phase in (Phase.LISTING, Phase.STATUS, Phase.OWED):
        out.append(_cache_term(repo_key, phase))
    return out


def _cache_term(repo_key: str, phase: Phase) -> dict:
    path = board.cache.path_for(repo_key, phase)
    fields = _stat_fields(path)
    if fields is None:
        return {
            "phase": phase.value,
            "exists": False,
            "size": 0,
            "mtime_ns": 0,
        }
    return {
        "phase": phase.value,
        "exists": True,
        "size": fields["size"],
        "mtime_ns": fields["mtime_ns"],
    }


def _batch_states() -> list[list]:
    out = []
    for run_id in batch.store.run_ids():
        fields = _stat_fields(
            batch.store.run_dir(run_id) / batch.store.STATE_FILE,
        )
        if fields is None:
            continue
        out.append([run_id, fields["size"], fields["mtime_ns"]])
    return sorted(out)


def _board_py_files() -> list[dict]:
    files = sorted(_BOARD_DIR.glob("*.py"), key=lambda path: path.name)
    out = []
    for path in files:
        fields = _stat_fields(path)
        if fields is None:
            continue
        out.append({
            "name": path.name,
            "size": fields["size"],
            "mtime_ns": fields["mtime_ns"],
        })
    return out


def _stat_fields(path: Path) -> dict | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns}
