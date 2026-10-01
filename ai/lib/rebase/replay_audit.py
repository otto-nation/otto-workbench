"""Audit a replay for changes its conflict resolution threw away.

`survival` judges one file from four texts. This module finds the texts: the
commit a rebase or cherry-pick is stopped on, its parent, the tip it is
replayed onto, and the resolution staged in the index. It also covers the
case no commit hook sees: a resolution that empties a commit makes
`rebase --continue` drop it without committing anything, and `rebase --skip`
does the same on request, so the only record either happened is the old→new
map git hands `post-rewrite`.

Three callers, one judgement:

* the global `prepare-commit-msg` hook refuses the commit that concludes a
  conflicted replay step when it discards a clean change — `-n` does not skip
  that hook, and `rebase --continue` runs it;
* the global `post-rewrite` hook reports commits a finished rebase dropped
  whose changes are not in the result;
* `pr rebase` audits its own resolutions before it continues, so a refusal
  arrives as a paused rebase with the files named rather than as a hook
  failure it would have to interpret.

Files the rebase tool itself resolves by taking one side — lockfiles it
regenerates, files marked generated — are exempt, and that list is
`conflicts.classify_conflict`'s, so the two cannot disagree about which files
may be taken whole.

Run as a hook entry point::

    python3 -I -c '...' <ai/lib> commit
    python3 -I -c '...' <ai/lib> rewritten rebase   < old-new pairs

Exit codes: 0 when nothing was refused (including when the audit could not
run — a bug here must never cost somebody a commit), ``REFUSED_EXIT`` when the
commit must not be made.
"""

# doc-group: platform

from __future__ import annotations

import argparse
import os
import shlex
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import core.log
import git.client
import rebase.conflicts
import rebase.inspect
import rebase.survival
import rebase.types

# Distinct from 1, which is what an uncaught exception exits with, and from 2,
# argparse's: the hook refuses on this code alone and fails open on any other,
# so a crash in the audit reads as "could not check", never as "refused".
REFUSED_EXIT = 10

# Set to 1 to commit a resolution the audit refuses, having checked that every
# listed change really is superseded by the other side.
ALLOW_ENV = "WORKBENCH_ALLOW_DROPPED_CHANGES"

# The ref naming the commit under replay, and the command that continues it.
_REPLAY_HEADS = (("REBASE_HEAD", "git rebase --continue"),
                 ("CHERRY_PICK_HEAD", "git cherry-pick --continue"))

# An `edit` stop leaves REBASE_HEAD set too, with the commit already made, and
# whatever is committed there is the user's restructuring — a split commit's
# first half lacks the second by design. Only `edit` writes this file.
_EDIT_STOP_MARKER = "amend"

# Todo commands that make a commit of their own. A `fixup`/`squash` folds into
# the previous one, so mapping to the previous new commit is expected there and
# is the signature of a drop everywhere else.
_OWN_COMMIT_COMMANDS = frozenset({"pick", "p", "reword", "r", "edit", "e"})

# git's empty tree, for a root commit's parent. `git hash-object -t tree
# /dev/null` in every SHA-1 repository.
_EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

# The rev reading a path from the index's resolved stage 0 — see git.client.blob.
_INDEX = ""

# Strategies under which the rebase tool takes a whole side on purpose.
_WHOLE_SIDE_STRATEGIES = frozenset({
    rebase.types.ConflictStrategy.REGENERATE,
    rebase.types.ConflictStrategy.ACCEPT_THEIRS,
})


@dataclass(frozen=True)
class Replay:
    """The commit a rebase or cherry-pick is stopped on."""
    commit: str
    # The base every change is measured from; the empty tree for a root commit.
    parent: str
    continue_command: str


@dataclass(frozen=True)
class Rewrite:
    """One line of `post-rewrite`'s stdin: a commit and what it became."""
    old: str
    new: str


