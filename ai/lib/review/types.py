"""The review subsystem's vocabulary: the nouns, with no behaviour around them.

A severity, a disposition, a finding, a review's attribution, and the job a run
threads through every phase. Everything here is a type or a constant that names
one — the modules above hold the code that parses, renders, merges and runs.

The split is about fan-in. `Finding` had 14 consumers and lived beside evidence
verification and document surgery, so wanting the dataclass meant importing all
of it; `ReviewJob` lived beside git collection and budget fitting, so every
phase that takes a job took those too. Naming the vocabulary separately is what
lets a consumer depend on what a review *is* without depending on what the
review pipeline *does*.

Nothing in the review layer is imported here, and nothing should be: this is the
layer everything else in it sits on. The heavier imports — `agent.types`,
`gh.types`, `serde`, `workbench_config` and `pr.state.now_iso` — are all below
the review layer; `ReviewMeta` reaches for `serde` and `ReviewJob` for `gh.types`
and the rest.
"""

# doc-group: findings

from __future__ import annotations

import functools
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import core.serde
import config.workbench_config
from agent.types import EFFORT_PRESETS
from gh.types import PRContext, PRMetadata
from core.phases import Effort, Mode, Phase
from pr.domains import ReviewStatus
from pr.state import now_iso


# ── Severity ─────────────────────────────────────────────────────────────────

SEVERITY_MUST = "M"
SEVERITY_SHOULD = "S"
SEVERITY_NIT = "N"
SEVERITY_IDIOMS = "I"


@dataclass(frozen=True)
class SeverityConfig:
    key: str
    label: str
    section: str
    posting: str
    body_group: str
    json_key: str
    aliases: tuple[str, ...] = ()


SEVERITIES = [
    SeverityConfig(SEVERITY_MUST,    "must-fix",  "Must fix",  posting="inline", body_group="by_severity", json_key="must_fix"),
    SeverityConfig(SEVERITY_SHOULD,  "should-fix", "Should fix", posting="inline", body_group="by_severity", json_key="should_fix"),
    SeverityConfig(SEVERITY_NIT,     "nit",       "Nit",        posting="body",   body_group="by_file", json_key="nit", aliases=("Nits",)),
    SeverityConfig(SEVERITY_IDIOMS,  "idiom",     "Idioms",     posting="body",   body_group="by_file", json_key="idiom"),
]

_SEVERITY_BY_KEY = {s.key: s for s in SEVERITIES}


def severity_by_key(key: str) -> SeverityConfig:
    return _SEVERITY_BY_KEY[key]


# ── Prior dispositions ───────────────────────────────────────────────────────


class PriorDisposition(StrEnum):
    """What a re-review says became of a prior finding.

    The values are what the strict reader validates `disposition` against.
    `FIXED` and `STILL_OPEN` keep their original spellings: a review file
    written before `DECLINED` existed still loads.

    Declaration order is load-bearing: `precedence` reads it, so a member
    declared later outranks every member above it.
    """

    FIXED = "Fixed"
    STILL_OPEN = "Still open"
    # Raised, considered, and rejected on the merits — a documented tradeoff or
    # a decision already made. Distinct from STILL_OPEN, which is outstanding
    # work: carrying an adjudicated finding as open re-presents it every review
    # and feeds it back into the next fix pass.
    DECLINED = "Declined"

    @property
    def precedence(self) -> int:
        """Which verdict survives when two groups disposition one finding.

        A group that still sees a finding beats one that says it went, so
        `STILL_OPEN` outranks `FIXED`. `DECLINED` outranks both: it is a
        judgement about the finding rather than a report on the code, and no
        amount of the code still looking that way overturns it.
        """
        return _DISPOSITION_PRECEDENCE[self]


# Derived from the enum's declaration order rather than restated, so a member
# added to `PriorDisposition` is ranked by where it is declared instead of
# raising a `KeyError` from `precedence` the first time two groups disagree.
_DISPOSITION_PRECEDENCE = {
    member: rank for rank, member in enumerate(PriorDisposition, 1)
}


# ── Coverage ─────────────────────────────────────────────────────────────────


