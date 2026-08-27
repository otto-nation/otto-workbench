"""PR comments lifecycle tracking.

Handles thread lifecycle state computation and GitHub data fetching for the
pr-comments skill. The ledger those threads are recorded in, and the file it
lives in, belong to `pr_comments_state`.

A thread's lifecycle state is what decides whether the run may report itself
done. `--post` is a request, not a guarantee: if triage routes any thread to
`needs_human` — contested, conflicting, a question, or too complex to
auto-fix — the fix pass *holds* publishing for the rest of the process, and the
hold outranks `--post`. Nothing reopens it (see `publishing`).

The fixes still get applied and still get committed. What waits is everything
that asserts the work is done: the push, the `Fixed in <sha>` replies, the
thread resolutions, and the summary. The commit sits locally with status
`push_held`, and `--finish --post` is what sends it:

```bash
pr comments --fix --post   # commits; holds the push, one thread is contested
# read the thread, answer the reviewer
pr comments --finish --post   # pushes, then drains the replies and the summary
```

Until that second command runs, the queue sits in state and the PR shows
nothing — an undelivered summary is indistinguishable from a run that had
nothing to say. `pr status` names it (`⚠ closeout owed: summary + 15 replies`)
and counts it as a merge blocker, so the hold survives the session that created
it.

This exists because threads are triaged independently. A reviewer saying "the
root cause you describe does not exist" removes that one thread from the
fixable set and leaves the pass free to fix, push, and report success on
everything else — 8 individually-real fixes pushed to a branch that had already
been superseded.

The halt is deliberately blunt: any open thread, not just a premise-invalidating
one. Telling those apart is the hard classification problem, and the cost of
being wrong is asymmetric — a needless hold costs one extra command, while a
missed one costs a pushed commit and a reply claiming work is done. Running
`--fix` and `--finish` in the same invocation does not defeat it: the discussion
is still open at both points, so the hold applies to both.
"""

# doc-group: publishing

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import gh_client
import log
import publishing
from pr_comments_state import ThreadRecord, ThreadState
from proc import CmdResult
from review_github import PRData, fetch_review_threads
from text import plural


# ── Thread lifecycle states ────────────────────────────────────────────────

_ACK_WORDS = {"done", "lgtm", "looks good", "thanks", "thank you", "fixed", "nice", "great", "sounds good", "perfect", "agreed", "makes sense"}
_ACK_EMOJI = {"👍", "✅", ":thumbsup:", ":white_check_mark:"}
_PUSHBACK_WORDS = {"still", "but", "however", "actually", "i think we should", "not quite", "doesn't address"}

ACK_MAX_LEN = 100


def _is_acknowledgment(body: str) -> bool:
    """Check if a reply body looks like an acknowledgment."""
    lower = body.lower().strip()
    if len(lower) > ACK_MAX_LEN:
        return False
    for word in _ACK_WORDS:
        if word in lower:
            return True
    for emoji in _ACK_EMOJI:
        if emoji in body:
            return True
    return False


def _is_pushback(body: str) -> bool:
    """Check if a reply body looks like pushback."""
    lower = body.lower().strip()
    for word in _PUSHBACK_WORDS:
        if word in lower:
            return True
    if "?" in body and len(lower) > 10:
        return True
    return False


def last_comment_is_mine(comments: list[dict], my_login: str) -> bool:
    """Whether our own comment is the last one on the thread.

    The authorship half of ADDRESSED, on its own. `compute_thread_state`
    answers RESOLVED before it ever looks at who spoke last, so a caller that
    needs "nobody has answered us yet" — the reply upsert — has to ask this
    rather than read a lifecycle state that folds resolution into the answer.
    """
    if not comments or not my_login:
        return False
    last_author = (comments[-1].get("author") or {}).get("login", "") or ""
    return last_author.lower() == my_login.lower()


def compute_thread_state(
    comments: list[dict],
    is_resolved: bool,
    my_login: str,
) -> ThreadState:
    """Compute the lifecycle state of a thread from its comments.

    Returns one of: new, addressed, verified, contested, resolved, ambiguous.
    """
    if is_resolved:
        return ThreadState.RESOLVED

    if not comments:
        return ThreadState.NEW

    my_login_lower = my_login.lower()
    last_comment = comments[-1]

    has_my_reply = any(
        (c.get("author") or {}).get("login", "").lower() == my_login_lower
        for c in comments
    )

    if not has_my_reply:
        return ThreadState.NEW

    if last_comment_is_mine(comments, my_login):
        return ThreadState.ADDRESSED

    # Reviewer replied after me — classify the reply
    body = last_comment.get("body", "")
    if _is_acknowledgment(body):
        return ThreadState.VERIFIED
    if _is_pushback(body):
        return ThreadState.CONTESTED
    return ThreadState.AMBIGUOUS


