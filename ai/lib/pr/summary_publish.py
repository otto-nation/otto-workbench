"""Getting one round's summary onto the PR without shrinking the record.

The publish decision and the two paths that reach it: the fix pass posting at
the end of a round, and `--finish` re-rendering from state once a deferred
tracking issue exists. Both build the same body through `pr.summary_render` and
both come through `publish_summary`, which is where the record is protected —
a row the published comments hold and this render cannot account for is carried
forward rather than overwritten, and an Action cell a person rewrote is kept.

Whether to edit the existing comment or post a fresh one is decided here too,
because it changes what the round is allowed to leave out: an edit rewrites its
target wholesale, a fresh post replaces nothing. `pr.summary_rounds` does that
arithmetic; this hands it the decision.
"""

# ceiling: the immediate path and the --finish re-render are kept in one module
# because they share the publish rule and the warning that guards it, and a
# reader asking "how does a summary reach the PR" should see both answers
# together. Past that they overlap little: one is handed a live RoundContent,
# the other rebuilds one from the state file and reconciles a commit first.
# Upgrade trigger: once either path grows an import the other has no use for, or
# this file trips the source size cap, split the --finish half into
# `summary_finish.py` and leave the publish rule here.

# doc-group: publishing

from __future__ import annotations

import functools
from collections.abc import Callable
from pathlib import Path

from core import log
from core import publishing
from git import push
from git.land import CommitStatus
from pr import attribution
from pr import comments as pc
from pr import history_rewrite
from pr import state as pr_state
from pr import summary_model
from pr import summary_render
from pr import summary_rounds
from pr import summary_row
from pr import summary_scope
from pr.fix import FixOutcome
from pr.summary_render import SUMMARY_MARKER
from pr.thread_models import CommentItem, PRReport, ReportThread

def _warn_unattributed_fixes(
    fixed: list[CommentItem],
    cp: attribution.CommitPushResult,
    folded: set[str] | None = None,
    history: attribution.AddressingHistory | None = None,
    threads_by_id: dict[str, ReportThread] | None = None,
) -> None:
    """Say so when rows claim fixes that no commit accounts for.

    The contradiction is caught rather than rendered silently: the summary goes
    out under the operator's name, and a row that asserts both halves is a claim
    about their branch that nothing in the run supports.

    This describes the table about to be published, so it counts that table's
    rows and no others. Two things follow, and both were once wrong here:

    - `folded` drops the entries the renderer will fold into a thread's row.
      They reach no reader, so counting them describes a list nobody sees and
      reads as a larger attribution problem than the branch has.
    - A row `summary_row.settled_outside_the_pass` recognises is not counted. The resolver
      declines to cite it, which is why it used to be, but the cell it renders
      is a specific claim about where the fix went — the work is on the branch,
      in a commit this run cannot name. Nothing about it contradicts itself, and
      the warning's own wording ("without a claim") never fitted it. What is
      left is the case the warning exists for: a row asserting a fix with
      nothing on the branch to show for it.

    The second half is asked through the same predicate the renderer asks, and
    of the record rather than of the rendered cell — the count and the rows
    under it are two readings of one answer and must not be able to disagree.
    `history` is passed on for the same reason: a row the table resolves to a
    commit is attributed, and counting it here would report an attribution
    problem the reader cannot see. Asking both of the same row costs one line
    lookup, not two — `history` memoizes on the location a row cites, which is
    why the caller hands over the instance the table will use rather than
    letting each build its own.

    An unpushed commit is not this either: the work is committed and the row
    says so, it is only the link that has to wait.
    """
    if attribution.commit_unpushed(cp.status):
        return
    folded = folded or set()
    threads_by_id = threads_by_id or {}
    orphans = [
        e for e in fixed
        if e.id not in folded
        and not attribution.attribute_commit(e, cp, history, threads_by_id.get(e.id)).cited
        and not summary_row.settled_outside_the_pass(e, cp)
    ]
    if not orphans:
        return
    log.warn(
        f"{len(orphans)} fixed row(s) have no commit to attribute them to "
        f"(commit status: {cp.status}) — rendering them without a claim"
    )


