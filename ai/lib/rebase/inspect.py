"""Rebase-state detection and ref reads.

Almost everything in this module is a read — it inspects ``.git/rebase-merge/``
or ``.git/rebase-apply/`` to tell the caller what state the worktree is in.
``drop_requeued_stopped_pick`` is the exception: it removes a duplicate todo
entry git re-queued after a refused commit.
"""

# doc-group: platform

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import core.log
import core.proc
import core.serde
import git.client

# ── Constants ───────────────────────────────────────────────────────────────

GIT_REBASE_MERGE_DIR = "rebase-merge"
GIT_REBASE_APPLY_DIR = "rebase-apply"
GIT_REBASE_TODO = "git-rebase-todo"
GIT_REBASE_NEXT = "next"
GIT_REBASE_LAST = "last"

_PICK_COMMANDS = frozenset({
    "pick", "p", "reword", "r", "edit", "e",
    "squash", "s", "fixup", "f", "drop", "d",
})
_PICK_VERBS = frozenset({
    "pick", "p", "reword", "r", "edit", "e",
    "squash", "s", "fixup", "f",
})


@dataclass(frozen=True)
class _TodoHead:
    index: int
    sha: str


# ── Git-dir resolution ─────────────────────────────────────────────────────


def git_dir(cwd: str) -> Path:
    """Return the ``.git`` directory path for the repo."""
    raw = git.client.out("rev-parse", "--git-dir", cwd=cwd)
    return Path(raw) if Path(raw).is_absolute() else Path(cwd) / raw


# ── Rebase-in-progress detection ────────────────────────────────────────────


def rebase_in_progress(cwd: str) -> bool:
    """Check if a rebase is currently in progress."""
    git_path = git_dir(cwd)
    return (
        (git_path / GIT_REBASE_MERGE_DIR).is_dir()
        or (git_path / GIT_REBASE_APPLY_DIR).is_dir()
    )


def rebase_orig_head(cwd: str) -> str:
    """The branch tip an in-progress rebase started from, or "" when none is recorded.

    git keeps it as `orig-head` in the rebase state directory: the local tip
    when the rebase began, which is what undoing the rebase restores.
    """
    git_path = git_dir(cwd)
    for state_dir in (GIT_REBASE_MERGE_DIR, GIT_REBASE_APPLY_DIR):
        try:
            return (git_path / state_dir / "orig-head").read_text().strip()
        except OSError:
            continue
    return ""


# ── Conflict and patch state ───────────────────────────────────────────────


def detect_conflicts(cwd: str) -> list[str]:
    """Return list of conflicted file paths."""
    return git.client.lines("diff", "--name-only", "--diff-filter=U", cwd=cwd)


def is_empty_patch(cwd: str) -> bool:
    """Check if the current rebase step has no changes (patch already applied upstream)."""
    staged = git.client.ok("diff", "--cached", "--quiet", cwd=cwd)
    return staged and git.client.ok("diff", "--quiet", cwd=cwd)


def _expected_paused_head(merge_dir: Path) -> str:
    """HEAD git leaves at a paused step: last rewritten new-sha, else onto."""
    last = ""
    try:
        text = (merge_dir / "rewritten-list").read_text()
    except OSError:
        text = ""
    for parts in (line.split() for line in text.splitlines()):
        last = parts[1] if len(parts) >= 2 else last
    if last:
        return last
    try:
        return (merge_dir / "onto").read_text().strip()
    except OSError:
        return ""


def step_committed_manually(cwd: str) -> bool:
    """Whether the paused empty tree is a hand-made commit, not an upstream patch.

    After `git commit` during a conflict, the index matches HEAD so
    ``is_empty_patch`` is true — the same reading as a patch already on the
    onto commit. ``stopped-sha`` is still set, and HEAD has moved past the
    paused parent (onto, or the last rewritten new-sha).
    """
    if not is_empty_patch(cwd):
        return False
    merge_dir = git_dir(cwd) / GIT_REBASE_MERGE_DIR
    if not (merge_dir / "stopped-sha").is_file():
        return False
    expected = _expected_paused_head(merge_dir)
    head = git.client.out("rev-parse", "--verify", "--quiet", "HEAD", cwd=cwd)
    if not expected or not head or head == expected:
        return False
    return git.client.ok("merge-base", "--is-ancestor", expected, head, cwd=cwd)


