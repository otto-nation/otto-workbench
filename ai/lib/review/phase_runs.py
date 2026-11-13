"""Record which review phases ran, and the pipeline a one-group hand-off took.

`record` appends a `PhaseRun` onto the job. `handed_to_single` is the one-group
redirect: it marks the run as the single-agent pipeline and records scout,
holistic, group and synthesis as not run because one group covers the PR.
The pipeline module's hand-off call site is the only writer of that redirect;
orchestrate reads `job.pipeline_run` to emit the trail event and to report
the pipeline that actually ran.
"""

# doc-group: pipeline

from __future__ import annotations

from review.types import PhaseRun, Pipeline, ReviewJob

_HANDOFF_SKIPPED = ("scout", "holistic", "group", "synthesis")
_HANDOFF_REASON = "one group covers the PR"


def record(job: ReviewJob, phase: str, *, ran: bool, reason: str = "") -> None:
    """Append one phase outcome onto the job."""
    job.phase_runs.append(PhaseRun(phase=phase, ran=ran, reason=reason))


def handed_to_single(job: ReviewJob) -> None:
    """Mark a one-group multi run as the single-agent pipeline.

    Scout, holistic, group and synthesis do not run: one group covers the PR,
    so the single-agent path is the whole review.
    """
    job.pipeline_run = Pipeline.SINGLE
    for phase in _HANDOFF_SKIPPED:
        record(job, phase, ran=False, reason=_HANDOFF_REASON)