# ── GitHub data fetching ───────────────────────────────────────────────────

GRAPHQL_RESOLVE = """
mutation($threadId: ID!) {
  resolveReviewThread(input: {threadId: $threadId}) {
    thread { isResolved }
  }
}
"""


def fetch_threads(
    owner: str, repo_name: str, pr_number: int,
    pr_data: PRData | None = None,
) -> list[dict]:
    """Fetch all review threads via GraphQL. Returns list of thread nodes."""
    if pr_data is not None:
        return pr_data.review_threads
    return fetch_review_threads(f"{owner}/{repo_name}", pr_number)


# ── GitHub writes ────────────────────────────────────────────────────────────

def _gh_post(endpoint: str, body: str, method: str = "POST") -> CmdResult:
    """Send *body* as a JSON `{"body": …}` payload to a gh api REST endpoint.

    The publishing gate lives here rather than in `gh_client`: it is a policy
    this module owns, and a transport that consulted it would gate every read
    in `ai/` on a flag about writes. A draft reports failure rather than
    success, because every "posted" counter downstream reads this result and
    nothing was posted.
    """
    if not publishing.enabled():
        publishing.draft(endpoint, body)
        return CmdResult(returncode=1, stderr=f"{endpoint} not published — publishing is off")
    r = gh_client.api(endpoint, method=method, input_text=json.dumps({"body": body}))
    if not r.ok and r.detail:
        log.error(f"gh api error: {r.detail}")
    return r


def _posted_url(r: CmdResult) -> str | None:
    """The `html_url` GitHub answered a write with, or None if it did not."""
    if not r.ok:
        return None
    try:
        return json.loads(r.stdout).get("html_url")
    except (json.JSONDecodeError, AttributeError, TypeError):
        return None


def post_thread_reply(
    repo: str, pr_number: int, comment_database_id: int, body: str,
) -> bool:
    """Post a reply to a review thread comment. Returns True on success."""
    endpoint = f"repos/{repo}/pulls/{pr_number}/comments/{comment_database_id}/replies"
    return _gh_post(endpoint, body).ok


def patch_thread_reply(repo: str, comment_database_id: int, body: str) -> bool:
    """Edit a review thread comment in place. Returns True on success.

    The counterpart to post_thread_reply, for the same reason
    _patch_issue_comment is the counterpart to post_issue_comment: a review
    cycle revisits the same thread, and a second reply saying something the
    first one contradicts is worse than no reply.  Unlike the issue-comment
    endpoint, this one is not PR-scoped — review comments are addressed by
    database ID alone.
    """
    return _gh_post(
        f"repos/{repo}/pulls/comments/{comment_database_id}", body, method="PATCH",
    ).ok


def update_pr_body(repo: str, pr_number: int, body: str) -> bool:
    """Replace a PR's description. Returns True when it reached GitHub.

    Routed through `_gh_post` rather than `gh pr edit` so the description is
    gated exactly like a reply: `_gh_post` asks `publishing` at the write, so
    there is no version of this call that publishes without `--post`. The
    endpoint's `body` field *is* the description, which is why the shared
    `{"body": …}` payload fits it unchanged.
    """
    return _gh_post(f"repos/{repo}/pulls/{pr_number}", body, method="PATCH").ok


@dataclass(frozen=True)
class MarkerComment:
    """The upsert target for a marked comment, as the lookup found it.

    ``found`` is whether the lookup itself succeeded, which is not the same
    question as whether a comment exists. A listing that errored must not read
    as "no prior comment": the caller would post a duplicate, and one that
    reconciles against ``body`` would conclude the published comment was empty
    and drop everything already in it.

    The two timestamps answer whether the target is still the last word in the
    conversation, which the same listing already knows. An edit does not move a
    comment or notify anyone, so a caller whose comment has to be read needs to
    know whether anything was said below it. ``created_at`` is the marker's own
    place in the timeline — not ``updated_at``, which each edit bumps without
    moving the comment. ``newest_other_at`` is the newest issue comment that is
    not itself a marker comment, so an earlier round's superseded summary does
    not read as someone answering.

    ``url`` is how a later comment links back to this one. A caller that spreads
    its record across several marker comments has to give the reader a way to
    walk the chain, and the listing already carries the link.
    """
    found: bool = False
    comment_id: int | None = None
    body: str = ""
    created_at: str = ""
    newest_other_at: str = ""
    url: str = ""


