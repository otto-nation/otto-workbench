"""Whether a recorded commit survived a history rewrite, and as which commit.

A rebase writes new commits and leaves the originals in the object database, so
a SHA recorded before one still resolves afterwards while naming a commit no
branch contains. Anything holding a recorded SHA across a rebase — a fix pass
that stamped one onto a row, a state file carrying one between runs — has to be
able to tell that apart from a commit that is simply not pushed yet.

Two questions, and the distinction between them is the whole module:

- :func:`rewritten_away` — *was it orphaned?* Ancestry, read for the one exit
  code that means orphaned rather than for truthiness.
- :class:`ReplayFinder` — *which commit carries it now?* git's own record of
  the rewrite first (`git.rewrites`), patch equivalence for a rewrite nobody
  recorded, and a typed answer either way — because "nothing carries it",
  "two things do" and "git could not say" each call for a different remedy.

`gh.landed` asks a neighbouring question of a whole branch — *is this work
upstream at all?* — and answers it with `git cherry`, which is cheaper and
all-or-nothing. Neither is a substitute for the other, and the boundary is worth
keeping: `landed` answers "is it there?", this answers "which one is it?".

Layer 2 rather than beside its first caller, so `gh`, `pr`, `fix` and `rebase`
can all reach it. The rebase subsystem is what *causes* the rewrites this
recovers from and should be reading the same answer.
"""

# doc-group: platform

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import core.proc
import core.timeouts
import git.client
import git.rewrites

# How many candidates one `log -p --no-walk | patch-id` is handed. The local
# bound is per call, so this is what keeps a large candidate set from timing
# out: a hundred commits that all touch one big generated file diff in about
# three seconds here, well inside it.
#
# ceiling: total time still grows with the number of upstream commits sharing a
# path with the orphan — about 4s for 150 commits touching a generated doc.
# This path only runs when git's rewrite record had no answer. Upgrade trigger:
# if that search is ever reported as a stall in `pr comments`, narrow the
# candidates by per-path line counts (`--numstat`) restricted to the orphan's
# paths before diffing them in full.
_REVS_PER_CALL = 100

# Marks a commit header in a `--name-only` listing. git quotes every control
# character in a path whatever `core.quotePath` says, so no path line can begin
# with one.
_HEADER = "\x1e"


def rewritten_away(wt_path: Path, sha: str) -> bool:
    """Whether HEAD's history no longer reaches *sha* — a rewrite orphaned it.

    Existence is the wrong question here, and asking it is the bug this answers.
    A rebase writes new commits and leaves the originals in the object database,
    so `cat-file` and `branch -r --contains` disagree in the worst possible way:
    the recorded SHA still resolves, and no branch anywhere contains it.

    Ancestry separates the two readings. Exit 1 is the orphan — reachable as an
    object, unreachable as history. Exit 0 means the commit is still on the
    branch and its absence from the remote is a genuine unpushed commit, which
    must keep holding the closeout. Any other exit is git declining to answer
    (an unresolvable SHA, a damaged object) and is read as "no evidence of a
    rewrite" on purpose: a question git could not answer must never be the
    reason a hold clears.

    That last reading is why this cannot be written with `git.client.ok`, which
    collapses every non-zero exit into one answer.
    """
    return git.client.run(
        "merge-base", "--is-ancestor", sha, "HEAD", cwd=wt_path,
    ).returncode == 1


@dataclass(frozen=True)
class _Answer:
    """What a git read produced: *ids* when it answered, *detail* when it did not."""

    ids: dict[str, list[str]] | None
    detail: str = ""


