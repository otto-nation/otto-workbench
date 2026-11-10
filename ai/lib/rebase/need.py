"""Whether a branch needs a rebase, read from refs rather than from GitHub.

GitHub's `mergeStateStatus` answers a different question — `UNKNOWN` while it
computes, `BLOCKED` outranking `BEHIND` — so a branch two commits behind its base
can read as up to date. This counts the commits the base has that the head does
not, and says "cannot tell" rather than "current" when either ref is missing.
`conflict_need` narrows that to a branch whose merge with the base conflicts.
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


def conflict_need(cwd: str, head_ref: str, base_ref: str, *, base_label: str = "") -> RebaseNeed:
    """Whether *head_ref* conflicts with *base_ref*, both resolved in *cwd*.

    A branch that is behind but merges cleanly needs no rebase here; only one
    `git merge-tree` cannot merge does. Unresolved, as `need` is, when either ref
    is missing or merge-tree fails rather than giving a verdict, so a caller
    falls back to another source instead of reading a failure as "no conflict".
    """
    label = base_label or base_ref
    behind = need(cwd, head_ref, base_ref, base_label=label)
    if not behind.needed:
        return behind
    merged = git.client.run("merge-tree", "--write-tree", base_ref, head_ref, cwd=cwd)
    if merged.returncode == 0:
        return RebaseNeed(False, f"{behind.behind} behind {label}, no conflicts", behind.behind)
    if merged.returncode == 1:
        return RebaseNeed(True, f"conflicts with {label}", behind.behind)
    # Exit >1 is git failing, not a merge verdict.
    return RebaseNeed(False, f"cannot test a merge with {label}")
