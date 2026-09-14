"""Writing what a comment fix pass did into the PR's state file.

`pr.comments_fix` owns the shape — `FixSummary`, its merge rules, its
rendering. This owns the write: assembling the record out of the round's
buckets, naming the reviewer behind each entry, and saving both alongside the
thread-tally delta in one transaction.

Split from `pr.comments_fix` rather than folded into it because the domain and
its writer answer different questions, and a module that holds both is the
shape `…-00` rule F-C warns about: a renderer reading a field some other file
is responsible for keeping current.

**The save is one transaction, deliberately.** The comment tally on disk was
snapshotted before the pass ran, so it learns of the threads the pass resolved
only from the delta applied here. A second save would write the fix record
against a tally that had not moved, and `pr status` would report threads still
open that GitHub has already closed.
"""

# doc-group: pr-state

from __future__ import annotations

from pathlib import Path

from core import log
from core.trail import Trail
from git.land import CommitStatus
from pr import attribution
from pr import comments_fix as pr_comments_fix
from pr import context as pr_context
from pr import state as pr_state
from pr.comments_state import ThreadState
from pr.fix import FixOutcome, FixRecord
from pr.thread_models import CommentItem


def fix_record_for(
    by_outcome: dict[FixOutcome, list[CommentItem]],
    commit_sha: str = "",
    commit_status: CommitStatus | None = None,
    head_sha: str = "",
) -> FixRecord:
    """Build the record a fix pass writes, one outcome at a time.

    Taking the buckets as a mapping rather than as a parameter each: the
    outcomes are `FixOutcome`'s to name, and a signature that spells them out
    has to grow a parameter — and every caller a positional argument — each
    time that vocabulary does.

    Here rather than on `FixRecord` itself, which three passes write and only
    this one reaches through `CommentItem`. CI builds its record from
    `ItemOutcome`s with no buckets at all, and a constructor on the shared type
    would pull the comment domain's vocabulary into it.

    `commit_sha` is stamped only on the buckets whose outcome may cite a
    commit, through `attribution.stamp_pass_commit` — the one writer of that
    field, and safe to call here even when a caller already stamped `fixed`
    upstream, since the writer is a no-op on an entry that already names a
    commit. The others were not landed by this commit, and a SHA on them would
    read as if they had been.
    """
    for outcome, entries in by_outcome.items():
        if outcome.may_cite_a_commit:
            attribution.stamp_pass_commit(entries, commit_sha)
    return FixRecord(
        items=[
            entry.to_outcome(outcome)
            for outcome, entries in by_outcome.items()
            for entry in entries
        ],
        commit_sha=commit_sha,
        commit_status=commit_status,
        head_sha=head_sha,
        updated_at=pr_state.now_iso(),
    )


def reviewers_for(
    by_outcome: dict[FixOutcome, list[CommentItem]],
) -> dict[str, str]:
    """The login behind each recorded outcome, keyed by the outcome's id.

    Kept beside the record rather than on it — see `FixSummary.reviewers`. An
    entry GitHub named no reviewer for contributes no key, so a later lookup
    misses rather than asserting an anonymous one.
    """
    return {
        entry.id: entry.reviewer
        for entries in by_outcome.values()
        for entry in entries
        if entry.id and entry.reviewer
    }


def persist(
    fix_summary: pr_comments_fix.FixSummary,
    wt_path: Path,
    ctx: pr_context.ResolvedContext,
    trail: Trail | None,
    resolved: list[ThreadState] | None = None,
) -> None:
    """Save what the pass did, and what it resolved, in one write.

    `resolved` names the bucket each thread this pass resolved on GitHub came
    from. The comment counts were snapshotted and saved before the pass ran, so
    they only account for those resolutions if the delta is applied here.

    Not `pr_state.apply_state_update`, which takes a domain name and a dict.
    Two differences rule it out and only one is about typing: this hands over a
    `FixSummary` the caller already built rather than a mapping to reconstruct
    through serde, and — the load-bearing one — it writes the comments domain
    as well, which a fix-domain update has no way to express.

    A failure here is logged and swallowed rather than raised. The pass has
    already committed, replied and posted by the time it reaches this; taking
    the process down over an unwritable state file would leave those acts done
    and unrecorded, which is worse than a state file one round behind.
    """
    try:
        st = pr_state.load_or_init(
            target_dir=ctx.target_dir, repo=ctx.repo, branch=ctx.branch,
            pr_number=ctx.pr_number, head_sha=ctx.head_sha,
            worktree_root=str(wt_path),
        )
        pr_state.apply(st, fix_summary)
        st.comments.move_to_resolved(resolved or [], updated_at=pr_state.now_iso())
        pr_state.save_state(ctx.target_dir, st)
    except Exception as exc:
        if trail:
            trail.error("fix_state", f"fix state update failed: {exc}")
        log.error(f"fix state update failed: {exc}")