def _patch_ids(wt_path: Path, *revs: str) -> _Answer:
    """Patch id → the commits in *revs* carrying it, git's own equivalence test.

    The same one `git cherry` and `git rebase` use to recognise a commit they
    have already replayed, reached here through `patch-id` because those two
    answer "is it there?" and this has to answer "which one is it?".

    `--stable` so the id is a property of the change and not of whoever's diff
    settings produced it, and `--no-merges` because a merge has no single patch:
    `log -p` prints no diff for one, which would otherwise land every merge on a
    shared empty id. An empty commit drops out the same way, and neither can
    collide with anything, since `patch-id` emits no line for either.

    git failing is kept apart from git finding nothing: *ids* is None for the
    first and `{}` for the second, and a caller must not read one as the other.
    """
    patches = git.client.run(
        "log", "-p", "--no-merges", "--format=commit %H", *revs, cwd=wt_path,
    )
    if not patches.ok:
        return _Answer(None, patches.detail or f"git log exit {patches.returncode}")
    if not patches.stdout:
        return _Answer({})
    # Not git.client: this one reads a diff on stdin, and `run` deliberately
    # exposes no way to write to a child's input.
    ids = core.proc.run(["git", "patch-id", "--stable"], cwd=wt_path,
                   input_text=patches.stdout, timeout=core.timeouts.LOCAL)
    if not ids.ok:
        return _Answer(None, ids.detail or f"git patch-id exit {ids.returncode}")
    by_id: dict[str, list[str]] = {}
    for line in ids.stdout.splitlines():
        patch_id, _, commit = line.partition(" ")
        if patch_id and commit.strip():
            by_id.setdefault(patch_id, []).append(commit.strip())
    return _Answer(by_id)


class ReplayStatus(StrEnum):
    """What :meth:`ReplayFinder.find` learned about a rewritten commit."""

    FOUND = "found"
    # No commit on the branch carries the change: dropped, or reworked into a
    # different one.
    NONE = "none"
    # More than one does, and nothing says which.
    AMBIGUOUS = "ambiguous"
    # git did not answer — a timeout, an object it cannot read. Says nothing
    # about whether the work is there.
    UNKNOWN = "unknown"


class ReplaySource(StrEnum):
    """Which evidence a FOUND answer rests on."""

    # git's own report of the rewrite, recorded by the post-rewrite hook. Exact.
    REWRITE_LOG = "rewrite-log"
    # The same patch on the branch. Inferred.
    PATCH_ID = "patch-id"


@dataclass(frozen=True)
class Replay:
    """The answer for one recorded commit.

    *sha* is set only when FOUND, abbreviated the way the recorded SHAs are.
    *detail* says why when UNKNOWN, so a warning can name what git refused
    rather than guessing at it.
    """

    status: ReplayStatus
    sha: str = ""
    source: ReplaySource | None = None
    detail: str = ""

    @property
    def found(self) -> bool:
        return self.status is ReplayStatus.FOUND


