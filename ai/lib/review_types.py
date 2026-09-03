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
layer everything else in it sits on. The heavier imports — `agent_types`,
`serde`, `workbench_config` and `pr_state.now_iso` — are all below the review
layer; `ReviewMeta` reaches for `serde` and `ReviewJob` for the rest.
"""

# doc-group: findings

from __future__ import annotations

import functools
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import serde
import workbench_config
from agent_types import EFFORT_PRESETS
from phases import Effort, Mode, Phase
from pr_state import now_iso


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

    The values are the words the prompt asks for and the words the ledger is
    parsed for, so the two cannot drift apart. `FIXED` and `STILL_OPEN` keep
    their original spellings: a review file written before `DECLINED` existed
    still parses.

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

    @classmethod
    def parse(cls, text: str) -> "PriorDisposition | None":
        """The disposition a ledger line states, if it states one plainly.

        The verdict has to stand on its own — the whole text, or ahead of the
        break that introduces its detail. A qualified one ("Fixed, but only on
        the happy path") is left unparsed rather than read as its optimistic
        half.
        """
        lowered = text.strip().lower()
        for member in cls:
            rest = lowered.removeprefix(member.value.lower())
            if rest != lowered and _DISPOSITION_TAIL_RE.match(rest):
                return member
        return None

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

# An entry whose wording the parser could not read a verdict from. It loses to
# any verdict it can read, and holds its slot against another unreadable one.
_NO_DISPOSITION_PRECEDENCE = 0


def disposition_precedence(disposition: "PriorDisposition | None") -> int:
    """`precedence`, tolerating the unparsed entry a ledger may also carry."""
    return _NO_DISPOSITION_PRECEDENCE if disposition is None else disposition.precedence


# What may follow a disposition without qualifying it: nothing, or a break that
# introduces detail rather than a caveat.
#
# The full stop is on the list because a review states a verdict in a sentence
# as readily as in a clause — "Fixed. `check_key` now calls it directly." is the
# same claim as "Fixed — `check_key` now calls it directly.", and the prompt's
# example cannot show every punctuation a model will reach for. The comma stays
# off it: what follows a comma qualifies the verdict rather than explaining it.
DISPOSITION_TAIL_PUNCTUATION = "—–:(-."
_DISPOSITION_TAIL_RE = re.compile(rf"^\s*(?:[{re.escape(DISPOSITION_TAIL_PUNCTUATION)}]|$)")


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

    `pr_comments_state.ThreadState` is a second vocabulary for where a thread
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

    The two reasons disagree about what a missing ``group-N.md`` means, so they
    stay distinct all the way to the executor. A recovery skip reuses a prior
    attempt's output, which must therefore be on disk — its absence is a real
    failure. A carried skip never had output: an incremental run takes that
    group's findings from the prior review's text, and a completed run sweeps
    its own group files, so the absence is the expected state.
    """

    RECOVERY = "recovery"
    CARRIED = "carried"


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
    pr_number: int | None = None
    head_sha: str = ""
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
    # Two timestamps because a review run has two moments worth recording and
    # they are not the same one. `started_at` is taken when the run begins;
    # `reviewed_at` is stamped only where a run reached its end with a review in
    # hand. Both are empty for a meta.json written before they existed — see
    # `ReviewEntry.reviewed_at` for what answers "when" in that case.
    started_at: str = ""
    reviewed_at: str = ""


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
_META_VOCABULARIES = {"review_type": ReviewType, "mode": Mode}


def review_meta_from_dict(d) -> ReviewMeta:
    """The attribution a `meta.json` payload states, keeping what it garbles out.

    `serde` does the reconstruction, so the fields on `ReviewMeta` are the whole
    schema and a key added there is read without a line here. What it will not
    do on its own is survive a vocabulary it does not know: an unrecognised
    `review_type` or `mode` raises out of `serde.from_dict` and would cost the
    whole record. Both are dropped to absent first, which is what `meta_enum`
    decides, so one unreadable field costs one field.

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
    return serde.from_dict(ReviewMeta, normalised)


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
    # The `FindingIdentity.stable_id` of the line this was parsed from, which a
    # posted comment carries so a later round can match a reply thread back to
    # the finding it hangs off. Set where the line is read rather than derived
    # from the fields beside it: `finding_spans` replaces `body` with the whole
    # span, and the identity hashes the declaration line's wording.
    stable_id: str = ""
    classification: str = ""
    skip_reason: str = ""
    checked: bool = False
    # Adjudicated, not outstanding: the finding was considered and rejected on
    # the merits. A skip is the fix pass saying "not me"; a decline is the
    # review saying "not at all", and the fix pass must not revisit it.
    declined: bool = False
    decline_reason: str = ""


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
class LedgerEntry:
    """One `## Prior findings` line: a prior finding, and what became of it."""

    ref: FindingRef
    disposition: PriorDisposition | None
    text: str

    def covers(self, ref: FindingRef) -> bool:
        """Whether this entry accounts for `ref`.

        An entry that names no path stands on its ID alone; one that names a
        path has to name the right one, or a single entry would account for
        every prior finding in its file.
        """
        if self.ref.finding_id != ref.finding_id:
            return False
        return not self.ref.path or self.ref.path == ref.path


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


# ── Finding location and span ────────────────────────────────────────────────


@dataclass(frozen=True)
class FindingLocation:
    """Where in the tree a finding line says its finding is.

    `line` and `end_line` are the range the location names — both absent when
    it names a file and no line of it.
    """

    path: str = ""
    line: int | None = None
    end_line: int | None = None

    @property
    def named(self) -> bool:
        """Whether the line named a location at all."""
        return bool(self.path)


class FindingScope(StrEnum):
    """What the heading above a finding declaration makes of it.

    `DECLARED` is a declaration under a severity heading — a finding the text
    reports as its own. `REPORTED` is one under a heading that names no
    severity: the `## Prior findings` ledger repeats the last review's findings
    there, and its IDs number that review rather than this one, so an edit that
    touched them would rewrite the record of a review it is not looking at.
    `UNHEADED` is a declaration with no heading above it at all, which is what
    a caller holding one severity's findings on their own hands in.
    """

    DECLARED = "declared"
    REPORTED = "reported"
    UNHEADED = "unheaded"


@dataclass(frozen=True)
class FindingSpan:
    """A finding declaration and the lines belonging to it.

    `line` is the declaration itself, stripped — what a caller with a narrower
    grammar than `FINDING_ID_RE` matches against to decide whether this is a
    finding it wants. `start` and `end` are line indices into the text the span
    was read from: the declaration's own line, and the line after the last one
    its body claims. `text_of` is the slice they name.

    The coordinates live here rather than on `Finding`, which the fix pass
    serializes to disk — a line number from one reading of one document is not
    something a stored finding should carry around.
    """

    finding: Finding
    line: str
    start: int
    end: int
    scope: FindingScope = FindingScope.UNHEADED

    @property
    def reported(self) -> bool:
        """Whether the span sits under a heading reporting on another review."""
        return self.scope is FindingScope.REPORTED

    def text_of(self, text: str) -> str:
        """The lines the span claims, verbatim.

        Trailing blank lines included, since a caller removing the span has to
        name every line the span owns or it leaves the gap behind.
        """
        return "\n".join(text.split("\n")[self.start:self.end])


# ── What is under review ─────────────────────────────────────────────────────

# One file's churn, as the prompt and the review header both list it.
FILE_STAT_FMT = "  - {path} (+{additions} -{deletions})"


@dataclass
class PRMetadata:
    title: str
    body: str
    head: str
    base: str
    head_sha: str
    additions: int
    deletions: int
    changed_files: int
    files: list[dict]
    is_draft: bool = False
    labels: list[str] = field(default_factory=list)
    author: str = ""

    @property
    def total_lines(self):
        return self.additions + self.deletions

    def file_stats(self, line_threshold: int):
        """The per-file churn breakdown, or "" for a PR small enough not to need it.

        The threshold is an argument rather than a module constant because
        ``EFFORT_PRESETS`` varies it by effort; re-deriving it here is what let
        the two owners disagree.
        """
        if self.total_lines <= line_threshold:
            return ""
        sorted_files = sorted(
            self.files, key=lambda f: f["additions"] + f["deletions"], reverse=True
        )
        return "\n".join(
            FILE_STAT_FMT.format(**f) for f in sorted_files
        )

    @property
    def all_files_formatted(self):
        return "\n".join(
            FILE_STAT_FMT.format(**f) for f in self.files
        )


@dataclass
class PRContext:
    commits: str = ""
    reviews: str = "[]"
    review_comments: str = "[]"
    comments: str = "[]"


@dataclass
class PreflightData:
    diff: str
    commit_log: str
    file_contents: dict[str, str]
    file_permissions: dict[str, str]
    claude_md: str
    architecture_md: str
    review_checklists: dict[str, str] = field(default_factory=dict)
    review_profiles: list = field(default_factory=list)
    omitted_files: list[str] = field(default_factory=list)
    delta_diff: str = ""
    delta_commit_log: str = ""
    delta_files: list[str] = field(default_factory=list)
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
    issue_link: str = ""
    issue_context: str = ""
    prior_review: str = ""
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
    reply_threads: ReplyThreads | None = None
    verification: dict | None = None
    pr_state_data: "PRState | None" = None
    viewer_role: str = ""
    throttle: "QuotaThrottle | None" = None
    # Taken when the job is built, not when the sidecar is written: every
    # branch that reaches a review file writes a sidecar, so a timestamp taken
    # there dates the deliverable rather than the run that produced it. This is
    # what `started_at` in meta.json carries.
    started_at: str = field(default_factory=now_iso)

    @functools.cached_property
    def config(self) -> workbench_config.WorkbenchConfig:
        """The merged workbench config for this job's worktree, read once.

        A review builds a ``PhaseRunner`` per phase and, in the group phase, one
        per group — every one of them resolving model, thinking and provider
        from the same two files. Cached on the job so the read happens once per
        review rather than once per runner.
        """
        return workbench_config.load_config_or_default(self.wt_path)

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
