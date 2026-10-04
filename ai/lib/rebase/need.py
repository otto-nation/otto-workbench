"""Whether a branch needs a rebase, read from refs rather than from GitHub.

GitHub's `mergeStateStatus` answers a different question — `UNKNOWN` while it
computes, `BLOCKED` outranking `BEHIND` — so a branch two commits behind its base
can read as up to date. This counts the commits the base has that the head does
not, and says "cannot tell" rather than "current" when either ref is missing.
"""

# doc-group: platform

from __future__ import annotations

from dataclasses import dataclass

import git.client


@dataclass(frozen=True)
class RebaseNeed:
    """Whether a rebase is needed, why, and the count it was read from.

    ``behind`` is None when either ref did not resolve, which ``resolved``
    reports: a caller falls back to another source instead of reading an
    unreadable branch as current.
    """

    needed: bool
    reason: str
    behind: int | None = None

    @property
    def resolved(self) -> bool:
        return self.behind is not None


def need(cwd: str, head_ref: str, base_ref: str, *, base_label: str = "") -> RebaseNeed:
    """Rebase need of *head_ref* against *base_ref*, both resolved in *cwd*.

    *base_label* names the base in the reason ("3 behind main"); the ref itself
    is used when it is empty.
    """
    label = base_label or base_ref
    behind = git.client.commits_behind(cwd, head_ref=head_ref, base_ref=base_ref)
    if behind is None:
        return RebaseNeed(False, f"cannot compare against {label}")
    if behind > 0:
        return RebaseNeed(True, f"{behind} behind {label}", behind)
    return RebaseNeed(False, f"up to date with {label}", 0)
