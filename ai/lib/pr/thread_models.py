"""Typed domain objects for PR review thread processing.

Persistence-oriented structures live in pr.domains and pr.comments_fix;
these model the runtime pipeline: triage, classification, tracking, and
fix-pass results.
"""

# doc-group: publishing

from __future__ import annotations

from dataclasses import dataclass, field, replace as dataclass_replace
from enum import StrEnum

from core import serde
from pr.comments_state import ThreadState
from pr.fix import FixOutcome, ItemOutcome, SettledBy


# ── Core types ─────────────────────────────────────────────────────────────


class Vocabulary(StrEnum):
    """Shared leniency for the three triage vocabularies.

    An unrecognised value from a model becomes UNSET rather than raising.
    That contract is declared once here so the vocabularies below cannot
    drift. Every subclass MUST define an `UNSET` member whose value is the
    empty string: `_missing_` returns it.

    The member's *value* is what the rule governs, not how it is spelled in
    the class body. A plain subclass writes `UNSET = ""`; one whose members
    carry several spellings writes the empty case of its tuple, and `__new__`
    unpacks that to the same empty value.

    This is the serde-path half of the leniency. `serde.from_dict` constructs
    a field with `hint(value)` and never reaches `__post_init__`. Direct
    construction never consults the enum at all — a dataclass does not coerce
    its own field types — so `_coerce_vocab` in `__post_init__` is the other
    half. Both are load-bearing.
    """

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if "UNSET" not in cls.__members__:
            raise TypeError(f"{cls.__name__} must define an UNSET member")
        # The value, not the spelling. A tuple-valued subclass declaring a
        # non-empty UNSET would satisfy the membership check while breaking
        # every caller that tests a vocabulary field for emptiness.
        if cls.UNSET.value != "":
            raise TypeError(
                f"{cls.__name__}.UNSET must have the empty string as its value, "
                f"not {cls.UNSET.value!r}")

    @classmethod
    def _missing_(cls, value):
        return cls.UNSET


class Classification(Vocabulary):
    """What kind of thing a reviewer's comment is.

    The vocabulary the triage prompt asks for, owned here so the prompt that
    names these values and the code that branches on them cannot drift. A
    `StrEnum` because the stdout report is `json.dump(asdict(...))`, which
    passes a plain `Enum` through unconverted and raises.

    `UNSET` is what an unrecognised or absent answer becomes. It routes
    nowhere, which is the same treatment `approval` gets: neither reaches a
    bucket.
    """

    ACTIONABLE_SUGGESTION = "actionable_suggestion"
    QUESTION = "question"
    APPROVAL = "approval"
    CONFLICTING = "conflicting"
    UNSET = ""


class Verification(Vocabulary):
    """Whether an actionable suggestion holds, and how it is answered.

    Only asked for when the classification is `actionable_suggestion`; the
    prompt says to leave it empty otherwise, which is `UNSET`.
    """

    VALID = "valid"
    ALREADY_ADDRESSED = "already_addressed"
    INVALID = "invalid"
    NEEDS_DISCUSSION = "needs_discussion"
    UNSET = ""

    @property
    def needs_evidence(self) -> bool:
        """Whether this verdict has to cite a line to be posted.

        These two are claims about the reviewer's own code — that it already
        does what they asked, or that their premise is wrong — and a claim with
        no line to point at is not one that can be made. `triage` demotes an
        uncitable one to `NEEDS_DISCUSSION` rather than post it.

        The property lives on the member because it is a fact about the
        verdict. Held as a tuple beside the function that read it, a new
        evidence-bearing verdict would be added here and silently not be one.
        """
        return self in (Verification.ALREADY_ADDRESSED, Verification.INVALID)


