"""Where a rebase starts replaying from — a named, detected, or whole-branch start.

``select`` is the one owner of that choice. An explicit ``--fork-point REF``
wins and replays only the commits after REF, whether or not a prefix was
detected. ``--force`` turns detection off and replays the whole branch.
Otherwise a landed prefix is skipped rather than refused.

When a leading run of commits is already in the base — a squash-landed
parent of a stacked child, or any other prefix that landed first — the
replay is ``git rebase --onto <base> <fork-point>``. The skip is announced
on the console as ``Skipping already-landed commit(s): …`` and recorded as
a ``fork_point`` trail event (the SHAs, how many landed, the source
``detected``).
"""

# doc-group: platform

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import core.log
from core.trail import Trail, tdecision, terr
import gh.landed
import git.client
import pr.context

# git's `<upstream>` argument. Named here so the trail and callers spell the
# flag the same way the CLI parser does.
FLAG = "--fork-point"


class ForkPointSource(StrEnum):
    """How the replay start was chosen."""

    REQUESTED = "requested"
    DETECTED = "detected"


@dataclass(frozen=True)
class ReplayStart:
    """Where the rebase starts replaying from.

    An empty ``sha`` means the whole branch. ``skipped`` is the prefix
    detection dropped; empty when the start was requested or forced.
    """

    sha: str = ""
    source: ForkPointSource | None = None
    skipped: tuple[str, ...] = ()


def _resolved_fork_point(cwd: str, fork_point: str) -> str:
    """*fork_point* as a sha, or "" if it is not a commit reachable from HEAD.

    Checked before the replay rather than left to git, because git's own
    failure for a bad ``<upstream>`` is indistinguishable from a rebase that
    went wrong — and because a ref that resolves but is *not* an ancestor of
    HEAD silently replays a different set of commits than the operator asked
    for, which git reports as success.
    """
    sha = git.client.out("rev-parse", "--verify", f"{fork_point}^{{commit}}",
                         cwd=cwd)
    if not sha:
        return ""
    if not git.client.ok("merge-base", "--is-ancestor", sha, "HEAD", cwd=cwd):
        return ""
    return sha


def select(
    cwd: str, ctx: pr.context.ResolvedContext, *, target_ref: str,
    requested: str, force: bool, trail: Trail | None,
) -> ReplayStart | None:
    """The replay start for this run, or None when a named ref is unusable.

    *requested* is checked first, so an explicit fork point still wins over
    ``--force``. Force with no request returns an empty start — the whole
    branch — and does not look for a landed prefix.
    """
    if requested:
        sha = _resolved_fork_point(cwd, requested)
        if not sha:
            terr(trail, "preflight", f"unusable fork point {requested}")
            core.log.error(
                f"Cannot replay from {requested} — it is not a commit "
                f"on {ctx.branch}."
            )
            core.log.dim("Pass a commit the branch descends from.")
            return None
        tdecision(
            trail, "fork_point", f"replaying only the commits after {sha}",
            reason=f"{FLAG} {requested}",
            data={
                "fork_point": sha,
                "requested": requested,
                "source": ForkPointSource.REQUESTED.value,
            },
        )
        core.log.info(
            f"Replaying only the commits after {git.client.abbrev(sha)}."
        )
        return ReplayStart(sha=sha, source=ForkPointSource.REQUESTED)
    if force:
        return ReplayStart()

    partial = gh.landed.partial_landing(cwd, target_ref=target_ref)
    if partial is None:
        return ReplayStart()

    commits = gh.landed.branch_commits(cwd, target_ref=target_ref)
    skipped = tuple(commits[:partial.landed])
    sha = partial.fork_point
    tdecision(
        trail, "fork_point", f"replaying only the commits after {sha}",
        reason=(
            f"{partial.landed} of {partial.total} commit(s) already in "
            f"{target_ref}"
        ),
        data={
            "fork_point": sha,
            "fork_subject": partial.fork_subject,
            "landed": partial.landed,
            "unlanded": partial.unlanded,
            "skipped": [c.sha for c in skipped],
            "source": ForkPointSource.DETECTED.value,
        },
    )
    core.log.warn(
        "Skipping already-landed commit(s): "
        + "; ".join(c.subject for c in skipped)
    )
    core.log.dim("Pass --force to replay the whole branch.")
    return ReplayStart(
        sha=sha,
        source=ForkPointSource.DETECTED,
        skipped=tuple(c.sha for c in skipped),
    )
