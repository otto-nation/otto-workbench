"""What the agent said against what the worktree shows, per item.

A fix pass has two accounts of itself and until this module they never met.
`fix.tracking` parses what the agent ticked; `fix.scope` observes what actually
changed. The engine held both — it computed the diff to scope its `git add` and
built its report from the boxes — and compared them nowhere, so an agent that
edited a file and ticked `deferred` had its edit committed and reported as work
still owed. Both accounts were right about their own half and nobody asked them
the same question.

**This module reports observations, not verdicts.** That division is the whole
design, and it is load-bearing in both directions:

*Where the observation is decisive, it routes.* An item saying "I did nothing"
whose own file moved in its batch is a contradiction on the item's own terms —
no judgement about code quality is needed to see it, and the outcome it carries
is one nothing else checks. That item is sent to the verify gate, which is
where claims already go to be tested.

*Where the observation is coarse, it informs.* An item saying "I fixed this"
whose anchor file did not move is not thereby wrong. `fix.scope` says agents
routinely edit a test, a fixture, or the caller that broke instead of the line
the reviewer annotated, and one edit can satisfy several items that name the
same file. A path-level miss is too weak to demote on and always will be, so
this module never demotes: it hands the gate the diff alongside the claim and
lets the reader that can weigh them do the weighing.

The tempting alternative was a mechanical "claimed a fix, changed nothing"
verdict. It was measured and rejected rather than skipped: chunk sizes are 10
for the comment and CI passes and 30 for the findings pass, and the check can
only fire when a batch's diff is *entirely* empty, because a non-empty diff is
consistent with any subset of that batch's items having caused it. One genuine
fix among ten therefore clears the other nine, which is precisely the case the
check would exist to catch. A rule that fires only when the pass did nothing at
all adds a second place for the vocabulary to drift and catches a case the
existing unproductive-pass guard already reports.
"""

# doc-group: pipeline

from __future__ import annotations

from enum import StrEnum

from fix.scope import BatchScope
from pr.fix import FixOutcome, ItemOutcome


class Contradiction(StrEnum):
    """How an item's recorded outcome disagrees with the observed tree.

    An enum rather than a bare flag because the set is open: the sibling case —
    a claimed fix whose batch changed nothing — is deliberately absent today
    (the module docstring argues why), and naming the one that does fire leaves
    room to add it without rewording every consumer.
    """

    # The item says no work was done, and the batch that answered it changed
    # the file the item is anchored to. One of the two is wrong and the pass
    # cannot tell which from the path alone.
    DEFERRED_WITH_EDIT = "deferred_with_edit"


# The outcomes that assert no work was done. Each closes an item having changed
# nothing, so an edit to the item's own file contradicts any of them. SKIPPED is
# included even though it means "never attempted": a batch that edited the file
# of an item it claims it never looked at is making the same untrue statement,
# and the stronger claim deserves the check at least as much.
#
# NEEDS_HUMAN is here for the same reason and was missing for a bad one. It
# reads as a hand-off rather than a claim, but the sentence it puts in the
# commit message is the one this module exists to check — the surfaces print it
# under the same "Skipped" heading as a deferral, so an agent that edited the
# code and then asked for a person publishes "no work was done" over a commit
# containing the edit. The set that decides a contradiction has to match the set
# that claims nothing happened, and the review pass's `_STILL_OPEN` has always
# been DEFERRED and NEEDS_HUMAN together.
#
# The gate demotes a falsified fix *to* NEEDS_HUMAN, which is not this case and
# is not re-examined: `_verify` resolves contradictions before any demotion, so
# an outcome only reaches that branch after this set has been consulted.
#
# Public because the verify gate words a contradicted item by it — the question
# put to the gate is the inverse of the one a claimed fix gets, and the set that
# decides a contradiction is the same set that decides the wording.
CLAIMS_NO_WORK = frozenset({
    FixOutcome.DEFERRED, FixOutcome.SKIPPED, FixOutcome.NEEDS_HUMAN,
})


def contradiction(
    outcome: ItemOutcome, scope: BatchScope,
) -> Contradiction | None:
    """How this outcome disagrees with its batch's diff, if it does.

    Anchored on `outcome.file`, which `fix.tracking.parse` reads back out of the
    section heading rather than taking from the agent's prose — so the path
    compared here is the one the domain wrote when it rendered the item, not
    one the agent could restate to suit its answer.

    An unknown scope contradicts nothing. A worktree that could not be read is
    the one state in which every item looks untouched, and reporting that as a
    pass full of false deferrals would turn a failed git call into an accusation
    against the agent.
    """
    # ceiling: two items anchored at the same file in one batch are
    # indistinguishable here — an edit for either contradicts a deferral of the
    # other, and the gate is what sorts them out at the cost of a turn. The
    # answer is a verdict rather than a wrong record, so this is a cost and not
    # a defect. Upgrade to hunk-level attribution, comparing the item's line
    # range against the diff's, if a domain starts routinely batching several
    # items anchored in one file.
    if not scope.known:
        return None
    if outcome.outcome in CLAIMS_NO_WORK and scope.touched(outcome.file):
        return Contradiction.DEFERRED_WITH_EDIT
    return None


def observed(outcome: ItemOutcome, scope: BatchScope) -> str:
    """What the tree shows about this item, as the verify gate is told it.

    Prose rather than data because the consumer is a prompt: the gate reads this
    beside the agent's own claim and judges the two together. It is written to
    be read by something deciding whether to believe a sentence, so it states
    what was observed and stops short of concluding anything from it.

    Silent for an unknown scope. A gate told "nothing changed" when the truth is
    "nobody could look" would hold that against a fix, and an absent block is
    the honest rendering of an absent observation.
    """
    if not scope.known:
        return ""
    if not scope.files:
        return (
            "**The worktree shows no change at all from the batch this was "
            "answered in.** Nothing the pass did is visible here, so a claim to "
            "have edited something has nothing behind it in the tree. Judge the "
            "claim on what you can run \u2014 an edit made and then reverted looks "
            "the same from here."
        )
    listed = "\n".join(f"- `{path}`" for path in sorted(scope.files))
    anchored = (
        f"`{outcome.file}` is among them."
        if scope.touched(outcome.file)
        else (
            f"`{outcome.file}` is **not** among them. That is not on its own "
            "wrong \u2014 a fix routinely lands in a test, a fixture, or the caller "
            "rather than at the line the reviewer annotated \u2014 but a claim to "
            "have edited this file is checkable against the list and fails it."
        )
        if outcome.file
        else "This item carries no file to check the list against."
    )
    return (
        "**What the worktree shows for the batch this was answered in.** These "
        f"files changed while the agent worked:\n\n{listed}\n\n{anchored}\n\n"
        "The list is per batch, not per item: several items were answered in "
        "the same run, so a file here may belong to any of them."
    )
