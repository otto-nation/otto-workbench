"""What triage decided, and the holds that decision places.

`pr.triage` asks the model and refuses what it cannot back. This is the other
half of the same round: sorting those verdicts into the four dispositions the
fix pass routes on, placing the publishing holds they call for, and carrying
the result as one value.

The two are separate modules because they are separate phases. The model can be
asked once and its answer disposed of twice — a triage-only run stops after
`pr.triage`, and only `--fix` reaches here — and the prompt half has no business
knowing about publishing holds.

**The holds are why `TriagedRound` has a constructor rather than being built by
its caller.** `publishing.hold()` flips `publishing.enabled()`, and the fix
pass reads that flag afterwards to decide whether the replies it rendered are
still owed. The two used to be kept in order by sitting near each other in one
function; here the ordering is the type's, since there is no `TriagedRound` that
predates its own holds.
"""

# doc-group: publishing

from __future__ import annotations

from dataclasses import dataclass, field, replace as dataclass_replace

from core import publishing
from core.trail import Trail
from git import topology as git_topology
from pr import summary_model
from pr import supersession
from pr.comments_state import ThreadState
from pr.fix import FixOutcome
from pr.thread_models import (
    Classification, ClassificationResult, CommentItem, Complexity, Disposition,
    PRReport, ReplyOutcome, TrackingResult, TriageResult, Verification,
)


_HUMAN_CLASSIFICATIONS = {
    Classification.CONFLICTING: summary_model.HumanReason.CONFLICTING,
    Classification.QUESTION: summary_model.HumanReason.QUESTION,
}

_VERIFICATION_ROUTES = {
    Verification.VALID: (Disposition.FIXABLE, None),
    Verification.NEEDS_DISCUSSION: (
        Disposition.NEEDS_HUMAN, summary_model.HumanReason.NEEDS_DISCUSSION,
    ),
    Verification.ALREADY_ADDRESSED: (Disposition.ALREADY_ADDRESSED, None),
    Verification.INVALID: (Disposition.DISMISSED, None),
}


def _route(
    tt: CommentItem,
) -> tuple[Disposition, summary_model.HumanReason | None] | None:
    """Where one entry goes, or None to drop it.

    Order is the rule. Contested state overrides the model; a question never
    reaches verification; valid + high is a person before VALID is fixable.
    """
    if tt.state == ThreadState.CONTESTED:
        return (Disposition.NEEDS_HUMAN, summary_model.HumanReason.CONTESTED)
    reason = _HUMAN_CLASSIFICATIONS.get(tt.classification)
    if reason is not None:
        return (Disposition.NEEDS_HUMAN, reason)
    if tt.classification is not Classification.ACTIONABLE_SUGGESTION:
        return None
    if (
        tt.verification is Verification.VALID
        and tt.complexity is Complexity.HIGH
    ):
        return (Disposition.NEEDS_HUMAN, summary_model.HumanReason.COMPLEX)
    return _VERIFICATION_ROUTES.get(tt.verification)


def _report_drop(tt: CommentItem, trail: Trail | None) -> None:
    """Say that an entry reached no bucket, where nothing used to say it.

    Two routes end here — a classification that is not an actionable
    suggestion, and a verification no route claims — and both were silent
    falls through an if/elif chain. Routing them through one function is what
    makes the drop a statement rather than the absence of one, and gives a
    round that quietly disposed of a thread somewhere to be read.
    """
    if not trail:
        return
    trail.info(
        "triage_drop",
        f"{tt.id}: no disposition for "
        f"classification={tt.classification.name} "
        f"verification={tt.verification.name}",
    )


def classify_entries(
    triage_entries: list[CommentItem], *,
    trail: Trail | None = None,
) -> ClassificationResult:
    """Sort one side's triage verdicts into the dispositions the pass routes on.

    Works for threads and for the entries decomposed out of top-level comments
    alike: the vocabulary the model answers in is the same for both, and which
    side an entry came from is the caller's to remember.

    Routing order is the rule. A contested thread is a person's before it is
    anything else — the state overrides whatever the model called it — and a
    question never reaches verification at all. Only an `actionable_suggestion`
    gets that far, and a valid one the model called complex goes to a person
    rather than to the agent.

    Every disposition takes a copy of the model's entry at one append. That
    single statement is what makes the four agree about object identity, so a
    later stamp on a bucket cannot write through to the original.
    """
    result = ClassificationResult()

    for tt in triage_entries:
        routed = _route(tt)
        if routed is None:
            _report_drop(tt, trail)
            continue
        disposition, reason = routed
        # The one copy, and the only place a classified entry is made.
        # Whether the entry a bucket holds is the model's object or a copy of
        # it used to be nine independent answers that happened to be two; it
        # is one answer here because there is one statement of it.
        result.bucket(disposition).append(
            dataclass_replace(tt, reason=reason.value) if reason
            else dataclass_replace(tt)
        )

    return result


