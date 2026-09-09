"""The repo-specific half of regeneration — what *this* repo rebuilds, and how.

``git.regenerate`` owns the repo-agnostic half: which command rebuilds a
lockfile of a given kind. This module answers the question that needs a repo to
answer it — what a repo declares under ``rebase.regenerate``, or what its task
runner conventionally offers — which is why it sits at layer 6 with ``config``
in reach rather than beside the registry at layer 2.
"""

# doc-group: platform

from __future__ import annotations

import shlex
import shutil
from functools import cache
from pathlib import Path

from config import workbench_config
from core import timeouts
from git import client as git_client
from git import regenerate as regen

Regenerator = regen.Regenerator
RegenQueue = regen.RegenQueue

# The conventional task name for "rebuild everything this repo generates".
# Used when a repo declares no `rebase.regenerate`, which is most of them.
CONVENTIONAL_REGEN_TASK = "generate"


# The three reads below are cached for the run, not for the process: neither
# `.workbench.yml` nor a repo's task list changes under a rebase that is only
# replaying commits, and each is asked once per generated file. A caller that
# outlives one repo state — a test suite, in practice — clears them.
@cache
def mise_has_task(repo_root: str, task: str) -> bool:
    """Whether `mise tasks ls` in this repo lists a task by exactly this name.

    Asked of mise rather than parsed out of a config file: a task can come from
    a `[tasks.*]` table, a `tasks/` directory, or an included file, and only
    mise knows which of those this repo uses.
    """
    if not shutil.which("mise"):
        return False
    r = regen.try_run(
        ["mise", "tasks", "ls", "--no-header"],
        cwd=repo_root, timeout=timeouts.QUICK,
    )
    if r is None or r.returncode != 0:
        return False
    return any(line.split()[:1] == [task] for line in r.stdout.splitlines() if line.strip())


@cache
def repo_root(cwd: str) -> str:
    """The toplevel of the repo `cwd` sits in, falling back to `cwd` itself."""
    return git_client.out("rev-parse", "--show-toplevel", cwd=cwd) or cwd


@cache
def repo_regenerators(cwd: str) -> tuple[Regenerator, ...]:
    """The commands that rebuild this repo's generated files, in order.

    A declared `rebase.regenerate` wins outright — a repo whose generators are
    not all reachable from one task is the case the key exists for, and a
    convention cannot know that. Otherwise fall back to the conventional
    `generate` task when the repo actually has one.

    Empty means this repo has told us nothing and has no conventional task, so
    a generated file's rebuild is unknown. The caller reports it stale rather
    than guessing, because a wrong rebuild command silently commits wrong
    generated output.
    """
    root = repo_root(cwd)

    declared = workbench_config.load_config_or_default(root).rebase.regenerate
    if declared:
        return tuple(Regenerator(tuple(shlex.split(cmd))) for cmd in declared)

    if mise_has_task(root, CONVENTIONAL_REGEN_TASK):
        return (Regenerator(("mise", "run", CONVENTIONAL_REGEN_TASK)),)

    return ()


def queue_repo_regeneration(filepath: str, cwd: str, queue: RegenQueue) -> bool:
    """Queue this repo's regeneration for a generated file. False if unknown.

    Keyed on the repo root rather than the file's directory, so the dozen
    generated files one rebase touches collapse into a single run of each
    command — RegenQueue dedupes on (directory, command).
    """
    regenerators = repo_regenerators(cwd)
    if not regenerators:
        return False
    for regenerator in regenerators:
        queue.add(Path(repo_root(cwd)), filepath, regenerator.cmd, stage_dir=True)
    return True


def clear_caches() -> None:
    """Forget every cached repo read.

    One process only ever rebases one repo, so nothing in production calls
    this. A test session drives many, each in its own temporary directory, and
    a stale answer keyed on a reused path is the failure it exists to prevent.
    """
    mise_has_task.cache_clear()
    repo_root.cache_clear()
    repo_regenerators.cache_clear()
