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

RESOLVE_BRANCH = Path(__file__).resolve().parent.parent.parent.parent / "bin" / "resolve-branch"


@dataclass(frozen=True)
class ResolvedContext:
    """Immutable PR context resolved once at command entry."""
    repo: str
    branch: str
    pr_number: int | None
    worktree_root: Path
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
    """
    cwd = repo_dir or None

    worktree_root = _git_toplevel(cwd)
    repo = _detect_repo(cwd)
    head_sha = _head_sha(cwd)

    if pr:
        pr_number = _parse_pr_input(pr)
        branch_name = _branch_from_pr(repo, pr_number) or _current_branch(cwd)
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


def _detect_repo(cwd: str | None = None) -> str:
    r = subprocess.run(
        ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
        capture_output=True, text=True, cwd=cwd,
    )
    if r.returncode != 0 or not r.stdout.strip():
        print("Cannot determine repository from git remote", file=sys.stderr)
        sys.exit(1)
    return r.stdout.strip()


def _current_branch(cwd: str | None = None) -> str:
    r = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, cwd=cwd,
    )
    if r.returncode != 0 or not r.stdout.strip():
        print("Cannot determine current branch", file=sys.stderr)
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
        print(f"resolve-branch: could not resolve {hint!r}, using as-is", file=sys.stderr)
        return hint
    except FileNotFoundError:
        # resolve-branch script not installed — fall back to current branch
        return _current_branch(cwd)


def _git_toplevel(cwd: str | None = None) -> Path:
    r = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, cwd=cwd,
    )
    if r.returncode != 0:
        print("Not in a git repository", file=sys.stderr)
        sys.exit(1)
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
