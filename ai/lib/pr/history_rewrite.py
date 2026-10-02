"""Keeping a recorded commit true after the branch is rewritten under it.

A fix pass that holds its push records the SHA it committed, and the run that
clears the hold is often the one *after* a rebase. That rebase rewrites every
commit on the branch, so the recorded SHA is orphaned — still resolvable,
contained by no branch, and therefore read as unpushed by every gate in the
closeout. The work is on the remote; only its name changed.

Two recoveries, one per direction:

- :func:`follow_history_rewrite` re-points a stored snapshot at the commits a
  rewrite left in place of its own, before anything asks whether a commit is
  published.
- :func:`reconciled_commit` goes the other way: the pass recorded *no* commit
  and the branch moved anyway, so someone landed the work by hand.

The git-level questions both rest on — was this orphaned, and which commit
replays it — belong to `git.replay` at layer 2, so the rebase subsystem that
causes these rewrites can reach the same answers. What is here is the part that
knows about a `PRState` and a `FixRecord`.
"""

# doc-group: pr-state

from __future__ import annotations

from pathlib import Path

import core.log
import git.push
import git.replay
import git.client
from git.land import CommitStatus
import pr.state
from pr.attribution import CommitClaim, CommitPushResult, commit_unpushed
from pr.fix import FixRecord


def follow_history_rewrite(state: pr.state.PRState, wt_path: Path) -> None:
    """Re-point the snapshot at the commits a rewrite left in place of its own.

    A fix pass that holds its push records the SHA it committed, and the run
    that clears the hold is often the one *after* a rebase — `pr rebase --fix`
    is what a supersession warning tells the operator to run. That rebase
    rewrites every commit on the branch and force-pushes, so the recorded SHA
    ends up orphaned: still resolvable, contained by no branch, and therefore
    read as unpushed by every gate in the closeout. The work is on the remote;
    only its name changed, and holding the replies and the summary over a name
    leaves the operator with no way forward that does not discard reviewed
    drafts.

    So the rename is followed here, before anything asks whether a commit is
    published. Downstream is unchanged and answers correctly either way: a
    replay that reached the remote clears the hold, and one still sitting local
    is unpushed for the ordinary reason and keeps holding.

    Nothing is cleared when the rename cannot be followed. An orphan with no
    single replay keeps the SHA it was recorded with and the hold stands, and
    the warning says which of three things stopped it — nothing carries the
    change, two things do, or git could not answer — because each has a
    different way out, and the one thing worse than blocking would be
    publishing on a guess.
    """
    record = state.fix.fix
    recorded = record.commit_sha
    # One finder for the whole snapshot, so every SHA in it shares one read of
    # the rewrite log and one walk per merge base. Answers are cached by
    # recorded SHA too: None means the commit was never orphaned.
    finder = git.replay.ReplayFinder(wt_path)
    replays: dict[str, git.replay.Replay | None] = {}

    def followed(sha: str) -> str:
        if not sha:
            return sha
        if sha not in replays:
            replays[sha] = (
                finder.find(sha) if git.replay.rewritten_away(wt_path, sha) else None
            )
        replay = replays[sha]
        return replay.sha if replay is not None and replay.found else sha

    record.commit_sha = followed(record.commit_sha)
    # The snapshot HEAD moves with the rest. It is the base of the "what landed
    # outside the fix pass" range, and a base git cannot resolve makes that
    # range empty — which reads as "nothing landed since", the one answer that
    # is never true after a rebase.
    record.head_sha = followed(record.head_sha)
    for outcome in record.items:
        outcome.commit_sha = followed(outcome.commit_sha)
        outcome.read_sha = followed(outcome.read_sha)

    moved = [r for sha, r in replays.items()
             if r is not None and r.found and r.sha != sha]
    if moved:
        exact = sum(1 for r in moved if r.source is git.replay.ReplaySource.REWRITE_LOG)
        core.log.info(
            f"Followed {len(moved)} rewritten commit(s) — the fix snapshot now "
            f"cites the history on the branch ({exact} from git's record of the "
            f"rewrite, {len(moved) - exact} by matching the change)"
        )
    held = replays.get(recorded) if recorded else None
    if held is not None and not held.found and commit_unpushed(record.commit_status):
        core.log.warn(_unfollowed_warning(recorded, held))