def _warned_history(
    content: summary_model.RoundContent,
    cp: attribution.CommitPushResult,
    threads_by_id: dict[str, ReportThread],
    wt_path: Path | None,
) -> attribution.AddressingHistory:
    """The history this render will resolve rows against, warned over first.

    Both publish paths open the same way and must keep doing so: the warning
    describes the table about to go out, so it has to see the same folded set
    and the same history the table will use. Spelled once because the two
    copies were byte-identical and a change to one was a silent disagreement
    between what the count reported and what the reader saw.

    One instance per render rather than one per question: `history` memoizes on
    the location a row cites, and `git log -L` is a process per location, so
    handing the same instance to the warning and to the table costs one lookup
    instead of two.
    """
    history = attribution.AddressingHistory(wt_path)
    _warn_unattributed_fixes(
        content.of(FixOutcome.FIXED), cp,
        summary_model.folded_item_ids(content, threads_by_id),
        history, threads_by_id,
    )
    return history


def newest_reviewer_activity(report: PRReport) -> str:
    """The newest review or review comment on the PR that is not ours.

    Our own is excluded because the fix pass replies to threads before it
    publishes, and a run that counted its own replies would find the summary
    answered every single round.  An unknown login counts as somebody else's,
    the same way `ReportThread.my_login` reads "cannot tell" as not ours.

    Issue-level comments are not read here. `find_marker_comments` already lists
    them to locate the summary, so `MarkerComment.newest_other_at` covers them
    off that listing — and covers the bot comments the report filters out,
    which bury a summary as surely as a human's do.
    """
    mine = report.my_login.lower()
    stamps = [
        v.get("submitted_at", "") for v in report.verdicts
        if not mine or (v.get("user") or "").lower() != mine
    ]
    stamps += [
        c.get("createdAt", "")
        for thread in report.threads for c in thread.comments
        if not mine or ((c.get("author") or {}).get("login") or "").lower() != mine
    ]
    return max((s for s in stamps if s), default="")


def _answered_since(existing: pc.MarkerComment, activity_at: str) -> bool:
    """Whether anything was said on the PR below the published summary.

    Editing is invisible: GitHub leaves the comment where it was posted and
    notifies nobody.  So an edit is only honest while the summary is still the
    last word — once a reviewer has answered below it, the round's outcome
    would land in a comment the reader has already scrolled past, and the round
    posts a fresh summary instead.

    A target with no timestamp keeps the in-place edit rather than guessing.
    The lookup could not read when the comment was posted, and guessing "buried"
    there would append a duplicate summary on every round for the life of the PR.
    """
    if not existing.comment_id or not existing.created_at:
        return False
    return max(existing.newest_other_at, activity_at) > existing.created_at


def _earlier_rounds(
    marked: pc.MarkerHistory, target_id: int | None,
) -> list[summary_rounds.SummaryRound]:
    """The summary comments this round's footer links back to, oldest first.

    ``target_id`` is the comment being edited, left out because a comment
    linking itself tells the reader nothing. A fresh post names no target and
    links every one of them.
    """
    return [
        summary_rounds.SummaryRound(number, c.url)
        for number, c in enumerate(marked.comments, 1)
        if c.url and (target_id is None or c.comment_id != target_id)
    ]