class ReviewType(StrEnum):
    """How much of the branch the review covers.

    Orthogonal to `Mode` — a self-review can be either. The two travel together
    in `meta.json` and reading one for the other is the bug fixed alongside this
    enum, so neither vocabulary borrows the other's members.
    """

    FULL = "full"
    INCREMENTAL = "incremental"

    @classmethod
    def of(cls, incremental: bool) -> "ReviewType":
        """The type a run of this shape has — the sidecar, the header and the
        pipeline state all record it, and they must agree."""
        return cls.INCREMENTAL if incremental else cls.FULL


class DeltaAttribution(StrEnum):
    """How far a re-review got in working out what the author changed.

    The delta drives two decisions — whether to run at all, and at what size —
    and they need different things from it. Skipping the run needs to know the
    author changed nothing; sizing the run needs to know the file count means
    anything. An empty list satisfies the first only if it is `ATTRIBUTED`, and
    a bare boolean could not tell the second which case it was in.

    `ATTRIBUTED` is the ancestry walk having run and answered. `UNATTRIBUTED`
    is the walk not having been possible — an unresolvable base ref, a git read
    that failed, a self-review, whose delta is the whole range since the prior
    review and whose emptiness proves nothing. `NONE` is a full review, which
    has no delta to attribute.
    """

    NONE = "none"
    ATTRIBUTED = "attributed"
    UNATTRIBUTED = "unattributed"


class Pipeline(StrEnum):
    """Which of the two shapes a run took.

    A branch small enough for one agent to hold takes `SINGLE`; anything above
    the effort preset's file and line thresholds is split across the phases and
    takes `MULTI`. The values are what a run reports as its `mode`, so they stay
    as they are for anything reading a completed run's JSON.
    """

    MULTI = "multi"
    SINGLE = "single"


class ReplyState(StrEnum):
    """What became of a finding the last review posted, as its thread reads now.

    Answered from the reviewer's side: the bot posted the root comment, and the
    state is what the author did with it. `UNREPLIED` and `RESOLVED` are the two
    ends — nothing said, and closed on GitHub — and the three between them are
    read off the last non-bot reply.

    `pr.comments_state.ThreadState` is a second vocabulary for where a thread
    stands, and the two are not interchangeable: that one answers the question
    from the author's side, for threads a human opened on the author's own PR.
    They share `contested` and `resolved` and agree on neither of the rest.
    """

    RESOLVED = "resolved"
    ACKNOWLEDGED = "acknowledged"
    CONTESTED = "contested"
    REPLIED = "replied"
    UNREPLIED = "unreplied"


class GroupSkip(StrEnum):
    """Why the group phase is not running a group.

    The two reasons disagree about what a missing ``group-N.json`` means, so they
    stay distinct all the way to the executor. A recovery skip reuses a prior
    attempt's output, which must therefore be on disk — its absence is a real
    failure. A carried skip never had output: an incremental run takes that
    group's findings from the prior review's text, and a completed run sweeps
    its own group files, so the absence is the expected state.
    """

    RECOVERY = "recovery"
    CARRIED = "carried"


# ── Sticky skips ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StickySkip:
    """A finding the fix pass handed to a person, held until its file changes.

    Keyed in `ReviewMeta.sticky_skips` by `path:line`. `blob` is the anchor
    file's object id when the skip was made: while the file still hashes to it,
    its lines are the lines that were judged, so a later round's finding at the
    same anchor and `severity` is the same call and goes back to the person
    rather than to the agent.
    """

    blob: str = ""
    severity: str = ""
    reason: str = ""


@dataclass(frozen=True)
class FixInterruption:
    """A fix pass stopped before it landed, and the edits it left behind.

    `leftovers` maps each path the agent had changed to its object id when the
    pass stopped ("" for a deletion). The next run on the same HEAD compares the
    file against it: still equal is the agent's edit, untouched, and is set
    aside; anything else is someone's since, and is left where it is.
    """

    head_before: str = ""
    leftovers: dict[str, str] = field(default_factory=dict)


