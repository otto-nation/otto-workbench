"""The repos on this machine that use otto-workbench — Python half.

Membership means a workbench command actually ran in a repo. This side does the
recording for the tools written in Python: Claude's SessionStart hook, which
already resolves the repo root, and the ``pr`` CLI, which already resolves a
worktree root. ``lib/projects.sh`` is the shell half — it owns the one-time
backfill, the CLI, and the reads that the machine profile generator and the
checkout-scoped migrations make.

Both halves read and write one file named by ``workbench_paths.projects_registry()``:
one absolute path per line, optionally followed by a tab and the repo identity
the shell half records from the sync. This side reads the path ahead of that tab
and writes bare paths, because resolving an identity means forking git on a
session's startup path. Text rather than YAML because every write is an append
and every read is a scan. ``tests/projects.bats`` cross-validates the two halves
against the same file.

Nothing here raises. Registration is a side effect of a command that was run for
some other reason, and a hook that failed because a state file was unwritable
would cost the user their session for a bookkeeping entry.
"""

# doc-group: platform

from __future__ import annotations

import os
from pathlib import Path

import workbench_paths

# Lines the shell half writes to mark its backfill as done. A reader that took
# one for a path would hand a caller a directory that does not exist.
COMMENT_PREFIX = "#"

# What separates a registered work tree from the repo identity behind it. The
# shell half writes that second field from the sync; nothing here does, because
# resolving it means forking git on a session's startup path. A reader that took
# a whole line for a path would hand a caller a directory that does not exist.
FIELD_SEP = "\t"


def _path_of(line: str) -> str:
    """The work-tree path a registry line names, without the repo identity."""
    return line.split(FIELD_SEP, 1)[0]

# Where test harnesses build throwaway git repos. `bats` creates one per suite
# under $TMPDIR and runs validators and pre-commit hooks inside it — workbench
# commands by every other test, and gone by the time anything reads the
# registry. lib/projects.sh's _project_excluded spells the same set.
#
# The /private twins are not redundant: /tmp and /var/folders are symlinks into
# /private on macOS, and callers hand over a path git already resolved.
#
# The /var/folders and /private/var/folders entries are macOS-only paths — on
# Linux they simply never match, which is harmless, not wrong. /tmp is the
# shared entry that still does the job on both.
TEMP_ROOTS = ("/tmp", "/private/tmp", "/var/folders", "/private/var/folders")


def registry_path() -> Path:
    return workbench_paths.projects_registry()


def _resolved(path: Path) -> Path:
    """path with symlinks followed, or path itself when that cannot be done."""
    try:
        return path.resolve()
    except OSError:
        return path


def excluded(repo_root: Path) -> bool:
    """True when repo_root must never enter the registry.

    The workbench's own state and cache roots join the temp roots here: the
    review system builds worktrees under them, and those are not projects.

    Both sides of every comparison are checked resolved as well as literal. The
    roots come from env vars a caller may well have written with a symlink in
    them, and this guard failing open puts a throwaway worktree in a file the
    machine profile renders.
    """
    if not repo_root.is_absolute():
        return True
    roots = [Path(r) for r in TEMP_ROOTS]
    roots += [workbench_paths.state_dir(), workbench_paths.cache_dir()]
    tmpdir = os.environ.get("TMPDIR")
    if tmpdir:
        roots.append(Path(tmpdir))
    bases = {base for root in roots for base in (root, _resolved(root))}
    candidates = (repo_root, _resolved(repo_root))
    return any(candidate == base or base in candidate.parents
               for base in bases for candidate in candidates)


def _is_worktree(repo_root: Path) -> bool:
    """True when repo_root is the root of a git work tree.

    ``.git`` alone is not the test: a bare-repo layout puts a bare repository at
    ``<container>/.git``, and the container holds worktrees rather than being
    one. Mirrors ``_project_is_worktree`` in ``lib/projects.sh``.
    """
    dot_git = repo_root / ".git"
    if not repo_root.is_dir() or not dot_git.exists():
        return False
    config = dot_git / "config"
    if not config.is_file():
        return True
    try:
        text = config.read_text(errors="replace")
    except OSError:
        return False
    return not any("".join(line.split()) == "bare=true"
                   for line in text.splitlines())


def register(repo_root: Path | str | None) -> bool:
    """Record repo_root as a repo that uses the workbench.

    repo_root must already be a resolved work-tree root — every caller has one
    in hand, so nothing here shells out to git. Returns True when the path is in
    the registry afterwards.

    A path holding FIELD_SEP is refused alongside the other membership rules: the
    tab is what tells the path field from the repo identity, so a path that
    carries one cannot be told apart from a line that already has an identity —
    the membership check would compare against the truncated path forever, never
    match the real one, and every workbench command run in that repo would
    append another line with no error.
    """
    if not repo_root:
        return False
    path = Path(repo_root)
    if FIELD_SEP in str(path) or excluded(path) or not _is_worktree(path):
        return False
    line = str(path)
    if any(_path_of(existing) == line for existing in _read_lines()):
        return True
    try:
        registry = registry_path()
        registry.parent.mkdir(parents=True, exist_ok=True)
        with open(registry, "a") as handle:
            handle.write(line + "\n")
    except OSError:
        return False
    return True


def _read_lines() -> list[str]:
    try:
        text = registry_path().read_text(errors="replace")
    except OSError:
        return []
    return [line.strip() for line in text.splitlines() if line.strip()]


def registered() -> list[Path]:
    """Every registered repo that still exists, each named once.

    Directories that are gone are dropped here rather than rewritten away —
    read-time filtering is what saves the registry from needing a pruning job.
    ``otto-workbench projects prune`` makes the drop permanent.

    Repeats are dropped for the same reason: registration is an append guarded
    by a membership check rather than a lock, so two commands starting in one
    repo at the same moment can each append. Mirrors ``project_registered``.
    """
    paths = [_path_of(line) for line in _read_lines()
             if not line.startswith(COMMENT_PREFIX)]
    return [Path(p) for p in dict.fromkeys(paths) if Path(p).is_dir()]
