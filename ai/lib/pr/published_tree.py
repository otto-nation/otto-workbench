"""What of this worktree the remote does not yet hold.

Triage and the comments pass read the filesystem, so a verdict formed here can
cite code a reviewer cannot open. The remote tip and the unpublished path set
are the one answer those callers share: committed-but-unpushed paths, plus
whatever porcelain still shows dirty or untracked. A remote that cannot be
asked is not an empty set — it is everything unpublished, so a claim is
withheld rather than posted with a 404.

Layer 4: `git.client` and `git.push` only. The dirty-set reader in `fix.scope`
stays there; lifting it would pull a layer-5 helper under a layer that cannot
import `fix`.
"""

# doc-group: publishing

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import git.client
import git.push


@dataclass(frozen=True)
class PublishedTree:
    """The remote tip of the checked-out branch, and every path not on it."""

    remote_tip: str
    unpublished_paths: frozenset[str]
    # True when the remote could not be asked, or the branch is not on origin.
    # An empty `unpublished_paths` is then "unknown", not "clean".
    all_unpublished: bool = False

    @property
    def unpublished(self) -> bool:
        """Whether any code this tree would cite is missing from the remote."""
        return self.all_unpublished or bool(self.unpublished_paths)


def of(wt_path: Path) -> PublishedTree:
    """The remote tip and every path this worktree has that the remote does not.

    `all_unpublished` when there is no branch, no remote tip, or a listing
    failed — the conservative answer, so a caller that cannot prove the tree
    is published treats it as unpublished.
    """
    branch = git.client.current_branch(cwd=wt_path)
    if not branch or branch == "HEAD":
        return _unknown()
    tip = git.push.remote_head(wt_path, branch)
    if not tip:
        return _unknown()
    committed = git.client.run(
        "diff", "--name-only", "-z", tip, "HEAD", cwd=wt_path,
    )
    dirty = _porcelain_paths(wt_path)
    if not committed.ok or dirty is None:
        return PublishedTree(
            remote_tip=tip, unpublished_paths=frozenset(), all_unpublished=True,
        )
    return PublishedTree(
        remote_tip=tip,
        unpublished_paths=_nul_paths(committed.stdout) | dirty,
    )


def _unknown() -> PublishedTree:
    return PublishedTree(
        remote_tip="", unpublished_paths=frozenset(), all_unpublished=True,
    )


def _nul_paths(stdout: str) -> frozenset[str]:
    return frozenset(p for p in stdout.split("\0") if p)


def _porcelain_paths(wt_path: Path) -> frozenset[str] | None:
    """Staged, unstaged, and untracked paths, or None when status cannot be read.

    `-z` so a path with a newline is one record, not two. Rename and copy
    records carry two paths; both are unpublished, because either side is
    code the remote does not have under that name.
    """
    r = git.client.run("status", "--porcelain", "-z", cwd=wt_path)
    if not r.ok:
        return None
    paths: set[str] = set()
    parts = r.stdout.split("\0")
    i = 0
    n = len(parts)
    while i < n:
        rec = parts[i]
        i += 1
        if len(rec) < 3:
            continue
        status, path = rec[:2], rec[3:]
        if "R" in status or "C" in status:
            dest = parts[i] if i < n else ""
            i += 1
            paths.update(p for p in (path, dest) if p)
            continue
        if path:
            paths.add(path)
    return frozenset(paths)