# ── Review metadata ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ReviewMeta:
    """Everything `meta.json` records about a review — the whole file, typed.

    One field per key on disk and no key without a field: the sidecar is written
    by rendering this and read by reconstructing it, so a writer cannot record
    something no reader can name. Every field has a default, because a sidecar
    outlives the version that wrote it and one written before a field existed
    still reconstructs.
    """

    repo: str = ""
    # The forge the repo is served from, carried because the process that renders
    # this review's permalinks cannot ask. `review-post` is spawned with a review
    # file and a PR number and nothing else — it never resolves a target and never
    # reads a remote — so the sidecar is the only thing that can tell it a link
    # belongs on an enterprise host. Empty is public GitHub, which is what a
    # meta.json written before this field existed correctly means.
    host: str = ""
    pr_number: int | None = None
    head_sha: str = ""
    # HEAD when the review was published, recorded only when a commit landed
    # after the run pinned `head_sha` and before it wrote the review. The review
    # covers `head_sha` and nothing later; this says so rather than re-pinning
    # to a commit the review never read. Empty is the ordinary case.
    head_moved_to: str = ""
    head_ref: str = ""
    base_ref: str = ""
    title: str = ""
    changed_files: int = 0
    generator_version: str = ""
    # The sidecar carries both, and they answer different questions — how much of
    # the branch was reviewed, and what was being reviewed. Both are None for a
    # meta.json written before the field existed, or by a caller that wrote only
    # the repo; `mode` defaulting to PR would claim a fact the file never stated.
    review_type: ReviewType | None = None
    mode: Mode | None = None
    # What an incremental review is a delta against, and which files moved. Both
    # are empty on a full review, which is a delta against nothing. A tuple
    # rather than a list so the frozen dataclass is frozen all the way down.
    prior_sha: str = ""
    delta_files: tuple[str, ...] = ()
    # The files the working tree differed from `head_sha` in when this
    # self-review was published: what it read uncommitted, plus anything that
    # moved while it ran — an edit made mid-run and, when a commit raced in
    # (`head_moved_to` set), that commit's files, which the review never read.
    # The next self-mode incremental round adds them to its change set, so a
    # file whose edit was reverted in between — back to matching HEAD,
    # invisible to any diff — is read again; over-listing costs a re-read,
    # never a skipped file. Empty on a PR review and on a clean tree.
    dirty_files: tuple[str, ...] = ()
    # The stable ids of the nits and idioms withheld from fix work (D13): on an
    # incremental round, the prior round's held nits plus this round's new
    # ones; empty after a full round, which hands every severity to the fix
    # pass. Read by `run_fix_pass`, so a resumed or later pass on this review
    # withholds what the publishing round withheld.
    held_nits: tuple[str, ...] = ()
    # Two timestamps because a review run has two moments worth recording and
    # they are not the same one. `started_at` is taken when the run begins;
    # `reviewed_at` is stamped only where a run reached its end with a review in
    # hand. Both are empty for a meta.json written before they existed — see
    # `ReviewEntry.reviewed_at` for what answers "when" in that case.
    started_at: str = ""
    reviewed_at: str = ""
    # The fix pass's commit, and what became of its push. Recorded here rather
    # than in the PR's state because a self-review need not have a PR, while the
    # review directory exists for every run — and because the commit is a fact
    # about this review rather than about a pull request.
    #
    # `fix_commit_status` is `CommitStatus`'s plain string, not the enum: it is
    # `git.land`'s vocabulary, and naming it here would make the sidecar's
    # schema depend on a module it otherwise knows nothing about.
    fix_commit_sha: str = ""
    fix_commit_status: str = ""
    # A fix pass that was stopped before it landed. Its own field rather than a
    # `fix_commit_status` value: that one is `CommitStatus`'s vocabulary, and an
    # interruption is not something `land` reports. Cleared once the leftovers
    # are set aside (`review.convergence.set_aside_leftovers`), and by any
    # publish, which builds a fresh sidecar.
    fix_interrupted: FixInterruption | None = None
    # Static violations a pass adjudicated rather than fixed, keyed by
    # `StaticViolation.site` and valued with the reason. The `## Static
    # Analysis` section is regenerated from a fresh scan every review, so an
    # annotation written onto it is erased by the next run — which for a
    # decline means the adjudication is lost and the violation is handed back
    # to the agent, every round, forever. A finding keeps its verdict because
    # the document carries it and `run_fix_pass` reads it back; a violation has
    # no such line to keep, so its verdict lives here instead.
    #
    # Keyed on the site hash rather than the `SA<n>` id, which is a position in
    # one rendering and names a different violation in the next.
    static_declined: dict[str, str] = field(default_factory=dict)
    # Findings the fix pass handed to a person, by `path:line`. Accumulated like
    # `static_declined` and for its reason — the document is rewritten every
    # round — and pruned at publish once the anchor file no longer hashes to the
    # recorded blob. See `review.convergence.apply_sticky_skips`.
    sticky_skips: dict[str, StickySkip] = field(default_factory=dict)
    # What the rendered header states about the run beyond attribution. They
    # live here because the record holds what the review says and the sidecar
    # holds what the run was; `review.md`'s header is rendered from these.
    date: str = ""
    prior_date: str = ""
    skipped_groups: int = 0
    total_groups: int = 0
    status: ReviewStatus | None = None

    @property
    def unpushed_fix_commit(self) -> str:
        """The fix commit this review left on the branch, or "" when none is owed.

        Which statuses mean "still local" is `pr.attribution`'s answer, not a
        second list here: it already owns the question for the comments domain,
        and two lists would disagree the first time a status is added — as one
        written by hand here already did, by omitting `push_unverified`.

        An empty status is a sidecar written before this field, and reads as
        nothing owed. Saying otherwise would report every earlier review as
        holding work, which is the false positive that teaches a reader to
        ignore the line.
        """
        from pr.attribution import commit_unpushed

        if not self.fix_commit_status:
            return ""
        return self.fix_commit_sha if commit_unpushed(self.fix_commit_status) else ""


