"""Shared constants, types, and helpers for the claude-review system.

This module is the contract between review-orchestrate and review-post.
Both scripts import from here instead of defining their own constants.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import TypeVar

import ai_usage
import log
import serde
import workbench_paths
from ai_usage import SessionUsage, parse_session_log
from pr_state import ReviewStatus, ReviewSummary, ReviewVerdict, now_iso


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


SECTION_FILE_TRIAGE = "File Triage"
SECTION_STATIC_ANALYSIS = "Static Analysis"

# A re-review's ledger: one line per prior finding, saying whether the change
# resolved it. Reconciliation reads it to tell a finding the re-review dropped
# on purpose from one it lost track of; it is stripped before the review is
# posted, since its finding IDs number the prior review, not this one.
SECTION_PRIOR_FINDINGS = "Prior findings"


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

        The verdict has to stand on its own — the whole text, or ahead of a
        dash, colon or parenthesis. A qualified one ("Fixed, but only on the
        happy path") is left unparsed rather than read as its optimistic half.
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
NO_DISPOSITION_PRECEDENCE = 0


def disposition_precedence(disposition: "PriorDisposition | None") -> int:
    """`precedence`, tolerating the unparsed entry a ledger may also carry."""
    return NO_DISPOSITION_PRECEDENCE if disposition is None else disposition.precedence


# What may follow a disposition without qualifying it: nothing, or a break that
# introduces detail rather than a caveat.
_DISPOSITION_TAIL_RE = re.compile(r"^\s*(?:[—–:(-]|$)")


def plural(n: int) -> str:
    """Return the plural suffix for a count — `f"{total} finding{plural(total)}"`."""
    return "" if n == 1 else "s"


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


# ── Phases ───────────────────────────────────────────────────────────────────


REVIEW_ENV_PREFIX = "CLAUDE_REVIEW_"


class Phase(StrEnum):
    """A stage of the review pipeline.

    Override env keys are derived from the member name, so adding a phase means
    one member here plus one ``PHASES`` entry — callers, preflight checks, and
    failure hints all read the derived keys rather than spelling them out.
    """

    SINGLE = "single"
    HOLISTIC = "holistic"
    SCOUT = "scout"
    GROUP = "group"
    SYNTHESIS = "synthesis"
    DISPROVE = "disprove"
    FIX = "fix"

    @property
    def model_env_key(self) -> str:
        return f"{REVIEW_ENV_PREFIX}{self.upper()}_MODEL"

    @property
    def thinking_env_key(self) -> str:
        return f"{REVIEW_ENV_PREFIX}{self.upper()}_THINKING"

    @property
    def _stem(self) -> str:
        """The filename stem this phase's artifacts share: the phase's own name.

        ``group`` is the one fan-out phase, so its stem carries the index.
        """
        return f"{self}-{{}}" if self is Phase.GROUP else str(self)

    @property
    def log_filename(self) -> str:
        """The session log this phase writes, named after the phase.

        ``single`` names no file of its own: it writes to the job's session
        log, which ``review-orchestrate --session-log`` may point outside the
        review directory.
        """
        return "" if self is Phase.SINGLE else f"{self._stem}.jsonl"

    @property
    def output_filename(self) -> str:
        """The findings artifact this phase writes, named after the phase.

        Empty for a phase that writes into the review document rather than an
        artifact of its own: ``single`` and ``synthesis`` produce ``review.md``
        and ``fix`` edits it in place.
        """
        return "" if self in _WRITES_REVIEW_FILE else f"{self._stem}.md"


# The phases whose output is the review document itself. Lives below the class
# because it names members; read at call time, so the forward reference in
# `output_filename` resolves.
_WRITES_REVIEW_FILE = frozenset({Phase.SINGLE, Phase.SYNTHESIS, Phase.FIX})


# ── Agent tuning ─────────────────────────────────────────────────────────────


class Effort(StrEnum):
    """Review depth. Selects a preset of budgets, thresholds, and phase skips."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Thinking(StrEnum):
    """Extended-thinking level passed through to the backend."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AgentKind(StrEnum):
    """Which reviewer agent definition a phase runs under.

    ``REVIEWER_LITE`` skips context gathering, so it only suits phases that are
    handed everything they need up front.

    Every member here is a review persona forbidden from editing the workspace.
    Phases that write to the branch pass ``None`` instead of an ``AgentKind`` —
    see ``run_fix_pass``.
    """

    REVIEWER = "reviewer"
    REVIEWER_LITE = "reviewer-lite"


DEFAULT_MAX_BUDGET_PER_AGENT = 5.0


@dataclass(frozen=True)
class EffortPreset:
    """Budgets, thresholds, and phase skips selected by ``--effort``.

    ``thinking=None`` means the phase's own default stands; a level here
    flattens every phase to it, matching what CLAUDE_REVIEW_THINKING does.
    """

    thinking: Thinking | None
    agent_budget: float
    max_groups: int
    multi_phase_line_threshold: int
    multi_phase_file_threshold: int
    skip_synthesis: bool
    skip_holistic: bool
    skip_scout: bool
    skip_disprove: bool
    skip_omitted_files: bool
    agent: AgentKind


# Lives here rather than beside the pipeline that reads it most: every layer
# down to prompt building needs a threshold from it, and the pipeline imports
# those layers, so owning it there would make the lookup a circular import.
EFFORT_PRESETS: dict[Effort, EffortPreset] = {
    Effort.LOW: EffortPreset(
        thinking=Thinking.LOW,
        agent_budget=3.0,
        max_groups=6,
        multi_phase_line_threshold=1000,
        multi_phase_file_threshold=15,
        skip_synthesis=True,
        skip_holistic=True,
        skip_scout=True,
        skip_disprove=True,
        skip_omitted_files=True,
        agent=AgentKind.REVIEWER_LITE,
    ),
    Effort.MEDIUM: EffortPreset(
        thinking=None,
        agent_budget=DEFAULT_MAX_BUDGET_PER_AGENT,
        max_groups=8,
        multi_phase_line_threshold=500,
        multi_phase_file_threshold=10,
        skip_synthesis=False,
        skip_holistic=False,
        skip_scout=False,
        skip_disprove=False,
        skip_omitted_files=False,
        agent=AgentKind.REVIEWER,
    ),
    Effort.HIGH: EffortPreset(
        thinking=Thinking.HIGH,
        agent_budget=8.0,
        max_groups=16,
        multi_phase_line_threshold=500,
        multi_phase_file_threshold=10,
        skip_synthesis=False,
        skip_holistic=False,
        skip_scout=False,
        skip_disprove=False,
        skip_omitted_files=False,
        agent=AgentKind.REVIEWER,
    ),
}


EnumT = TypeVar("EnumT", bound=StrEnum)


def enum_arg(enum_cls: type[EnumT]) -> Callable[[str], EnumT]:
    """An argparse ``type`` that converts to ``enum_cls`` by value.

    Passing the enum class directly as ``type=`` drops the valid-value list from
    the error message, because a failed conversion never reaches argparse's own
    ``choices`` check — so the message is reproduced here.
    """
    def parse(value: str) -> EnumT:
        try:
            return enum_cls(value)
        except ValueError:
            choices = ", ".join(repr(str(m)) for m in enum_cls)
            raise argparse.ArgumentTypeError(
                f"invalid choice: {value!r} (choose from {choices})"
            ) from None

    return parse


# ── Failure diagnosis ────────────────────────────────────────────────────────


class DiagnosisKind(StrEnum):
    """Why an agent run left no output.

    Retry policy switches on this, so a member is added when a *decision* needs
    to tell one outcome from another — not when a message needs new wording.
    """

    MAX_TURNS = "max_turns"
    COMPLETED = "completed"
    AGENT_ERROR = "agent_error"
    TRANSIENT = "transient"
    QUOTA_EXHAUSTED = "quota_exhausted"
    NO_SESSION_LOG = "no_session_log"
    NO_RESULT_RECORD = "no_result_record"
    # The three below are the pipeline's own verdicts, reached without ever
    # reading a session log: a group the pipeline declined to run, one abandoned
    # when the budget ran out, and one whose output vanished between passes.
    SKIPPED = "skipped"
    BUDGET_EXCEEDED = "budget_exceeded"
    OUTPUT_MISSING = "output_missing"
    # Synthesis's own outcomes. Recorded against the pipeline rather than an
    # agent, so neither has a session log behind it: no group produced usable
    # output, and a synthesis that degraded to the mechanical merge.
    ALL_GROUPS_FAILED = "all_groups_failed"
    MECHANICAL_FALLBACK = "mechanical_fallback"
    # Only reachable by reading a pipeline state file written before failures
    # were structured. `detail` holds that file's rendered message verbatim.
    UNKNOWN = "unknown"


# Prefixes every backend crash. Load-bearing beyond rendering: the no-write
# check and the transient-error check both use it to tell a crash apart from a
# run that ended on its own terms.
AGENT_ERROR_PREFIX = "agent error:"

# A backend error whose text matches one of these will fail again the same way,
# so no amount of retrying or recovery helps. Matched against `Diagnosis.detail`
# the way `_TRANSIENT_ERROR_MARKERS` is — the error text is free-form, and these
# are the fragments of it that carry a verdict.
NON_RECOVERABLE_ERROR_MARKERS = ("permission denied",)

_DIAGNOSIS_MESSAGES = {
    DiagnosisKind.QUOTA_EXHAUSTED: "quota exhausted (429)",
    DiagnosisKind.NO_SESSION_LOG: "no session log found",
    DiagnosisKind.NO_RESULT_RECORD: "no result record in session log",
    DiagnosisKind.BUDGET_EXCEEDED: "budget exceeded",
    DiagnosisKind.OUTPUT_MISSING: "output missing",
    DiagnosisKind.ALL_GROUPS_FAILED: "all groups failed",
    DiagnosisKind.MECHANICAL_FALLBACK: "mechanical fallback",
}

# Every constant message, reversed. A state file written before a message was a
# kind holds the rendered text; this reads it back as the kind it renders as,
# rather than burying it in `UNKNOWN`. Derived, so a new message is covered
# without a second edit. Kinds whose message interpolates `detail` are absent
# from the forward map and so stay verbatim under `UNKNOWN`, as before.
_MESSAGE_KINDS = {message: kind for kind, message in _DIAGNOSIS_MESSAGES.items()}

NO_WRITE_TOOL_SUFFIX = "never called a file-writing tool"


@dataclass(frozen=True)
class Diagnosis:
    """A single agent run's failure, classified once and rendered on demand.

    Frozen because two of the pipeline's decisions compare diagnoses for
    equality — the consecutive-failure abort and the all-groups-failed circuit
    breaker — and one of them puts them in a set.
    """

    kind: DiagnosisKind
    no_write_tool: bool = False
    detail: str = ""
    # None when the backend reported no turn count; rendered as "?".
    num_turns: int | None = None

    @property
    def message(self) -> str:
        """The human-readable reason, as it appears in logs and review files."""
        return self._base_message() + (
            f" — {NO_WRITE_TOOL_SUFFIX}" if self.no_write_tool else ""
        )

    def _base_message(self) -> str:
        if self.kind is DiagnosisKind.MAX_TURNS:
            turns = self.num_turns if self.num_turns is not None else "?"
            return f"agent hit max turns ({turns})"
        if self.kind is DiagnosisKind.COMPLETED:
            return f"agent completed (subtype={self.detail}) but did not write output"
        if self.kind in (DiagnosisKind.AGENT_ERROR, DiagnosisKind.TRANSIENT):
            return f"{AGENT_ERROR_PREFIX} {self.detail}"
        if self.kind is DiagnosisKind.SKIPPED:
            return f"skipped: {self.detail}"
        if self.kind is DiagnosisKind.UNKNOWN:
            # A legacy state file could hold an empty reason; the failures table
            # gets a word rather than a blank cell.
            return self.detail or DiagnosisKind.UNKNOWN.value
        return _DIAGNOSIS_MESSAGES[self.kind]

    @property
    def recoverable(self) -> bool:
        """Whether `pr review --recover` could plausibly do better than this run."""
        lowered = self.detail.lower()
        return not any(m in lowered for m in NON_RECOVERABLE_ERROR_MARKERS)

    @classmethod
    def _from_raw(cls, raw) -> "Diagnosis | None":
        """Rebuild a diagnosis from any shape a state file can hold.

        `serde` hands the whole field over here rather than assuming a dict,
        because reviews live in `~/.local/state/workbench/reviews/` and outlive the
        code that wrote them. A file written before diagnoses were typed holds
        the rendered reason — recover the kind where the text names one, and
        keep the rest verbatim under `UNKNOWN`, so a `--recover` run against an
        in-flight review still renders its failures.

        Returns None for a raw value that records no failure at all: an optional
        field written before it was optional holds `""`, and reading that as a
        blank diagnosis would turn a clean run into a failed one.
        """
        if not raw:
            return None
        if isinstance(raw, cls):
            return raw
        if isinstance(raw, dict):
            return serde.from_dict(cls, raw)
        text = str(raw)
        if text in _MESSAGE_KINDS:
            return cls(_MESSAGE_KINDS[text])
        return cls(DiagnosisKind.UNKNOWN, detail=text)

    @classmethod
    def _raw_schema(cls, object_schema: dict) -> dict:
        """Both shapes `_from_raw` reads, for anything that publishes a schema
        over a diagnosis. The bare string is the pre-typed form a review file
        written by an older run still holds, and a schema naming only the
        object would call that file invalid where the reader accepts it."""
        return {"oneOf": [object_schema, {"type": "string"}]}


# ── Templates ────────────────────────────────────────────────────────────────

TEMPLATE_SINGLE = "single-agent.md"
TEMPLATE_HOLISTIC = "holistic.md"
TEMPLATE_GROUP = "group.md"
TEMPLATE_SYNTHESIS = "synthesis.md"
TEMPLATE_SELF_REVIEW = "self-review.md"
TEMPLATE_SELF_SYNTHESIS = "self-review-synthesis.md"
TEMPLATE_SCOUT = "scout.md"
TEMPLATE_DISPROVE = "disprove.md"
TEMPLATE_FIX = "fix-findings.md"
TEMPLATE_FIX_COMMENTS = "fix-comments.md"
TEMPLATE_FIX_CI = "fix-ci.md"

TEMPLATE_DIR_REL = Path("lib") / "review-templates"


# ── Shared prompt blocks ─────────────────────────────────────────────────────
#
# Owned here rather than hand-copied into each template: every template that
# writes an output file or works in a worktree renders the same block.

def build_output_block(output_path: str, *, stdout_warning: bool = False) -> str:
    """How an agent saves its output file.

    Agents run under `claude --bare`, which exposes only Bash, Edit and Read —
    there is no Write tool. The pipeline pre-creates the output file empty, so
    an Edit with an empty old_string inserts the whole document in one call.
    """
    stdout_line = (
        "\nDo NOT print the output to stdout — it only counts if it lands in the file."
        if stdout_warning else ""
    )
    return (
        f"Write your output to: {output_path}\n"
        "The file already exists and is empty — Read it, then use the Edit tool "
        "with an empty `old_string` to insert the complete contents. That Read "
        "plus one Edit is the entire write; do not build the file up in pieces.\n"
        "The Write tool is NOT available in this environment — do not attempt it, "
        "and do not fall back to Bash (`cat`, heredoc, python). Do NOT create "
        f"directories or empty files.{stdout_line}"
    )


def build_worktree_block(wt_path: str) -> str:
    """Where the branch is checked out and how to address it.

    Like `build_output_block`, this is the body only — the template owns the
    `## Worktree` heading above the slot.
    """
    return (
        f"Branch checked out at: {wt_path}\n"
        "\n"
        "All file reads and git commands MUST use this path directly "
        f'(e.g. `git -C "{wt_path}" diff`).\n'
        "Never use command substitution `$(...)` to discover the worktree path — "
        "it triggers permission prompts."
    )


# ── Filenames ────────────────────────────────────────────────────────────────

FILENAME_PRIOR = "prior.md"
FILENAME_SESSION = "session.jsonl"
FILENAME_META = "meta.json"
FILENAME_PIPELINE_STATE = "pipeline.json"
FILENAME_PROMPT_STATS = "prompt-stats.json"

FILENAME_POST_SESSION = "post.jsonl"
REVIEW_EXT = ".md"

PIPELINE_MULTI = "multi"
PIPELINE_SINGLE = "single"


SEVERITY_COUNT_RE_FMT = r"^\s*- (\[ \] )?\*\*\[{}[0-9]+\]\*\*"


# ── Metadata format ──────────────────────────────────────────────────────────

FILE_STAT_FMT = "  - {path} (+{additions} -{deletions})"
META_DATE = "<!-- date: {today} -->"
META_HEAD_SHA = "<!-- head_sha: {head_sha} -->"
META_REVIEW_TYPE = "<!-- review_type: {review_type} -->"
META_PRIOR_SHA = "<!-- prior_sha: {prior_sha} -->"
META_PRIOR_DATE = "<!-- prior_date: {prior_date} -->"
META_DELTA_FILES = "<!-- delta_files: {delta_file_count} -->"
META_SKIPPED_GROUPS = "<!-- skipped_groups: {skipped}/{total} -->"
META_GENERATOR = "<!-- generator: {generator_version} -->"
META_STATUS = "<!-- status: {status} -->"

PRIOR_SHA_RE = re.compile(r"<!-- head_sha: ([a-f0-9]+) -->")
PRIOR_DATE_RE = re.compile(r"<!-- date: (\d{4}-\d{2}-\d{2}) -->")


# ── Path helpers ─────────────────────────────────────────────────────────────

def _derive_path(review_file: str, filename: str) -> str:
    return str(Path(review_file).parent / filename)


def phase_log_path(review_file: str, phase: Phase, index: int | None = None) -> str:
    """Where ``phase`` writes its session log for the review at ``review_file``.

    Empty for a phase that names no log of its own — the caller falls back to
    the job's.
    """
    name = phase.log_filename
    if index is not None and "{}" not in name:
        raise ValueError(f"{phase} writes a single log — do not pass an index")
    if not name:
        return ""
    if "{}" in name and index is None:
        raise ValueError(f"{phase} writes one log per index — pass an index")
    return _derive_path(review_file, name.format(index))


def phase_artifacts(review_dir: Path) -> list[Path]:
    """Every phase artifact and session log present in *review_dir*.

    Both an artifact and a session log are named after the phase that wrote
    them, so the set is derived rather than hand-copied — a phase added to the
    enum is found here for free. review.md is the deliverable and names no
    phase, so it is never matched.
    """
    # Only GROUP's stem carries a "{}" placeholder (see Phase._stem); the
    # format call is a no-op for every other phase's plain filename.
    patterns = [
        name.format("*")
        for p in Phase
        for name in (p.output_filename, p.log_filename)
        if name
    ]
    return [f for pat in patterns for f in review_dir.glob(pat) if f.is_file()]


def phase_output_path(review_file: str, phase: Phase, index: int | None = None) -> str:
    """Where ``phase`` writes its findings artifact for the review at ``review_file``.

    Raises for a phase that writes the review document itself. Unlike a
    missing log there is nothing to fall back to, and an empty name would
    derive to the review directory — a wrong path that reads as a real one.
    """
    name = phase.output_filename
    if not name:
        raise ValueError(f"{phase} writes the review file, not an artifact of its own")
    if index is not None and "{}" not in name:
        raise ValueError(f"{phase} writes a single artifact — do not pass an index")
    if "{}" in name and index is None:
        raise ValueError(f"{phase} writes one artifact per index — pass an index")
    return _derive_path(review_file, name.format(index))


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


# ── Log preservation for retries ─────────────────────────────────────────────


def preserve_log(path: str) -> str:
    """Read session log content before a retry that will overwrite it."""
    try:
        return Path(path).read_text()
    except OSError:
        return ""


def restore_preserved(path: str, prior: str) -> None:
    """Prepend prior log content so both attempts' result records are preserved."""
    if not prior:
        return
    try:
        current = Path(path).read_text()
    except OSError:
        current = ""
    Path(path).write_text(prior + current)


