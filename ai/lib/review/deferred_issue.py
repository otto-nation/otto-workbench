"""The tracking issue a fix pass owes the threads it deferred.

A deferred thread is a reviewer's finding that nobody has answered and nobody
has fixed. The PR carries no record of it once the round closes, so this files
one: an issue listing every deferred thread, linked from a reply on each. What
makes it worth a module rather than a function is the failure mode — nothing
gets filed and the threads have no home — which is silent in four distinct
ways, and `IssueResult` exists so each of them reaches `pr status` instead of
only the trail.

Layer 6, beside `review.issue`, which owns issue creation and the
`CreatedIssue`/`IssueDelivery`/`IssueResult` vocabulary this reads. That
dependency is the whole reason this is not at layer 4 with the rest of the
comment pass: everything else it touches is `pr` (4) or below, so if those
types ever moved down, this module would follow them rather than stay.

What is not here: who decides a thread is deferred (`pr.triage`, `fix.comments`),
how the replies pointing at the issue are written (`pr.thread_replies`), and the
summary comment that renders the same deferral as a row
(`pr.summary_render`) — see `finalize_deferred` for the one ordering
dependency between that surface and this one.
"""

# doc-group: publishing

from __future__ import annotations

import sys

from config import workbench_config
from core import log
from core import markdown
from core import publishing
from core.trail import Trail
from pr import context as pr_context
from pr import permalinks
from pr import state as pr_state
from pr import thread_replies
from pr.fix import FixOutcome
from pr.thread_models import CommentItem, ReportThread
from review import issue as review_issue
from review.issue import CreatedIssue, IssueDelivery, IssueResult

# The columns of the tracking issue's table. Three, not the summary comment's
# four: this table is written and never read back, so it carries what a person
# needs to triage the backlog rather than what a parser needs to recover a row.
_TABLE_COLUMNS = ("Thread", "File", "Reason")


class TrackAll(frozenset):
    """Sentinel for --track-all: every id is selected.

    A frozenset subclass rather than a truthy string so `t.id in track` reads
    the same for the ordinary per-thread case and this one.
    """

    def __contains__(self, item) -> bool:
        return True

    def __bool__(self) -> bool:
        # Inherited from frozenset, this would be False: the instance holds no
        # elements. It selects every id, so reporting itself as empty is the
        # opposite of the truth for anyone who asks.
        return True


TRACK_ALL = TrackAll()


def deferred_outcomes(state: pr_state.PRState) -> list:
    """Every outcome the fix pass deferred, whatever became of it since.

    One reading of the record for the three questions asked of it — which
    threads to validate `--track` against, which to file, and which to report
    as unfiled. Spelled out at each site instead, the filter is three copies of
    one rule about what `DEFERRED` means, and the one that drifts is the one
    whose surface nobody was looking at.
    """
    return [o for o in state.fix.fix.items if o.outcome == FixOutcome.DEFERRED]


def validate_track(state: pr_state.PRState, track) -> None:
    """Exit if --track named anything that is not a deferred thread.

    A typo'd id would otherwise be indistinguishable from a thread the tool
    chose not to file, and the user would read "filed nothing" as agreement.

    Runs against an empty snapshot too. An empty selection has no unknown ids
    and says nothing, so the guard costs an ordinary no-op run no noise while
    still catching the id that could never have matched.

    ceiling: this exits rather than returning a complaint, which is why it can
    only be called from a path the CLI owns. The alternative — returning the
    unknown ids and letting the caller exit — is what `pr.settlement`'s
    `settle_targets` does, and is the better shape for a library. Upgrade
    trigger: when `cli/review_threads.py` lands and `main` must return rather
    than exit, convert this with it.
    """
    if track is TRACK_ALL:
        return
    unknown = set(track) - {o.id for o in deferred_outcomes(state)}
    if unknown:
        log.error(f"--track named threads that are not deferred: {sorted(unknown)}")
        sys.exit(1)


