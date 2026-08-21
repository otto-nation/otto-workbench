"""PR comments lifecycle tracking.

Handles thread lifecycle state computation, local state persistence,
and GitHub data fetching for the pr-comments skill.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path

import log
import publishing
import serde
import timeouts
from pr_state import CommentsSummary, FixSummary, ThreadAction, TriageSummary
from review_common import plural
from review_github import PRData, fetch_review_threads


# ── State file I/O ─────────────────────────────────────────────────────────

def empty_state(repo: str, pr_number: int, my_login: str) -> dict:
    """Create a fresh state object."""
    return {
        "repo": repo,
        "pr_number": pr_number,
        "last_run": datetime.now(timezone.utc).isoformat(),
        "my_login": my_login,
        "threads": {},
    }


def load_state(path: Path) -> dict | None:
    """Load state from file. Returns None if file doesn't exist."""
    if not path.exists():
        return None
    with open(path) as f:
        return json.load(f)


def save_state(path: Path, state: dict) -> None:
    """Save state to file, creating parent directories."""
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    serde.write_json(path, state)


# ── Thread lifecycle states ────────────────────────────────────────────────


class ThreadState(StrEnum):
    NEW = "new"
    ADDRESSED = "addressed"
    VERIFIED = "verified"
    CONTESTED = "contested"
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"


STATE_NEW = ThreadState.NEW
STATE_ADDRESSED = ThreadState.ADDRESSED
STATE_VERIFIED = ThreadState.VERIFIED
STATE_CONTESTED = ThreadState.CONTESTED
STATE_RESOLVED = ThreadState.RESOLVED
STATE_AMBIGUOUS = ThreadState.AMBIGUOUS

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
        return STATE_RESOLVED

    if not comments:
        return STATE_NEW

    my_login_lower = my_login.lower()
    last_comment = comments[-1]

    has_my_reply = any(
        (c.get("author") or {}).get("login", "").lower() == my_login_lower
        for c in comments
    )

    if not has_my_reply:
        return STATE_NEW

    if last_comment_is_mine(comments, my_login):
        return STATE_ADDRESSED

    # Reviewer replied after me — classify the reply
    body = last_comment.get("body", "")
    if _is_acknowledgment(body):
        return STATE_VERIFIED
    if _is_pushback(body):
        return STATE_CONTESTED
    return STATE_AMBIGUOUS


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


def _gh_rest(endpoint: str) -> tuple[int, str]:
    """Call gh api REST endpoint. Returns (exit_code, stdout)."""
    result = subprocess.run(
        ["gh", "api", endpoint],
        capture_output=True, text=True, timeout=timeouts.NETWORK,
    )
    return result.returncode, result.stdout


def _paginated_json(endpoint: str) -> tuple[int, str]:
    """Call a gh api REST endpoint across all pages. Returns (exit_code, stdout).

    --slurp wraps the pages in an outer array, so the result is a list of pages
    rather than a flat list of items.
    """
    result = subprocess.run(
        ["gh", "api", "--paginate", "--slurp", endpoint],
        capture_output=True, text=True, timeout=timeouts.TRANSFER,
    )
    return result.returncode, result.stdout


# ── GitHub writes ────────────────────────────────────────────────────────────

def _gh_post(endpoint: str, body: str, method: str = "POST") -> tuple[int, str]:
    """Send a JSON body to a gh api REST endpoint. Returns (exit_code, stdout).

    A draft reports failure rather than success: every "posted" counter
    downstream reads this exit code, and nothing was posted.
    """
    if not publishing.enabled():
        publishing.draft(endpoint, body)
        return 1, ""
    payload = json.dumps({"body": body})
    result = subprocess.run(
        ["gh", "api", endpoint, "--method", method, "--input", "-"],
        input=payload, capture_output=True, text=True, timeout=timeouts.NETWORK,
    )
    if result.returncode != 0 and result.stderr.strip():
        log.error(f"gh api error: {result.stderr.strip()}")
    return result.returncode, result.stdout


def post_thread_reply(
    repo: str, pr_number: int, comment_database_id: int, body: str,
) -> bool:
    """Post a reply to a review thread comment. Returns True on success."""
    endpoint = f"repos/{repo}/pulls/{pr_number}/comments/{comment_database_id}/replies"
    code, _ = _gh_post(endpoint, body)
    return code == 0


