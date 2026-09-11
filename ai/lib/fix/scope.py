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

from pathlib import Path

from core import log, proc
from git import client as git_client


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