class Complexity(Vocabulary):
    """How large a change a valid suggestion asks for.

    Only asked for when the verification is `valid`. `UNSET` is a valid
    answer and deliberately stays fixable — an entry the model gave no
    complexity to is not thereby a job for a person.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNSET = ""


class CommentSourceKind(Vocabulary):
    """Which kind of top-level comment a decomposed item was cut from.

    Unlike its three siblings this is not a vocabulary a model chooses: triage
    stamps it from the list the comment was read out of. It is a `Vocabulary`
    anyway because the value survives a state-file round trip and reaches the
    same `_lenient_from_dict` path, where a raise costs the whole entry rather
    than the one field.

    Each kind is spelled three ways, and every one of them used to be written
    out separately:

    - `value` is the token on `CommentItem.source_type`, persisted and shown to
      the model in the triage schema
    - `id_prefix` opens the synthetic id triage assigns, `{prefix}-{source}-{n}`
    - `anchor` is the GitHub URL fragment a permalink to that comment ends in

    Holding them on one member is what makes a new kind one declaration. Held
    apart, the id prefix and the token were related by a two-armed ternary
    whose else-branch claimed every unrecognised token was a review body, so a
    drifted `source_type` produced an `rb-` id for an issue comment and a
    permalink that resolved to nothing.

    `UNSET` has no anchor and no prefix. A review thread is not a comment item
    at all, so its entries hold this and `permalink` answers None.
    """

    ISSUE_COMMENT = ("issue_comment", "ic", "issuecomment")
    REVIEW_BODY = ("review_body", "rb", "pullrequestreview")
    UNSET = ("", "", "")

    def __new__(cls, value: str, id_prefix: str, anchor: str) -> CommentSourceKind:
        obj = str.__new__(cls, value)
        obj._value_ = value
        obj.id_prefix = id_prefix
        obj.anchor = anchor
        return obj

    @classmethod
    def from_id_prefix(cls, prefix: str) -> CommentSourceKind:
        """The kind whose synthetic ids open with `prefix`, or `UNSET`.

        `UNSET`'s own prefix is empty and is deliberately not matchable: a
        caller asking about `""` is asking about a thread id, which belongs to
        no kind.
        """
        if not prefix:
            return cls.UNSET
        for kind in cls:
            if kind is not cls.UNSET and kind.id_prefix == prefix:
                return kind
        return cls.UNSET

    @classmethod
    def anchors(cls) -> tuple[str, ...]:
        """Every real kind's anchor, in definition order.

        The readers compile their patterns from this rather than restating the
        alternation, so a kind added here is one a published permalink can be
        recognised by without a second edit.
        """
        return tuple(k.anchor for k in cls if k is not cls.UNSET)


# The fragment a permalink to a review thread ends in. Not a member of the enum
# above: a thread is what a comment item is not, and the two are alternatives
# everywhere they are read. It lives here so the writer in `permalinks` and the
# readers in `summary_model` and the reply path share one spelling.
THREAD_ANCHOR = "discussion_r"


def _coerce_vocab(enum_cls, value):
    """One of `enum_cls`'s members, or its `UNSET`.

    The two hooks cover two different construction paths. `_missing_` on
    `Vocabulary` covers construction through serde, which never reaches
    `__post_init__`. This function in `__post_init__` covers DIRECT
    construction, which never consults the enum at all — a dataclass does not
    coerce its own field types, so `CommentItem(verification="banana")` would
    otherwise store the raw string. Every existing test in this repo constructs
    `CommentItem` directly with bare strings, so both are load-bearing.

    With `_missing_` inherited, `enum_cls(value)` never raises: an unrecognised
    or non-string input becomes `UNSET` inside the enum constructor. The
    conversion is therefore a direct call.
    """
    return enum_cls(value)


@dataclass
class CommentItem:
    """A PR review comment at any pipeline stage.

    Covers inline review threads, decomposed top-level comment items,
    and post-classification entries. Fields unused at a given stage
    default to empty values.
    """

    id: str = ""
    file: str = ""
    line: int = 0
    reviewer: str = ""
    summary: str = ""
    reason: str = ""
    reasoning: str = ""
    state: str = ""
    source_id: str = ""
    source_type: CommentSourceKind = CommentSourceKind.UNSET
    index: int = 0
    classification: Classification = Classification.UNSET
    verification: Verification = Verification.UNSET
    complexity: Complexity = Complexity.UNSET
    body: str = ""
    # Where in the tree the verdict can be checked. A verdict posted back to a
    # reviewer has to point at code, so triage cites the location it read.
    evidence_file: str = ""
    evidence_line: int = 0
    # The commit that fixed this entry, when it is known to be an older one than
    # the pass now running. Set when an entry is rebuilt from an ItemOutcome to
    # drain a deferred reply queue; empty for an entry the current pass fixed,
    # which the pass's own SHA covers.
    commit_sha: str = ""
    # The tree `line` and `evidence_line` were read in. A line number is a
    # coordinate in one tree and means nothing in another, so a permalink into a
    # different commit has to drop its anchor rather than send the reviewer to
    # whatever code inherited the number. Empty means "not recorded", which
    # reads as "cannot anchor".
    read_sha: str = ""
    # What decided this entry, and who decided it. Both are the record's answer
    # rather than the item's, so they are set only on the replay path — an entry
    # triage just built is the running pass's own work and holds the defaults.
    # A renderer asks `settled_by` before crediting the pass's commit for a row;
    # without the field it would have to recognise the prose in `reason`.
    outcome: FixOutcome | None = None
    settled_by: SettledBy = SettledBy.PASS
    # Whether anything was run against this fix and passed, and what. Carried on
    # the entry as well as the outcome because `--finish` renders replies and
    # the summary out of state rather than out of the pass that wrote them: a
    # field the drain drops is one the published reply would have to guess at,
    # and the confident guess is the one that makes the false claim.
    #
    # None is "the gate did not run", which is not the same as "the gate could
    # not tell" — see `ItemOutcome.verified`, which this mirrors.
    verified: bool | None = None
    verify_detail: str = ""

    def __post_init__(self) -> None:
        self.line = int(self.line or 0)
        self.index = int(self.index or 0)
        self.evidence_line = int(self.evidence_line or 0)
        # The three fields a model fills in, coerced where the ints already
        # are. Not left to `serde`: its enum coercion raises on an unknown
        # value, and `_lenient_from_dict` answers a raise by discarding the
        # whole entry. An invented verdict should cost itself, not the id and
        # summary the model got right.
        self.classification = _coerce_vocab(Classification, self.classification)
        self.verification = _coerce_vocab(Verification, self.verification)
        self.complexity = _coerce_vocab(Complexity, self.complexity)
        # Not a field the model invents, but one that arrives from a state file
        # and from every test that builds an entry with a bare string, so it
        # needs the same direct-construction coercion the three above do.
        self.source_type = _coerce_vocab(CommentSourceKind, self.source_type)

    def has_evidence(self) -> bool:
        """Whether this item cites a location a permalink can point at."""
        return bool(self.evidence_file) and self.evidence_line > 0

    def to_outcome(
        self, outcome: FixOutcome | None = None, reason: str = "",
    ) -> ItemOutcome:
        """This entry as the record writes it — everything but the reviewer.

        `outcome` is the verdict to record. Omitting it records the one the entry
        already carries, which is how a round drained from state writes back what
        it read; an entry carrying none is work nobody decided, and records as
        DEFERRED for the reason `ItemOutcome` defaults that way.

        The login is the item as GitHub handed it over rather than a fact about
        what the pass did, so it is recorded beside the record in
        `FixSummary.reviewers` and does not travel here. `from_outcome` is what
        puts the two back together, and the two are inverses: what goes out
        through one comes back through the other, provenance included.

        The evidence location travels for the same reason the anchor does, and
        is not the same location: a record that kept only the anchor made the
        round that replayed it ask `git log -L` at a different line from the
        round that published it, which is how a row published as a fix came back
        one round later reworded as one that needed no action.
        """
        return ItemOutcome(
            id=self.id,
            file=self.file,
            line=self.line,
            summary=self.summary,
            outcome=outcome or self.outcome or FixOutcome.DEFERRED,
            settled_by=self.settled_by,
            reason=reason or self.reason or self.reasoning,
            commit_sha=self.commit_sha,
            read_sha=self.read_sha,
            evidence_file=self.evidence_file,
            evidence_line=self.evidence_line,
            verified=self.verified,
            verify_detail=self.verify_detail,
        )

    @classmethod
    def from_outcome(
        cls, outcome: ItemOutcome, reviewer: str = "", *, reason_field: str = "reason",
    ) -> "CommentItem":
        """A recorded outcome as an entry again, for a surface that renders one.

        The replay path for everything `--finish` picks up out of state: the
        reply queue, the deferred tracking issue, the re-rendered summary. Each
        of those reads entries rather than records, so a round drained from disk
        goes through here and every renderer downstream sees one type.

        `reason_field` is which of the two the outcome's reason lands in. The
        reply templates read `reasoning` — a dismissal's justification is the
        body of the reply — while the summary table and the tracking issue read
        `reason`, so the caller names the one its surface will look at rather
        than filling both and letting a renderer pick the wrong half.

        The verdict and its provenance travel too. A renderer holding a replayed
        entry has to know whether the running pass is the thing that settled it
        before it credits that pass's commit, and the only other trace of that
        is the wording of `reason`.
        """
        return cls(
            id=outcome.id,
            file=outcome.file,
            line=outcome.line,
            reviewer=reviewer,
            summary=outcome.summary,
            commit_sha=outcome.commit_sha,
            read_sha=outcome.read_sha,
            evidence_file=outcome.evidence_file,
            evidence_line=outcome.evidence_line,
            outcome=outcome.outcome,
            settled_by=outcome.settled_by,
            verified=outcome.verified,
            verify_detail=outcome.verify_detail,
            **{reason_field: outcome.reason},
        )


# ── Triage result types ──────────────────────────────────────────────────


@dataclass
class TriageStats:
    total: int = 0
    actionable: int = 0
    questions: int = 0
    approvals: int = 0
    conflicting: int = 0
    valid: int = 0
    invalid: int = 0
    comment_items_total: int = 0
    comment_items_actionable: int = 0


@dataclass
class TriageResult:
    """Complete triage classification output from AI."""

    threads: list[CommentItem] = field(default_factory=list)
    comment_items: list[CommentItem] = field(default_factory=list)
    stats: TriageStats = field(default_factory=TriageStats)


class Disposition(StrEnum):
    """Where a classified entry goes.

    Not a `FixOutcome`: `FIXABLE` is a question the agent has yet to answer,
    and comes back from it as FIXED, DEFERRED, NEEDS_HUMAN or DECLINED.
    """

    FIXABLE = "fixable"
    NEEDS_HUMAN = "needs_human"
    DISMISSED = "dismissed"
    ALREADY_ADDRESSED = "already_addressed"


@dataclass
class ClassificationResult:
    """What one side of triage decided about each entry it was given.

    Every disposition the fix pass routes on, ahead of the agent: what it
    will be asked to fix, what a person has to answer, what does not hold, and
    what the code already does. They are not `FixOutcome`s and must not be
    confused for them — `fixable` is a question the agent has yet to answer,
    and comes back from it as FIXED, DEFERRED, NEEDS_HUMAN or DECLINED.

    Entries carry their full triage fields rather than being reduced to ids:
    the checklist the agent is handed needs the summary and the conversation,
    and the reply to a dismissal needs the reasoning.

    One side of the round only. Threads and decomposed comment items are
    classified separately because only a thread has somewhere to reply — see
    `TriagedRound`, which holds both and merges them where a surface treats
    them alike.
    """

    fixable: list[CommentItem] = field(default_factory=list)
    needs_human: list[CommentItem] = field(default_factory=list)
    dismissed: list[CommentItem] = field(default_factory=list)
    already_addressed: list[CommentItem] = field(default_factory=list)

    def bucket(self, disposition: Disposition) -> list[CommentItem]:
        """The entries filed under one disposition.

        A `Disposition` is not a `FixOutcome`. `FIXABLE` is a question the
        agent has yet to answer; `TrackingResult.bucket` is the same verb on
        the container keyed by what came back. Appending to what this returns
        files the entry; `TrackingResult.bucket` does not work that way — it
        returns a throwaway list on a miss, and writing goes through `add()`.

        Each `Disposition` value must equal a field name on this class;
        `getattr(self, disposition.value)` is the lookup. The drift test on
        `ClassificationResult` guards that coupling.
        """
        return getattr(self, disposition.value)

    @property
    def any_entry(self) -> bool:
        """Whether triage put anything at all in this side's buckets."""
        return any(self.bucket(d) for d in Disposition)

    def ids(self) -> set[str]:
        """Every id this side gave a disposition to.

        What `has_unaccounted` is measured against: a thread on the PR that
        appears under no disposition is one this round never reached, and
        the summary it publishes is partial until someone does.
        """
        return {e.id for d in Disposition for e in self.bucket(d)}


