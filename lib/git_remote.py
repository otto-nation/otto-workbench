"""The remote, its default branch, and whether a branch exists on it.

The Python half of `lib/git_remote.sh`, for the callers that are Python rather
than bash. Same fact, same ladder: an unfetched clone, a `wt-init`-converted
repo, or any remote whose HEAD was never pointed with `git remote set-head
origin -a` lacks the `refs/remotes/origin/HEAD` symref, and when it is missing a
remote-tracking ref that actually exists beats a literal guess — `main`, then
`master`, and only then the literal `main`.

`tests/git_remote_test.py` runs both halves against the same repositories and
fails if they ever answer differently. The Python side used to stop at the
symref and return `"main"`, so a `master` repository was told its default branch
was one it does not have.

Two contracts, deliberately separate, matching the bash side:
`resolve_default_branch` always answers, because a caller printing a hint needs
a name even when it is a guess; `default_base_ref` returns None unless the ref
is really there, because a caller about to diff against it turns a wrong guess
into git's "unknown revision" with nobody's name on it.

The git environment is not cleared here, exactly as it is not in the bash half:
`GIT_DIR` beats `-C`, but unsetting it belongs to the caller that knows whether
it owns the process — `bin/local/check-surface-compat` calls `git_env_clear`
before asking, and the pre-push hook must keep its own `GIT_DIR` intact.
"""

from __future__ import annotations

import subprocess

# Git remote name used for push/fetch/range operations.
GIT_REMOTE = "origin"

# Branch names tried, in order, when the remote's HEAD symref is missing.
DEFAULT_BRANCH_CANDIDATES = ("main", "master")

# Answer when no candidate has a remote-tracking ref, so that a caller with
# something to print always has a name. `default_base_ref` is the way to find
# out that this is a guess.
DEFAULT_BRANCH_FALLBACK = "main"

# Seconds any single query may take. These are all local ref reads with no
# network call, so the bound is only there to stop a wedged git holding a
# caller open forever.
_TIMEOUT = 10.0


def _git(args: list[str], cwd: str | None = None) -> str | None:
    """Run a read-only git query, or None when git cannot answer."""
    try:
        result = subprocess.run(
            ["git", *args], capture_output=True, text=True,
            check=False, cwd=cwd, timeout=_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def remote_branch_ref_exists(branch: str, cwd: str | None = None) -> bool:
    """Whether BRANCH has a remote-tracking ref under GIT_REMOTE in `cwd`."""
    ref = f"refs/remotes/{GIT_REMOTE}/{branch}"
    return _git(["show-ref", "--verify", "--quiet", ref], cwd) is not None


def resolve_default_branch(cwd: str | None = None) -> str:
    """The remote's default branch name in `cwd`. Always answers.

    `symbolic-ref`, not `rev-parse --abbrev-ref`: when the symref is missing,
    rev-parse still echoes "origin/HEAD" to stdout before exiting non-zero, so a
    caller stripping the prefix is left holding a non-empty "HEAD" that defeats
    its own fallback.
    """
    prefix = f"refs/remotes/{GIT_REMOTE}/"
    ref = _git(["symbolic-ref", f"{prefix}HEAD"], cwd)
    if ref and ref.startswith(prefix):
        return ref[len(prefix):]

    for candidate in DEFAULT_BRANCH_CANDIDATES:
        if remote_branch_ref_exists(candidate, cwd):
            return candidate
    return DEFAULT_BRANCH_FALLBACK


def default_base_ref(cwd: str | None = None) -> str | None:
    """"GIT_REMOTE/<default branch>" when that ref resolves in `cwd`, else None.

    The fallible half of the pair, for callers about to hand the answer to `git
    diff` or `git merge-base`. Composed from the two above rather than walking
    its own ref list, so "which branch is trunk" is still answered in one place.
    """
    branch = resolve_default_branch(cwd)
    if not remote_branch_ref_exists(branch, cwd):
        return None
    return f"{GIT_REMOTE}/{branch}"
