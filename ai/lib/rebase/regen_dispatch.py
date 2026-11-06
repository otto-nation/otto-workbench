"""AI-free regeneration dispatch used by both ``--fix`` and the default stop.

``resolve_ai`` imports ``agent.invoke`` at module level, so the three helpers
that only stage generated files and run regenerators live here: default-mode
conflict handling can reuse them without importing the AI backend.
"""
# doc-group: platform

from __future__ import annotations

from pathlib import Path

import core.log
from core.trail import Trail, tinfo
import git.regenerate

from . import conflicts
from . import repo_regen
from . import types as rebase_types

GeneratedSignal = rebase_types.GeneratedSignal
Regenerator = git.regenerate.Regenerator
RegenQueue = git.regenerate.RegenQueue


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


def _run_deferred_regenerations(
    queue: RegenQueue, cwd: str, *, trail: Trail | None,
) -> list[str]:
    """Run deferred regeneration jobs and return files that failed."""
    failed: list[str] = []
    for job in queue:
        if not git.regenerate.run_regeneration(job, cwd=cwd, trail=trail):
            failed.extend(job.files)
    return failed
