"""Whether a recorded commit survived a history rewrite, and as which commit.

A rebase writes new commits and leaves the originals in the object database, so
a SHA recorded before one still resolves afterwards while naming a commit no
branch contains. Anything holding a recorded SHA across a rebase — a fix pass
that stamped one onto a row, a state file carrying one between runs — has to be
able to tell that apart from a commit that is simply not pushed yet.

Two questions, and the distinction between them is the whole module:

- :func:`rewritten_away` — *was it orphaned?* Ancestry, read for the one exit
  code that means orphaned rather than for truthiness.
- :func:`replayed_commit` — *which commit carries it now?* Patch equivalence,
  refusing to answer when more than one candidate matches.

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

from pathlib import Path

from core import proc
from core import timeouts
from git import client as git_client


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

    That last reading is why this cannot be written with `git_client.ok`, which
    collapses every non-zero exit into one answer.
    """
    return git_client.run(
        "merge-base", "--is-ancestor", sha, "HEAD", cwd=wt_path,
    ).returncode == 1


def patch_ids(wt_path: Path, *revs: str) -> dict[str, list[str]]:
    """Patch id → the commits in *revs* carrying it, git's own equivalence test.

    The same one `git cherry` and `git rebase` use to recognise a commit they
    have already replayed, reached here through `patch-id` because those two
    answer "is it there?" and this has to answer "which one is it?".

    `--stable` so the id is a property of the change and not of whoever's diff
    settings produced it, and `--no-merges` because a merge has no single patch:
    `log -p` prints no diff for one, which would otherwise land every merge on a
    shared empty id. An empty commit drops out the same way, and neither can
    collide with anything, since `patch-id` emits no line for either.

    ceiling: the whole `revs` diff is materialised to be piped through one
    `patch-id`, trading memory for a fixed two processes rather than two per
    candidate commit. Upgrade to a per-commit `diff-tree | patch-id` walk if a
    range ever grows large enough that the LOCAL timeout expires — which
    degrades safely, since no ids means no match means the hold stands.
    """
    patches = git_client.run(
        "log", "-p", "--no-merges", "--format=commit %H", *revs, cwd=wt_path,
    )
    if not patches.ok or not patches.stdout:
        return {}
    # Not git_client: this one reads a diff on stdin, and `run` deliberately
    # exposes no way to write to a child's input.
    ids = proc.run(["git", "patch-id", "--stable"], cwd=wt_path,
                   input_text=patches.stdout, timeout=timeouts.LOCAL)
    if not ids.ok:
        return {}
    by_id: dict[str, list[str]] = {}
    for line in ids.stdout.splitlines():
        patch_id, _, commit = line.partition(" ")
        if patch_id and commit.strip():
            by_id.setdefault(patch_id, []).append(commit.strip())
    return by_id


def replayed_commit(wt_path: Path, sha: str) -> str:
    """The commit now on the branch that replays *sha*, or "" when none does.

    Matched on the patch the commit carries, because the header carries nothing
    that tells two fix commits apart: a pass commits under one static subject,
    and two rounds close enough together — a retry, a fast CI loop — share an
    author date to the second as well. Content is the only field a rebase
    preserves that is also the commit's own, and it survives an amend and a
    conflict resolution that a subject match would not.

    Searched from the orphan's own merge base rather than from the upstream
    branch, because the rewrite may have moved the branch onto a newer base and
    the old one is the only commit both histories still agree on.

    Two answers is no answer. A patch that appears twice in range — applied,
    reverted, applied again; a cherry-pick duplicated by the rebase — leaves
    nothing to choose between, so it takes the no-match path and keeps holding
    rather than citing a commit picked by list order. A reword or an amend still
    matches, since neither touches the diff; a squash, a dropped commit, and a
    fix reworked into a different change do not, and that is the honest answer:
    the recorded commit's work is not on the branch under any name.
    """
    orphan = patch_ids(wt_path, "--no-walk", sha)
    base = git_client.out("merge-base", sha, "HEAD", cwd=wt_path)
    if len(orphan) != 1 or not base:
        return ""
    replays = patch_ids(wt_path, f"{base}..HEAD").get(next(iter(orphan)), [])
    if len(replays) != 1:
        return ""
    return git_client.out("rev-parse", "--short", replays[0], cwd=wt_path)
