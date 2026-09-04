"""Where a run's bookkeeping lives, keyed by what the run targets.

A run's target is ``(origin repo key, target branch)``. Both components are
readable from a checkout with no network call, which is what lets the readers in
this repo agree on one directory without one of them having to ask the network:
the ``pr`` CLI resolving a PR it is about to review, and ``workbench-statusline``
rendering a prompt.

Two repos that share a repo key share one ``state.json`` and one ``run.lock``:
one run overwrites the other's state and serializes behind its lock, which is
the under-locking bug this layout exists to close. ``acme/api`` and
``other-org/api`` are the routine case, and every attempt to keep such pairs
apart by flattening the origin path into one component with a character map has
left another pair colliding — a lossy map cannot be injective, whatever the map.

So the key does not rely on the flattening for distinctness:

    key = <readable>-<digest>

The digest makes two different repos impossible to confuse. The readable part is
there only so a human reading ``pr/`` can tell which directory is which, which
frees it to be as lossy as flattening a path into one component requires.

The rule, in one paragraph because every reader here has to agree on it and a
rule that is hard to restate is itself a defect:

    Take the remote's **path**: for a remote that names a host (an explicit
    ``scheme://authority``, or scp-style ``host:path``), the path below the
    host; for a ``file://`` URL, the path below the authority; for a plain
    filesystem path, the whole string. Collapse repeated ``/`` and strip
    leading and trailing ``/``. Strip one trailing ``.git``, matching the
    suffix through the fold below, then strip trailing ``/`` again. For a
    ``file://`` URL or a filesystem path, keep the trailing segment alone.
    Fold the result: **map U+0041–U+005A to U+0061–U+007A and leave every other
    codepoint alone.** That is the **canonical form**; when it is empty there is
    no key — return ``None``. The key is ``slug(canonical)``, truncated to 64
    characters and stripped of trailing ``-``, then ``-``, then the first 8 hex
    characters of ``sha256(canonical.encode("utf-8")).hexdigest()``. When the
    readable part is empty, the key is the digest alone.

``slug(s)``, used above and again for the branch, is the whole of its own rule:

    Replace every run of one or more characters **outside**
    ``A-Z a-z 0-9 . _ -`` with a single ``-``, then strip leading and trailing
    ``-``. Nothing else — no case fold, and the dot and underscore survive. A
    mirror that guesses ``[^a-z0-9-]`` turns ``feat/v1.2`` into ``feat-v1-2``
    where this gives ``feat-v1.2``: two directories for one target, which
    under-locks every branch with a dot in its name.

Three properties of that rule a mirror has to reproduce exactly, because a run
that disagrees about any of them looks in a directory nobody writes:

* **A remote is hosted per its scheme, never per its authority.** ``file`` is
  never hosted, whatever authority follows it, because git ignores a file URL's
  authority and clones the path — ``file://localhost/srv/git/widget.git`` is the
  same clone as ``/srv/git/widget.git``. The scheme is matched
  case-insensitively and folded before that comparison, so ``FILE:///srv/repo``
  is unhosted exactly as ``file:///srv/repo`` is; a mirror testing
  ``scheme === "file"`` against the raw text calls it hosted and keeps the whole
  path. A remote naming no path names no repo.
* **Slashes are normalized on both sides of the ``.git`` strip.** git accepts
  ``https://github.com/acme/widget.git/``, whose trailing slash hides the suffix
  from a strip that ran first, and it accepts ``https://github.com/acme/widget/.git``,
  whose strip uncovers a trailing slash a pass that ran only first would leave
  behind. Normalizing once, on either side, gives one of those two spellings its
  own directory and its own lock.
* **The fold is codepoint arithmetic, not a call to a language's lowercase.**
  Repo paths are case-insensitive on GitHub and GitLab, so two differently-cased
  remotes are one repo; git refs are case-sensitive, so ``feat/A`` and ``feat/a``
  are two branches and the branch slug never folds. Deliberately *not*
  ``.toLowerCase()`` or ``.lower()``, on any subset of the input: the canonical
  form is what the digest hashes, so the fold has to be a pure function of
  codepoints and nothing else. A locale-sensitive variant such as
  ``toLocaleLowerCase`` folds ASCII ``I`` to ``ı`` under a Turkish locale, which
  would give ``acme/API`` two keys depending on where the process runs; and a
  Unicode-wide fold makes the key depend on the runtime's Unicode version, which
  is not the same across implementations (this runtime is Unicode 16.0, Node 22
  ships ICU 15.1). Restricting the fold to A–Z removes both channels: the 26
  codepoints it touches have meant the same thing in every Unicode version.

There is deliberately no second key format. An alternate PR-number key would be
a second source of truth for one target, and a transient ``gh`` failure could
move a live target between the two mid-flight.

The layout, which is this repo's own and is not reimplemented anywhere else:

    <state_dir()>/pr/<repo-key>-<branch-slug>/
        state.json
        run.lock

where ``<repo-key>`` is the key above and ``<branch-slug>`` is ``slug(branch)``.

``state_dir()`` rather than a literal path: the state root is relocatable, and
resolving through the function is what makes ``pr/`` ride along with a move
instead of being stranded at the old location.

That both components are derivable offline is a convenience for this repo's own
code, not an invitation to rebuild the path elsewhere: this module is the owner,
and another repo that wants to know what has been reviewed asks the CLI (see
``review_listing``) rather than deriving where a review would sit.
"""

