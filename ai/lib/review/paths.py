"""Where a review lives on disk, and what is allowed to be there.

Each review owns a directory under `~/.local/state/workbench/reviews/` —
`review.md` plus its session logs, group outputs, and pipeline state. The
directory is derived from the review file's path, and it is the only place
outside the worktree that review agents may write to. Granting the shared
reviews root instead is how agent scratch files ended up sitting beside
unrelated reviews.

Every artifact in that directory is named here rather than by its writer. A
phase's log and output are derived from `PhaseSpec`, so a phase added to the
enum is found by the sweeps for free; everything else is a `FILENAME_*`
constant, so the reader and the writer of a file cannot disagree about its
name.

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

A re-review rotates what it is replacing into an `archives/` subdirectory and
keeps the last few, so the directory a run finds is the one its predecessor
left rather than an unbounded pile. That retention is layout too, which is why
it is decided here and not by the entry point that triggers it.
"""

# doc-group: pipeline

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path

from agent import usage as ai_usage
from core import log
from core import serde
from core import workbench_paths
from agent.registry import PHASES, REVIEW_PHASES
from core.phases import Phase
from agent.usage import SessionUsage, parse_session_log
from pr.state import now_iso
from review.types import ReviewMeta, review_meta_from_dict

# ── Filenames ────────────────────────────────────────────────────────────────

FILENAME_PRIOR = "prior.md"
FILENAME_SESSION = "session.jsonl"
FILENAME_META = "meta.json"
FILENAME_PIPELINE_STATE = "pipeline.json"
FILENAME_PROMPT_STATS = "prompt-stats.json"
FILENAME_PRIOR_FINDINGS = "prior-findings.json"

FILENAME_POST_SESSION = "post.jsonl"
REVIEW_EXT = ".md"

ARCHIVES_DIRNAME = "archives"
ARCHIVE_KEEP_COUNT = 3

_ARCHIVE_TIMESTAMP_FMT = "%Y%m%d-%H%M%S"


# ── Path helpers ─────────────────────────────────────────────────────────────

def review_artifact_path(review_file: str, filename: str) -> str:
    """Where `filename` sits for the review whose deliverable is at `review_file`.

    The one rule that places everything a run writes: beside the review, in the
    directory the review names. A caller that joins the reviews root to a name
    of its own is writing somewhere no sweep looks.
    """
    return str(Path(review_file).parent / filename)


def archives_dir(review_dir: Path) -> Path:
    """Where `review_dir` keeps what earlier runs of the same review produced."""
    return review_dir / ARCHIVES_DIRNAME


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
    return review_artifact_path(review_file, name.format(index))


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
    return review_artifact_path(review_file, name.format(index))


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


# ── Review file helpers ─────────────────────────────────────────────────────


def review_file_path(repo: str, pr_number: str) -> Path:
    """Return the expected path for a review file given repo and PR number."""
    repo_name = repo.split("/")[-1]
    return workbench_paths.reviews_dir() / f"{repo_name}-{pr_number}" / f"review{REVIEW_EXT}"


def _load_review_meta(review_dir: Path) -> ReviewMeta | None:
    """The sidecar a review directory holds, or None if it holds no usable one.

    The distinction `read_review_meta` throws away: a reader wanting attribution
    is served as well by an empty `ReviewMeta` as by a missing file, but a
    writer must not mistake one for the other and overwrite what it could not
    read.

    A payload that is not an object is unusable in the same way an unparseable
    one is, and is reported the same way. `review_meta_from_dict` reads it as an
    empty record — the right answer for a reader, and the wrong one here, since
    it would licence writing a fresh sidecar over the file.
    """
    meta_file = review_dir / FILENAME_META
    if not meta_file.is_file():
        return None
    try:
        payload = json.loads(meta_file.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    return review_meta_from_dict(payload) if isinstance(payload, dict) else None


def read_review_meta(review_dir: Path) -> ReviewMeta:
    """What meta.json attributes this review to, empty where it says nothing."""
    return _load_review_meta(review_dir) or ReviewMeta()


def write_review_meta(review_dir: Path, meta: ReviewMeta) -> None:
    """Record `meta` as the review directory's sidecar, replacing what was there.

    The whole file, every time: `ReviewMeta` is the schema, so re-rendering it
    cannot drop a key that anything reads. Written atomically, because every
    review lookup on the machine walks these files and a half-written one reads
    as a review attributed to nothing.
    """
    serde.write_json(review_dir / FILENAME_META, serde.to_dict(meta))


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
    meta = _load_review_meta(review_dir)
    if meta is None:
        return
    try:
        write_review_meta(review_dir, replace(meta, reviewed_at=now_iso()))
    except OSError as exc:
        # Warned rather than raised: the review is already written and this
        # runs at the very end of a run that worked. Losing the stamp costs a
        # reader the mtime fallback; failing here would cost the whole review.
        meta_file = review_dir / FILENAME_META
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


# ── Archiving what a re-review replaces ──────────────────────────────────────


def _prune_archives(archive_dir: Path, suffix: str) -> None:
    """Drop all but the newest ARCHIVE_KEEP_COUNT archives of one kind.

    Newest is decided by name rather than by mtime: the stamp sorts
    lexicographically, and it records when the file was archived, which a copy
    or a restore does not disturb the way it does an mtime.
    """
    if not archive_dir.is_dir():
        return
    for old in sorted(archive_dir.glob(f"2*{suffix}"), reverse=True)[ARCHIVE_KEEP_COUNT:]:
        old.unlink(missing_ok=True)


def archive_review(review_file: Path, session_log: str) -> str:
    """Rotate the review at `review_file` and its logs into `archives/`.

    Returns the path of the copy left beside the review as `prior.md`, which is
    what a re-review reconciles its findings against — or "" when there was no
    review to archive, which is a first run.

    All three files take one timestamp, so a run's archives sort together and a
    reader can tell which log belongs to which review. Pruning runs whether or
    not this call archived anything: a keep count lowered since the last run has
    to take effect on the run that reads it, not the next one to write.
    """
    review_dir = review_file.parent
    archive_dir = archives_dir(review_dir)
    stamp = datetime.now().strftime(_ARCHIVE_TIMESTAMP_FMT)
    prior_path = ""

    if review_file.is_file():
        prior_path = str(review_dir / FILENAME_PRIOR)
        shutil.copy2(str(review_file), prior_path)
        log.info(f"Archived prior review to {ARCHIVES_DIRNAME}/{stamp}{REVIEW_EXT}")

    rotations = (
        (review_file, REVIEW_EXT),
        (Path(session_log), ".session.jsonl"),
        (review_dir / FILENAME_POST_SESSION, ".post.jsonl"),
    )
    for source, suffix in rotations:
        if not source.is_file():
            continue
        archive_dir.mkdir(exist_ok=True)
        shutil.move(str(source), str(archive_dir / f"{stamp}{suffix}"))

    for _, suffix in rotations:
        _prune_archives(archive_dir, suffix)
    return prior_path


def aggregate_session_usage(review_dir: Path | None) -> SessionUsage:
    """Aggregate usage from session and post-session logs."""
    if not review_dir:
        return SessionUsage()
    return ai_usage.merge([
        parse_session_log(str(review_dir / n))
        for n in (FILENAME_SESSION, FILENAME_POST_SESSION)
        if (review_dir / n).is_file()
    ])