def rebase_head_info(cwd: str) -> tuple[str, str]:
    """Return ``(short_sha, subject)`` of the commit being rebased."""
    sha = git.client.out("rev-parse", "--short", "REBASE_HEAD", cwd=cwd)
    subject = git.client.out("log", "-1", "--format=%s", "REBASE_HEAD", cwd=cwd)
    return sha, subject


def remaining_rebase_commits(cwd: str) -> int:
    """Count remaining commits in the rebase todo."""
    git_path = git_dir(cwd)
    todo = git_path / GIT_REBASE_MERGE_DIR / GIT_REBASE_TODO
    if todo.exists():
        return sum(
            1 for line in todo.read_text().splitlines()
            if line.strip() and not line.startswith("#")
            and line.split()[0] in _PICK_COMMANDS
        )
    next_file = git_path / GIT_REBASE_APPLY_DIR / GIT_REBASE_NEXT
    last_file = git_path / GIT_REBASE_APPLY_DIR / GIT_REBASE_LAST
    if next_file.exists() and last_file.exists():
        try:
            return int(last_file.read_text().strip()) - int(next_file.read_text().strip())
        except ValueError:
            return 0
    return 0


def _first_todo_pick(lines: list[str]) -> _TodoHead | None:
    """The first actionable todo line, if it is a pick-like command."""
    for idx, line in enumerate(lines):
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split()
        if parts[0] in _PICK_VERBS and len(parts) >= 2:
            return _TodoHead(index=idx, sha=parts[1])
        return None
    return None


def drop_requeued_stopped_pick(cwd: str) -> str:
    """Remove a leading todo pick that git re-queued for the stopped commit.

    After a refused ``prepare-commit-msg``, git's ``reschedule_last_command``
    prepends the stopped pick onto ``git-rebase-todo`` while ``stopped-sha``
    still names that commit. ``rebase --continue`` would then pick it twice.
    Returns the full stopped sha when a duplicate was dropped, else "".
    """
    if not cwd or not Path(cwd).is_dir():
        return ""
    merge_dir = git_dir(cwd) / GIT_REBASE_MERGE_DIR
    todo_path = merge_dir / GIT_REBASE_TODO
    try:
        stopped = (merge_dir / "stopped-sha").read_text().strip()
        lines = todo_path.read_text().splitlines()
    except OSError:
        return ""
    if not stopped:
        return ""
    head = _first_todo_pick(lines)
    if head is None:
        return ""
    stopped_full = git.client.out(
        "rev-parse", "--verify", "--quiet", f"{stopped}^{{commit}}", cwd=cwd,
    )
    todo_full = git.client.out(
        "rev-parse", "--verify", "--quiet", f"{head.sha}^{{commit}}", cwd=cwd,
    )
    if not stopped_full or stopped_full != todo_full:
        return ""
    kept = [line for i, line in enumerate(lines) if i != head.index]
    tmp = todo_path.with_suffix(".tmp")
    tmp.write_text("\n".join(kept) + ("\n" if kept else ""))
    core.serde.replace_file(tmp, todo_path)
    return stopped_full


# ── Status helpers ──────────────────────────────────────────────────────────


def status_lines(cwd: str) -> list[str] | None:
    """Porcelain status lines for the worktree, or None when git could not read it.

    Ignored files are excluded.  None rather than an empty list, because the
    caller reads emptiness as a clean tree and gates on it: it stashes before
    the rebase.  A ``status`` killed by a timeout or a locked index must not be
    spelled the same way as a tree with nothing in it.
    """
    status = git.client.run("status", "--porcelain", cwd=cwd)
    if not status.ok:
        core.log.warn(core.proc.failure_message(f"git status failed in {cwd}", status))
        return None
    return [line for line in status.stdout.splitlines() if line.strip()]


# ── Ref inspection ──────────────────────────────────────────────────────────


def ref_exists(cwd: str, ref: str) -> bool:
    """Whether *ref* resolves in the repo at *cwd*."""
    return git.client.ok("rev-parse", "--verify", "--quiet", ref, cwd=cwd)


def shares_history(cwd: str, *, target_ref: str) -> bool:
    """Whether HEAD and ``target_ref`` have any commit in common.

    A ref that does not resolve counts as shared history rather than unrelated:
    ``git merge-base`` fails the same way for a typo'd ``--onto`` and for a
    base branch the fetch never brought down, and refusing those as unrelated
    history sends the operator after a root they do not have.  Left to git,
    the rebase fails with the ref's own error text instead.
    """
    if not ref_exists(cwd, target_ref):
        return True
    return git.client.ok("merge-base", target_ref, "HEAD", cwd=cwd)