class ReplayFinder:
    """Which commit now on the branch replays a commit a rewrite orphaned.

    One per worktree read, so that asking about every SHA a snapshot recorded
    reads the rewrite log once and walks a merge base's range once, however
    many SHAs share them. Nothing runs until :meth:`find` is called.

    Ask :func:`rewritten_away` first. A commit still on the branch is its own
    answer, and asked here it finds nothing: the patch search runs from the
    commit's own merge base, which for a commit on the branch is that commit,
    so the range excludes it.
    """

    def __init__(self, wt_path: Path) -> None:
        self._wt = wt_path
        self._rewrites: dict[str, list[str]] | None = None
        self._indexes: dict[str, _FileIndex] = {}

    def find(self, sha: str) -> Replay:
        """The replay of *sha*, from the best evidence that answers.

        git's record of the rewrite wins whenever it reaches a commit on the
        branch, because it is git saying which commit it wrote and not an
        inference from content. It also answers where content cannot: a
        conflict resolution that changed the hunks, and a fixup or squash, whose
        folded commits git lists against the commit they became.

        Patch equivalence answers the rest — a rewrite made with no hook to see
        it, or a commit the rebase dropped because its change was already
        upstream, whose only remaining copy is the upstream one. Content is the
        only field a rebase preserves that is also the commit's own; the header
        tells two fix commits apart no better than the static subject a pass
        commits under.
        """
        resolved = git.client.run(
            "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}", cwd=self._wt,
        )
        full = resolved.stdout.strip() if resolved.ok else ""
        if not full:
            # `--quiet` makes exit 1 the one "no such commit" answer; anything
            # else (a timeout, a git failure) says nothing about the commit.
            if resolved.returncode == 1:
                detail = f"{sha} no longer resolves to a commit"
            else:
                detail = resolved.detail or f"git rev-parse failed for {sha}"
            return Replay(ReplayStatus.UNKNOWN, detail=detail)
        from_log = self._from_rewrite_log(full)
        return from_log if from_log is not None else self._from_patch_id(full)

    def _from_rewrite_log(self, full: str) -> Replay | None:
        """Follow git's recorded rewrites from *full* to the commits on HEAD.

        A chain, because a commit rebased and then amended was rewritten twice.
        Descent stops at the first commit on the branch: a rewrite of a commit
        already on HEAD would be a commit that is not. None when the log reaches
        nothing on the branch, which leaves the question to patch matching.

        A commit whose ancestry git will not report is UNKNOWN rather than
        skipped: it may be the one that is on the branch, and patch matching
        from there could answer "none" for work that is there.
        """
        if self._rewrites is None:
            self._rewrites = git.rewrites.load(self._wt)
        on_head: list[str] = []
        seen = {full}
        frontier = list(self._rewrites.get(full, []))
        while frontier:
            commit = frontier.pop()
            if commit in seen:
                continue
            seen.add(commit)
            ancestry = _on_head(self._wt, commit)
            if ancestry.contained is None:
                return Replay(ReplayStatus.UNKNOWN, detail=ancestry.detail)
            if ancestry.contained:
                on_head.append(commit)
            else:
                frontier.extend(self._rewrites.get(commit, []))
        if not on_head:
            return None
        if len(on_head) > 1:
            return Replay(ReplayStatus.AMBIGUOUS, source=ReplaySource.REWRITE_LOG)
        return Replay(ReplayStatus.FOUND, _short(self._wt, on_head[0]),
                      ReplaySource.REWRITE_LOG)

    def _from_patch_id(self, full: str) -> Replay:
        """The single commit on the branch carrying *full*'s patch.

        Searched from the orphan's own merge base rather than from the upstream
        branch. The rewrite may have moved the branch onto a newer base, and the
        old one is the only commit both histories still agree on — and a commit
        the rebase dropped as already upstream survives only as that upstream
        copy, which a branch-only range would exclude.

        That range is every commit upstream gained since the old base, which
        after a large rebase is thousands. Diffing them all to hash each one
        cost more than the local bound allows, so they are narrowed first by
        path: a commit can only share a patch with the orphan if it touches a
        path the orphan does, since `patch-id` hashes the paths with the hunks.
        Listing paths diffs trees rather than blobs, and only the commits that
        survive are diffed in full — the shortcut git's own `--cherry-pick`
        takes. "Shares a path" rather than "touches the same paths" because it
        is the weaker claim, and so holds whichever git version decides what a
        mode-only change contributes to the hash. The listing is made without
        rename detection, so a rename lists both its paths and whether git
        pairs them cannot differ between the orphan's listing and a candidate's.

        Two answers is no answer. A patch that appears twice in range — applied,
        reverted, applied again; a cherry-pick duplicated by the rebase — leaves
        nothing to choose between, rather than a commit picked by list order.
        """
        orphan = _patch_ids(self._wt, "--no-walk", full)
        if orphan.ids is None:
            return Replay(ReplayStatus.UNKNOWN, detail=orphan.detail)
        if not orphan.ids:
            # An empty commit, or one `patch-id` emits no line for: there is no
            # change to look for, so "nothing carries it" would be a claim about
            # work that does not exist, and would advise restoring it.
            return Replay(ReplayStatus.UNKNOWN,
                          detail=f"{full[:12]} carries no change to match")
        # A single commit has at most one patch id.
        orphan_id = next(iter(orphan.ids))

        base = git.client.run("merge-base", full, "HEAD", cwd=self._wt)
        if base.returncode == 1:
            return Replay(ReplayStatus.NONE)
        if not base.ok or not base.stdout.strip():
            return Replay(ReplayStatus.UNKNOWN,
                          detail=base.detail or "git merge-base gave no answer")

        own = _FileIndex.build(self._wt, "--no-walk", full)
        index = self._index(base.stdout.strip())
        for read in (own, index):
            if read.detail:
                return Replay(ReplayStatus.UNKNOWN, detail=read.detail)
        candidates = index.sharing(own.paths_of(full))
        if not candidates:
            return Replay(ReplayStatus.NONE)

        matches: list[str] = []
        for batch in _batches(candidates, _REVS_PER_CALL):
            found = _patch_ids(self._wt, "--no-walk", *batch)
            if found.ids is None:
                return Replay(ReplayStatus.UNKNOWN, detail=found.detail)
            matches.extend(found.ids.get(orphan_id, []))
        if not matches:
            return Replay(ReplayStatus.NONE)
        if len(matches) > 1:
            return Replay(ReplayStatus.AMBIGUOUS, source=ReplaySource.PATCH_ID)
        return Replay(ReplayStatus.FOUND, _short(self._wt, matches[0]),
                      ReplaySource.PATCH_ID)

    def _index(self, base: str) -> _FileIndex:
        if base not in self._indexes:
            self._indexes[base] = _FileIndex.build(self._wt, f"{base}..HEAD")
        return self._indexes[base]


