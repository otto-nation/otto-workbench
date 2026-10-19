"""Which `pr` target each batch item is, so a run can hold their locks and hand each child its own.

A batch run holds each unfinished item's target lock until that item is
terminal (`done`, `dropped`, `skipped_closed`), so a manual `pr` command on one
of its PRs is refused instead of interleaving with the batch's own step. A
terminal item is one the scheduler never admits again, so `ItemHolds` releases
its lock while the run carries on, and the PR is free for manual work at once.
Its children must get through those locks, and each only through its own: a
child is started with `WORKBENCH_RUN_LOCK` naming its item's target and nothing
else, so a step for one PR cannot ride the batch's hold onto another. A child
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
from collections.abc import Iterable
from pathlib import Path

import core.run_lock
import pr.target
from batch.model import Item, Run

# The markers a child must not inherit from the batch: it holds every item's
# target, and no checkout or global lock a step could use.
_MARKERS = (core.run_lock.LOCK_ENV, core.run_lock.TREE_LOCK_ENV,
            core.run_lock.GLOBAL_LOCK_ENV)


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
    try:
        key = pr.target.repo_key_from_origin(item.repo_dir)
    except OSError:
        # A vanished or unreadable checkout counts as "no target", same as an
        # item with no origin.
        return None
    return pr.target.target_dir(key, item.branch) if key else None


def child_env(item: Item) -> dict[str, str]:
    """The environment for a child working on *item* alone.

    This process's environment with every run-lock marker stripped. The target
    marker is then set to *item*'s target only when this process holds that
    target's lock, so the child passes through a lock we actually hold and
    takes its own locks otherwise — including an idle resolve, which holds
    nothing.
    """
    env = {k: v for k, v in os.environ.items() if k not in _MARKERS}
    target = item_target_dir(item)
    if target is not None and core.run_lock.holds(target / core.run_lock.LOCK_FILE):
        env[core.run_lock.LOCK_ENV] = str(target)
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

    def __init__(self) -> None:
        self._stacks: dict[Path, contextlib.ExitStack] = {}
        self._keys: dict[Path, tuple[str, ...]] = {}

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
        self._stacks.pop(target).close()
