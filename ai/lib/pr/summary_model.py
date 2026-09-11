"""What a summary round is made of: its rows' vocabulary, and its identity.

The value types the summary comment is built from, and the two questions that
have to be answered the same way on both sides of a round trip through GitHub:
what outcome an Action cell reports, and which row a rendered row *is*.

Identity lives here rather than in the renderer for the reason `core.markdown`
holds both halves of cell escaping. A row is rendered to markdown, published,
and re-read on the next round to recover what the summary already carried, so
the fresh row and the published one must key alike. Deriving the key from the
rendered cells is what makes that true: the file cell reflects
`permalinks.anchored_line`'s runtime decision rather than the entry's own line
number, and a summary containing a markdown link renders nested. A key built
from the typed entry disagrees with the published row in exactly those two
cases — see `row_key_from_cells`.

What is not here: how a cell is built (`pr.summary_row`), how the body around
the table is rendered (`pr.summary_render`), how a published body is read back
(`pr.summary_scope`), and which rows a round may leave to an earlier comment
(`pr.summary_rounds`).
"""

# doc-group: publishing

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum

from core import markdown
from pr import comments_fix as pr_comments_fix
from pr import permalinks
from pr.fix import FixOutcome
from pr.thread_models import CommentItem, ReportThread, finding_location

TABLE_COLUMNS = ("Thread", "Reviewer", "File", "Action")


TABLE_HEADER = f"| {' | '.join(TABLE_COLUMNS)} |"


TABLE_DIVIDER = "|" + "|".join("-" * 8 for _ in TABLE_COLUMNS) + "|"


# The outcomes the summary shows a reviewer under one heading. A thread the
# agent argued against and one it could not decide both end the same way — an
# operator has to read it — and the reason is carried per row either way, so the
# distinction the state file keeps buys the summary nothing.
NEEDS_A_PERSON = (FixOutcome.NEEDS_HUMAN, FixOutcome.DECLINED)


def unseen_comments(comments: list[dict]) -> list[dict]:
    """The comments no summary has reported yet.

    One spelling of the flag for its two readers — `RoundContent.unseen`, which
    decides whether a round has anything to say, and
    `_render_raw_comment_sections`, which prints them.
    """
    return [c for c in comments if not c.get("seen")]


def duplicate_item_ids(
    entries: list[CommentItem], threads_by_id: dict[str, ReportThread],
) -> set[str]:
    """Ids of comment items a review thread in the same table already covers.

    Triage decomposes a top-level comment into items without knowing which of
    its points an inline thread already carries, so one review finding can
    arrive twice — once as a thread and once as a fragment of the comment that
    restated it. Rendering both puts one point in the table under two outcomes
    that need not agree, which reads as the summary contradicting itself. The
    thread is the copy that stays: it is where the reply lands and where
    resolution is recorded.

    What counts as "the same point" is `finding_location`, whose ceiling
    comment names what that coarsening costs.

    `e.id in threads_by_id` is the test for "this entry is a review thread"
    rather than a lookup across two id spaces: a thread entry carries the
    thread's own id, while a decomposed item carries a synthetic `ic-`/`rb-`
    one that no thread can have. So the entries that hit are exactly the
    threads, and the ones that miss are the items being tested against them.
    """
    covered = {
        finding_location(e) for e in entries if e.id in threads_by_id
    } - {""}
    return {
        e.id for e in entries
        if e.id not in threads_by_id
        and permalinks.comment_item_source(e).ok
        and finding_location(e) in covered
    }

