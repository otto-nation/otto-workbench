"""Whether HEAD moved under a fix pass, and what that means for landing.

The engine reads HEAD and the branch before the agent runs and compares them
again just before it lands. A commit made on top is tolerated and recorded,
because a hand commit made while the agent runs is allowed by contract. A
branch switch or a history rewrite (reset, rebase, amend) is refused, since
landing would then commit the pass's scope onto a branch it never worked on or
on top of history it did not read. A domain that asks to ``recover`` is
reported, never refused.
"""

# doc-group: pipeline

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import core.log
import git.client
from core.trail import Trail, tinfo, twarn


class Drift(StrEnum):
    """How HEAD relates to where it was when the pass started."""

    UNCHANGED = "unchanged"
    # The old HEAD is an ancestor: a hand or agent commit on top.
    ADVANCED = "advanced"
    # The old HEAD is no longer an ancestor: reset, rebase or amend.
    REWRITTEN = "rewritten"
    BRANCH_CHANGED = "branch_changed"
    UNREADABLE = "unreadable"


@dataclass(frozen=True)
class HeadState:
    """The commit and branch a worktree was on when it was read."""

    sha: str = ""
    # `git.client.current_branch`: "HEAD" when detached.
    branch: str = ""


@dataclass(frozen=True)
class HeadDrift:
    """The comparison of an earlier ``HeadState`` with the one now."""

    kind: Drift
    before: HeadState = HeadState()
    now: HeadState = HeadState()
    # before..now, newest first; only set for ADVANCED.
    commits: tuple[str, ...] = ()

    @property
    def refuses_landing(self) -> bool:
        """Whether landing must not proceed: a rewrite or a branch change."""
        return self.kind in (Drift.REWRITTEN, Drift.BRANCH_CHANGED)

    def message(self) -> str:
        """One sentence naming what moved, for the console and the commit result."""
        if self.kind is Drift.BRANCH_CHANGED:
            return (
                f"the branch changed during the fix pass ({self.before.branch or '?'} "
                f"-> {self.now.branch or '?'}); not committing onto it"
            )
        if self.kind is Drift.REWRITTEN:
            return (
                f"history was rewritten during the fix pass (HEAD {git.client.abbrev(self.before.sha)} "
                f"is no longer an ancestor of {git.client.abbrev(self.now.sha)}); not committing onto it"
            )
        if self.kind is Drift.ADVANCED:
            return f"{len(self.commits)} commit(s) landed on HEAD during the fix pass"
        return f"HEAD is {self.kind}"


def read(workdir: str | Path) -> HeadState:
    """The worktree's HEAD sha and branch right now."""
    return HeadState(
        sha=git.client.head_sha(cwd=workdir),
        branch=git.client.current_branch(cwd=workdir),
    )


def drift(workdir: str | Path, before: HeadState) -> HeadDrift:
    """Compare *before* with the worktree's HEAD now."""
    now = read(workdir)
    if not before.sha or not now.sha:
        return HeadDrift(Drift.UNREADABLE, before, now)
    if before.branch != now.branch:
        return HeadDrift(Drift.BRANCH_CHANGED, before, now)
    if before.sha == now.sha:
        return HeadDrift(Drift.UNCHANGED, before, now)
    ancestor = git.client.run(
        "merge-base", "--is-ancestor", before.sha, now.sha, cwd=workdir,
    )
    if ancestor.returncode == 1:
        return HeadDrift(Drift.REWRITTEN, before, now)
    if ancestor.returncode != 0:
        return HeadDrift(Drift.UNREADABLE, before, now)
    listed = git.client.run("rev-list", f"{before.sha}..{now.sha}", cwd=workdir)
    commits = tuple(listed.stdout.split()) if listed.ok else ()
    return HeadDrift(Drift.ADVANCED, before, now, commits)


def report(found: HeadDrift, trail: Trail | None) -> None:
    """Record *found*: a refusal is an error and a warn event, the rest are info events."""
    data = {
        "before": found.before.sha, "now": found.now.sha,
        "branch_before": found.before.branch, "branch_now": found.now.branch,
        "commits": list(found.commits),
    }
    if found.refuses_landing:
        core.log.error(found.message())
        twarn(trail, "fix_head_drift_refused", found.message(), data=data)
    elif found.kind is Drift.ADVANCED:
        tinfo(trail, "fix_head_advanced", found.message(), data=data)
    elif found.kind is Drift.UNREADABLE:
        tinfo(trail, "fix_head_unreadable", "could not compare HEAD around the pass",
              data=data)