def finalize_deferred(
    state: pr_state.PRState,
    ctx: pr_context.ResolvedContext,
    threads_by_id: dict,
    trail: Trail | None = None,
    *,
    track=frozenset(),
) -> None:
    """Create tracking issue and post deferred replies for threads named by --track.

    Mutates `state` and leaves the save to the caller, which mutates the same
    object either side of this call. Reading the file again here and saving
    that copy would drop whatever the caller had already written.

    The issue id and url written at the end are read two ways, and the second
    is easy to miss: `pr.summary_publish.render_deferred_summary` renders the
    tracking-issue link into the summary comment's deferred rows, reading
    `state.fix.deferred_issue_id`/`_url` off the same in-memory object. It runs
    immediately after this in `--finish`, so **the summary's link exists only
    because this ran first**. Reordering the two, or giving the summary its own
    copy of the state, drops the link with nothing failing.

    `track` defaults to selecting nothing. Filing a thread on the tracking
    issue posts a reply, under the PR author's name, saying a reviewer's
    finding was triaged and postponed — a deferral is a decision someone makes
    per thread, never the fallback disposition for whatever a fix pass failed
    to fix.
    """
    # Validation precedes the empty-snapshot return rather than following it.
    # An id naming no deferred thread is an operator asking for something that
    # cannot happen, and an empty snapshot is the one case where nothing else
    # would say so — filing nothing there is indistinguishable from success.
    # The ordinary no-op stays silent on its own: no `--track` selects nothing,
    # so there is no unknown id to report.
    validate_track(state, track)

    if not state.fix.fix.items:
        return

    # `reason` carries why the thread was held back, and it is the only column
    # in the tracking issue that distinguishes an agent that gave up from a
    # decision anyone made — so the outcome's reason lands there rather than in
    # the `reasoning` the reply templates read.
    deferred = [
        CommentItem.from_outcome(o, state.fix.reviewers.get(o.id, ""))
        for o in deferred_outcomes(state)
        if o.id in track
    ]
    if not deferred or not ctx.pr_number:
        return

    result = create_or_update_deferred_issue(
        deferred, ctx.repo, ctx.pr_number, threads_by_id,
        ctx, state.fix.deferred_issue_id, trail,
        state.fix.deferred_issue_url,
    )
    issue = result.issue

    if issue.id:
        thread_replies.post_deferred_replies(
            deferred, threads_by_id, ctx.repo, ctx.pr_number,
            issue.id, issue.url, ctx.require_worktree(),
        )

    state.fix.deferred_issue_id = issue.id
    state.fix.deferred_issue_url = issue.url
    # An issue that was owed and did not get filed leaves these threads pointing
    # at something that does not exist. The trail records that too, but nobody
    # reads the trail to decide whether a PR is safe to merge — they read
    # `pr status`, which reads this.
    state.fix.deferred_issue_pending = result.owed


def report_unfiled_deferrals(state: pr_state.PRState, track) -> None:
    """Name the deferrals nobody asked to file, so the omission is visible.

    Filing nothing is the correct default, but a silent nothing reads as "there
    was nothing to file". List the ids so the user can pass the ones they meant.

    Membership, not truthiness: a partial selection leaves the unnamed threads
    just as unfiled as no selection does, and they are the easiest ones to lose
    — the run reports success for the few that were named.

    Runs after `finalize_deferred` so a typo'd `--track` is reported by
    `validate_track` before this prints a list the operator would read as the
    whole story.
    """
    still = [o for o in deferred_outcomes(state) if o.id not in track]
    if not still:
        return
    log.info(
        f"{len(still)} deferred thread(s) not filed — pass --track <id> for "
        f"each one to track, or --track-all: {', '.join(o.id for o in still)}"
    )


def build_deferred_issue_body(
    deferred: list[CommentItem],
    repo: str,
    pr_number: int,
    threads_by_id: dict[str, ReportThread],
) -> str:
    """Build markdown description for the deferred threads tracking issue.

    Unlike the summary comment this body is written and never read back, so
    nothing here has to survive a round trip and the wordings carry no parse
    contract. The pipe escaping is still load-bearing for an ordinary reason: a
    reason or summary containing one would shift every later cell and render
    the backlog as a broken table.
    """
    pr_url = f"https://github.com/{repo}/pull/{pr_number}"
    parts = [
        f"## Deferred Review Comments — [PR #{pr_number}]({pr_url})",
        "",
        markdown.render_row(list(_TABLE_COLUMNS)),
        markdown.table_divider(len(_TABLE_COLUMNS)),
    ]
    for entry in deferred:
        file_cell = f"`{entry.file}:{entry.line}`" if entry.file else "—"
        parts.append(markdown.render_row([
            permalinks.thread_cell(entry, threads_by_id, repo, pr_number),
            file_cell,
            markdown.escape_cell(entry.reason or "—"),
        ]))
    parts.append("")
    return "\n".join(parts)


def update_deferred_issue(
    provider: str, issue_id: str, body: str, repo: str, opts: dict | None,
) -> IssueResult:
    """Refresh the tracking issue that already exists for this cycle.

    Filed either way: an update that did not land leaves the issue stale rather
    than missing, so the deferred threads still have the home the closeout debt
    exists to insist on.
    """
    ok = review_issue.update_issue(provider, issue_id, body, repo=repo, opts=opts)
    if not ok:
        log.error(f"Failed to update deferred issue {issue_id}")
        return IssueResult(IssueDelivery.FILED, CreatedIssue(id=issue_id))
    url = review_issue.get_issue_url(provider, issue_id)
    return IssueResult(IssueDelivery.FILED, CreatedIssue(id=issue_id, url=url))


