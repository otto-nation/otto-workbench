"""Content snapshots that restore a verify or suite phase's writes.

A fix pass's verify agent and the repo's suite both run in the operator's real
worktree with write access. Their writes used to fold into the commit scope —
including a tracked file the verify agent overwrote with a fixture. This module
snapshots *content* (not path sets) around those phases and restores anything
they changed, so the fix agent's pre-phase edits survive and a rewrite of an
already-dirty file is undone.

The snapshot uses a temporary ``GIT_INDEX_FILE`` so it never touches the
operator's real index or stash. Blobs it writes live in the object database
and are otherwise unreachable.

Tool denials on the verify agent (Claude Edit/Write, Pi review-guard) are
ergonomics. Process writes — ``python3 -c``, a suite, ``sed`` — bypass both,
which is why this restore is the real control.
"""

# ceiling: gitignored writes are invisible to this snapshot, because
# ``git add -A`` respects exclude-standard. Upgrade trigger: if an agent is
# observed writing a gitignored path that later feeds a commit.
#
# ceiling: the fix agent still runs in the real tree. Upgrade once a way
# exists to provision ignored state in a temp worktree.

# doc-group: pipeline

from __future__ import annotations

import os
import shutil
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import core.log
import core.proc
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


def take(workdir: str | Path) -> Snapshot:
    """Snapshot worktree contents without touching the real index.

    Failed reads return ``Snapshot(ok=False)`` and warn. Callers must not
    treat that as an empty tree.
    """
    workdir = Path(workdir)
    with tempfile.TemporaryDirectory(prefix="fix-snap-") as tmp:
        index = str(Path(tmp) / "index")
        env = os.environ | {"GIT_INDEX_FILE": index}
        added = git.client.run("add", "-A", "--", ".", cwd=workdir, env=env)
        if not added.ok:
            _unread(workdir, added)
            return Snapshot(ok=False)
        tree = git.client.run("write-tree", cwd=workdir, env=env)
        sha = tree.stdout.strip()
        if not tree.ok or not sha:
            _unread(workdir, tree)
            return Snapshot(ok=False)
        listed = git.client.run("ls-tree", "-r", "-z", sha, cwd=workdir, env=env)
        if not listed.ok:
            _unread(workdir, listed)
            return Snapshot(ok=False)
        return Snapshot(files=_parse_tree(listed.stdout), ok=True)


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
        _save_clobber(workdir, saved_to, path)
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


def head_moved(workdir: str | Path, head_before: str) -> str | None:
    """Current HEAD when it differs from *head_before*, else None.

    An agent that ran ``git -C . commit`` (which evades Claude's prefix deny)
    moves HEAD. The engine refuses to land on top of that unless the domain
    asked for ``recover``.
    """
    if not head_before:
        return None
    now = git.client.head_sha(cwd=workdir)
    if now and now != head_before:
        return now
    return None


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


def _save_clobber(workdir: Path, dest: Path, rel: str) -> None:
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
    blob = git.client.run("cat-file", "blob", record.sha, cwd=workdir)
    if not blob.ok:
        core.log.warn(core.proc.failure_message(
            f"Could not restore {rel} from snapshot", blob,
        ))
        return
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)
    if record.mode == _SYMLINK:
        path.symlink_to(blob.stdout.rstrip("\n"))
        return
    path.write_bytes(blob.stdout.encode())
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
