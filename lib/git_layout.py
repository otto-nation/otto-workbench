"""The bare-repo worktree layout, as git names it.

A bare-repo checkout is not one directory but two: the *container*, holding the
bare `.git` plus every worktree as a peer, and the worktree the caller happens
to be standing in.  Anything that belongs to the repo rather than to one branch
— Claude Code's permission grants, the workbench's own `.workbench.yml` — lives
at the container, because a worktree is deleted by `wt remove` and takes what
was written into it along.

`container_dir` is the one answer to "where is that directory", shared so the
two subsystems that need it cannot disagree:

    lib/permissions.py                 the grants mirrored into the container
    ai/lib/config/workbench_config.py  the container's config scope

It is deliberately a question git answers rather than a walk up the
filesystem.  A walk would let a `.workbench.yml` dropped in a grouping
directory govern every unrelated checkout beneath it; "the directory holding
this repo's common git dir" cannot reach sideways.

`bin/resolve-worktree` is the bash owner of the other direction —
container → the worktree it stands in for. `worktree_for` below is how Python
asks it, by running it rather than by re-deriving the rule.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass

_LIB_DIR = os.path.dirname(os.path.realpath(__file__))
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)
from gitenv import git_env_clear  # noqa: E402

_RESOLVE_WORKTREE = os.path.join(os.path.dirname(_LIB_DIR), 'bin', 'resolve-worktree')

# Seconds `bin/resolve-worktree` may take. It reads two local refs and a
# worktree list, so the bound is a hang detector rather than a work budget —
# the same 10s `ai/lib/core/timeouts.LOCAL` names, spelled here because `lib/`
# is outside `ai/` and cannot import it. Match that tier if it ever moves.
_RESOLVE_TIMEOUT = 10.0

# `bin/resolve-worktree`'s exit codes, which are its interface. NOT_BARE is the
# ordinary answer — every normal repo, worktree and non-repo directory lands
# there — and UNRESOLVED is the one a caller must not paper over: a container
# naming no checkout has no tree to offer, and picking one anyway is how a tool
# writes into a worktree the operator never asked for.
RESOLVED = 0
UNRESOLVED = 1
NOT_BARE = 2
USAGE = 64
# Not one of resolve-worktree's codes: the script was not reachable, or did not
# answer. Callers treat it like NOT_BARE — no tree, no guess — but it is spelled
# apart so a missing install is distinguishable from a directory that simply is
# not a container.
UNAVAILABLE = 127


@dataclass(frozen=True)
class Worktree:
    """What `bin/resolve-worktree` made of a directory.

    `status` carries the script's exit code rather than collapsing to a bool,
    because the two failures mean opposite things to a caller: `NOT_BARE` says
    the directory was never a container and whatever the caller already had is
    still right, while `UNRESOLVED` says it *is* one and deliberately names no
    tree. `ok` is the predicate to branch on; `path` is set only when it holds.
    """

    path: str | None
    status: int

    @property
    def ok(self) -> bool:
        return self.status == RESOLVED and self.path is not None


def worktree_for(directory: str) -> Worktree:
    """The worktree a bare-repo container stands in for, via the bash owner.

    A thin wrapper over `bin/resolve-worktree`, not a second implementation of
    it. The rule — the checkout on the branch the container's own HEAD names —
    already has more spellings than it should, and `tests/container_source.bats`
    exists because a disagreement between two of them is a live hazard rather
    than a hypothetical one. Paying a subprocess is the cost of not adding a
    third.

    The environment is cleared for the same reason `git()` above clears it, and
    with sharper consequences: `resolve-worktree` does its own `cd` and asks
    plain `git`, so an inherited `GIT_DIR` — which the pre-push hook exports —
    makes it exit 128, outside the set of codes it documents.

    A missing script is not an error worth raising. `bin/` does not ship in the
    otto-ai-tools tarball, so a caller running from one gets `UNAVAILABLE` and
    degrades to whatever it does without a container answer.
    """
    try:
        result = subprocess.run(
            (_RESOLVE_WORKTREE, directory),
            capture_output=True, text=True, check=False,
            env=git_env_clear(), timeout=_RESOLVE_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return Worktree(None, UNAVAILABLE)
    if result.returncode != RESOLVED:
        return Worktree(None, result.returncode)
    path = result.stdout.strip()
    return Worktree(path, RESOLVED) if path else Worktree(None, UNRESOLVED)


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
# A process outliving one command is the case that assumption does not cover,
# and the only one today is the test suite, where a session builds and tears
# down repos on purpose: `tests/git_layout_test.py` clears this between tests.
# Anything longer-lived arriving later — a daemon, a watcher — needs the same,
# so clear the dict rather than reading around it.
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
