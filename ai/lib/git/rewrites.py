"""Which commit each rewritten commit became, as git itself reported it.

A rebase knows exactly which commit it turned into which, and says so once: to
the `post-rewrite` hook, as one `<old> <new>` line per rewritten commit, after
every `commit --amend` and every `rebase`. That list covers what matching on
content cannot — a pick whose conflict resolution changed its hunks, and every
commit a `fixup`/`squash` folded, each listed against the commit it folded into.
A commit the rebase dropped, by `drop` or because its change was already
upstream, is not listed, because it was not rewritten into anything.

Then git forgets it. `rebase-merge/rewritten-list` is deleted when the rebase
finishes, so the only moment the answer exists is inside that hook. The global
`git/hooks/post-rewrite` hands the list to :func:`record`, and :func:`load`
reads it back for `git.replay`, which asks this first and falls back to patch
equivalence only for a rewrite nobody recorded — one made before the hook was
installed, or in a repository whose own `core.hooksPath` keeps the global hooks
out.

The log lives in the repository's common git directory rather than in the
workbench state root. A commit belongs to the repository, every worktree of it
rewrites into the same history, and the entry should go when the repository
does; a machine-wide file would also let a busy repository evict a quiet one's
entries before anything read them.

Append-only text in git's own line format, so a rebase is one `write` with
`O_APPEND` and two rewrites finishing together cannot drop each other's lines —
the read-modify-write race `push_intent` accepts does not arise here. Only the
trim reads and replaces the file.

:func:`record` never raises. It runs from a hook in every repository on this
machine, and although git ignores `post-rewrite`'s exit status, a traceback
printed after every amend would be its own kind of breakage. A lost record costs
a fallback to patch matching, never a wrong answer.
"""

# doc-group: platform

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import git.client

LOG_NAME = "workbench-rewrites"

# A trimmed log keeps the newest half, so a trim runs once per _KEEP_LINES
# rewrites rather than on every one past the cap. Twenty thousand lines is a
# couple of megabytes and many big rebases deep — far older than any fix commit
# a closeout is still holding.
_MAX_LINES = 20_000
_KEEP_LINES = _MAX_LINES // 2
# The longest line the log writes: two SHA-256 object names, a space, a newline.
_MAX_LINE_BYTES = 2 * 64 + 2

_OBJECT_NAME = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")


@dataclass(frozen=True)
class Rewrite:
    """One line of git's report: *old* was rewritten into *new*."""

    old: str
    new: str


def parse(text: str) -> list[Rewrite]:
    """The `<old> <new>` lines in *text*, dropping anything else.

    git's own format is the only producer, so a line of any other shape is not a
    rewrite this can say anything true about. A third field — git reserves one
    for "extra info" — is ignored. A commit rewritten into itself carries no
    information and would only make :func:`load` walk in a circle.
    """
    pairs = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 2:
            continue
        old, new = fields[0], fields[1]
        if old != new and _OBJECT_NAME.fullmatch(old) and _OBJECT_NAME.fullmatch(new):
            pairs.append(Rewrite(old, new))
    return pairs


def record(common_dir: Path, text: str) -> None:
    """Append the rewrites git reported in *text* to *common_dir*'s log."""
    pairs = parse(text)
    if not pairs:
        return
    path = common_dir / LOG_NAME
    with path.open("a", encoding="ascii") as log:
        log.write("".join(f"{r.old} {r.new}\n" for r in pairs))
    if path.stat().st_size > _MAX_LINES * _MAX_LINE_BYTES:
        _trim(path)


def _trim(path: Path) -> None:
    """Keep the newest :data:`_KEEP_LINES` lines, replacing the file atomically.

    ceiling: a rewrite appended between this read and the replace is lost,
    because the replace writes what was read. The cost is one rewrite falling
    back to patch matching, once per _KEEP_LINES rewrites. Upgrade trigger: if a
    lost entry is ever observed, take an `fcntl.flock` on the log around the
    append and the trim.
    """
    kept = path.read_text(encoding="ascii").splitlines(keepends=True)[-_KEEP_LINES:]
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text("".join(kept), encoding="ascii")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def log_path(wt_path: Path) -> Path | None:
    """Where *wt_path*'s repository keeps its log, or None when git will not say.

    `--path-format=absolute` because a bare `--git-common-dir` answers relative
    to the process's cwd rather than to *wt_path* in an ordinary clone.
    """
    common = git.client.out(
        "rev-parse", "--path-format=absolute", "--git-common-dir", cwd=wt_path,
    )
    return Path(common) / LOG_NAME if common else None


def load(wt_path: Path) -> dict[str, list[str]]:
    """Old commit → every commit it was rewritten into, oldest rewrite first.

    Each distinct answer once. Usually one, but a commit can be rewritten twice — a branch reset back to it
    and rebased again — and every answer is kept so a reader can see that.
    An absent or unreadable log is an empty map, which reads as "nothing
    recorded" and sends the caller to its fallback.
    """
    path = log_path(wt_path)
    if path is None:
        return {}
    try:
        text = path.read_text(encoding="ascii", errors="replace")
    except OSError:
        return {}
    rewrites: dict[str, list[str]] = {}
    for rewrite in parse(text):
        news = rewrites.setdefault(rewrite.old, [])
        # git can report one pair twice — a fixup's target is listed both as
        # picked and as folded into — and a repeat is not a second answer.
        if rewrite.new not in news:
            news.append(rewrite.new)
    return rewrites


def main(argv: Sequence[str] | None = None) -> int:
    """Record a rewrite from the global `post-rewrite` hook.

    Always returns zero — see the module docstring for why a bookkeeping failure
    here must cost a warning and nothing else.
    """
    parser = argparse.ArgumentParser(description="Record what a rewrite became.")
    parser.add_argument("--git-dir", required=True, type=Path,
                        help="the repository's common git directory")
    ns = parser.parse_args(argv)
    try:
        record(ns.git_dir, sys.stdin.read())
    except Exception as exc:
        print(f"workbench: could not record this rewrite: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
