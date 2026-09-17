"""Deciding whether a finished review reaches GitHub, and putting it there.

A PR review ends in one of four ways: told not to post, told to post, or asked
and answered either way. A self review ends in none of them — it has no GitHub
review to file — which is why this is a module the PR flow calls rather than a
branch inside a shared one.

Posting returns a `PostResult` rather than printing. The four endings differ in
exactly one thing the summary needs: whether a post session log exists to
aggregate cost from. Returning that instead of printing in each branch is what
lets both flows share a single `print_summary` call.
"""

# doc-group: publishing

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from core import log
from core import prompt
from core import timeouts
from core.serde import load_file as serde_load_file, to_dict as serde_to_dict
from gh import client as gh_client
from pr.state import PostTracking
from review.paths import FILENAME_POST_SESSION


@dataclass(frozen=True)
class PostResult:
    """What posting did, for the summary that follows it.

    `post_session_log` is empty when nothing was posted, which is what
    `print_summary` expects: it aggregates usage from that log when there is
    one, and reports the review's own cost alone when there is not.

    `hints` are the lines printed after the summary rather than before it —
    "post it yourself" advice only makes sense once the operator has seen what
    they are deciding about.
    """

    posted: bool
    submitted: bool
    post_session_log: str
    hints: tuple[str, ...] = ()


_HINT_POST = "Post to GitHub:  pr review --post"


def post(pr_number: str, review_file: str, submit: bool, *, bin_dir: Path) -> None:
    """Hand the review to review-post.

    A spawn rather than a call for the same reason as the orchestrate one, and
    it is #909 tranche 4 that closes both. Worth noting that the return code is
    deliberately ignored: `review-post` exits 1 on a chunk failure having
    already posted earlier chunks, and the review on disk is the deliverable
    either way.
    """
    post_args = [
        str(bin_dir / "review-post"),
        "--pr", pr_number,
        "--review-file", review_file,
    ]
    if submit:
        post_args.append("--submit")
    subprocess.run(post_args, check=False, timeout=timeouts.UNBOUNDED)


def submit_pending(repo: str, pr_number: str, review_file: str) -> bool:
    """Submit a review that was posted as pending. Returns whether it worked.

    Degrades to a message rather than an error when the tracking file is
    missing or unreadable: the review is posted either way, and an unsubmitted
    one is recoverable by hand from the GitHub UI.
    """
    review_dir = Path(review_file).parent
    post_file = review_dir / FILENAME_POST_SESSION
    if not post_file.is_file():
        log.error(f"No post tracking file found: {post_file}")
        return False

    tracking = serde_load_file(PostTracking, post_file)

    if not tracking or not tracking.review_id:
        log.error(f"Could not read review_id from {post_file}")
        return False

    log.info(f"Submitting review #{tracking.review_id}...")
    r = gh_client.api(
        f"repos/{repo}/pulls/{pr_number}/reviews/{tracking.review_id}/events",
        method="POST", raw_fields={"event": "COMMENT"}, retry=False,
    )
    if not r.ok:
        log.warn(f"Failed to submit review #{tracking.review_id}")
        return False

    log.info(f"Review #{tracking.review_id} submitted")

    # Best-effort bookkeeping: the submit call above already reached GitHub,
    # so a failure writing the local tracking file back has nothing left to
    # affect but the next run's own read of it.
    try:
        tracking.submitted = True
        post_file.write_text(json.dumps(serde_to_dict(tracking)))
    except Exception:
        pass
    return True


def resolve(
    repo: str,
    pr_number: str,
    review_file: Path,
    *,
    no_post: bool,
    auto_post: bool,
    auto_submit: bool,
    bin_dir: Path,
) -> PostResult:
    """Post the review, or decide not to, and say which happened.

    Every path returns. Declining to post is not declining to record: the
    review ran and is on disk, so a caller that exited here would leave the
    domain unwritten, and `pr status` would report "not checked: review" for a
    review that did run while `pr fix` skipped the review pass entirely.
    """
    review_dir = review_file.parent
    posted_log = str(review_dir / FILENAME_POST_SESSION)

    if no_post:
        return PostResult(False, False, "", (_HINT_POST,))

    if auto_post:
        post(pr_number, str(review_file), auto_submit, bin_dir=bin_dir)
        return PostResult(True, auto_submit, posted_log)

    if not prompt.confirm("Satisfied with the review?"):
        return PostResult(False, False, "", (
            f"Edit the review:  $EDITOR {review_file}",
            _HINT_POST,
        ))

    if not prompt.confirm("Post review to GitHub?"):
        return PostResult(False, False, "", (_HINT_POST,))

    post(pr_number, str(review_file), False, bin_dir=bin_dir)

    submitted = False
    if prompt.confirm("Submit review now?"):
        submitted = submit_pending(repo, pr_number, str(review_file))

    return PostResult(True, submitted, posted_log)