# ── Subprocess ───────────────────────────────────────────────────────────────

def _run(cmd: list[str], check: bool = True, cwd: str | None = None) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if check and r.returncode != 0:
        return ""
    return r.stdout.strip()


def has_uncommitted_changes(wt_path: str | Path) -> bool:
    """Whether a worktree has unstaged, staged, or untracked changes.

    Porcelain rather than `diff --quiet`: a fix agent that only adds files
    (tests, fixtures) leaves the tracked diff empty, and a diff-only gate
    skips the commit while the caller still reports the finding as fixed.
    """
    r = subprocess.run(
        ["git", "-C", str(wt_path), "status", "--porcelain"],
        capture_output=True, text=True,
    )
    return bool(r.stdout.strip())


# ── Review file helpers ─────────────────────────────────────────────────────


def review_file_path(repo: str, pr_number: str) -> Path:
    """Return the expected path for a review file given repo and PR number."""
    repo_name = repo.split("/")[-1]
    return workbench_paths.reviews_dir() / f"{repo_name}-{pr_number}" / f"review{REVIEW_EXT}"


def read_review_meta(review_dir: Path) -> ReviewMeta:
    """Read meta.json from a review directory."""
    meta_file = review_dir / FILENAME_META
    if not meta_file.is_file():
        return ReviewMeta()
    try:
        return review_meta_from_dict(json.loads(meta_file.read_text()))
    except (json.JSONDecodeError, OSError):
        return ReviewMeta()


