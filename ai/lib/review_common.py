"""Shared constants and helpers for the claude-review system.

This module is the contract between review-orchestrate and review-post.
Both scripts import from here instead of defining their own constants. The
vocabulary they name those constants alongside — severities, modes, findings,
the job a run threads through — is `review_types`', so a consumer that only
needs a noun does not take the artifact layout with it.

Each review owns a directory under `~/.local/state/workbench/reviews/` —
`review.md` plus its session logs, group outputs, and pipeline state. The
directory is derived from the review file's path, and it is the only place
outside the worktree that review agents may write to. Granting the shared
reviews root instead is how agent scratch files ended up sitting beside
unrelated reviews.

Each directory carries a `meta.json` sidecar, and that is what a review is
attributed by — the repo, the PR number, the head and base refs. The directory
name is for a human reading `ls`; nothing decides what a review is *for* by
parsing it, so a lookup is never answered by a similarly-named directory that
belongs to another repo. `meta.json` also carries two timestamps, which answer
different questions: `started_at` is stamped when a run begins, `reviewed_at`
only when it finishes with a review in hand. Neither is backfilled — a review
written before they existed dates from its `review.md` mtime and reports no
start.

Everything that reads the tree — the two `pr gc` sweeps and every review lookup
— walks it through one shared iterator (`iter_review_entries`), which classifies
each entry at the root as a review, an orphaned directory, or a stray file. A
new consumer reads that walk rather than adding a fourth set of rules for what
counts as a review.
"""

# doc-group: findings

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import TypeVar

import ai_usage
import log
import workbench_paths
from agent_registry import PHASES, REVIEW_PHASES
from agent_types import Phase
from ai_usage import SessionUsage, parse_session_log
from pr_domains import ReviewVerdict
from pr_state import now_iso
from review_types import (
    SEVERITIES, SEVERITY_MUST, SEVERITY_SHOULD, Mode, ReviewMeta, ReviewType,
    review_meta_from_dict,
)


# ── Sections ─────────────────────────────────────────────────────────────────

SECTION_FILE_TRIAGE = "File Triage"
SECTION_STATIC_ANALYSIS = "Static Analysis"

# A re-review's ledger: one line per prior finding, saying whether the change
# resolved it. Reconciliation reads it to tell a finding the re-review dropped
# on purpose from one it lost track of; it is stripped before the review is
# posted, since its finding IDs number the prior review, not this one.
SECTION_PRIOR_FINDINGS = "Prior findings"


def plural(n: int) -> str:
    """Return the plural suffix for a count — `f"{total} finding{plural(total)}"`."""
    return "" if n == 1 else "s"


_EnumT = TypeVar("_EnumT", bound=StrEnum)


def enum_arg(enum_cls: type[_EnumT]) -> Callable[[str], _EnumT]:
    """An argparse ``type`` that converts to ``enum_cls`` by value.

    Passing the enum class directly as ``type=`` drops the valid-value list from
    the error message, because a failed conversion never reaches argparse's own
    ``choices`` check — so the message is reproduced here.
    """
    def parse(value: str) -> _EnumT:
        try:
            return enum_cls(value)
        except ValueError:
            choices = ", ".join(repr(str(m)) for m in enum_cls)
            raise argparse.ArgumentTypeError(
                f"invalid choice: {value!r} (choose from {choices})"
            ) from None

    return parse


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
FILENAME_PRIOR_FINDINGS = "prior-findings.json"

FILENAME_POST_SESSION = "post.jsonl"
REVIEW_EXT = ".md"

PIPELINE_MULTI = "multi"
PIPELINE_SINGLE = "single"


SEVERITY_COUNT_RE_FMT = r"^\s*- (\[ \] )?\*\*\[{}[0-9]+\]\*\*"


# ── Metadata format ──────────────────────────────────────────────────────────

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
    name = PHASES[phase].log_filename
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
    them, so the set is derived rather than hand-copied — a review phase added
    to the enum is found here for free. review.md is the deliverable and names
    no phase, so it is never matched.

    Only the review phases are swept: a phase belonging to another entry point
    writes into that entry point's own tracking directory, and asking it for an
    artifact name raises rather than minting one that would never match.
    """
    # Only GROUP's stem carries a "{}" placeholder (see PhaseSpec._stem); the
    # format call is a no-op for every other phase's plain filename.
    patterns = [
        name.format("*")
        for p in REVIEW_PHASES
        for name in (PHASES[p].output_filename, PHASES[p].log_filename)
        if name
    ]
    return [f for pat in patterns for f in review_dir.glob(pat) if f.is_file()]


def phase_output_path(review_file: str, phase: Phase, index: int | None = None) -> str:
    """Where ``phase`` writes its findings artifact for the review at ``review_file``.

    Raises for a phase that writes the review document itself. Unlike a
    missing log there is nothing to fall back to, and an empty name would
    derive to the review directory — a wrong path that reads as a real one.
    """
    name = PHASES[phase].output_filename
    if not name:
        raise ValueError(f"{phase} writes the review file, not an artifact of its own")
    if index is not None and "{}" not in name:
        raise ValueError(f"{phase} writes a single artifact — do not pass an index")
    if "{}" in name and index is None:
        raise ValueError(f"{phase} writes one artifact per index — pass an index")
    return _derive_path(review_file, name.format(index))


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
