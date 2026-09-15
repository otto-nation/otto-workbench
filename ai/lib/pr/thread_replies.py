"""Our standing reply on a review thread, and whether we may still write it.

One reply per thread, replaced in place each round rather than appended to — a
thread accumulating a comment per round is unreadable, and the newest verdict is
the only one that is true. :func:`upsert_thread_reply` is that replacement.

The constraint the module is built around is that a human may have rewritten
what we wrote. `--fix` re-drains every fixed thread on later rounds, so without
a check the three-line template would overwrite reasoning somebody typed, with
no undo but the edit history. :func:`is_generated_reply` measures divergence
against the template rather than tracking a round number, because the round a
reply was written in says nothing about whether it is still ours.

Four builders sit on one driver. They look alike and are not: each reads a
different field, links through a different permalink helper with a different
fallback, and words its log line differently. The differences are load-bearing
and are named where they occur.

The hand-written reply (`run_reply`, at the foot of this module) goes through
the same upsert as all four. It used to post straight to the REST endpoint with
no dedup on that path at all, which is how one thread ends up carrying three of
our comments that contradict each other — so the one-per-thread rule is the
module's, not the fix pass's.
"""

# doc-group: publishing

from __future__ import annotations

import re
import sys
from collections.abc import Callable
from pathlib import Path

from core import log
from core import publishing
from gh.pr_reads import fetch_pr_data
from git import client as git_client
from pr import attribution
from pr import comments as pc
from pr import context as pr_context
from pr import permalinks
from pr.thread_models import THREAD_ANCHOR, CommentItem, ReportThread


APPLIED_REPLY_PREFIX = "Applied"
ADDRESSED_REPLY_PREFIX = "Already addressed"
DISMISSED_REPLY_PREFIX = "Suggestion reviewed and determined to be inapplicable"
DEFERRED_REPLY_PREFIX = "Deferred:"
# Every opening line a generated reply can have. Ordered by nothing — this is a
# membership test, used to tell our own template apart from a reply someone
# rewrote by hand.
GENERATED_REPLY_PREFIXES = (
    APPLIED_REPLY_PREFIX,
    ADDRESSED_REPLY_PREFIX,
    DISMISSED_REPLY_PREFIX,
    DEFERRED_REPLY_PREFIX,
)
# The subset that says the thread was handled. DEFERRED_REPLY_PREFIX is
# deliberately absent: it says the opposite, and counting it would make every
# thread reconcile itself on the second --finish. Derived rather than listed,
# so a fifth generated opening joins this set instead of silently missing it.
HANDLED_REPLY_PREFIXES = tuple(
    prefix for prefix in GENERATED_REPLY_PREFIXES
    if prefix != DEFERRED_REPLY_PREFIX
)
# Every trailing paragraph a generated reply can have: one sentence naming a
# commit, a file, or an issue. Kept in step with the four body_fn builders
# below — a new trailing line there needs its opening added here, or the reply
# it appears in stops being recognised as ours.
_GENERATED_FOLLOWUP_RE = re.compile(
    r"(?:Fixed in|Result is in|Current behaviour is at|Addressed in|See|"
    r"Tracked in|Unchanged at|Not verified) .+\.$",
    re.DOTALL,
)

# The hedge an unverified fix carries. A fix pass edits code and then says so;
# whether the edit works is a separate claim, and one nothing establishes unless
# the verify gate ran and reached a verdict. Saying "fixed" for both is what
# turns an ordinary wrong guess into a retraction, because the reviewer closes
# the thread on the strength of a claim nobody checked.
UNVERIFIED_REPLY_NOTE = "Not verified automatically"


def unverified_note(entry: CommentItem) -> str:
    """The sentence an unverified fix carries, or "" when one was established.

    The detail is why nobody could check it, which is the part that makes the
    row actionable — but the hedge is the claim being weakened and stands on its
    own when the gate reached no verdict at all.

    A pass that never ran the gate (`verified is None`) says nothing either way.
    Hedging there would put a caveat on every fix on every PR and teach the
    reader to skip the ones that mean something.
    """
    if entry.verified is not False:
        return ""
    if entry.verify_detail:
        return f"{UNVERIFIED_REPLY_NOTE} — {entry.verify_detail}."
    return f"{UNVERIFIED_REPLY_NOTE}: please confirm it does what you asked."