def stamp_reviewed(review_dir: Path) -> None:
    """Record in the sidecar that this review's run reached its end.

    Kept apart from the sidecar write itself because the two answer different
    questions: the sidecar is written wherever a run learns what it is
    reviewing, so anything stamped there means "started". This is the only
    place that means "reviewed", so there is one answer to when a review was
    produced rather than one per pipeline branch.

    Nothing is created here. A run that never wrote a sidecar produced no
    review to date, and a meta.json we cannot read is not one to overwrite —
    the fields already in it are worth more than this timestamp.
    """
    meta_file = review_dir / FILENAME_META
    try:
        meta = json.loads(meta_file.read_text())
    except (json.JSONDecodeError, OSError):
        return
    if not isinstance(meta, dict):
        return
    meta["reviewed_at"] = now_iso()
    try:
        meta_file.write_text(json.dumps(meta))
    except OSError as exc:
        # Warned rather than raised: the review is already written and this
        # runs at the very end of a run that worked. Losing the stamp costs a
        # reader the mtime fallback; failing here would cost the whole review.
        log.warn(f"could not stamp {meta_file} ({exc}) — its age will read from the file's mtime")


# ── Walking the reviews tree ─────────────────────────────────────────────────


class ReviewEntryKind(StrEnum):
    """What one entry at the reviews root turned out to be.

    The three kinds are what the callers of the walk already distinguish: gc
    collects strays and orphans on different rules, and everything that looks
    up a review wants the entries that actually hold one.
    """

    # A directory holding a review.md — a review someone can read.
    REVIEW = "review"
    # A directory with no review.md: a run in flight, or one that never
    # produced its deliverable.
    ORPHAN = "orphan"
    # A loose file at the reviews root. Reviews live in directories, so this is
    # either an agent's scratch file or a leftover of the flat layout.
    STRAY = "stray"


