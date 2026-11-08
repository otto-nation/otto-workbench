"""Content snapshots that restore a phase's writes in a named worktree.

A fix pass's verify agent and the repo's suite both run in the operator's real
worktree with write access. Their writes used to fold into the commit scope —
including a tracked file the verify agent overwrote with a fixture. This module
snapshots *content* (not path sets) around those phases and restores anything
they changed, so the fix agent's pre-phase edits survive and a rewrite of an
already-dirty file is undone.

The same helpers wrap extra worktrees a domain granted the agent — the comments
pass's default-branch checkout is ``--add-dir`` / Pi ``REVIEW_ALLOWED_DIRS``,
so it is writable on both backends, and prompt text calling it read-only does
not revert a write. ``around_each`` snapshots those trees around the pass and
restores any path whose content changed, leaving the operator's pre-existing
dirt intact when the agent did not touch it.

The snapshot uses a temporary ``GIT_INDEX_FILE`` seeded from ``HEAD`` so it
never touches the operator's real index or stash, and so a tracked file under
an ignored path is still seen. Blobs it writes live in the object database
and are otherwise unreachable.

Tool denials on the verify agent (Claude Edit/Write, Pi review-guard) are
ergonomics. Process writes — ``python3 -c``, a suite, ``sed`` — bypass both,
which is why this restore is the real control.

The review adapter's commit scope is the other half: it keeps only finding
anchors and the tests named for them, so a PR file that is not an anchor stays
in the worktree with a warning.
"""

# ceiling: untracked gitignored writes and submodule (gitlink) contents are
# invisible to this snapshot; tracked files under an ignored path are seen
# because the temp index is seeded from HEAD. Upgrade trigger: if an agent
# is observed writing an untracked ignored path that later feeds a commit.
#
# ceiling: every snapshot rehashes each tracked file — the seeded index has
# no stat cache. Upgrade trigger: once a pass trail shows snapshots taking
# over 5 seconds, seed from a copy of the real index instead.
#
# ceiling: the fix agent still runs in the real tree, so it can overwrite an
# operator's uncommitted edit. `fix.dirty_backup` copies those bytes under
# artifacts/dirty-backup before the agent runs, but the overwrite still
# happens. Upgrade trigger: once a `fix_pass_left_dirty` trail event carries
# `already_dirty` paths the operator did not ask the pass to touch, run the
# agent in a copy of the worktree.

# doc-group: pipeline

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path

import core.log
import core.proc
import core.timeouts
import git.client
from core.trail import Trail, twarn

_GITLINK = "160000"
_SYMLINK = "120000"


@dataclass(frozen=True)
class FileRecord:
    """One path in a content snapshot: blob sha and git mode."""

    path: str
    sha: str
    mode: str


@dataclass(frozen=True)
class Snapshot:
    """The worktree's tracked and untracked (non-ignored) contents.

    ``ok`` is False when the snapshot could not be read. That is not the same
    as an empty tree — unknown is not "nothing changed", and a failed read
    must not restore anything.
    """

    files: tuple[FileRecord, ...] = ()
    ok: bool = True

    def by_path(self) -> dict[str, FileRecord]:
        """Index records by path."""
        return {record.path: record for record in self.files}


@dataclass(frozen=True)
class Reverted:
    """What restore undid, and where the clobbered bytes were saved."""

    paths: tuple[str, ...]
    saved_to: Path | None


@dataclass(frozen=True)
class WorktreeTree:
    """A tree object holding the worktree's tracked and untracked-unignored content."""

    sha: str = ""
    ok: bool = True