# doc-group: pr-state

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from git import client as git_client
from core import workbench_paths

TARGETS_DIR = "pr"

# Runs of anything outside this set collapse to one dash.
# tests/pr_target_test.py::SLUG_VECTORS is the fixture.
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

# The case fold, as codepoint arithmetic: U+0041-U+005A map to U+0061-U+007A and
# every other codepoint is left alone. Not str.lower() — see the module
# docstring: the canonical form is hashed, so a fold that varies by locale or by
# the runtime's Unicode version hands one repo two keys.
_ASCII_FOLD = {c: c + 0x20 for c in range(ord("A"), ord("Z") + 1)}


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


def _fold_case(text: str) -> str:
    """``A``-``Z`` folded to ``a``-``z``, every other codepoint untouched."""
    return text.translate(_ASCII_FOLD)


def _drop_git_suffix(path: str) -> str:
    """One trailing ``.git``, whatever the case of the suffix.

    ``widget.GIT`` and ``widget.git`` are one repo on every host, so a clone
    spelled either way has to reach one key. Matched through ``_fold_case``
    rather than ``str.lower`` so that the whole contract has exactly one notion
    of case and no path through this module can reach a Unicode fold.
    """
    return path[: -len(".git")] if _fold_case(path[-len(".git"):]) == ".git" else path


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
    # Because the host is gone, the key cannot know whether the forge that
    # served it was case-sensitive, so the fold below is unconditional: on a
    # self-hosted forge that does distinguish them, acme/Widget and acme/widget
    # are two repos and one key.
    path, hosted = _remote_path(url)
    path = _SLASHES_RE.sub("/", path).strip("/")
    # Both sides of the strip: the first pass uncovers a suffix hidden behind a
    # trailing slash (acme/widget.git/), the second removes a slash the strip
    # itself uncovered (acme/widget/.git, the git directory of a non-bare
    # checkout, which git accepts as a clone source).
    path = _drop_git_suffix(path).strip("/")
    if not hosted:
        # ceiling: a local remote keys on its trailing segment alone, so
        # /srv/a/widget and /srv/b/widget are one key. Deliberate — a local
        # clone's leading directories are machine-specific, and keying on them
        # would give one repo a different key on every machine, which is the
        # worse failure. Upgrade trigger: anyone running two same-named local
        # repos against the pr CLI on one machine. The same reduction puts a
        # local clone and a one-segment hosted path on one key: git@host:widget
        # and /srv/git/widget both canonicalize to "widget".
        path = path.rpartition("/")[2]
    return _fold_case(path)


