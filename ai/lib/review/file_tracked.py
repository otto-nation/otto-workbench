"""File selected open findings from a branch's existing self-review record.

Finding ids (`M1`, `S2`, …) are positions in one rendering; a re-run
renumbers. This path runs no agent. It locates the branch's self-review
record, refuses unless that record's `covered_sha` (else `head_sha`) still
covers the worktree HEAD, and then calls `review.finding_issue.file_and_link`.

`--post` is the publishing gate the caller opens. Without it this drafts and
does not stamp `filed=`.
"""

# doc-group: publishing

from __future__ import annotations

from types import SimpleNamespace

import core.log
import core.publishing
import review.document
import review.finding_issue
import review.paths
import review.store
from review.record import load_record


def run(ctx, track: frozenset[str]) -> int:
    """File *track* from the self-review of *ctx*'s branch. 0, or 1 on refuse."""
    review_file = review.paths.self_review_file_path(ctx.repo, ctx.branch or "")
    review_dir = review_file.parent
    stored = review.store.read_review(review_file)
    if stored is None:
        core.log.error(
            f"no self-review record for {ctx.repo} branch `{ctx.branch or ''}`"
        )
        return 1

    reviewed = stored.meta.reviewed_through
    head = ctx.head_sha or ""
    if not review.document.covers(reviewed, head):
        core.log.error(
            f"self-review record covers {reviewed or '(none)'}, "
            f"HEAD is {head or '(none)'}; --track ids belong to that "
            f"record and a re-run renumbers"
        )
        return 1

    job = SimpleNamespace(
        repo=ctx.repo,
        pr_number=str(ctx.pr_number) if ctx.pr_number else "",
        host=getattr(ctx, "host", "") or "",
        wt_path=str(ctx.worktree_root) if ctx.worktree_root else "",
        pr=SimpleNamespace(head=ctx.branch or ""),
    )
    known = {f.id for f in stored.record.visible}
    unknown = set(track) - known
    if unknown:
        # Same refuse as `validate_track`, but already-filed ids are known:
        # a second `--track M1` must not open another issue.
        core.log.error(
            f"--track named findings that are not open: {sorted(unknown)}"
        )
        return 1
    pending = frozenset(
        f.id for f in review.finding_issue.filable(review_dir) if f.id in track
    )
    if not pending:
        return 0
    if not review.finding_issue.validate_track(review_dir, pending):
        return 1

    if not core.publishing.enabled():
        selected = [f for f in review.finding_issue.filable(review_dir) if f.id in pending]
        core.publishing.draft(
            f"file {len(selected)} open finding(s): "
            + ", ".join(f.id for f in selected),
            review.finding_issue.build_body(selected, job),
        )
        review.finding_issue.report_unfiled(review_dir, pending)
        return 0

    if not review.finding_issue.file_and_link(review_dir, job, pending):
        return 1
    record = load_record(review_dir)
    stamped = [
        f for f in (record.visible if record else [])
        if f.id in pending and f.filed
    ]
    if not stamped:
        core.log.error(
            "--track --post filed nothing; the findings are still open"
        )
        return 1
    core.log.info(
        f"Filed {', '.join(f.id for f in stamped)} as {stamped[0].filed}"
    )
    return 0