def our_last_reply_id(thread: ReportThread | None) -> int | None:
    """The ID of our own reply when it is still the last comment, else None.

    Editing is only safe while nobody has answered us: rewriting a comment a
    reviewer has already replied to changes the text their reply responds to.
    Authorship of the last comment is therefore the whole test, and resolution
    is orthogonal to it — a resolved thread whose last comment is ours still
    has one standing reply to revise.

    ThreadState.ADDRESSED cannot stand in for that: compute_thread_state
    returns RESOLVED for any resolved thread before it looks at who spoke
    last, and `--finish --post` resolves every thread it replies to. Reading
    the state instead of the comments stacked a second reply on exactly the
    threads the one-reply-per-thread rule is for.

    The length guard covers self-review, where the thread root is our own
    comment — editing that would rewrite the review point instead of our
    answer to it.

    Nothing but the edit-or-post choice may read this. Whether a human wrote
    our standing reply is `has_hand_written_reply`, which has to survive a
    reviewer answering and so cannot share this condition.
    """
    if not thread or len(thread.comments) < 2:
        return None
    if not pc.last_comment_is_mine(thread.comments, thread.my_login):
        return None
    return thread.comments[-1].get("databaseId")


def upsert_thread_reply(
    thread: ReportThread, repo: str, pr_number: int, body: str, existing_id: int | None,
) -> bool:
    """Replace our standing reply on a thread, or post the first one."""
    if existing_id:
        return pc.patch_thread_reply(repo, existing_id, body)
    root_id = thread.comments[0].get("databaseId")
    if not root_id:
        return False
    return pc.post_thread_reply(repo, pr_number, root_id, body)


def is_generated_reply(body: str) -> bool:
    """Whether a standing reply is still one of ours, in template shape.

    The upsert replaces our standing reply wholesale, and the fix queue
    re-drains every fixed thread on later rounds — so a reply someone rewrote
    by hand, with reasoning or an explicit decision not to do what the reviewer
    asked, would otherwise be overwritten by the three-line template with no
    undo but the edit history.

    Divergence is measured against the template rather than tracked by round
    number: the round a reply was written in says nothing about whether it is
    still ours. Regenerating an identical body is harmless; replacing a
    divergent one is not.

    ceiling: a hand edit still reads as generated when it stays inside the
    template's own free text — prose in the opening paragraph, or a trailing
    sentence that happens to start with one of the followup openings. Tightening
    the followup pattern is not the fix, since the linkless generated shapes
    would stop matching and their replies could never be updated again. Add an
    HTML marker to generated bodies and key off that if either case ever costs
    someone a reply.
    """
    paragraphs = [p.strip() for p in body.strip().split("\n\n") if p.strip()]
    if not paragraphs or not paragraphs[0].startswith(GENERATED_REPLY_PREFIXES):
        return False
    return all(_GENERATED_FOLLOWUP_RE.fullmatch(p) for p in paragraphs[1:])


def has_hand_written_reply(thread: ReportThread | None) -> bool:
    """Whether a human wrote the newest reply of ours on this thread.

    "May I edit our standing reply?" and "has a human already spoken for us
    here?" are two different questions, and only the first turns on who spoke
    last — so this deliberately does not go through `our_last_reply_id`. Gating
    the hand-written check on that one made the protection strongest while
    nothing had happened and dropped it the moment a reviewer answered, which is
    exactly when the thread has become a conversation worth not talking over.

    The newest reply of ours is the one that stands, whatever came after it: a
    reviewer's answer below a hand-written reply does not make the templated
    body a better statement of our position. Our comments are read newest-first
    so that a `--reply` posted deliberately after a hand edit still decides,
    and the root is skipped because on a self-review it is our own review point
    rather than an answer to one.

    `--reply <id> --body-file <f>` remains the way to replace a hand-written
    reply on purpose, so nothing is lost by never doing it automatically.
    """
    if not thread or len(thread.comments) < 2:
        return False
    login = (thread.my_login or "").lower()
    if not login:
        return False
    for comment in reversed(thread.comments[1:]):
        author = ((comment.get("author") or {}).get("login") or "").lower()
        if author == login:
            return not is_generated_reply(str(comment.get("body", "")))
    return False


