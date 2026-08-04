"""Push domain — status rendering.

Detects unpushed commits by comparing local HEAD against the remote
tracking branch.  Computed at render time (no stored state needed).
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def detect_unpushed(worktree_root: Path, branch: str) -> int | None:
    """Count commits ahead of the remote tracking branch.

    Returns the count (0 = up to date), or None if no remote ref exists
    (branch never pushed).
    """
    cwd = str(worktree_root)
    r = subprocess.run(
        ["git", "rev-list", "--count", f"origin/{branch}..HEAD"],
        capture_output=True, text=True, cwd=cwd,
    )
    if r.returncode != 0:
        return None
    count = r.stdout.strip()
    return int(count) if count.isdigit() else None


def render_status(worktree_root: Path, branch: str) -> list[str]:
    """Render push state as status lines for the pr dashboard."""
    ahead = detect_unpushed(worktree_root, branch)
    if ahead is None:
        return ["**Push**: branch not pushed to remote"]
    if ahead == 0:
        return ["**Push**: up to date"]
    return [f"**Push**: {ahead} commit(s) not pushed"]