# ── Fix tracking types ────────────────────────────────────────────────────


@dataclass(frozen=True)
class ReplyOutcome:
    """What a round's replies did: how many went out, and what they resolved.

    The two travel together because they are saved together. `persist` applies
    the resolution delta in the same write that records the reply count, and
    the comment tally on disk was snapshotted before the pass ran — so a delta
    that arrives in a second save is a delta the tally never sees.

    They were a pair of loose locals accumulated across two phases, which is a
    tuple with the parentheses left off: triage replies to the dismissed and
    the already-addressed, then the pass replies to what it fixed, and both
    halves have to reach one `persist` call. A value that adds to another is
    how the second phase extends the first without either knowing the shape of
    the sum.

    `resolved` names the bucket each thread came from rather than the thread,
    because that is what the tally moves between — see
    `CommentsSummary.move_to_resolved`.
    """

    posted: int = 0
    resolved: tuple[ThreadState, ...] = ()

    def plus(self, other: "ReplyOutcome") -> "ReplyOutcome":
        """This round's replies, extended by a later phase's."""
        return ReplyOutcome(
            posted=self.posted + other.posted,
            resolved=(*self.resolved, *other.resolved),
        )


# What a verdict means when the agent ticked its box without saying why. FIXED
# is absent deliberately: the change itself is the reason, and an entry that
# needs no explanation should carry whatever triage already put on it.
_UNSTATED_REASON = {
    FixOutcome.DEFERRED: "agent could not auto-fix",
    FixOutcome.NEEDS_HUMAN: "agent could not auto-fix",
    FixOutcome.DECLINED: "agent declined without giving a reason",
}


