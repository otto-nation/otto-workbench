"""What a batch publish pushes, read from the tree, and the lease it pushes under.

Publish is the only thing in a batch run that reaches the remote. It fetches the
one branch, compares the local branch with origin's, and checks the remote is
still the head the batch planned from (`Item.remote_sha`):

| Tree | Command |
|---|---|
| the worktree stands on another branch than the item's | refuse: `wrong_branch` |
| the fetch failed | refuse: `fetch_failed` |
| the fix commit failed, or the worktree has staged changes | refuse: `unlanded_fixes` |
| a commit a step drafted is not on the branch, as itself, a patch-equivalent, or a replay `git.replay` can follow | refuse: `drafted_dropped` |
| origin is not `remote_sha` (somebody pushed) | refuse: `remote_moved` |
| refs not comparable | refuse: `not_comparable` |
| local == origin, or only behind | nothing to push |
| local is a fast-forward of origin | `pr push --expect <remote_sha>` |
| diverged, from a batch rebase whose start holds neither `remote_sha` nor a patch-equivalent of each of its commits | refuse: `not_incorporated` |
| diverged, a remote commit has no patch-equivalent locally | refuse: `not_incorporated_remote` |
| diverged | `pr rebase --push-only --expect <remote_sha>` |

Both pushes are `pr` commands leasing on `remote_sha`, so each writes its own
report into the publish log and a remote that moved after the fetch is still
refused.

`pr comments --finish --post` follows when the comments step drafted, an item is
tracked, or the PR's saved `pr` state says a closeout is owed
(`batch.plan.closeout_debt`). The last case applies even when there is nothing
to push. The command is `CloseoutDebt.command`, so an owed tracking issue adds
`--track-all`. A refusal is a `failed` decision on step `publish` carrying `reason`;
a `not_incorporated_remote` one also lists the remote commits, and the operator
answers it with `force-publish` (push past exactly those commits; one that
appeared since is refused again) or drops the PR. A `drafted_dropped` one lists
the drafted commits the branch no longer carries — a hand `reset --hard`, a
rebase that dropped one, a hand push without it — and is answered by
cherry-picking them back and retrying, or by `force-publish` past exactly that
list. A commit whose replay git could not determine is listed too, marked
unverifiable: the gate fails closed. Confirmations accumulate on the item
(`Item.forced`), so confirming one refusal never reopens the other.

A `remote_moved` retry adopts the remote as the lease when it is a fast-forward
of the old lease that the local branch already contains (`remote_fast_forwarded`)
— the push that follows is a fast-forward and loses nothing.

A branch the operator rebased by hand with `--fork-point` reaches the
`not_incorporated_remote` refusal too: the commits the fork point skipped are
the ones listed, and the rebase's start tip stays the one the batch saw before
the hand rebase (`batch.resolve`, `force`), so `not_incorporated` does not fire
for it. The lease advances as soon as
the push lands, so a failure in the replies after it never strands the item.
"""

# doc-group: batch

from __future__ import annotations

import shlex
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import batch.steps
import git.client
import git.replay
import rebase.target
from batch.model import Item, Step
from pr.comments_fix import CloseoutDebt
from rebase.types import RefDivergence


class Refusal(StrEnum):
    REMOTE_MOVED = "remote_moved"
    NOT_COMPARABLE = "not_comparable"
    NOT_INCORPORATED = "not_incorporated"
    NOT_INCORPORATED_REMOTE = "not_incorporated_remote"
    FETCH_FAILED = "fetch_failed"
    UNLANDED_FIXES = "unlanded_fixes"
    DRAFTED_DROPPED = "drafted_dropped"
    # Also the reason the scheduler records when a step would start on a
    # worktree standing on another branch: one value for one condition.
    WRONG_BRANCH = "wrong_branch"


