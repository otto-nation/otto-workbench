"""Telling a red verify run the pass caused from one it inherited.

`fix.suite` runs the repo's checks over the tree a fix pass is about to commit,
and a red result withdraws every fix the pass claimed. That is right when the
pass broke something, and wrong when the tree was red before the pass began —
a hand commit's failure then demotes an unrelated import hoist, and the commit
body tells the reader the pass is to blame.

So a red run is asked once more, of the tree the pass started from. The engine
snapshots that tree before the agent runs (`snapshot`), and on red this module
checks it out into a throwaway worktree and runs the same command with the same
selection — `fix.suite.FIX_PATHS_ENV` names the pass's paths, so a selector
that reads it picks the same tests over the older code. A failure both runs
share is inherited. Only when every failure the pass run names is inherited
does the verdict become `PREEXISTING`; a new failure beside an old one stays
`RED`, with the old ones named in the note.

Re-running the same selection, not just the failing tests, keeps the repo the
owner of its runner: the engine cannot know how to run one test in an arbitrary
repo, and `fix.suite` explains why it does not invent one.
"""

# doc-group: platform

from __future__ import annotations

import tempfile
from dataclasses import replace
from pathlib import Path

import core.log
import fix.suite
import git.client
from core.trail import Trail, tinfo


def snapshot(workdir: Path) -> str:
    """A commit holding the tree as the pass found it — HEAD plus tracked edits.

    `git stash create` writes that commit without touching the worktree or the
    stash list. It prints nothing on a clean tree, which is HEAD's answer.
    """
    # ceiling: untracked files that were present before the pass are not in the
    # snapshot, so the baseline tree lacks them. Upgrade trigger: if a failure
    # is ever labelled new because a pre-existing untracked file was missing at
    # the baseline, copy the pass's untracked dirty_before paths into it.
    return git.client.out("stash", "create", cwd=workdir) or git.client.head_sha(cwd=workdir)


def label_preexisting(
    workdir: Path, result: fix.suite.SuiteResult, command: str, timeout_s: int,
    trail: Trail | None, *, ref: str, paths_file: str = "",
) -> fix.suite.SuiteResult:
    """*result*, labelled by what of its failure the tree at *ref* shares.

    Unchanged unless *result* is red, named its failures and there is a *ref*
    to compare against: an unparsed failure cannot be matched, and staying red
    is the safe answer. A baseline that could not run or did not reach a
    verdict leaves it unchanged for the same reason.
    """
    if not result.demotes or not result.failures or not ref:
        return result
    baseline = _run_at(workdir, ref, command, timeout_s, paths_file)
    if baseline is None or not baseline.ran:
        return result
    before = set(baseline.failures)
    inherited = tuple(f for f in result.failures if f in before)
    tinfo(trail, "fix_verify_baseline",
          f"{len(inherited)} of {len(result.failures)} failure(s) also fail at {git.client.abbrev(ref)}",
          data={"ref": ref, "inherited": list(inherited), "failures": list(result.failures)})
    if not inherited:
        return result
    if len(inherited) == len(result.failures):
        core.log.warn(f"Every failing check also fails before this pass ({git.client.abbrev(ref)})"
                      " — not withdrawing the pass's fixes")
        return replace(result, status=fix.suite.SuiteStatus.PREEXISTING, inherited=inherited)
    return replace(result, inherited=inherited)


def _run_at(
    workdir: Path, ref: str, command: str, timeout_s: int, paths_file: str,
) -> fix.suite.SuiteResult | None:
    """The command's verdict over a throwaway checkout of *ref*, or None when none could be made.

    Pruned before and after: a run killed mid-baseline leaves its worktree
    registered with the directory gone, and registrations nobody prunes
    accumulate in every later `git worktree list`.
    """
    with tempfile.TemporaryDirectory(prefix="fix-baseline-") as scratch:
        tree = Path(scratch) / "tree"
        git.client.run("worktree", "prune", cwd=workdir)
        added = git.client.run("worktree", "add", "--detach", str(tree), ref, cwd=workdir)
        if not added.ok:
            core.log.warn(f"Could not check out {git.client.abbrev(ref)} to re-run the checks: "
                          f"{added.stderr.strip()}")
            return None
        try:
            core.log.info(f"Re-running the checks at the pass's base ({git.client.abbrev(ref)})")
            return fix.suite.run(tree, command, timeout_s, base=ref,
                                 paths_file=paths_file, baseline=True)
        finally:
            git.client.run("worktree", "remove", "--force", str(tree), cwd=workdir)
            git.client.run("worktree", "prune", cwd=workdir)
