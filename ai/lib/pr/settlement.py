"""How a review thread ends when the fix pass is not what ended it.

Two ways in, one vocabulary. Reconciliation asks GitHub what became of the
threads a snapshot still calls unsettled — a reply of ours naming the verdict, a
resolve button pressed by someone else — and rewrites the rows GitHub
contradicts. `--settle` is the operator stating an ending outright, for work
they did by hand where nothing the tool can read was left behind.

Both write the same outcomes onto the same records, and both have to agree on
what evidence counts, which is why they are one module rather than two. The
grade of the evidence is the thing they share: a standing reply of ours names a
verdict and supports FIXED, a resolve button on its own settles the thread
without saying anybody fixed anything and supports only SETTLED_ELSEWHERE.
Getting that wrong publishes a false claim about someone's code.

What is not here: the summary that renders these endings, the replies that
announce them, and the argparse layer that spells `--settle`. This module
decides what happened and records it; the surfaces read the record.
"""

# ceiling: the two halves are kept in one module for the shared vocabulary
# alone — past that they overlap in almost nothing, reconciliation reaching for
# the comment and permalink readers while --settle reaches for git and the
# worktree. A reader asking how a thread can end should see both endings
# together, which is worth the disjoint imports at this size.
# Upgrade trigger: once this file passes ~600 lines, split it into
# `reconciliation.py` and `settle.py` and leave the shared outcome sets here —
# the import sets already name the seam.

# doc-group: publishing

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from core import log
from git import client as git_client
from git import push
from pr import attribution
from pr import comments as pc
from pr import comments_fix as pr_comments_fix
from pr import context as pr_context
from pr import permalinks
from pr import state as pr_state
from pr import thread_replies
from pr.comments_state import ThreadState
from pr.fix import (
    RECONCILED_REASON, SETTLED_REASON, FixOutcome, FixRecord, ItemOutcome,
    SettledBy,
)
from pr.thread_models import CommentItem, ReportThread, finding_location


# The terminal outcomes --settle can record. Each is one of the three the
# closeout knows how to reply to, which is the whole point of recording one: the
# thread rejoins the ordinary path instead of staying open for the life of the
# PR. DEFERRED is deliberately absent — it is already reachable, through
# --track, and it says work is still owed rather than settled.
SETTLE_OUTCOMES = (
    FixOutcome.FIXED,
    FixOutcome.DISMISSED,
    FixOutcome.ALREADY_ADDRESSED,
)

# The outcomes a later run re-checks against GitHub before republishing them.
# Each records work someone still owes, and each has an ending the tool never
# sees — an operator who fixes the thread by hand, a reviewer who accepts the
# answer and resolves it. DECLINED sits here for the same reason NEEDS_HUMAN
# does: the pass handed the thread to a person, so a person settling it is the
# expected ending rather than the exception. This is also the set --settle
# offers when an id it was given matches nothing: reconciliation infers those
# endings from GitHub, and --settle is how the operator states one outright.
UNSETTLED_OUTCOMES = (
    FixOutcome.DEFERRED,
    FixOutcome.NEEDS_HUMAN,
    FixOutcome.DECLINED,
)

# The id in a permalink back to a top-level comment. Capturing, unlike the
# summary's `_ITEM_ANCHOR_RE`, because the id is the whole point here.
_SOURCE_ANCHOR_RE = re.compile(r"#(?:issuecomment|pullrequestreview)-(\d+)")


def settlement_for(thread: ReportThread | None) -> FixOutcome | None:
    """What GitHub shows became of this thread, or None when it shows nothing.

    Two grades of evidence, and which one it is decides what may be claimed. A
    standing reply of ours — applied, already addressed, dismissed — names the
    verdict outright, so the thread reads as FIXED however its resolve button
    stands.

    Resolution on its own names nothing of the sort. The button covers a
    reviewer who was answered, who deferred the point, or who withdrew it, as
    readily as one whose fix landed, so it settles the thread without saying
    anybody fixed anything: SETTLED_ELSEWHERE. Both grades disqualify the thread
    from being reported as deferred, which is what this is asked for; only the
    first is a fix.
    """
    if not thread:
        return None
    if any(
        str(c.get("body", "")).startswith(thread_replies.HANDLED_REPLY_PREFIXES)
        for c in thread.comments
    ):
        return FixOutcome.FIXED
    if thread.is_resolved or thread.state in (ThreadState.RESOLVED, ThreadState.ADDRESSED):
        return FixOutcome.SETTLED_ELSEWHERE
    return None