@dataclass(frozen=True)
class TreeState:
    """The local branch against origin's, read after a fetch of that branch.

    ``incorporated`` is False only when a batch rebase started from a tip that
    holds neither the planned remote head nor a patch-equivalent of each remote
    commit, so force-pushing its result would drop remote commits the replay
    never saw. A start tip an earlier, unpushed rebase rewrote holds the remote's
    commits as equivalents rather than ancestors, and counts as incorporated.
    ``unincorporated`` names the remote commits (``"<sha> <subject>"``) with no
    patch-equivalent in the local branch — what a force-push of a diverged
    branch would throw away. ``dropped`` names the commits a step drafted that
    the local branch no longer carries (``"<sha> <subject>"``, suffixed
    ``(unverifiable)`` where git could not say) — what publishing the branch as
    it stands would lose.
    """

    local: str
    remote: str
    divergence: RefDivergence
    fetched: bool = True
    incorporated: bool = True
    unincorporated: tuple[str, ...] = ()
    dropped: tuple[str, ...] = ()
    # The branch the worktree stands on (`batch.steps.checked_out_branch`),
    # "" when git could not say. None only in a tree built without reading
    # one — a test's — and then not checked.
    checked_out: str | None = None


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


def _cherry_plus(cwd: str, upstream: str, head: str, limit: str = "",
                 *, verbose: bool = False) -> tuple[str, ...] | None:
    """Commits on *head* with no patch-equivalent on *upstream*; None when unreadable."""
    args = (("-v",) if verbose else ()) + (upstream, head) + ((limit,) if limit else ())
    r = git.client.run("cherry", *args, cwd=cwd)
    if not r.ok:
        return None
    return tuple(line[2:] for line in r.stdout.splitlines() if line.startswith("+ "))


def _unincorporated(item: Item, cwd: str, upstream: str = "") -> tuple[str, ...] | None:
    """Remote commits since the base with no patch-equivalent in *upstream*; None when unreadable.

    *upstream* defaults to the local branch.
    """
    limit = git.client.out("merge-base", f"refs/remotes/origin/{item.base_ref}",
                           item.remote_sha, cwd=cwd) if item.base_ref else ""
    return _cherry_plus(cwd, upstream or f"refs/heads/{item.branch}", item.remote_sha,
                        limit, verbose=True)


def _start_incorporated(item: Item, cwd: str) -> bool:
    """Whether the batch rebase's start tip held every commit of the planned remote head."""
    if git.client.ok("merge-base", "--is-ancestor", item.remote_sha, item.pre_rebase_head, cwd=cwd):
        return True
    # An unreadable comparison yields None, which is not (), so it fails closed.
    return _unincorporated(item, cwd, item.pre_rebase_head) == ()


def drafted_commits(item: Item) -> list[str]:
    """Every commit *item*'s steps recorded, in step order, each once."""
    return list(dict.fromkeys(sha for rec in item.steps for sha in rec.commits))


class _Carried(StrEnum):
    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


def _carried(wt: str, branch: str, sha: str, finder: git.replay.ReplayFinder) -> _Carried:
    """Whether *branch* still carries *sha*; UNKNOWN when git could not say.

    On the branch as itself, or as a patch-equivalent (`git cherry` against
    *sha* alone), or as the replay `git.replay` follows through git's rewrite
    record — a conflict resolution or a squash keeps the work under a
    different patch. An ambiguous replay counts as carried: two commits holding
    the change is not the change lost.
    """
    ref = f"refs/heads/{branch}"
    if git.client.ok("merge-base", "--is-ancestor", sha, ref, cwd=wt):
        return _Carried.YES
    if _cherry_plus(wt, ref, sha, f"{sha}^") == ():
        return _Carried.YES
    replay = finder.find(sha)
    if replay.status is git.replay.ReplayStatus.NONE:
        return _Carried.NO
    if replay.status is git.replay.ReplayStatus.UNKNOWN:
        return _Carried.UNKNOWN
    return _Carried.YES


# Appended to the label of a drafted commit whose replay git could not determine.
_UNVERIFIABLE = " (unverifiable)"

_DROPPED_CACHE: dict[tuple[str, str, str, tuple[str, ...]], tuple[str, ...]] = {}