def worktree_tree(workdir: str | Path) -> WorktreeTree:
    """Write the worktree's content as a tree object, leaving the real index alone.

    The temporary index is seeded from ``HEAD`` when there is one, so a tracked
    file under a ``.gitignore`` path still has an entry that ``add -A`` updates.
    An unborn repo skips the seed. Failed reads return ``WorktreeTree(ok=False)``
    and warn; callers must not treat that as an empty tree.
    """
    workdir = Path(workdir)
    with tempfile.TemporaryDirectory(prefix="fix-snap-") as tmp:
        env = os.environ | {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
        has_head = git.client.run(
            "rev-parse", "--verify", "-q", "HEAD", cwd=workdir,
        ).ok
        failed = _fill_index(workdir, env, has_head)
        if failed is not None:
            _unread(workdir, failed)
            return WorktreeTree(ok=False)
        written = git.client.run("write-tree", cwd=workdir, env=env)
        sha = written.stdout.strip()
        if not written.ok or not sha:
            _unread(workdir, written)
            return WorktreeTree(ok=False)
        return WorktreeTree(sha=sha)


def _fill_index(
    workdir: Path, env: dict[str, str], has_head: bool,
) -> core.proc.CmdResult | None:
    """Seed (when HEAD exists) and fill the temp index; the failing step, if any."""
    steps = [("add", "-A", "--", ".")]
    if has_head:
        steps.insert(0, ("read-tree", "HEAD"))
    for step in steps:
        done = git.client.run(*step, cwd=workdir, env=env)
        if not done.ok:
            return done
    return None


def read_tree(workdir: str | Path, treeish: str) -> Snapshot:
    """The records of every blob under *treeish*, or ``Snapshot(ok=False)``."""
    listed = git.client.run("ls-tree", "-r", "-z", treeish, cwd=workdir)
    if not listed.ok:
        _unread(Path(workdir), listed)
        return Snapshot(ok=False)
    return Snapshot(files=_parse_tree(listed.stdout), ok=True)


def take(workdir: str | Path) -> Snapshot:
    """Snapshot worktree contents without touching the real index.

    Failed reads return ``Snapshot(ok=False)`` and warn. Callers must not
    treat that as an empty tree.
    """
    tree = worktree_tree(workdir)
    if not tree.ok:
        return Snapshot(ok=False)
    return read_tree(workdir, tree.sha)


@contextmanager
def around(
    workdir: str | Path, artifacts: str | Path, phase: str, trail: Trail | None,
) -> Iterator[None]:
    """Snapshot, run the phase, snapshot, restore anything the phase wrote."""
    before = take(workdir)
    try:
        yield
    finally:
        after = take(workdir)
        restore(
            workdir, before, after, artifacts=artifacts, phase=phase, trail=trail,
        )


@contextmanager
def around_each(
    workdirs: Sequence[str | Path],
    artifacts: str | Path,
    trail: Trail | None,
    *,
    exclude: str | Path | None = None,
    phase: str = "extra-worktree",
) -> Iterator[None]:
    """Snapshot each extra worktree around a pass; restore anything it wrote.

    ``exclude`` is the pass's own workdir, so listing it cannot revert the
    agent's real edits. Empty *workdirs* is a no-op — every adapter but comments.

    Attribution is by content, so an edit the operator makes in one of these
    trees while the pass runs is reverted like an agent write; the saved copy
    under ``artifacts/containment/<phase>`` holds it.
    """
    # ceiling: content snapshots cannot tell the agent's write from the
    # operator's concurrent edit. Upgrade trigger: if an operator reports a
    # mid-pass edit lost to this restore, deny the agent writes there instead.
    skip = Path(exclude).resolve() if exclude is not None else None
    unique = {Path(raw).resolve(): Path(raw) for raw in reversed(list(workdirs)) if raw}
    dirs = [p for k, p in reversed(unique.items()) if k != skip]
    if not dirs:
        yield
        return
    with ExitStack() as stack:
        for wd in dirs:
            stack.enter_context(around(wd, artifacts, phase, trail))
        yield


def restore(
    workdir: str | Path,
    before: Snapshot,
    after: Snapshot,
    *,
    artifacts: str | Path,
    phase: str,
    trail: Trail | None,
) -> Reverted:
    """Put *before*'s contents back for every path *after* disagrees on.

    Saves the clobbered post-phase bytes under ``artifacts/containment/<phase>``
    first. A snapshot that failed to read reverts nothing and warns.
    """
    workdir = Path(workdir)
    # `ok` alone suffices: both snapshots come from the same take(), and any
    # git failure there sets ok=False.
    if not before.ok or not after.ok:
        core.log.warn(
            f"could not snapshot the worktree around the {phase} phase — "
            "leaving it as-is (unknown is not nothing changed)"
        )
        twarn(
            trail, "fix_containment_unread",
            f"{phase} phase snapshot unread; nothing reverted",
            data={"phase": phase, "workdir": str(workdir)},
        )
        return Reverted(paths=(), saved_to=None)

    before_map = before.by_path()
    after_map = after.by_path()
    changed = sorted(
        path for path in set(before_map) | set(after_map)
        if before_map.get(path) != after_map.get(path)
    )
    if not changed:
        return Reverted(paths=(), saved_to=None)

    saved_to = Path(artifacts) / "containment" / phase
    saved_to.mkdir(parents=True, exist_ok=True)
    for path in changed:
        save_clobber(workdir, saved_to, path)
        _put_back(workdir, before_map.get(path), path)

    named = ", ".join(changed)
    core.log.warn(
        f"reverted {len(changed)} file(s) the {phase} phase wrote in "
        f"{workdir}: {named}. Clobbered content saved under {saved_to}."
    )
    twarn(
        trail, "fix_containment_reverted",
        f"reverted {len(changed)} {phase} phase write(s)",
        data={
            "phase": phase,
            "paths": changed,
            "saved_to": str(saved_to),
            "workdir": str(workdir),
        },
    )
    return Reverted(paths=tuple(changed), saved_to=saved_to)


def _unread(workdir: Path, result: core.proc.CmdResult) -> None:
    core.log.warn(core.proc.failure_message(
        f"Could not snapshot worktree contents in {workdir}", result,
    ))


def _parse_tree(raw: str) -> tuple[FileRecord, ...]:
    records: list[FileRecord] = []
    for entry in raw.split("\0"):
        if not entry:
            continue
        meta, _, path = entry.partition("\t")
        parts = meta.split()
        if len(parts) != 3 or not path:
            continue
        mode, _kind, sha = parts
        if mode == _GITLINK:
            continue
        records.append(FileRecord(path=path, sha=sha, mode=mode))
    return tuple(records)


def save_clobber(workdir: Path, dest: Path, rel: str) -> None:
    src = workdir / rel
    if not src.is_file() and not src.is_symlink():
        return
    target = dest / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        target.unlink()
    shutil.copy2(src, target, follow_symlinks=False)


def _put_back(workdir: Path, record: FileRecord | None, rel: str) -> None:
    path = workdir / rel
    if record is None:
        _remove(path)
        _prune_empty(path.parent, workdir)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    # Bytes, not text: `git.client.run` decodes, which corrupts a binary blob.
    blob = subprocess.run(
        ["git", "cat-file", "blob", record.sha], cwd=workdir,
        capture_output=True, check=False, timeout=core.timeouts.LOCAL,
    )
    if blob.returncode != 0:
        core.log.warn(
            f"Could not restore {rel} from snapshot: "
            f"{blob.stderr.decode(errors='replace').strip()}"
        )
        return
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)
    if record.mode == _SYMLINK:
        path.symlink_to(os.fsdecode(blob.stdout))
        return
    path.write_bytes(blob.stdout)
    path.chmod(stat.S_IMODE(int(record.mode, 8)))


def _remove(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _prune_empty(start: Path, root: Path) -> None:
    current = start
    while current != root and current.is_dir():
        try:
            next(current.iterdir())
        except StopIteration:
            current.rmdir()
            current = current.parent
            continue
        except OSError:
            return
        return
