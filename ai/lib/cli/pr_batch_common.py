"""Shared helpers for the `pr batch` CLI modules.

A leaf: imported by `cli.pr_batch`, `cli.pr_batch_requests`, and
`cli.pr_batch_views`. Imports none of them.
"""

# doc-group: cli

from __future__ import annotations

import os
import sys

import batch.store
import pr.target
from batch.store import Role


def err(message: str) -> int:
    print(f"pr batch: {message}", file=sys.stderr)
    return 1


def cwd_repo() -> batch.store.RepoRef | None:
    identity = pr.target.repo_identity_from_origin(os.getcwd())
    if identity is None:
        return None
    return batch.store.RepoRef(identity.key, identity.label)


def warn_other_active(chosen: str) -> None:
    for run_id in batch.store.run_ids():
        if run_id != chosen and is_active(run_id):
            print(f"pr batch: other active run {run_id} is being driven", file=sys.stderr)


def warn_foreign_run(cwd: batch.store.RepoRef | None, chosen: str) -> None:
    if cwd is None:
        print(f"pr batch: showing {chosen}, the newest run on this machine",
              file=sys.stderr)
        return
    print(f"pr batch: no run covers {cwd.label}; showing {chosen}, "
          f"the newest run on this machine", file=sys.stderr)


def resolve_run_id(given: str | None, *, covering_only: bool = False) -> str | None:
    """*given*, or the covering run for this checkout; names other live drivers on stderr."""
    if given:
        return given
    cwd = cwd_repo()
    if covering_only:
        chosen = batch.store.covering_run_id(cwd)
    else:
        chosen = batch.store.default_run_id(cwd)
        if chosen and batch.store.covering_run_id(cwd) != chosen:
            warn_foreign_run(cwd, chosen)
    if chosen:
        warn_other_active(chosen)
    return chosen


def is_active(run_id: str) -> bool:
    """Whether a scheduler is driving *run_id*: the live holder of its lock is a driver.

    An applier's brief hold is not a scheduler. A record with no role reads as
    a driver, which is what a held run lock meant before roles existed.
    """
    found = batch.store.holder(run_id)
    return found is not None and found.role in (Role.DRIVER, None)
