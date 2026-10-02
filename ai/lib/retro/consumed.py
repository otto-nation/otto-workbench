"""The record of which local reviews a retro run consumed, and may delete.

`retro-scan` reads the reviews root and reports what it found; the retro
skill's final phase deletes what was reported. Those are two processes with a
whole analysis between them, so the permission to delete has to survive as a
file — and a file on disk is a permission anything can pick up.

That is the hazard this module exists to bound. A plain list of directory
names grants deletion to whoever reads it next: a debug scan overwrites it, an
abandoned retro leaves it armed, and the completion cannot tell either from the
run it is actually finishing. So the record carries the identity of the scan
that wrote it and a fingerprint of each review as it was read, and the
completion presents the scan ID it believes it is completing. A record that
does not answer to that ID is refused rather than honoured, and an entry whose
review has changed since the scan is left alone rather than deleted.

Writing the record is `retro.scan`'s (run by `retro-scan --consume`), and
reading and enforcing it is `consume()` below (run by `retro-consume`, which
the skill calls in place of an inline `rm`).
"""

# doc-group: platform

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

import core.log
import core.serde
import core.workbench_paths


# ── Constants ────────────────────────────────────────────────────────────────

# Beside reviews/ under the state root rather than under --home: both are
# generated data, and the completion reads them through the state root too.
# Its half of this name is RETRO_CONSUMED_REVIEWS_FILE in lib/constants.sh;
# tests/workbench_roots.bats holds the two together.
CONSUMED_REVIEWS_NAME = "retro-consumed-reviews.json"


@dataclass(frozen=True)
class ConsumedReview:
    """One review a scan read, and enough of it to recognise again.

    `reviewed_at` is the fingerprint. A review re-run between the scan and the
    completion is a different review with the same directory name — the retro
    analysed the old one, so deleting the new one would discard a review nobody
    has read. Comparing the timestamp the scan saw against the one on disk
    catches exactly that, and costs a field.
    """

    dir_name: str
    repo: str = ""
    reviewed_at: str = ""


@dataclass(frozen=True)
class ConsumeRecord:
    """What one scan consumed, stamped with the identity of that scan.

    `scan_id` is the trail invocation of the run that wrote it, which is also
    the handle `otto-log show` takes — so a record that turns up unexpectedly
    can be traced back to the command that armed it.
    """

    scan_id: str
    scanned_at: str = ""
    reviews: list[ConsumedReview] = field(default_factory=list)


def record_path() -> Path:
    """Where the consume record lives."""
    return core.workbench_paths.state_dir() / CONSUMED_REVIEWS_NAME


def write_record(record: ConsumeRecord, path: Path | None = None) -> None:
    """Write `record`, replacing any previous one.

    Written even when it consumed nothing. An empty record is the statement
    "this scan claims no reviews", and it has to overwrite a previous run's
    claim — skipping the write on empty is what lets an abandoned retro's list
    survive into a later run that scanned nothing of its own.
    """
    core.serde.write_json(path or record_path(), core.serde.to_dict(record))


def read_record(path: Path | None = None) -> ConsumeRecord | None:
    """The record on disk, or None when there is not a usable one."""
    return core.serde.load_file(ConsumeRecord, path or record_path())


def clear_record(path: Path | None = None) -> None:
    """Remove the record. Safe to call when there is none."""
    (path or record_path()).unlink(missing_ok=True)


def deletable(record: ConsumeRecord, reviews_dir: Path) -> tuple[list[Path], list[str]]:
    """The review directories from `record` that are still safe to delete.

    Returns the paths to delete and a list of human-readable reasons for each
    entry being kept back. An entry is skipped when its directory is already
    gone, when the name is not a plain directory name, or when the review on
    disk no longer matches the one the scan read.

    The name check is not paranoia about a hostile file: it is that this list
    feeds a recursive delete, and a name carrying a separator would resolve
    outside the reviews root. Nothing legitimate writes one, so anything that
    does is a bug whose blast radius belongs contained here.
    """
    targets: list[Path] = []
    skipped: list[str] = []
    for entry in record.reviews:
        name = entry.dir_name
        if not name or name in (".", "..") or "/" in name or "\\" in name:
            skipped.append(f"{name!r}: not a plain directory name")
            continue
        target = reviews_dir / name
        if not target.is_dir():
            skipped.append(f"{name}: directory is already gone")
            continue
        current = _reviewed_at(target)
        if entry.reviewed_at and current and current != entry.reviewed_at:
            skipped.append(f"{name}: re-reviewed since the scan read it")
            continue
        targets.append(target)
    return targets, skipped


def _reviewed_at(review_dir: Path) -> str:
    """The on-disk review's timestamp, by the same rule the scan recorded it.

    Imported here rather than at module scope: `review.paths` pulls in the
    review pipeline, and the completion path needs only this one answer from
    it.
    """
    from review.paths import ReviewEntry, ReviewEntryKind, read_review_meta

    entry = ReviewEntry(
        path=review_dir,
        kind=ReviewEntryKind.REVIEW,
        meta=read_review_meta(review_dir),
    )
    return entry.reviewed_at


def consume(scan_id: str, *, dry_run: bool = False) -> int:
    """Delete the reviews this scan recorded, if the record answers to it.

    Returns 0 when done or when there is nothing to do, and 1 when the
    record belongs to another scan.
    """
    record = read_record()
    if record is None:
        # Not an error. A retro whose scan consumed nothing, or one run before
        # this record existed, has nothing to clean up and should not fail the
        # completion over it.
        core.log.info("No consume record — nothing to clean up")
        return 0

    if record.scan_id != scan_id:
        core.log.error(
            f"Consume record belongs to scan {record.scan_id}, not {scan_id} "
            f"— refusing to delete {len(record.reviews)} review(s) this retro did not scan. "
            f"Trace it with `otto-log show {record.scan_id}`."
        )
        return 1

    reviews_dir = core.workbench_paths.reviews_dir()
    targets, skipped = deletable(record, reviews_dir)
    for reason in skipped:
        core.log.warn(f"Keeping {reason}")

    if dry_run:
        for target in targets:
            core.log.info(f"Would delete {target.name}")
        core.log.info(f"{len(targets)} review(s) would be deleted, {len(skipped)} kept")
        return 0

    deleted = 0
    raced = 0
    for target in targets:
        try:
            shutil.rmtree(target)
            deleted += 1
        except FileNotFoundError:
            # Gone since deletable()'s is_dir() check — a concurrent sweep
            # (e.g. `pr gc`) beat this run to it. The record stays until
            # clear_record() below, so a re-run with the same scan ID skips
            # it as already gone rather than losing track of it.
            raced += 1
            core.log.warn(f"{target.name}: gone before deletion — already removed")

    # Cleared only after the deletions it authorised have happened. A crash
    # part-way leaves the record in place, so the next run with the same scan
    # ID deletes the remainder rather than losing track of it — the entries
    # already gone are skipped as missing.
    clear_record()
    # The raced clause only appears when it has something to report. A run that
    # hit no race is the overwhelmingly common one, and "0 raced" on every line
    # of a tool's normal output is noise that trains the reader past the clause
    # for the run where it is not zero.
    summary = f"Deleted {deleted} consumed review(s), kept {len(skipped)}"
    if raced:
        summary += f", {raced} raced (removed concurrently)"
    core.log.ok(summary)
    return 0
