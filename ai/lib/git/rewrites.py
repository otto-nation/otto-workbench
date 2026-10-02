"""What git reports about a rewrite, and the record of it `pr` reads back.

A rebase knows exactly which commit it turned into which, and says so once: to
the `post-rewrite` hook, as one `<old> <new>` line per commit, after every
`commit --amend` and every `rebase`. That list covers what matching on content
cannot — a pick whose conflict resolution changed its hunks, and every commit a
`fixup`/`squash` folded, each listed against the commit it folded into. Then git
forgets it: `rebase-merge/rewritten-list` is deleted when the rebase finishes,
so the only moment the answer exists is inside that hook.

Not every line is a rewrite. git maps a commit it *dropped* — a `--skip`, a
resolution that emptied it — onto whatever came before it, so a dropped fix
commit would read as having become an unrelated commit already on the branch.
:func:`drops` is git's rule for telling those lines apart, and it needs the
rebase's own state (`onto`, the `done` todo), which exists only while the hook
runs. So the hook's one Python entry, `rebase.replay_audit rewritten`, reads
that state once, reports the drops that lost changes, and calls :func:`record`
with every line that is not a drop. When the state cannot be read it records
nothing, and `git.replay` falls back to patch matching — slower, and never
wrong in that direction.

The record lives in the repository's common git directory rather than in the
workbench state root. A commit belongs to the repository, every worktree of it
rewrites into the same history, and the entry should go when the repository
does; a machine-wide file would also let a busy repository evict a quiet one's
entries before anything read them.

Append-only text in git's own line format, so a rebase is one `write` with
`O_APPEND` and two rewrites finishing together cannot drop each other's lines —
the read-modify-write race `push_intent` accepts does not arise here. Only the
trim reads and replaces the file.
"""

# doc-group: platform

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import core.serde
import git.client

LOG_NAME = "workbench-rewrites"

# Todo commands that make a commit of their own. A `fixup`/`squash` folds into
# the previous one, so mapping to the previous new commit is expected there and
# is the signature of a drop everywhere else.
OWN_COMMIT_COMMANDS = frozenset({"pick", "p", "reword", "r", "edit", "e"})

# A trimmed log keeps the newest _KEEP_LINES lines, so a trim runs once per
# several thousand rewrites rather than on every one past the cap. The cap is a
# byte size, worked out from the longest line: twenty thousand SHA-256 lines, or
# about thirty-one thousand SHA-1 ones (which trim back to _KEEP_LINES, so about
# twenty-two thousand rewrites between trims). Either is a couple of megabytes
# and many big rebases deep — far older than any fix commit a closeout is still
# holding.
_MAX_LINES = 20_000
_KEEP_LINES = _MAX_LINES // 2
# The longest line the log writes: two SHA-256 object names, a space, a newline.
_MAX_LINE_BYTES = 2 * 64 + 2

_OBJECT_NAME = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
# A todo line's commit: full or abbreviated (`core.abbrev` allows down to 4).
_TODO_SHA = re.compile(r"[0-9a-f]{4,64}")
# Todo commands whose first argument is a commit, as opposed to a label name.
_COMMIT_ARG_COMMANDS = OWN_COMMIT_COMMANDS | {"fixup", "f", "squash", "s", "drop", "d"}


@dataclass(frozen=True)
class Rewrite:
    """One line of `post-rewrite`'s stdin: a commit and what it became."""

    old: str
    new: str

    @property
    def informative(self) -> bool:
        """A line git could have written, naming a commit that actually changed.

        A filter for what is written to and read back from the log, not for
        :func:`parse`: `parse` keeps every line, because :func:`drops` reads each
        one against the line before it.
        """
        return (self.old != self.new and bool(_OBJECT_NAME.fullmatch(self.old))
                and bool(_OBJECT_NAME.fullmatch(self.new)))


def parse(text: str) -> list[Rewrite]:
    """The `<old> <new>` lines git writes to `post-rewrite`'s stdin, in order.

    Every line is kept, a commit mapped onto itself included: :func:`drops`
    reads each line against the one before it, and a line left out would make
    the next drop look like a rewrite. A third field — git reserves one for
    "extra info" — is ignored.
    """
    rewrites = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) >= 2:
            rewrites.append(Rewrite(fields[0], fields[1]))
    return rewrites


def done_commands(state: Path) -> dict[str, str] | None:
    """Each commit sha the rebase in *state* processed, mapped to its todo command.

    None when the `done` file cannot be read — which is not the same answer as
    an empty one, and a caller that treated it as one would read every commit as
    having no command.
    """
    try:
        lines = (state / "done").read_text().splitlines()
    except OSError:
        return None
    commands = {}
    for line in lines:
        fields = line.split()
        # Only a hex word after a commit command is a commit: `label beef`,
        # `exec make` and `fixup -C <sha>` carry other first arguments, and
        # `command_for` prefix-matches the keys.
        if (len(fields) >= 2 and not line.startswith("#")
                and fields[0] in _COMMIT_ARG_COMMANDS
                and _TODO_SHA.fullmatch(fields[1])):
            commands[fields[1]] = fields[0]
    return commands


