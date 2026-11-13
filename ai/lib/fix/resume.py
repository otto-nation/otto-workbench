"""Edits an interrupted comments pass left uncommitted, so the next round resumes them.

A comments fix pass that is stopped mid-agent leaves its edits in the worktree.
The next round treats those files as somebody else's dirt and will not commit
them — which is right for an operator's own edits, and wrong for the pass that
just wrote them. This module records the HEAD and the blob of each leftover at
the stop, and on the next run drops matching paths from the dirty baseline so
they are attributed to the pass again.

The review domain keeps its own stash (`review.convergence.set_aside_leftovers`);
only the comments adapter opts in. A checkpoint resume (`fix.pass_resume`)
skips this adoption and clears the leftover record so the two cannot disagree.
"""

# doc-group: pipeline

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import core.log
import core.serde
import git.client
from core.trail import Trail, tinfo

_FILENAME = "interrupted-pass.json"


@dataclass(frozen=True)
class Interruption:
    """What a stopped pass left behind: the HEAD it started from, and leftover blobs."""

    head_before: str
    leftovers: dict[str, str]


def _path(artifacts: str | Path) -> Path:
    return Path(artifacts) / _FILENAME


def record(
    artifacts: str | Path, workdir: str | Path, head_before: str, paths,
) -> None:
    """Write *paths*' current blobs under *artifacts*, replacing any prior record."""
    leftovers = {
        path: git.client.worktree_blob(path, cwd=workdir) for path in sorted(paths)
    }
    core.serde.write_json(
        _path(artifacts),
        {"head_before": head_before, "leftovers": leftovers},
    )


def pending(artifacts: str | Path) -> Interruption | None:
    """The last interrupted pass recorded under *artifacts*, or None if none."""
    path = _path(artifacts)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    head = data.get("head_before")
    leftovers = data.get("leftovers")
    if not isinstance(head, str) or not isinstance(leftovers, dict):
        return None
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in leftovers.items()):
        return None
    return Interruption(head_before=head, leftovers=leftovers)


def adopt(
    artifacts: str | Path, workdir: str | Path, dirty_before: set[str],
    head_now: str, trail: Trail | None,
) -> set[str]:
    """*dirty_before* without leftovers whose blob still matches, if HEAD has not moved.

    The record is always deleted: a file that no longer describes this tree must
    not resume a later, unrelated pass. Paths whose blob changed stay dirty —
    someone else edited them after the stop. A moved HEAD keeps every path.
    """
    info = pending(artifacts)
    _path(artifacts).unlink(missing_ok=True)
    dirty = set(dirty_before)
    if info is None:
        return dirty
    if not git.client.same_commit(info.head_before, head_now, cwd=workdir):
        return dirty
    resumed = [
        path for path, blob in info.leftovers.items()
        if git.client.worktree_blob(path, cwd=workdir) == blob
    ]
    dirty -= set(resumed)
    if resumed:
        listed = ", ".join(sorted(resumed))
        core.log.warn(
            f"Resuming {len(resumed)} edit(s) an interrupted pass left "
            f"uncommitted: {listed}"
        )
        tinfo(trail, "fix_leftovers_resumed",
              f"resumed {len(resumed)} leftover edit(s)",
              data={"paths": sorted(resumed), "workdir": str(workdir)})
    return dirty


def clear(artifacts: str | Path) -> None:
    """Drop a leftover-adoption record without adopting it.

    A checkpoint resume owns ``dirty_before`` / ``head_before`` and must not
    leave ``interrupted-pass.json`` for a later fresh pass to adopt as if this
    interruption were still pending.
    """
    _path(artifacts).unlink(missing_ok=True)