def meta_enum(enum_cls, value):
    """Read a persisted fixed-vocabulary field, tolerating a value we don't know.

    A review's records — `meta.json` and the document's own metadata header —
    are written by whatever version of the reviewer produced the review and read
    by whatever version is running now, so an unrecognised member reads as
    absent rather than taking the whole record down with it.
    """
    try:
        return enum_cls(value) if value else None
    except ValueError:
        return None


# The sidecar's fixed-vocabulary keys, by the enum each is read into.
_META_VOCABULARIES = {"review_type": ReviewType, "mode": Mode, "status": ReviewStatus}


def review_meta_from_dict(d) -> ReviewMeta:
    """The attribution a `meta.json` payload states, keeping what it garbles out.

    `serde` does the reconstruction, so the fields on `ReviewMeta` are the whole
    schema and a key added there is read without a line here. What it will not
    do on its own is survive a vocabulary it does not know: an unrecognised
    `review_type`, `mode`, or `status` raises out of `serde.from_dict` and would
    cost the whole record. Those are dropped to absent first, which is what
    `meta_enum` decides, so one unreadable field costs one field.

    A payload that is not an object at all — a hand-edit that left a list, a
    truncated write that json still parsed — reconstructs as if the file were
    absent. There is nothing in it to attribute a review by.
    """
    if not isinstance(d, dict):
        return ReviewMeta()
    normalised = {
        k: v for k, v in d.items()
        if k not in _META_VOCABULARIES or meta_enum(_META_VOCABULARIES[k], v)
    }
    return core.serde.from_dict(ReviewMeta, normalised)


# ── Findings ─────────────────────────────────────────────────────────────────


@dataclass
class Finding:
    id: str
    severity: str
    seq: int
    path: str
    line: int | None
    end_line: int | None
    body: str
    full_path: str = ""
    posted_id: str = ""
    # The `FindingIdentity.stable_id` of this finding, which a posted comment
    # carries so a later round can match a reply thread back to the finding it
    # hangs off.
    stable_id: str = ""
    classification: str = ""
    skip_reason: str = ""
    checked: bool = False
    # Adjudicated, not outstanding: the finding was considered and rejected on
    # the merits. A skip is the fix pass saying "not me"; a decline is the
    # review saying "not at all", and the fix pass must not revisit it.
    declined: bool = False
    decline_reason: str = ""
    # Handed to a person by a fix pass (`Disposition.SKIPPED`). Not work: the
    # next pass does not take it, and a later round re-raising it at the same
    # anchor is marked skipped until the file changes.
    skipped: bool = False


