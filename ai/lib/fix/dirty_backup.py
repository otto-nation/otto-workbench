"""Pre-pass copies of files already dirty in the operator's worktree.

The fix agent runs in the real worktree and can overwrite an uncommitted edit.
Containment snapshots verify and suite phases and restores those writes, but
nothing on disk held the operator's bytes from before the agent ran. This
module copies already-dirty files under ``artifacts/dirty-backup/`` so a
``fix_pass_left_dirty`` warning can name a restorable copy.

It reuses ``fix.containment.save_clobber`` (bytes-exact, binary-safe, symlinks
recorded not followed). Backup failure never aborts the pass: a trail warning
is recorded and the pass continues. The copies live under the pass's artifacts,
which are already excluded from the commit and from leftovers.
"""

# ceiling: files larger than MAX_FILE_BYTES and backups totalling more than
# MAX_TOTAL_BYTES are skipped rather than streamed. Upgrade trigger: if a
# fix_pass_left_dirty event names an already_dirty path that was skipped as
# oversize and the operator needed those bytes.

# doc-group: pipeline

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import core.log
from core.trail import Trail, twarn
from fix.containment import save_clobber

DIRNAME = "dirty-backup"
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_TOTAL_BYTES = 50 * 1024 * 1024
_SKIPPED_NAME = "dirty-backup.skipped.json"


@dataclass(frozen=True)
class Skipped:
    """One already-dirty path that was not copied, and why."""

    path: str
    reason: str


@dataclass(frozen=True)
class Backup:
    """What save copied, what it skipped, and where the copies live."""

    dest: Path | None = None
    paths: tuple[str, ...] = ()
    skipped: tuple[Skipped, ...] = ()


def dest_dir(artifacts: str | Path) -> Path:
    """``<artifacts>/dirty-backup``."""
    return Path(artifacts) / DIRNAME


def save(
    workdir: str | Path,
    artifacts: str | Path,
    dirty_before: set[str],
    *,
    trail: Trail | None = None,
    exclude: str = "",
) -> Backup:
    """Copy each already-dirty file under ``artifacts/dirty-backup/``.

    *exclude* is a POSIX relative prefix (the pass's own artifacts) that is
    never copied. Missing paths, directories, and gitlinks are skipped
    silently. Oversize files are recorded on the result and in a sibling
    JSON list. Never raises: a failure warns on the trail and returns an
    empty ``Backup``.
    """
    try:
        return _save(workdir, artifacts, dirty_before, exclude=exclude)
    except Exception as exc:
        _failed(workdir, artifacts, trail, exc)
        return Backup()


def _save(
    workdir: str | Path,
    artifacts: str | Path,
    dirty_before: set[str],
    *,
    exclude: str,
) -> Backup:
    workdir = Path(workdir)
    artifacts = Path(artifacts)
    dest = dest_dir(artifacts)
    paths: list[str] = []
    skipped: list[Skipped] = []
    total = 0
    for rel in sorted(dirty_before):
        if _excluded(rel, exclude, workdir, artifacts):
            continue
        src = workdir / rel
        try:
            info = src.lstat()
        except OSError:
            continue
        if not stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode):
            continue
        if info.st_size > MAX_FILE_BYTES:
            skipped.append(Skipped(rel, f"file exceeds {MAX_FILE_BYTES} bytes"))
            continue
        if total + info.st_size > MAX_TOTAL_BYTES:
            skipped.append(Skipped(
                rel, f"total backup would exceed {MAX_TOTAL_BYTES} bytes",
            ))
            continue
        dest.mkdir(parents=True, exist_ok=True)
        try:
            save_clobber(workdir, dest, rel)
        except OSError as exc:
            skipped.append(Skipped(rel, f"unreadable: {exc}"))
            continue
        paths.append(rel)
        total += info.st_size
    if skipped:
        _write_skipped(artifacts, skipped)
    return Backup(
        dest=dest if (paths or skipped) else None,
        paths=tuple(paths),
        skipped=tuple(skipped),
    )


def _excluded(
    rel: str, exclude: str, workdir: Path, artifacts: Path,
) -> bool:
    if exclude and PurePosixPath(rel).is_relative_to(exclude):
        return True
    try:
        (workdir / rel).resolve().relative_to(artifacts.resolve())
    except ValueError:
        return False
    return True


def _write_skipped(artifacts: Path, skipped: list[Skipped]) -> None:
    payload = [{"path": item.path, "reason": item.reason} for item in skipped]
    path = Path(artifacts) / _SKIPPED_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.tmp"
    tmp.write_text(json.dumps(payload, indent=2) + "\n")
    os.replace(tmp, path)


def _failed(
    workdir: str | Path,
    artifacts: str | Path,
    trail: Trail | None,
    exc: BaseException,
) -> None:
    core.log.warn(
        f"could not back up already-dirty files in {workdir} under "
        f"{artifacts}: {exc}"
    )
    twarn(
        trail, "fix_dirty_backup_failed",
        "already-dirty backup failed; continuing the pass",
        data={
            "workdir": str(workdir),
            "artifacts": str(artifacts),
            "error": str(exc),
        },
    )
