"""Checkpoint helpers for a resumable fix-engine pass (`fix.pass_resume`).

The engine checkpoints after each batch and after the gate, the suite, and
the landing. A later run reads those records back through these helpers —
answers, the agent's files, the suite result, the landing — so it can skip
work that already finished. Nothing here writes a checkpoint; the adapter's
``checkpoint`` hook is what persists.
"""

# doc-group: pipeline

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import core.serde
import fix.containment
import fix.suite
import fix.tracking
import git.client
import git.land
from pr.fix import FixOutcome, ItemOutcome
from pr.fix_checkpoint import EngineProgress


# Not a blob sha. Distinct from a missing map entry so a restored deletion
# (path gone from the live map, still recorded as deleted) is visible.
DELETED = "-"


def answered(outcomes: Sequence[ItemOutcome]) -> list[ItemOutcome]:
    """Outcomes that already have a non-``DEFERRED`` answer, keyed later by id.

    ``DEFERRED`` is work still owed, so a resume must not treat it as done.
    Anonymous rows (no id) cannot be matched against items and are dropped.
    """
    return [o for o in outcomes if o.id and o.outcome is not FixOutcome.DEFERRED]


def prior_answers(
    progress: EngineProgress | None, tracking_path: Path, *, overlay: bool = False,
) -> dict[str, ItemOutcome]:
    """Answers already given, keyed by id.

    Checkpointed ``progress.answers`` are the engine's last record. When
    *overlay* is true — only while the agent is still running — ticks the
    agent wrote to the tracking file after that record overlay them, so a
    mid-batch kill does not re-ask an item the file already ticked. From
    ``AGENT_DONE`` on, the engine record is the sole owner: do not overlay.
    """
    if overlay:
        by_id = {
            o.id: o for o in answered(progress.answers if progress else ())
        }
        for outcome in answered(fix.tracking.parse(tracking_path)):
            by_id[outcome.id] = outcome
        return by_id
    return {o.id: o for o in (progress.answers if progress else ()) if o.id}


def agent_files(
    workdir: Path | str, dirty_before: set[str] | frozenset[str],
    *, base: str = "HEAD",
) -> dict[str, str] | None:
    """Blob sha of each file the agent changed, keyed by path.

    Called only when the pass records checkpoints, so a default-off pass
    does not take an extra content snapshot. A path in the snapshot is
    mapped to its blob sha; a path that vanished is ``DELETED``. An
    unreadable snapshot is ``None`` — unknown is not "nothing changed".
    Files dirty before the pass are omitted: they are not the agent's.

    *base* is the tree the pass started from (``engine.head_before``),
    never live HEAD: after landing, HEAD already contains the agent's
    blobs, so comparing to HEAD would report no agent files.
    """
    snap = fix.containment.take(workdir)
    if not snap.ok:
        return None
    after = snap.by_path()
    head = fix.containment.read_tree(workdir, base)
    before = head.by_path() if head.ok else {}
    out: dict[str, str] = {}
    for path in set(before) | set(after):
        if path in dirty_before:
            continue
        rec = after.get(path)
        prior = before.get(path)
        if rec == prior:
            continue
        out[path] = rec.sha if rec is not None else DELETED
    return out


def edited_since(
    workdir: Path | str,
    recorded: dict[str, str],
    dirty_before: set[str] | frozenset[str] = frozenset(),
    *, base: str = "HEAD",
) -> list[str] | None:
    """Agent-file paths whose blob sha is not what *recorded* stored.

    Empty when every recorded path still has the same sha and no new agent
    file has appeared. ``None`` when the worktree cannot be read, which a
    later resume decision treats as a hard refuse rather than as "nothing
    changed". Missing vs deleted are distinct, so a file that was deleted
    and then restored is reported. *base* is the same tree ``agent_files``
    records against — ``engine.head_before``.
    """
    now = agent_files(workdir, dirty_before, base=base)
    if now is None:
        return None
    names = set(recorded) | set(now)
    return sorted(p for p in names if _blob(now, p) != _blob(recorded, p))


def _blob(mapping: dict[str, str], path: str) -> str | None:
    """Live or recorded sha, with ``""`` (legacy) and ``DELETED`` equivalent."""
    if path not in mapping:
        return None
    sha = mapping[path]
    return DELETED if sha in ("", DELETED) else sha


def suite_dict(result: fix.suite.SuiteResult) -> dict:
    """JSON-safe form of a suite result, for ``EngineProgress.suite``."""
    return core.serde.to_dict(result)


def suite_of(data: dict) -> fix.suite.SuiteResult:
    """Reconstruct a suite result from ``suite_dict`` output, or empty."""
    if not data:
        return fix.suite.SuiteResult()
    return core.serde.from_dict(fix.suite.SuiteResult, data)


def apply_suite_if_missing(
    outcomes: list[ItemOutcome], result: fix.suite.SuiteResult,
) -> None:
    """Re-apply a red suite only when the checkpointed answers predate it."""
    if not result.demotes:
        return
    if any(fix.suite.RED_DETAIL in (o.verify_detail or "") for o in outcomes):
        return
    fix.suite.apply_to(outcomes, result)


def landed_of(progress: EngineProgress | None) -> git.land.LandResult:
    """The landing ``progress`` recorded, or ``NO_CHANGES`` when none."""
    if progress is None or progress.commit_status is None:
        return git.land.LandResult(git.land.CommitStatus.NO_CHANGES)
    return git.land.LandResult(
        progress.commit_status,
        sha=progress.commit_sha,
        error=progress.land_error,
    )


def head_is_one_past(workdir: Path | str, head_before: str) -> bool:
    """Whether HEAD is the one commit whose parent is *head_before*.

    The CHECKED→LANDED gap: ``_land`` committed, then the process died
    before the LANDED checkpoint. ``HEAD^ == head_before`` is that shape.
    """
    if not head_before:
        return False
    head = git.client.head_sha(cwd=workdir)
    if not head:
        return False
    before = git.client.out("rev-parse", head_before, cwd=workdir)
    parent = git.client.out("rev-parse", f"{head}^", cwd=workdir)
    now = git.client.out("rev-parse", head, cwd=workdir)
    return bool(before and parent and now and now != before and parent == before)
