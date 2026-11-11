"""The repo-specific half of regeneration — what *this* repo rebuilds, and how.

``git.regenerate`` owns the repo-agnostic half: which command rebuilds a
lockfile of a given kind. This module answers the question that needs a repo to
answer it — what a repo declares under ``rebase.regenerate`` and
``rebase.regenerate_paths``, or what its task runner conventionally offers —
which is why it sits at layer 6 with ``config`` in reach rather than beside
the registry at layer 2. An uncovered generated file is reported stale rather
than queued, so a command that does not produce that file cannot silently
keep the branch's copy.
"""

# doc-group: platform

from __future__ import annotations

import fnmatch
import shlex
import shutil
from functools import cache
from pathlib import Path

import config.workbench_config
import core.timeouts
import git.client
import git.regenerate

Regenerator = git.regenerate.Regenerator
RegenQueue = git.regenerate.RegenQueue

# The conventional task name for "rebuild everything this repo generates".
# Used when a repo declares no `rebase.regenerate`, which is most of them.
CONVENTIONAL_REGEN_TASK = "generate"


# The cached reads below last for the run, not the process: neither
# `.workbench.yml` nor a repo's task list changes under a rebase that is only
# replaying commits, and each is asked once per generated file. A caller that
# outlives one repo state — a test suite, in practice — clears them.
@cache
def mise_has_task(root: str, task: str) -> bool:
    """Whether `mise tasks ls` in this repo lists a task by exactly this name.

    Asked of mise rather than parsed out of a config file: a task can come from
    a `[tasks.*]` table, a `tasks/` directory, or an included file, and only
    mise knows which of those this repo uses.
    """
    if not shutil.which("mise"):
        return False
    r = git.regenerate.try_run(
        ["mise", "tasks", "ls", "--no-header"],
        cwd=root, timeout=core.timeouts.QUICK,
    )
    if r is None or r.returncode != 0:
        return False
    return any(line.split()[:1] == [task] for line in r.stdout.splitlines() if line.strip())


@cache
def repo_root(cwd: str) -> str:
    """The toplevel of the repo `cwd` sits in, falling back to `cwd` itself."""
    return git.client.out("rev-parse", "--show-toplevel", cwd=cwd) or cwd


@cache
def repo_regenerators(cwd: str, *, conventional: bool = True) -> tuple[Regenerator, ...]:
    """The commands that rebuild this repo's generated files, in order.

    A declared `rebase.regenerate` wins outright — a repo whose generators are
    not all reachable from one task is the case the key exists for, and a
    convention cannot know that. Otherwise fall back to the conventional
    `generate` task when the repo actually has one, unless ``conventional`` is
    false: default-mode conflict handling requires an explicit declaration so
    ``mise run generate`` stays ``--fix``-only.

    Empty means this repo has told us nothing and has no conventional task, so
    a generated file's rebuild is unknown. The caller reports it stale rather
    than guessing, because a wrong rebuild command silently commits wrong
    generated output.
    """
    root = repo_root(cwd)

    declared = config.workbench_config.load_config_or_default(root).rebase.regenerate
    if declared:
        return tuple(Regenerator(tuple(shlex.split(cmd))) for cmd in declared)

    if conventional and mise_has_task(root, CONVENTIONAL_REGEN_TASK):
        return (Regenerator(("mise", "run", CONVENTIONAL_REGEN_TASK)),)

    return ()


@cache
def regenerate_paths(cwd: str) -> tuple[str, ...]:
    """Globs of generated files ``rebase.regenerate`` actually rebuilds.

    Empty means every generated file. A non-empty list that matches nothing
    for a given path is how that file is reported stale without queuing.
    """
    return tuple(
        config.workbench_config.load_config_or_default(
            repo_root(cwd),
        ).rebase.regenerate_paths
    )


def queue_repo_regeneration(
    filepath: str, cwd: str, queue: RegenQueue, *, conventional: bool = True,
) -> bool:
    """Queue this repo's regeneration for a generated file. False if unknown.

    Keyed on the repo root rather than the file's directory, so the dozen
    generated files one rebase touches collapse into a single run of each
    command — RegenQueue dedupes on (directory, command).

    Returns False — so the caller reports the file stale — when no regenerator
    exists, or when ``rebase.regenerate_paths`` is non-empty and no glob
    matches ``filepath``. The match is ``fnmatch.fnmatchcase`` against the
    repo-relative path; empty paths cover everything, including the
    conventional fallback.
    """
    regenerators = repo_regenerators(cwd, conventional=conventional)
    if not regenerators:
        return False
    patterns = regenerate_paths(cwd)
    if patterns and not any(
        fnmatch.fnmatchcase(filepath, pattern) for pattern in patterns
    ):
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
    regenerate_paths.cache_clear()