@dataclass(frozen=True)
class ReviewEntry:
    """One entry at the reviews root, classified and attributed.

    Attribution is `meta.json`'s, never the directory name's. The name is a
    convenience for a human reading `ls`, and it is chosen from the repo's short
    name, so two repos sharing one — `acme/widget` and `other/widget` — are
    indistinguishable by name. Only the sidecar says what a review is for.
    """

    path: Path
    kind: ReviewEntryKind
    meta: ReviewMeta = ReviewMeta()

    @property
    def review_file(self) -> Path:
        """Where this entry's deliverable is, whether or not it was written.

        A stray is a loose file rather than a directory, so it has nowhere to
        hold a deliverable. Asking is a caller that mixed the kinds up, and
        `check_hunks.py/review.md` is a worse answer than a raise — it is a path
        that never exists, which reads downstream as a review not yet written.
        """
        if self.kind is ReviewEntryKind.STRAY:
            raise ValueError(f"a stray file holds no review: {self.path}")
        return self.path / f"review{REVIEW_EXT}"

    def is_for(self, repo: str, pr_number: str | int) -> bool:
        """Whether meta.json attributes this entry to *repo*'s PR *pr_number*."""
        if not self.meta.repo or self.meta.pr_number is None:
            return False
        return self.meta.repo == repo and str(self.meta.pr_number) == str(pr_number)

    @property
    def reviewed_at(self) -> str:
        """When this review was produced, as an ISO timestamp, or "" if unknowable.

        meta.json is authoritative because it survives a copy, an rsync, and a
        backup restore. A review written before the field existed has only its
        deliverable's mtime left to date it — filesystem state, which every one
        of those rewrites, but the only record there is.

        Raises for a stray, for the reason `review_file` does.
        """
        if self.meta.reviewed_at:
            return self.meta.reviewed_at
        try:
            mtime = self.review_file.stat().st_mtime
        except OSError:
            return ""
        return datetime.fromtimestamp(mtime, timezone.utc).isoformat()