def publish_summary(
    repo: str, pr_number: int, build_body: Callable[..., str],
    activity_at: str = "",
) -> str | None:
    """Publish the round without shrinking the record the PR already holds.

    `build_body(carried_over=..., scope=..., chain=...)` renders from local
    state. It is called again when the published comments hold rows this render
    must not write — rows it did not cover at all, and rows whose Action cell a
    human rewrote — because those rows have to reach the reader through the same
    body: the counts line and the table are one artifact, not two.

    Held rows are resolved before carried ones, and the carry-forward set is
    then computed against the body that already holds them, so a hand-written
    row is kept once rather than emitted twice.

    The comment is edited in place while nothing has been said below it, and
    posted fresh once something has — see `_answered_since`. Which one
    it is decides how much of the record this body has to carry. An edit
    replaces its target, so every row that target holds is re-rendered or
    carried forward. A fresh comment replaces nothing: the earlier rounds stay
    where they were posted, so this one describes its own round and links back
    to them — see `summary_rounds.RoundScope` and `_earlier_rounds`.

    A lookup that fails outright carries nothing: `MarkerHistory.comments` is
    empty, so this cannot tell comments it could not read from a PR that has
    none. Everything is then rendered and `post_issue_comment` posts rather than
    patching, on the standing trade that a duplicate comment beats a lost
    update — the earlier rounds stay readable in the comments this run could
    not reach.
    """
    marked = pc.find_marker_comments(repo, pr_number, SUMMARY_MARKER)
    existing = marked.newest
    answered = _answered_since(existing, activity_at)
    scope = summary_rounds.round_scope(marked, answered)
    render = functools.partial(
        build_body,
        scope=scope,
        chain=_earlier_rounds(marked, None if answered else existing.comment_id),
    )
    body = render(carried_over=[])
    hand_held = summary_scope.hand_written_rows(marked.bodies, body)
    for row in hand_held:
        log.warn(
            f"Keeping the hand-written Action cell on {row.key}: "
            f"{summary_scope.row_action_cell(row.published)!r} — this round would have "
            f"rendered {summary_scope.row_action_cell(row.replaced_by)!r}"
        )
    if hand_held:
        body = render(carried_over=[], hand_held=hand_held)
    # Only against the comment being replaced. A row on any other summary
    # comment is still published there, and lifting it into this one would
    # restate the round the chain already carries.
    carried = summary_scope.carried_over_rows(
        "" if answered else existing.body, body, scope.elsewhere_keys)
    if carried:
        log.warn(
            f"Published summary has {len(carried)} row(s) this run cannot account "
            "for — carrying them forward rather than dropping them"
        )
        body = render(carried_over=carried, hand_held=hand_held)
    if answered:
        log.info(
            "The published summary has been answered since it was posted — "
            "posting a fresh one scoped to this round rather than editing a "
            "comment nobody will re-read"
        )
        return pc.post_issue_comment(repo, pr_number, body)
    return pc.post_issue_comment(
        repo, pr_number, body, marker=SUMMARY_MARKER, existing=existing,
    )


def post_fix_summary(
    content: summary_model.RoundContent,
    cp: attribution.CommitPushResult,
    repo: str,
    pr_number: int,
    threads_by_id: dict[str, ReportThread],
    has_comment_items: bool = False,
    head_sha: str = "",
    activity_at: str = "",
    wt_path: Path | None = None,
    history: attribution.AddressingHistory | None = None,
) -> str | None:
    """Post summary issue comment to the PR. Returns the comment URL or None."""
    if not content.has_content:
        return None
    url = publish_summary(repo, pr_number, functools.partial(
        summary_render.build_summary_body,
        content, cp, repo, pr_number, threads_by_id,
        has_comment_items=has_comment_items,
        head_sha=head_sha,
        wt_path=wt_path,
        history=history,
    ), activity_at=activity_at)
    if url:
        log.info(f"Posted fix summary: {url}")
    elif publishing.enabled():
        log.error("failed to post fix summary")
    return url


def summary_still_owed(
    content: summary_model.RoundContent, commit_status: str, has_unaccounted: bool,
) -> bool:
    """Whether this round has a fix summary the PR has not been told about.

    Discussion that is still open, a commit that never reached the remote, or
    threads this pass could not account for all make the summary premature, so
    it has to be rendered again later whatever went out. Otherwise the round
    owes a summary exactly when it has one to render, which is
    `summary_model.RoundContent.has_content` — not the outcomes this pass produced. A round
    settled entirely as `already_addressed`, by the agent or by `--settle`,
    renders a full table while producing neither a fix nor a dismissal, and a
    clause naming only those two buckets closes it out with the published
    comment still holding the previous round's rows.

    Whether the summary then went out is not asked here: the caller pairs this
    with `summary_url is None`, so a draft that printed the table and a post the
    API refused both leave it owed without this reading the publishing gate.

    A hand-written Action cell does not retire the render this owes, and is not
    read here. The question is whether local state still has something to say,
    and it does: the thread is recorded as `needs_human` either way, and the
    render that answers this is now the one that preserves the cell. Reading the
    published comment from here would cost a second listing per run to change
    nothing but which of two harmless renders happens.
    """
    if content.of(FixOutcome.DEFERRED) or content.needs_a_person or has_unaccounted:
        return True
    if attribution.commit_unpushed(commit_status):
        return True
    return content.has_content