def hold_if_superseded(
    verdict: supersession.Verdict, trail: Trail | None = None,
) -> None:
    """Report what the preflight found, and hold if any of it is evidence.

    A hold rather than the refusal `pr review` and `pr rebase` answer with: by
    the time this runs the triage pass has already been paid for, so there is
    nothing left to save by stopping, and the fixes themselves are worth having
    locally either way. What must not happen is asserting outward that
    superseded code was fixed.

    The hold is the same one an open thread places, and reaches the same acts:
    the push, the `Fixed in <sha>` replies, the thread resolutions, and the
    summary. The local commit is not one of them — see `fix.engine`, whose gate
    is fixed at commit-but-do-not-push for exactly this reason.
    """
    supersession.report(verdict)
    if not verdict.superseded:
        return
    holding = verdict.holding
    publishing.hold(
        f"{len(holding)} supersession signal(s) suggest this branch is superseded"
    )
    if trail:
        trail.decision(
            "publishing_hold",
            f"held publishing — {len(holding)} supersession signal(s)",
            reason="fixing code the default branch has already removed asserts it should exist",
            data={"signals": [s.kind for s in holding]},
        )


def hold_while_contested(
    needs_human: list[CommentItem], trail: Trail | None = None,
) -> None:
    """Stop the pass asserting progress while a thread is still being argued.

    Threads are bucketed independently, so a finding that says "your root cause
    does not exist" only removes itself from `fixable` — the pass goes on to fix
    everything else, commit it, push it, and reply that it is done. Every one of
    those findings can be individually real and the branch still not worth
    landing — that is how a pass pushes a run of fixes to superseded code.

    Telling a premise-invalidating question from a bikeshed is the hard problem,
    and getting it wrong in the permissive direction is what this exists to
    prevent. So the halt is deliberately blunt — any open thread — and cheap:
    the fixes are still applied and still committed locally, and only the
    outward acts wait. A wrong call costs a local commit, not a pushed one. The
    human clears it by answering and re-running `--finish --post`.
    """
    if not needs_human:
        return
    publishing.hold(f"{len(needs_human)} thread(s) awaiting discussion")
    if trail:
        trail.decision(
            "publishing_hold",
            f"held publishing — {len(needs_human)} thread(s) awaiting discussion",
            reason="a contested thread can invalidate the premise of every other fix",
            data={"reasons": sorted({t.reason for t in needs_human if t.reason})},
        )


