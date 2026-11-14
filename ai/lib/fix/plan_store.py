"""Load and save per-item plans under a pass's artifacts directory.

Plans are local JSON beside the run — never posted. A write is atomic through
``core.serde.write_json``. An interrupted pass does not resume from these
files; they are an audit record and the input the implementer is handed.
"""

# doc-group: pipeline

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

import core.serde
from fix.plan_model import ItemPlan, classify_paths

_SAFE_ID = re.compile(r"[^A-Za-z0-9._-]+")


def sanitise_id(item_id: str) -> str:
    """A filename-safe form of an item id."""
    cleaned = _SAFE_ID.sub("_", item_id).strip("._")
    return cleaned or "item"


def plan_path(directory: Path, item_id: str) -> Path:
    """`<directory>/<sanitised id>.json`."""
    return Path(directory) / f"{sanitise_id(item_id)}.json"


def save(plan: ItemPlan, directory: Path) -> Path:
    """Write `plan` atomically and return the path."""
    path = plan_path(directory, plan.item_id)
    core.serde.write_json(path, core.serde.to_dict(plan))
    return path


def load(directory: Path, item_id: str) -> ItemPlan | None:
    """The plan for `item_id`, or None if missing or unreadable."""
    return core.serde.load_file(ItemPlan, plan_path(directory, item_id))


def clear(directory: Path) -> None:
    """Remove every plan JSON under `directory`. Missing is a no-op."""
    root = Path(directory)
    if not root.is_dir():
        return
    for path in sorted(root.glob("*.json")):
        path.unlink(missing_ok=True)


def with_paths(
    plan: ItemPlan,
    pr_files: frozenset[str] | None = None,
) -> ItemPlan:
    """Copy of `plan` with `outside_pr` filled; files themselves unchanged.

    Rejected paths are left on `files` so `valid` can still see them; the
    caller decides whether an unsafe path makes the plan unusable.
    """
    _rejected, outside = classify_paths(plan.files, pr_files)
    if outside == plan.outside_pr:
        return plan
    return replace(plan, outside_pr=outside)
