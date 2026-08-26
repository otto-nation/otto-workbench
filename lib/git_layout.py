"""The bare-repo worktree layout, as git names it.

A bare-repo checkout is not one directory but two: the *container*, holding the
bare `.git` plus every worktree as a peer, and the worktree the caller happens
to be standing in.  Anything that belongs to the repo rather than to one branch
— Claude Code's permission grants, the workbench's own `.workbench.yml` — lives
at the container, because a worktree is deleted by `wt remove` and takes what
was written into it along.

`container_dir` is the one answer to "where is that directory", shared so the
two subsystems that need it cannot disagree:

    lib/permissions.py          the grants mirrored into the container
    ai/lib/workbench_config.py  the container's config scope

It is deliberately a question git answers rather than a walk up the
filesystem.  A walk would let a `.workbench.yml` dropped in a grouping
directory govern every unrelated checkout beneath it; "the directory holding
this repo's common git dir" cannot reach sideways.

`bin/resolve-worktree` is the bash owner of the other direction —
container → the worktree it stands in for.
"""

from __future__ import annotations

import os
import subprocess
import sys

_LIB_DIR = os.path.dirname(os.path.realpath(__file__))
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)
from gitenv import git_env_clear  # noqa: E402


def git(repo_root: str, *args: str) -> str | None:
    """Run a read-only git query in the repo, or None if git cannot answer.

    The environment is cleared of git's own overrides first, because they beat
    `-C`. The pre-push hook exports `GIT_DIR`, and with one set every question
    below is answered for the hook's repository instead of the directory asked
    about — `rev-parse --show-toplevel` at the container answers the container,
    so the no-working-tree guard holds and the container is skipped in exactly
    the run that had to see it.
    """
    try:
        result = subprocess.run(('git', '-C', repo_root, *args),
                                capture_output=True, text=True, check=False,
                                env=git_env_clear())
    except OSError:
        return None
    return result.stdout.strip() if result.returncode == 0 else None


# What the shared git dir at each key resolved to, for the life of the process.
#
# Every worktree of one bare repo names the same `--git-common-dir`, so a
# caller walking a whole registry — `otto-workbench config get` over the
# project list, the permission sweep — asks the same question once per repo
# instead of once per checkout, and the two `rev-parse` reads behind it are
# paid once.  The layout a repo is in does not change while a command runs, so
# there is nothing to invalidate.
#
# Keyed on the shared git dir rather than on `repo_root`, because that is what
# the worktrees have in common.  Populated only once git has named it, so a
# directory that is not a repo yet is never remembered as one.
_CONTAINERS: dict[str, str | None] = {}


def container_dir(repo_root: str) -> str | None:
    """The directory holding the shared git dir, when it is not the worktree.

    In a bare-repo worktree layout every worktree is a peer of the bare `.git`
    inside a container directory, so a file written at the container sits above
    anything a walk rooted in a worktree can see — and Claude Code roots a
    session wherever it was launched, the container included.

    `--git-common-dir` names the shared git dir and its parent is the
    container.  In a normal clone that parent is the worktree itself, so the
    comparison makes the extra scan a no-op instead of a special case.  It is
    the comparison rather than an unconditional `..` because the parent of a
    plain checkout belongs to somebody else.

    A container holds no working tree, which is the second half of the test:
    linked worktrees added to an ordinary clone put the shared git dir inside
    the main checkout, and that checkout is a working tree with an owner — its
    `.claude/settings.json` is tracked, its `.workbench.yml` is committed —
    not an unreviewed file sitting outside every checkout.

    Answers for a shared git dir already seen in this process come from
    `_CONTAINERS` — see the note there for why that is safe and what it buys.
    """
    common = git(repo_root, 'rev-parse', '--git-common-dir')
    if not common:
        return None
    shared = os.path.realpath(os.path.join(repo_root, common))
    if shared in _CONTAINERS:
        return _CONTAINERS[shared]
    toplevel = git(repo_root, 'rev-parse', '--show-toplevel')
    if not toplevel:
        return None
    container = os.path.dirname(shared)
    if container == os.path.realpath(toplevel):
        found = None
    else:
        found = None if git(container, 'rev-parse', '--show-toplevel') else container
    _CONTAINERS[shared] = found
    return found
