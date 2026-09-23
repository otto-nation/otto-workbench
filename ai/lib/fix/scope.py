"""What a fix pass changed, so its commit can be scoped to exactly that.

A fix pass runs an agent with edit permissions in a worktree it does not own.
Committing the whole tree afterwards sweeps in whatever else was dirty — a
build artifact, an operator's work in progress, another tool's scratch file —
and the pass then pushes it. Naming the files instead is what keeps a commit to
the work the pass is accountable for.

The set is read twice, before and after the agent, and the difference is the
pass's own doing. It is not the set of files the pass was *asked* about: an
agent fixing a finding in one file routinely edits its test, its fixture, or
the caller that broke — and no pass reports the files it actually touched.
Asking git afterwards is the only account of that there is.

None and the empty set are different answers and both are returned. Empty says
the agent changed nothing, so there is nothing to commit. None says the
worktree could not be read, so the pass cannot tell its own work from what was
already there — which is the one case where committing nothing is right and
committing everything is how unreviewed content reaches a branch.
"""

# doc-group: pipeline

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core import log, proc
from git import client as git_client


@dataclass(frozen=True)
class BatchScope:
    """What one agent invocation changed, as the engine observed it.

    The counterpart of a batch's outcomes: those are what the agent said it
    did, this is what the worktree says happened while it was running. Holding
    them apart is the point — a fix pass cannot watch an agent work, and until
    these two are compared the only account of the pass is the one the agent
    wrote about itself.

    Scoped to one invocation rather than to the pass, because attribution is by
    path and a path is only as informative as the number of items it could
    belong to. A thirty-item pass observed once attributes every file to all
    thirty; observed per batch, the same file narrows to the items that batch
    actually carried. `_chunks` already splits the work, so this costs two git
    calls per batch and no new structure.

    `files` empty is a batch that changed nothing, which is a fact worth
    carrying rather than an absence: an agent that ticked `fixed` in a batch
    with an empty diff made a claim the tree does not support anywhere.

    `known` is False when the worktree could not be read. It is not the same as
    an empty `files` and must never be treated as one — an unreadable worktree
    is a batch about which nothing is known, and reporting that as "changed
    nothing" would turn a failed git call into evidence against the agent.
    """

    files: frozenset[str] = frozenset()
    known: bool = True

    def touched(self, path: str) -> bool:
        """Whether this batch changed `path`.

        False for an unknown scope, and callers must check `known` first where
        the difference matters: "we did not see it change" and "we could not
        look" are the same answer here, and only the caller knows which of the
        two its question can survive.
        """
        return bool(path) and path in self.files


# A scope for a batch nothing could be observed about. Named rather than
# spelled out at each site so the unknown case cannot be written as an empty
# `files` with `known` left at its default by mistake.
UNKNOWN_SCOPE = BatchScope(known=False)


def changed_files(wt_path: str | Path) -> set[str] | None:
    """The changed files (staged, unstaged, and untracked), or None on failure.

    `--exclude-standard` keeps gitignored paths out of the snapshot, so they
    cannot reach the difference and cannot be staged from it.

    `run` rather than `lines`, which returns `[]` on a non-zero exit: a path
    missing from a snapshot is a path the pass never commits, so a read that
    failed must not be spelled the same way as a worktree with nothing in it.
    One failed half is enough to return None — a partial snapshot is the same
    silent omission in a smaller size.
    """
    changed: set[str] = set()
    # Untracked files count: a fix that only adds a test file still fixed the
    # finding, and diff-only detection would report it as skipped.
    for args in (("diff", "HEAD", "--name-only"),
                 ("ls-files", "--others", "--exclude-standard")):
        r = git_client.run(*args, cwd=wt_path)
        if not r.ok:
            log.warn(proc.failure_message(
                f"Could not list what changed in {wt_path}", r,
            ))
            return None
        changed.update(line for line in r.stdout.splitlines() if line)
    return changed


def agent_changed(wt_path: str | Path, before: set[str] | None) -> set[str] | None:
    """What the agent added to the worktree's dirty set, or None when unknowable.

    None in gives None out: a pass with no baseline cannot attribute anything,
    and guessing from the second snapshot alone would claim every dirty file in
    the worktree as the agent's work.
    """
    # ceiling: attribution is by path, so a file already dirty when the pass
    # starts is never credited to the agent — edits it makes to that file are
    # neither staged nor committed. Upgrade to comparing each path's content
    # hash across the snapshot once fix passes routinely run against trees that
    # are dirty in the very files the pass has work in.
    if before is None:
        return None
    after = changed_files(wt_path)
    return None if after is None else after - before


def batch_scope(wt_path: str | Path, before: set[str] | None) -> BatchScope:
    """What one invocation changed, as a scope the reconciler can question.

    The same difference `agent_changed` takes, in the form the per-item
    comparison needs: a failed read becomes `UNKNOWN_SCOPE` rather than None,
    so a caller that forgets to check cannot silently read "could not look" as
    "nothing changed". That distinction is the whole reason this returns a type
    instead of a set.

    Inherits the path-attribution ceiling `agent_changed` documents: a file
    already dirty when the batch began is not in the difference, so an item
    fixed there is observed as untouched. That direction is the safe one — it
    understates what the agent did, and every consumer treats a quiet scope as
    no evidence rather than as evidence against.
    """
    changed = agent_changed(wt_path, before)
    return UNKNOWN_SCOPE if changed is None else BatchScope(files=frozenset(changed))


def report_unattributable(wt_path: str | Path) -> None:
    """Report a fix pass whose work could not be attributed, and where it is.

    Staging everything is not the fallback: the pass stages by name so that a
    build artifact or unrelated work in progress never rides along in a commit
    it then pushes, and a snapshot that failed is exactly when that list is
    unavailable. The edits are still in the worktree, so the honest end of this
    path is to say so and commit nothing.
    """
    log.error(
        f"could not read what the fix pass changed in {wt_path} — nothing was "
        f"committed or pushed. Any fixes it made are still in the worktree:\n"
        f"  git -C '{wt_path}' status"
    )
