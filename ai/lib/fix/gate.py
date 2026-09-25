"""The verify gate: when it runs, and what its verdicts mean.

``fix.verify`` owns the one call that produces verdicts. This module owns
everything around it — what the gate is shown for a fix, a decline and a
contradicted deferral, and how an answer it gives (or withholds) changes an
item's outcome. Split from ``fix.engine``, which owns the batch/invoke/retry
pipeline and nothing about judgement.
"""

# doc-group: pipeline

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from core import log
from core.trail import Trail
from fix import reconcile as fix_reconcile
from fix import scope as fix_scope
from fix.types import FixItem
from pr.fix import FixOutcome, ItemOutcome


@dataclass(frozen=True)
class Verdict:
    """What the gate established about one fix.

    ``ok`` is three-valued on purpose. True is "something ran against the
    changed path and passed", False is "something ran and it failed", and None
    is "nothing could be run". Collapsing the last two would demote a fix on a
    project with no runnable check, which is the whole class of work the gate is
    least able to judge and has the least right to overrule.

    ``detail`` is what ran and what came of it, in the words a reply prints. It
    matters most when ``ok`` is None: an unverified row is only actionable if it
    says why nobody could check it.
    """

    ok: bool | None
    detail: str = ""


# What the gate is handed and what it gives back: the fixed items, and a verdict
# per item id. An id the gate does not answer is not a verdict — see `_verify`.
VerifyFn = Callable[..., dict[str, Verdict]]

# What the fix pass said holds its change, as the gate is shown it. Composed
# here rather than carried on `FixItem`: that type is the question a pass asks,
# the reason is the answer it got back, and this adapter is the one place that
# legitimately holds both.
#
# Worded for any domain's evidence, not just a test name — a CI fix names the
# check it re-ran. "held by" reads correctly for both; "the test for this fix"
# would not.
_CLAIM_HEADING = "**The fix pass claims this change is held by:**"

_NO_CLAIM = (
    "**The fix pass named nothing that holds this change.** That is a claim "
    "nobody made rather than a claim that failed, so it is not on its own a "
    "reason to call the fix broken — judge it on what you can run."
)


# A decline asks the gate a different question from a fix. A fix says "I changed
# this" and is checked by running it; a decline says "this finding is wrong about
# the code" and is checked by reading the tree the decline describes. Both are
# claims the pass made about work nobody else watched, which is why they share a
# gate — but a gate handed a decline under the fix wording looks for a change
# that was never made and reports every one of them broken.
_DECLINE_HEADING = (
    "**The fix pass rejected this {noun} rather than acting on it, saying:**"
)

_DECLINE_ASK = (
    "Check that reason against the tree, and judge only whether it holds.\n\n"
    "Two ways it commonly does not, both of which read as sound prose:\n\n"
    "1. **It describes the tree after the pass's own edits.** A pass that fixed "
    "the {noun} and then declined it reports the fix as a non-defect, and the "
    "reason is true when you read the file precisely because the pass made it "
    "true. The uncommitted edits are in the worktree — `git diff` is exactly "
    "what this pass changed. If the reason is true only with that diff applied, "
    "the {noun} was fixed, not declined: answer **broken** and say so.\n"
    "2. **It cites a commit that does not contain what it claims.** A reason "
    "naming a SHA is checkable: `git show <sha>` it. A pass cannot cite its own "
    "commit here, because it has not committed yet — so a SHA that does not "
    "carry the change described is a reason with nothing behind it.\n\n"
    "A decline resting on scope, house convention, a documented tradeoff, or the "
    "{noun}'s own text is not any of the above. Judge it as written and answer "
    "**verified** when it holds."
)


def _claim_block(reason: str) -> str:
    """What the fix pass said holds this change, framed as a claim to check.

    The empty case says so in words rather than rendering nothing: a gate shown
    no claim block cannot tell "the pass was never asked" from "the pass was
    asked and declined to answer", and only the second is worth reporting.
    """
    return f"{_CLAIM_HEADING} {reason}" if reason else _NO_CLAIM


def _decline_block(reason: str, noun: str) -> str:
    """A decline as the gate is asked to check it, worded for the domain's own noun."""
    heading = _DECLINE_HEADING.format(noun=noun)
    ask = _DECLINE_ASK.format(noun=noun)
    return f"{heading} {reason}\n\n{ask}"


# A contradicted deferral asks the gate the inverse of every other item here.
# The rest say "I changed this, check it works"; this one says "I changed
# nothing" while its own file moved in the same run. Handing it over under the
# fix wording would ask the gate to verify a fix the pass never claimed, and the
# honest answer to that is always "broken" — which is the wrong answer, since
# the question is whether the work is there at all.
_UNDONE_HEADING = (
    "**The fix pass recorded this {noun} as work it did not do**, but this "
    "{noun}'s own file changed while that answer was being written. One of the "
    "two is wrong."
)