def patch_thread_reply(repo: str, comment_database_id: int, body: str) -> bool:
    """Edit a review thread comment in place. Returns True on success.

    The counterpart to post_thread_reply, for the same reason
    _patch_issue_comment is the counterpart to post_issue_comment: a review
    cycle revisits the same thread, and a second reply saying something the
    first one contradicts is worse than no reply.  Unlike the issue-comment
    endpoint, this one is not PR-scoped — review comments are addressed by
    database ID alone.
    """
    code, _ = _gh_post(
        f"repos/{repo}/pulls/comments/{comment_database_id}", body, method="PATCH",
    )
    return code == 0


def update_pr_body(repo: str, pr_number: int, body: str) -> bool:
    """Replace a PR's description. Returns True when it reached GitHub.

    Routed through `_gh_post` rather than `gh pr edit` so the description is
    gated exactly like a reply: `_gh_post` asks `publishing` at the write, so
    there is no version of this call that publishes without `--post`. The
    endpoint's `body` field *is* the description, which is why the shared
    `{"body": …}` payload fits it unchanged.
    """
    code, _ = _gh_post(f"repos/{repo}/pulls/{pr_number}", body, method="PATCH")
    return code == 0


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
    """
    found: bool = False
    comment_id: int | None = None
    body: str = ""
    created_at: str = ""
    newest_other_at: str = ""


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
    code, out = _gh_post(endpoint, body)
    if code != 0:
        return None
    try:
        return json.loads(out).get("html_url")
    except (json.JSONDecodeError, TypeError):
        return None


def find_marker_comment(repo: str, pr_number: int, marker: str) -> MarkerComment:
    """Find the newest issue comment containing marker.

    Paginated: the marker comment is posted on the first round of a review
    cycle, so on a busy PR it is the one most likely to fall off page one.
    """
    code, out = _paginated_json(f"repos/{repo}/issues/{pr_number}/comments?per_page=100")
    if code != 0:
        return MarkerComment()
    try:
        pages = json.loads(out)
    except (json.JSONDecodeError, TypeError):
        return MarkerComment()
    if not isinstance(pages, list):
        return MarkerComment()
    comments = [c for page in pages for c in page] if pages and isinstance(pages[0], list) else pages
    newest_other = max(
        (c.get("created_at", "") or "" for c in comments if marker not in (c.get("body") or "")),
        default="",
    )
    for c in reversed(comments):
        if marker in (c.get("body") or ""):
            return MarkerComment(
                True, c.get("id"), c.get("body") or "",
                created_at=c.get("created_at", "") or "",
                newest_other_at=newest_other,
            )
    return MarkerComment(found=True, newest_other_at=newest_other)


def _patch_issue_comment(repo: str, comment_id: int, body: str) -> str | None:
    """Edit an existing issue comment in place. Returns the comment URL or None."""
    code, out = _gh_post(
        f"repos/{repo}/issues/comments/{comment_id}", body, method="PATCH",
    )
    if code != 0:
        return None
    try:
        return json.loads(out).get("html_url")
    except (json.JSONDecodeError, TypeError):
        return None


def fetch_reviewer_verdicts(
    repo: str, pr_number: int,
    pr_data: PRData | None = None,
) -> list[dict]:
    """Fetch latest review verdict per reviewer."""
    if pr_data is not None:
        return pr_data.reviewer_verdicts()

    code, out = _gh_rest(f"repos/{repo}/pulls/{pr_number}/reviews?per_page=100")
    if code != 0:
        return []
    try:
        reviews = json.loads(out)
    except (json.JSONDecodeError, TypeError):
        return []
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

    code, out = _gh_rest(f"repos/{repo}/issues/{pr_number}/comments?per_page=100")
    if code != 0:
        return []
    try:
        comments = json.loads(out)
    except (json.JSONDecodeError, TypeError):
        return []
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

    code, out = _gh_rest(f"repos/{repo}/pulls/{pr_number}/reviews?per_page=100")
    if code != 0:
        return []
    try:
        reviews = json.loads(out)
    except (json.JSONDecodeError, TypeError):
        return []
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
    query = json.dumps({
        "query": GRAPHQL_RESOLVE,
        "variables": {"threadId": thread_id},
    })
    result = subprocess.run(
        ["gh", "api", "graphql", "--input", "-"],
        input=query, capture_output=True, text=True, timeout=timeouts.NETWORK,
    )
    return result.returncode == 0


# ── State sync ─────────────────────────────────────────────────────────────

def sync_threads(
    threads: list[dict],
    prior_threads: dict,
    my_login: str,
) -> dict:
    """Sync GitHub thread data with local state. Returns updated threads dict."""
    result = {}
    for thread in threads:
        tid = thread["id"]
        comments = thread.get("comments", {}).get("nodes", [])
        is_resolved = thread.get("isResolved", False)

        state = compute_thread_state(comments, is_resolved, my_login)
        last_comment_id = comments[-1]["databaseId"] if comments else None
        first_comment = comments[0] if comments else {}
        reviewer = (first_comment.get("author") or {}).get("login", "")

        prior = prior_threads.get(tid, {})
        prior_last_seen = prior.get("last_seen_reply_id")

        has_new_replies = prior_last_seen is not None and last_comment_id != prior_last_seen

        if has_new_replies:
            classification = None
            summary = None
            decided_at = None
        else:
            classification = prior.get("classification")
            summary = prior.get("summary")
            decided_at = prior.get("decided_at")

        result[tid] = {
            "state": state,
            "reviewer": reviewer,
            "last_seen_reply_id": last_comment_id,
            "file": thread.get("path"),
            "line": thread.get("line"),
            "classification": classification,
            "summary": summary,
            "decided_at": decided_at,
        }
    return result


# ── Dashboard ──────────────────────────────────────────────────────────────

def _relative_time(iso_str: str) -> str:
    """Convert ISO timestamp to relative time string."""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - dt
        hours = int(delta.total_seconds() // 3600)
        if hours < 1:
            return f"{int(delta.total_seconds() // 60)} minutes ago"
        if hours < 24:
            return f"{hours} hours ago"
        days = hours // 24
        return f"{days} day{plural(days)} ago"
    except (ValueError, TypeError):
        return ""


def render_dashboard(
    pr_number: int,
    threads: dict,
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

    counts = {STATE_RESOLVED: 0, STATE_ADDRESSED: 0, STATE_CONTESTED: 0,
              STATE_NEW: 0, STATE_VERIFIED: 0, STATE_AMBIGUOUS: 0}
    for t in threads.values():
        counts[t["state"]] = counts.get(t["state"], 0) + 1
    total = len(threads)

    lines.append(f"Threads: {total} total")
    if counts[STATE_RESOLVED]:
        lines.append(f"  ✓ {counts[STATE_RESOLVED]} resolved")
    if counts[STATE_VERIFIED]:
        lines.append(f"  ✓ {counts[STATE_VERIFIED]} verified (ready to resolve)")
    if counts[STATE_ADDRESSED]:
        lines.append(f"  ⏳ {counts[STATE_ADDRESSED]} addressed (awaiting reviewer)")
    if counts[STATE_CONTESTED]:
        lines.append(f"  ⚠ {counts[STATE_CONTESTED]} contested (reviewer pushed back)")
    if counts[STATE_AMBIGUOUS]:
        lines.append(f"  ? {counts[STATE_AMBIGUOUS]} ambiguous (needs your input)")
    if counts[STATE_NEW]:
        lines.append(f"  → {counts[STATE_NEW]} new (unaddressed)")

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


# ── Status rendering ─────────────────────────────────────────────────────


def render_status(c: CommentsSummary) -> list[str]:
    """Render comments state as status lines for the pr dashboard."""
    if not c.updated_at:
        return ["**Comments**: not checked yet"]
    lines = [f"**Comments**: {c.total_threads} thread(s)"]
    if c.by_state:
        parts = [f"{s}: {ct}" for s, ct in sorted(c.by_state.items())]
        lines.append(f"  {', '.join(parts)}")
    if c.blocking_reviewers:
        lines.append(f"  blocking: {', '.join(c.blocking_reviewers)}")
    return lines


def render_triage_status(t: TriageSummary) -> list[str]:
    """Render triage state as status lines for the pr dashboard."""
    if not t.updated_at:
        return ["**Triage**: not run yet"]
    return [f"**Triage**: {t.total} threads — {t.actionable} actionable ({t.valid} valid), {t.questions} questions"]


# ── Closeout queue ───────────────────────────────────────────────────────


# The one command that drains the queue. Spelled once so the status line, the
# merge-readiness blocker, and the docs cannot drift from each other.
CLOSEOUT_COMMAND = "pr comments --finish --post"

# The three reply buckets --finish drains (`_post_pending_fix_replies` in
# review-threads). Threads with any other outcome owe no reply, so they must
# not inflate the count the operator is quoted.
_REPLY_ACTIONS = frozenset({
    ThreadAction.FIXED, ThreadAction.ALREADY_ADDRESSED, ThreadAction.DISMISSED,
})


@dataclass(frozen=True)
class CloseoutDebt:
    """What a fix pass rendered but never delivered to the PR.

    The queue's only symptom is the *absence* of comments on the PR, which is
    indistinguishable from a run that had nothing to say — so every surface
    that reports on a fix pass has to say the debt out loud.
    """

    summary: bool = False
    replies: bool = False
    # A tracking issue the fix pass owed the deferred threads and never filed.
    # Its absence is quieter still than the other two: the summary renders a
    # bare "Deferred" with no link, which reads exactly like a deferral nobody
    # asked to track.
    deferred_issue: bool = False
    # Recounted from the recorded outcomes rather than read off a stored number,
    # which makes it advisory: a queue whose outcomes were pruned still owes its
    # replies via `replies` while this reads 0. `replies` alone decides whether
    # anything is owed; the count only sharpens the wording.
    reply_count: int = 0
    # A PR description the fix pass rewrote but could not send. It is a GitHub
    # write like any other, so it is owed here rather than quietly sitting in
    # the worktree until someone notices the description never changed.
    description: bool = False

    @property
    def owed(self) -> bool:
        return self.summary or self.replies or self.deferred_issue or self.description

    def describe(self) -> str:
        """Name what is owed — 'summary', '15 replies', 'deferred tracking issue', 'PR description', or a mix."""
        parts = []
        if self.summary:
            parts.append("summary")
        if self.replies:
            # An uncounted queue reads as replies owed, never as zero of them.
            noun = "reply" if self.reply_count == 1 else "replies"
            parts.append(f"{self.reply_count} {noun}" if self.reply_count else "replies")
        if self.deferred_issue:
            parts.append("deferred tracking issue")
        if self.description:
            parts.append("PR description")
        return " + ".join(parts)


def closeout_command_for(debt: CloseoutDebt) -> str:
    """The command that actually drains the given debt.

    The bare CLOSEOUT_COMMAND drains a rendered-but-unsent summary or reply
    queue, but a deferred tracking issue is only ever filed for threads named
    by `--track`/`--track-all` — `--track` defaults to selecting nothing, so
    the bare command would hit that early return and leave the issue unfiled
    forever. Quote the flag that actually files it whenever that debt is owed.
    """
    if debt.deferred_issue:
        return f"{CLOSEOUT_COMMAND} --track-all"
    return CLOSEOUT_COMMAND


def closeout_debt(f: FixSummary) -> CloseoutDebt:
    """Read the undelivered closeout out of fix state.

    Reads only what the fix pass already recorded — no fetch, no new state.
    """
    return CloseoutDebt(
        summary=f.summary_deferred,
        replies=f.replies_pending,
        deferred_issue=f.deferred_issue_pending,
        reply_count=sum(1 for t in f.threads if t.action in _REPLY_ACTIONS),
        description=f.pr_body_pending,
    )


def render_fix_status(f: FixSummary) -> list[str]:
    """Render fix state as status lines for the pr dashboard."""
    if not f.updated_at:
        return ["**Fix**: not run yet"]
    by_action: dict[str, int] = {}
    for t in f.threads:
        by_action[t.action] = by_action.get(t.action, 0) + 1
    parts = []
    if by_action.get(ThreadAction.FIXED, 0):
        parts.append(f"**{by_action[ThreadAction.FIXED]} fixed**")
    if by_action.get(ThreadAction.DEFERRED, 0):
        parts.append(f"{by_action[ThreadAction.DEFERRED]} deferred")
    if by_action.get(ThreadAction.NEEDS_HUMAN, 0):
        parts.append(f"{by_action[ThreadAction.NEEDS_HUMAN]} need discussion")
    if by_action.get(ThreadAction.DISMISSED, 0):
        parts.append(f"{by_action[ThreadAction.DISMISSED]} dismissed")
    if by_action.get(ThreadAction.ALREADY_ADDRESSED, 0):
        parts.append(f"{by_action[ThreadAction.ALREADY_ADDRESSED]} already addressed")
    summary = " · ".join(parts) if parts else "no threads"
    lines = [f"**Fix**: {summary}"]
    if f.commit_sha:
        lines[0] += f" (commit: {f.commit_sha}, {f.commit_status})"
    debt = closeout_debt(f)
    if debt.owed:
        lines.append(f"  ⚠ closeout owed: {debt.describe()} — run: {closeout_command_for(debt)}")
    if f.deferred_issue_id:
        lines.append(f"  tracked in {f.deferred_issue_id}")
    return lines
