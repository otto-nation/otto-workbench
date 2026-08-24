"""The review-thread ledger, and the one state file that is not a snapshot.

``ignore/pr-comments/state.json`` records where every review thread on a PR
stood at the end of the last run. Most of what it holds — the lifecycle state,
the reviewer, the last reply seen, the file and line — is re-fetched from the
API on every run, so losing it costs nothing. Three fields are not:
``classification``, ``summary`` and ``decided_at`` are triage decisions made
locally, and no API call reproduces them.

That asymmetry is why this file recovers differently from every other one behind
:func:`serde.load_file`. The shared reader discards a file it cannot parse and
lets the next write rebuild it, which is unconditionally safe for a cache. Here
it would silently re-triage every thread on the PR to recover from one bad
entry, so the tolerance sits one level down: :meth:`ThreadRecord._from_raw`
takes the loss per entry, and the threads either side of a corrupt one keep
their decisions.

Imports ``log`` and ``serde`` and nothing else in ``ai/lib``, so the vocabulary
the thread pipeline is written in sits below the code that fetches, renders and
triages it.
"""

# doc-group: pr-state

from __future__ import annotations

from dataclasses import dataclass, field, replace as dataclass_replace
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path

import log
import serde


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
    """One review thread, as the last run left it.

    The first five fields are a snapshot of what GitHub said and are replaced
    wholesale on the next sync. The last three are local: triage decided them,
    nothing re-derives them, and `sync_threads` drops them only when a reply
    arrives that the decision was not made against.

    Frozen because a run reads the whole ledger, decides, and writes a new
    record — a caller that resolves a thread replaces its record rather than
    reaching into the map to edit one field of it.
    """

    state: ThreadState = ThreadState.NEW
    reviewer: str = ""
    last_seen_reply_id: int | None = None
    file: str = ""
    line: int | None = None
    classification: str | None = None
    summary: str | None = None
    decided_at: str | None = None

    @classmethod
    def _from_raw(cls, raw) -> "ThreadRecord":
        """Rebuild one thread's record, or a blank one when the entry is corrupt.

        The hook exists for the blank. `serde` reconstructs a nested dataclass
        by raising on a value it cannot read, and that exception would travel
        up to :func:`serde.load_file` and discard the whole ledger — every
        thread's triage lost to recover from one entry. Catching it here spends
        the one entry instead: the thread it names is re-triaged on this run,
        which is the correct answer for a decision that cannot be read, and its
        neighbours are untouched.

        The warning is what keeps that from being silent. A run that quietly
        re-asks the model about a thread the operator already decided looks
        exactly like a run that had nothing cached.
        """
        if isinstance(raw, cls):
            return raw
        try:
            return serde.from_dict(cls, raw)
        except (TypeError, ValueError) as exc:
            log.warn(f"unreadable thread entry — re-triaging it: {exc}")
            return cls()

    @classmethod
    def _raw_schema(cls, object_schema: dict) -> dict:
        """The object form, unwidened — the recovery path is not a second shape.

        `_from_raw` reads more than this describes, but what it reads beyond the
        object is corruption it discards, not a document anything should write.
        Publishing the wider form would invite a writer to produce it.
        """
        return object_schema


@dataclass(frozen=True)
class CommentsState:
    """The whole of ``ignore/pr-comments/state.json``.

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

    The shared reader, reached through the per-entry tolerance
    :meth:`ThreadRecord._from_raw` installs — so this returns None only for a
    file that is missing or whose top level will not parse, never for one whose
    threads are individually damaged.
    """
    return serde.load_file(CommentsState, path)


def save_state(path: Path, state: CommentsState) -> None:
    """Write the thread state, stamping the time of this run.

    The stamp is applied here rather than by the caller so no writer can record
    a ledger without recording when it was taken.
    """
    stamped = dataclass_replace(
        state, last_run=datetime.now(timezone.utc).isoformat())
    serde.write_json(path, serde.to_dict(stamped))