@dataclass(frozen=True)
class _FileIndex:
    """Each non-merge commit in a range → the paths it touches.

    *detail* is set, and *paths* empty, when git did not list the range.
    """

    paths: dict[str, frozenset[str]]
    detail: str = ""

    @classmethod
    def build(cls, wt_path: Path, *revs: str) -> _FileIndex:
        listing = git.client.run(
            "log", "--no-merges", "--name-only", "--no-renames",
            f"--format={_HEADER}%H", *revs,
            cwd=wt_path,
        )
        if not listing.ok:
            return cls({}, listing.detail or f"git log exit {listing.returncode}")
        paths: dict[str, set[str]] = {}
        current: set[str] | None = None
        # split, not splitlines: splitlines also breaks on the record separator
        # the header starts with, and would strip it off every header line.
        for line in listing.stdout.split("\n"):
            if line.startswith(_HEADER):
                current = paths.setdefault(line[len(_HEADER):].strip(), set())
            elif line and current is not None:
                current.add(line)
        return cls({commit: frozenset(p) for commit, p in paths.items()})

    def paths_of(self, commit: str) -> frozenset[str]:
        return self.paths.get(commit, frozenset())

    def sharing(self, wanted: frozenset[str]) -> list[str]:
        """The commits touching at least one of *wanted*."""
        if not wanted:
            return []
        return [commit for commit, paths in self.paths.items() if paths & wanted]


@dataclass(frozen=True)
class _Ancestry:
    """Whether HEAD contains a commit: *contained* is None when git won't say,
    and *detail* then says why."""

    contained: bool | None
    detail: str = ""


def _on_head(wt_path: Path, commit: str) -> _Ancestry:
    """Whether HEAD's history contains *commit*.

    Exit 0 is yes and exit 1 is no. Any other exit — a timeout, an unreadable
    object — is "unanswered" with the reason, kept apart from "no" the way
    :func:`rewritten_away` keeps them apart.
    """
    result = git.client.run(
        "merge-base", "--is-ancestor", commit, "HEAD", cwd=wt_path,
    )
    if result.returncode in (0, 1):
        return _Ancestry(result.returncode == 0)
    return _Ancestry(None, result.detail or f"git merge-base exit {result.returncode}")


def _short(wt_path: Path, commit: str) -> str:
    """*commit* abbreviated the way recorded SHAs are, or in full if git won't.

    Never empty: a FOUND answer carrying "" would overwrite the recorded SHA
    with nothing, which every reader downstream takes for "no commit".
    """
    return git.client.out("rev-parse", "--short", commit, cwd=wt_path) or commit


def _batches(items: list[str], size: int) -> Iterable[list[str]]:
    for start in range(0, len(items), size):
        yield items[start:start + size]


def replayed_commit(wt_path: Path, sha: str) -> str:
    """The commit now on the branch that replays *sha*, or "" when none does.

    The string form of :meth:`ReplayFinder.find`, for a caller with one SHA that
    does not need to tell "none", "ambiguous" and "unknown" apart. A caller
    that reports on the outcome needs the finder, because each of those calls
    for a different remedy.
    """
    return ReplayFinder(wt_path).find(sha).sha
