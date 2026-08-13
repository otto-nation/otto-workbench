"""Where a run's bookkeeping lives, keyed by what the run targets.

A run's target is ``(origin repo key, target branch)``. Both components are
readable from a checkout with no network call, which is what lets three
different readers agree on one directory: the ``pr`` CLI resolving a PR it is
about to review, ``workbench-statusline`` rendering a prompt, and ui-code's
server watching a repo it has never checked out.

The repo key is the origin URL's whole path below the host, flattened —
``acme-widget``, not ``widget``, and ``group-subgroup-widget`` for a nested
GitLab project. A bare repo name is not unique: ``acme/api`` and
``other-org/api`` would share a directory, so one repo's run would overwrite the
other's ``state.json`` and hold its ``run.lock``, serializing PRs that have
nothing to do with each other. Forks and ordinary names (``api``, ``docs``,
``site``) make that collision routine, and keeping only the last namespace
segment would leave the same collision in place one level up.

There is deliberately no second key format. An alternate PR-number key would be
a second source of truth for one target, and a transient ``gh`` failure could
move a live target between the two mid-flight.

The layout is a published interface — ui-code reimplements it in TypeScript::

    <state_dir()>/pr/<repo-key>-<branch-slug>/
        state.json
        run.lock

``state_dir()`` rather than a literal path: #624 phase 4 moves that root to
``XDG_STATE_HOME`` alongside the migration that carries the data, and resolving
through the function is what makes ``pr/`` ride along instead of being stranded.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import workbench_paths

TARGETS_DIR = "pr"

# Runs of anything outside this set collapse to one dash. ui-code mirrors this
# exactly; tests/pr_target_test.py::SLUG_VECTORS is the shared fixture.
_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")

# Everything up to the last "/" or ":", used only for remotes that name no host.
_REPO_TAIL_RE = re.compile(r"[/:]")

# A remote names a host two ways: an explicit scheme (https://, ssh://, git://)
# followed by a non-empty authority, or scp-style host:path. Everything else is a
# filesystem path (/srv/git/widget.git, ../widget, and file:///srv/git/widget.git,
# which is one wearing a scheme) — no host means no namespace to qualify the key
# with, so such a remote keeps its single trailing segment.
_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)
# No "user@" requirement: git reads any colon before the first slash as the
# host/path separator, so an ~/.ssh/config alias (gitbox:acme/widget.git) is
# scp-style too, and dropping it here would collide every repo behind that alias.
_SCP_RE = re.compile(r"^[^/:]+:")


def slug(branch: str) -> str:
    """A branch name as a single path component.

    Deliberate ceiling: ``a/b`` and ``a-b`` collide, exactly as they already do
    for worktree directory names.
    """
    return _SLUG_RE.sub("-", branch).strip("-")


def _hosted_path(url: str) -> str:
    """A hosted remote's whole path below the host, or "" when it has no host."""
    scheme = _SCHEME_RE.match(url)
    if scheme:
        authority, _, path = url[scheme.end():].partition("/")
        # An empty authority is the file:// case: no host was named, so the URL
        # is a filesystem path and has to key the same as the bare path does.
        return path if authority else ""
    scp = _SCP_RE.match(url)
    return url[scp.end():] if scp else ""


def _drop_git_suffix(path: str) -> str:
    return path[: -len(".git")] if path.endswith(".git") else path


def _repo_key(url: str) -> str | None:
    """An origin URL as one path component naming the repo, or None.

    The whole path below the host, not its last segment or two:
    ``git@github.com:acme/widget.git`` and ``https://github.com/acme/widget``
    both give ``acme-widget``, and ``gitlab.com/group/subgroup/widget`` gives
    ``group-subgroup-widget`` — truncating to a fixed depth would let two nested
    projects sharing a leaf namespace collide. Composed through ``slug`` so the
    separator rules stay in one place.
    """
    url = url.rstrip("/")
    hosted = _drop_git_suffix(_hosted_path(url).strip("/"))
    if hosted:
        return slug(hosted) or None
    # No host to namespace under, so there is nothing above the repo directory
    # that means anything: /srv/git/widget.git and ../widget are both "widget".
    tail = _drop_git_suffix(_REPO_TAIL_RE.split(url)[-1])
    return slug(tail) or None


def repo_key_from_origin(cwd: str | None = None) -> str | None:
    """The repo's key per its ``origin`` remote, or None if it has none.

    Not ``gh repo view``: the key must be derivable without the network, and two
    sources for one component is how the two derivations below drift apart.
    """
    r = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        capture_output=True, text=True, cwd=cwd,
    )
    if r.returncode != 0 or not r.stdout.strip():
        return None
    return _repo_key(r.stdout.strip())


def target_dir(repo_key: str, branch: str) -> Path:
    """Where a run's state and lock live, keyed by what the run targets."""
    return workbench_paths.state_dir() / TARGETS_DIR / f"{repo_key}-{slug(branch)}"


def target_dir_for_checkout(path: Path) -> Path | None:
    """The same directory, read out of a checkout's git config.

    For readers holding a directory and nothing else. None on a detached HEAD or
    a repo without an ``origin`` — callers render that as "no state" rather than
    guessing a key.
    """
    repo_key = repo_key_from_origin(str(path))
    if not repo_key:
        return None
    # Not `rev-parse --abbrev-ref`: that needs HEAD to resolve to a commit, so
    # it fails on a freshly-init'd branch with nothing committed yet.
    # `symbolic-ref` reads the ref HEAD points at, unborn or not, and fails
    # cleanly on detached HEAD instead of resolving to the literal name "HEAD".
    r = subprocess.run(
        ["git", "symbolic-ref", "--short", "HEAD"],
        capture_output=True, text=True, cwd=str(path),
    )
    branch = r.stdout.strip()
    if r.returncode != 0 or not branch:
        return None
    return target_dir(repo_key, branch)