@dataclass(frozen=True)
class MarkerHistory:
    """Every comment on a PR carrying one marker, oldest first.

    ``find_marker_comment`` answers "which comment do I edit", which is the whole
    question while a marker names a single comment. A caller that posts a new
    marker comment per round has two more: what the earlier rounds already
    published, and where a reader can find them. Both are answers this one
    listing already holds, so they are returned from it rather than paid for
    again.

    ``found`` is the listing's own success, carried for the same reason
    ``MarkerComment.found`` is: an errored listing must not read as a PR with no
    marker comments on it.
    """

    found: bool = False
    comments: tuple[MarkerComment, ...] = ()
    newest_other_at: str = ""

    @property
    def newest(self) -> MarkerComment:
        """The comment a marked upsert targets — the newest one, or an empty stand-in."""
        if self.comments:
            return self.comments[-1]
        return MarkerComment(found=self.found, newest_other_at=self.newest_other_at)

    @property
    def bodies(self) -> list[str]:
        """Every marker comment's body, oldest first."""
        return [c.body for c in self.comments]


def post_issue_comment(
    repo: str, pr_number: int, body: str, marker: str = "",
    existing: MarkerComment | None = None,
) -> str | None:
    """Post an issue-level comment on a PR. Returns the comment URL or None.

    When marker is given, an existing comment containing it is edited in place
    instead of posting a new one.  Review cycles run several rounds; without
    this each round leaves its own partial summary behind.

    ``existing`` is that same lookup, already done. A caller that reconciles the
    new body against what is published has to read the comment before it can
    render, and paying for the listing twice would also let the two reads
    disagree about which comment is the target.
    """
    if marker:
        target = existing if existing is not None else find_marker_comment(repo, pr_number, marker)
        if target.comment_id:
            return _patch_issue_comment(repo, target.comment_id, body)
        if not target.found:
            # The lookup failed rather than came back empty, so an earlier
            # comment may exist. Posting a duplicate beats dropping the update.
            log.error("could not list PR comments — posting a new one instead of editing")
    endpoint = f"repos/{repo}/issues/{pr_number}/comments"
    return _posted_url(_gh_post(endpoint, body))


def find_marker_comment(repo: str, pr_number: int, marker: str) -> MarkerComment:
    """Find the newest issue comment containing marker.

    Paginated: the marker comment is posted on the first round of a review
    cycle, so on a busy PR it is the one most likely to fall off page one.
    """
    return find_marker_comments(repo, pr_number, marker).newest


def find_marker_comments(repo: str, pr_number: int, marker: str) -> MarkerHistory:
    """Find every issue comment containing marker, oldest first.

    The listing behind `find_marker_comment`, kept whole. One round's marked
    comment is the upsert target and the rest are the record it continues, and
    a caller that needs both would otherwise list the PR twice and let the two
    reads disagree about which comment is which.
    """
    pages = gh_client.api_json(
        f"repos/{repo}/issues/{pr_number}/comments?per_page=100",
        paginate=True, slurp=True,
    )
    if not isinstance(pages, list):
        return MarkerHistory()
    comments = [c for page in pages for c in page] if pages and isinstance(pages[0], list) else pages
    newest_other = max(
        (c.get("created_at", "") or "" for c in comments if marker not in (c.get("body") or "")),
        default="",
    )
    marked = tuple(
        MarkerComment(
            True, c.get("id"), c.get("body") or "",
            created_at=c.get("created_at", "") or "",
            newest_other_at=newest_other,
            url=c.get("html_url", "") or "",
        )
        for c in comments if marker in (c.get("body") or "")
    )
    return MarkerHistory(found=True, comments=marked, newest_other_at=newest_other)


def _patch_issue_comment(repo: str, comment_id: int, body: str) -> str | None:
    """Edit an existing issue comment in place. Returns the comment URL or None."""
    return _posted_url(_gh_post(
        f"repos/{repo}/issues/comments/{comment_id}", body, method="PATCH",
    ))