def _no_team_key(
    provider: str, trail: Trail | None, *, publishing_open: bool,
) -> IssueResult:
    """Report a tracker this run cannot address, and say what it costs.

    A provider that keys issues by team with no team to name is a fourth route
    to the outcome the closeout debt exists to catch: nothing gets filed and the
    deferred threads have no home. So it is owed while the gate is open, and a
    non-event while it is shut — a draft run has failed at nothing it would
    otherwise have attempted.

    The key comes from configuration alone, so the remediation is one command
    and the message names it. Both wordings spell the command rather than the
    file: told only the key, a reader writes the config by hand under whatever
    name the worktree in front of them happens to use, while the command is
    what checks that name against the workbench doing the reading.
    """
    remedy = (
        f"run otto-workbench config set {workbench_config.ISSUE_TEAM_KEY} TEAM --project"
    )
    if not publishing_open:
        log.dim(
            f"No team key for {provider} issue creation — skipping;"
            f" to file these, {remedy}",
        )
        if trail:
            trail.info("deferred_issue", "skipped — no team key")
        return IssueResult(IssueDelivery.SKIPPED)

    log.error(
        f"No team key for {provider} — deferred tracking issue not filed;"
        f" {remedy}",
    )
    if trail:
        trail.error("deferred_issue", "no team key")
    return IssueResult(IssueDelivery.UNDELIVERED)


def create_deferred_issue(
    provider: str,
    body: str,
    ctx: pr_context.ResolvedContext,
    repo: str,
    pr_number: int,
    opts: dict | None,
    trail: Trail | None,
    *,
    publishing_open: bool,
) -> IssueResult:
    # Kept for the tracker's own use rather than for the team key: Linear takes
    # it as `--parent`, so the tracking issue hangs off whatever issue the
    # branch names.
    parent_id = review_issue.extract_issue_id(provider, ctx.branch)
    # The team comes from configuration and nowhere else. Splitting it out of a
    # parent issue id made filing depend on what the run was invoked against
    # rather than on how the repo is configured, and assumed an id format that
    # `needs_team_key` deliberately keeps as a per-provider fact.
    team = (opts or {}).get("team", "")
    if not team and review_issue.needs_team_key(provider):
        return _no_team_key(provider, trail, publishing_open=publishing_open)

    title = f"fix(review): deferred review comments — PR #{pr_number}"
    result = review_issue.create_issue(
        provider, team, title, body, parent_id=parent_id, repo=repo, opts=opts,
    )
    if result.filed:
        if trail:
            trail.info(
                "deferred_issue",
                f"created {result.issue.id}",
                data={"url": result.issue.url},
            )
        return result
    if result.owed:
        log.error("Failed to create deferred tracking issue")
        if trail:
            trail.error("deferred_issue", "creation failed")
        return result

    # The publishing gate declined the write, which is the gate working. Saying
    # it failed here would put a spurious error in the closeout `pr status`
    # reads, on every draft run.
    log.dim("Deferred tracking issue not filed — publishing is off")
    if trail:
        trail.info("deferred_issue", "skipped — publishing off")
    return result


def create_or_update_deferred_issue(
    deferred: list[CommentItem],
    repo: str,
    pr_number: int,
    threads_by_id: dict[str, ReportThread],
    ctx: pr_context.ResolvedContext,
    existing_issue_id: str,
    trail: Trail | None,
    existing_issue_url: str = "",
) -> IssueResult:
    """Create or update the deferred threads tracking issue.

    Reports whether the issue was delivered, not only what it produced: the
    caller records an owed-and-unfiled issue in state, which is what puts it in
    front of `pr status` instead of leaving it in the trail alone.
    """
    if not deferred:
        return IssueResult(IssueDelivery.SKIPPED, CreatedIssue(id=existing_issue_id))

    worktree = str(ctx.require_worktree())
    publishing_open = publishing.enabled()
    # A draft run files nothing — create_issue gates on publishing.enabled() —
    # so asking which tracker to file to would be a question with no consequence.
    if publishing_open:
        provider_info = review_issue.ensure_issue_provider(worktree)
    else:
        provider_info = review_issue.load_issue_provider(worktree)
    if not provider_info.resolved:
        # ensure_issue_provider has already said why. The delivery is what the
        # caller records, and what `pr status` reads: with the gate open the
        # deferred threads are owed an issue nothing can file, while a draft run
        # has failed at nothing it would otherwise have attempted.
        if trail and publishing_open:
            trail.error("deferred_issue", "no issue tracker configured")
        elif trail:
            trail.info("deferred_issue", "skipped — no issue tracker configured")
        return IssueResult(
            IssueDelivery.UNDELIVERED if publishing_open else IssueDelivery.SKIPPED,
        )
    provider = provider_info.name

    body = build_deferred_issue_body(deferred, repo, pr_number, threads_by_id)

    if existing_issue_id:
        updated = update_deferred_issue(
            provider, existing_issue_id, body, repo, provider_info.options,
        )
        return IssueResult(updated.delivery, CreatedIssue(
            id=updated.issue.id, url=updated.issue.url or existing_issue_url,
        ))

    return create_deferred_issue(
        provider, body, ctx, repo, pr_number, provider_info.options, trail,
        publishing_open=publishing_open,
    )
