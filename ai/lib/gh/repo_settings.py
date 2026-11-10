"""The merge buttons GitHub shows for a repository.

`gh pr view` has no repository fields, so a rebase that needs to know whether
the repo is squash-only cannot piggy-back on the PR snapshot. `gh repo view`
answers with `squashMergeAllowed`, `mergeCommitAllowed` and
`rebaseMergeAllowed` on one call.

Best effort, like every other tracker read: gh missing, unauthenticated,
rate-limited, or a payload that is not three booleans all arrive as None.
Nothing here may invent a merge policy from a partial answer.
"""

# doc-group: platform

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import gh.client

MERGE_FIELDS = ("squashMergeAllowed", "mergeCommitAllowed", "rebaseMergeAllowed")


@dataclass(frozen=True)
class MergeMethods:
    """Which merge buttons GitHub shows for a repository."""

    squash: bool
    merge: bool
    rebase: bool

    @property
    def squash_only(self) -> bool:
        """Whether squash is the only merge method the repo allows."""
        return self.squash and not self.merge and not self.rebase


def merge_methods(repo: str, cwd: str | Path | None = None) -> MergeMethods | None:
    """The merge buttons GitHub shows for *repo*, or None when they cannot be read.

    *repo* is ``OWNER/REPO``, or ``HOST/OWNER/REPO`` on Enterprise, passed as
    ``gh repo view``'s positional repository — not ``--repo``, which that
    command does not take. Empty uses the repository of *cwd*.
    """
    args = ["repo", "view", *([repo] if repo else []),
            "--json", ",".join(MERGE_FIELDS)]
    data = gh.client.json_out(*args, cwd=cwd, default=None)
    if not isinstance(data, dict):
        return None
    values = tuple(data.get(field) for field in MERGE_FIELDS)
    if not all(isinstance(v, bool) for v in values):
        return None
    return MergeMethods(*values)