def post_or_defer_summary(
    content: summary_model.RoundContent,
    cp: attribution.CommitPushResult,
    repo: str,
    pr_number: int,
    threads_by_id: dict[str, ReportThread],
    has_comment_items: bool = False,
    head_sha: str = "",
    activity_at: str = "",
    wt_path: Path | None = None,
) -> str | None:
    """Post summary immediately or defer to state when discussion is pending.

    Returns the comment URL if posted, None if deferred or nothing to post.
    When deferred, the summary is re-rendered from FixSummary during --finish.
    """
    history = _warned_history(content, cp, threads_by_id, wt_path)
    if attribution.commit_unpushed(cp.status):
        log.info("Deferred fix summary — commit not on the remote, will post after push lands")
        return None

    if not content.needs_a_person and not content.of(FixOutcome.DEFERRED):
        return post_fix_summary(
            content, cp, repo, pr_number, threads_by_id,
            has_comment_items=has_comment_items,
            head_sha=head_sha,
            activity_at=activity_at,
            wt_path=wt_path,
            history=history,
        )

    log.info("Deferred fix summary — will render from state on --finish")
    return None


def render_deferred_summary(
    state: pr_state.PRState, report: PRReport, repo: str, pr_number: int,
    threads_by_id: dict[str, ReportThread],
) -> None:
    """Re-render fix summary from state and post it.

    Called during --finish after the deferred tracking issue has been created
    and stored in state.fix.  Rebuilds the summary body from the recorded
    outcomes so the issue link renders inline — no string patching.
    Updates state.fix.summary_url and summary_deferred on success.
    """
    fix = state.fix
    record = fix.fix
    if not fix.summary_deferred:
        return

    wt_path = Path(state.identity.worktree_root) if state.identity.worktree_root else None

    status = record.commit_status
    if attribution.commit_unpushed(record.commit_status) and record.commit_sha:
        if not wt_path or not push.holds(wt_path, record.commit_sha):
            log.info("Push still pending — keeping summary deferred")
            return
        status = CommitStatus.PUSHED
        # Same reason as the deferred-reply queue: only a run that actually
        # publishes may retire an unpushed status, or the queue is lost silently.
        if publishing.enabled():
            record.commit_status = status

    # A plain partition over every outcome, with nothing dropped: this renderer
    # is the only one --finish reaches, and the condition that routes here is
    # the presence of needs_human, so omitting that bucket would omit exactly
    # the threads that took the most operator judgment.
    #
    # _finish_deferred_work reconciles the snapshot against GitHub before
    # calling this, so a thread settled outside the fix pass has already left
    # DEFERRED and NEEDS_HUMAN — for FIXED where our own reply names the
    # verdict, and for SETTLED_ELSEWHERE where the resolve button is the whole
    # of the evidence.
    #
    # The rows are entries again before any of it: the reviewer column, the
    # permalink and the fold key are all read off an entry, and rebuilding them
    # here is what leaves every renderer downstream with one type to handle.
    by_outcome: dict[FixOutcome, list[CommentItem]] = {}
    for o in record.items:
        by_outcome.setdefault(o.outcome, []).append(
            CommentItem.from_outcome(o, fix.reviewers.get(o.id, "")),
        )
    content = summary_model.RoundContent(
        by_outcome=by_outcome,
        issue_comments=report.issue_comments,
        review_body_comments=report.review_body_comments,
    )

    cp = history_rewrite.reconciled_commit(record, status, wt_path)
    history = _warned_history(content, cp, threads_by_id, wt_path)
    url = publish_summary(repo, pr_number, functools.partial(
        summary_render.build_summary_body,
        content, cp, repo, pr_number, threads_by_id,
        deferred_issue_id=fix.deferred_issue_id,
        deferred_issue_url=fix.deferred_issue_url,
        has_comment_items=fix.has_comment_items,
        head_sha=cp.sha or state.identity.head_sha,
        wt_path=wt_path,
        history=history,
    ), activity_at=newest_reviewer_activity(report))
    if url:
        log.info(f"Posted deferred fix summary: {url}")
        fix.summary_url = url
        fix.summary_deferred = False
    elif publishing.enabled():
        log.error("failed to post deferred fix summary")
