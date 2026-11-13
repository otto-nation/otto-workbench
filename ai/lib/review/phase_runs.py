"""Record which review phases ran, and the pipeline a one-group hand-off took.

`record` appends a `PhaseRun` onto the job. `handed_to_single` is the one-group
redirect: it marks the run as the single-agent pipeline and records scout,
holistic, group and synthesis as not run because one group covers the PR.
The pipeline module's hand-off call site is the only writer of that redirect;
orchestrate reads `job.pipeline_run` to emit the trail event and to report
the pipeline that actually ran.

`phases_line` is the `Phases:` line review.md and the comment place beside
`Models:`: ran phases by name, skipped ones grouped by reason, and nothing
when the sidecar recorded none.

`record_group_phase` is the multi-path group decision: `--no-group`, a budget
cut-off, groups reused from a prior attempt, or groups carried on an
incremental round. Each other phase records beside the log that already
states the decision.
"""

# doc-group: pipeline

from __future__ import annotations

from collections.abc import Sequence

from agent.diagnosis import Diagnosis, DiagnosisKind
from core.phases import Phase
from review.types import GroupSkip, PhaseRun, Pipeline, ReviewJob

_HANDOFF_SKIPPED = (Phase.SCOUT, Phase.HOLISTIC, Phase.GROUP, Phase.SYNTHESIS)
_HANDOFF_REASON = "one group covers the PR"
_RECOVERY_REASON = "resumed from prior attempt"
_PHASE_LABEL = {
    Phase.SINGLE.value: "single agent",
    Phase.SCOUT.value: "scan",
    Phase.HOLISTIC.value: "scan",
    Phase.GROUP.value: "groups",
    Phase.SYNTHESIS.value: "synthesis",
    Phase.DISPROVE.value: "disprove",
}


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


def phases_line(runs: Sequence[PhaseRun]) -> str:
    """The `Phases:` line, or "" when the sidecar recorded none.

    Ran phases are listed by name; skipped ones are grouped by reason.
    A one-group hand-off states that reason on the single agent so the
    skipped scan, groups and synthesis do not repeat it. Whitespace is
    collapsed as on the `Models:` line.
    """
    if not runs:
        return ""

    ran: list[str] = []
    skip_names: dict[str, list[str]] = {}
    skip_order: list[str] = []
    for run in runs:
        name = _PHASE_LABEL.get(run.phase, run.phase)
        reason = " ".join(run.reason.split())
        if run.ran:
            ran.append(f"{name} ({reason})" if reason else name)
            continue
        if reason not in skip_names:
            skip_order.append(reason)
            skip_names[reason] = []
        if name not in skip_names[reason]:
            skip_names[reason].append(name)

    if _HANDOFF_REASON in skip_names:
        ran = [
            f"single agent ({_HANDOFF_REASON})" if item == "single agent" else item
            for item in ran
        ]

    parts = [", ".join(ran)] if ran else []
    for reason in skip_order:
        names = ", ".join(skip_names[reason])
        hide_reason = (
            reason == _HANDOFF_REASON
            and any(item.startswith("single agent") for item in ran)
        )
        if reason and not hide_reason:
            parts.append(f"{names} not run ({reason})")
        else:
            parts.append(f"{names} not run")
    return " ".join(f"Phases: {' · '.join(parts)}".split())
