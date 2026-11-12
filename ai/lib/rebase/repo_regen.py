"""The repo-specific half of regeneration — what *this* repo rebuilds, and how.

``git.regenerate`` owns the repo-agnostic half: which command rebuilds a
lockfile of a given kind. This module answers the question that needs a repo to
answer it — the ``regenerate=<command>`` gitattributes attribute, what a repo
declares under ``rebase.regenerate``, or what its task runner conventionally
offers — which is why it sits at layer 6 with ``config`` in reach rather than
beside the registry at layer 2. An unattributed generated file with no
fallback is reported stale rather than queued, so a command that does not
produce that file cannot silently keep the branch's copy.
"""

# doc-group: platform

from __future__ import annotations

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

# git check-attr reports these when the path has no usable regenerate= value.
_ATTR_NONE = frozenset({"unspecified", "unset", "set", ""})
_ATTR_MARKER = ": regenerate: "


# The cached reads below last for the run, not the process: neither
# `.gitattributes`, `.workbench.yml`, nor a repo's task list changes under a
# rebase that is only replaying commits, and each is asked once per generated
# file. A caller that outlives one repo state — a test suite, in practice —
# clears them.
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
def regenerate_attr(filepath: str, cwd: str) -> str | None:
    """The ``regenerate`` gitattributes value for this path, or None.

    ``unspecified``, ``unset``, and ``set`` (flag with no value) mean the file
    has no regenerator of its own. Cached for the run; ``clear_caches``
    forgets it.
    """
    r = git.client.run("check-attr", "regenerate", "--", filepath, cwd=cwd)
    if not r.ok:
        return None
    line = r.stdout.strip().splitlines()[0] if r.stdout.strip() else ""
    idx = line.rfind(_ATTR_MARKER)
    if idx < 0:
        return None
    value = line[idx + len(_ATTR_MARKER):]
    if value in _ATTR_NONE:
        return None
    return value


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


def queue_repo_regeneration(
    filepath: str, cwd: str, queue: RegenQueue, *, conventional: bool = True,
) -> bool:
    """Queue this repo's regeneration for a generated file. False if unknown.

    The ``regenerate=<command>`` attribute wins for that file. Otherwise the
    declared ``rebase.regenerate`` list, then the conventional ``generate``
    task, exactly as before — other repos that have not set the attribute are
    unchanged. A repo that attributes some files but not others reports the
    unattributed ones stale only when it also has no declared list and no
    conventional task.

    Keyed on the repo root rather than the file's directory, so the dozen
    generated files one rebase touches collapse into a single run of each
    command — RegenQueue dedupes on (directory, command). ``stage_dir`` stays
    true with that root.
    """
    root = Path(repo_root(cwd))
    attr = regenerate_attr(filepath, cwd)
    if attr:
        queue.add(root, filepath, tuple(shlex.split(attr)), stage_dir=True)
        return True
    regenerators = repo_regenerators(cwd, conventional=conventional)
    if not regenerators:
        return False
    for regenerator in regenerators:
        queue.add(root, filepath, regenerator.cmd, stage_dir=True)
    return True


def clear_caches() -> None:
    """Forget every cached repo read.

    One process only ever rebases one repo, so nothing in production calls
    this. A test session drives many, each in its own temporary directory, and
    a stale answer keyed on a reused path is the failure it exists to prevent.
    """
    mise_has_task.cache_clear()
    repo_root.cache_clear()
    regenerate_attr.cache_clear()
    repo_regenerators.cache_clear()
