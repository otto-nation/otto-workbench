"""The review subsystem's vocabulary: the nouns, with no behaviour around them.

A severity, a mode, a disposition, a finding, a review's attribution, and the
job a run threads through every phase. Everything here is a type or a constant
that names one — the modules above hold the code that parses, renders, merges
and runs.

The split is about fan-in. `Finding` had 14 consumers and lived beside evidence
verification and document surgery, so wanting the dataclass meant importing all
of it; `ReviewJob` lived beside git collection and budget fitting, so every
phase that takes a job took those too. Naming the vocabulary separately is what
lets a consumer depend on what a review *is* without depending on what the
review pipeline *does*.

Nothing in the review layer is imported here, and nothing should be: this is the
layer everything else in it sits on. The three heavier imports — `agent_types`,
`workbench_config` and `pr_state.now_iso` — are all below the review layer, and
`ReviewJob` is the only thing that reaches for them.
"""

# doc-group: findings

from __future__ import annotations

import functools
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import workbench_config
from agent_types import Effort
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


# ── Modes ────────────────────────────────────────────────────────────────────


class Mode(StrEnum):
    """What the review is reviewing: an open PR or the working branch."""

    PR = "pr"
    SELF = "self"


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
    repo: str = ""
    pr_number: int | None = None
    head_sha: str = ""
    head_ref: str = ""
    base_ref: str = ""
    # The sidecar carries both, and they answer different questions — how much of
    # the branch was reviewed, and what was being reviewed. Both are None for a
    # meta.json written before the field existed, or by a caller that wrote only
    # the repo; `mode` defaulting to PR would claim a fact the file never stated.
    review_type: ReviewType | None = None
    mode: Mode | None = None
    # Two timestamps because a review run has two moments worth recording and
    # they are not the same one. `started_at` is taken when the run begins;
    # `reviewed_at` is stamped only where a run reached its end with a review in
    # hand. Both are empty for a meta.json written before they existed — see
    # `ReviewEntry.reviewed_at` for what answers "when" in that case.
    started_at: str = ""
    reviewed_at: str = ""


def _meta_enum(enum_cls, value):
    """Read a fixed-vocabulary meta.json field, tolerating a value we don't know.

    meta.json is written by whatever version of the reviewer produced the review
    and read by whatever version is running now, so an unrecognised member reads
    as absent rather than taking the whole file down with it.
    """
    try:
        return enum_cls(value) if value else None
    except ValueError:
        return None


def review_meta_from_dict(d: dict) -> ReviewMeta:
    pr_number = d.get("pr_number")
    return ReviewMeta(
        repo=d.get("repo", ""),
        # Truthiness check intentionally treats "" and 0 as absent — no valid PR is #0
        pr_number=int(pr_number) if pr_number else None,
        head_sha=d.get("head_sha", ""),
        head_ref=d.get("head_ref", ""),
        base_ref=d.get("base_ref", ""),
        review_type=_meta_enum(ReviewType, d.get("review_type")),
        mode=_meta_enum(Mode, d.get("mode")),
        started_at=d.get("started_at") or "",
        reviewed_at=d.get("reviewed_at") or "",
    )


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
    classification: str = ""
    skip_reason: str = ""
    checked: bool = False
    # Adjudicated, not outstanding: the finding was considered and rejected on
    # the merits. A skip is the fix pass saying "not me"; a decline is the
    # review saying "not at all", and the fix pass must not revisit it.
    declined: bool = False
    decline_reason: str = ""


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
    include_generated: bool = False
    reply_threads: dict = field(default_factory=dict)
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
    def artifact_dir(self) -> str:
        """This review's own directory — every artifact is a sibling of the review file.

        Agents are granted write access to exactly this, never to the shared reviews
        root: a root grant is how scratch files ended up beside unrelated reviews.
        """
        return str(Path(self.review_file).parent)
