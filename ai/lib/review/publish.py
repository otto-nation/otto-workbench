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
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import core.log
import core.prompt
import core.publishing
from core.serde import load_file as serde_load_file
import gh.client
import git.client
from gh.pr_data import fetch_pr_data
from pr.state import PostTracking, post_event_for_github_state
from review.paths import FILENAME_POST_SESSION, FILENAME_RECORD
from review.post_file import event_refusal, head_moved
from review.posting import ReviewEvent, write_post_tracking


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
    published: bool
    post_session_log: str
    hints: tuple[str, ...] = ()


_HINT_POST = "Post a draft to GitHub:  pr review --post"


def post(pr_number: str, review_file: str, *, bin_dir: Path,
         branch: str = "") -> None:
    """Hand the review to review-post, in this process, as a draft.

    The exit code is deliberately ignored, and that predates the call:
    `review-post` fails on a chunk having already posted the earlier ones, and
    the review on disk is the deliverable either way.

    `branch`, when known, is passed through as `--expect-ref` so review-post
    can tell this run's review from one written by a run the lock never made
    contend with it — `cli.review_modes.post` is the sibling call site this
    mirrors.

    `bin_dir` is unused here now and kept, because it is not this function's
    to drop: it arrives from `ReviewFlags`, where `review.invoke` still needs
    it to build the orchestrate argv, and `land` passes the same value to
    both. Removing it from one leaf does not remove the field.
    """
    post_args = ["--pr", pr_number, "--review-file", review_file]
    if branch:
        post_args += ["--expect-ref", branch]
    core.publishing.call_entry_point("cli.review_post:main", post_args)


class DraftPublish(StrEnum):
    """What publishing a recorded draft did."""

    PUBLISHED = "published"
    NO_DRAFT = "no_draft"
    NOT_PENDING = "not_pending"
    HEAD_MOVED = "head_moved"
    FAILED = "failed"

    @property
    def ok(self) -> bool:
        """The draft is now a published review on GitHub."""
        return self is DraftPublish.PUBLISHED


_NO_DRAFT = (
    "No draft of this review on GitHub — post one with: pr review --post"
)
_NOT_PENDING = (
    "The draft is no longer pending on GitHub (published or deleted there)"
)


def _record_if_github_submitted(
    repo: str, pr_number: str, review_file: str, tracking: PostTracking,
) -> None:
    """Best-effort: if GitHub already published this review, record that."""
    r = gh.client.api(
        f"repos/{repo}/pulls/{pr_number}/reviews/{tracking.review_id}",
        method="GET", retry=False,
    )
    if not r.ok:
        return
    try:
        payload = json.loads(r.stdout)
    except (json.JSONDecodeError, TypeError):
        return
    mapped = post_event_for_github_state(payload.get("state", ""))
    if mapped is None:
        return
    tracking.status = mapped.value
    tracking.submitted = True
    write_post_tracking(review_file, tracking)


def publish_draft(
    repo: str, pr_number: str, review_file: str, event: ReviewEvent,
) -> DraftPublish:
    """Publish the draft recorded in post.jsonl as *event*."""
    tracking = serde_load_file(
        PostTracking, Path(review_file).parent / FILENAME_POST_SESSION,
    )
    if not tracking or not tracking.pending or not tracking.review_id:
        core.log.error(_NO_DRAFT)
        return DraftPublish.NO_DRAFT

    try:
        pr_data = fetch_pr_data(repo, pr_number)
    except SystemExit:
        return DraftPublish.FAILED

    if pr_data.pending_review_id != tracking.review_id:
        core.log.error(_NOT_PENDING)
        _record_if_github_submitted(repo, pr_number, review_file, tracking)
        return DraftPublish.NOT_PENDING

    recorded = tracking.head_sha_at_post or tracking.commit_id
    head = pr_data.head_sha
    if not recorded or head_moved(recorded, head):
        core.log.error(
            f"The PR head moved since the draft was posted "
            f"({git.client.abbrev(recorded)} → {git.client.abbrev(head)}) — "
            f"re-run the review and post again"
        )
        return DraftPublish.HEAD_MOVED
    refusal = event_refusal(event, tracking.review_sha, head)
    if refusal:
        core.log.error(refusal)
        return DraftPublish.HEAD_MOVED

    r = gh.client.api(
        f"repos/{repo}/pulls/{pr_number}/reviews/{tracking.review_id}/events",
        method="POST",
        input_text=json.dumps({"event": event.github}),
        retry=False,
    )
    if not r.ok:
        core.log.warn(f"Failed to publish review #{tracking.review_id}")
        return DraftPublish.FAILED

    tracking.submitted = True
    tracking.status = event.post_event.value
    write_post_tracking(review_file, tracking)
    core.log.info(f"Review #{tracking.review_id} published as {event.value}")
    return DraftPublish.PUBLISHED


def resolve(
    repo: str,
    pr_number: str,
    review_file: Path,
    *,
    no_post: bool,
    auto_post: bool,
    bin_dir: Path,
    branch: str = "",
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
        post(pr_number, str(review_file), bin_dir=bin_dir, branch=branch)
        return PostResult(True, False, posted_log)

    if not core.prompt.confirm("Satisfied with the review?"):
        return PostResult(False, False, "", (
            f"Edit the record:  $EDITOR {review_dir / FILENAME_RECORD}",
            _HINT_POST,
        ))

    if not core.prompt.confirm("Post the review to GitHub as a draft?"):
        return PostResult(False, False, "", (_HINT_POST,))

    post(pr_number, str(review_file), bin_dir=bin_dir, branch=branch)

    published = False
    if core.prompt.confirm("Publish the draft now as a comment?"):
        published = publish_draft(
            repo, pr_number, str(review_file), ReviewEvent.COMMENT,
        ).ok

    return PostResult(True, published, posted_log)
