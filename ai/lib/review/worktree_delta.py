"""Worktree and range file lists a review's delta is built from.

A self-review's incremental file list is the working tree against the prior
head, plus files that round already read dirty; a PR-mode fallback's is
``prior_sha..head`` — both from numstat, which no prompt budget clips.
`worktree_diff` is the matching patch (and its counts-only form) so the
files a review reports and the diff it sends come off one range.
"""

# doc-group: pipeline

from __future__ import annotations

import os

import git.client
import git.numstat
from review.types import ReviewJob


def _untracked_files(wt_path: str) -> list[str]:
    """Paths git does not track and .gitignore does not exclude."""
    return git.client.lines("ls-files", "--others", "--exclude-standard", cwd=wt_path)


def _diff_untracked(wt_path: str, paths: list[str], counts_only: bool = False) -> str:
    """Diff each untracked path against nothing, as a whole-file addition."""
    flags = ["--numstat"] if counts_only else []
    parts = []
    for path in paths:
        # --no-index exits 1 whenever the two sides differ, which is always here,
        # so the exit code is read past rather than through `out`.
        out = git.client.run(
            "diff", "--no-index", *flags, "--", os.devnull, path, cwd=wt_path,
        ).stdout.strip()
        if not out:
            continue
        # numstat names the pair "/dev/null => <path>"; the diff body is already clean.
        parts.append(out.rsplit("\t", 1)[0] + "\t" + path if counts_only else out)
    return "\n".join(parts)


def _join_nonempty(*parts: str) -> str:
    return "\n".join(p for p in parts if p)


def worktree_diff(wt_path: str, since: str, *, counts_only: bool = False) -> str:
    """Every change from ``since`` to the working tree, untracked files included.

    ``counts_only`` asks for the per-file add/delete counts instead of the
    patch, so the file list a review reports and the diff it sends come off
    the same range rather than off two commands that could disagree.

    ``since`` bounds the tracked half only. Untracked files have no history to
    compare against, so they come through whole every time — on a delta review
    that means one lingering across runs is re-shown rather than dropped.
    """
    # ceiling: untracked files ignore `since`; a non-ignored untracked file
    # also makes every same-HEAD self-review incremental over it and lands in
    # dirty_files each round (bounded to the surface). Upgrade to diffing
    # against the prior review's copy if repeated deltas start drowning in
    # re-shown files.
    flags = ["--numstat"] if counts_only else []
    return _join_nonempty(
        git.client.out("diff", *flags, since, cwd=wt_path),
        _diff_untracked(wt_path, _untracked_files(wt_path), counts_only=counts_only),
    )


def uncommitted_files(wt_path: str, since: str) -> list[str]:
    """Every path the working tree differs from *since* in, untracked files included.

    Read from numstat, which no prompt budget clips: a file list a delta acts
    on must not depend on how much of the patch fitted. `--no-renames` makes a
    rename a deletion plus an addition, so both paths are listed rather than
    one `old => new` entry that names neither file. Untracked files come
    through `_diff_untracked`, as in `worktree_diff`.
    """
    tracked = git.client.out("diff", "--numstat", "--no-renames", since, cwd=wt_path)
    untracked = _diff_untracked(wt_path, _untracked_files(wt_path), counts_only=True)
    counted = git.numstat.parse_numstat(_join_nonempty(tracked, untracked))
    return sorted(f["path"] for f in counted.files)


def _on_surface(paths: set[str], pr_files: list[dict]) -> list[str]:
    surface = {f["path"] for f in pr_files}
    return sorted(p for p in paths if not surface or p in surface)


def self_delta_files(job: ReviewJob, since: str) -> list[str]:
    """The surface files a self-review's delta from *since* covers.

    What the tree differs from *since* in, plus what the prior round read dirty
    (`ReviewMeta.dirty_files`): a file whose edit was reverted since matches
    *since* again while differing from what was reviewed, and only the record
    says so. Narrowed to the review's surface, as the patch is — the re-read
    paths too: a file whose only change on the branch was the reverted edit
    has left the surface, so no group holds it, and when it is the only
    difference the round falls back to a full review of the same HEAD, as
    every such round did before.
    """
    reread = set(job.prior.meta.dirty_files) if job.prior else set()
    paths = set(uncommitted_files(job.wt_path, since)) | reread
    return _on_surface(paths, job.pr.files)


def range_delta_files(
    wt_path: str, prior_sha: str, head: str, pr_files: list[dict],
) -> list[str]:
    """Every path ``prior_sha..head`` differs in, untruncated, scoped to *pr_files*.

    Read from numstat, which no prompt budget clips: a file list a delta acts
    on must not depend on how much of the patch fitted. ``--no-renames`` makes
    a rename a deletion plus an addition, so both paths are listed rather than
    one ``old => new`` entry that names neither file. An empty *pr_files*
    keeps the whole range.
    """
    counted = git.numstat.parse_numstat(
        git.client.out(
            "diff", "--numstat", "--no-renames", f"{prior_sha}..{head}",
            cwd=wt_path,
        )
    )
    return _on_surface({f["path"] for f in counted.files}, pr_files)
