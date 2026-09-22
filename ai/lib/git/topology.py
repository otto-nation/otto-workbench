"""Which directory holds which branch, and creating one when there is none.

Worktree and bare-repo topology, split out of `pr.context` because the resolver
needs it rather than because it is part of resolving: nothing here reads a
`ResolvedContext`, and every read goes to git or to worktrunk. `pr.sync` is the
other half of that split and points the other way — it takes a resolved context
and acts on it.

The transport is plain `subprocess`: these are local reads with a `timeouts.LOCAL`
bound, and the one unbounded call is `wt switch`, which creates a checkout.
"""

# doc-group: platform

from __future__ import annotations

import functools
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from core import log
from core import timeouts
from core.proc import failure_message

# `git_remote` is a workbench-wide module rather than an `ai/lib` one, because
# the pre-push hooks and the surface gate resolve the same default branch. In a
# checkout that is one directory up; in the otto-ai-tools tarball, which
# flattens both into one `lib/`, it is one directory up from this file too —
# not beside it — and the path below does not exist.
_WORKBENCH_LIB = Path(__file__).resolve().parent.parent.parent.parent / "lib"
if _WORKBENCH_LIB.is_dir() and str(_WORKBENCH_LIB) not in sys.path:
    sys.path.insert(0, str(_WORKBENCH_LIB))
import git_remote  # noqa: E402

RESOLVE_BRANCH = Path(__file__).resolve().parent.parent.parent.parent / "bin" / "resolve-branch"

# Where remote-tracking refs live, spelled once. `stack_parent` both filters on
# this prefix and strips it, and the two have to agree.
REMOTE_REF_PREFIX = f"refs/remotes/{git_remote.GIT_REMOTE}/"


@dataclass(frozen=True)
class WorktreeEntry:
    """One checkout git reported: where it is, and what is checked out in it.

    `branch` is None for a detached HEAD, which is a distinct answer from "no
    entry" — `find_worktree_for_branch` matches a directory name only against
    an entry that reported no branch, so a worktree named `main/` holding
    someone else's branch is never mistaken for the main worktree.
    """

    path: Path
    branch: str | None


def is_bare_repo(cwd: str | None = None) -> bool:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--is-bare-repository"],
            capture_output=True, text=True, cwd=cwd, timeout=timeouts.LOCAL,
        )
        return r.stdout.strip() == "true"
    except Exception:
        return False


def _parse_worktree_block(block: str) -> WorktreeEntry | None:
    """One ``--porcelain`` record as ``(path, branch)``, or None if not a checkout.

    Branch is None for a detached HEAD. The bare repo is not a checkout and is
    dropped — handing it back as one would point callers at the .git directory.
    """
    path: Path | None = None
    branch: str | None = None
    for line in block.splitlines():
        if line == "bare":
            return None
        if line.startswith("worktree "):
            path = Path(line.removeprefix("worktree "))
        elif line.startswith("branch refs/heads/"):
            branch = line.removeprefix("branch refs/heads/")
    return WorktreeEntry(path, branch) if path else None


def worktree_entries(cwd: str | None = None) -> list[WorktreeEntry]:
    """Every non-bare worktree as ``(path, branch)``; branch None when detached.

    Parses ``--porcelain`` rather than the human listing, which packs path,
    SHA and ``[branch]`` onto one whitespace-separated line: a path containing
    a space gets truncated by a naive split, and one containing a bracket reads
    as a branch tag. Porcelain gives the path verbatim on its own line.
    """
    try:
        r = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True, text=True, cwd=cwd, timeout=timeouts.LOCAL,
        )
    except Exception:
        return []
    parsed = map(_parse_worktree_block, r.stdout.split("\n\n"))
    return [entry for entry in parsed if entry]


def find_worktree_for_branch(
    branch: str, cwd: str | None = None,
) -> Path | None:
    """Find the worktree git reports as checked out on *branch*.

    Falls back to a worktree whose directory name matches the sanitized branch
    (slashes to dashes) so detached-HEAD worktrees are still found — but only
    when git reports no branch for it. A worktree named ``main/`` with someone
    else's branch checked out is not the main worktree, and callers that reset
    or check out what they get back would clobber that branch.

    Use find_worktree_dir_named when you only need a working directory.
    """
    entries = worktree_entries(cwd)
    for entry in entries:
        if entry.branch == branch:
            return entry.path
    sanitized = branch.replace("/", "-")
    for entry in entries:
        if entry.branch is None and entry.path.name == sanitized:
            return entry.path
    return None