def iter_review_entries(reviews_dir: Path | None = None) -> Iterator[ReviewEntry]:
    """Every entry at the reviews root, walked once and classified once.

    The one walk of the tree: gc, the prune, and every review lookup read it,
    so what counts as a review and what a review is for are decided here rather
    than re-derived per call site with rules that drift apart.

    The listing is taken eagerly, so a caller may delete what it is handed
    without disturbing the iteration. A root that does not exist yields
    nothing — a machine that has never run a review is not an error.
    """
    root = workbench_paths.reviews_dir() if reviews_dir is None else reviews_dir
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return
    for entry in entries:
        if entry.is_dir():
            kind = (
                ReviewEntryKind.REVIEW
                if (entry / f"review{REVIEW_EXT}").is_file()
                else ReviewEntryKind.ORPHAN
            )
            # ceiling: meta.json is read for every directory, including by a
            # caller that only wants strays. A reviews root holds tens of
            # directories and each read is a few hundred bytes, which is
            # cheaper than the machinery to defer it. Upgrade trigger: if a
            # sweep over this root ever shows up in a profile, make `meta` a
            # lazily-read cached property.
            yield ReviewEntry(entry, kind, read_review_meta(entry))
        elif entry.is_file():
            yield ReviewEntry(entry, ReviewEntryKind.STRAY)