def answered_comment_sources(
    outcomes: list[ItemOutcome],
    repo: str, pr_number: int, my_login: str,
) -> frozenset[str]:
    """Source comment ids our standing reply on the PR reports as handled.

    A decomposed comment item has no review thread, so `settlement_for`
    has nothing to read for one: the item is a fragment of a top-level comment,
    and a top-level comment carries no thread of its own. What it does carry is
    a reply of ours further down the PR citing its permalink — the same evidence
    a thread reply is, on the only surface the source has.

    Costs one listing, and only when the snapshot holds an unsettled item that
    such a reply could settle.
    """
    if not any(
        o.outcome in UNSETTLED_OUTCOMES and permalinks.comment_item_source(o).ok
        for o in outcomes
    ):
        return frozenset()
    if not my_login:
        # Without a login there is no telling our reply from the reviewer
        # restating their own point, and reading theirs as an answer would
        # settle the item on the strength of the complaint.
        log.warn(
            "Cannot identify our own comments — leaving comment items unreconciled"
        )
        return frozenset()
    mine = my_login.lower()
    # include_self, because the reply being looked for is ours and the listing
    # drops our own comments by default.
    answered: set[str] = set()
    for comment in pc.fetch_issue_comments(
        repo, pr_number, my_login, include_self=True,
    ):
        if str(comment.get("user", "")).lower() != mine:
            continue
        body = str(comment.get("body", ""))
        if not body.startswith(thread_replies.HANDLED_REPLY_PREFIXES):
            continue
        answered.update(m.group(1) for m in _SOURCE_ANCHOR_RE.finditer(body))
    return frozenset(answered)


def entry_settlement(
    entry: CommentItem,
    threads_by_id: dict[str, ReportThread],
    answered_sources: frozenset[str],
    handled_locations: dict[str, FixOutcome],
) -> FixOutcome | None:
    """What GitHub shows became of this snapshot row, or None when nothing did.

    Two kinds of row reach here. A review thread carries its own evidence and is
    read directly. A comment item has none to carry — looking its synthetic id
    up among review threads can only ever miss — so it is settled either by its
    source comment having been answered, or by a review thread saying the same
    thing about the same line being settled: triage decomposes a top-level
    comment without knowing which of its points an inline thread already covers.

    An item settled through a thread inherits that thread's grade rather than
    being promoted: the evidence is the thread's, so the claim it supports is
    too. An answered source is our own reply naming the verdict, which is the
    same evidence a thread reply is.
    """
    thread = threads_by_id.get(entry.id)
    if thread:
        return settlement_for(thread)
    source = permalinks.comment_item_source(entry)
    if not source.ok:
        return None
    if source.id in answered_sources:
        return FixOutcome.FIXED
    return handled_locations.get(finding_location(entry))


def settled_locations(
    threads_by_id: dict[str, ReportThread],
) -> dict[str, FixOutcome]:
    """Each code location a settled thread occupies, and how it was settled.

    Keyed by `finding_location`, so a decomposed comment item restating an
    inline thread can find it. A location two settled threads share takes the
    better-evidenced of the two: a reply naming the verdict outranks a resolve
    button that names nothing.
    """
    located: dict[str, FixOutcome] = {}
    for thread in threads_by_id.values():
        settlement = settlement_for(thread)
        key = finding_location(thread)
        if not settlement or not key:
            continue
        if settlement.counts_as_fixed or key not in located:
            located[key] = settlement
    return located


