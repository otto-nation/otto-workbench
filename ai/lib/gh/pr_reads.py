"""The review system's single-call reads of a PR.

The PR's own metadata, its surrounding conversation, its refs, the diff, the
pending-review check and the new-commit count. Used by the pipeline before any
agent runs, and by review.posting after. Each accepts the consolidated
`gh.pr_data.PRData` where it has one and answers from it without a call.

The consolidated query itself is `gh.pr_data`; the paginated connections and
their page sizes are `gh.pr_pages`.

The transport is not here. ``gh.client`` owns running gh, the timeout tiers and
the rate-limit ladder; this module owns what the review system asks for and how
it reads the answer. Nothing here decides how a call is made, so a change to
retry or to a bound is made once, in the client, for every caller.

Nor is the worktree. A branch with no PR behind it is described from local git
by `review.collect.fetch_branch_metadata`, which reaches the same `PRMetadata`
this fetches — every read here goes to GitHub.
"""

# doc-group: publishing

from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

import gh.client
import gh.pr_data
import git.client
import core.log
import git.numstat
import core.proc
from gh.types import PRContext, PRMetadata


# ── What a review knows about its PR ────────────────────────────────────────

def fetch_pr_metadata(
    repo: str, pr_number: str, pin_sha: str = "", wt_path: str = "",
) -> PRMetadata:
    """Fetch PR metadata, optionally pinned to an earlier commit.

    ``pin_sha`` is the commit a --recover run must complete against; ``wt_path``
    is a checkout of it. Both must be set for pinning to take effect.
    """
    data = gh.client.pr_view(
        pr_number, "title", "body", "headRefName", "baseRefName", "headRefOid",
        "additions", "deletions", "changedFiles", "files",
        "isDraft", "labels", "author",
        repo=repo,
    )
    if not data:
        core.log.error(f"failed to fetch PR #{pr_number} from {repo}")
        sys.exit(1)
    head_sha = data["headRefOid"]
    additions = data["additions"]
    deletions = data["deletions"]
    changed_files = data["changedFiles"]
    files = [
        {"path": f["path"], "additions": f["additions"], "deletions": f["deletions"]}
        for f in data["files"]
    ]

    # --recover completes a run against the commit it started from, so the
    # changeset must come from the pinned checkout rather than the moved PR head.
    if pin_sha and pin_sha != head_sha and wt_path:
        counts = git.numstat.parse_numstat(git.client.out(
            "diff", "--numstat", f"origin/{data['baseRefName']}...HEAD", cwd=wt_path,
        ))
        files = counts.files
        additions = counts.additions
        deletions = counts.deletions
        changed_files = len(files)
        head_sha = pin_sha

    return PRMetadata(
        title=data["title"],
        body=data.get("body") or "",
        head=data["headRefName"],
        base=data["baseRefName"],
        head_sha=head_sha,
        additions=additions,
        deletions=deletions,
        changed_files=changed_files,
        files=files,
        is_draft=data.get("isDraft", False),
        labels=[l["name"] for l in data.get("labels", [])],
        author=(data.get("author") or {}).get("login", ""),
    )


def fetch_pr_context(
    repo: str, pr_number: str, pr_data: gh.pr_data.PRData | None = None,
) -> PRContext:
    """The PR's surrounding conversation — commits, reviews, comments.

    ``pr_data`` is a consolidated query's answer; when it is given, the context
    is read out of it and no call is made.
    """
    if pr_data is not None:
        return gh.pr_data.pr_context_from_data(pr_data)

    cmds = {
        "commits": [
            "pr", "view", pr_number, "--repo", repo,
            "--json", "commits",
            "--jq", '[.commits[] | .messageHeadline] | join("\\n")',
        ],
        "reviews": [
            "api", f"repos/{repo}/pulls/{pr_number}/reviews",
            "--jq", '[.[] | {user: .user.login, state, body}]',
        ],
        "review_comments": [
            "api", f"repos/{repo}/pulls/{pr_number}/comments",
            "--jq", '[.[] | {id, path, line, body, user: .user.login, in_reply_to_id}]',
        ],
        "comments": [
            "api", f"repos/{repo}/issues/{pr_number}/comments",
            "--jq", '[.[] | {user: .user.login, body}]',
        ],
    }
    results = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(gh.client.out, *cmd): name for name, cmd in cmds.items()}
        for future in as_completed(futures):
            results[futures[future]] = future.result()
    return PRContext(
        commits=results["commits"],
        reviews=results["reviews"] or "[]",
        review_comments=results["review_comments"] or "[]",
        comments=results["comments"] or "[]",
    )



