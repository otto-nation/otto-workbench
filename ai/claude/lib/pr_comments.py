"""PR comments lifecycle tracking.

Handles thread lifecycle state computation, local state persistence,
and GitHub data fetching for the pr-comments skill.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


# ── State file I/O ─────────────────────────────────────────────────────────

def empty_state(repo: str, pr_number: int, my_login: str) -> dict:
    """Create a fresh state object."""
    return {
        "repo": repo,
        "pr_number": pr_number,
        "last_run": datetime.now(timezone.utc).isoformat(),
        "my_login": my_login,
        "threads": {},
    }


def load_state(path: Path) -> dict | None:
    """Load state from file. Returns None if file doesn't exist."""
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def save_state(path: Path, state: dict) -> None:
    """Save state to file, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    with open(path, "w") as f:
        json.dump(state, f, indent=2)
        f.write("\n")
