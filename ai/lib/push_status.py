"""Push domain — status rendering.

Detects unpushed commits by comparing local HEAD against the remote
tracking branch.  Computed at render time (no stored state needed).
"""

# doc-group: pr-state

from __future__ import annotations

from pathlib import Path

import git_client


def detect_unpushed(worktree_root: Path, branch: str) -> int | None:
    """Count commits ahead of the remote tracking branch.

    Returns the count (0 = up to date), or None if no remote ref exists
    (branch never pushed).
    """
    r = git_client.run("rev-list", "--count", f"origin/{branch}..HEAD",
                       cwd=worktree_root)
    if not r.ok:
        return None
    count = r.stdout.strip()
    return int(count) if count.isdigit() else None


_UNSET = object()


def render_status(worktree_root: Path, branch: str, *, ahead=_UNSET) -> list[str]:
    """Render push state as status lines for the pr dashboard.

    If *ahead* is provided (pre-computed by the caller via detect_unpushed),
    it is used directly and no subprocess is spawned.  Pass ahead=None to
    indicate the branch is not pushed; pass a non-negative int for the commit
    count.  When omitted, detect_unpushed is called internally.
    """
    if ahead is _UNSET:
        ahead = detect_unpushed(worktree_root, branch)
    if ahead is None:
        return ["**Push**: branch not pushed to remote"]
    if ahead == 0:
        return ["**Push**: up to date"]
    return [f"**Push**: {ahead} commit(s) not pushed"]
