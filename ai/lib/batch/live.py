"""What GitHub says about a run's repos now: the `github` section of `pr batch status`.

A run's state file holds what this machine saw when it last drove the run. A
PR opened, pushed, merged or closed since then, from another machine or by
hand, does not show in it. This module re-reads the viewer's open PRs in every
repo the run covers, using the search `pr batch plan` makes, and compares them
with the run's items.

| Key | Content |
|---|---|
| `checked` | true when GitHub answered. When false, `error` says why, or `skipped` is true under `--offline` |
| `viewer` | the login the search ran as |
| `repos` | the repos searched |
| `unchecked` | repos the run covers with no checkout found here, so not searched |
| `open` | how many open PRs the search found |
| `untracked` | open PRs the run has no item for: `pr`, `title`, `branch`, `head_sha` |
| `moved` | items whose GitHub head differs from the one the run last saw: `pr`, `run_sha`, `head_sha` |
| `closed` | keys of items no longer open on GitHub (merged or closed) |
| `owed` | open PRs with a step needed: `pr`, `needs` (step → reason) |

The needs are `pr batch plan`'s, fetch included: GitHub's merge state reads
CLEAN for a branch that is only behind, so without the fetch the rebase need
would be understated. The plan's namespaced refs are dropped before returning.

A checkout is found by the item's `repo_dir` first, then the current
directory. Either one counts only when its `origin` names the item's repo,
because a relative `repo_dir` saved by an older run resolves against whatever
directory the reader happens to be in.
"""

# doc-group: batch

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import batch.plan
import pr.target
from batch.model import ItemStatus, Run

PlanFn = Callable[[list[str]], batch.plan.Plan]


def skipped() -> dict:
    """The section when `--offline` asked for no GitHub read."""
    return {"checked": False, "skipped": True}


def _origin_repo(repo_dir: str) -> str:
    """*repo_dir*'s repo per its `origin`, case-folded, or "" when it names none."""
    if not Path(repo_dir).is_dir():
        return ""
    identity = pr.target.repo_identity_from_origin(repo_dir)
    return identity.label.casefold() if identity else ""


def _checkout_of(item, cwd: str) -> str:
    """A checkout of *item*'s repo: its `repo_dir`, else *cwd*, else ""."""
    candidates = (os.path.abspath(os.path.join(cwd, item.repo_dir)), cwd)
    return next((c for c in candidates if _origin_repo(c) == item.repo.casefold()), "")


def checkouts(run: Run, cwd: str) -> dict[str, str]:
    """Repo slug → a checkout of it here, for every repo *run* covers that has one."""
    found: dict[str, str] = {}
    for item in run.items:
        if item.repo not in found and (where := _checkout_of(item, cwd)):
            found[item.repo] = where
    return found


def _last_seen(item) -> str:
    """The head the run last knew GitHub to have: what it pushed, else what it planned from."""
    return item.published_sha or item.remote_sha


def compare(run: Run, plan: batch.plan.Plan, repos: set[str]) -> dict:
    """The section's comparison of *run*'s items in *repos* with *plan*'s open PRs.

    Items in a repo that was not searched are left out: absent from the search
    is not the same as closed.
    """
    items = {i.key: i for i in run.items if i.repo in repos}
    rows = {r.key: r for r in plan.rows}
    return {
        "open": len(rows),
        "untracked": [{"pr": k, "title": r.title, "branch": r.branch, "head_sha": r.head_sha}
                      for k, r in rows.items() if k not in items],
        "moved": [{"pr": k, "run_sha": _last_seen(items[k]), "head_sha": r.head_sha}
                  for k, r in rows.items() if k in items and r.head_sha != _last_seen(items[k])],
        "closed": [k for k, i in items.items()
                   if k not in rows and i.status is not ItemStatus.SKIPPED_CLOSED],
        "owed": [{"pr": k, "needs": {s.value: n.reason for s, n in r.needs.items() if n.needed}}
                 for k, r in rows.items() if any(n.needed for n in r.needs.values())],
    }


def view(run: Run, *, cwd: str, plan_fn: PlanFn | None = None) -> dict:
    """The `github` section for *run*. A GitHub failure is reported in the section, not raised."""
    found = checkouts(run, cwd)
    covered = sorted({i.repo for i in run.items})
    head = {"repos": sorted(found), "unchecked": [r for r in covered if r not in found]}
    if not found:
        return {"checked": False, **head,
                "error": "no checkout of the run's repos found here; "
                         "run from one, or use `pr batch plan --checkout DIR`"}
    try:
        plan = (plan_fn or batch.plan.build_plan)(sorted(found.values()))
    except batch.plan.PlanError as exc:
        return {"checked": False, **head, "error": str(exc)}
    try:
        return {"checked": True, "viewer": plan.viewer, **head,
                **compare(run, plan, set(found))}
    finally:
        # Nothing runs from a status read, so its refs go now, as `pr batch plan`'s do.
        batch.plan.drop_refs(plan.ref_dirs, plan.ref_namespace)


def owes(section: dict | None) -> bool:
    """Whether *section* shows work the run's own report would not: owed, new, or moved PRs."""
    if not section or not section.get("checked"):
        return False
    return bool(section["owed"] or section["untracked"] or section["moved"])