def find_review_file(repo: str, pr_number: str) -> Path | None:
    """Find a review file by repo and PR, checking canonical path then scanning meta.

    Both steps answer with meta.json. The scan used to pre-filter directories on
    `name.startswith(repo_name)`, which made the name part of the matching rule:
    a review correctly attributed to this repo went unfound because its
    directory was named for something else.
    """
    canonical = review_file_path(repo, pr_number)
    # The canonical name is derived from the repo's short name, so `acme/widget`
    # and `other/widget` derive the same one. Take it unless the sidecar
    # positively attributes it elsewhere — a review carrying no attribution at
    # all predates the field and still resolves the way it always did.
    if canonical.is_file() and read_review_meta(canonical.parent).repo in ("", repo):
        return canonical
    for entry in iter_review_entries():
        if entry.kind is ReviewEntryKind.REVIEW and entry.is_for(repo, pr_number):
            return entry.review_file
    return None


def count_severities(file: Path | None) -> dict[str, int]:
    """Count findings of every severity, keyed by severity key.

    Counts all four in one read: every caller wants more than one of them, and
    a per-severity helper re-read the file once per count. Always returns a
    complete dict, zeroed when the file is missing or unreadable.
    """
    zeroed = {s.key: 0 for s in SEVERITIES}
    if not file or not file.is_file():
        return zeroed
    try:
        text = file.read_text()
    except OSError:
        return zeroed
    return {
        s.key: len(re.findall(
            SEVERITY_COUNT_RE_FMT.format(re.escape(s.key)), text, re.MULTILINE,
        ))
        for s in SEVERITIES
    }


