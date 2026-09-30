"""Which worktrees an interactive agent session is editing right now.

An unattended fix pass and a person editing in Pi or Claude Code are two
writers in one working tree, and neither can see the other. The pass commits
what it finds, so a half-finished edit lands in a commit nobody reviewed.

The three locks in this package answer three different questions, and taking
the wrong one is worse than taking none:

``run_lock``
    One workbench command per checkout — an exclusive flock held for the
    length of a ``pr`` run.
``tree_lock``
    A validator is *reading* this tree, so nothing may edit it. Both edit
    guards refuse writes while it is held, so a session that took this one
    would block its own edits.
``session_lock``
    This module. A person is editing here, so an unattended pass must not
    commit.

A record, not a flock, and that is the design rather than a shortcut. A flock
is held by a process, and under Claude Code there is no process to hold one:
``SessionStart`` and ``SessionEnd`` are short-lived subprocesses whose locks
die with the hook. Holding one would take a daemon per session, and a daemon
that dies while its session lives is a lock that lies. So the holder is the
harness process itself, named rather than attached to.

Kill-safety comes from the pid instead of the kernel. A record is live only
while its pid exists *and* was started at the recorded time: ``ps -o lstart=``
returns non-zero for a dead pid, so a session killed with SIGKILL leaves a
record the next reader prunes, and a pid recycled onto an unrelated process
fails the start-time match rather than impersonating the session that died.
That is strictly more than a bare ``os.kill(pid, 0)`` — which is all
``tree_lock._alive`` needs, because there the flock is the verdict and the pid
only names a holder in a diagnostic. Here the record *is* the verdict.

Several sessions in one worktree are legitimate, so the file is JSONL with one
object per holder, as ``tree_lock``'s is.
"""

# doc-group: platform

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from core import serde, timeouts, tree_lock

LOCK_FILE = "workbench-session.lock"

# How far up the process tree a self-exemption walk will go. A tool subprocess
# sits a handful of levels under its harness; a cycle or a runaway chain is a
# bug in the walk rather than a real ancestry.
_MAX_ANCESTRY = 16


@dataclass(frozen=True)
class SessionHolder:
    """One interactive session editing one worktree."""

    pid: int
    started: str
    harness: str
    session_id: str
    command: str

    def describe(self) -> str:
        """One line naming the holder, for a refusal message."""
        return (
            f"{self.harness} session {self.command} "
            f"(pid {self.pid}, started {self.started})"
        )


def lock_path(worktree: Path) -> Path | None:
    """Where the session record for ``worktree`` lives, or None outside a repo.

    The worktree's own git dir, as the other two locks use, so a linked
    worktree gets its own record rather than sharing the bare repo's.
    ``tree_lock._git_dir`` is reused rather than copied: it already strips the
    ``GIT_DIR``/``GIT_WORK_TREE`` a hook exports, which is the trap a fourth
    copy of that resolver would re-import.
    """
    git_dir = tree_lock._git_dir(worktree)
    return None if git_dir is None else git_dir / LOCK_FILE


def process_start(pid: int) -> str:
    """When ``pid`` started, or "" when it is not running.

    The half of liveness ``os.kill`` cannot give. A pid is reused once the
    kernel wraps around, and on a machine that churns subprocesses the reused
    one is often a tool subprocess of the very agent asking — so without this
    a dead session's record would go on refusing passes forever.
    """
    try:
        out = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=timeouts.LOCAL,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def _alive(pid: int, started: str) -> bool:
    """Whether the process that wrote a record still holds it."""
    current = process_start(pid)
    return bool(current) and current == started


def _as_record(holder: SessionHolder) -> str:
    """One holder, as the line the lock file stores."""
    return json.dumps({
        "pid": holder.pid,
        "started": holder.started,
        "harness": holder.harness,
        "session_id": holder.session_id,
        "command": holder.command,
    })