@dataclass
class TrackingResult:
    """What the fix agent recorded, sorted back onto the entries it was handed.

    Keyed by :class:`~pr.fix.FixOutcome` rather than held in a field per verdict:
    the vocabulary belongs to the tracking file, and a named field for each of
    its members would have to grow here every time that file learns to say
    something new. Two dicts rather than one because only an inline thread has
    somewhere to reply — the entries decomposed out of top-level comments are
    reported on but never replied to, so every surface downstream needs the two
    apart or together on demand rather than merged on the way in.
    """

    threads: dict[FixOutcome, list[CommentItem]] = field(default_factory=dict)
    items: dict[FixOutcome, list[CommentItem]] = field(default_factory=dict)

    @classmethod
    def from_outcomes(
        cls, outcomes: list[ItemOutcome], fixable: list[CommentItem],
        fixable_items: list[CommentItem] | None = None,
    ) -> "TrackingResult":
        """Sort the agent's recorded outcomes back onto the entries they belong to.

        Reading the file is `fix.tracking`'s job and stays there: that module
        knows the format and nothing about which domain handed the entries
        over. What this adds is the domain's own entries. An outcome carries an
        id and a verdict, while the reviewer, the summary and the conversation
        behind that id live on the `CommentItem` the pass started from — so the
        outcome selects the bucket and the entry is what goes in it.

        A constructor rather than a function beside the type: the two dicts it
        fills are private to the shape above, and nothing outside should be
        reaching for `add` in a loop to build one.

        An id the pass did not hand over is skipped. The file the outcomes were
        read from is agent-editable, and a section that names no thread is a
        section with no reviewer to reply to.

        Which entry the id resolves to and which side it is filed under are one
        decision. They used to be two — the entry was looked up threads-first
        and the side was `id in items_by_id` — so an id both sides claimed took
        the thread's entry and was filed as an item, which is a thread nothing
        would ever reply to. Unreachable today, because a comment item's id is
        synthesised with an `ic-`/`rb-` prefix and a thread's is GitHub's, but
        the two answers were free to disagree and only the prefix was stopping
        them.
        """
        fixable_by_id = {t.id: t for t in fixable}
        items_by_id = {it.id: it for it in (fixable_items or [])}
        result = cls()

        for recorded in outcomes:
            is_item = recorded.id not in fixable_by_id
            source = items_by_id.get(recorded.id) if is_item else fixable_by_id[recorded.id]
            if not source:
                continue
            reason = recorded.reason or _UNSTATED_REASON.get(recorded.outcome, "")
            result.add(
                recorded.outcome,
                dataclass_replace(source, reason=reason or source.reason),
                item=is_item,
            )

        return result

    def _side(self, item: bool) -> dict[FixOutcome, list[CommentItem]]:
        return self.items if item else self.threads

    def bucket(self, outcome: FixOutcome, *, item: bool = False) -> list[CommentItem]:
        """The entries recorded under one outcome, threads by default."""
        return self._side(item).get(outcome, [])

    def both(self, outcome: FixOutcome) -> list[CommentItem]:
        """Threads and comment items alike, for a surface that treats them the same."""
        return self.bucket(outcome) + self.bucket(outcome, item=True)

    def add(self, outcome: FixOutcome, entry: CommentItem, *, item: bool = False) -> None:
        """Record one entry under one outcome."""
        self._side(item).setdefault(outcome, []).append(entry)

    def merge(self, other: TrackingResult) -> None:
        """Accumulate another result's entries into this one."""
        for item, side in ((False, other.threads), (True, other.items)):
            for outcome, entries in side.items():
                self._side(item).setdefault(outcome, []).extend(entries)

    def drop(self, outcome: FixOutcome) -> None:
        """Forget everything recorded under one outcome, threads and items both.

        What a retry supersedes: the entries it was handed have been decided
        again, and keeping the first answer beside the second would report both.
        """
        self.threads.pop(outcome, None)
        self.items.pop(outcome, None)