@dataclass(frozen=True)
class RoundContent:
    """Everything one round of the fix pass has to say, in one value.

    The buckets are a mapping keyed by `FixOutcome` for the reason
    `_build_fix_record` takes one: the outcomes are `FixOutcome`'s to name, and
    a value spelling them out as fields has to grow a field — and every
    construction site an argument — each time that vocabulary does. A mapping
    also makes miswiring unrepresentable, since a bucket is reached by the
    outcome naming it rather than by position; the five keyword-only parameters
    `folded_item_ids` used to take were a guard against exactly that.

    The unseen top-level comments ride along because the questions a round is
    asked are asked of both halves at once — see `has_content`. Every field is
    required: the round that printed a table and recorded that it owed nothing
    was one caller passing a bucket another caller did not, and a default here
    would leave that available to the next construction site.
    """

    by_outcome: Mapping[FixOutcome, list[CommentItem]]
    issue_comments: list[dict]
    review_body_comments: list[dict]

    def of(self, *outcomes: FixOutcome) -> list[CommentItem]:
        """The entries under these outcomes, in the order the outcomes are given.

        An outcome no entry reached contributes nothing rather than raising. A
        round is under no obligation to produce every outcome — the pass with
        nothing to fix produces three of them at most — so absence is an
        ordinary answer here, not a missing key.
        """
        return [e for o in outcomes for e in self.by_outcome.get(o, [])]

    @property
    def needs_a_person(self) -> list[CommentItem]:
        """The entries a reviewer has to answer, under every outcome meaning that.

        One coarsening, in one place. The state file keeps `DECLINED` apart from
        `NEEDS_HUMAN` because the reason each carries is worth telling apart,
        and they travel together to every reviewer-facing surface — see
        `NEEDS_A_PERSON`. Spelled out at a call site instead, a member added to
        that constant reaches the surfaces that read it and silently misses the
        ones that listed its members by hand.
        """
        return self.of(*NEEDS_A_PERSON)

    @property
    def unseen(self) -> list[dict]:
        """The top-level comments no summary has reported yet, both kinds at once."""
        return unseen_comments([*self.issue_comments, *self.review_body_comments])

    @property
    def has_content(self) -> bool:
        """Whether this round has anything for the fix summary to say.

        One owner for the question, because three callers have to agree on it:
        `_post_fix_summary` renders nothing when the answer is no,
        `_summary_still_owed` must leave a summary owed exactly when a render
        would produce one, and the no-fixable path decides from it whether to
        attempt the post at all. Asked separately they drift, and a bucket one
        of them forgets is a round whose table is printed and never published.

        An unseen issue or review-body comment counts on its own. The summary
        reports those too, so a round that settled no thread at all still has a
        table to render when one arrived unread.
        """
        return bool(any(self.by_outcome.values()) or self.unseen)


def thread_covered_locations(
    entries: Iterable[CommentItem], threads_by_id: dict[str, ReportThread],
) -> frozenset[str]:
    """The locations a review thread in this round speaks for.

    One question with two readers: `duplicate_item_ids` folds a comment item
    at one of these locations out of the render, and `_carried_over_rows` drops
    the published row restating it rather than carrying it back. Computed once
    here so the two cannot disagree about which locations a thread covers — a
    fold the carry-forward step does not recognise reinstates the duplicate the
    fold removed.
    """
    return frozenset(
        finding_location(e) for e in entries if e.id in threads_by_id
    ) - {""}


def duplicate_item_ids(
    entries: list[CommentItem], threads_by_id: dict[str, ReportThread],
) -> set[str]:
    """Ids of comment items a review thread in the same table already covers.

    Triage decomposes a top-level comment into items without knowing which of
    its points an inline thread already carries, so one review finding can
    arrive twice — once as a thread and once as a fragment of the comment that
    restated it. Rendering both puts one point in the table under two outcomes
    that need not agree, which reads as the summary contradicting itself. The
    thread is the copy that stays: it is where the reply lands and where
    resolution is recorded.

    What counts as "the same point" is `finding_location`, whose ceiling
    comment names what that coarsening costs.
    """
    covered = thread_covered_locations(entries, threads_by_id)
    return {
        e.id for e in entries
        if e.id not in threads_by_id
        and permalinks.comment_item_source(e).ok
        and finding_location(e) in covered
    }


def folded_item_ids(
    content: RoundContent, threads_by_id: dict[str, ReportThread],
) -> set[str]:
    """The ids no bucket will render, folded across every bucket at once.

    Which buckets a fold looks at is one decision, and this is where it is
    made. Folding is cross-bucket by nature — the thread that keeps a point may
    sit in a different bucket from the comment item restating it — so a caller
    holding only one bucket cannot answer for it, and a caller that rebuilt the
    list would be a second place to keep in step with the first.

    `_build_summary_body` reads this to drop the rows and the counts over them;
    `_warn_unattributed_fixes` reads it so what it counts is what the table
    goes on to publish. Both recompute rather than pass a set between them:
    it is a pure read of the buckets, so recomputing makes the two agree by
    construction, where a threaded value could arrive stale and put the count
    back out of step with the table — the very fault this answers.

    Every bucket the round has, not a list of them named here: an outcome added
    to the round's vocabulary is folded across from the moment it exists, where
    a signature naming the buckets would keep folding the old set until someone
    noticed.
    """
    return duplicate_item_ids(_every_entry(content), threads_by_id)


def folded_locations(
    content: RoundContent, threads_by_id: dict[str, ReportThread],
) -> frozenset[str]:
    """The locations this round's fold accounts for, across every bucket.

    `folded_item_ids` says which entries the fold removes; this says where
    they were. `_carried_over_rows` needs the second question, because what it
    has to recognise is a *published* row at one of those locations — there is
    no entry of its own to match ids against.

    Cross-bucket for the reason `folded_item_ids` is, and reading the same
    buckets: the thread keeping a point and the item restating it need not
    share an outcome.
    """
    return thread_covered_locations(_every_entry(content), threads_by_id)