def fetch_reviewer_verdicts(
    repo: str, pr_number: int,
    pr_data: PRData | None = None,
) -> list[dict]:
    """Fetch latest review verdict per reviewer."""
    if pr_data is not None:
        return pr_data.reviewer_verdicts()

    reviews = gh_client.api_json(f"repos/{repo}/pulls/{pr_number}/reviews?per_page=100", default=[])
    by_user: dict[str, dict] = {}
    for r in reviews:
        user = r.get("user", {}).get("login", "")
        submitted = r.get("submitted_at", "")
        state = r.get("state", "")
        if state == "PENDING":
            continue
        if user not in by_user or submitted > by_user[user]["submitted_at"]:
            by_user[user] = {"user": user, "state": state, "submitted_at": submitted}
    return list(by_user.values())


def fetch_issue_comments(
    repo: str, pr_number: int, my_login: str,
    pr_data: PRData | None = None,
    include_self: bool = False,
) -> list[dict]:
    """Fetch issue-level comments (general discussion). Returns non-self ones.

    Bot comments are always dropped, and so are ``my_login``'s own — the caller
    is normally reading what reviewers said, and our replies are noise in that.
    ``include_self`` keeps them, for the caller whose subject is our own
    standing reply on the PR rather than the review it answers.
    """
    if pr_data is not None:
        # `non_self_issue_comments` names the login to leave out, so naming no
        # login leaves nobody out. Kept here rather than at the call sites so
        # the sentinel has one home and one contract.
        return pr_data.non_self_issue_comments("" if include_self else my_login)

    comments = gh_client.api_json(f"repos/{repo}/issues/{pr_number}/comments?per_page=100", default=[])
    result = []
    my_login_lower = my_login.lower()
    for c in comments:
        user_obj = c.get("user", {})
        user = user_obj.get("login", "")
        if not include_self and user.lower() == my_login_lower:
            continue
        if user_obj.get("type") == "Bot":
            continue
        result.append({
            "id": c.get("id"),
            "user": user,
            "body": c.get("body", ""),
            "created_at": c.get("created_at", ""),
        })
    return result


def fetch_review_body_comments(
    repo: str, pr_number: int, my_login: str,
    pr_data: PRData | None = None,
) -> list[dict]:
    """Fetch review-level body comments (reviews with substantive body text).

    These are top-level review bodies — distinct from inline code comments
    (review threads) and issue-level discussion comments.
    """
    if pr_data is not None:
        return pr_data.review_body_comments(my_login)

    reviews = gh_client.api_json(f"repos/{repo}/pulls/{pr_number}/reviews?per_page=100", default=[])
    result = []
    my_login_lower = my_login.lower()
    for r in reviews:
        user = r.get("user", {}).get("login", "")
        if user.lower() == my_login_lower:
            continue
        state = r.get("state", "")
        if state == "PENDING":
            continue
        body = (r.get("body") or "").strip()
        if not body:
            continue
        result.append({
            "id": r.get("id"),
            "user": user,
            "body": body,
            "state": state,
            "submitted_at": r.get("submitted_at", ""),
        })
    return result


def resolve_thread(thread_id: str) -> bool:
    """Resolve a review thread on GitHub via GraphQL mutation."""
    if not publishing.enabled():
        publishing.draft(f"resolve thread {thread_id}")
        return False
    document = json.dumps({
        "query": GRAPHQL_RESOLVE,
        "variables": {"threadId": thread_id},
    })
    return gh_client.graphql("", input_text=document).ok


# ── State sync ─────────────────────────────────────────────────────────────