_UNDONE_ASK = (
    "Establish which. The pass may have done the work and mis-recorded it; it "
    "may have edited the file for a different {noun} answered in the same run; "
    "or it may have left something half-applied.\n\n"
    "Answer for *this* {noun} only:\n\n"
    "- **verified** — the work this {noun} asks for is present in the tree and "
    "does what was asked. The recorded answer was wrong and will be corrected "
    "to fixed.\n"
    "- **broken** — something for this {noun} is present but does not do what "
    "was asked. It goes to a person rather than back to the pass.\n"
    "- **not verified** — the change belongs to something else, or you cannot "
    "tell. The recorded answer stands untouched, which is the right outcome "
    "whenever the file moved for a reason other than this {noun}.\n\n"
    "`git diff` is the pass's uncommitted work. Nothing here is committed yet, "
    "so a reason citing a SHA cannot be citing this pass's own work."
)


def _undone_block(reason: str, noun: str) -> str:
    """A contradicted "not done" as the gate is asked to settle it.

    The agent's own reason is included when it gave one — a pass that said *why*
    it could not do the work is offering the gate the most direct thing to check
    the tree against.
    """
    heading = _UNDONE_HEADING.format(noun=noun)
    said = f" It said: {reason}" if reason else ""
    return f"{heading}{said}\n\n{_UNDONE_ASK.format(noun=noun)}"


def _verify_item(
    outcome: ItemOutcome, source: FixItem | None, noun: str,
    scope: fix_scope.BatchScope = fix_scope.UNKNOWN_SCOPE,
) -> FixItem:
    """One claimed fix as the gate is asked about it.

    The body is three things joined: the domain's own rendering of what the
    reviewer said, carried over verbatim; the fix pass's claim about what now
    holds the change; and what the worktree showed while that claim was being
    made. The gate judges the fix against the ask, and the ask is not
    recoverable from the outcome; it also checks the claim, and the claim is not
    recoverable from the source item.

    The observation is the half that was missing, and it is why a reason
    describing work the pass did not do used to survive this gate. The claim is
    prose written by the agent being checked, so a gate holding it up against
    nothing could only ask whether it *sounded* like a fix — and a fluent
    sentence about an unrelated change reads exactly like a fluent sentence
    about a real one. Naming the files that actually moved gives the gate
    something the agent's prose has to agree with.

    It is evidence and not a verdict: the list is per batch, the block says so,
    and a fix landing in a caller rather than at the reviewer's line is normal
    enough that concluding from a path miss alone would be wrong more often than
    right. The gate is the reader equipped to weigh that; this only makes sure
    it is not weighing it blind.

    The claim goes last so the ask is read first, and so it sits immediately
    above the verdict boxes answering it. Falling back to the outcome alone
    keeps a gate that is merely under-informed rather than one that crashes, for
    an id the pass answered but never handed out — the claim still reaches it,
    since that half comes from the outcome.
    """
    claim = (
        _decline_block(outcome.reason, noun)
        if outcome.outcome is FixOutcome.DECLINED
        else _undone_block(outcome.reason, noun)
        if outcome.outcome in fix_reconcile.CLAIMS_NO_WORK
        else _claim_block(outcome.reason)
    )
    seen = fix_reconcile.observed(outcome, scope)
    if seen:
        claim = f"{claim}\n\n{seen}"
    if source is None:
        return FixItem(id=outcome.id, file=outcome.file, line=outcome.line,
                       label=outcome.summary, body=claim)
    body = source.body.rstrip()
    return FixItem(
        id=outcome.id,
        # The outcome's anchor, not the source's: the agent may have moved the
        # code, and the gate should look where the fix landed.
        file=outcome.file or source.file,
        line=outcome.line or source.line,
        label=source.label or outcome.summary,
        body=f"{body}\n\n{claim}" if body else claim,
    )


def _gated_decline(outcome: ItemOutcome) -> bool:
    """Whether this decline is one the gate can check.

    Every decline with a reason is, and the predicate is deliberately no
    cleverer than that. The tempting alternative is to gate only the ones whose
    wording asserts something about the tree — "already", "not present",
    "current HEAD" — but a decline is prose, the phrasings are unbounded, and a
    keyword list quietly exempts the rewording it does not know. The failure it
    would miss is the one worth catching, so the cost of gating the honest
    declines too is accepted.

    A decline with no reason is skipped because there is no claim to check.
    `_record_unevidenced` reports that one instead.
    """
    return outcome.outcome is FixOutcome.DECLINED and bool(outcome.reason)


