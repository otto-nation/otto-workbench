"""What a fix pass did, in terms no one domain owns.

Three fix passes run in this workbench — reviewer comments, CI failures, and
review findings — and each one used to record its result differently: one wrote
typed thread outcomes into state, one wrote checkbox counts, one rewrote
checkboxes inside the review markdown. The types here are the shape all three
settle on. A pass records one :class:`ItemOutcome` per item it was handed and
one :class:`FixRecord` per run, and every domain carries a record because
:class:`~pr_domains.Domain` declares the field — so a domain that gains a fix
pass gains somewhere to record it by declaring nothing.

This module is below the domains rather than beside them: ``pr_domains`` imports
it, and it imports nothing from ``ai/lib`` in return. That is what lets the
record hang off the base class without the domains and the vocabulary they are
written in forming a cycle.

Not every member of :class:`FixOutcome` is a verdict a pass reached. An item can
turn out to have been settled somewhere else entirely — a thread the reviewer
resolved on the forge, a row the operator closed by hand — and the vocabulary
says so rather than calling it a fix, because a resolved thread covers a
reviewer who was answered, who agreed to defer, or who withdrew the point as
readily as one whose code changed. Two predicates on the enum,
:attr:`FixOutcome.counts_as_fixed` and :attr:`FixOutcome.may_cite_a_commit`, are
where that reading lives, so a tally and a commit attribution ask the vocabulary
instead of each re-listing the members it should believe.
:class:`SettledBy` records the other half — who reached the outcome, which is
what tells the pass's own work from a reconciliation or an operator's say-so.

The CI and comment passes both write one, through :mod:`fix_engine` — the
shared pipeline all three now run on, and the thing that produces the
:class:`ItemOutcome` list a record is assembled from. Running on the engine is
not the same as recording through these types: the review-findings pass
re-renders the review document from its outcomes rather than writing a record
at all.
"""

# doc-group: pr-state

from __future__ import annotations

from dataclasses import dataclass, field, replace as dataclass_replace
from enum import StrEnum


class CommitStatus(StrEnum):
    """How the fix pass left the commit — the state everything downstream reads.

    A `StrEnum`, so the persisted values and the JSON payload are the same
    strings they have always been: a state file written before this existed
    still loads, and its plain strings still compare equal to these members.
    The enum is for the code: two of these values are easily confused for one
    another, which is the argument for naming them in one place.
    """

    # Committed and on the remote.
    PUSHED = "pushed"
    # Nothing to commit: the fix pass changed no files.
    NO_CHANGES = "no_changes"
    # A commit was attempted and refused — a hook, or a dirty tree left behind.
    COMMIT_FAILED = "commit_failed"
    # Committed locally; the push was attempted and failed.
    PUSH_FAILED = "push_failed"
    # Committed locally; the push was withheld.
    PUSH_HELD = "push_held"
    # Committed locally; git reported the push and the remote does not hold it.
    # Kept apart from `push_failed` because the operator sees a clean push and
    # would otherwise be told a push failed that, as far as their terminal went,
    # did not.
    PUSH_LOST = "push_lost"
    # Committed locally; the push was reported and the remote could not be asked
    # whether it arrived. Almost certainly on the remote, which is why it is not
    # `push_lost` — that names a remote that answered, and answered no. Treated
    # as unpushed everywhere it matters, because "probably" is not a SHA a
    # reviewer can be handed.
    PUSH_UNVERIFIED = "push_unverified"
    # Render-time only, never persisted: HEAD has moved past the snapshot, but
    # the commit that moved it is not one a reviewer can open, so the summary
    # says the work was handled without naming a SHA for it.
    RECONCILED = "reconciled"


class FixOutcome(StrEnum):
    """What became of one item a fix pass was handed.

    The union of what the three passes distinguish today, which is wider than
    any one of them: the comment pass separates a reviewer who must answer from
    a reviewer who was already answered, the CI pass has kinds it declines to
    attempt at all, and the findings pass lets the agent argue a finding is
    wrong rather than merely hard.

    The five values the comment pass persisted under its own vocabulary keep
    their exact strings, so an outcome that pass wrote before the two were
    folded together reads back as the member of the same name here.
    """

    # The pass changed code for this item.
    FIXED = "fixed"
    # The pass tried and could not — the item is still owed.
    DEFERRED = "deferred"
    # Not a code change: it needs an answer from a person.
    NEEDS_HUMAN = "needs_human"
    # Judged not to apply. Nothing is owed.
    DISMISSED = "dismissed"
    # The code already does what the item asks. Nothing is owed.
    ALREADY_ADDRESSED = "already_addressed"
    # Never attempted: the pass excludes items of this kind on sight.
    SKIPPED = "skipped"
    # Attempted and argued against — the agent's position is that the item is
    # wrong, not that the fix is hard. Distinct from DISMISSED, which is the
    # triage step's call before any agent looked.
    DECLINED = "declined"
    # Settled on the forge by evidence no stronger than the thread being
    # resolved. Resolution covers answered, deferred and declined-by-the-reviewer
    # as readily as fixed, so this is not a claim that anything was fixed: it is
    # not this pass's work, and no commit is known to carry it.
    SETTLED_ELSEWHERE = "settled_elsewhere"

    @property
    def counts_as_fixed(self) -> bool:
        """Whether a tally of fixed work may include this item.

        Every pass counts its fixes and every surface reports that count, so the
        question is asked from six places and has to have one answer. Only FIXED
        is a fix: the settled members mean nobody owes anything, which is not the
        same as the pass having changed code.
        """
        return self is FixOutcome.FIXED

    @property
    def may_cite_a_commit(self) -> bool:
        """Whether a commit SHA may be attached to this item and shown for it.

        Attribution is a claim that a named commit carries this item's fix. An
        outcome that never changed code has no commit to name, and stamping the
        running pass's SHA onto one credits it with work it did not do.
        """
        return self is FixOutcome.FIXED