def _unfollowed_warning(recorded: str, replay: git.replay.Replay) -> str:
    """Why *recorded* could not be followed, and the way out that fits."""
    stays = "the closeout stays held"
    retriage = "re-run `pr comments --fix --post` to re-triage against HEAD"
    if replay.status is git.replay.ReplayStatus.UNKNOWN:
        return (
            f"Could not tell which commit replays fix commit {recorded} — git did "
            f"not answer ({replay.detail}) — so {stays}. This says nothing about "
            f"whether the work is on the branch, so check the history before "
            f"restoring anything (a commit that no longer resolves cannot be "
            f"cherry-picked back); {retriage}"
        )
    if (replay.status is git.replay.ReplayStatus.AMBIGUOUS
            and replay.source is git.replay.ReplaySource.REWRITE_LOG):
        # Not a claim about content: git's record names several commits on the
        # branch, and they may have diverged since.
        return (
            f"Fix commit {recorded} was rewritten into more than one commit now "
            f"on this branch, and git's record does not say which one holds the "
            f"work — so there is nothing to choose between and {stays}. Check "
            f"which of them carries it, then {retriage}"
        )
    if replay.status is git.replay.ReplayStatus.AMBIGUOUS:
        return (
            f"Fix commit {recorded} was rewritten and more than one commit on "
            f"this branch now carries that change — a duplicated cherry-pick, or "
            f"an apply-revert-reapply — so there is nothing to choose between "
            f"and {stays}. Check the history for the duplicate, then {retriage}"
        )
    return (
        f"Fix commit {recorded} is no longer on this branch and no commit on it "
        f"carries that change — {stays}. If the work landed as a different "
        f"change — a reworked fix — {retriage}; if it was dropped, restore it "
        f"with `git cherry-pick {recorded}` while the orphaned object is still "
        "there — a gc reclaims it"
    )


def reconciled_commit(
    record: FixRecord, status: str, wt_path: Path | None,
) -> CommitPushResult:
    """The pass's commit as the worktree now shows it, not as it was recorded.

    The permalinks in this summary already fall back to the identity HEAD when
    the pass recorded no commit of its own; the status cell did not, so a
    hook-rejected commit the operator then made by hand rendered as a flat
    "no commit needed" beside a file link pointing at the very change it
    denied.

    A moved HEAD with nothing recorded means someone committed outside the
    pass. That commit is only worth naming if a reviewer can open it, so an
    unpushed one falls back to the same non-committal cell a reconciled
    outcome gets.

    What it cannot say is which of those commits carries any particular row.
    Whether one landed or several, this function sees only the branch, and a
    record holds rows from every round the fix pass has run — a row an earlier
    round settled sits in it attributed to nothing, and the commit that just
    landed is not the one that carried it. So the SHA is handed on as
    `UNDETERMINED`: a tree for the permalinks to pin to, and not a commit any
    surface may cite as the one carrying a row. That is a per-row question, so
    it is asked per row: `attribution.attribute_commit` reads each thread's own
    line history and cites the commit that changed it after the reviewer asked.
    """
    if record.commit_sha or not wt_path or not record.head_sha:
        return CommitPushResult(record.commit_sha or None, status, "")
    current = git.client.head_sha(cwd=wt_path, short=True)
    if not current or current == record.head_sha:
        return CommitPushResult(None, status, "")
    if not git.push.holds(wt_path, current):
        return CommitPushResult(None, CommitStatus.RECONCILED, "")
    core.log.info(
        "Work landed outside the fix pass — each row is attributed from its "
        "own line history, and left unattributed rather than credited to "
        f"{current} where that finds nothing"
    )
    return CommitPushResult(
        current, CommitStatus.PUSHED, "", claim=CommitClaim.UNDETERMINED,
    )