# ── Report types ──────────────────────────────────────────────────────────


@dataclass
class ReportThread:
    """A thread in the PR report, combining GitHub data with lifecycle state."""

    id: str = ""
    state: ThreadState = ThreadState.NEW
    classification: str | None = None
    reviewer: str = ""
    comments: list[dict] = field(default_factory=list)
    is_resolved: bool = False
    file: str = ""
    line: int | None = None
    # The login that makes a comment on this thread ours. Carried per thread
    # because the reply upsert decides edit-vs-post from comment authorship,
    # several call layers below the PRReport that knows the login. Empty means
    # "cannot tell", which the upsert reads as post rather than edit.
    my_login: str = ""


def finding_location(entry: CommentItem | ReportThread) -> str:
    """Reviewer and code location of an entry, or "" when it has neither.

    The join key between a review thread and a decomposed comment item that
    restates it. Both halves are required: a file on its own is too coarse to
    call two findings the same point, and two reviewers writing about one line
    are writing about two different things.

    Every use of the heuristic reads the tradeoff below:
    `summary_model.duplicate_item_ids` folds the pair out of a fresh render,
    and `summary_scope.carried_over_rows` reads the same keys so it does not
    reinstate a row this render folded. It recovers the published side's keys
    from rendered markdown with `summary_scope.row_location_key`, which is why
    that function strips the `@` the Reviewer cell is written with — the two
    forms are compared and must spell the login alike.

    "" is not "no match", it is "cannot answer", and the fold treats it that
    way: an entry with no line falls to `summary_model.ThreadRestatement`
    instead of silently escaping. Triage is asked for a line only "if
    referenced in the item", so an empty key is the ordinary shape of a
    decomposed item rather than an edge case.
    """
    # ceiling: reviewer plus file:line is the whole test for "the same point",
    # so two distinct findings by one reviewer on one line fold into one row —
    # and, read off a published row instead of an entry, a colliding row is
    # dropped from the carried-over set rather than preserved.
    # Upgrade trigger: once a reviewer's separate findings on a single line are
    # seen collapsing, compare the summaries too rather than the location alone.
    line = entry.line or 0
    if not entry.file or not line:
        return ""
    return f"{entry.reviewer}|{entry.file}:{line}"