def _every_entry(content: RoundContent) -> list[CommentItem]:
    """Every entry the round holds, whichever bucket it sits in."""
    return [e for entries in content.by_outcome.values() for e in entries]




# The two anchor shapes a published row can be identified by. Not markdown
# primitives: what they match is a GitHub review thread and a decomposed
# comment item, which is PR vocabulary — `core.markdown` owns the cell, this
# owns what a cell of ours says.
#
# They live beside `row_key_from_cells` rather than beside the re-parser
# because identity is what reads them, and identity has one definition that
# both a freshly rendered row and a published one go through.
ITEM_ANCHOR_RE = re.compile(r"#(?:issuecomment|pullrequestreview)-\d+")
THREAD_ANCHOR_RE = re.compile(r"#discussion_r\d+")


def row_key_from_cells(cells: list[str]) -> str:
    """Identity of one summary row, from its cells. The whole definition.

    A thread anchor is identity on its own: one `#discussion_r` id names one
    review thread, which this renderer writes one row for. A comment anchor is
    not. Triage decomposes a top-level comment into N items, and every one of
    them links back to the one `#issuecomment` / `#pullrequestreview` permalink
    the comment has — so the anchor names the source the row was cut from,
    while the summary cell is what tells the siblings apart. Keying the
    siblings alike collapses them: each map built on this key keeps one row per
    key, so N findings reach the table as one.

    Falls back to the row's own text for a row with no permalink — a comment
    item whose source could not be resolved. Link targets and backticks are
    stripped so the same entry keys the same way however it was decorated, and
    the Action cell is left out of every form because a later round changing it
    (deferred to fixed) is exactly the case that must count as the same row.

    Cells, not the typed entry, are what identity is derived from, and the two
    are not interchangeable. The file cell carries whatever
    `permalinks.anchored_line` decided at render time, which is the bare path
    when the anchor could not be held in the SHA being linked — an entry with a
    line number renders, and must key as, a row without one. A summary
    containing a markdown link renders nested, and recovering it from the
    published row yields a different string than the entry's own summary. Both
    cases are ones where a key built from the entry disagrees with the row the
    reader sees, which is the disagreement this function exists to prevent.

    The anchor search runs over the whole row rather than the cell that carries
    the link, because that is what the published-row path has always done and
    narrowing it is a semantic change rather than a refactor.
    """
    row = " | ".join(cells)
    thread = THREAD_ANCHOR_RE.search(row)
    if thread:
        return thread.group(0)
    item = ITEM_ANCHOR_RE.search(row)
    if item:
        # ceiling: the summary cell is the whole of what separates one item
        # from its siblings, so a round that rewords an item's summary keys it
        # as a new row and the published one carries forward beside it.
        # Upgrade trigger: once a reworded item is seen rendering twice, write
        # the item's synthetic id into the row and key on that instead.
        return f"{item.group(0)} | {markdown.plain_cell(cells[0])}"
    return " | ".join(
        markdown.plain_cell(cell) for cell in cells[:len(TABLE_COLUMNS) - 1]
    )


class HumanReason(Enum):
    """Why a thread was routed to a human, in both spellings it is written in.

    `value` is the machine-readable token stamped on the entry's `reason`.
    `to_outcome` persists it as `ItemOutcome.reason`, and a later `--finish`
    reads it back out of the state file to render the Action cell through
    `prose_for` — that round trip is why the values must stay stable. They also
    leave the process verbatim, in the stdout JSON report and the
    `publishing_hold` trail entry, but neither of those interprets them.

    `--track` and the deferral replies read neither: both work the deferred
    bucket, whose `reason` is free text ("agent could not auto-fix") and never
    a member of this enum.

    `prose` is what the summary table's Action cell shows, alongside "Already
    addressed" and "Dismissed (invalid)". One member owns both, so a token
    cannot reach the published comment the way `needs_discussion` once did.
    """

    CONTESTED = ("contested", "Contested — needs discussion")
    CONFLICTING = ("conflicting", "Conflicting reviewer feedback")
    QUESTION = ("question", "Question for the author")
    COMPLEX = ("complex", "Too complex to auto-fix")
    NEEDS_DISCUSSION = ("needs_discussion", "Needs discussion")

    def __new__(cls, value: str, prose: str) -> HumanReason:
        obj = object.__new__(cls)
        obj._value_ = value
        obj.prose = prose
        return obj

    @classmethod
    def prose_for(cls, reason: str) -> str:
        """The Action cell text for `reason`, readable whatever it holds.

        A reason no member claims — an older state file, a hand-edited entry —
        falls back to the generic prose rather than being emitted raw, since an
        unrecognised token in a published comment is the failure this maps away.
        """
        try:
            return cls(reason).prose
        except ValueError:
            return cls.NEEDS_DISCUSSION.prose


