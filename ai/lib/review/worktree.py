"""Worktree lifecycle for a review run: obtain one, pin one, release one.

The primitives (`setup_pr_worktree`, `detached_worktree_at`, `switch_to_branch`,
`cleanup_worktree`) create and destroy checkouts. The resolvers below compose
those primitives rather than duplicating them: they answer *which directory*
a review should run in, given a slug, a branch name, or a `--repo-dir`.

That is a different question from `git.topology`, which starts from a git cwd
and asks which of *its* worktrees holds a branch. Topology has no GitHub-slug
lookup, no `~/git` walk, and no `gh`. The slug-to-clone path is `find_repo_root`.
"""

# doc-group: pipeline

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass

from gh import client as gh_client
from git import client as git_client
from git import topology as git_topology
from core import log
from core import timeouts
from pr import sync as pr_sync
from core import proc


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

    wt_path = git_topology.wt_switch(f"pr:{pr_number}", repo_dir)
    if wt_path:
        if pr_head:
            pr_sync.fetch_and_reset(wt_path, pr_head)
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

    wt_path = git_topology.wt_switch(branch, repo_dir)
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


def cleanup_self_review_worktree(wt_cleanup: WorktreeResult | None, repo_dir: str) -> None:
    # Runs from a `finally` that may already be unwinding the failure the
    # operator needs to see, so raising here would replace it with something
    # about git. Leaving the worktree behind is the lesser loss.
    try:
        original_repo_dir = repo_dir or git_client.out("rev-parse", "--show-toplevel")
    except OSError:
        return
    if original_repo_dir:
        cleanup_worktree(wt_cleanup, original_repo_dir)


def resolve_wt_path(repo_dir: str, branch: str) -> str:
    cwd = repo_dir or None
    try:
        toplevel = git_client.out("rev-parse", "--show-toplevel", cwd=cwd)
    except OSError:
        toplevel = ""
    if toplevel:
        return toplevel

    if not repo_dir and git_topology.is_bare_repo(cwd):
        # branch or None: an empty string is "no branch requested", which
        # topology spells as None — passed through it would be a request for a
        # branch named "".
        wt = git_topology.resolve_bare_repo_worktree(None, branch or None)
        if wt:
            return str(wt)
        display_branch = branch or git_topology.default_branch()
        log.error(f"Bare repository — no worktree found for {display_branch}. Pass --repo-dir to specify a worktree")
        sys.exit(1)

    if repo_dir:
        log.error(f"Not a git repository: {repo_dir}")
    else:
        log.error("Not in a git repository — pass --repo-dir to specify the repo")
    sys.exit(1)


def resolve_branch_input(pr_input: str, repo_dir: str) -> str:
    resolve_dir = repo_dir or "."
    try:
        r = subprocess.run(
            ["resolve-branch", pr_input],
            capture_output=True, text=True, cwd=resolve_dir, timeout=timeouts.LOCAL,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        # resolve-branch is a convenience lookup, not a requirement — a
        # missing binary, a timeout, or any other failure to run it falls
        # back to treating pr_input as the branch name it already might be.
        pass
    return pr_input


def find_repo_root(repo: str, explicit_dir: str = "") -> str:
    """Resolve a GitHub slug to a local clone directory.

    ``git.topology`` answers a different question — given a git cwd, which
    worktree holds this branch. It has no slug lookup, no ``~/git`` walk, and
    no ``gh``. This function is the one that starts from ``owner/name`` and
    finds a checkout the process can run in, which is what issue-provider
    resolution at a PR review needs when the caller is not already inside the
    clone.

    Order: ``explicit_dir`` if it is a directory; else the current git toplevel
    when its basename (or its parent's — the bare-container heuristic) matches
    the slug's repo name; else ``gh repo view`` plus a depth-2 walk of
    ``~/git``. Returns "" when nothing matches.
    """
    repo_name = repo.split("/")[-1]

    if explicit_dir:
        if os.path.isdir(explicit_dir):
            return explicit_dir
        return ""

    # An unusable git has to fall through to the gh and find lookups below,
    # which are guarded the same way — a non-zero exit already does, but an
    # absent binary raises out of subprocess. Caught broadly as OSError (not
    # just FileNotFoundError, as retro-scan and reuse-session-start do for the
    # same call) because a permissions error on the git binary should fall
    # through here too, not surface as an unhandled exception.
    try:
        git_toplevel = git_client.out("rev-parse", "--show-toplevel")
    except OSError:
        git_toplevel = ""

    if git_toplevel:
        if os.path.basename(git_toplevel) == repo_name:
            return git_toplevel
        parent_dir = os.path.dirname(git_toplevel)
        if os.path.basename(parent_dir) == repo_name:
            return parent_dir

    name = gh_client.out("repo", "view", repo, "--json", "name", "--jq", ".name")
    if not name:
        return ""

    home_git = os.path.expanduser("~/git")
    try:
        r2 = subprocess.run(
            ["find", home_git, "-maxdepth", "2", "-name", name, "-type", "d"],
            capture_output=True, text=True, timeout=timeouts.LOCAL,
        )
        found = r2.stdout.strip().splitlines()
    except (subprocess.TimeoutExpired, OSError):
        # A missing `find` binary, a timeout, or any other failure to run it
        # means no match — the caller's "" return already covers that case.
        found = []
    return found[0] if found else ""