@dataclass
class PRReport:
    """Assembled PR report passed between pipeline stages."""

    repo: str = ""
    pr_number: int = 0
    my_login: str = ""
    threads: list[ReportThread] = field(default_factory=list)
    issue_comments: list[dict] = field(default_factory=list)
    review_body_comments: list[dict] = field(default_factory=list)
    verdicts: list[dict] = field(default_factory=list)


# ── Fix pass result types ─────────────────────────────────────────────────


@dataclass
class CommentFixResult:
    """Complete results from a comment fix pass."""

    fixed: list[CommentItem] = field(default_factory=list)
    needs_human: list[CommentItem] = field(default_factory=list)
    dismissed: list[CommentItem] = field(default_factory=list)
    already_addressed: list[CommentItem] = field(default_factory=list)
    deferred: list[CommentItem] = field(default_factory=list)
    commit_sha: str | None = None
    commit_status: str = ""
    replies_posted: int = 0
    summary_url: str | None = None
    summary_deferred: bool = False
    # Per-batch, not pass-wide: the fix pass runs one agent invocation per batch
    # of items, each with its own budget. `batches` is what makes these readable.
    max_turns: int = 0
    max_budget: float = 0.0
    batches: int = 0


# ── Deserialization helpers ───────────────────────────────────────────────