def find_worktree_dir_named(
    branch: str, cwd: str | None = None,
) -> Path | None:
    """Any worktree whose directory is named after *branch*, whatever is in it.

    Answers "which directory is this repo's <branch> checkout" rather than
    "which worktree is on <branch>". Only for callers that need somewhere to
    run read-only commands — never for ones that reset or check out the result,
    which is how a feature branch parked in main/ got hard-reset away.
    """
    sanitized = branch.replace("/", "-")
    for entry in worktree_entries(cwd):
        if entry.path.name == sanitized:
            return entry.path
    return None


def create_worktree_for_branch(
    branch: str, cwd: str | None = None,
) -> Path | None:
    """Create a worktree for *branch*, or None if it can't be created.

    Delegates to ``wt switch`` so the worktree lands wherever worktrunk's
    path template puts it, keeping tooling-created worktrees in the same
    layout as hand-created ones.
    """
    path = wt_switch(branch, cwd)
    if not path:
        # wt_switch names the cause on every failure path it has; a second,
        # vaguer line here would only bury the one that says something.
        return None
    log.info(f"Created worktree for {branch} at {path}")
    return Path(path)


def wt_switch(ref: str, cwd: str | None = None) -> str | None:
    """Path of the worktree ``wt switch`` lands on for *ref*, or None.

    *ref* is anything worktrunk accepts — a branch name or a ``pr:<n>`` ref.
    Non-interactive and hook-free so it is safe to call from tooling.
    """
    try:
        r = subprocess.run(
            ["wt", "switch", ref, "--no-cd", "--no-hooks", "--format", "json", "-y"]
            + (["-C", cwd] if cwd else []),
            capture_output=True, text=True, timeout=timeouts.UNBOUNDED,
        )
    except FileNotFoundError:
        log.warn("worktrunk (wt) is not installed — cannot switch worktrees")
        return None
    except OSError as e:
        log.warn(f"Cannot run worktrunk (wt) — cannot switch worktrees: {e}")
        return None
    path = parse_wt_switch_path(r.stdout)
    if not path:
        log.warn(failure_message(f"wt switch {ref} reported no worktree path", r))
    return path


def parse_wt_switch_path(stdout: str) -> str | None:
    """Pull the ``path`` field out of ``wt switch --format json`` output."""
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            path = json.loads(line).get("path", "")
        except json.JSONDecodeError:
            continue
        if path:
            return path
    return None


def default_branch(cwd: str | Path | None = None) -> str:
    """The repo's default branch name, via lib/git_remote.py's shared ladder.

    Falls back to "main" whenever git cannot answer — an unfetched clone has no
    origin/HEAD, and every caller needs a base ref more than it needs an error.
    Before the shared ladder this stopped at the symref, so a repository whose
    trunk is `master` was told its default branch was one it does not have.

    Deliberately uncached: this is imported by every `pr` script, and a
    module-level cache here would outlive the tests that set up their own repos.
    Call sites that need it per-file wrap it in their own cache, or use
    :func:`default_branch_cached`.
    """
    return git_remote.resolve_default_branch(str(cwd) if cwd is not None else None)


def _rank_ancestor(ref: str, head: str, cwd: str | None) -> tuple[int, int, str] | None:
    """Sort key for one stack-parent candidate, or None when it cannot be one.

    The first element is the distance from the fork point to HEAD, so the
    nearest ancestor sorts first. The other two break a tie that the distance
    cannot: two refs at the same commit describe the same diff, so which one is
    named is cosmetic — but it has to be the *same* cosmetic answer on every
    machine, and ``for-each-ref`` order is not that. A remote-tracking ref wins
    over a local one because it is the ref the ranges are anchored to anyway,
    and the name settles the rest.

    *head* is passed in rather than read here: it is the same commit for every
    candidate, and resolving it per ref spends a subprocess per branch to learn
    one answer.
    """
    merge_base = _git_out(["merge-base", ref, "HEAD"], cwd)
    if not merge_base:
        return None
    # A candidate containing HEAD is a descendant, not a parent: the branch
    # stacked on *this* one, or this one's own ref under another name. Diffing
    # against it yields nothing, which reads as a review with no changes.
    if merge_base == head:
        return None
    distance = _git_out(["rev-list", "--count", f"{merge_base}..HEAD"], cwd)
    if not distance:
        return None
    return (int(distance), 0 if ref.startswith(REMOTE_REF_PREFIX) else 1, ref)


