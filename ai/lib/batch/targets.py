"""Which `pr` target each batch item is, so a run can hold their locks and hand each child its own.

A batch run holds each unfinished item's target lock until that item is
terminal (`done`, `dropped`, `skipped_closed`), so a manual `pr` command on one
of its PRs is refused instead of interleaving with the batch's own step. Once
an item's worktree is known — assigned, or recorded by an earlier run being
resumed — the run holds that checkout's lock too, on the same stack, so it is
released with the target: a `pr` command run in the worktree on another branch
is refused as well, and the driver's own git writes there (restore, fetch,
reset) are covered by a lock rather than by luck.
A terminal item is one the scheduler never admits again, so `ItemHolds`
releases its lock while the run carries on, and the PR is free for manual work
at once.
Its children must get through those locks, and each only through its own: a
child is started with `WORKBENCH_RUN_LOCK` naming its item's target and
`WORKBENCH_RUN_LOCK_TREE` naming its item's checkout, and nothing else, so a
step for one PR cannot ride the batch's hold onto another. A child
started for an item whose lock was released takes its own.

The target is computed the way `pr.context` keys a run — the repo key read off
the checkout's `origin`, and the item's branch — so the lock the batch holds is
the one its child resolves. An item whose checkout has no `origin` has no
target: nothing can hold one for it, and its steps fail to resolve on their own.
"""

# doc-group: batch

from __future__ import annotations

import contextlib
import os
from collections.abc import Callable, Iterable
from pathlib import Path

import core.run_lock
import git.client
import pr.target
from batch.model import Item, Run

# The markers a child must not inherit from the batch: it holds every item's
# target, and no checkout or global lock a step could use.
_MARKERS = (core.run_lock.LOCK_ENV, core.run_lock.TREE_LOCK_ENV,
            core.run_lock.GLOBAL_LOCK_ENV)


def origin_key(directory: str) -> str:
    """The origin key of the checkout at *directory*, or "" when it has no
    `origin`, or is vanished or unreadable.

    The one place a batch reads a checkout's key: planning records it, the
    lock declaration and the driver read it back, and all of them must agree.
    """
    try:
        return pr.target.repo_key_from_origin(directory) or ""
    except OSError:
        return ""


def item_repo_key(item: Item) -> str | None:
    """*item*'s origin key, recorded at plan time, or read from its checkout.

    None when the item was saved without a key and the checkout has no
    `origin`, or is vanished or unreadable.
    """
    return item.repo_key or origin_key(item.repo_dir) or None


def item_target_dir(item: Item) -> Path | None:
    """*item*'s target directory, or None when its checkout has no `origin` to
    key it on, or is vanished or unreadable.
    """
    # Keyed on item.repo_dir, not item.worktree: the batch holds item targets
    # before worktrees exist. A worktree shares origin and branch with that
    # checkout, so the key matches what the child resolves.
    # ceiling: a checkout-local .workbench.yml github.host the worktree does
    # not inherit would hash two keys for one item; upgrade if a batch item
    # can declare a host on the worktree that its repo_dir does not.
    key = item_repo_key(item)
    return pr.target.target_dir(key, item.branch) if key else None


def run_repo_keys(run: Run) -> list[str]:
    """Sorted, deduplicated origin keys of every item in *run*; skips unknown."""
    keys = []
    for item in run.items:
        key = item_repo_key(item)
        if key:
            keys.append(key)
    return sorted(set(keys))


def checkout_git_dir(worktree: str) -> Path | None:
    """The private git dir *worktree*'s checkout lock is keyed on, or None.

    Asked the way `core.run_lock` asks — `--absolute-git-dir`, with `GIT_DIR`
    and `GIT_WORK_TREE` stripped — so the marker a child is handed matches the
    lock it resolves.
    """
    if not worktree or not Path(worktree).is_dir():
        return None
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}
    r = git.client.run("rev-parse", "--absolute-git-dir", cwd=worktree, env=env)
    path = r.stdout.strip() if r.ok else ""
    return Path(path) if path else None


