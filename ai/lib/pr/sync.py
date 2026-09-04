"""Bringing a worktree in line with its remote, and the guards that refuse to.

Every read behind the guards fails towards blocked: a `git status` that was
killed or timed out says nothing about the tree, and reading that silence as
clean is how a worktree full of uncommitted work gets reset. The other
direction costs a skipped reset and a logged reason.

Split out of `pr_context` because it is a mutation built on the resolver's
output rather than part of resolving — it takes a `ResolvedContext` and acts on
it, where `git_topology` is the topology the resolver reads on the way in.
"""

# doc-group: pr-state

from __future__ import annotations

import subprocess
from dataclasses import replace as dataclass_replace

from git import topology as git_topology
from core import log
from pr import context as pr_context
from core import timeouts
from core.proc import failure_message


def _reset_guard_read(cwd: str, *args: str) -> subprocess.CompletedProcess:
    """A local git read in *cwd*, with a failure to run reported as a non-zero exit.

    The two reads below guard a `git reset --hard`, and both have to treat "did
    not complete" exactly as they treat "exited non-zero" — a `TimeoutExpired`
    raised out of `subprocess.run` would otherwise abort the command from
    inside a safety check. `proc.run` folds the same exception into a result
    for the callers that go through the git client.
    """
    argv = ["git", "-C", cwd, *args]
    try:
        return subprocess.run(
            argv, capture_output=True, text=True, timeout=timeouts.LOCAL,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(argv, 1, "", f"{type(exc).__name__}: {exc}")


def _worktree_is_dirty(cwd: str) -> bool:
    """Whether *cwd* has uncommitted changes, or git could not say.

    Fails towards dirty, the direction `_unpushed_count` below fails in and for
    the same reason: both answer `_reset_blocker`, which is deciding whether a
    `git reset --hard` would destroy work. See the module docstring for why
    failing towards blocked is the safe direction here.
    """
    r = _reset_guard_read(cwd, "status", "--porcelain")
    if r.returncode != 0:
        log.warn(failure_message(
            f"Could not read the state of {cwd} — treating it as dirty", r,
        ))
        return True
    return bool(r.stdout.strip())


def _unpushed_count(cwd: str, branch: str) -> int | None:
    """Commits in *cwd* that origin/*branch* does not have, or None if unknown.

    Only meaningful once origin/<branch> has been fetched — against a stale
    remote ref this over-reports, which is the safe direction for callers
    using it to decide whether a hard reset would destroy work. None carries
    that same safety into the case over-reporting cannot cover: a `rev-list`
    that never ran counted no commits, and a caller reading that back as zero
    resets over however many there were.
    """
    r = _reset_guard_read(cwd, "rev-list", f"origin/{branch}..HEAD", "--count")
    if r.returncode != 0:
        log.warn(failure_message(f"Could not count unpushed commits in {cwd}", r))
        return None
    count = r.stdout.strip()
    if not count.isdigit():
        log.warn(f"Could not count unpushed commits in {cwd} — git answered {count!r}")
        return None
    return int(count)


def _reset_blocker(wt_path: str, branch: str) -> str | None:
    """Why hard-resetting *wt_path* to origin/*branch* would destroy work.

    Returns None when the reset is safe. Call only after fetching the branch.

    Every read behind it fails towards blocked, so None means the reads agreed
    the worktree holds nothing — not that they were unable to look. The reason
    strings stay true either way, and the read that could not answer has
    already logged git's own account of why.
    """
    current = git_topology.current_branch_quiet(wt_path)
    if current != branch:
        return f"it is on {current or 'detached HEAD'}, not {branch}"
    if _worktree_is_dirty(wt_path):
        return "it has uncommitted changes, or its state could not be read"
    unpushed = _unpushed_count(wt_path, branch)
    if unpushed is None:
        return "its unpushed commits could not be counted"
    if unpushed:
        return f"it has {unpushed} unpushed commit(s)"
    return None


def fetch_and_reset(wt_path: str, branch: str) -> None:
    """Fetch branch from origin and hard-reset worktree to match.

    Skips the reset unless *wt_path* is provably on *branch*, clean, and fully
    pushed — a check git could not complete blocks it too. Callers locate this
    worktree by branch, but find_worktree_for_branch can return one that merely
    has the right directory name — resetting it unconditionally destroys
    whatever is actually checked out there.
    """
    try:
        subprocess.run(
            ["git", "-C", wt_path, "fetch", "origin", branch],
            capture_output=True, text=True, check=True, timeout=timeouts.TRANSFER,
        )
    except Exception:
        return
    blocker = _reset_blocker(wt_path, branch)
    if blocker:
        log.warn(f"Not resetting {wt_path} to origin/{branch} — {blocker}")
        return
    try:
        subprocess.run(
            ["git", "-C", wt_path, "reset", "--hard", f"origin/{branch}"],
            capture_output=True, text=True, timeout=timeouts.UNBOUNDED,
        )
    except Exception:
        pass


def update_to_remote(ctx: pr_context.ResolvedContext) -> pr_context.ResolvedContext:
    """Fetch branch from remote and reset worktree to match, safely.

    Skips when the worktree has uncommitted changes or unpushed commits, and
    equally when git could not report either — a read that failed is not a
    worktree that turned out to be empty. Returns a new context with the
    updated head_sha when reset succeeds.
    """
    if not ctx.worktree_root or not ctx.branch:
        return ctx

    cwd = str(ctx.worktree_root)

    current = git_topology.current_branch_quiet(cwd)
    if current != ctx.branch:
        log.info(f"Worktree is on {current}, not {ctx.branch} — skipping update to remote")
        return ctx

    if _worktree_is_dirty(cwd):
        log.warn(
            "Worktree has uncommitted changes, or its state could not be read "
            "— skipping update to remote"
        )
        return ctx

    r = subprocess.run(
        ["git", "-C", cwd, "fetch", "origin", ctx.branch],
        capture_output=True, text=True, timeout=timeouts.TRANSFER,
    )
    if r.returncode != 0:
        return ctx

    r = subprocess.run(
        ["git", "-C", cwd, "rev-parse", "--verify", f"origin/{ctx.branch}"],
        capture_output=True, text=True, timeout=timeouts.LOCAL,
    )
    if r.returncode != 0:
        return ctx
    remote_sha = r.stdout.strip()

    local_sha = pr_context.head_sha(cwd)
    if local_sha == remote_sha:
        return ctx

    unpushed = _unpushed_count(cwd, ctx.branch)
    if unpushed is None:
        log.dim("skipping update to remote")
        return ctx
    if unpushed > 0:
        log.warn(f"Branch has {unpushed} unpushed commit(s) — skipping update to remote")
        return ctx

    r = subprocess.run(
        ["git", "-C", cwd, "reset", "--hard", f"origin/{ctx.branch}"],
        capture_output=True, text=True, timeout=timeouts.UNBOUNDED,
    )
    if r.returncode != 0:
        log.warn(failure_message(f"git reset --hard origin/{ctx.branch} failed", r))
        log.dim("keeping the existing worktree state")
        return ctx

    log.info(f"Updated worktree to origin/{ctx.branch}")
    return dataclass_replace(ctx, head_sha=remote_sha)
