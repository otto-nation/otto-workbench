"""The review-thread ledger: where every thread stood at the end of the last run.

``<target_dir>/pr-comments/state.json`` is a snapshot of what GitHub said about
each review thread — its lifecycle state, reviewer, file and line.
``pr.comments.threads_state_path`` owns that join; this module is handed the path
rather than deriving it.

Everything here is re-fetched from the API on every run, so the file is a cache
and recovers like one: :func:`serde.load_file` discards a file it cannot parse
and the next write rebuilds it. Its one use beyond the dashboard is standing in
for a thread an incomplete fetch could not reach — see ``pr.comments.sync_threads``.

Whether a thread still needs triage is not recorded here. That is the fix
record's to answer, through ``pr.settlement.settled_ids``.

Imports ``serde`` and nothing else in ``ai/lib``, so the vocabulary the thread
pipeline is written in sits below the code that fetches, renders and triages it.
"""

# doc-group: pr-state

from __future__ import annotations

from dataclasses import dataclass, field, replace as dataclass_replace
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path

import core.serde


class ThreadState(StrEnum):
    """Where a review thread stands in the conversation.

    Computed from the thread's comments on every run rather than persisted as a
    decision — `compute_thread_state` owns the derivation. It is recorded so a
    later run can report what changed without re-deriving the previous answer.
    """

    NEW = "new"
    ADDRESSED = "addressed"
    VERIFIED = "verified"
    CONTESTED = "contested"
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class ThreadRecord:
    """One review thread, as the last run saw it on GitHub.

    Frozen because a run reads the whole ledger, decides, and writes a new
    record — a caller that resolves a thread replaces its record rather than
    reaching into the map to edit one field of it.
    """

    state: ThreadState = ThreadState.NEW
    reviewer: str = ""
    file: str = ""
    line: int | None = None


@dataclass(frozen=True)
class CommentsState:
    """The whole of ``<target_dir>/pr-comments/state.json``.

    ``threads`` is keyed by GraphQL node id, which is what every consumer joins
    the freshly-fetched threads against.

    ``repo``, ``pr_number`` and ``my_login`` are written for the reader of the
    file rather than for the code: a run is handed all three by its context and
    reconstructs the ledger from GitHub regardless, so nothing loads them to
    decide anything.
    """

    repo: str = ""
    pr_number: int = 0
    my_login: str = ""
    last_run: str = ""
    threads: dict[str, ThreadRecord] = field(default_factory=dict)


def load_state(path: Path) -> CommentsState | None:
    """The recorded thread state, or None when there is no usable file.

    A file with any unreadable entry is discarded whole: every field is
    re-fetched on the next run, so there is nothing in it worth keeping apart.
    """
    return core.serde.load_file(CommentsState, path)


def save_state(path: Path, state: CommentsState) -> None:
    """Write the thread state, stamping the time of this run.

    The stamp is applied here rather than by the caller so no writer can record
    a ledger without recording when it was taken.
    """
    stamped = dataclass_replace(
        state, last_run=datetime.now(timezone.utc).isoformat())
    core.serde.write_json(path, core.serde.to_dict(stamped))