@dataclass(frozen=True)
class CommitAudit:
    """One replayed commit's files, as far as anything was lost from them."""
    commit: str
    subject: str
    files: tuple[rebase.survival.FileAudit, ...]

    @property
    def refused(self) -> tuple[rebase.survival.FileAudit, ...]:
        return tuple(f for f in self.files if f.blocking)

    @property
    def flagged(self) -> tuple[rebase.survival.FileAudit, ...]:
        return tuple(f for f in self.files if f.advisory)

    @property
    def ok(self) -> bool:
        return not self.refused


def override_requested() -> bool:
    """Whether the operator has asked to commit a refused resolution anyway."""
    return os.environ.get(ALLOW_ENV) == "1"


# ── Reading the replay ──────────────────────────────────────────────────────

def replaying(cwd: str) -> Replay | None:
    """The commit a conflicted replay step is about to conclude, or None.

    None for an `edit` stop, for a merge commit (whose base is ambiguous), and
    when nothing is being replayed at all.

    REBASE_HEAD alone is not evidence of a rebase: git leaves it behind when a
    rebase finishes on a step that had stopped for a conflict, and only
    `--abort` clears it. Read as live, it would audit every later ordinary
    commit against a long-finished replay — and outrank a cherry-pick's
    CHERRY_PICK_HEAD — so it counts only while the rebase state directory is
    there.
    """
    for ref, continue_command in _REPLAY_HEADS:
        if ref == "REBASE_HEAD" and not rebase.inspect.rebase_in_progress(cwd):
            continue
        commit = git.client.out("rev-parse", "-q", "--verify", f"{ref}^{{commit}}", cwd=cwd)
        if not commit:
            continue
        if ref == "REBASE_HEAD" and _edit_stop(cwd):
            return None
        parents = git.client.out("rev-list", "--parents", "-n", "1", commit, cwd=cwd).split()[1:]
        if len(parents) > 1:
            return None
        return Replay(commit, parents[0] if parents else _EMPTY_TREE, continue_command)
    return None


def _edit_stop(cwd: str) -> bool:
    state = rebase.inspect.git_dir(cwd) / rebase.inspect.GIT_REBASE_MERGE_DIR
    return (state / _EDIT_STOP_MARKER).exists()


def _changed_paths(cwd: str, old: str, new: str) -> list[str]:
    """Every path *new* changed relative to *old*, renames split into delete and add."""
    r = git.client.run("diff", "--name-only", "--no-renames", "-z", old, new, cwd=cwd)
    return [p for p in r.stdout.split("\0") if p] if r.ok else []


def _exemption(cwd: str, path: str, texts: Sequence[str | None]) -> str:
    """Why *path* is not audited, or empty when it is."""
    if any(text is not None and "\0" in text for text in texts):
        return "binary"
    plan = rebase.conflicts.classify_conflict(path, Path(cwd) / path, cwd)
    if plan.strategy in _WHOLE_SIDE_STRATEGIES:
        return f"resolved whole by design ({plan.strategy})"
    if plan.strategy is rebase.types.ConflictStrategy.BINARY_ERROR:
        return "binary"
    return ""


def audit_paths(
    cwd: str, paths: Sequence[str], *,
    base: str, target: str, replayed: str, resolved: str,
) -> tuple[rebase.survival.FileAudit, ...]:
    """Each path's audit between the four revisions named."""
    audits = []
    for path in paths:
        texts = [git.client.blob(rev, path, cwd=cwd) for rev in (base, target, replayed, resolved)]
        skipped = _exemption(cwd, path, texts)
        if skipped:
            audits.append(rebase.survival.FileAudit(path, skipped=skipped))
            continue
        b, u, t, r = texts
        audits.append(rebase.survival.audit(path, base=b, target=u, replayed=t, resolved=r))
    return tuple(audits)


def _subject(cwd: str, commit: str) -> str:
    return git.client.out("log", "-1", "--format=%s", commit, cwd=cwd)


# ── The two audits ──────────────────────────────────────────────────────────

