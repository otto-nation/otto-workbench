"""Snapshots of a knowledge base, and getting one back.

A knowledge base is the one tree the workbench holds that nothing can
regenerate. Everything else it writes has a producer that can be run again; an
article was typed by a person, and a stray delete ends it rather than delaying
it. That is what the data root exists to say, and this is the part that does
something about it.

What this is honest about: a snapshot here protects against a stray delete, a
compile that went wrong, and an editor that ate a file. It sits on the same disk
as the base, so it does not protect against losing the disk. Off-machine
durability is a backup tool's job, and `references/init.md` names one.

The archives live under the *state* root rather than beside the base. They have
a producer — this module — so they are exactly what state is for, and putting
them under the data root would nest the copy inside the tree being copied.

ceiling: whole-tree tarballs, with no deduplication between them. A knowledge
base is prose measured in megabytes, so ten copies cost less than the machinery
to avoid them would. Upgrade trigger: once a base is large enough that a
snapshot is slow enough to notice, which is the point at which an incremental
tool is worth reaching for instead of growing this one.
"""

# doc-group: platform

from __future__ import annotations

import hashlib
import tarfile
from datetime import datetime, timezone
from pathlib import Path

from core import serde
from core import workbench_paths

from .paths import BACKUPS_DIRNAME, is_wiki

# How many snapshots of one base to keep. Ten is enough to reach back past a
# mistake nobody noticed for a few sessions, and small enough that the directory
# stays readable.
KEEP_DEFAULT = 10

# When `wiki status` starts saying a base is overdue for one.
STALE_BACKUP_DAYS = 30

# Sorts chronologically as text, which is what lets retention pick the oldest
# by filename rather than by asking the filesystem for times it may not have.
STAMP_FORMAT = "%Y%m%dT%H%M%SZ"

ARCHIVE_SUFFIX = ".tar.gz"

# The half-written archive, renamed over the real name only once it is complete.
PARTIAL_SUFFIX = ".part"


def backups_dir(root: Path) -> Path:
    """Where snapshots of the base at *root* are kept.

    Named for the base's directory plus a hash of its full path: two repos both
    called `notes` must not share a backup directory, and the readable half is
    what makes the directory identifiable in a listing. Callers hand in the
    resolved root, and two spellings of one path normalise to one name anyway,
    so a base cannot end up with two directories.

    A sha256 of the path rather than the repo's `<org>/<repo>` identity, which
    would be the nicer name: that identity comes from `pr.target`, and this
    module is layer 2 and cannot reach it. `hashlib` rather than the built-in
    `hash`, which is randomised per process and would send the same base to a
    different directory on every run.
    """
    digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:12]
    return workbench_paths.state_dir() / BACKUPS_DIRNAME / f"{root.name}-{digest}"


def snapshots(root: Path) -> list[Path]:
    """Every snapshot of *root*, oldest first.

    Sorted explicitly. A glob comes back in filesystem order, so the "latest"
    taken off an unsorted listing is a different file on another machine.
    """
    directory = backups_dir(root)
    if not directory.is_dir():
        return []
    return sorted(directory.glob(f"*{ARCHIVE_SUFFIX}"))


def latest(root: Path) -> Path | None:
    found = snapshots(root)
    return found[-1] if found else None


def is_overdue(root: Path, now: datetime | None = None) -> bool:
    """Whether *root* has no snapshot, or none for `STALE_BACKUP_DAYS`.

    Read from the newest snapshot's stamp rather than its mtime: the stamp is
    what the archive is named for, and a copied or restored file carries a mtime
    that says when it was copied.
    """
    newest = latest(root)
    if newest is None:
        return True
    stamped = _stamp_of(newest)
    if stamped is None:
        return True
    now = now or datetime.now(timezone.utc)
    return (now - stamped).days >= STALE_BACKUP_DAYS


def _stamp_of(archive: Path) -> datetime | None:
    """The time in a snapshot's filename, or ``None`` if it does not carry one.

    The trailing counter every name carries is dropped first: without that, a
    stamp never parses and every base reads as never backed up.
    """
    name = archive.name[: -len(ARCHIVE_SUFFIX)].split("-")[0]
    try:
        return datetime.strptime(name, STAMP_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def snapshot(root: Path, keep: int = KEEP_DEFAULT, now: datetime | None = None) -> Path:
    """Archive the base at *root*, prune old snapshots, and return the new one.

    Written to a sibling `.part` and renamed into place, so an interrupted run
    leaves no archive rather than a truncated one that would restore to a
    partial knowledge base without saying so.
    """
    directory = backups_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now(timezone.utc)).strftime(STAMP_FORMAT)
    target = _free_name(directory, stamp, ARCHIVE_SUFFIX)
    partial = target.with_name(target.name + PARTIAL_SUFFIX)

    try:
        with tarfile.open(partial, "w:gz") as archive:
            archive.add(root, arcname=root.name)
        serde.replace_file(partial, target)
    finally:
        partial.unlink(missing_ok=True)

    prune(root, keep)
    return target


def _free_name(directory: Path, stamp: str, suffix: str) -> Path:
    """``<stamp><suffix>`` in *directory*, disambiguated if it is taken.

    The stamp is per-second, so two snapshots a moment apart would otherwise be
    one: the second overwrites the first and the count silently does not grow.

    The counter is zero-padded and starts at ``-01`` for the *first* name rather
    than being omitted, because these names are sorted as text and retention
    deletes from the front. A bare ``…Z`` would sort after ``…Z-02``, making the
    oldest-first order wrong exactly when a second collides — and the file it
    then pruned would be the newest.
    """
    attempt = 1
    candidate = directory / f"{stamp}-{attempt:02d}{suffix}"
    while candidate.exists():
        attempt += 1
        candidate = directory / f"{stamp}-{attempt:02d}{suffix}"
    return candidate


def prune(root: Path, keep: int = KEEP_DEFAULT) -> list[Path]:
    """Delete all but the newest *keep* snapshots, returning what went."""
    found = snapshots(root)
    if keep < 0 or len(found) <= keep:
        return []
    doomed = found[: len(found) - keep]
    for archive in doomed:
        archive.unlink()
    return doomed


def restore(root: Path, archive: Path) -> Path:
    """Extract *archive* beside the base, and return where it landed.

    Never over the live base. A restore is run when something has already gone
    wrong, and overwriting in place would make a wrong guess about which
    snapshot was wanted unrecoverable. The caller moves the result into place
    once they have looked at it.
    """
    stamp = datetime.now(timezone.utc).strftime(STAMP_FORMAT)
    destination = _free_name(root.parent, f"{root.name}.restored-{stamp}", "")
    destination.mkdir(parents=True)
    with tarfile.open(archive, "r:gz") as tar:
        # `data` refuses absolute paths, parent traversal, links out of the
        # tree, and device files. These archives are written here, but they
        # live in a directory a person can drop a file into.
        tar.extractall(destination, filter="data")

    unwrapped = destination / root.name
    if is_wiki(unwrapped):
        return unwrapped
    return destination
