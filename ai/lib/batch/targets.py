"""Which `pr` target each batch item is, so a run can hold their locks and hand each child its own.

A batch run holds every item's target lock for as long as it runs, so a manual
`pr` command on one of its PRs is refused instead of interleaving with the
batch's own step. Its children must get through those locks, and each only
through its own: a child is started with `WORKBENCH_RUN_LOCK` naming its item's
target and nothing else, so a step for one PR cannot ride the batch's hold onto
another.

The target is computed the way `pr.context` keys a run — the repo key read off
the checkout's `origin`, and the item's branch — so the lock the batch holds is
the one its child resolves. An item whose checkout has no `origin` has no
target: nothing can hold one for it, and its steps fail to resolve on their own.
"""

# doc-group: batch

from __future__ import annotations

import os
from pathlib import Path

import core.run_lock
import pr.target
from batch.model import Item

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
