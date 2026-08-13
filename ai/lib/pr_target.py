"""Where a run's bookkeeping lives, keyed by what the run targets.

A run's target is ``(origin repo key, target branch)``. Both components are
readable from a checkout with no network call, which is what lets three
different readers agree on one directory: the ``pr`` CLI resolving a PR it is
about to review, ``workbench-statusline`` rendering a prompt, and ui-code's
server watching a repo it has never checked out.

Two repos that share a repo key share one ``state.json`` and one ``run.lock``:
one run overwrites the other's state and serializes behind its lock, which is
the under-locking bug this layout exists to close. ``acme/api`` and
``other-org/api`` are the routine case, and every attempt to keep such pairs
apart by flattening the origin path into one component with a character map has
left another pair colliding — a lossy map cannot be injective, whatever the map.

So the key does not rely on the flattening for distinctness::

    key = <readable>-<digest>

The digest makes two different repos impossible to confuse. The readable part is
there only so a human reading ``pr/`` can tell which directory is which, which
frees it to be as lossy as flattening a path into one component requires.

The rule, in one paragraph because ui-code has to mirror it and a rule that is
hard to restate is itself a defect:

    Reduce the origin URL to a **canonical form**: for a remote that names a
    host (an explicit ``scheme://authority``, or scp-style ``host:path``), the
    path below the host; for a ``file://`` URL or a plain filesystem path, the
    trailing path segment alone. Strip leading and trailing ``/``, collapse
    repeated ``/``, strip one trailing ``.git`` case-insensitively, and
    lowercase the result. An empty canonical form means no key — return
    ``None``. The key is ``slug(canonical)``, truncated to 64 characters and
    stripped of trailing ``-``, then ``-``, then the first 8 hex characters of
    ``sha256(canonical.encode("utf-8")).hexdigest()``. When the readable part is
    empty, the key is the digest alone.

The slash normalization runs before the ``.git`` strip, not after: git accepts
``https://github.com/acme/widget.git/``, whose trailing slash hides the suffix
from a strip that ran first, and that spelling has to reach the same key as
every other spelling of the same repo.

Two properties of that rule a mirror has to reproduce exactly, because a run
that disagrees about either looks in a directory nobody writes:

* **A remote is hosted per its scheme, never per its authority.** ``file`` is
  never hosted, whatever authority follows it, because git ignores a file URL's
  authority and clones the path — ``file://localhost/srv/git/widget.git`` is the
  same clone as ``/srv/git/widget.git``. A remote naming no path names no repo.
* **The repo key folds to lowercase; the branch slug never folds.** Repo paths
  are case-insensitive on GitHub and GitLab, so two differently-cased remotes
  are one repo; git refs are case-sensitive, so ``feat/A`` and ``feat/a`` are
  two branches. The fold is on the canonical form, so it is what the digest
  sees: mirror it with Unicode default case conversion — Python's ``.lower()``
  and TypeScript's ``.toLowerCase()``, never a locale-sensitive variant such as
  ``toLocaleLowerCase``, which folds ``I`` to ``ı`` under a Turkish locale and
  would hand one repo two digests.

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

import hashlib
import re
import subprocess
from pathlib import Path

import workbench_paths

TARGETS_DIR = "pr"

# Runs of anything outside this set collapse to one dash. ui-code mirrors this
# exactly; tests/pr_target_test.py::SLUG_VECTORS is the shared fixture.
_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")

# "acme//widget" is "acme/widget": git accepts the doubled separator and clones
# the same repo, so the two spellings have to reduce to one canonical form.
_SLASHES_RE = re.compile(r"/+")

# A remote names a host two ways: an explicit scheme (https://, ssh://, git://),
# or scp-style host:path. Everything else is a filesystem path
# (/srv/git/widget.git, ../widget, and file://.../srv/git/widget.git, which is one
# wearing a scheme) — no host means no namespace to qualify the key with, so such
# a remote keeps its single trailing segment.
_SCHEME_RE = re.compile(r"^([a-z][a-z0-9+.-]*)://", re.IGNORECASE)
# The one scheme that names no host. git parses a file URL's authority and then
# discards it: `git clone file://bogushost/srv/git/widget.git` clones
# /srv/git/widget.git, and records the URL verbatim in remote.origin.url.
_LOCAL_SCHEME = "file"
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


def _remote_path(url: str) -> tuple[str, bool]:
    """A remote's path, and whether a host qualified it.

    Hosted is decided by the scheme, not by the authority. Deciding on the
    authority made ``file://localhost/srv/git/widget.git`` key as hosted
    (``srv-git-widget``) while the identical clone spelled ``/srv/git/widget.git``
    keyed ``widget`` — one target, two state dirs, two locks.

    A ``file`` URL still yields the path *below* its authority, unhosted: the
    authority is git's to discard, so ``file://localhost/`` names no path and no
    repo, rather than naming a repo called ``localhost``.
    """
    scheme = _SCHEME_RE.match(url)
    if scheme:
        _authority, _, path = url[scheme.end():].partition("/")
        return path, scheme.group(1).lower() != _LOCAL_SCHEME
    scp = _SCP_RE.match(url)
    return (url[scp.end():], True) if scp else (url, False)


def _drop_git_suffix(path: str) -> str:
    """One trailing ``.git``, whatever its case.

    ``widget.GIT`` and ``widget.git`` are one repo on every host, so a clone
    spelled either way has to reach one key. Case-insensitively here rather than
    after the fold so that the fold has one job.
    """
    return path[: -len(".git")] if path.lower().endswith(".git") else path


def _canonical(url: str) -> str:
    """The origin URL reduced to the string the key is derived from.

    Every spelling git accepts for one repo collapses here, and nowhere else:
    the digest and the readable part are both computed from this, so they cannot
    disagree about which repo they name.
    """
    # ceiling: the host is dropped here, so github.com/acme/widget and
    # gitlab.com/acme/widget are one key. Existing behaviour from the approved
    # design, kept: one repo is routinely spelled with several hosts (an ssh
    # alias, a mirror), and those spellings must take one lock. Upgrade trigger:
    # anyone running the pr CLI against two same-pathed repos on two hosts.
    path, hosted = _remote_path(url)
    path = _SLASHES_RE.sub("/", path).strip("/")
    if not hosted:
        # ceiling: a local remote keys on its trailing segment alone, so
        # /srv/a/widget and /srv/b/widget are one key. Deliberate — a local
        # clone's leading directories are machine-specific, and keying on them
        # would give one repo a different key on every machine, which is the
        # worse failure. Upgrade trigger: anyone running two same-named local
        # repos against the pr CLI on one machine.
        path = path.rpartition("/")[2]
    return _drop_git_suffix(path).lower()


def _repo_key(url: str) -> str | None:
    """An origin URL as one path component naming the repo, or None.

    A readable prefix and a digest of the canonical form. The digest is what
    makes the key injective — two repos cannot collide however the flattening
    mangles them — and the prefix is what makes ``pr/`` legible to a human::

        git@github.com:acme/widget.git          -> acme/widget   -> acme-widget-<d>
        https://github.com/Acme/Widget.GIT      -> acme/widget   -> acme-widget-<d>   (same key)
        https://gitlab.com/group/sub/widget.git -> group/sub/widget -> group-sub-widget-<d>
        https://github.com/acme/文档.git         -> acme/文档      -> acme-<d>
        /srv/git/widget.git                     -> widget        -> widget-<d>
        file://localhost/srv/git/widget.git     -> widget        -> widget-<d>        (same key)
        ssh://git@github.com/                   -> ""            -> None

    Because the prefix carries no distinctness, it is free to be lossy: a
    segment that slugs away contributes nothing, and two paths that flatten
    alike (``acme/wid/get`` and ``acme/wid-get``) share a prefix and differ in
    the digest. That is the design, not a defect in it.
    """
    canonical = _canonical(url)
    if not canonical:
        return None
    # 8 hex characters is 32 bits of the canonical form's SHA-256: at the scale
    # one machine keys repos, a collision needs no more, and the readable part
    # still leads the directory name.
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:8]
    # ceiling: the readable prefix is truncated at 64 characters, so two long
    # paths can share it. Harmless — the digest still separates them — and the
    # cap is what keeps the directory name inside every filesystem's component
    # limit. Nothing to upgrade; the note is here so nobody "fixes" it.
    readable = slug(canonical)[:64].rstrip("-")
    return f"{readable}-{digest}" if readable else digest


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
