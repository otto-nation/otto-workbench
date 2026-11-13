"""AI-free regeneration dispatch used by both ``--fix`` and the default stop.

``resolve_ai`` imports ``agent.invoke`` at module level, so the three helpers
that only stage generated files and run regenerators live here: default-mode
conflict handling can reuse them without importing the AI backend.
"""
# doc-group: platform

from __future__ import annotations

from pathlib import Path

import core.log
from core.trail import Trail, terr, tinfo
import git.regenerate

from . import conflicts
from . import inspect as rebase_inspect
from . import repo_regen
from . import types as rebase_types

ConflictStrategy = rebase_types.ConflictStrategy
GeneratedSignal = rebase_types.GeneratedSignal
Regenerator = git.regenerate.Regenerator
RegenQueue = git.regenerate.RegenQueue
ResolutionTally = rebase_types.ResolutionTally

_GENERATED = (ConflictStrategy.ACCEPT_THEIRS, ConflictStrategy.REGENERATE)


def dispatch_regenerate(
    filepath: str, full_path: Path, cwd: str,
    regenerator: Regenerator, queue: RegenQueue,
    *, trail: Trail | None = None,
) -> bool:
    """Handle the REGENERATE strategy. Returns False on failure."""
    if trail:
        trail.decision(
            "regenerate", f"accepting theirs for {filepath}",
            reason=f"lockfile with known regenerator: {' '.join(regenerator.cmd)}",
        )
    if not conflicts.accept_theirs_and_stage(filepath, cwd):
        return False
    queue.add(full_path.parent, filepath, regenerator.cmd, regenerator.stage_dir)
    return True


def dispatch_accept_theirs(
    filepath: str, full_path: Path, cwd: str,
    signal: GeneratedSignal, queue: RegenQueue,
    *, trail: Trail | None = None, conventional: bool = True,
) -> bool:
    """Handle the ACCEPT_THEIRS strategy. Returns False on failure.

    Taking theirs is a placeholder, not the resolution: the incoming side was
    generated from the branch's sources before the base moved, so it is stale
    the moment it is staged. The queued regeneration is what actually resolves
    the file, and a repo that declares no way to rebuild leaves it stale — the
    honest report, and what the caller surfaces as ``files_stale``.
    """
    if trail:
        trail.decision(
            "generated_file", f"accepting theirs for {filepath}",
            reason=f"generated file detected via {signal}",
        )
    if not conflicts.accept_theirs_and_stage(filepath, cwd):
        return False
    if not repo_regen.queue_repo_regeneration(
        filepath, cwd, queue, conventional=conventional,
    ):
        queue.mark_unrebuildable(filepath)
        core.log.warn(f"No regeneration command for {filepath} — staged stale")
        tinfo(
            trail, "generated_file", f"no regeneration command for {filepath}",
            data={"filepath": filepath, "signal": str(signal)},
        )
    return True


def run_deferred_regenerations(
    queue: RegenQueue, cwd: str, *, trail: Trail | None,
) -> list[str]:
    """Run deferred regeneration jobs and return files that failed."""
    failed: list[str] = []
    for job in queue:
        if not git.regenerate.run_regeneration(job, cwd=cwd, trail=trail):
            failed.extend(job.files)
    return failed


def _hand_written_unmerged(cwd: str) -> bool:
    """True when an unmerged path is not generated-class.

    Generators import hand-written code; conflict markers in one of those
    files crash the rebuild. Generated unmerged paths are the ones being
    rebuilt and do not block.
    """
    files = rebase_inspect.detect_conflicts(cwd)
    if not files:
        return False
    for filepath in files:
        plan = conflicts.classify_conflict(filepath, Path(cwd) / filepath, cwd)
        if plan.strategy not in _GENERATED:
            return True
    return False


def _queue_stale_path(
    filepath: str, cwd: str, queue: RegenQueue, *, conventional: bool,
) -> bool:
    """Re-derive a regenerator for a persisted stale path. False if unknown."""
    regenerator = git.regenerate.find_regenerator(filepath)
    if regenerator is not None:
        queue.add(
            Path(cwd) / Path(filepath).parent, filepath,
            regenerator.cmd, regenerator.stage_dir,
        )
        return True
    return repo_regen.queue_repo_regeneration(
        filepath, cwd, queue, conventional=conventional,
    )


def rebuild_stale(
    cwd: str, tally: ResolutionTally, *,
    trail: Trail | None, conventional: bool = True,
    paths: list[str] | None = None,
) -> None:
    """Rebuild persisted ``files_stale`` once no hand-written file is unmerged.

    Paths are the stop's ``RebaseSummary.files_stale`` (restored onto the
    tally); regenerators are re-derived via ``find_regenerator`` / repo
    regen rather than persisted as jobs. Successes drop off the tally;
    failures stay in ``tally.stale`` with the real regeneration error.
    """
    pending = list(dict.fromkeys(paths if paths is not None else tally.stale))
    if not pending:
        return
    if _hand_written_unmerged(cwd):
        return
    queue = RegenQueue()
    unknown: list[str] = []
    for filepath in pending:
        if not _queue_stale_path(
            filepath, cwd, queue, conventional=conventional,
        ):
            unknown.append(filepath)
    failed = run_deferred_regenerations(queue, cwd, trail=trail)
    if failed:
        terr(trail, "regenerate", "regeneration failed", data={"files": failed})
        core.log.warn(
            f"Regeneration failed for: {', '.join(failed)} — lockfiles may be stale",
        )
    failed_set = set(failed)
    unknown_set = set(unknown)
    attempted = set(pending)
    tally.stale[:] = [
        path for path in tally.stale
        if path not in attempted or path in failed_set or path in unknown_set
    ]