def _no_scope(_outcome: ItemOutcome) -> fix_scope.BatchScope:
    """The scope lookup for a caller that supplied no observations.

    Keeps `_verify` callable without a `_Settled` — which the tests do, and
    which a domain calling the gate directly would — by answering the way an
    unobserved pass genuinely should: nothing is known, so nothing is
    contradicted and no observation block is rendered.
    """
    return fix_scope.UNKNOWN_SCOPE


def _verify(
    outcomes: list[ItemOutcome], verify: VerifyFn | None, adapter,
    by_id: dict[str, FixItem], trail: Trail | None,
    scope_for: Callable[[ItemOutcome], fix_scope.BatchScope] = _no_scope,
) -> None:
    """Hold each claim against what actually runs, before anything lands.

    A ticked `fixed` box is the agent saying it applied an edit. That is not the
    same claim as the edit working, and the two are indistinguishable in a fix
    pass's output: both produce a ticked box, a commit, and a summary row. This
    is where they stop being indistinguishable.

    A decline with a reason comes here too, and for the same argument. It is the
    pass closing a reviewer's finding on its own say-so, with no diff anyone can
    read to check it — the weakest-evidence outcome the vocabulary has, and
    until this it was the only one nothing checked. The failure that motivated
    it: a pass fixed a finding, then ticked `declined` describing the tree its
    own edit had just produced, and cited a commit that did not contain the
    change. The edit was committed anyway, because staging reads the worktree
    diff and not the boxes, so the fix shipped recorded as "not a defect".

    Only falsification demotes. A verdict of None, and an id the gate never
    answered at all, both leave the outcome FIXED and unverified — silence is
    not evidence, and a gate that ran out of turns has not established that a
    fix is wrong. Demoting on absence would make the gate's own flakiness look
    like the fix's.

    Mutates in place, before `landing` is asked for a spec, so the outcome the
    domain records and the outcome the commit carries cannot disagree.

    `by_id` is the items as the domain rendered them, which is where the
    reviewer's own words are. An outcome carries a location and a verdict and
    nothing else — `parse` reads the anchor back out of the section heading and
    never the label — so a gate handed only outcomes would be asked whether a
    fix at `a.py:2` works without being told what it was meant to do. The
    prompt's first instruction is to run the reviewer's repro; this is what
    puts that repro in front of it.
    """
    contradicted = {
        o.id for o in outcomes
        if fix_reconcile.contradiction(o, scope_for(o)) is not None
    }
    if verify is None:
        # A domain that runs no gate still gets the observation reported. The
        # contradiction is a fact about the tree and is established without
        # asking anything — it is only the *resolution* that needs an agent —
        # so staying silent here would hide a known discrepancy from the two
        # passes (CI, pre-push) that never opted into a gate. Nothing is
        # demoted or promoted on it: with no verdict available the recorded
        # answer stands, which is the same rule the gated path follows when the
        # gate reaches no verdict.
        _report_ungated(contradicted, trail)
        return
    claimed = [
        o for o in outcomes
        if o.outcome.counts_as_fixed or _gated_decline(o) or o.id in contradicted
    ]
    if not claimed:
        return
    if contradicted:
        log.warn(
            f"{len(contradicted)} item(s) recorded as work not done, in a batch "
            "that changed the item's own file — sent to the verify gate"
        )
        if trail:
            trail.warn(
                "fix_contradicted",
                f"{len(contradicted)} item(s) contradicted by the observed tree",
                data={"items": sorted(contradicted)},
            )

    items = [
        _verify_item(o, by_id.get(o.id), adapter.item_noun, scope_for(o))
        for o in claimed
    ]
    # The gate's own phase where the domain declared one. Falling back to the
    # fix pass's phase keeps a domain that has not declared one working, but it
    # prompts the gate with the fix pass's template — so a domain running a gate
    # is expected to set `verify_phase`.
    verdicts = verify(
        adapter.verify_phase or adapter.phase, "", items=items, adapter=adapter,
    ) or {}

    falsified = 0
    promoted = 0
    for outcome in claimed:
        verdict = verdicts.get(outcome.id)
        if outcome.id in contradicted:
            promoted += _resolve_contradiction(outcome, verdict)
            continue
        if verdict is None:
            # The gate ran and this id was not in its answer. That is a fix
            # nothing established, which is what False means — distinct from the
            # None of a pass that never gated at all.
            outcome.verified = False
            continue
        outcome.verify_detail = verdict.detail
        if verdict.ok is True:
            outcome.verified = True
            continue
        if verdict.ok is None:
            outcome.verified = False
            continue
        # Falsified. NEEDS_HUMAN rather than DEFERRED: the pass already had its
        # retry, and an edit that is present but wrong is not work the next
        # identical attempt gets right — it is a call for a person, and the
        # reason carries what the gate saw so they do not start from nothing.
        was_declined = outcome.outcome is FixOutcome.DECLINED
        outcome.outcome = FixOutcome.NEEDS_HUMAN
        outcome.reason = verdict.detail or (
            "the reason given for declining this did not hold up"
            if was_declined else "the fix did not hold up when run"
        )
        outcome.verified = False
        falsified += 1

    if falsified:
        log.warn(
            f"Verify gate: {falsified} of {len(claimed)} claimed "
            f"item{'s' if len(claimed) != 1 else ''} did not hold up — "
            "demoted, not recorded as the pass claimed them"
        )
    if promoted:
        log.warn(
            f"Verify gate: {promoted} item(s) recorded as not done were "
            "confirmed fixed against the tree — recorded as fixed, not as the "
            "pass claimed them"
        )
    if trail:
        trail.info(
            "fix_verify", "verify gate complete",
            data={
                "claimed": len(claimed),
                "falsified": falsified,
                "promoted": promoted,
                "verified": sum(1 for o in claimed if o.verified),
            },
        )