def _lenient_from_dict(cls, raw):
    """`serde.from_dict`, but a wrong-shaped raw value defaults instead of raising.

    This reads AI-generated triage JSON, which is malformed occasionally by
    nature — a thread entry that comes back as a bare string, `stats` as an
    empty list. `serde.from_dict` rejects a non-dict top level with
    `TypeError` so a state file can be discarded; here there is no file to
    discard, only one entry in a batch, and the caller — a single triage
    pass — should not crash for the whole PR over one malformed field.
    Neither `CommentItem` nor `TriageStats` has a required field, so the only
    way this raises is the non-dict case, not a missing-field one.

    `ValueError` is caught beside it because `CommentItem` carries enum fields
    the replay path sets (`FixOutcome`, `SettledBy`). Those have no `_missing_`,
    so `serde` still raises rather than defaulting for a value it does not
    recognise. The three vocabulary enums (`Classification`, `Verification`,
    `Complexity`) do not raise: `Vocabulary._missing_` returns `UNSET`. Those
    replay keys are not in the triage schema, so a model emitting one has
    invented it — that entry defaults rather than taking the batch down with it.

    Catching it widens the net past the enums, and deliberately: `__post_init__`
    coerces `line`, `index` and `evidence_line` with `int()`, which raises the
    same way for a model that writes `"line": "abc"`. That used to crash the
    pass over one unreadable number, which is the outcome this helper exists to
    prevent — a malformed field costs the entry, not the PR.
    """
    try:
        return serde.from_dict(cls, raw)
    except (TypeError, ValueError):
        return cls()


def _lenient_list(raw):
    """The list behind a triage key, or an empty one if it is anything else.

    `d.get(key, [])` only falls back when the key is absent, and the model
    emits the key with an explicit `null` often enough that iterating the
    result is its own crash — one the per-entry wrapper below cannot catch,
    because it never gets called. A scalar is no more iterable than `None`.
    """
    return raw if isinstance(raw, list) else []


def triage_result_from_dict(d: dict) -> TriageResult:
    """Parse AI triage JSON output into typed structures."""
    threads = [_lenient_from_dict(CommentItem, t) for t in _lenient_list(d.get("threads"))]
    items = [_lenient_from_dict(CommentItem, it) for it in _lenient_list(d.get("comment_items"))]
    stats = _lenient_from_dict(TriageStats, d.get("stats", {}))
    return TriageResult(threads=threads, comment_items=items, stats=stats)