@dataclass(frozen=True)
class TriagedRound:
    """Every comment's disposition for one round, and what the round already said.

    The fix pass's inputs as one value rather than as eleven arguments
    reassembled inside a constructor. Two of them were worse than loose: the
    reply count and the resolutions accumulate across two phases, so they were
    a tuple with the parentheses left off, and a phase that forgot to thread one
    through lost its half silently.

    Threads and comment items stay apart in the two `ClassificationResult`s
    because only a thread has somewhere to reply, and merge in the properties
    below for the surfaces that treat them alike. Nothing here re-derives what
    those two already hold.

    Built by `triage_the_round`, never directly. The holds it places flip
    `publishing.enabled()`, which the pass reads afterwards to decide what it
    still owes a reviewer — an ordering that used to be kept by two calls
    sitting near each other and is now kept by there being no round that
    predates its own holds.
    """

    threads: ClassificationResult = field(default_factory=ClassificationResult)
    items: ClassificationResult = field(default_factory=ClassificationResult)
    # A bare instance rather than a `default_factory`, which the two above need
    # and this does not: `ReplyOutcome` is frozen, so the one shared default is
    # unwritable and cannot carry a round's replies into the next round. Give
    # it a mutable field and this line becomes the classic shared-default bug
    # — take the `default_factory` then.
    replies: ReplyOutcome = ReplyOutcome()
    # A thread on the PR that this round gave no disposition to. The summary it
    # publishes is partial while one exists, so the round stays owed however
    # much of it went out.
    has_unaccounted: bool = False

    @property
    def fixable(self) -> list[CommentItem]:
        """The threads the agent will be asked about."""
        return self.threads.fixable

    @property
    def fixable_items(self) -> list[CommentItem]:
        """The comment items the agent will be asked about."""
        return self.items.fixable

    @property
    def needs_human(self) -> list[CommentItem]:
        return [*self.threads.needs_human, *self.items.needs_human]

    @property
    def dismissed(self) -> list[CommentItem]:
        return [*self.threads.dismissed, *self.items.dismissed]

    @property
    def already_addressed(self) -> list[CommentItem]:
        return [*self.threads.already_addressed, *self.items.already_addressed]

    @property
    def has_fixables(self) -> bool:
        """Whether the agent has anything to be handed.

        A round with nothing here still has a summary to publish for what
        triage settled, so this decides whether the fix pass runs, not whether
        the round reports.
        """
        return bool(self.fixable or self.fixable_items)

    def by_outcome(
        self, tracking: TrackingResult,
    ) -> dict[FixOutcome, list[CommentItem]]:
        """This round's entries under every outcome, in one mapping.

        The join between what triage decided, which this round holds, and what
        the agent recorded, which `TrackingResult` holds. Neither can own it
        alone: the tracking result has never heard of `dismissed`, and
        `RoundContent` is built *from* this rather than holding it.

        **One mapping, two readings, and the disagreement between them is the
        point.** `FixRecord` keeps `DECLINED` apart from `NEEDS_HUMAN` because
        the reason each carries is worth telling apart in the state file, and
        `RoundContent.needs_a_person` folds them because a reviewer reads both
        the same way. That is only safe while both are reading one dict. Built
        twice — which it was, in two shapes thirty lines apart — the two
        spellings are free to drift, and the drift shows up as a state file
        that contradicts the published table.

        A round the agent never ran for passes an empty `TrackingResult` and
        gets the same mapping with three empty buckets, which `RoundContent.of`
        documents as an ordinary answer and `fix_record_for` skips.
        """
        return {
            FixOutcome.FIXED: tracking.both(FixOutcome.FIXED),
            FixOutcome.DEFERRED: tracking.both(FixOutcome.DEFERRED),
            FixOutcome.NEEDS_HUMAN: (
                self.needs_human + tracking.both(FixOutcome.NEEDS_HUMAN)
            ),
            FixOutcome.DECLINED: tracking.both(FixOutcome.DECLINED),
            FixOutcome.DISMISSED: self.dismissed,
            FixOutcome.ALREADY_ADDRESSED: self.already_addressed,
        }

    @property
    def has_items(self) -> bool:
        """Whether any decomposed comment item reached a bucket this round.

        Persisted as `FixSummary.has_comment_items` and read back on
        `--finish`, where it decides whether the render appends the raw comment
        sections: an item that is already a table row must not have its body
        repeated below the table.

        Every bucket counts, not just the fixable one — an item the round
        dismissed is a row like any other.
        """
        return self.items.any_entry


def triage_the_round(
    triage_result: TriageResult,
    report: PRReport,
    wt_path,
    ctx,
    trail: Trail | None = None,
) -> TriagedRound:
    """Dispose of what triage said, place the holds, and report the round.

    The holds come after the classification because they are decided by it, and
    before anything reads `publishing.enabled()` because they change the answer.
    Nothing between the two may ask that question.

    `has_unaccounted` is measured here rather than by the caller: it compares
    the PR's own open threads against the ids both sides gave a disposition to,
    and both halves of that comparison are this function's.
    """
    threads = classify_entries(triage_result.threads, trail=trail)
    items = classify_entries(triage_result.comment_items, trail=trail)

    hold_if_superseded(
        supersession.detect_cached(
            wt_path, ctx.repo, ctx.target_dir,
            base=f"origin/{git_topology.default_branch_cached(wt_path)}",
            trail=trail,
        ),
        trail,
    )
    hold_while_contested(
        [*threads.needs_human, *items.needs_human], trail,
    )

    open_ids = {
        t.id for t in report.threads if t.state != ThreadState.RESOLVED
    }
    return TriagedRound(
        threads=threads,
        items=items,
        has_unaccounted=bool(open_ids - (threads.ids() | items.ids())),
    )