def _report_ungated(contradicted: set[str], trail: Trail | None) -> None:
    """Say that the tree disagrees with the record, for a pass with no gate.

    A warning rather than a change of outcome. The observation is too coarse to
    settle an item on its own — that argument is `fix.reconcile`'s and does not
    weaken because no gate is configured — so what an operator gets here is the
    discrepancy and the ids behind it, which is strictly more than the silence
    that preceded this.
    """
    if not contradicted:
        return
    log.warn(
        f"{len(contradicted)} item(s) recorded as work not done, in a batch "
        "that changed the item's own file. No verify gate is configured for "
        "this pass, so the recorded answer stands — check these by hand: "
        + ", ".join(sorted(contradicted))
    )
    if trail:
        trail.warn(
            "fix_contradicted_ungated",
            f"{len(contradicted)} item(s) contradicted by the observed tree, "
            "with no gate to settle them",
            data={"items": sorted(contradicted)},
        )



# What a contradicted item says when the gate reached no verdict on it. Not a
# claim either way: the observation is a fact about the tree, and the absence of
# a verdict is a fact about the run. Both beat the summary's "no auto-fix"
# fallback, which is the one reading of an empty reason that is certainly wrong
# here.
_UNSETTLED_CONTRADICTION = (
    "recorded as not done, but this item's file was changed in the same run "
    "and the gate reached no verdict on it — read the diff"
)


def _resolve_contradiction(
    outcome: ItemOutcome, verdict: Verdict | None,
) -> int:
    """Settle an item whose "nothing was done" the tree disagrees with.

    Returns 1 when the item was promoted to FIXED, so the caller can report how
    many answers the tree overturned.

    The gate is asked the inverse of its usual question here, and the three
    answers mean correspondingly inverted things. **verified** is the gate
    confirming the work is present and correct, which makes the recorded
    deferral simply wrong: the item becomes FIXED, and it is the one path in the
    engine that promotes rather than demotes. **broken** is the gate finding the
    edit present but not doing what was asked — not a deferral either, and not
    something a further identical retry fixes, so it goes to NEEDS_HUMAN on the
    same argument the falsified branch makes. **not verified**, and a gate that
    never answered, leave the deferral exactly as the agent recorded it.

    That last case is why this cannot demote on the observation alone. A file
    moving in a batch is evidence that *something* happened, never that this
    item is what happened — several items share a batch and a fix routinely
    touches a neighbour's file — so with no verdict behind it the honest record
    is the one the pass wrote, and the contradiction survives in the trail
    rather than in an outcome nobody established.

    `verified` is left alone throughout. It means "something ran against this
    fix and passed", and the surfaces read `False` as a hedge to print beside a
    *claimed fix* — writing it onto a row that claims no fix at all would
    caveat a deferral for failing to prove work it never said it did.

    What the unsettled cases do get is a `reason`, because the alternative is
    worse than saying nothing: an outcome with no reason renders as the summary's
    bare fallback, "no auto-fix", which asserts to whoever reads the commit that
    nothing happened here — over a commit that carries the edit. The outcome
    stands as recorded; only the sentence published about it stops overclaiming.
    """
    if verdict is None:
        outcome.reason = outcome.reason or _UNSETTLED_CONTRADICTION
        return 0
    outcome.verify_detail = verdict.detail
    if verdict.ok is None:
        outcome.reason = outcome.reason or verdict.detail or _UNSETTLED_CONTRADICTION
        return 0
    if verdict.ok is True:
        outcome.outcome = FixOutcome.FIXED
        outcome.verified = True
        outcome.reason = verdict.detail or (
            "recorded as not done, but the change is present in the tree and "
            "the gate confirmed it does what was asked"
        )
        return 1
    outcome.outcome = FixOutcome.NEEDS_HUMAN
    outcome.reason = verdict.detail or (
        "recorded as not done, but this item's file was changed in the same "
        "run and the change does not do what was asked"
    )
    return 0
