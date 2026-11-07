"""Default-mode conflict stop: classify, regenerate if declared, else abort.

``pr rebase`` without ``--fix`` never calls the AI backend. A stop where every
conflicted file is generated-class continues only when each rebuild succeeds
with ``files_stale`` empty; otherwise ``git rebase --abort`` leaves a clean
tree and exit 3. ``--stop-on-conflict`` and a resumed rebase keep the paused
tree instead — those paths stay in ``lifecycle._report_conflicts_and_stop``.

``rebase.regenerate`` must be declared for ACCEPT_THEIRS files; the conventional
``mise run generate`` fallback is ``--fix``-only. REGENERATE files still use
``git.regenerate.LOCKFILE_REGENERATORS``.
"""
# doc-group: platform

from __future__ import annotations

from pathlib import Path

import core.log
import git.client
import git.regenerate
from pr.domains import RebaseStatus

from . import conflicts
from . import inspect as rebase_inspect
from . import regen_dispatch
from . import types as rebase_types

ConflictStrategy = rebase_types.ConflictStrategy
GeneratedSignal = rebase_types.GeneratedSignal
RegenQueue = git.regenerate.RegenQueue

_GENERATED = frozenset({
    ConflictStrategy.ACCEPT_THEIRS.value,
    ConflictStrategy.REGENERATE.value,
})


def _stage_generated(
    filepath: str, kind: str, cwd: str, queue: RegenQueue,
) -> bool:
    full_path = Path(cwd) / filepath
    plan = conflicts.classify_conflict(filepath, full_path, cwd)
    if kind == ConflictStrategy.REGENERATE.value and plan.regenerator is None:
        return False
    if kind == ConflictStrategy.REGENERATE.value:
        return regen_dispatch.dispatch_regenerate(
            filepath, full_path, cwd, plan.regenerator, queue,
        )
    signal = plan.signal or GeneratedSignal.HEADER
    return regen_dispatch.dispatch_accept_theirs(
        filepath, full_path, cwd, signal, queue, conventional=False,
    )


def classify_stop(cwd: str, files: list[str]) -> dict[str, str]:
    """Map each conflicted path to ``ConflictStrategy.value`` while still paused."""
    return {
        filepath: conflicts.classify_conflict(
            filepath, Path(cwd) / filepath, cwd,
        ).strategy.value
        for filepath in files
    }


def try_generated(
    cwd: str, classes: dict[str, str], tally: rebase_types.ResolutionTally,
) -> bool:
    """Stage and rebuild generated-class conflicts. True only when none are stale.

    Hand-written, binary, or delete conflicts, a missing declared regenerator,
    or a failed rebuild all return False. Staging that already happened is
    undone by the caller's abort.
    """
    if not classes or any(kind not in _GENERATED for kind in classes.values()):
        return False
    queue = RegenQueue()
    resolved: list[str] = []
    for filepath, kind in classes.items():
        if not _stage_generated(filepath, kind, cwd, queue):
            return False
        resolved.append(filepath)
    failed = regen_dispatch._run_deferred_regenerations(queue, cwd, trail=None)
    stale = list(queue.unrebuildable) + failed
    tally.files.extend(path for path in resolved if path not in tally.files)
    tally.stale.extend(path for path in stale if path not in tally.stale)
    if stale:
        return False
    tally.resolution = "generated"
    return True


def abort_clean(
    cwd: str, ctx, *, target_ref: str,
    classes: dict[str, str], tally: rebase_types.ResolutionTally,
) -> int:
    """Abort the in-progress rebase, emit the exit-3 report, return CONFLICTS_EXIT.

    Captures the conflict snapshot before ``git rebase --abort``, which drops
    rebase state. Does not import ``rebase.commands``: that module imports
    ``lifecycle``, which imports this one.
    """
    pre = tally.pre_rebase_head or rebase_inspect.rebase_orig_head(cwd)
    report = rebase_types.ConflictReport.from_repo(
        cwd, aborted=True, tree="clean", classes=classes,
        files_stale=list(tally.stale), pre_rebase_head=pre,
    )
    core.log.info("Aborting rebase...")
    r = git.client.run("rebase", "--abort", cwd=cwd)
    if r.ok:
        core.log.ok("Rebase aborted.")
    rebase_types.RebaseOutcome(
        status=RebaseStatus.ABORTED,
        files_stale=list(tally.stale),
        aborted=True,
        classes=dict(classes),
        pre_rebase_head=pre,
        target_base=target_ref,
    ).save(ctx)
    report.emit()
    return rebase_types.CONFLICTS_EXIT
