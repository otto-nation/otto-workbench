"""Files a fix pass changed that its commit does not hold.

A pass commits only the paths it can attribute to itself, so an edit to a file
that was already dirty, or to a PR file that is not a finding anchor, stays in
the worktree while the pass reports a clean landing. This module compares the
worktree before the pass with the worktree after landing, by content, and says
what the commit left behind — on the console and as a ``fix_pass_left_dirty``
trail event. When any of those files were already dirty before the pass, the
warning names ``artifacts/dirty-backup/``, which holds a bytes-exact copy of
each such file as it was before the agent ran. It reports and never changes
the tree.
"""

# doc-group: pipeline

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import core.log
import fix.containment
from core.trail import Trail, tinfo, twarn
from git.land import CommitStatus, LandResult


@dataclass(frozen=True)
class Leftovers:
    """Files a pass changed that its commit does not hold, and which were already dirty."""

    paths: tuple[str, ...] = ()
    already_dirty: tuple[str, ...] = ()
    # False when a snapshot could not be read: unknown is not "nothing left".
    known: bool = True


def artifacts_prefix(workdir: str | Path, artifacts: str | Path) -> str:
    """*artifacts* as a POSIX path relative to *workdir*, or "" when outside it."""
    try:
        return Path(artifacts).resolve().relative_to(Path(workdir).resolve()).as_posix()
    except ValueError:
        return ""


def _under(path: str, prefix: str) -> bool:
    return bool(prefix) and PurePosixPath(path).is_relative_to(prefix)


def find(
    workdir: str | Path,
    pre: fix.containment.Snapshot,
    dirty_before: set[str],
    exclude: str = "",
) -> Leftovers:
    """What the pass changed by content that is still not in ``HEAD``.

    *pre* is the snapshot taken before the pass. *exclude* is a POSIX relative
    prefix (the pass's own artifacts) that is never reported. Content-level, so
    an edit to a file that was already dirty counts.
    """
    post = fix.containment.take(workdir)
    head = fix.containment.read_tree(workdir, "HEAD")
    if not (pre.ok and post.ok and head.ok):
        return Leftovers(known=False)
    before, after, committed = pre.by_path(), post.by_path(), head.by_path()
    changed = {p for p in set(before) | set(after) if before.get(p) != after.get(p)}
    dirty_now = {
        p for p in set(after) | set(committed) if after.get(p) != committed.get(p)
    }
    paths = sorted(p for p in changed & dirty_now if not _under(p, exclude))
    return Leftovers(
        paths=tuple(paths),
        already_dirty=tuple(sorted(set(paths) & dirty_before)),
    )


def _backup_note(
    already_dirty: tuple[str, ...], backed_up: tuple[str, ...], backup_dir: str | Path,
) -> str:
    """Name the pre-pass copies that exist and flag the files that have none."""
    copied = [p for p in already_dirty if p in backed_up]
    missing = [p for p in already_dirty if p not in backed_up]
    note = ""
    if copied:
        note += f"; pre-pass copies of {', '.join(copied)} under {backup_dir}"
    if missing:
        note += (
            f"; no pre-pass copy of {', '.join(missing)} "
            "(see dirty-backup.skipped.json)"
        )
    return note


def report(
    workdir: str | Path, found: Leftovers, landed: LandResult, trail: Trail | None,
    backup_dir: str | Path | None = None, backed_up: tuple[str, ...] = (),
    dirty_before: set[str] | frozenset[str] = frozenset(),
) -> None:
    """Warn on the console and record ``fix_pass_left_dirty`` when *found* has paths.

    *backup_dir* is named only when an already-dirty path was changed, so the
    operator can restore the pre-pass bytes ``fix.dirty_backup`` copied there.
    Only the already-dirty paths in *backed_up* are claimed as copied; the rest
    were skipped (see ``dirty-backup.skipped.json``).

    *dirty_before* is what was already uncommitted when the pass started. A
    failed commit against that set is not the pass refusing its own work — a
    fix pass never stages a file that was dirty on entry — so the warning names
    that rule rather than leaving the operator to infer it from leftover paths.
    """
    if landed.status is CommitStatus.COMMIT_FAILED and dirty_before:
        core.log.warn(
            "the worktree already held uncommitted edits before this pass "
            f"({', '.join(sorted(dirty_before))}): a fix pass never commits a "
            "file that was dirty when it started — commit or stash them, or "
            "re-run so an interrupted pass's edits are resumed"
        )
    if not found.known:
        tinfo(trail, "fix_leftovers_unread",
              "could not read the worktree to find what the pass left uncommitted",
              data={"workdir": str(workdir)})
        return
    if not found.paths:
        return
    message = (
        f"{len(found.paths)} file(s) this pass changed are not in its commit in "
        f"{workdir}: {', '.join(found.paths)}"
    )
    if found.already_dirty:
        message += (
            "; already dirty before the pass, so never credited: "
            f"{', '.join(found.already_dirty)}"
        )
        if backup_dir is not None:
            message += _backup_note(found.already_dirty, backed_up, backup_dir)
    message += "."
    core.log.warn(message)
    data = {
        "paths": list(found.paths),
        "already_dirty": list(found.already_dirty),
        "land_status": str(landed.status),
        "workdir": str(workdir),
    }
    if found.already_dirty and backup_dir is not None:
        data["backup"] = str(backup_dir)
    twarn(trail, "fix_pass_left_dirty", message, data=data)
