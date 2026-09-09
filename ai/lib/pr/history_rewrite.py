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

from core import log
from git import push
from git import replay
from git import client as git_client
from git.land import CommitStatus
from pr import state as pr_state
from pr.attribution import CommitClaim, CommitPushResult, commit_unpushed
from pr.fix import FixRecord


def follow_history_rewrite(state: pr_state.PRState, wt_path: Path) -> None:
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

    Nothing is cleared when the rename cannot be followed. An orphan no single
    commit replays keeps the SHA it was recorded with, the hold stands, and the
    warning below names the two ways out — which is the honest answer to a state
    this cannot read, and the one thing worse than blocking would be publishing
    on a guess.
    """
    record = state.fix.fix
    recorded = record.commit_sha
    # Keyed by recorded SHA, so a branch whose threads all cite one commit asks
    # git once. "" is a cached answer too: it means orphaned with no replay.
    replays: dict[str, str] = {}

    def followed(sha: str) -> str:
        if not sha:
            return sha
        if sha not in replays:
            replays[sha] = (
                replay.replayed_commit(wt_path, sha)
                if replay.rewritten_away(wt_path, sha) else sha
            )
        return replays[sha] or sha

    record.commit_sha = followed(record.commit_sha)
    # The snapshot HEAD moves with the rest. It is the base of the "what landed
    # outside the fix pass" range, and a base git cannot resolve makes that
    # range empty — which reads as "nothing landed since", the one answer that
    # is never true after a rebase.
    record.head_sha = followed(record.head_sha)
    for outcome in record.items:
        outcome.commit_sha = followed(outcome.commit_sha)
        outcome.read_sha = followed(outcome.read_sha)

    moved = sum(1 for sha, replayed in replays.items() if replayed and replayed != sha)
    if moved:
        log.info(
            f"Followed {moved} rewritten commit(s) — the fix snapshot now cites "
            "the history on the branch"
        )
    if recorded and replays.get(recorded) == "" and commit_unpushed(
            record.commit_status):
        log.warn(
            f"Fix commit {recorded} is no longer on this branch and no single "
            "commit on it carries that change — the closeout stays held. This "
            "also fires when the change now appears twice (a duplicated "
            "cherry-pick, an apply-revert-reapply) — check history for that "
            "before restoring anything. If the work landed as a different "
            "change — a squash, a reworked fix — re-run "
            "`pr comments --fix --post` to re-triage against HEAD; if it was "
            f"dropped, restore it with `git cherry-pick {recorded}` while the "
            "orphaned object is still there — a gc reclaims it"
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
    current = git_client.head_sha(cwd=wt_path, short=True)
    if not current or current == record.head_sha:
        return CommitPushResult(None, status, "")
    if not push.holds(wt_path, current):
        return CommitPushResult(None, CommitStatus.RECONCILED, "")
    log.info(
        "Work landed outside the fix pass — each row is attributed from its "
        "own line history, and left unattributed rather than credited to "
        f"{current} where that finds nothing"
    )
    return CommitPushResult(
        current, CommitStatus.PUSHED, "", claim=CommitClaim.UNDETERMINED,
    )