def stack_parent(cwd: str | None = None, default: str = "") -> str:
    """The branch HEAD is stacked on, or "" when it is stacked on the trunk.

    For a branch whose parent is another feature branch, which the trunk-shaped
    default is wrong about: a review, a rebase or a diff measured against the
    trunk covers the parent's commits as well as this branch's own, so the
    parent's changes are reported as if this branch had made them.

    The answer is a *name*. Callers resolve it to a commit themselves, normally
    as ``origin/<name>``, so a local ref sitting at a stale position can
    nominate a base without being the thing measured against.

    ``--no-merged <default>`` is what keeps this from having a special case for
    the ordinary branch: a branch off the trunk has no ancestor the trunk does
    not already contain, so the candidate set is empty and the caller keeps its
    own default. Only a genuine stack yields anything here.

    Best effort like every other topology read: an unresolvable default, a
    detached HEAD or a git that will not answer all give "", and the caller
    falls back to the base it would have used before asking.
    """
    default = default or default_branch(cwd)
    # An unfetched clone has no such ref, and `for-each-ref` rejects the whole
    # query as a malformed object name rather than ignoring the exclusion — so
    # the empty listing below is also the answer for a repo with no trunk to
    # measure against, which is the right one: without the exclusion every
    # ancestor of HEAD is a candidate and the nearest local branch would win.
    trunk = f"{REMOTE_REF_PREFIX}{default}"

    listing = _git_out(
        ["for-each-ref", "--merged", "HEAD", "--no-merged", trunk,
         "--format=%(refname)", "refs/heads", REMOTE_REF_PREFIX.rstrip("/")],
        cwd,
    )
    if not listing:
        return ""

    # Compared after shortening, which drops `origin/<current>` as well as the
    # local ref. That is the case that matters: a branch with unpushed commits
    # has its own remote-tracking ref sitting closer than its real parent, so
    # ranking it would silently narrow the base to "whatever I have not pushed".
    #
    # None on a detached HEAD, which is not a state to bail out of: the review
    # pins one deliberately for `--recover` and for a PR whose branch is gone.
    # Only `_rank_ancestor`'s at-HEAD check holds there, so a stale ref parked
    # mid-branch can win on distance — the same stale-neighbour limitation an
    # attached HEAD has, minus the one guard that usually hides it. `--base` is
    # the remedy in both cases; a distance floor here would reject the
    # one-commit parent a real stack legitimately has.
    current = current_branch_quiet(cwd)
    head = _git_out(["rev-parse", "HEAD"], cwd)
    if not head:
        return ""

    ranked = []
    for ref in listing.splitlines():
        short = _short_ref(ref)
        if not short or short == current:
            continue
        rank = _rank_ancestor(ref, head, cwd)
        if rank:
            ranked.append((rank, short))
    if not ranked:
        return ""
    return min(ranked)[1]


def _short_ref(ref: str) -> str:
    """``refs/heads/x`` and ``refs/remotes/origin/x`` alike as ``x``.

    Stripped here rather than asked of ``%(refname:short)``, which abbreviates a
    remote-tracking ref to ``origin/x``: the caller wants a branch name it can
    spell as ``origin/<name>`` itself, and one already carrying the remote would
    come back out as ``origin/origin/x``.
    """
    if ref.startswith(REMOTE_REF_PREFIX):
        return ref.removeprefix(REMOTE_REF_PREFIX)
    return ref.removeprefix("refs/heads/")