def aggregate_session_usage(review_dir: Path | None) -> SessionUsage:
    """Aggregate usage from session and post-session logs."""
    if not review_dir:
        return SessionUsage()
    return ai_usage.merge([
        parse_session_log(str(review_dir / n))
        for n in (FILENAME_SESSION, FILENAME_POST_SESSION)
        if (review_dir / n).is_file()
    ])


def _load_pipeline_state(review_dir: Path | None) -> "PipelineState | None":
    # Local import: review_preflight imports this module, and PipelineState is
    # the one thing that knows how to read its own file.
    from review_preflight import PipelineState
    return PipelineState.load(review_dir)


def read_pipeline_status(review_dir: Path | None) -> str:
    """Derive review status from pipeline state.

    complete — all phases succeeded, no failures
    partial  — review produced but with failures (groups or synthesis fallback)
    error    — all groups failed, no usable output
    """
    state = _load_pipeline_state(review_dir)
    if state is None:
        return ReviewStatus.COMPLETED.value
    return state.status.value


def read_pipeline_warnings(review_dir: Path | None) -> list[str]:
    """Return human-readable warnings for incomplete pipeline phases."""
    state = _load_pipeline_state(review_dir)
    if state is None:
        return []
    return state.warnings


def build_failure_detail(review_dir: Path | None) -> str:
    """Build a human-readable failure detail string from pipeline state."""
    state = _load_pipeline_state(review_dir)
    if state is None:
        return ""
    if not state.groups_failed and not state.synthesis_failed:
        return ""

    parts = []
    if state.groups_failed:
        reasons = ", ".join(sorted({d.message for d in state.groups_failed.values()}))
        if state.all_groups_failed:
            parts.append(f"all groups failed: {reasons}")
        else:
            n_failed, n_total = len(state.groups_failed), state.group_count
            parts.append(f"{n_failed}/{n_total} groups failed: {reasons}")

    # ALL_GROUPS_FAILED restates what the groups line already said.
    if state.synthesis_failed and state.synthesis_failed.kind is not DiagnosisKind.ALL_GROUPS_FAILED:
        parts.append(f"synthesis: {state.synthesis_failed.message}")

    return "; ".join(parts)


def parse_review_verdict(review_path: Path | None) -> ReviewVerdict | None:
    """The verdict a review's `## Verdict` section states, if it states one."""
    if not review_path or not review_path.is_file():
        return None
    try:
        text = review_path.read_text()
    except OSError:
        return None
    in_verdict = False
    for line in text.splitlines():
        if line.strip().lower().startswith("## verdict"):
            in_verdict = True
            continue
        if not in_verdict:
            continue
        stripped = line.strip()
        if not stripped:
            continue
        return ReviewVerdict.stated_in(stripped)
    return None


