"""Shared PR context resolution.

Resolves repo, branch, PR number, worktree root, and HEAD SHA once
per invocation. Replaces the duplicated discovery logic in ci-check,
review-threads, and review_common.detect_repo().
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import log

RESOLVE_BRANCH = Path(__file__).resolve().parent.parent.parent.parent / "bin" / "resolve-branch"


@dataclass(frozen=True)
class ResolvedContext:
    """Immutable PR context resolved once at command entry."""
    repo: str
    branch: str
    pr_number: int | None
    worktree_root: Path | None
    head_sha: str


def resolve(
    *,
    pr: str | None = None,
    branch: str | None = None,
    repo_dir: str | None = None,
) -> ResolvedContext:
    """Resolve PR context from arguments and git state.

    Resolution order:
    1. --pr given: derive branch and repo from the PR.
    2. --branch given: use directly, detect repo from remote.
    3. Neither: detect everything from current git state.

    Raises ValueError if both pr and branch are given.
    """
    if pr is not None and branch is not None:
        raise ValueError("--pr and --branch are mutually exclusive")

    cwd = repo_dir

    worktree_root, cwd = _resolve_worktree(cwd, pr=pr, branch=branch)

    repo = _detect_repo(cwd)
    head_sha = _head_sha(cwd) if worktree_root else ""

    if pr:
        pr_number = _parse_pr_input(pr)
        branch_name = _branch_from_pr(repo, pr_number)
        if not branch_name:
            branch_name = _current_branch(cwd) if worktree_root else ""
    elif branch:
        branch_name = _resolve_branch(branch, cwd)
        pr_number = _pr_from_branch(repo, branch_name)
    else:
        branch_name = _current_branch(cwd)
        pr_number = _pr_from_current(cwd)

    return ResolvedContext(
        repo=repo,
        branch=branch_name,
        pr_number=pr_number,
        worktree_root=worktree_root,
        head_sha=head_sha,
    )


def fetch_and_reset(wt_path: str, branch: str) -> None:
    """Fetch branch from origin and hard-reset worktree to match."""
    try:
        subprocess.run(
            ["git", "-C", wt_path, "fetch", "origin", branch],
            capture_output=True, text=True, check=True,
        )
    except Exception:
        return
    try:
        subprocess.run(
            ["git", "-C", wt_path, "reset", "--hard", f"origin/{branch}"],
            capture_output=True, text=True,
        )
    except Exception:
        pass


def update_to_remote(ctx: ResolvedContext) -> ResolvedContext:
    """Fetch branch from remote and hard-reset worktree to match.

    Returns a new context with the updated head_sha.
    No-op when there is no worktree or no branch to update.
    """
    if not ctx.worktree_root or not ctx.branch:
        return ctx

    cwd = str(ctx.worktree_root)
    local_sha = _head_sha(cwd)

    fetch_and_reset(cwd, ctx.branch)

    new_sha = _head_sha(cwd)
    if new_sha == local_sha:
        return ctx

    log.info(f"Updated worktree to origin/{ctx.branch}")
    return ResolvedContext(
        repo=ctx.repo,
        branch=ctx.branch,
        pr_number=ctx.pr_number,
        worktree_root=ctx.worktree_root,
        head_sha=new_sha,
    )


def _current_branch_quiet(cwd: str | None = None) -> str | None:
    """Return current branch name, or None on failure (e.g. detached HEAD)."""
    r = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, cwd=cwd,
    )
    if r.returncode != 0 or not r.stdout.strip() or r.stdout.strip() == "HEAD":
        return None
    return r.stdout.strip()


def _find_worktree_by_branch(
    branch_hint: str, cwd: str,
) -> Path | None:
    """Find a worktree for branch_hint, trying exact then fuzzy resolution."""
    wt = find_worktree_for_branch(branch_hint, cwd)
    if wt:
        return wt
    resolved = _resolve_branch(branch_hint, cwd)
    if resolved != branch_hint:
        return find_worktree_for_branch(resolved, cwd)
    return None


def _redirect_to_branch_worktree(
    branch: str, effective_cwd: str,
) -> Path | None:
    """If CWD's branch differs from the target, find the target's worktree."""
    current = _current_branch_quiet(effective_cwd)
    if current is None or current == branch:
        return None
    return _find_worktree_by_branch(branch, effective_cwd)


def _resolve_worktree(
    cwd: str | None,
    *,
    pr: str | None,
    branch: str | None,
) -> tuple[Path | None, str | None]:
    """Resolve worktree root, handling bare repos transparently."""
    toplevel = _git_toplevel(cwd)
    if toplevel is None:
        return _resolve_non_worktree(cwd, pr=pr, branch=branch)

    if branch:
        wt = _redirect_to_branch_worktree(branch, cwd or str(toplevel))
        if wt:
            return wt, str(wt)
    return toplevel, cwd


def _resolve_non_worktree(
    cwd: str | None,
    *,
    pr: str | None,
    branch: str | None,
) -> tuple[Path | None, str | None]:
    """Handle bare repos and non-git directories."""
    if is_bare_repo(cwd):
        return _resolve_bare(cwd, pr=pr, branch=branch)

    if not pr and not branch:
        log.error("Not in a git repository")
        sys.exit(1)
    return None, cwd


def _resolve_bare(
    cwd: str | None,
    *,
    pr: str | None,
    branch: str | None,
) -> tuple[Path | None, str | None]:
    """Resolve worktree from a bare repo."""
    wt = resolve_bare_repo_worktree(cwd, branch)
    if wt:
        return wt, str(wt)
    if not pr and not branch:
        log.error("Bare repository — pass --branch or --repo-dir")
        sys.exit(1)
    return None, cwd


def _detect_repo(cwd: str | None = None) -> str:
    r = subprocess.run(
        ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
        capture_output=True, text=True, cwd=cwd,
    )
    if r.returncode != 0 or not r.stdout.strip():
        log.error("Cannot determine repository from git remote")
        sys.exit(1)
    return r.stdout.strip()


def _current_branch(cwd: str | None = None) -> str:
    r = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, cwd=cwd,
    )
    if r.returncode != 0 or not r.stdout.strip():
        log.error("Cannot determine current branch")
        sys.exit(1)
    return r.stdout.strip()


def _resolve_branch(hint: str, cwd: str | None = None) -> str:
    try:
        r = subprocess.run(
            [str(RESOLVE_BRANCH), hint],
            capture_output=True, text=True, cwd=cwd,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
        # resolve-branch exited non-zero or returned nothing — use hint as-is
        # rather than silently discarding the user's explicit --branch value
        log.warn(f"resolve-branch: could not resolve {hint!r}, using as-is")
        return hint
    except FileNotFoundError:
        return hint if hint else _current_branch(cwd)


def _git_toplevel(cwd: str | None = None) -> Path | None:
    r = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, cwd=cwd,
    )
    if r.returncode != 0:
        return None
    return Path(r.stdout.strip())


def _head_sha(cwd: str | None = None) -> str:
    r = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True, text=True, cwd=cwd,
    )
    return r.stdout.strip()


def _parse_pr_input(pr_input: str) -> int:
    """Extract PR number from URL or raw number."""
    if "/" in pr_input:
        parts = pr_input.rstrip("/").split("/")
        return int(parts[-1])
    return int(pr_input)


def _pr_from_current(cwd: str | None = None) -> int | None:
    r = subprocess.run(
        ["gh", "pr", "view", "--json", "number", "-q", ".number"],
        capture_output=True, text=True, cwd=cwd,
    )
    if r.returncode != 0:
        return None
    try:
        return int(r.stdout.strip())
    except ValueError:
        return None


def _pr_from_branch(repo: str, branch: str) -> int | None:
    r = subprocess.run(
        ["gh", "pr", "list", "--repo", repo, "--head", branch,
         "--json", "number", "--jq", ".[0].number"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return None
    try:
        return int(r.stdout.strip())
    except ValueError:
        return None


def _branch_from_pr(repo: str, pr_number: int) -> str | None:
    r = subprocess.run(
        ["gh", "pr", "view", str(pr_number), "--repo", repo,
         "--json", "headRefName", "-q", ".headRefName"],
        capture_output=True, text=True,
    )
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip()
    return None


# ── Bare-repo helpers ──────────────────────────────────────────────────────


def is_bare_repo(cwd: str | None = None) -> bool:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--is-bare-repository"],
            capture_output=True, text=True, cwd=cwd,
        )
        return r.stdout.strip() == "true"
    except Exception:
        return False


def find_worktree_for_branch(
    branch: str, cwd: str | None = None,
) -> Path | None:
    """Find the worktree directory checked out on *branch*."""
    try:
        r = subprocess.run(
            ["git", "worktree", "list"],
            capture_output=True, text=True, cwd=cwd,
        )
    except Exception:
        return None
    for line in r.stdout.splitlines():
        if f"[{branch}]" in line:
            return Path(line.split()[0])
    return None


def resolve_bare_repo_worktree(
    cwd: str | None, branch: str | None,
) -> Path | None:
    """Best-effort worktree discovery for bare repos.

    Tries the requested branch first, then the default branch.
    """
    if branch:
        wt = find_worktree_for_branch(branch, cwd)
        if wt:
            return wt

    default_branch = "main"
    try:
        r = subprocess.run(
            ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
            capture_output=True, text=True, cwd=cwd,
        )
        ref = r.stdout.strip()
        if ref:
            default_branch = ref.replace("refs/remotes/origin/", "")
    except Exception:
        pass

    return find_worktree_for_branch(default_branch, cwd)