# ── PR refs ─────────────────────────────────────────────────────────────────

def _fetch_pr_refs(repo: str, pr: str, pr_data: gh.pr_data.PRData | None = None) -> dict:
    """Fetch the PR's refs (head SHA, head ref, base ref) in one call."""
    if pr_data is not None:
        return {"head_sha": pr_data.head_sha, "head_ref": pr_data.head_ref, "base_ref": pr_data.base_ref}
    r = gh.client.api(f"repos/{repo}/pulls/{pr}")
    if not r.ok:
        core.log.error(core.proc.failure_message(f"Failed to fetch metadata for {repo}#{pr}", r))
        sys.exit(1)
    try:
        data = json.loads(r.stdout)
    except (json.JSONDecodeError, TypeError):
        core.log.error("Failed to parse PR metadata from API response")
        sys.exit(1)
    return {
        "head_sha": data.get("head", {}).get("sha", ""),
        "head_ref": data.get("head", {}).get("ref", ""),
        "base_ref": data.get("base", {}).get("ref", ""),
    }


def _get_diff(repo: str, pr: str) -> str:
    """Get the PR diff. Returns empty string if the diff is unavailable
    (e.g. PRs exceeding GitHub's 300-file limit)."""
    r = gh.client.api(
        f"repos/{repo}/pulls/{pr}",
        headers={"Accept": "application/vnd.github.v3.diff"},
    )
    if not r.ok:
        core.log.warn(core.proc.failure_message(
            "Failed to get diff from API — inline positioning unavailable", r))
        return ""
    return r.stdout


@dataclass(frozen=True)
class PendingReview:
    """The PR's open PENDING review, as the lookup found it.

    ``looked`` is whether the lookup itself succeeded, which is not the same
    question as whether a pending review exists — the distinction
    `MarkerComment.found` already draws for comments, for the same reason. A
    caller that reads a failed listing as "no pending review" opens a second
    one, and GitHub allows a user only one at a time.
    """

    review_id: int | None = None
    looked: bool = True


def _check_existing_pending(
    repo: str, pr: str, pr_data: gh.pr_data.PRData | None = None,
) -> PendingReview:
    """The PR's PENDING review, and whether we managed to ask."""
    if pr_data is not None:
        return PendingReview(pr_data.pending_review_id)
    r = gh.client.api(f"repos/{repo}/pulls/{pr}/reviews")
    if not r.ok:
        # Warned rather than silent: a caller that reads None as "no pending
        # review" opens a second one, and the reason it could not look is the
        # only thing that explains the duplicate.
        core.log.warn(core.proc.failure_message(
            f"Could not check {repo}#{pr} for an existing pending review", r))
        return PendingReview(looked=False)
    try:
        reviews = json.loads(r.stdout)
    except (json.JSONDecodeError, TypeError):
        core.log.warn(f"Could not parse {repo}#{pr}'s reviews — treating the check as unanswered")
        return PendingReview(looked=False)
    for review in reviews:
        if review.get("state") == gh.pr_data.REVIEW_STATE_PENDING:
            return PendingReview(int(review.get("id", 0)) or None)
    return PendingReview()


@dataclass(frozen=True)
class NewCommits:
    """How far the branch moved since the review, as the lookup found it.

    ``counted`` separates "nothing new since the review" from "we could not
    ask", which were both 0. Only the warning told them apart, and a warning is
    not something the code rendering the drift banner can read.
    """

    count: int = 0
    counted: bool = True


def _count_new_commits(
    repo: str, pr: str, review_sha: str, pr_data: gh.pr_data.PRData | None = None,
) -> NewCommits:
    """Commits on the PR since the review SHA, and whether we managed to count."""
    if pr_data is not None:
        return NewCommits(pr_data.new_commit_count(review_sha))
    r = gh.client.api(f"repos/{repo}/pulls/{pr}/commits?per_page=100")
    if not r.ok:
        core.log.warn(core.proc.failure_message(f"Could not count new commits on {repo}#{pr}", r))
        return NewCommits(counted=False)
    try:
        commits = json.loads(r.stdout)
    except (json.JSONDecodeError, TypeError):
        core.log.warn(f"Could not parse {repo}#{pr}'s commits — the drift count is unknown")
        return NewCommits(counted=False)
    for i, c in enumerate(commits):
        sha = c.get("sha", "")
        if sha.startswith(review_sha) or review_sha.startswith(sha):
            return NewCommits(len(commits) - i - 1)
    return NewCommits(len(commits))