def sync_threads(
    threads: list[dict],
    prior_threads: dict[str, ThreadRecord],
    my_login: str,
) -> dict[str, ThreadRecord]:
    """Fold what GitHub says about each thread into what the last run recorded.

    Everything GitHub owns is taken fresh. The three triage fields are carried
    over from `prior_threads`, because nothing re-derives them — except on a
    thread that has been replied to since the decision was made, where carrying
    them would attach a verdict to a conversation it never read.
    """
    result: dict[str, ThreadRecord] = {}
    for thread in threads:
        tid = thread["id"]
        comments = thread.get("comments", {}).get("nodes", [])
        is_resolved = thread.get("isResolved", False)

        state = compute_thread_state(comments, is_resolved, my_login)
        last_comment_id = comments[-1]["databaseId"] if comments else None
        first_comment = comments[0] if comments else {}
        reviewer = (first_comment.get("author") or {}).get("login", "")

        prior = prior_threads.get(tid) or ThreadRecord()
        has_new_replies = (
            prior.last_seen_reply_id is not None
            and last_comment_id != prior.last_seen_reply_id
        )
        decided = ThreadRecord() if has_new_replies else prior

        result[tid] = ThreadRecord(
            state=state,
            reviewer=reviewer,
            last_seen_reply_id=last_comment_id,
            file=thread.get("path") or "",
            line=thread.get("line"),
            classification=decided.classification,
            summary=decided.summary,
            decided_at=decided.decided_at,
        )
    return result


# ── Dashboard ──────────────────────────────────────────────────────────────

def _relative_time(iso_str: str) -> str:
    """Convert ISO timestamp to relative time string."""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - dt
        hours = delta // timedelta(hours=1)
        if hours < 1:
            return f"{delta // timedelta(minutes=1)} minutes ago"
        days = delta // timedelta(days=1)
        if not days:
            return f"{hours} hours ago"
        return f"{days} day{plural(days)} ago"
    except (ValueError, TypeError):
        return ""


def render_dashboard(
    pr_number: int,
    threads: dict[str, ThreadRecord],
    verdicts: list[dict],
    issue_comments: list[dict],
    review_body_comments: list[dict] | None = None,
) -> str:
    """Render the status dashboard as a string."""
    review_body_comments = review_body_comments or []
    lines = [f"## PR #{pr_number} Review Status", ""]

    lines.append("Reviewers:")
    for v in sorted(verdicts, key=lambda x: x.get("submitted_at", ""), reverse=True):
        time_str = _relative_time(v.get("submitted_at", ""))
        lines.append(f"  @{v['user']} — {v['state']} ({time_str})")
    lines.append("")

    counts = {state: 0 for state in ThreadState}
    for t in threads.values():
        counts[t.state] += 1
    total = len(threads)

    lines.append(f"Threads: {total} total")
    if counts[ThreadState.RESOLVED]:
        lines.append(f"  ✓ {counts[ThreadState.RESOLVED]} resolved")
    if counts[ThreadState.VERIFIED]:
        lines.append(f"  ✓ {counts[ThreadState.VERIFIED]} verified (ready to resolve)")
    if counts[ThreadState.ADDRESSED]:
        lines.append(f"  ⏳ {counts[ThreadState.ADDRESSED]} addressed (awaiting reviewer)")
    if counts[ThreadState.CONTESTED]:
        lines.append(f"  ⚠ {counts[ThreadState.CONTESTED]} contested (reviewer pushed back)")
    if counts[ThreadState.AMBIGUOUS]:
        lines.append(f"  ? {counts[ThreadState.AMBIGUOUS]} ambiguous (needs your input)")
    if counts[ThreadState.NEW]:
        lines.append(f"  → {counts[ThreadState.NEW]} new (unaddressed)")

    lines.extend(_dashboard_raw_comments(review_body_comments, issue_comments))
    lines.append("")

    blockers = [v["user"] for v in verdicts if v["state"] == "CHANGES_REQUESTED"]
    if blockers:
        lines.append(f"Blocking merge: {', '.join('@' + b for b in blockers)}")
    elif not any(v["state"] == "APPROVED" for v in verdicts):
        lines.append("Blocking merge: no approvals yet")
    lines.append("")

    return "\n".join(lines)


def _dashboard_raw_comments(
    review_body_comments: list[dict],
    issue_comments: list[dict],
) -> list[str]:
    """Render raw comment counts for the dashboard (fallback when items aren't available)."""
    lines: list[str] = []
    unseen_review = sum(1 for c in review_body_comments if not c.get("seen"))
    unseen_issue = sum(1 for c in issue_comments if not c.get("seen"))
    if review_body_comments:
        label = f"  📝 {len(review_body_comments)} review-level comments"
        if unseen_review:
            label += f" ({unseen_review} new)"
        lines.append(label)
    if issue_comments:
        label = f"  💬 {len(issue_comments)} discussion comments"
        if unseen_issue:
            label += f" ({unseen_issue} new)"
        lines.append(label)
    return lines