def holders(worktree: Path) -> list[SessionHolder]:
    """Every live session editing ``worktree``, in the order recorded.

    Dead records are dropped from what is returned and left on disk: a reader
    that rewrote the file would race every other reader, and ``acquire``
    prunes them on the next write anyway.
    """
    path = lock_path(worktree)
    if path is None or not path.exists():
        return []
    try:
        text = path.read_text()
    except OSError:
        return []

    live: list[SessionHolder] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            # A torn write from an acquire that was killed mid-rename. Skipping
            # beats failing: the question is who holds this tree, and a line
            # nobody can read holds nothing.
            continue
        if not isinstance(record, dict):
            continue
        pid, started = record.get("pid"), record.get("started", "")
        if not isinstance(pid, int) or not _alive(pid, started):
            continue
        live.append(
            SessionHolder(
                pid=pid,
                started=started,
                harness=record.get("harness", "unknown"),
                session_id=record.get("session_id", ""),
                command=record.get("command", "an interactive session"),
            )
        )
    return live


def ancestor_pids() -> frozenset[int]:
    """Every pid above this process, to a bounded depth.

    The self-exemption that needs no cooperation from either harness: a ``pr``
    run typed into a session has that session among its ancestors, whichever
    harness it is and whether or not that harness exports anything about
    itself.
    """
    found: set[int] = set()
    pid = os.getppid()
    for _ in range(_MAX_ANCESTRY):
        if pid <= 1 or pid in found:
            break
        found.add(pid)
        try:
            out = subprocess.run(
                ["ps", "-o", "ppid=", "-p", str(pid)],
                capture_output=True,
                text=True,
                timeout=timeouts.LOCAL,
            )
        except (OSError, subprocess.SubprocessError):
            break
        text = out.stdout.strip()
        if out.returncode != 0 or not text.isdigit():
            break
        pid = int(text)
    return frozenset(found)


def _own_session_pids() -> frozenset[int]:
    """The pids this process must never refuse on.

    Ancestry covers the ordinary case. ``CLAUDE_PID`` is added because Claude
    Code injects it into every Bash subprocess, which covers a tool that
    re-parents away from the session and so drops out of the walk.
    """
    pids = set(ancestor_pids())
    pids.add(os.getpid())
    claude = os.environ.get("CLAUDE_PID", "")
    if claude.isdigit():
        pids.add(int(claude))
    return frozenset(pids)


def held_by_others(worktree: Path) -> list[SessionHolder]:
    """Sessions editing ``worktree`` that are not the caller's own.

    A session running ``pr comments --fix`` from its own terminal must not
    refuse itself, so the caller's ancestry and its harness-declared session
    are exempt. Anything left is a second writer.
    """
    exempt = _own_session_pids()
    session_id = os.environ.get("PI_SESSION_ID", "")
    return [
        holder
        for holder in holders(worktree)
        if holder.pid not in exempt
        and not (session_id and holder.session_id == session_id)
    ]


def acquire(
    worktree: Path,
    *,
    pid: int,
    harness: str,
    session_id: str = "",
    command: str = "",
) -> bool:
    """Record that ``pid`` is editing ``worktree``. True when recorded.

    Idempotent for one pid: a second call replaces that pid's record rather
    than adding a second, so a harness that re-runs its start hook — Pi's
    ``/reload`` emits shutdown then start — does not accumulate entries. Dead
    records are pruned here, the only place the file is rewritten.
    """
    path = lock_path(worktree)
    if path is None:
        return False
    started = process_start(pid)
    if not started:
        return False

    keep = [h for h in holders(worktree) if h.pid != pid]
    keep.append(
        SessionHolder(
            pid=pid,
            started=started,
            harness=harness,
            session_id=session_id,
            command=command or harness,
        )
    )
    return _write(path, [_as_record(h) for h in keep])


def release(worktree: Path, pid: int) -> bool:
    """Drop ``pid``'s record. True when the file was rewritten."""
    path = lock_path(worktree)
    if path is None or not path.exists():
        return False
    keep = [h for h in holders(worktree) if h.pid != pid]
    if not keep:
        try:
            path.unlink()
        except OSError:
            return False
        return True
    return _write(path, [_as_record(h) for h in keep])


def _write(path: Path, lines: list[str]) -> bool:
    """Replace the record file atomically.

    A temp file and a rename, so a reader never sees a half-written record —
    a truncating open would show an empty lock to every pass that read it
    mid-write, which is the one moment the answer matters.

    Through ``serde.replace_file`` rather than a local ``os.replace``: this is
    JSONL rather than a JSON document, so ``write_json`` does not fit, but the
    rename is the same one and serde owns it. Four hand-rolled copies of this
    dance is what that owner replaced, and one of them had drifted into not
    being atomic at all.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text("".join(f"{line}\n" for line in lines))
        serde.replace_file(tmp, path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        return False
    return True