def _post_thread_replies(
    entries: list[CommentItem],
    threads_by_id: dict[str, ReportThread],
    repo: str,
    pr_number: int,
    body_fn: Callable[[CommentItem], str],
) -> int:
    """Leave one reply per thread, building each body with body_fn.

    Whichever verdict a round reaches, it lands in the same place: our standing
    reply on that thread, replaced — unless a human has rewritten that reply,
    in which case the thread is left alone for the life of the PR, answered or
    not. Returns the number replied to.
    """
    replied = 0
    edited = 0
    kept = 0
    for entry in entries:
        thread = threads_by_id.get(entry.id)
        if not thread or not thread.comments:
            continue
        if has_hand_written_reply(thread):
            log.warn(f"{entry.id}: standing reply was hand-written — leaving it alone")
            kept += 1
            continue
        existing_id = our_last_reply_id(thread)
        # ceiling: thread.comments is not refreshed after a post, so two replies
        # to one thread within a single run would still stack. Unreachable while
        # the reply categories stay a partition of the triage verdicts; refetch
        # the thread here if that ever stops holding.
        if not upsert_thread_reply(thread, repo, pr_number, body_fn(entry), existing_id):
            continue
        replied += 1
        edited += existing_id is not None
    if edited:
        log.info(f"Edited {edited} standing repl{'y' if edited == 1 else 'ies'} in place")
    if kept:
        log.info(
            f"Left {kept} hand-written repl{'y' if kept == 1 else 'ies'} untouched "
            f"— use --reply <id> --body-file <f> to replace one deliberately"
        )
    return replied


def post_fix_replies(
    fixed: list[CommentItem],
    threads_by_id: dict[str, ReportThread],
    repo: str,
    pr_number: int,
    cp: attribution.CommitPushResult,
    history: "attribution.AddressingHistory | None" = None,
) -> int:
    """Post replies to each fixed thread. Returns count of replies posted.

    Every entry here must be one `attribution.attribute_commit` can cite, which is what
    `reply_to_fixed` partitions for — this body asserts a commit link and a
    blob permalink under it, and neither has a shape that degrades gracefully.

    `history` must be the one the partition was taken with. A row this body
    asks about differently from the caller that routed it here is a row sent
    down the citing path with nothing to cite, which is precisely the 404 the
    partition exists to prevent.
    """
    def body_fn(entry: CommentItem) -> str:
        sha = attribution.attribute_commit(entry, cp, history, threads_by_id.get(entry.id)).sha
        parts = [
            f"{APPLIED_REPLY_PREFIX}: {entry.summary}",
            f"Fixed in [`{sha}`]({permalinks.commit_permalink(repo, sha)}).",
        ]
        # The file, not the line: the fix has just moved the lines around it,
        # and pointing a reviewer at the wrong line is worse than pointing them
        # at the file the change landed in.
        if entry.file:
            url = permalinks.blob_permalink(repo, sha, entry.file)
            parts.append(f"Result is in [`{entry.file}`]({url}).")
        note = unverified_note(entry)
        if note:
            parts.append(note)
        return "\n\n".join(parts)

    posted = _post_thread_replies(fixed, threads_by_id, repo, pr_number, body_fn)
    if posted:
        log.info(f"Replied on {posted} fixed thread(s)")
    return posted