def audit_replay(cwd: str, replay: Replay | None = None) -> CommitAudit | None:
    """The staged resolution of the commit under replay, or None when there is none."""
    replay = replay if replay is not None else replaying(cwd)
    if replay is None:
        return None
    paths = _changed_paths(cwd, replay.parent, replay.commit)
    files = audit_paths(
        cwd, paths, base=replay.parent, target="HEAD",
        replayed=replay.commit, resolved=_INDEX,
    )
    return CommitAudit(replay.commit, _subject(cwd, replay.commit), files)


def parse_rewrites(text: str) -> list[Rewrite]:
    """The ``<old> <new>`` lines git writes to `post-rewrite`'s stdin, in order."""
    rewrites = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) >= 2:
            rewrites.append(Rewrite(fields[0], fields[1]))
    return rewrites


def _done_commands(state: Path) -> dict[str, str]:
    """Each commit sha the rebase processed, mapped to its todo command."""
    try:
        lines = (state / "done").read_text().splitlines()
    except OSError:
        return {}
    commands = {}
    for line in lines:
        fields = line.split()
        if len(fields) >= 2 and not line.startswith("#"):
            commands[fields[1]] = fields[0]
    return commands


def _command_for(commands: dict[str, str], commit: str) -> str:
    """*commit*'s todo command; `done` may abbreviate the sha.

    The longest matching sha wins when more than one is a prefix match: it is
    the more specific of the two, and the only tie-break available short of
    talking to git again.
    """
    matches = [
        (sha, command) for sha, command in commands.items()
        if commit.startswith(sha) or sha.startswith(commit)
    ]
    if not matches:
        return ""
    return max(matches, key=lambda pair: len(pair[0]))[1]


def dropped_commits(cwd: str, rewrites: Sequence[Rewrite]) -> tuple[CommitAudit, ...]:
    """Commits a finished rebase left out whose changes are not in the result.

    git maps a commit it dropped to whatever came before it — the previous new
    commit, or `onto` for the first — so a commit of its own mapping to its
    predecessor was dropped. Most such drops are right: the change was already
    upstream, and then the predecessor's tree holds it and the audit finds
    nothing lost. What is reported is a drop whose changes are absent.
    """
    state = rebase.inspect.git_dir(cwd) / rebase.inspect.GIT_REBASE_MERGE_DIR
    commands = _done_commands(state)
    try:
        previous = (state / "onto").read_text().strip()
    except OSError:
        return ()

    dropped = []
    for rewrite in rewrites:
        audit = _dropped(cwd, commands, rewrite, previous)
        if audit is not None:
            dropped.append(audit)
        previous = rewrite.new
    return tuple(dropped)


def _dropped(
    cwd: str, commands: dict[str, str], rewrite: Rewrite, previous: str,
) -> CommitAudit | None:
    """*rewrite*'s audit when git dropped its commit and lost changes doing so.

    A drop is a commit of its own mapped onto whatever came before it. A merge
    commit is skipped, its base being ambiguous, as `replaying` skips one.
    """
    old, new = rewrite.old, rewrite.new
    if new != previous or _command_for(commands, old) not in _OWN_COMMIT_COMMANDS:
        return None
    parents = git.client.out("rev-list", "--parents", "-n", "1", old, cwd=cwd).split()[1:]
    if len(parents) > 1:
        return None
    parent = parents[0] if parents else _EMPTY_TREE
    files = audit_paths(
        cwd, _changed_paths(cwd, parent, old),
        base=parent, target=new, replayed=old, resolved=new,
    )
    audit = CommitAudit(old, _subject(cwd, old), files)
    return None if audit.ok else audit


# ── Reporting ───────────────────────────────────────────────────────────────

def _file_lines(files: Sequence[rebase.survival.FileAudit], *, refused: bool) -> list[str]:
    lines = []
    for f in files:
        losses = f.blocking if refused else f.advisory
        lines.append(f"  {f.path}")
        lines.extend(f"    - {loss.describe()}" for loss in losses)
    return lines