def reconcile_fix_snapshot(
    state: pr_state.PRState, threads_by_id: dict[str, ReportThread],
    answered_sources: frozenset[str] = frozenset(),
) -> int:
    """Flip snapshot outcomes that GitHub contradicts. Returns the flip count.

    The snapshot records what one fix pass concluded, and `FixRecord.merge_into`
    accumulates it across rounds without ever expiring an outcome. Work done by
    hand in between never passes through it, so a thread fixed outside the tool
    stays DEFERRED forever. Publishing that is a false claim about someone's
    code, so GitHub is consulted before anything is written.

    NEEDS_HUMAN and DECLINED are the same problem under another name, and a
    worse case of it: both outcomes exist precisely because the pass handed the
    thread to the operator, so the operator settling it is the expected ending,
    not an exception. Reporting it as still awaiting discussion after it has
    been answered and pushed is the claim this is here to prevent.

    `answered_sources` is what GitHub said about the top-level comments behind
    the decomposed items — see `answered_comment_sources`. It defaults to
    nothing asked, which is the honest reading for a caller that consulted only
    the review threads.

    What each row flips *to* is graded by the evidence behind it, not fixed at
    FIXED — see `settlement_for`. A thread whose only evidence is the resolve
    button lands on SETTLED_ELSEWHERE, so it leaves the deferred bucket without
    being counted as work this cycle did or attributed to a commit.
    """
    handled_locations = settled_locations(threads_by_id)
    flipped = 0
    for outcome in state.fix.fix.items:
        if outcome.outcome not in UNSETTLED_OUTCOMES:
            continue
        entry = CommentItem.from_outcome(
            outcome, state.fix.reviewers.get(outcome.id, ""),
        )
        settlement = entry_settlement(
            entry, threads_by_id, answered_sources, handled_locations,
        )
        if settlement is None:
            continue
        outcome.outcome = settlement
        outcome.settled_by = SettledBy.RECONCILIATION
        outcome.reason = RECONCILED_REASON
        flipped += 1
    if flipped:
        log.info(f"Reconciled {flipped} stale outcome(s) against GitHub")
    return flipped


def resolve_fixed_threads(
    fixed: list[CommentItem],
    threads_by_id: dict[str, ReportThread],
) -> list[ThreadState]:
    """Resolve fixed threads on GitHub. Returns the bucket each one came from.

    Entries absent from threads_by_id are silently skipped — they are either
    comment items with synthetic IDs (ic-…/rb-…) that are not real review
    threads, or items added by callers for a different purpose.  Do not add
    comment items to `fixed` expecting them to be resolved here.

    The prior buckets rather than a count, because every caller resolves threads
    after the persisted tally was written and has to tell it which buckets these
    left — see `CommentsSummary.move_to_resolved`.
    """
    priors: list[ThreadState] = []
    for entry in fixed:
        thread = threads_by_id.get(entry.id)
        if thread is None:
            continue
        if thread.is_resolved:
            continue
        if pc.resolve_thread(entry.id):
            # Written back so a second caller over the same report skips it. A
            # combined --fix --finish run resolves the already-addressed bucket
            # in the fix pass and drains it again in the closeout, off the same
            # thread objects; without this the guard above never fires and the
            # bucket is counted out of the tally twice.
            thread.is_resolved = True
            priors.append(thread.state)
    if priors:
        log.info(f"Resolved {len(priors)} fixed thread(s)")
    return priors


@dataclass(frozen=True)
class SettledCommit:
    """The commit a hand-settled fix may cite, or why it may cite none.

    Three answers, not two: a commit to name, no commit to name, and an operator
    to stop. Only the last is an error — a settlement whose commit cannot be
    resolved is still worth recording, it just renders without a link.
    """

    sha: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