def post_already_addressed_replies(
    fixed: list[CommentItem],
    threads_by_id: dict[str, ReportThread],
    repo: str,
    pr_number: int,
    wt_path: Path,
    acted: bool = False,
) -> int:
    """Post replies to threads the current code satisfies. Returns count posted.

    Two readings, one body. A thread whose code was made to satisfy the
    reviewer after they asked gets the `Applied:` / `Fixed in <sha>` framing the
    `fixed` verdict uses, because that is what happened to it; `Already
    addressed` is left for code that predates the comment.

    `acted` says the caller landed the change itself — the unattributed half of
    `reply_to_fixed`, which is here for the linkless body and not because the
    reviewer's point was moot.
    """
    history = attribution.AddressingHistory(wt_path)
    head_sha = git_client.head_sha(short=True, cwd=wt_path)

    def body_fn(entry: CommentItem) -> str:
        framing = history.framing(entry, threads_by_id.get(entry.id), acted=acted)
        # The line was read at HEAD, so that is the tree the link has to pin to;
        # the addressing commit answers "when", not "where".
        if framing.in_response:
            parts = [f"{APPLIED_REPLY_PREFIX}: {entry.summary}"]
        else:
            parts = [f"{ADDRESSED_REPLY_PREFIX}: {entry.summary}"]
        # No evidence check: triage.downgrade_unsupported_verdicts already refused to
        # let an already_addressed verdict through without a citation that
        # resolves. Checking again here would only overrule it with a weaker
        # link.
        link = permalinks.code_link(entry, repo, head_sha, wt_path, verify_evidence=False)
        if link:
            parts.append(f"Current behaviour is at {link}.")
        if framing.cited:
            sha = framing.sha
            commit_url = permalinks.commit_permalink(repo, sha)
            lead = "Fixed in" if framing.in_response else "Addressed in"
            parts.append(f"{lead} [`{git_client.abbrev(sha)}`]({commit_url}).")
        if len(parts) == 1 and not framing.in_response:
            return (
                f"{ADDRESSED_REPLY_PREFIX} in the current implementation: "
                f"{entry.summary}"
            )
        return "\n\n".join(parts)

    posted = _post_thread_replies(fixed, threads_by_id, repo, pr_number, body_fn)
    if posted:
        log.info(f"Replied on {posted} satisfied thread(s)")
    return posted


def reply_to_fixed(
    fixed: list[CommentItem],
    threads_by_id: dict[str, ReportThread],
    repo: str,
    pr_number: int,
    cp: attribution.CommitPushResult,
    wt_path: Path,
) -> int:
    """Reply on every fixed thread, each citing the commit attributed to it.

    The split is the resolver's, not the caller's. Both the fix pass and the
    `--finish` drain reply to fixed threads, and each used to decide for itself
    which SHA a reply could name — the two disagreed, and the reply path was
    the one that got it wrong. An entry the resolver cannot attribute gets the
    linkless shape rather than a reply whose commit link and blob permalink
    both 404 — but still under the framing its own bucket earned, since a
    missing citation says nothing about whether the pass acted. It did: that is
    what put the entry in `fixed`.
    """
    # One history for the partition and the bodies under it: the split asks
    # which rows can be cited and the reply then asks what to cite, and two
    # objects answering that could put a row in the citing bucket and then
    # decline to name the commit it was routed there for.
    history = attribution.AddressingHistory(wt_path)
    attributed, unattributed = [], []
    for entry in fixed:
        cited = attribution.attribute_commit(
            entry, cp, history, threads_by_id.get(entry.id),
        ).cited
        bucket = attributed if cited else unattributed
        bucket.append(entry)
    posted = 0
    if attributed:
        posted += post_fix_replies(
            attributed, threads_by_id, repo, pr_number, cp, history,
        )
    if unattributed:
        posted += post_already_addressed_replies(
            unattributed, threads_by_id, repo, pr_number, wt_path, acted=True,
        )
    return posted


def post_dismissed_replies(
    dismissed: list[CommentItem],
    threads_by_id: dict[str, ReportThread],
    repo: str,
    pr_number: int,
    wt_path: Path,
) -> int:
    """Post replies to invalid suggestion threads. Returns count posted.

    Reserved for suggestions whose premise does not hold.  A suggestion the code
    already satisfies is not dismissed — it goes through
    post_already_addressed_replies, which credits the reviewer instead of
    telling them their point did not apply.

    Telling a reviewer they misread the code is the one reply that most needs a
    line to point at, so the dismissal carries the permalink triage cited.
    """
    head_sha = git_client.head_sha(short=True, cwd=wt_path)

    def body_fn(entry: CommentItem) -> str:
        reasoning = entry.reasoning
        body = DISMISSED_REPLY_PREFIX
        body = f"{body}: {reasoning}" if reasoning else f"{body}."
        link = permalinks.evidence_link(entry, repo, head_sha, wt_path)
        return f"{body}\n\nSee {link}." if link else body

    posted = _post_thread_replies(dismissed, threads_by_id, repo, pr_number, body_fn)
    if posted:
        log.info(f"Dismissed {posted} invalid suggestion(s)")
    return posted