def render_refusal(audit: CommitAudit, continue_command: str) -> str:
    """Why the commit concluding a replay step was refused, and the way out."""
    paths = " ".join(shlex.quote(f.path) for f in audit.refused)
    return "\n".join([
        f"✗ This resolution throws away changes — refusing to commit "
        f"{git.client.abbrev(audit.commit)} {audit.subject}",
        *_file_lines(audit.refused, refused=True),
        "",
        "  A whole-file `git checkout --ours`/`--theirs`, or a file copied from either",
        "  side, replaces every hunk — including the ones git had merged cleanly. Bring",
        "  the conflict back and resolve it hunk by hunk instead:",
        f"    git checkout -m -- {paths}",
        "  If the change was made by a script, re-run the script on the restored file.",
        "  If each listed change really is superseded by the other side:",
        f"    {ALLOW_ENV}=1 {continue_command}",
    ])


def render_advisory(audit: CommitAudit) -> str:
    """The regions resolved to one side, which may be intended."""
    return "\n".join([
        "⚠ Resolved to one side only — confirm the other side's change is meant to go:",
        *_file_lines(audit.flagged, refused=False),
    ])


def render_dropped(dropped: Sequence[CommitAudit], orig_head: str) -> str:
    """The commits a rebase dropped with their changes, and how to get them back."""
    lines = [f"✗ This rebase dropped {len(dropped)} commit(s) whose changes are not in the result:"]
    for audit in dropped:
        lines.append(f"  {git.client.abbrev(audit.commit)} {audit.subject}")
        lines.extend(f"  {line}" for line in _file_lines(audit.refused, refused=True))
    lines += [
        "",
        "  A conflict resolved to nothing, or a `rebase --skip`, drops the commit with",
        "  no message. Re-apply what was lost, or undo the whole rebase:",
        *[f"    git cherry-pick {git.client.abbrev(a.commit)}" for a in dropped],
    ]
    if orig_head:
        lines.append(f"    git reset --hard {git.client.abbrev(orig_head)}   # the tip before the rebase")
    return "\n".join(lines)


# ── Entry point ─────────────────────────────────────────────────────────────

def _commit(cwd: str) -> int:
    replay = replaying(cwd)
    if replay is None:
        return 0
    audit = audit_replay(cwd, replay)
    if audit is None:
        return 0
    if audit.flagged:
        print(render_advisory(audit), file=sys.stderr)
    if audit.ok:
        return 0
    print(render_refusal(audit, replay.continue_command), file=sys.stderr)
    if override_requested():
        core.log.warn(f"{ALLOW_ENV}=1 — committing anyway.")
        return 0
    return REFUSED_EXIT


def _rewritten(cwd: str, kind: str, stdin: str) -> int:
    if kind != "rebase":
        return 0
    dropped = dropped_commits(cwd, parse_rewrites(stdin))
    if dropped:
        state = rebase.inspect.git_dir(cwd) / rebase.inspect.GIT_REBASE_MERGE_DIR
        try:
            orig_head = (state / "orig-head").read_text().strip()
        except OSError:
            orig_head = ""
        print(render_dropped(dropped, orig_head), file=sys.stderr)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Hook entry point — see the module docstring for the exit codes."""
    parser = argparse.ArgumentParser(description="Audit a replay for discarded changes.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("commit", help="audit the staged resolution of the commit under replay")
    rewritten = sub.add_parser("rewritten", help="report commits a rebase dropped (post-rewrite)")
    rewritten.add_argument("kind", help="post-rewrite's first argument: rebase or amend")
    ns = parser.parse_args(argv)
    cwd = os.getcwd()
    try:
        if ns.command == "commit":
            return _commit(cwd)
        return _rewritten(cwd, ns.kind, sys.stdin.read())
    except Exception as exc:
        # Fail open: this runs inside commit hooks in every repository on the
        # machine, and a bug in the audit must cost a warning, not a commit.
        core.log.warn(f"replay audit could not run: {exc}")
        return 0
