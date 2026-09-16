"""Checks that run before a review spends anything.

Three gates, one module, so the unified run flow can call them without
reaching back into the binary. A review is the largest model spend in the
repo; each of these fires before the first agent call.

- ``refuse_if_superseded`` stops a run whose branch may already be gone from
  the default branch — findings about deleted code read as ordinary comments.
- ``check_stale_review`` asks before re-reviewing the same HEAD, or auto-recovers
  when the prior run failed.
- ``check_pending_review`` finds an unsubmitted GitHub review and offers to
  delete it so a new post does not collide.

``--force`` skips the last two. The supersession refusal has its own override
(``supersession_override``) because ``--post``/``--no-post`` also set the
``force`` local that suppresses confirmation prompts, and an unattended run is
the one the refusal most has to survive.
"""

# doc-group: pipeline

from __future__ import annotations

import json
import sys
from pathlib import Path

from gh import client as gh_client
from git import client as git_client
from core import log
from core import prompt
from core.trail import Trail
from pr import domains as pr_domains
from pr import supersession
from review import recover as review_recover
from review.state import read_pipeline_status


def check_pending_review(repo: str, pr_number: str, force: bool) -> None:
    if force:
        return

    gh_user = gh_client.login()
    pending = gh_client.api_json(
        f"repos/{repo}/pulls/{pr_number}/reviews",
        jq=f'[.[] | select(.user.login == "{gh_user}" and .state == "PENDING")] | first // empty',
    )
    # `first // empty` prints nothing when no review matches, so gh's stdout is
    # empty and `api_json` falls back to its default. Anything other than a dict
    # therefore means "no pending review", not a review with no id.
    pending_id = pending.get("id") if isinstance(pending, dict) else None
    if pending_id is None:
        return

    comment_count = gh_client.api(
        f"repos/{repo}/pulls/{pr_number}/reviews/{pending_id}/comments", jq="length",
    ).stdout.strip() or "0"

    log.warn(f"PENDING review exists with {comment_count} comments (not yet submitted)")
    if not prompt.confirm("Delete the pending review before re-reviewing?"):
        log.info("Keeping pending review — new review will replace it at post time")
        return

    deleted = gh_client.api(
        f"repos/{repo}/pulls/{pr_number}/reviews/{pending_id}", method="DELETE",
    )
    if deleted.ok:
        log.info("Deleted pending review")
    else:
        log.warn("Could not delete the pending review — it will be replaced at post time")


def check_stale_review(repo: str, pr_number: str, review_file: Path, force: bool) -> None:
    if force:
        return
    if not review_file.is_file():
        return

    review_sha = review_recover.read_review_sha(review_file)
    if not review_sha:
        return
    pr_head_sha = review_recover.get_pr_head_sha(repo, pr_number)
    if not pr_head_sha:
        return

    if review_sha == pr_head_sha:
        review_dir = review_file.parent
        pipeline_status = read_pipeline_status(review_dir)
        if pipeline_status in (pr_domains.ReviewStatus.PARTIAL.value, pr_domains.ReviewStatus.ERROR.value):
            log.info(
                "Recovering failed review agents "
                f"(HEAD unchanged at {git_client.abbrev(pr_head_sha)})")
            return

        log.warn(
            f"No new commits since the last review (HEAD {git_client.abbrev(pr_head_sha)})")
        if not prompt.confirm("Re-review anyway?"):
            sys.exit(0)
    else:
        log.info(
            "Incremental review: new commits since last review "
            f"({git_client.abbrev(review_sha)}..{git_client.abbrev(pr_head_sha)})")


def supersession_override(user_force: bool, recover: bool) -> bool:
    """Whether this run is exempt from the supersession refusal, and why.

    `user_force` is the raw `--force` flag, deliberately not the `force` local
    the confirmation prompts use. That one also absorbs `--post` and
    `--no-post` as "nobody is here to answer a prompt", and an unattended run
    is the one this refusal most has to survive: there is no operator to notice
    that the findings describe deleted code before they are posted to the PR.

    `--recover` is exempt, on both entry points. It finishes a run whose spend
    has already been made, so refusing it saves nothing and strands the
    artifacts of the run it was asked to complete.
    """
    return bool(user_force or recover)


def refuse_if_superseded(
    wt_path: str, repo: str, target_dir: Path, branch: str, *,
    override: bool, trail: Trail,
) -> None:
    """Stop before the review spends anything on a branch that may be superseded.

    A refusal rather than the hold `pr comments` places, because of where the
    money is. A review is the largest model spend in the repo and this runs
    before the first agent call, so refusing costs nothing and saves all of it;
    holding the posting at the end would save nothing that had not already been
    spent. That is the behavioural difference between the two callers, and it
    is deliberate.

    Findings about code the default branch has already deleted are worse than
    no findings: they read as ordinary review comments, so acting on them
    means fixing code that no longer exists.
    """
    if override:
        return
    verdict = supersession.detect_cached(
        Path(wt_path), repo, target_dir, trail=trail,
    )
    supersession.report(verdict)
    if not verdict.superseded:
        return

    log.error(f"Refusing to review {branch} — it may already be superseded.")
    log.dim("Reviewing a branch that re-adds code the default branch removed "
            "produces findings about code that should not exist.")
    log.dim(f"Pass {supersession.OVERRIDE_FLAG} to review it anyway.")
    trail.decision(
        "supersession_refusal",
        f"refused review — {len(verdict.holding)} supersession signal(s)",
        reason="reviewing code the default branch has already removed asserts it should exist",
        data={"signals": [s.kind for s in verdict.holding]},
    )
    json.dump({
        "branch": branch,
        "status": "superseded",
        "signals": [
            {"kind": s.kind, "detail": s.detail, "holds": s.holds}
            for s in verdict.signals
        ],
        "override": supersession.OVERRIDE_FLAG,
    }, sys.stdout, indent=2)
    print()
    sys.exit(supersession.EXIT_SUPERSEDED)
