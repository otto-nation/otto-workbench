"""Worktree lifecycle management for claude-review."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

import log
import pr_context


WORKTREE_FALLBACK_DIR = ".worktrees"


@dataclass(frozen=True)
class WorktreeResult:
    path: str
    cleanup_ref: str
    is_fallback: bool


def setup_pr_worktree(repo: str, pr_number: int | str, repo_dir: str, pr_head: str = "") -> WorktreeResult:
    if _is_shallow(repo_dir):
        log.info("Unshallowing repository...")
        try:
            subprocess.run(
                ["git", "-C", repo_dir, "fetch", "--unshallow"],
                capture_output=True, text=True,
            )
        except Exception:
            pass

    log.info(f"Setting up worktree for PR #{pr_number}...")

    wt_path = _wt_switch(f"pr:{pr_number}", repo_dir)
    if wt_path:
        if pr_head:
            pr_context.fetch_and_reset(wt_path, pr_head)
        return WorktreeResult(path=wt_path, cleanup_ref=f"pr:{pr_number}", is_fallback=False)

    log.info("Branch deleted, fetching via PR ref...")
    try:
        subprocess.run(
            ["git", "-C", repo_dir, "fetch", "origin", f"pull/{pr_number}/head"],
            capture_output=True, text=True, check=True,
        )
    except subprocess.CalledProcessError:
        raise RuntimeError(f"Failed to fetch PR #{pr_number} ref")

    fallback_path = f"{repo_dir}/{WORKTREE_FALLBACK_DIR}/pr-{pr_number}-review"

    try:
        subprocess.run(
            ["git", "-C", repo_dir, "worktree", "remove", "--force", fallback_path],
            capture_output=True, text=True,
        )
    except Exception:
        pass

    result = subprocess.run(
        ["git", "-C", repo_dir, "worktree", "add", "--detach", fallback_path, "FETCH_HEAD"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to create worktree for PR #{pr_number}")

    return WorktreeResult(path=fallback_path, cleanup_ref=fallback_path, is_fallback=True)


def detached_worktree_at(sha: str, repo_dir: str, label: str) -> WorktreeResult | None:
    """Create a throwaway detached worktree at *sha*, or None if it is unreachable.

    Used by --recover to pin a partially-completed review to the commit it was
    started from. Detaching leaves every branch ref untouched, so this is safe to
    run against a repo whose worktrees hold the user's live development state.
    """
    if not _has_commit(sha, repo_dir):
        # A force-push can leave the recorded commit unreferenced locally while
        # the remote still serves it by SHA, so try one fetch before giving up.
        subprocess.run(
            ["git", "-C", repo_dir, "fetch", "origin", sha],
            capture_output=True, text=True,
        )
        if not _has_commit(sha, repo_dir):
            return None

    path = f"{repo_dir}/{WORKTREE_FALLBACK_DIR}/{label.replace('/', '-')}"

    subprocess.run(
        ["git", "-C", repo_dir, "worktree", "remove", "--force", path],
        capture_output=True, text=True,
    )

    result = subprocess.run(
        ["git", "-C", repo_dir, "worktree", "add", "--detach", path, sha],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return None

    return WorktreeResult(path=path, cleanup_ref=path, is_fallback=True)


def switch_to_branch(branch: str, repo_dir: str) -> WorktreeResult | None:
    log.info(f"Switching to branch {branch}...")

    wt_path = _wt_switch(branch, repo_dir)
    if wt_path:
        return WorktreeResult(path=wt_path, cleanup_ref=branch, is_fallback=False)

    sanitized = branch.replace("/", "-")
    fallback_dir = f"{repo_dir}/self-review-{sanitized}"

    try:
        subprocess.run(
            ["git", "-C", repo_dir, "fetch", "origin",
             f"+refs/heads/{branch}:refs/remotes/origin/{branch}"],
            capture_output=True, text=True,
        )
    except Exception:
        pass

    try:
        subprocess.run(
            ["git", "-C", repo_dir, "worktree", "remove", fallback_dir, "--force"],
            capture_output=True, text=True,
        )
    except Exception:
        pass

    result = subprocess.run(
        ["git", "-C", repo_dir, "worktree", "add", "--detach", fallback_dir, f"origin/{branch}"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return None

    return WorktreeResult(path=fallback_dir, cleanup_ref=fallback_dir, is_fallback=True)


def switch_to_pr_branch(pr_number: int | str, repo: str, repo_dir: str) -> WorktreeResult | None:
    try:
        gh_result = subprocess.run(
            ["gh", "pr", "view", str(pr_number), "--repo", repo,
             "--json", "headRefName", "--jq", ".headRefName"],
            capture_output=True, text=True,
        )
        pr_head = gh_result.stdout.strip()
    except Exception:
        pr_head = ""

    if not pr_head:
        return None

    try:
        git_result = subprocess.run(
            ["git", "-C", repo_dir, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True,
        )
        current_branch = git_result.stdout.strip()
    except Exception:
        current_branch = ""

    if current_branch == pr_head:
        return None

    return switch_to_branch(pr_head, repo_dir)


def cleanup_worktree(result: WorktreeResult | None, repo_dir: str) -> None:
    if result is None:
        return

    # Only clean up temporary fallback worktrees created for the review.
    # Non-fallback worktrees are the user's development worktrees — leave them alone.
    if not result.is_fallback:
        return

    try:
        subprocess.run(
            ["git", "-C", repo_dir, "worktree", "remove", "--force", result.path],
            capture_output=True, text=True,
        )
    except Exception:
        pass


def _has_commit(sha: str, repo_dir: str) -> bool:
    result = subprocess.run(
        ["git", "-C", repo_dir, "cat-file", "-e", f"{sha}^{{commit}}"],
        capture_output=True, text=True,
    )
    return result.returncode == 0


def _is_shallow(repo_dir: str) -> bool:
    try:
        result = subprocess.run(
            ["git", "-C", repo_dir, "rev-parse", "--is-shallow-repository"],
            capture_output=True, text=True,
        )
        return result.stdout.strip() == "true"
    except Exception:
        return False


def _wt_switch(ref: str, repo_dir: str) -> str | None:
    return pr_context.wt_switch(ref, repo_dir)