# ── Prior-finding vocabulary ─────────────────────────────────────────────────

@dataclass(frozen=True)
class FindingRef:
    """How a re-review names a prior finding: its ID and its path.

    The pair travels together because neither half identifies a finding on its
    own — IDs are per-review sequence numbers and one file holds many findings.
    """

    finding_id: str = ""
    path: str = ""

    @property
    def label(self) -> str:
        """How the reference reads in a log line.

        A reference with no path is the ID alone rather than the ID and an
        empty pair of backticks — the line is already reporting that nothing
        read a path off the finding, and printing `` there says it twice.
        """
        if not self.path:
            return self.finding_id
        return f"{self.finding_id} `{self.path}`".strip()


@dataclass(frozen=True)
class PriorFinding:
    """A finding line from the prior review, as reconciliation sees it.

    `text` runs from the finding line to whatever ends it, so a finding that
    quotes the code it objects to below its first line keeps the quotation —
    which is what lets reconciliation ask whether that code is still there.
    """

    ref: FindingRef
    stable_id: str
    text: str = ""
    # Whether the prior round declined it, read from the record's disposition
    # rather than re-parsed from the rendered annotation.
    declined: bool = False


# ── What is under review ─────────────────────────────────────────────────────

@dataclass
class PreflightData:
    diff: str
    commit_log: str
    file_contents: dict[str, str]
    file_permissions: dict[str, str]
    instructions_md: str
    architecture_md: str
    review_checklists: dict[str, str] = field(default_factory=dict)
    # Repo-relative path of the file instructions_md was read from (AGENTS.md
    # or a legacy CLAUDE.md), so the prompt names the file the repo uses.
    instructions_path: str = ""
    review_profiles: list = field(default_factory=list)
    omitted_files: list[str] = field(default_factory=list)
    # Every collected file's byte size, kept or dropped. The contents of a
    # dropped file are gone by the time the block is rendered, so this is the
    # only place its size still exists — and a path with no size beside it is a
    # read the agent cannot price before making it.
    file_sizes: dict[str, int] = field(default_factory=dict)
    delta_diff: str = ""
    delta_commit_log: str = ""
    delta_files: list[str] = field(default_factory=list)
    # Review effort, with deletions weighted below additions by
    # `git.numstat.weighted_lines`. This is what sizes the run.
    #
    # The weighting rounds to the nearest int, so a lone deleted line (weight
    # 0.25) rounds to zero here even though `numstat`'s own reasoning is that
    # a deletion is "not free" — that argument holds in aggregate over a diff,
    # not for one deleted line in isolation.
    delta_weighted_lines: int = 0
    # What the delta actually contains. Reported rather than acted on: the
    # trail's line count has to keep meaning the same thing across runs from
    # before and after the weighting, or no two of them compare.
    delta_raw_lines: int = 0
    # Whether `delta_files` and the two line counts are the author's work or
    # merely everything in the range — which decides whether any can be acted
    # on. Only the ancestry walk in `review.collect` reports `ATTRIBUTED`.
    delta_attribution: DeltaAttribution = DeltaAttribution.NONE

    @property
    def delta_proven_empty(self) -> bool:
        """The author committed nothing since the prior review, established.

        The same question `DeltaScope.proven_empty` answers, asked of the
        record that outlives it. Spelling it out at a call site invites the
        half of it that reads `not delta_files`, which is true of a delta
        nobody could attribute.
        """
        return (
            self.delta_attribution is DeltaAttribution.ATTRIBUTED
            and not self.delta_files
        )
    prior_head_sha: str = ""


@dataclass
class Group:
    name: str
    files: list[str]
    lines: int


class ViewerRole(StrEnum):
    AUTHOR = "author"
    REQUESTED = "requested reviewer"
    REVIEWER = "reviewer"