def command_for(commands: dict[str, str], commit: str) -> str:
    """*commit*'s todo command; `done` may abbreviate the sha.

    The longest matching sha wins when more than one is a prefix match: it is
    the more specific of the two, and the only tie-break available short of
    talking to git again.

    *commit* is always the full sha `post-rewrite` hands us, and `done` never
    abbreviates to something longer than that — so `sha.startswith(commit)`
    only ever agrees with `commit.startswith(sha)` on an exact match, which the
    first disjunct already covers. Kept anyway as the one test that stays
    correct if that assumption about `done` ever stops holding.
    """
    matches = [
        (sha, command) for sha, command in commands.items()
        if commit.startswith(sha) or sha.startswith(commit)
    ]
    if not matches:
        return ""
    return max(matches, key=lambda pair: len(pair[0]))[1]


def drops(
    rewrites: Sequence[Rewrite], onto: str, commands: dict[str, str],
) -> list[Rewrite]:
    """The lines in *rewrites* that report a commit git dropped, not rewrote.

    git maps a commit it dropped to whatever came before it — the previous new
    commit, or *onto* for the first — so a commit of its own mapping to its
    predecessor was dropped. A `fixup`/`squash` maps there too, by folding into
    it, which is why the todo command is part of the rule.
    """
    return split(rewrites, onto, commands).dropped


@dataclass(frozen=True)
class Split:
    """A rebase's lines, sorted by what git meant by each.

    *kept* are rewrites — a commit and the one it became. *dropped* are drops
    git reported as mapped onto their predecessor. A line in neither could not
    be told apart.
    """

    kept: list[Rewrite]
    dropped: list[Rewrite]


def split(rewrites: Sequence[Rewrite], onto: str, commands: dict[str, str]) -> Split:
    """*rewrites* sorted into rewrites and drops, each line in one place or neither.

    Lines are told apart by position, not by value, so a pair git reports twice
    stays as many lines as it was. A line whose commit the todo does not name
    is in neither list: with no command there is no telling a drop from a
    fold, and "not a drop" would be a guess rather than a finding.
    """
    kept, dropped = [], []
    previous = onto
    for rewrite in rewrites:
        command = command_for(commands, rewrite.old)
        if command and rewrite.new == previous and command in OWN_COMMIT_COMMANDS:
            dropped.append(rewrite)
        elif command:
            kept.append(rewrite)
        previous = rewrite.new
    return Split(kept, dropped)


def common_dir(cwd: str | Path) -> Path | None:
    """*cwd*'s repository's common git directory, absolute, or None.

    Resolved by hand rather than with `--path-format=absolute`, which git older
    than 2.31 does not know and echoes back as if it were an answer. A bare
    `--git-common-dir` is relative to the directory git ran in, which is *cwd*.
    """
    raw = git.client.out("rev-parse", "--git-common-dir", cwd=cwd)
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_absolute() else Path(cwd).resolve() / path


def record(common: Path, rewrites: Sequence[Rewrite]) -> None:
    """Append *rewrites* — drops already removed — to *common*'s log."""
    lines = [f"{r.old} {r.new}\n" for r in rewrites if r.informative]
    if not lines:
        return
    path = common / LOG_NAME
    payload = "".join(lines).encode("ascii")
    # One `write(2)` on an O_APPEND descriptor, so two rewrites finishing
    # together cannot interleave their lines. The return value is not checked: a
    # short write to a regular file (disk full, a signal) is accepted, and a
    # truncated tail is repaired by the newline check on the next `record`.
    fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        # A damaged log may end mid-line; appended to it, a new line would merge
        # with the stump and `load` would lose both.
        size = os.fstat(fd).st_size
        if size and os.pread(fd, 1, size - 1) != b"\n":
            payload = b"\n" + payload
        os.write(fd, payload)
    finally:
        os.close(fd)
    if path.stat().st_size > _MAX_LINES * _MAX_LINE_BYTES:
        _trim(path)


def _trim(path: Path) -> None:
    """Keep the newest :data:`_KEEP_LINES` lines, replacing the file atomically.

    ceiling: a rewrite appended between this read and the replace is lost,
    because the replace writes what was read. The cost is one rewrite falling
    back to patch matching, once per trim (see the cap above). Upgrade trigger: if a
    lost entry is ever observed, take an `fcntl.flock` on the log around the
    append and the trim.
    """
    # Bytes, so a stray non-ASCII byte in a damaged log cannot make every trim
    # raise and leave the file to grow without bound. `load` drops the line.
    kept = path.read_bytes().splitlines(keepends=True)[-_KEEP_LINES:]
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_bytes(b"".join(kept))
        core.serde.replace_file(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def load(wt_path: Path) -> dict[str, list[str]]:
    """Old commit → every commit it was rewritten into, oldest rewrite first.

    Each distinct answer once. Usually one, but a commit can be rewritten
    twice — a branch reset back to it and rebased again — and every answer is
    kept so a reader can see that. An absent or unreadable log is an empty
    map, which reads as "nothing recorded" and sends the caller to its
    fallback.
    """
    common = common_dir(wt_path)
    if common is None:
        return {}
    try:
        text = (common / LOG_NAME).read_text(encoding="ascii", errors="replace")
    except OSError:
        return {}
    rewrites: dict[str, list[str]] = {}
    for rewrite in parse(text):
        if not rewrite.informative:
            continue
        news = rewrites.setdefault(rewrite.old, [])
        # git can report one pair twice — a fixup's target is listed both as
        # picked and as folded into — and a repeat is not a second answer.
        if rewrite.new not in news:
            news.append(rewrite.new)
    return rewrites
