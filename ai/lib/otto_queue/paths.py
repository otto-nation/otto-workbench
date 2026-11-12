"""Where the queue keeps its files: `state_dir()/queue/` holds the ledger, `leases/` and `slots/`.

Resolved per call, never at import, so a re-pointed `WORKBENCH_STATE_DIR` is
seen by every reader.
"""

# doc-group: queue

from __future__ import annotations

from pathlib import Path

import core.workbench_paths
from otto_queue.model import EntryClass

QUEUE_DIRNAME = "queue"
DB_NAME = "queue.db"
LEASES_DIRNAME = "leases"
SLOTS_DIRNAME = "slots"


def queue_root() -> Path:
    return core.workbench_paths.state_dir() / QUEUE_DIRNAME


def db_path(root: Path) -> Path:
    return root / DB_NAME


def lease_path(root: Path, entry_id: int) -> Path:
    return root / LEASES_DIRNAME / f"{entry_id}.lock"


def slot_path(root: Path, entry_class: EntryClass, index: int) -> Path:
    # Three digits, so a listing sorts in numeric order however many slots a class has.
    return root / SLOTS_DIRNAME / f"{entry_class.value}-{index:03d}.lock"


def admit_lock_path(root: Path) -> Path:
    return root / SLOTS_DIRNAME / "admit.lock"