def _dropped(item: Item, wt: str) -> tuple[str, ...]:
    """The drafted commits *item*'s branch no longer carries, labelled for the refusal.

    One publish reads the tree several times; the answer depends only on the
    branch tip and the drafted list, so it is computed once per pair. An answer
    holding an unverifiable commit is not kept: git declining once — a timeout,
    a locked object — must not refuse that tip for the rest of the driver's life.

    ceiling: the cache lives for the process and is never evicted. Entries are
    a few labels per item per tip a publish read. Upgrade trigger: if a
    long-lived driver ever reads enough distinct tips for this to show in its
    RSS, key it by run and drop a run's entries when the run finishes.
    """
    drafted = tuple(drafted_commits(item))
    if not drafted:
        return ()
    tip = git.client.out("rev-parse", "--verify", "--quiet", f"refs/heads/{item.branch}", cwd=wt)
    key = (wt, item.branch, tip, drafted)
    if tip and key in _DROPPED_CACHE:
        return _DROPPED_CACHE[key]
    result = _compute_dropped(item, wt, drafted)
    if tip and not any(label.endswith(_UNVERIFIABLE) for label in result):
        _DROPPED_CACHE[key] = result
    return result


def _compute_dropped(item: Item, wt: str, drafted: Sequence[str]) -> tuple[str, ...]:
    finder = git.replay.ReplayFinder(Path(wt))
    dropped: list[str] = []
    for sha in drafted:
        carried = _carried(wt, item.branch, sha, finder)
        if carried is _Carried.YES:
            continue
        subject = git.client.out("log", "-1", "--format=%s", sha, cwd=wt)
        label = f"{git.client.abbrev(sha)} {subject}".rstrip()
        dropped.append(label if carried is _Carried.NO else f"{label}{_UNVERIFIABLE}")
    return tuple(dropped)


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
        incorporated = _start_incorporated(item, wt)
    unincorporated: tuple[str, ...] = ()
    if divergence.diverged and item.remote_sha:
        found = _unincorporated(item, wt)
        if found is None:
            # Fail closed: a comparison that cannot be made must not read as
            # "nothing to lose", so the refs are reported not comparable.
            divergence = RefDivergence()
        else:
            unincorporated = found
    dropped = _dropped(item, wt) if item.worktree else ()
    return TreeState(local, remote, divergence, fetched, incorporated, unincorporated,
                     dropped, batch.steps.checked_out_branch(wt))


def _has_staged(wt: str) -> bool:
    """Whether *wt* has staged changes. An unreadable tree is not staged."""
    if not wt or not Path(wt).is_dir():
        return False
    return git.client.run("diff", "--cached", "--quiet", cwd=wt).returncode == 1


def _tracked_clean(wt: str) -> bool:
    """Whether *wt* has no staged or unstaged changes to tracked files.

    Untracked files are allowed. Unreadable is not clean.
    """
    return (git.client.run("diff", "--quiet", cwd=wt).ok
            and git.client.run("diff", "--cached", "--quiet", cwd=wt).ok)


def try_adopt_equivalent_remote(item: Item, tree: TreeState) -> bool:
    """`git reset --keep` *tree.remote* when every local commit is already on it.

    Tracked files must be clean; untracked files are allowed. Fails closed on a
    missing worktree, an unreadable comparison, any unique local commit, or a
    reset that does not land. Does not write the lease — the caller does.
    """
    if not tree.fetched or not tree.remote or tree.remote == item.remote_sha:
        return False
    wt = item.worktree
    if not wt or not Path(wt).is_dir() or not _tracked_clean(wt):
        return False
    if _cherry_plus(wt, tree.remote, "HEAD") != ():
        return False
    return git.client.run("reset", "--keep", tree.remote, cwd=wt).ok


def remote_fast_forwarded(item: Item, tree: TreeState) -> bool:
    """Whether the remote moved forward from the lease to a commit the local branch holds.

    ``lease ⊑ remote ⊑ local``: pushing the local branch over that remote is a
    fast-forward, so nothing anyone pushed is lost. Fails closed on a fetch that
    failed, a missing ref, or an ancestry git will not confirm. Does not write
    the lease — the caller does.
    """
    if not (tree.fetched and tree.remote and tree.local and item.remote_sha):
        return False
    if tree.remote == item.remote_sha or not item.worktree:
        return False
    wt = item.worktree
    return (git.client.ok("merge-base", "--is-ancestor", item.remote_sha, tree.remote, cwd=wt)
            and git.client.ok("merge-base", "--is-ancestor", tree.remote, tree.local, cwd=wt))


