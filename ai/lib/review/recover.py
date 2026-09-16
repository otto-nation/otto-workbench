"""Finish a failed review at the commit it started from, even when HEAD moved.

`--recover` is not a fresh review of whatever the branch is now. The failed run
recorded a SHA in pipeline state, and recovery has to complete that analysis
or it is reviewing different code under the same findings file. These helpers
decide that SHA, detect drift against the worktree HEAD, and pin a throwaway
detached checkout at it.

They compose `review.worktree` rather than duplicating it: pinning is
`detached_worktree_at`, and cleanup of that pin stays with the caller that
owns the `finally`. `pin_recover_worktree` lives here, not in `worktree.py`,
because it is recover-specific — it exits when the recorded commit is gone,
and nothing else in the worktree lifecycle has that contract.
"""

# doc-group: pipeline

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from gh import client as gh_client
from git import client as git_client
from core import log
from pr import context as pr_context
from pr import domains as pr_domains
from review.paths import FILENAME_PIPELINE_STATE
from review.state import read_pipeline_status
from review import worktree as review_worktree


_HEAD_SHA_RE = re.compile(r"<!-- head_sha: ([a-f0-9]+) -->")


def read_review_sha(review_file: Path) -> str:
    try:
        text = review_file.read_text()
    except OSError:
        return ""
    m = _HEAD_SHA_RE.search(text)
    return m.group(1) if m else ""


def get_pr_head_sha(repo: str, pr_number: str) -> str:
    return gh_client.pr_view(pr_number, "headRefOid", repo=repo).get("headRefOid", "")


def resolve_recover_sha(review_dir: Path, head_sha: str) -> str:
    """Return the commit a --recover run must complete against. Exits on failure.

    ``head_sha`` is the current HEAD — the PR head for PR reviews, the local
    worktree HEAD for self-reviews. The returned SHA is the one the failed run
    recorded in its pipeline state, so recovery finishes the analysis it started
    even when the branch has moved on. An empty return means no pinning: either
    HEAD could not be determined or the state predates SHA tracking.
    """
    pipeline_path = review_dir / FILENAME_PIPELINE_STATE
    if not pipeline_path.is_file():
        log.error("No pipeline state found — nothing to recover")
        sys.exit(1)
    if read_pipeline_status(review_dir) == "completed":
        log.info("Nothing to recover — review is complete")
        sys.exit(0)
    try:
        pipeline_data = json.loads(pipeline_path.read_text())
    except (json.JSONDecodeError, OSError):
        log.error(f"Corrupt pipeline state: {review_dir}")
        sys.exit(1)
    recorded_sha = pipeline_data.get("head_sha", "")
    if not recorded_sha:
        return ""
    if head_sha and recorded_sha != head_sha:
        log.warn(
            f"HEAD has moved since the failed review "
            f"({git_client.abbrev(recorded_sha)} → {git_client.abbrev(head_sha)}); "
            f"completing the run at {git_client.abbrev(recorded_sha)} — "
            f"run `pr review` to review the new commits"
        )
    return recorded_sha


def recover_drifted(recover_sha: str, wt_path: str) -> bool:
    """True when the commit being recovered is behind the worktree's HEAD."""
    return bool(recover_sha) and recover_sha != pr_context.head_sha(wt_path)


def pin_recover_worktree(
    recover_sha: str, wt_path: str, repo_dir: str, label: str,
) -> tuple[str, review_worktree.WorktreeResult | None]:
    """Check the recovered commit out into a throwaway worktree when HEAD moved.

    Returns the path the review should run against and the worktree to clean up
    afterwards (None when no pinning was needed).
    """
    if not recover_drifted(recover_sha, wt_path):
        return wt_path, None

    pinned = review_worktree.detached_worktree_at(recover_sha, repo_dir, label)
    if pinned is None:
        log.error(
            f"Commit {git_client.abbrev(recover_sha)} from the failed review is no longer "
            f"available — run `pr review` for a fresh review"
        )
        sys.exit(1)

    return pinned.path, pinned


def should_auto_recover(repo: str, pr_number: str, review_file: Path) -> None:
    """Log recovery intent when HEAD unchanged and prior review had failures."""
    review_sha = read_review_sha(review_file)
    if not review_sha:
        return
    pr_head_sha = get_pr_head_sha(repo, pr_number)
    if not pr_head_sha or review_sha != pr_head_sha:
        return
    pipeline_status = read_pipeline_status(review_file.parent)
    if pipeline_status in (pr_domains.ReviewStatus.PARTIAL.value, pr_domains.ReviewStatus.ERROR.value):
        log.info(
            f"Recovering failed review agents (HEAD unchanged at {git_client.abbrev(review_sha)})")
