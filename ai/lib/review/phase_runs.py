"""Record which review phases ran, and the pipeline a one-group hand-off took.

`record` appends a `PhaseRun` onto the job. `handed_to_single` is the one-group
redirect: it marks the run as the single-agent pipeline and records scout,
holistic, group and synthesis as not run because one group covers the PR.
The pipeline module's hand-off call site is the only writer of that redirect;
orchestrate reads `job.pipeline_run` to emit the trail event and to report
the pipeline that actually ran.

`record_group_phase` is the multi-path group decision: `--no-group`, a budget
cut-off, groups reused from a prior attempt, or groups carried on an
incremental round. Each other phase records beside the log that already
states the decision.
"""

# doc-group: pipeline

from __future__ import annotations

from agent.diagnosis import Diagnosis, DiagnosisKind
from core.phases import Phase
from review.types import GroupSkip, PhaseRun, Pipeline, ReviewJob

_HANDOFF_SKIPPED = (Phase.SCOUT, Phase.HOLISTIC, Phase.GROUP, Phase.SYNTHESIS)
_HANDOFF_REASON = "one group covers the PR"
_RECOVERY_REASON = "resumed from prior attempt"


def record(job: ReviewJob, phase: Phase, *, ran: bool, reason: str = "") -> None:
    """Append one phase outcome onto the job.

    `phase` is the enum; storage keeps the member value so an unknown string
    in a sidecar cannot cost the whole meta.
    """
    job.phase_runs.append(PhaseRun(phase=phase.value, ran=ran, reason=reason))


def handed_to_single(job: ReviewJob) -> None:
    """Mark a one-group multi run as the single-agent pipeline.

    Scout, holistic, group and synthesis do not run: one group covers the PR,
    so the single-agent path is the whole review.
    """
    job.pipeline_run = Pipeline.SINGLE
    for phase in _HANDOFF_SKIPPED:
        record(job, phase, ran=False, reason=_HANDOFF_REASON)


def group_ran_reason(group_skips: dict[int, GroupSkip]) -> str:
    """Why a group phase that ran still reused some groups.

    Recovery skips win: a `--recover` run's reused groups are this attempt's
    groups, not a new pass. Otherwise the carried-group count, when any.
    """
    if any(kind is GroupSkip.RECOVERY for kind in group_skips.values()):
        return _RECOVERY_REASON
    carried = sum(1 for kind in group_skips.values() if kind is GroupSkip.CARRIED)
    if not carried:
        return ""
    noun = "group" if carried == 1 else "groups"
    return f"{carried} {noun} carried forward"


def record_group_phase(
    job: ReviewJob,
    *,
    unrun: Diagnosis | None,
    group_skips: dict[int, GroupSkip],
) -> None:
    """Record the group-phase decision the multi path just took."""
    if unrun is not None:
        reason = "--no-group" if unrun.kind is DiagnosisKind.SKIPPED else "budget"
        record(job, Phase.GROUP, ran=False, reason=reason)
        return
    record(job, Phase.GROUP, ran=True, reason=group_ran_reason(group_skips))
