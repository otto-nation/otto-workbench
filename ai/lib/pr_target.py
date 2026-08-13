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

Three rules finish the key, each stated so a second implementation can mirror it:

* **A remote is hosted per its scheme, never per its authority.** ``file`` is
  never hosted, whatever authority follows it, because git ignores a file URL's
  authority and clones the path — ``file://localhost/srv/git/widget.git`` is the
  same clone as ``/srv/git/widget.git``. A hosted URL naming no path names no
  repo, and keys ``None`` rather than its host.
* **Each path segment is slugged on its own, and a segment that slugs to empty
  stands in the first 8 hex characters of the SHA-256 of its UTF-8 bytes.**
  Slugging the whole path at once drops such a segment entirely, which would key
  ``acme/文档`` and ``acme/日本語`` alike as ``acme``.
* **The repo key folds to lowercase, after slugging; the branch slug never
  folds.** Repo paths are case-insensitive on GitHub and GitLab, so two
  differently-cased remotes are one repo; git refs are case-sensitive, so
  ``feat/A`` and ``feat/a`` are two branches. Folding after slugging keeps the
  fold ASCII-only, where ``.lower()`` and ``.toLowerCase()`` agree byte for byte.

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

# Everything up to the last "/" or ":", used only for remotes that name no host.
_REPO_TAIL_RE = re.compile(r"[/:]")

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


def _hosted_path(url: str) -> str | None:
    """A hosted remote's whole path below the host, or None when it has no host.

    Hosted is decided by the scheme, not by the authority. Deciding on the
    authority made ``file://localhost/srv/git/widget.git`` key as hosted
    (``srv-git-widget``) while the identical clone spelled ``/srv/git/widget.git``
    keyed ``widget`` — one target, two state dirs, two locks.

    A hosted URL with an empty path returns "" rather than None, which keeps a
    partially-built ``ssh://git@github.com/`` out of the no-host branch: that
    branch would split the whole URL and key the userinfo and host.
    """
    scheme = _SCHEME_RE.match(url)
    if scheme:
        if scheme.group(1).lower() == _LOCAL_SCHEME:
            return None
        _authority, _, path = url[scheme.end():].partition("/")
        return path
    scp = _SCP_RE.match(url)
    return url[scp.end():] if scp else None


def _drop_git_suffix(path: str) -> str:
    return path[: -len(".git")] if path.endswith(".git") else path


def _slug_segment(segment: str) -> str:
    """One path segment as a key component, never empty.

    A segment holding no ``[A-Za-z0-9._-]`` character slugs to nothing and would
    vanish from the key, so ``acme/文档`` and ``acme/日本語`` would both key
    ``acme`` and share one ``state.json`` and one ``run.lock``. The stand-in is
    the first 8 hex characters of the segment's SHA-256, which any language can
    reproduce from the segment's UTF-8 bytes.
    """
    slugged = slug(segment)
    # ceiling: the stand-in fires only on an empty slug, not on a lossy one, so
    # acme/wídget and acme/wîdget both key acme-w-dget and still collide. An
    # empty slug stops naming the repo at all; lossy collapse is the ceiling
    # slug() already declares. The narrow rule is also the one ui-code can mirror
    # without matching Python's character-class semantics exactly. Upgrade to
    # "digest whenever slugging is lossy" if two live repos ever collide that way.
    return slugged or hashlib.sha256(segment.encode("utf-8")).hexdigest()[:8]


def _slug_path(path: str) -> str:
    """A whole path as one key component, slugged a segment at a time.

    Per segment rather than all at once so that no segment can disappear. Empty
    segments (``acme//widget``) are separator noise, not content, and are
    dropped. This is byte-identical to slugging the whole path for any path whose
    every segment holds at least one sluggable character — ASCII or not — which
    is every published vector; it differs only where the old behaviour dropped a
    segment, including for ASCII segments like ``@`` that hold none.
    """
    return "-".join(_slug_segment(s) for s in path.split("/") if s)


def _repo_key(url: str) -> str | None:
    """An origin URL as one path component naming the repo, or None.

    The whole path below the host, not its last segment or two:
    ``git@github.com:acme/widget.git`` and ``https://github.com/acme/widget``
    both give ``acme-widget``, and ``gitlab.com/group/subgroup/widget`` gives
    ``group-subgroup-widget`` — truncating to a fixed depth would let two nested
    projects sharing a leaf namespace collide. Composed through ``slug`` so the
    separator rules stay in one place.

    Lowercased because two clones of one GitHub repo can spell the path with
    different case and must take one lock. Only here: ``slug`` is shared with the
    branch, and git refs are case-sensitive, so folding there would merge
    ``feat/A`` and ``feat/a`` into one target. Folding after slugging keeps the
    input ASCII, away from Turkish-I and the rest of the locale traps a
    cross-language contract cannot afford.
    """
    url = url.rstrip("/")
    hosted = _hosted_path(url)
    # None is "no host to namespace under", so there is nothing above the repo
    # directory that means anything: /srv/git/widget.git and ../widget are both
    # "widget". An empty hosted path is a hosted URL naming no repo, and keys
    # None below rather than falling through to name the host.
    path = hosted if hosted is not None else _REPO_TAIL_RE.split(url)[-1]
    # One fold, on the repo key only — see the branch-slug asymmetry above.
    return _slug_path(_drop_git_suffix(path.strip("/"))).lower() or None


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