def _git_out(args: list[str], cwd: str | None = None) -> str:
    """Stripped stdout of a local read, or "" when git did not answer.

    `git.client` is the usual transport for this, but it sits at the same layer
    as this module and the two may not import each other — see the module
    docstring on why the reads here are plain `subprocess`.
    """
    try:
        r = subprocess.run(
            ["git", *args],
            capture_output=True, text=True, cwd=cwd, timeout=timeouts.LOCAL,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


@functools.cache
def default_branch_cached(wt_path: Path) -> str:
    """:func:`default_branch`, memoised for the life of the process.

    The default branch cannot change mid-run and several call sites need it once
    per file processed, so the memo is worth having — but it is a separate name
    rather than a decorator on the resolver above, because the two have
    different contracts and only one of them is safe to hold across repos.

    The cache is process-global and never expires, which is the right trade for
    a short-lived CLI run against one worktree and the wrong one for anything
    long-running: a caller that walks several worktrees in one process, or that
    outlives a branch being renamed underneath it, gets the first answer for
    each path forever. Such a caller wants :func:`default_branch`. A test
    harness exercising several paths with different git state must call
    ``default_branch_cached.cache_clear()`` between cases.
    """
    return default_branch(wt_path)


def find_bare_repo_worktree(
    cwd: str | None, branch: str | None,
) -> Path | None:
    """Worktree discovery for bare repos, creating nothing.

    Tries the requested branch (with fuzzy resolution). Only falls back to the
    default branch's worktree when no branch was requested at all.

    Never substitutes another branch's worktree for an explicitly requested
    branch: callers check the branch out, so handing back the default branch's
    worktree makes them displace it.
    """
    if branch:
        return find_worktree_by_branch(branch, cwd)

    # With no branch requested this is "give me a working directory for this
    # repo", not "is this worktree on <default>" — so the directory named after
    # the default branch will do even when something else is checked out there.
    # Returning None instead would strand ~10 callers that dereference
    # worktree_root without a guard.
    default = default_branch(cwd)
    return (
        find_worktree_for_branch(default, cwd)
        or find_worktree_dir_named(default, cwd)
    )


def resolve_bare_repo_worktree(
    cwd: str | None, branch: str | None,
) -> Path | None:
    """The same discovery, creating a worktree for *branch* when it is missing.

    Creating a checkout is a side effect a caller has to ask for, which is why
    it lives here rather than in find_bare_repo_worktree: a command that only
    reads state should not leave a worktree behind. See create_worktree_for_branch.
    """
    wt = find_bare_repo_worktree(cwd, branch)
    if wt or not branch:
        return wt
    return create_worktree_for_branch(branch, cwd)


def find_worktree_by_branch(
    branch_hint: str, cwd: str,
) -> Path | None:
    """Find a worktree for branch_hint, trying exact then fuzzy resolution."""
    wt = find_worktree_for_branch(branch_hint, cwd)
    if wt:
        return wt
    resolved = resolve_branch(branch_hint, cwd)
    if resolved != branch_hint:
        return find_worktree_for_branch(resolved, cwd)
    return None


def resolve_branch(hint: str, cwd: str | None = None) -> str:
    try:
        r = subprocess.run(
            [str(RESOLVE_BRANCH), hint],
            capture_output=True, text=True, cwd=cwd, timeout=timeouts.LOCAL,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
        # resolve-branch exited non-zero or returned nothing — use hint as-is
        # rather than silently discarding the user's explicit --branch value
        log.warn(failure_message(f"resolve-branch could not resolve {hint!r}", r))
        log.dim(f"using {hint!r} as-is")
        return hint
    # A resolver that hangs is a resolver that did not answer, which this
    # function already knows how to survive.
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return hint if hint else current_branch(cwd)


def current_branch(cwd: str | None = None) -> str:
    r = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, cwd=cwd, timeout=timeouts.LOCAL,
    )
    branch = r.stdout.strip()
    if r.returncode != 0 or not branch:
        log.error(failure_message("Cannot determine current branch", r))
        sys.exit(1)
    if branch == "HEAD":
        log.error("Cannot determine current branch — HEAD is detached")
        sys.exit(1)
    return branch


def current_branch_quiet(cwd: str | None = None) -> str | None:
    """Return current branch name, or None on failure (e.g. detached HEAD)."""
    r = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, cwd=cwd, timeout=timeouts.LOCAL,
    )
    if r.returncode != 0 or not r.stdout.strip() or r.stdout.strip() == "HEAD":
        return None
    return r.stdout.strip()