def post_deferred_replies(
    deferred: list[CommentItem],
    threads_by_id: dict[str, ReportThread],
    repo: str,
    pr_number: int,
    issue_id: str,
    issue_url: str,
    wt_path: Path | None = None,
) -> int:
    """Post replies to deferred threads linking the tracking issue."""
    issue_ref = f"[{issue_id}]({issue_url})" if issue_url else issue_id
    head_sha = git_client.head_sha(short=True, cwd=wt_path) if wt_path else ""

    def body_fn(entry: CommentItem) -> str:
        parts = [f"{DEFERRED_REPLY_PREFIX} {entry.summary}",
                 f"Tracked in {issue_ref}."]
        # A deferral says the code still stands as the reviewer found it, which
        # is a claim about the tree like any other — pin it.
        link = permalinks.code_link(entry, repo, head_sha, wt_path)
        if link:
            parts.append(f"Unchanged at {link}.")
        return "\n\n".join(parts)

    posted = _post_thread_replies(deferred, threads_by_id, repo, pr_number, body_fn)
    if posted:
        log.info(f"Replied on {posted} deferred thread(s)")
    return posted


def find_reply_target(
    threads_raw: list[dict], target: str, my_login: str,
) -> ReportThread | None:
    """Resolve a thread by node ID or by any comment ID inside it.

    Both, because the two are handed out by different things: the report prints
    the node ID, while a reviewer sends a link ending `#discussion_r3717174529`
    — which is a comment ID, and the only identifier a human ever has.
    """
    wanted = target.strip().rsplit("#", 1)[-1].removeprefix(THREAD_ANCHOR)
    for data in threads_raw:
        comments = data.get("comments", {}).get("nodes", [])
        ids = {str(c.get("databaseId")) for c in comments}
        if data.get("id") != target and wanted not in ids:
            continue
        is_resolved = data.get("isResolved", False)
        return ReportThread(
            id=data.get("id", ""),
            state=pc.compute_thread_state(comments, is_resolved, my_login),
            reviewer=(comments[0].get("author") or {}).get("login", "") if comments else "",
            comments=comments,
            is_resolved=is_resolved,
            file=data.get("path", ""),
            line=data.get("line"),
            my_login=my_login,
        )
    return None


def read_reply_body(body_file: str | None) -> str | None:
    """Read a reply body from a file, or from stdin when given '-'.

    None for a path that is not there, "" for a file that is: a mistyped path
    and an empty draft need different advice.
    """
    if not body_file:
        return None
    if body_file == "-":
        return sys.stdin.read().strip()
    path = Path(body_file)
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8").strip()


def run_reply(ctx: pr_context.ResolvedContext, target: str, body_file: str | None) -> int:
    """Post one hand-written reply through the same upsert the fix pass uses.

    Manual replies used to go straight to the REST replies endpoint, with no
    dedup on that path at all — which is how a thread ends up carrying three of
    our comments that contradict each other. Routing them here holds a manual
    reply to the same one-per-thread rule as a generated one.
    """
    body = read_reply_body(body_file)
    if body is None:
        missing = f"--body-file not found: {body_file}" if body_file else "--reply needs --body-file"
        log.error(missing)
        return 1
    if not body:
        log.error(f"--body-file is empty: {body_file}")
        return 1

    repo = ctx.repo
    pr_number = ctx.pr_number
    owner, repo_name = repo.split("/", 1)
    pr_data = fetch_pr_data(repo, str(pr_number))
    threads_raw = pc.fetch_threads(owner, repo_name, pr_number, pr_data)

    thread = find_reply_target(threads_raw, target, pr_data.viewer_login)
    if thread is None:
        log.error(f"no review thread on PR #{pr_number} matches {target!r}")
        return 1

    if "/blob/" not in body:
        log.warn(
            "reply cites no permalink — a claim about the code needs a link to "
            "the line that settles it"
        )

    existing_id = our_last_reply_id(thread)
    wrote = upsert_thread_reply(thread, repo, pr_number, body, existing_id)
    if not publishing.enabled():
        # The closing line is the one read as the outcome, so it carries the
        # same label the body was printed under rather than a past tense the
        # run never earned.
        publishing.draft(f"{'edit' if existing_id else 'post'} reply on {thread.id}")
        return 0
    if not wrote:
        log.error(f"failed to reply on {thread.id}")
        return 1
    log.info(f"{'Edited' if existing_id else 'Posted'} reply on {thread.id}")
    return 0