def resolve_settled_commit(
    wt_path: Path, outcome: ItemOutcome, explicit: str,
) -> SettledCommit:
    """Which commit carries a fix the operator landed by hand.

    Resolved here rather than at render time, and the difference matters: this
    is the one moment the worktree, the remote and the operator are all
    available to ask. A SHA inferred while rendering the closeout could name a
    commit that never left the machine, and the reply citing it would 404 for
    the reviewer it was written for.

    `explicit` is the operator overriding the inference, for a fix that landed
    somewhere other than the line the thread is anchored to. It is held to a
    stricter standard than the inferred answer for the same reason: they named
    that commit, so silently declining to cite it would leave them reading a row
    that says less than they asked it to.

    The answer comes back abbreviated, because that is the width every other SHA
    this command persists carries — see `attribution.pass_commit`. Git is asked in full
    first: `push.holds` compares ancestry, and an operator may well have typed a
    tag or a branch name rather than a SHA at all.
    """
    if explicit:
        sha = git_client.out(
            "rev-parse", "--verify", "--quiet", f"{explicit}^{{commit}}", cwd=wt_path,
        )
        if not sha:
            return SettledCommit(
                error=f"--commit names no commit in this worktree: {explicit}"
            )
        if not push.holds(wt_path, sha):
            return SettledCommit(error=(
                f"--commit {git_client.abbrev(sha)} is not on the remote — push it "
                f"first, or the reply citing it sends the reviewer to a 404"
            ))
        return SettledCommit(sha=git_client.abbrev(sha))
    sha = attribution.find_addressing_commit(wt_path, outcome.file, outcome.line) or ""
    if sha and push.holds(wt_path, sha):
        return SettledCommit(sha=git_client.abbrev(sha))
    return SettledCommit()


def settle_targets(record: FixRecord, targets: list[str]) -> list[ItemOutcome] | None:
    """The recorded outcomes `--settle` named, or None once it has said what is missing.

    Every id is checked before any is written. A run that settled the first two
    of three and exited on a typo in the fourth would leave the operator to work
    out which half landed, which is the state surgery this command exists to
    replace.

    An unknown id is an error rather than a silent skip, for the reason
    `_validate_track` gives: "settled nothing" and "settled the thread you meant"
    are indistinguishable from the outside, so a typo would read as agreement.
    """
    by_id = {o.id: o for o in record.items if o.id}
    unknown = sorted({t for t in targets if t not in by_id})
    if not unknown:
        return [by_id[t] for t in targets]
    log.error(f"--settle named threads the fix pass never recorded: {unknown}")
    settleable = sorted(o.id for o in record.items if o.outcome in UNSETTLED_OUTCOMES)
    log.info(
        f"Threads a person still owes an answer: {', '.join(settleable)}"
        if settleable else "No thread in the fix snapshot is waiting on a person."
    )
    return None


def record_settlement(
    outcome: ItemOutcome, kind: FixOutcome, reason: str, sha: str,
) -> bool:
    """Write one settlement onto its recorded outcome. False when it said this already.

    The whole triple decides: an outcome re-settled the same way but with a
    commit that has since become resolvable has changed, and reporting it as a
    no-op would leave a row uncited that could now name its commit.

    A resolved SHA replaces nothing when there is none to replace — an earlier
    round's attribution outlives a re-settle that could not improve on it. Only
    within one ending, though: a row re-settled as dismissed drops the commit it
    was fixed in, because "dismissed, fixed in abc1234" is not a state the
    operator can have meant, and a reader of the raw snapshot has no way to tell
    which half of it is stale.
    """
    before = (outcome.outcome, outcome.reason, outcome.commit_sha)
    outcome.outcome = kind
    outcome.settled_by = SettledBy.OPERATOR
    # The operator's words for a dismissal, because that is the one settlement
    # whose reply is an argument the reviewer may answer. The other two render
    # from the summary, so their reason says what the provenance field records.
    outcome.reason = reason if kind is FixOutcome.DISMISSED else SETTLED_REASON
    outcome.commit_sha = (sha or outcome.commit_sha) if kind.may_cite_a_commit else ""
    return (outcome.outcome, outcome.reason, outcome.commit_sha) != before


def settled_commits(
    wt_path: Path, picked: list[ItemOutcome], kind: FixOutcome, commit: str,
) -> list[str] | None:
    """One citation per settlement, or None once one of them has failed to resolve.

    Every commit is resolved before any outcome is written, for the reason
    `settle_targets` checks every id before any is written: a run that settles
    two of three and stops on the third leaves the operator to work out which
    half landed. Nothing here mutates an outcome, so returning None discards the
    whole run rather than half of it.

    Only a fix cites a commit — the other two settlements render from the
    summary — so the rest come back uncited without git being asked at all.
    """
    if not kind.may_cite_a_commit:
        return ["" for _ in picked]
    resolved = [resolve_settled_commit(wt_path, o, commit) for o in picked]
    failed = next((r for r in resolved if not r.ok), None)
    if failed:
        log.error(failed.error)
        return None
    return [r.sha for r in resolved]


