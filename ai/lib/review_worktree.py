"""Worktree lifecycle management for claude-review."""

# doc-group: pipeline

from __future__ import annotations

from dataclasses import dataclass

import gh_client
import git_client
import log
import pr_context
import proc


WORKTREE_FALLBACK_DIR = ".worktrees"


@dataclass(frozen=True)
class WorktreeResult:
    path: str
    cleanup_ref: str
    is_fallback: bool


def setup_pr_worktree(repo: str, pr_number: int | str, repo_dir: str, pr_head: str = "") -> WorktreeResult:
    if _is_shallow(repo_dir):
        log.info("Unshallowing repository...")
        git_client.run("fetch", "--unshallow", cwd=repo_dir)

    log.info(f"Setting up worktree for PR #{pr_number}...")

    wt_path = pr_context.wt_switch(f"pr:{pr_number}", repo_dir)
    if wt_path:
        if pr_head:
            pr_context.fetch_and_reset(wt_path, pr_head)
        return WorktreeResult(path=wt_path, cleanup_ref=f"pr:{pr_number}", is_fallback=False)

    log.info("Branch deleted, fetching via PR ref...")
    r = git_client.run("fetch", "origin", f"pull/{pr_number}/head", cwd=repo_dir)
    if not r.ok:
        raise RuntimeError(proc.failure_message(f"Failed to fetch PR #{pr_number} ref", r))

    fallback_path = f"{repo_dir}/{WORKTREE_FALLBACK_DIR}/pr-{pr_number}-review"

    git_client.run("worktree", "remove", "--force", fallback_path, cwd=repo_dir)

    r = git_client.run(
        "worktree", "add", "--detach", fallback_path, "FETCH_HEAD", cwd=repo_dir)
    if not r.ok:
        raise RuntimeError(proc.failure_message(
            f"Failed to create worktree for PR #{pr_number}", r))

    return WorktreeResult(path=fallback_path, cleanup_ref=fallback_path, is_fallback=True)


def detached_worktree_at(sha: str, repo_dir: str, label: str) -> WorktreeResult | None:
    """Create a throwaway detached worktree at *sha*, or None if it is unreachable.

    Used by --recover to pin a partially-completed review to the commit it was
    started from. Detaching leaves every branch ref untouched, so this is safe to
    run against a repo whose worktrees hold the user's live development state.
    """
    if not git_client.commit_exists(sha, cwd=repo_dir):
        # A force-push can leave the recorded commit unreferenced locally while
        # the remote still serves it by SHA, so try one fetch before giving up.
        git_client.run("fetch", "origin", sha, cwd=repo_dir)
        if not git_client.commit_exists(sha, cwd=repo_dir):
            return None

    path = f"{repo_dir}/{WORKTREE_FALLBACK_DIR}/{label.replace('/', '-')}"

    git_client.run("worktree", "remove", "--force", path, cwd=repo_dir)

    if not git_client.run("worktree", "add", "--detach", path, sha, cwd=repo_dir).ok:
        return None

    return WorktreeResult(path=path, cleanup_ref=path, is_fallback=True)


def switch_to_branch(branch: str, repo_dir: str) -> WorktreeResult | None:
    log.info(f"Switching to branch {branch}...")

    wt_path = pr_context.wt_switch(branch, repo_dir)
    if wt_path:
        return WorktreeResult(path=wt_path, cleanup_ref=branch, is_fallback=False)

    sanitized = branch.replace("/", "-")
    fallback_dir = f"{repo_dir}/self-review-{sanitized}"

    git_client.run(
        "fetch", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}",
        cwd=repo_dir)

    git_client.run("worktree", "remove", fallback_dir, "--force", cwd=repo_dir)

    added = git_client.run(
        "worktree", "add", "--detach", fallback_dir, f"origin/{branch}", cwd=repo_dir)
    if not added.ok:
        return None

    return WorktreeResult(path=fallback_dir, cleanup_ref=fallback_dir, is_fallback=True)


def switch_to_pr_branch(pr_number: int | str, repo: str, repo_dir: str) -> WorktreeResult | None:
    pr_head = gh_client.pr_view(pr_number, "headRefName", repo=repo).get("headRefName", "")
    if not pr_head:
        return None

    if git_client.current_branch(cwd=repo_dir) == pr_head:
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
        git_client.run("worktree", "remove", "--force", result.path, cwd=repo_dir)
    except OSError:
        # Cleanup runs on the way out of a failing review, and a second failure
        # here would replace the error the caller is already reporting. A
        # non-zero exit already comes back as a result; only an unusable git
        # raises this far.
        pass


def _is_shallow(repo_dir: str) -> bool:
    return git_client.out("rev-parse", "--is-shallow-repository", cwd=repo_dir) == "true"
