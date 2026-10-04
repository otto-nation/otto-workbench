"""What a batch publish pushes, read from the tree, and the lease it pushes under.

Publish is the only thing in a batch run that reaches the remote. It fetches the
one branch, compares the local branch with origin's, and checks the remote is
still the head the batch planned from (`Item.remote_sha`):

| Tree | Command |
|---|---|
| the fetch failed | refuse: `fetch_failed` |
| origin is not `remote_sha` (somebody pushed) | refuse: `remote_moved` |
| refs not comparable | refuse: `not_comparable` |
| local == origin, or only behind | nothing to push |
| local is a fast-forward of origin | `git-push` |
| diverged, from a batch rebase that started without `remote_sha` | refuse: `not_incorporated` |
| diverged, a remote commit has no patch-equivalent locally | refuse: `not_incorporated_remote` |
| diverged | `pr rebase --push-only --expect <remote_sha>` |

`pr comments --finish --post` follows when the comments step drafted or an item
is tracked. A refusal is a `failed` decision on step `publish` carrying `reason`;
a `not_incorporated_remote` one also lists the remote commits, and the operator
answers it with `force-publish` (push past exactly those commits; one that
appeared since is refused again) or drops the PR. The lease advances as soon as
the push lands, so a failure in the replies after it never strands the item.
"""

# doc-group: batch

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

import git.client
import rebase.target
from batch.model import Item, Step
from rebase.types import RefDivergence

GIT_PUSH = "git-push"


class Refusal(StrEnum):
    REMOTE_MOVED = "remote_moved"
    NOT_COMPARABLE = "not_comparable"
    NOT_INCORPORATED = "not_incorporated"
    NOT_INCORPORATED_REMOTE = "not_incorporated_remote"
    FETCH_FAILED = "fetch_failed"


@dataclass(frozen=True)
class TreeState:
    """The local branch against origin's, read after a fetch of that branch.

    ``incorporated`` is False only when a batch rebase started from a tip that
    does not contain the planned remote head, so force-pushing its result would
    drop remote commits the replay never saw. ``unincorporated`` names the
    remote commits (``"<sha> <subject>"``) with no patch-equivalent in the local
    branch — what a force-push of a diverged branch would throw away.
    """

    local: str
    remote: str
    divergence: RefDivergence
    fetched: bool = True
    incorporated: bool = True
    unincorporated: tuple[str, ...] = ()


@dataclass(frozen=True)
class PublishPlan:
    commands: list[list[str]] = field(default_factory=list)
    pushes: bool = False
    refusal: Refusal | None = None
    detail: str = ""
    # The remote commits a not_incorporated_remote refusal would drop.
    commits: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.refusal is None


def _unincorporated(item: Item, cwd: str) -> tuple[str, ...] | None:
    """Remote commits since the base with no patch-equivalent locally; None when unreadable."""
    limit = git.client.out("merge-base", f"refs/remotes/origin/{item.base_ref}",
                           item.remote_sha, cwd=cwd) if item.base_ref else ""
    r = git.client.run("cherry", "-v", f"refs/heads/{item.branch}", item.remote_sha,
                       *([limit] if limit else []), cwd=cwd)
    if not r.ok:
        return None
    return tuple(line[2:] for line in r.stdout.splitlines() if line.startswith("+ "))


def read_tree(item: Item) -> TreeState:
    """Fetch *item*'s branch alone and compare it with the local branch."""
    wt, branch = item.worktree, item.branch
    fetched = git.client.run(
        "fetch", "--no-tags", "--quiet", "origin",
        f"+refs/heads/{branch}:refs/remotes/origin/{branch}", cwd=wt,
    ).ok
    local = git.client.out("rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", cwd=wt)
    remote = git.client.out("rev-parse", "--verify", "--quiet",
                            f"refs/remotes/origin/{branch}", cwd=wt)
    divergence = rebase.target.local_vs_remote(wt, branch)
    incorporated = True
    if item.pre_rebase_head and item.remote_sha:
        incorporated = git.client.ok("merge-base", "--is-ancestor", item.remote_sha,
                                     item.pre_rebase_head, cwd=wt)
    unincorporated: tuple[str, ...] = ()
    if divergence.diverged and item.remote_sha:
        found = _unincorporated(item, wt)
        if found is None:
            # Fail closed: a comparison that cannot be made must not read as
            # "nothing to lose", so the refs are reported not comparable.
            divergence = RefDivergence()
        else:
            unincorporated = found
    return TreeState(local, remote, divergence, fetched, incorporated, unincorporated)


def _refuse(refusal: Refusal, detail: str, commits: list[str] | None = None) -> PublishPlan:
    return PublishPlan(refusal=refusal, detail=detail, commits=list(commits or []))


def plan(item: Item, pr_bin: str, tree: TreeState, *,
         confirmed: Sequence[str] = ()) -> PublishPlan:
    """The commands that publish *item* given *tree*, or the refusal that stops it.

    *confirmed* is the operator's ``force-publish``: the remote commits listed
    in the refusal they answered. It pushes past those and past nothing else —
    a remote commit that appeared since is refused again, with the full list.
    """
    wt = ["--repo-dir", item.worktree]
    if not tree.fetched:
        return _refuse(Refusal.FETCH_FAILED, f"could not fetch {item.branch}")
    if not item.remote_sha or tree.remote != item.remote_sha:
        return _refuse(Refusal.REMOTE_MOVED,
                       f"origin/{item.branch} is {git.client.abbrev(tree.remote) or 'gone'}; "
                       f"the batch planned from {git.client.abbrev(item.remote_sha)}")
    div = tree.divergence
    if not div.comparable:
        return _refuse(Refusal.NOT_COMPARABLE, f"cannot compare {item.branch} with origin")
    commands: list[list[str]] = []
    if div.diverged:
        if not tree.incorporated:
            return _refuse(Refusal.NOT_INCORPORATED,
                           f"the rebase started from {git.client.abbrev(item.pre_rebase_head)}, "
                           f"which does not contain {git.client.abbrev(item.remote_sha)}")
        if set(tree.unincorporated) - set(confirmed):
            return _refuse(Refusal.NOT_INCORPORATED_REMOTE,
                           f"{len(tree.unincorporated)} remote commit(s) have no equivalent "
                           f"in {item.branch}", list(tree.unincorporated))
        commands.append([pr_bin, "rebase", "--push-only", "--expect", item.remote_sha] + wt)
    elif div.ahead:
        commands.append([GIT_PUSH, item.worktree])
    pushes = bool(commands)
    if (item.has(Step.COMMENTS) and item.step(Step.COMMENTS).drafted) or item.track:
        track = [arg for t in item.track for arg in ("--track", t)]
        commands.append([pr_bin, "comments", "--finish", "--post", *track] + wt)
    return PublishPlan(commands=commands, pushes=pushes)