def child_env(item: Item) -> dict[str, str]:
    """The environment for a child working on *item* alone.

    This process's environment with every run-lock marker stripped. The target
    and checkout markers are then set to *item*'s target and worktree only when
    this process holds those locks, so the child passes through locks we
    actually hold and takes its own otherwise — including an idle resolve,
    which holds nothing.
    """
    env = {k: v for k, v in os.environ.items() if k not in _MARKERS}
    target = item_target_dir(item)
    if target is not None and core.run_lock.holds(target / core.run_lock.LOCK_FILE):
        env[core.run_lock.LOCK_ENV] = str(target)
    git_dir = checkout_git_dir(item.worktree)
    if git_dir is not None and core.run_lock.holds(git_dir / core.run_lock.TREE_LOCK_FILE):
        env[core.run_lock.TREE_LOCK_ENV] = str(git_dir)
    return env


class ItemHolds:
    """The target locks a batch driver holds for its items, each released on its own.

    `take` enters one target's `core.run_lock.hold`, which the caller opens
    (`cli.pr_batch`, the one place `bin/local/validate-run-locks` lets a batch
    take a lock), on a stack of its own, so `release_finished` can return it
    without disturbing the others.
    A release is that hold's ordinary exit: the record is stamped `released`
    before the flock is dropped, and the registry and `WORKBENCH_RUN_LOCK` are
    re-synced. Used as a context manager, every hold still open is released
    when the block ends, newest first.
    """

    def __init__(self, checkout: Callable[[str], contextlib.AbstractContextManager]
                 | None = None) -> None:
        self._stacks: dict[Path, contextlib.ExitStack] = {}
        self._keys: dict[Path, tuple[str, ...]] = {}
        self._trees: dict[Path, set[str]] = {}
        # Opens the `core.run_lock.hold` on one worktree's checkout; supplied
        # by `cli.pr_batch`, which is where a batch may take a lock. Without
        # one (a test, a dry tick) no checkout is held.
        self._checkout = checkout

    def __enter__(self) -> ItemHolds:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def take(self, target: Path, keys: Iterable[str],
             hold: contextlib.AbstractContextManager) -> None:
        """Enter *hold*, the `core.run_lock.hold` on *target*, for the items *keys*.

        Raises `core.run_lock.LockBusy` when the target is held elsewhere.
        Nothing is recorded on a refusal, so a refused take owes no release.
        """
        stack = contextlib.ExitStack()
        stack.enter_context(hold)
        self._stacks[target] = stack
        self._keys[target] = tuple(keys)

    def hold_checkout(self, key: str, worktree: str) -> None:
        """Hold *worktree*'s checkout lock with item *key*'s target, released with it.

        A no-op when the item's target is not held here (no `origin`, or
        already released), when this worktree is already held, or when no
        checkout opener was supplied. Raises `core.run_lock.LockBusy` when
        another process holds the checkout; nothing is recorded then.
        """
        target = next((t for t, keys in self._keys.items() if key in keys), None)
        if target is None or self._checkout is None or not worktree:
            return
        trees = self._trees.setdefault(target, set())
        if worktree in trees:
            return
        self._stacks[target].enter_context(self._checkout(worktree))
        trees.add(worktree)

    def held(self) -> list[Path]:
        """The targets still held, in the order they were taken."""
        return list(self._stacks)

    def release_finished(self, run: Run) -> list[Path]:
        """Release every held target whose items are all terminal in *run*; return those released.

        A target shared by several items stays held until the last of them is
        terminal. Call it only after the change that made an item terminal is
        committed, so a crash cannot leave a run on disk still owning an item
        whose lock was given away. Raises `KeyError` for a held key *run* lacks.
        """
        done = [t for t, keys in self._keys.items()
                if all(run.item(k).terminal for k in keys)]
        for target in done:
            self._release(target)
        return done

    def close(self) -> None:
        """Release every target still held, newest first."""
        for target in reversed(list(self._stacks)):
            self._release(target)

    def _release(self, target: Path) -> None:
        del self._keys[target]
        self._trees.pop(target, None)
        self._stacks.pop(target).close()
