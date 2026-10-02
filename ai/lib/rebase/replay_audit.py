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
  whose changes are not in the result — and, since it is reading the same
  map and the same rebase state, writes `git.rewrites`' record of every line
  that is not a drop;
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
    python3 -I -c '...' <ai/lib> rewritten amend    < old-new pairs

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
import git.rewrites
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
        base_text, target_text, replayed_text, resolved_text = texts
        audits.append(rebase.survival.audit(
            path, base=base_text, target=target_text,
            replayed=replayed_text, resolved=resolved_text,
        ))
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


def dropped_commits(
    cwd: str, drops: Sequence[git.rewrites.Rewrite],
) -> tuple[CommitAudit, ...]:
    """The commits among *drops* whose changes are not in the result.

    Which lines are drops is git's rule, and `git.rewrites.drops` owns it. Most
    drops are right: the change was already upstream, and then the
    predecessor's tree holds it and the audit finds nothing lost. What is
    reported is a drop whose changes are absent.
    """
    dropped = []
    for rewrite in drops:
        audit = _dropped(cwd, rewrite)
        if audit is not None:
            dropped.append(audit)
    return tuple(dropped)


def _dropped(cwd: str, rewrite: git.rewrites.Rewrite) -> CommitAudit | None:
    """*rewrite*'s audit when git dropped its commit and lost changes doing so.

    A merge commit is skipped, its base being ambiguous, as `replaying` skips
    one.
    """
    old, new = rewrite.old, rewrite.new
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
    """Report a rebase's lossy drops, and record every rewrite that is not a drop.

    Both read the one copy of git's map, and both need the rebase state that is
    gone once this hook returns — which is why the record is written from here
    rather than by a second interpreter. An amend drops nothing, so all of its
    lines are rewrites. A rebase whose state cannot be read records nothing:
    with no way to tell a drop from a rewrite, a dropped commit would be
    recorded as having become its predecessor.
    """
    rewrites = git.rewrites.parse(stdin)
    if kind != "rebase":
        _record(cwd, rewrites)
        return 0
    state = rebase.inspect.git_dir(cwd) / rebase.inspect.GIT_REBASE_MERGE_DIR
    try:
        onto = (state / "onto").read_text().strip()
    except OSError:
        return 0
    commands = git.rewrites.done_commands(state)
    drops = git.rewrites.drops(rewrites, onto, commands)
    _record(cwd, [r for r in rewrites if r not in drops])
    dropped = dropped_commits(cwd, drops)
    if dropped:
        try:
            orig_head = (state / "orig-head").read_text().strip()
        except OSError:
            orig_head = ""
        print(render_dropped(dropped, orig_head), file=sys.stderr)
    return 0


def _record(cwd: str, rewrites: Sequence[git.rewrites.Rewrite]) -> None:
    """Write the rewrite record, failing open on its own.

    Kept apart from the audit's failure handling so a record that cannot be
    written never costs the report of a drop that lost changes.
    """
    try:
        common = git.rewrites.common_dir(cwd)
        if common is not None:
            git.rewrites.record(common, rewrites)
    except OSError as exc:
        core.log.warn(f"could not record this rewrite for `pr`: {exc}")


def main(argv: Sequence[str] | None = None) -> int:
    """Hook entry point — see the module docstring for the exit codes."""
    parser = argparse.ArgumentParser(description="Audit a replay for discarded changes.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("commit", help="audit the staged resolution of the commit under replay")
    rewritten = sub.add_parser("rewritten", help="report lossy drops and record rewrites (post-rewrite)")
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