@dataclass
class ReviewJob:
    repo: str
    pr_number: str
    pr: PRMetadata
    ctx: PRContext
    wt_path: str
    review_file: str
    session_log: str
    # Beside `repo` rather than folded into it: that string is the `--repo`
    # argument and the GraphQL `owner/name`, and a host in it would reach both.
    # In memory only — the job is never serialised; it reaches disk through
    # `ReviewMeta`, which carries its own copy.
    host: str = ""
    issue_link: str = ""
    issue_context: str = ""
    # The prior round as data. Annotated as a string for the reason `PRState`
    # is: `review.store` imports this module, so naming `StoredReview` here
    # would close the cycle.
    prior: "StoredReview | None" = None
    mode: Mode = Mode.PR
    generator_version: str = ""
    preflight: PreflightData | None = None
    model: str = ""
    effort: Effort = Effort.MEDIUM
    # What `--no-<phase>` switched off, kept apart from the effort preset's own
    # skips rather than merged into them at construction: `--disprove` overrides
    # the preset and must still lose to an explicit `--no-disprove`, which it
    # cannot do once the two sources read the same.
    skip_phases: frozenset[Phase] = frozenset()
    include_generated: bool = False
    # Keep a same-HEAD self-review full even when the working tree gives it a
    # delta — what `pr review --self --force` asks for.
    full_review: bool = False
    # The stable ids of this round's new nits, read by
    # `review.outcome._reconcile_and_verify` while the record still carries the
    # sids that tell a carry from a new finding; `_publish` adds them to
    # `ReviewMeta.held_nits`. Empty on every path that adds no finding.
    new_nits: frozenset[str] = frozenset()
    reply_threads: ReplyThreads | None = None
    verification: dict | None = None
    pr_state_data: "PRState | None" = None
    # What the static checkers found, held from the run that wrote the `## Static
    # Analysis` section so the fix pass works the same list a reader sees.
    # Re-running the checkers there would scan a tree the review has already
    # described, and the section and the work set would then disagree.
    #
    # Annotated as a string for the reason `PRState` is: `review.static_analysis`
    # imports `review.document`, which imports this module, so naming the type
    # here would close the cycle.
    static_results: "list[CheckerResult]" = field(default_factory=list)
    viewer_role: str = ""
    throttle: "QuotaThrottle | None" = None
    # Taken when the job is built, not when the sidecar is written: every
    # branch that reaches a review file writes a sidecar, so a timestamp taken
    # there dates the deliverable rather than the run that produced it. This is
    # what `started_at` in meta.json carries.
    started_at: str = field(default_factory=now_iso)

    @functools.cached_property
    def config(self) -> config.workbench_config.WorkbenchConfig:
        """The merged workbench config for this job's worktree, read once.

        A review builds a ``PhaseRunner`` per phase and, in the group phase, one
        per group — every one of them resolving model, thinking and provider
        from the same two files. Cached on the job so the read happens once per
        review rather than once per runner.
        """
        return config.workbench_config.load_config_or_default(self.wt_path)

    @property
    def skipped(self) -> frozenset[Phase]:
        """Every phase this run will not run: the operator's plus the preset's.

        The two sources are indistinguishable to a phase deciding whether to
        run, and only the disprove gate — where ``--disprove`` beats the preset
        — needs to tell them apart. It reads ``skip_phases`` directly.
        """
        return self.skip_phases | EFFORT_PRESETS[self.effort].skips

    @property
    def artifact_dir(self) -> str:
        """This review's own directory — every artifact is a sibling of the review file.

        Agents are granted write access to exactly this, never to the shared reviews
        root: a root grant is how scratch files ended up beside unrelated reviews.
        """
        return str(Path(self.review_file).parent)


@dataclass(frozen=True)
class PromptVerification:
    """What one render was measured against, and whether it may be sent.

    `tokens` is the exact count when one was taken. `token_verified` is derived
    from it, so the two cannot disagree; a missing count records `reason` and
    is neither pass nor fail.
    `ok` is the send decision: bytes always, tokens only when verified.
    """

    prompt_bytes: int
    budget_bytes: int
    tokens: int | None
    reason: str
    ok: bool
    byte_overshoot: int
    token_overshoot: int

    @property
    def token_verified(self) -> bool:
        return self.tokens is not None
