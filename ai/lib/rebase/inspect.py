"""Rebase-state detection and ref reads.

Everything in this module is a read — it inspects ``.git/rebase-merge/`` or
``.git/rebase-apply/`` to tell the caller what state the worktree is in, but
never mutates it.
"""

# doc-group: platform

from __future__ import annotations

from pathlib import Path

from core import log, proc
from git import client as git_client

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

# What git prints, on stderr, for a conflict it resolved from its rerere cache.
# "Staged" is the `rerere.autoUpdate` wording and the one this run provokes;
# "Resolved" is what the same replay prints without it, kept so the parser still
# reads a worktree whose own config enabled rerere but not the auto-staging.
_RERERE_REPLAY_PREFIXES = (
    "Staged '",
    "Resolved '",
)
_RERERE_REPLAY_SUFFIX = "' using previous resolution."


# ── Git-dir resolution ─────────────────────────────────────────────────────


def git_dir(cwd: str) -> Path:
    """Return the ``.git`` directory path for the repo."""
    raw = git_client.out("rev-parse", "--git-dir", cwd=cwd)
    return Path(raw) if Path(raw).is_absolute() else Path(cwd) / raw


# ── Rebase-in-progress detection ────────────────────────────────────────────


def rebase_in_progress(cwd: str) -> bool:
    """Check if a rebase is currently in progress."""
    git_path = git_dir(cwd)
    return (
        (git_path / GIT_REBASE_MERGE_DIR).is_dir()
        or (git_path / GIT_REBASE_APPLY_DIR).is_dir()
    )


# ── Conflict and patch state ───────────────────────────────────────────────


def detect_conflicts(cwd: str) -> list[str]:
    """Return list of conflicted file paths."""
    return git_client.lines("diff", "--name-only", "--diff-filter=U", cwd=cwd)


def _replayed_path(line: str) -> str | None:
    """The path in one ``... using previous resolution.`` line, or None.

    Split out from the loop below so each holds one idea: this decides whether
    a single line is a replay line, and the caller decides what to do with the
    ones that are.
    """
    line = line.strip()
    if not line.endswith(_RERERE_REPLAY_SUFFIX):
        return None
    prefix = next(
        (p for p in _RERERE_REPLAY_PREFIXES if line.startswith(p)), None,
    )
    if prefix is None:
        return None
    # Falsy for `Staged '' using previous resolution.`, which the caller drops:
    # an empty path is a line that did not parse, not a file.
    return line[len(prefix):-len(_RERERE_REPLAY_SUFFIX)] or None


def rerere_replayed(output: str) -> list[str]:
    """Paths git says it resolved from its rerere cache, in the order printed.

    Parsed from git's own output because there is no plumbing that answers it:
    ``git rerere status`` reports what is *still* conflicted, and by the time
    this is asked the replayed file has been staged out of that set. The
    alternative is diffing the index before and after every step, which is more
    work to learn something git already said.

    Presentation output, so it is matched conservatively — a line that does not
    parse yields no path rather than a guess. Nothing is driven by the result:
    it feeds the trail and the run's counts, so a miss under-reports a saving
    and never changes what the rebase does.
    """
    parsed = (_replayed_path(line) for line in output.splitlines())
    return [path for path in parsed if path is not None]


def is_empty_patch(cwd: str) -> bool:
    """Check if the current rebase step has no changes (patch already applied upstream)."""
    staged = git_client.ok("diff", "--cached", "--quiet", cwd=cwd)
    return staged and git_client.ok("diff", "--quiet", cwd=cwd)


def rebase_head_info(cwd: str) -> tuple[str, str]:
    """Return ``(short_sha, subject)`` of the commit being rebased."""
    sha = git_client.out("rev-parse", "--short", "REBASE_HEAD", cwd=cwd)
    subject = git_client.out("log", "-1", "--format=%s", "REBASE_HEAD", cwd=cwd)
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


# ── Status helpers ──────────────────────────────────────────────────────────


def status_lines(cwd: str) -> list[str] | None:
    """Porcelain status lines for the worktree, or None when git could not read it.

    Ignored files are excluded.  None rather than an empty list, because the
    caller reads emptiness as a clean tree and gates on it: it stashes before
    the rebase.  A ``status`` killed by a timeout or a locked index must not be
    spelled the same way as a tree with nothing in it.
    """
    status = git_client.run("status", "--porcelain", cwd=cwd)
    if not status.ok:
        log.warn(proc.failure_message(f"git status failed in {cwd}", status))
        return None
    return [line for line in status.stdout.splitlines() if line.strip()]


# ── Ref inspection ──────────────────────────────────────────────────────────


def ref_exists(cwd: str, ref: str) -> bool:
    """Whether *ref* resolves in the repo at *cwd*."""
    return git_client.ok("rev-parse", "--verify", "--quiet", ref, cwd=cwd)


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
    return git_client.ok("merge-base", target_ref, "HEAD", cwd=cwd)
