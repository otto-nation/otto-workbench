"""What keeps successive self-review rounds converging instead of circling.

A round reviews what changed, settles what it can without an agent, and leaves
the next round less to do. Each rule here closes one way the loop used to fail
to terminate: a decline re-stated every round, a design call handed back to
the fix agent every round, new nits on files nobody touched, a killed pass
whose leftovers looked like author edits, and a fix commit that always moved
HEAD past the review that produced it.

`change_set` is what "did not change" means for all of them.
"""

# doc-group: pipeline

from __future__ import annotations

from core.phases import Mode
from review.collect import uncommitted_files
from review.document import covers
from review.types import ReviewJob


def change_set(job: ReviewJob) -> frozenset[str] | None:
    """The files this run's review treats as changed since the prior round, or None.

    The delta when the run is incremental. When the prior review already covers
    HEAD, a self-review's same-commit change set is the working tree's
    (uncommitted edits plus the prior round's dirty files); in PR mode it is
    empty — a file changed only by merging the base counts as unchanged. None
    when neither holds: a full review with a moved HEAD measured no delta, so
    "not changed" cannot be answered and nothing may be settled on its strength.
    """
    preflight = job.preflight
    if preflight is not None and preflight.prior_head_sha:
        return frozenset(preflight.delta_files)
    if job.prior is not None and covers(job.prior.meta.head_sha, job.pr.head_sha):
        if job.mode != Mode.SELF:
            return frozenset()
        dirty = ()
        prior = job.prior
        if prior is not None and prior.meta is not None:
            dirty = prior.meta.dirty_files
        return frozenset(uncommitted_files(job.wt_path, job.pr.head_sha)) | frozenset(dirty)
    return None