def _key_for(canonical: str) -> str:
    """A canonical form as one path component naming the repo.

    A readable prefix and a digest of the canonical form. The digest is what
    makes the key injective — two repos cannot collide however the flattening
    mangles them — and the prefix is what makes ``pr/`` legible to a human::

        git@github.com:acme/widget.git          -> acme/widget   -> acme-widget-<d>
        https://github.com/Acme/Widget.GIT      -> acme/widget   -> acme-widget-<d>   (same key)
        https://gitlab.com/group/sub/widget.git -> group/sub/widget -> group-sub-widget-<d>
        https://github.com/acme/文档.git         -> acme/文档      -> acme-<d>
        /srv/git/widget.git                     -> widget        -> widget-<d>
        file://localhost/srv/git/widget.git     -> widget        -> widget-<d>        (same key)
        ssh://git@github.com/                   -> ""            -> no key

    Because the prefix carries no distinctness, it is free to be lossy: a
    segment that slugs away contributes nothing, and two paths that flatten
    alike (``acme/wid/get`` and ``acme/wid-get``) share a prefix and differ in
    the digest. That is the design, not a defect in it.

    Takes the canonical form rather than the URL so that a caller wanting both
    names pays for one ``_canonical`` — see ``repo_identity_from_origin``.
    """
    # 8 hex characters is 32 bits of the canonical form's SHA-256: at the scale
    # one machine keys repos, a collision needs no more, and the readable part
    # still leads the directory name.
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:8]
    # ceiling-permanent: the readable prefix is truncated at 64 characters, so
    # two long paths can share it. Harmless — the digest still separates them —
    # and the note is here so nobody "fixes" the truncation. What the cap buys
    # is a bound on the key's half of the directory name (73 characters: 64 +
    # "-" + 8), not a bound on the name: target_dir appends slug(branch), which
    # is uncapped, so a long enough branch still overruns a filesystem's
    # component limit. That half is the branch slug's shape, fixed by the
    # approved spec.
    readable = slug(canonical)[:64].rstrip("-")
    return f"{readable}-{digest}" if readable else digest


def _repo_key(url: str) -> str | None:
    """An origin URL as one path component naming the repo, or None."""
    canonical = _canonical(url)
    return _key_for(canonical) if canonical else None


def _origin_url(cwd: str | None) -> str | None:
    """The ``origin`` remote's URL as git records it, or None if it has none."""
    return git_client.out("remote", "get-url", "origin", cwd=cwd) or None


@dataclass(frozen=True)
class RepoIdentity:
    """What one ``origin`` says about a repo: how to name it, how to key it.

    ``label`` is the canonical form (``acme/widget``) — for callers that want to
    *show* the repo without paying for ``gh repo view``. Not interchangeable
    with ``gh``'s ``owner/repo``: the host is dropped and ``A``-``Z`` folded
    (see ``_canonical``), and a local remote reduces to its trailing segment.
    Fine for a header, wrong for an API call.

    ``key`` is the target key the layout is built from — opaque, per the module
    docstring.

    One type rather than two lookups because both come from one canonical form:
    a label and a key that reach a caller together cannot name different repos,
    and reading the remote once is what makes that true rather than likely.
    """

    label: str
    key: str


def repo_identity_from_origin(cwd: str | None = None) -> RepoIdentity | None:
    """Both names from one read of ``origin``, or None if it names no repo.

    Not ``gh repo view``: the key must be derivable without the network, and two
    sources for one component is how the two derivations drift apart.
    """
    url = _origin_url(cwd)
    canonical = _canonical(url) if url else ""
    if not canonical:
        return None
    return RepoIdentity(label=canonical, key=_key_for(canonical))


def repo_key_from_origin(cwd: str | None = None) -> str | None:
    """The repo's key per its ``origin`` remote, or None if it has none."""
    identity = repo_identity_from_origin(cwd)
    return identity.key if identity else None


def targets_root() -> Path:
    """The directory every run's target lives under, before the repo-branch key.

    A function, not a module constant: ``state_dir()`` resolves per call, and
    caching this at import time is what would break ``WORKBENCH_STATE_DIR``
    monkeypatching in tests. The sole owner of the join, so a second copy of
    it (e.g. in review_gc's gc sweep) cannot drift from this one.
    """
    return workbench_paths.state_dir() / TARGETS_DIR


def target_dir(repo_key: str, branch: str) -> Path:
    """Where a run's state and lock live, keyed by what the run targets."""
    return targets_root() / f"{repo_key}-{slug(branch)}"


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
    branch = git_client.out("symbolic-ref", "--short", "HEAD", cwd=str(path))
    if not branch:
        return None
    return target_dir(repo_key, branch)