def resolve_review_verdict(
    review_path: Path | None,
    *,
    counts: dict[str, int] | None = None,
    self_review: bool = False,
) -> ReviewVerdict | None:
    """The verdict to record and report for a finished review.

    The prose the synthesis agent wrote and the findings that survived
    verification are two readings of the same review, and this is the only
    place they are reconciled: the stronger call wins, so the prose can never
    under-report findings that block, and the counts can never quietly discard
    a stronger call the agent made. Disapprove is unranked and always stands —
    no count implies it and none refutes it.

    Pass `counts` from `count_severities` when the caller already has them, to
    save re-reading the review file.
    """
    if not review_path or not review_path.is_file():
        return None
    stated = parse_review_verdict(review_path)
    if stated is ReviewVerdict.DISAPPROVE:
        return stated
    # A self-review is advisory — it has no PR to approve or block. Disapprove
    # is the exception above: it judges the approach, which holds without a PR.
    if self_review:
        return None
    if counts is None:
        counts = count_severities(review_path)
    derived = ReviewVerdict.from_counts(
        counts.get(SEVERITY_MUST, 0), counts.get(SEVERITY_SHOULD, 0),
    )
    return stated if stated and stated.outranks(derived) else derived


def build_review_summary(repo: str, pr_number: str, review_file: str) -> dict:
    """Build a review summary dict for a review."""
    review_path = Path(review_file) if review_file else None
    by_key = count_severities(review_path)
    counts = {s.json_key: by_key[s.key] for s in SEVERITIES}
    total = sum(by_key.values())

    review_dir = Path(review_file).parent if review_file else None
    meta = read_review_meta(review_dir) if review_dir else ReviewMeta()

    resolved = resolve_review_verdict(
        review_path, counts=by_key, self_review=meta.mode is Mode.SELF,
    )
    verdict = resolved.value if resolved else ""

    usage = aggregate_session_usage(review_dir)

    review_content = None
    if review_path and review_path.is_file():
        try:
            review_content = review_path.read_text()
        except OSError:
            pass

    status = read_pipeline_status(review_dir)
    failure_detail = build_failure_detail(review_dir)

    return {
        "repo": repo,
        "pr_number": int(pr_number) if pr_number else None,
        "head_sha": meta.head_sha or None,
        "head_ref": meta.head_ref or None,
        "base_ref": meta.base_ref or None,
        "review_type": meta.review_type,
        "review_file": review_file,
        "review_content": review_content,
        "findings": {**counts, "total": total},
        "verdict": verdict,
        "status": status,
        "failure_detail": failure_detail,
        "cost_usd": usage.cost,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_read_tokens": usage.cache_read_tokens,
        "cache_write_tokens": usage.cache_write_tokens,
        "duration_ms": usage.duration_ms,
    }


def json_summary(repo: str, pr_number: str, review_file: str) -> str:
    """Build a REVIEW_SUMMARY:{json} string for a review."""
    data = build_review_summary(repo, pr_number, review_file)
    return f"REVIEW_SUMMARY:{json.dumps(data)}"


# ── Status rendering ─────────────────────────────────────────────────────


def _verdict_display(verdict: str) -> str:
    try:
        return ReviewVerdict(verdict).prose
    except ValueError:
        return verdict


def render_status(rev: ReviewSummary) -> list[str]:
    """Render review state as status lines for the pr dashboard."""
    if not rev.updated_at:
        return ["**Review**: not run yet"]
    suffixes = []
    if rev.status == ReviewStatus.PARTIAL.value:
        detail = f" — {rev.failure_detail}" if rev.failure_detail else ""
        suffixes.append(f"[PARTIAL{detail}]")
    elif rev.status == ReviewStatus.ERROR.value:
        detail = f" — {rev.failure_detail}" if rev.failure_detail else ""
        suffixes.append(f"[ERROR{detail}]")
    if rev.verdict == ReviewVerdict.DISAPPROVE.value:
        suffixes.append("[DISAPPROVED]")
    suffix = " " + " ".join(suffixes) if suffixes else ""
    # The dashboard shows the verdict the way the review states it, not the way
    # state serializes it. An unrecognised value is shown as stored rather than
    # dropped, so state written by an older version still reads.
    verdict_part = f": {_verdict_display(rev.verdict)}" if rev.verdict else ""
    lines = [f"**Review** ({rev.review_type}){verdict_part}{suffix}"]
    if rev.finding_counts:
        parts = [f"{sev}: {count}" for sev, count in sorted(rev.finding_counts.items())]
        lines.append(f"  findings: {', '.join(parts)}")
    if rev.cost_usd:
        lines.append(f"  cost: ${rev.cost_usd:.2f}")
    if rev.status in (ReviewStatus.PARTIAL.value, ReviewStatus.ERROR.value):
        lines.append("  recover: pr review --recover")
    return lines