# Every opening an Action cell this renderer wrote can have, under the outcome
# that cell reports. The reply-side counterpart of `thread_replies.GENERATED_REPLY_PREFIXES`,
# and read two ways: to tell a cell we produced apart from one somebody rewrote
# by hand, and to tell a round that changed a row's outcome from one that only
# re-worded it.
#
# Naming the outcome is what makes the second reading possible. One outcome is
# written several ways — a fix reported with a commit one round and without one
# the next, see `_fixed_status_for` — so a cell compared against a cell reports
# a change that did not happen, and restates the row for the life of the PR.
#
# No opening may open another under a different outcome, which is what lets
# `action_outcome` scan in any order; the mapping cannot express the rule, so a
# test asserts it. Its absence would be silent — the row is restated every
# round, or left behind holding a stale outcome, with no wording to show which.
#
# Kept in step with the four places a status cell is built — `_fixed_status_for`
# and `_fixed_status_text` (every "Fix…" opening), the literal cells in
# `_build_summary_body`, and `HumanReason.prose`. A new wording that is not
# covered here reads as hand-written, and its row is then frozen at whatever the
# published comment already said.
#
# Retired wordings stay in the table. A summary comment outlives the code that
# wrote it, so an opening no builder produces any more still opens rows on live
# PRs, and dropping it here freezes every one of them.
ACTION_OUTCOMES: Mapping[str, FixOutcome] = {
    "Fixed in ": FixOutcome.FIXED,
    "Fix applied": FixOutcome.FIXED,
    "Fix committed locally": FixOutcome.FIXED,
    "Fix committed and pushed": FixOutcome.FIXED,
    "Fix pending": FixOutcome.FIXED,
    "Added to the PR description (no commit)": FixOutcome.FIXED,
    # Not FIXED, though a fixed row can render it: the cell says the work landed
    # somewhere this run cannot name, which is the same thing a settled-elsewhere
    # row says. Both sides of the comparison read the cell, so a fixed row
    # reading back as this one is not a change and does not restate the row.
    pr_comments_fix.RECONCILED_STATUS_TEXT: FixOutcome.SETTLED_ELSEWHERE,
    "Deferred": FixOutcome.DEFERRED,
    "Already addressed": FixOutcome.ALREADY_ADDRESSED,
    "Dismissed (invalid)": FixOutcome.DISMISSED,
    **{reason.prose: FixOutcome.NEEDS_HUMAN for reason in HumanReason},
}


GENERATED_ACTION_PREFIXES = tuple(ACTION_OUTCOMES)


def action_outcome(cell: str) -> FixOutcome | None:
    """The outcome an Action cell reports, or None for one we did not write.

    Read on both sides of the comparison `RoundScope.covers` makes: the cell a
    published comment carries, and the one this round's render would put in its
    place. Two cells reporting the same outcome in different words is the case
    the table exists to answer, so both sides have to come through here.

    None is not "some outcome we cannot name" but "no claim to compare
    against": a cell a person wrote, or a wording retired before this table was.
    Both are rows `_hand_written_rows` owns, and reading either as an outcome
    would let every round's own render differ from it and restate the row.
    """
    for prefix, outcome in ACTION_OUTCOMES.items():
        if cell.startswith(prefix):
            return outcome
    return None


def is_generated_action(cell: str) -> bool:
    """Whether an Action cell is still one of ours, in template shape.

    ceiling: an opening is the whole test, so a hand edit that appends to a
    generated cell ("Fixed in `abc` — but see below") still reads as generated
    and is overwritten on the next round. Matching the whole cell instead is not
    the fix: the commit SHA and the deferral issue link vary per round, so an
    exact set cannot be written down. Upgrade trigger: if an appended note is
    ever lost this way, mark generated cells with an HTML comment and key off
    that instead of the opening.
    """
    return cell.startswith(GENERATED_ACTION_PREFIXES)


@dataclass(frozen=True)
class HeldRow:
    """A published summary row whose Action cell a human wrote.

    `published` is re-emitted in place of `replaced_by`, which is what this
    render would otherwise have said. Both halves are carried so the run can
    report the swap in full: a hand edit is only defensible as a hand edit if
    the reader can see what the generated cell would have claimed instead.
    """

    key: str
    published: str
    replaced_by: str