class SettledBy(StrEnum):
    """Who decided an item's outcome — the pass, or something outside it.

    The provenance every surface asks about before it credits the running pass's
    commit with an item's fix. It is recorded as a field rather than inferred
    from the reason text because the reason is prose written for a person: a
    renderer matching on it is one rewording away from crediting a commit for
    work that commit does not contain.
    """

    # The fix pass now running decided this item.
    PASS = "pass"
    # Reconciled against the forge after the fact: the pass recorded one thing
    # and the thread showed another.
    RECONCILIATION = "reconciliation"
    # The operator settled it by hand and told the CLI so.
    OPERATOR = "operator"


@dataclass
class ItemOutcome:
    """What one fix pass did about one item.

    Carries only what every pass can answer. A reviewer login, a CI job name, a
    finding's severity belong to the item as the domain fetched it, not to the
    record of what happened to it, so they stay on the domain — see
    `FixSummary.reviewers`, which keys the comment pass's logins by outcome id
    rather than widening this type with a field two of the three passes would
    leave empty.

    The default is DEFERRED rather than FIXED because a record assembled from a
    tracking file the agent left untouched must read as work still owed. An
    outcome nobody set is not evidence that anything was fixed, and defaulting
    the other way turns a fix pass that crashed into one that claims the item.
    """

    id: str = ""
    outcome: FixOutcome = FixOutcome.DEFERRED
    # Who reached that outcome. Defaults to the pass, which is what every
    # outcome written before this field existed was — a state file that predates
    # it reads back as the pass's own work, which is what it recorded.
    settled_by: SettledBy = SettledBy.PASS
    summary: str = ""
    # Why, in the words the surface reporting this prints. Empty for FIXED,
    # where the change speaks for itself.
    reason: str = ""
    file: str = ""
    line: int = 0
    # The commit that landed this item's fix. Per-outcome rather than per-pass:
    # a record accumulates across rounds, so one envelope SHA would relabel
    # every earlier round's work with the latest round's commit.
    commit_sha: str = ""
    # The tree `line` was read in, carried so a later round can tell whether the
    # anchor still points at the code the item meant. Empty reads as "cannot
    # anchor" rather than as "anchor is current".
    read_sha: str = ""


@dataclass
class FixRecord:
    """One domain's fix pass, as state.

    Empty means no pass has run for this domain. That is why ``commit_status``
    is None rather than a member: NO_CHANGES is a pass that ran and found
    nothing to commit, which is a different answer from one that never ran.
    """

    items: list[ItemOutcome] = field(default_factory=list)
    commit_sha: str = ""
    commit_status: CommitStatus | None = None
    # The HEAD this record describes. Outcomes recorded against a commit that is
    # no longer checked out describe work that may since have been done, undone,
    # or superseded by hand, so a consumer reconciles before publishing.
    head_sha: str = ""
    updated_at: str = ""

    def merge_into(self, prior: "FixRecord") -> "FixRecord":
        """Fold this pass into what earlier rounds recorded.

        Outcomes accumulate keyed by id — a later pass supersedes an earlier
        outcome for the same item but never drops items it did not touch. A
        review cycle spans several rounds and the summary has to account for all
        of them, not just the most recent pass.

        An empty record is a round that said nothing about this domain rather
        than one that cleared it, so it folds to `prior` untouched. Without that,
        any command writing its own domain would blank the fix record beside it.
        """
        if self == FixRecord():
            return prior
        merged = {item.id: item for item in prior.items if item.id}
        # Entries without an id cannot be de-duplicated; keep them side by side
        # rather than collapsing every one onto the "" key.
        # ceiling: an id-less outcome is kept for every round it appears in, so a
        # long review cycle accumulates one entry per round with no way to tell
        # the repeats apart. Fix that by giving the pass's items ids — cap this
        # list only if a pass turns out to have items it genuinely cannot key.
        anonymous = [item for item in prior.items if not item.id]
        for item in self.items:
            if item.id:
                merged[item.id] = item
            else:
                anonymous.append(item)
        return dataclass_replace(self, items=list(merged.values()) + anonymous)