def report_settlement(
    outcome: ItemOutcome, kind: FixOutcome, was: FixOutcome, sha: str,
) -> None:
    """Say what was recorded, what it replaced, and how the closeout will render it.

    The uncited case earns its own line because the operator's next move depends
    on it: the row is settled either way, but it names no commit until the fix is
    pushed or `--commit` points at the one that carries it.
    """
    replaced = f" (was {was.value})" if was != kind else ""
    cited = f", fixed in {sha}" if sha else ""
    log.info(f"{outcome.id}: recorded as {kind.value}{replaced}{cited}")
    if kind.may_cite_a_commit and not sha:
        log.info(
            f"{outcome.id}: no pushed commit found for {outcome.file}:"
            f"{outcome.line} — the summary row will read "
            f"\"{pr_comments_fix.RECONCILED_STATUS_TEXT}\". Push the fix and re-run "
            f"--settle, or name it with --commit, to cite it"
        )


def settle_flag_error(kind: FixOutcome, reason: str, commit: str) -> str:
    """What is wrong with this combination of --as, --reason and --commit, or "".

    A flag that does not apply is refused rather than ignored. An operator who
    typed a justification and watched the run succeed has every reason to
    believe the reviewer will read it.
    """
    if kind is FixOutcome.DISMISSED and not reason:
        return (
            "--as dismissed needs --reason: a dismissal that tells the reviewer "
            "their point does not apply, and gives them nothing to argue with, "
            "is worse than no reply at all"
        )
    if reason and kind is not FixOutcome.DISMISSED:
        return f"--reason is only read for --as dismissed; --as {kind.value} renders none"
    if commit and not kind.may_cite_a_commit:
        return f"--commit is only read for --as fixed; --as {kind.value} cites no commit"
    return ""


def run_settle(
    ctx: pr_context.ResolvedContext,
    targets: list[str],
    as_name: str,
    reason: str,
    commit: str,
) -> int:
    """Record that the operator settled these threads by hand.

    The ending the fix pass cannot see. A thread it routed to a person is
    settled off the tool's surface — the operator writes the fix, commits it,
    pushes — and nothing in that sequence reaches the snapshot, so the closeout
    goes on reporting a thread that has been dealt with as awaiting discussion.

    Local state only, and deliberately so: `--post` gates every other write, and
    a command that recorded *and* published in one step would leave the operator
    no way to read the closeout back before it left the machine. What this leaves
    behind is a snapshot `--finish` treats like any other settled thread.
    """
    kind = FixOutcome(as_name)
    flag_error = settle_flag_error(kind, reason, commit)
    if flag_error:
        log.error(flag_error)
        return 1

    wt_path = ctx.require_worktree()
    state = pr_state.load_state(ctx.target_dir)
    if state is None or not state.fix.fix.items:
        log.error(
            "No fix snapshot to settle against — run `pr comments --fix` first"
        )
        return 1

    picked = settle_targets(state.fix.fix, targets)
    if picked is None:
        return 1

    shas = settled_commits(wt_path, picked, kind, commit)
    if shas is None:
        return 1

    settled = 0
    for outcome, sha in zip(picked, shas):
        was = outcome.outcome
        if not record_settlement(outcome, kind, reason, sha):
            log.info(f"{outcome.id}: already recorded as {kind.value} — nothing to do")
            continue
        settled += 1
        report_settlement(outcome, kind, was, sha)

    if not settled:
        return 0

    # Both flags are what --finish reads before it does anything: the reply queue
    # early-returns unless replies are pending, and the summary renderer
    # early-returns unless one is deferred. Re-arming them is also what puts the
    # closeout back on `pr status`, which is correct — the PR is owed a reply and
    # a corrected summary again.
    state.fix.replies_pending = True
    state.fix.summary_deferred = True
    pr_state.save_state(ctx.target_dir, state)
    log.info(
        f"Recorded {settled} settled thread(s) — next: "
        f"{pr_comments_fix.CLOSEOUT_COMMAND}"
    )
    return 0