def _refuse(refusal: Refusal, detail: str, commits: list[str] | None = None) -> PublishPlan:
    return PublishPlan(refusal=refusal, detail=detail, commits=list(commits or []))


def _comments_command(item: Item, pr_bin: str, closeout: CloseoutDebt,
                      *, no_verify: bool = False) -> list[str]:
    """`pr comments --finish --post` as `pr` itself spells the closeout, plus the tracked ids.

    `CloseoutDebt.command` is the one statement of the flags; it adds
    `--track-all` when a tracking issue is owed, and that overrides any
    `--track` ids, so they are left off rather than passed and ignored.
    `--no-push-verify` is the git-hook skip, not `--no-verify` (the fix
    pass's verify gate).
    """
    _, *flags = shlex.split(closeout.command)
    if "--track-all" not in flags:
        flags += [arg for t in item.track for arg in ("--track", t)]
    if no_verify:
        flags.append("--no-push-verify")
    return [pr_bin, *flags]


def plan(item: Item, pr_bin: str, tree: TreeState, *,
         confirmed: Sequence[str] = (), closeout: CloseoutDebt = CloseoutDebt(),
         no_verify: bool = False) -> PublishPlan:
    """The commands that publish *item* given *tree*, or the refusal that stops it.

    *confirmed* is the operator's ``force-publish``: the commits listed in the
    refusals they answered, remote or drafted. It pushes past those and past
    nothing else — a commit that appeared since is refused again, with the full
    list.
    *closeout* is what `pr` recorded as owed to the PR; when it is owed the
    comments command runs even with nothing drafted or to push.
    """
    wt = ["--repo-dir", item.worktree]
    nv = ["--no-verify"] if no_verify else []
    if tree.checked_out is not None and tree.checked_out != item.branch:
        return _refuse(Refusal.WRONG_BRANCH,
                       f"{item.worktree} is on {tree.checked_out or 'no branch git could name'}, "
                       f"not {item.branch}")
    if not tree.fetched:
        return _refuse(Refusal.FETCH_FAILED, f"could not fetch {item.branch}")
    if not item.remote_sha or tree.remote != item.remote_sha:
        return _refuse(Refusal.REMOTE_MOVED,
                       f"origin/{item.branch} is {git.client.abbrev(tree.remote) or 'gone'}; "
                       f"the batch planned from {git.client.abbrev(item.remote_sha)}")
    div = tree.divergence
    if not div.comparable:
        return _refuse(Refusal.NOT_COMPARABLE, f"cannot compare {item.branch} with origin")
    if closeout.unlanded or _has_staged(item.worktree):
        return _refuse(
            Refusal.UNLANDED_FIXES,
            "the fix commit was refused, or the worktree still holds staged fixes",
        )
    if set(tree.dropped) - set(confirmed):
        return _refuse(Refusal.DRAFTED_DROPPED,
                       f"{len(tree.dropped)} commit(s) the batch drafted are not on "
                       f"{item.branch}", list(tree.dropped))
    commands: list[list[str]] = []
    if div.diverged:
        if not tree.incorporated:
            return _refuse(Refusal.NOT_INCORPORATED,
                           f"the rebase started from {git.client.abbrev(item.pre_rebase_head)}, "
                           f"which lacks {git.client.abbrev(item.remote_sha)}")
        if set(tree.unincorporated) - set(confirmed):
            return _refuse(Refusal.NOT_INCORPORATED_REMOTE,
                           f"{len(tree.unincorporated)} remote commit(s) have no equivalent "
                           f"in {item.branch}", list(tree.unincorporated))
        commands.append(
            [pr_bin, "rebase", "--push-only", "--expect", item.remote_sha] + nv + wt)
    elif div.ahead:
        commands.append([pr_bin, "push", "--expect", item.remote_sha] + nv + wt)
    pushes = bool(commands)
    drafted = item.has(Step.COMMENTS) and item.step(Step.COMMENTS).drafted
    if drafted or item.track or closeout.owed:
        commands.append(_comments_command(item, pr_bin, closeout, no_verify=no_verify) + wt)
    return PublishPlan(commands=commands, pushes=pushes)
