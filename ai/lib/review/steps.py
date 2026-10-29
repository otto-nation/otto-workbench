"""One function per phase of a multi-phase review.

Each takes the job and the run's state, does that phase's work, and reports
what it spent. They are ordered here as the run orders them, but none of them
calls the next: sequencing is `review.pipeline`'s, so a phase can be skipped,
resumed, or budgeted out without any other phase knowing.

`review.phases` owns the phase runner, the group fan-out, and the disprove and
synthesis rules this module drives without reimplementing: invoking an agent,
running the group phase (`_phase_group_reviews`), deciding whether the
disprove gate runs (`_should_disprove`), and sizing the synthesis turn budget
(`_synthesis_max_turns`) all read from there. Writing the result to the review
file is `review.outcome`'s.
"""

# doc-group: pipeline

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import core.log
from agent.diagnosis import Diagnosis, DiagnosisKind
from agent.registry import PHASES, SCAN_PHASES, phase_skip_argv
from core.phases import Mode, Phase
from core.strict import example_json
from agent.session import _parse_session_cost, diagnose_missing_output
from review.convergence import change_set, drop_out_of_delta_nits, prior_ids
from review.prompt_sections import _is_incremental
from review.document import SECTION_FOCUS, ReviewDocument
from review.grouping import HOLISTIC_MIN_GROUPS
from review.artifacts import (
    GroupArtifact, ReviewArtifact, as_group_artifact, declinable_sids,
)
from review.focus import coverage_problems, covered, needs_derived
from review.ingest import (
    ArtifactOutcome, IngestError, IngestRejected, ingest_artifact,
    load_artifact,
)
from review.merge import append_findings, merge_records
from review.outcome import (
    _build_mechanical_fallback, _fallback_status, _no_synthesis_record,
    _post_process_review, _publish, _reconcile_and_verify, _write_clean_review,
    _write_mechanical_fallback, _write_settled_review,
)
from review.paths import phase_log_path, phase_output_path
from review.phases import (
    PhaseResult, PhaseRunner,
    _phase_group_reviews, _should_disprove, _synthesis_max_turns,
    reuse_scan, run_phase,
)
from review.overflow import run_with_overflow_recovery
from review.prompt import PromptTooLarge
from review.prompt_prior import _scoped, _with_sids
from review.reconcile import passed_over
from review.record import (
    Disposition, ProseSection, RecordFinding, ReviewRecord, SeveritySection,
)
from review.registry import build_prompt
from review.render import render_body
from review.retry import GroupFailure, _has_output, _retry_missing_output
from review.state import (
    PipelineState, _inject_failures_and_status, _write_pipeline_state,
)
from review.store import StoredReview, read_review, write_review
from review.types import (
    SEVERITY_MUST, SEVERITY_SHOULD, Group, GroupSkip, PriorFinding, ReviewJob,
)
from review.verdict import BUDGET_SUMMARY, SKIPPED_SUMMARY, open_counts
from review.verify import apply_disprove
from core.text import plural

DISPROVE_MIN_FINDINGS = 3

# Dispositions synthesis may copy. Anything else would re-open if shown as
# group JSON, so it is held out of the prompt and restored afterwards.
_SYNTHESIS_KEEP = (Disposition.OPEN, Disposition.DECLINED)


@dataclass(frozen=True)
class GroupPhaseResult:
    """What the group phase produced and what its agents cost."""

    outputs: list[str]
    failures: list[GroupFailure]
    cost: float


@dataclass(frozen=True)
class DisproveRun:
    result: PhaseResult
    record: ReviewRecord


@dataclass(frozen=True)
class Merged:
    """The groups' findings as one record — what every writer publishes from.

    `text` is what the synthesis prompt quotes: the record as the JSON a group
    writes, so synthesis copies findings, evidence, sids and ledger entries
    forward structurally rather than translating them. The harness's own
    sections (`extra`: the `Review gaps` naming each group no agent reviewed)
    have no field in that JSON, so they follow it as markdown: synthesis must
    still know which files nobody looked at.
    """

    record: ReviewRecord
    # How many visible findings the groups that ran produced this round, before
    # anything was carried in. None where nobody counted. Synthesis is skipped
    # when it is 0 and every prior finding is settled — see `_nothing_new`.
    fresh: int | None = None

    @property
    def text(self) -> str:
        shown = f"```json\n{example_json(as_group_artifact(self._for_synthesis()))}\n```"
        harness = render_body(ReviewRecord(extra=self.record.extra)).strip()
        return f"{shown}\n\n{harness}" if harness else shown

    def carry(self, record: ReviewRecord) -> "Merged":
        notes = self.record.severity_sections + record.severity_sections
        return Merged(
            replace(
                append_findings(self.record, record.findings),
                severity_sections=notes,
            ),
            fresh=self.fresh,
        )

    def _for_synthesis(self) -> ReviewRecord:
        # as_group_artifact renders anything other than OPEN/DECLINED as a
        # plain open finding, so those stay off the page synthesis copies.
        return replace(
            self.record,
            findings=tuple(
                f for f in self.record.findings if f.disposition in _SYNTHESIS_KEEP
            ),
        )


def _finding_identity(finding: RecordFinding) -> frozenset[str]:
    return frozenset(token for token in (finding.stable_id, finding.sid) if token)


def _take_held(
    finding: RecordFinding, held: list[RecordFinding],
) -> RecordFinding | None:
    keys = _finding_identity(finding)
    for index, candidate in enumerate(held):
        if keys & _finding_identity(candidate):
            return held.pop(index)
    return None


def _restore_held_findings(merged: ReviewRecord, record: ReviewRecord) -> ReviewRecord:
    """Keep findings synthesis was not shown, unless it also emitted them.

    Identity is the non-empty {stable_id, sid}. A synthesis copy that matches
    a held finding is dropped. When severities match, the held finding takes
    that slot and the copy's id so synthesis prose still names it; otherwise
    the copy's id becomes a gap and the held finding is appended with a fresh
    id. Remaining unmatched held findings are appended the same way.
    """
    held = [f for f in merged.findings if f.disposition not in _SYNTHESIS_KEEP]
    if not held:
        return record
    kept: list[RecordFinding] = []
    leftover: list[RecordFinding] = []
    for finding in record.findings:
        match = _take_held(finding, held)
        if match is None:
            kept.append(finding)
            continue
        if match.severity == finding.severity:
            kept.append(replace(match, id=finding.id))
        else:
            leftover.append(match)
    restored = append_findings(
        replace(record, findings=tuple(kept)), leftover + held,
    )
    ids = [f.id for f in restored.findings]
    if len(ids) != len(set(ids)):
        # Unreachable by construction: same-severity replacements take the
        # copy's id (unique in the synthesis artifact), a severity mismatch
        # leaves the copy's slot empty, and leftover held findings get fresh
        # ids from append_findings.
        raise ValueError(f"held restore produced duplicate ids: {ids}")
    return restored


def _holistic_skip_reason(
    job: ReviewJob, incremental: bool, group_count: int,
) -> str | None:
    # Phase 1 is one scan chosen from two candidates, so it drops out only when
    # both are off — `--no-scout` alone falls back to the holistic scan, which
    # is what that flag has always meant.
    if incremental:
        return "incremental review"
    if SCAN_PHASES <= job.skip_phases:
        return " ".join(phase_skip_argv(SCAN_PHASES))
    if SCAN_PHASES <= job.skipped:
        return f"effort={job.effort}"
    if group_count < HOLISTIC_MIN_GROUPS:
        return f"{group_count} groups < {HOLISTIC_MIN_GROUPS} threshold"
    return None


def _scan_phase(job: ReviewJob) -> Phase:
    """Which of phase 1's two candidates this job runs."""
    return Phase.HOLISTIC if Phase.SCOUT in job.skipped else Phase.SCOUT


def _run_holistic_phase(
    job: ReviewJob, group_count: int, state: PipelineState, incremental: bool,
) -> PhaseResult:
    """Phase 1's scan, the log it wrote, and what that scan spent.

    A branch that reuses a prior attempt's scan reports the log at no cost:
    `_resolve_recovery` already charged the resumed run for it, so pricing it
    again here would bill that scan twice. Whether there is a prior attempt to
    reuse is `state.scanned` — the state is already here, so nothing needs to
    carry the answer in from the recovery plan.

    The chosen scan is resolved before the skip check because the skip branch
    records it too: a run that scouts and then skips phase 1 has to say scout,
    which a state field named for the holistic scan could never say.
    """
    phase = _scan_phase(job)
    reason = _holistic_skip_reason(job, incremental, group_count)
    if reason:
        core.log.info(f"Holistic/scout phase skipped ({reason})")
        if not state.scanned:
            state.done.add(phase)
            _write_pipeline_state(job, state)
        return PhaseResult()

    label = PHASES[phase].label
    output = phase_output_path(job.review_file, phase)
    if state.scanned and _has_output(output):
        core.log.info(f"Phase 1: {label} skipped (exists)")
        return reuse_scan(phase, output, phase_log_path(job.review_file, phase))

    result = run_phase(job, phase, f"Phase 1/{group_count}: {label}...")
    state.done.add(phase)
    _write_pipeline_state(job, state)
    return result


def _run_group_phase(
    job: ReviewJob, groups: list[Group], group_count: int,
    holistic_content: str, max_parallel: int,
    skip_groups: "dict[int, GroupSkip]", state: PipelineState,
) -> GroupPhaseResult:
    group_outputs, failed_groups = _phase_group_reviews(
        groups, job, group_count, holistic_content, max_parallel,
        skip_groups=skip_groups, pipeline_state=state,
    )

    cost = 0.0
    new_group_indices = [
        i for i in range(1, len(group_outputs) + 1)
        if i not in skip_groups
    ]
    for i in new_group_indices:
        cost += _parse_session_cost(phase_log_path(job.review_file, Phase.GROUP, i))

    return GroupPhaseResult(outputs=group_outputs, failures=failed_groups, cost=cost)


def _run_synthesis_or_fallback(
    job: ReviewJob, state: PipelineState,
    holistic_content: str, group_count: int,
    merged: Merged, failed_groups: "list[GroupFailure]",
    n_skipped: int, cost_so_far: float, max_cost: float,
    *, settled: bool = False,
) -> PhaseResult:
    """The synthesis session log and what synthesis spent.

    Every branch below that reaches the review file without an agent — a clean
    review, a mechanical fallback, a skip — spends nothing and says so, so the
    caller's running total means the same thing at the disprove gate as it did
    here.
    *settled* is `_nothing_new`'s answer, decided once by the caller.
    """
    all_groups_failed = len(failed_groups) == group_count

    if not merged.record.visible and not failed_groups:
        core.log.info("No findings from any group — writing clean review")
        _write_clean_review(
            job, group_count, merged.record, skipped_groups=n_skipped,
        )
        state.done.add(Phase.SYNTHESIS)
        _write_pipeline_state(job, state)
        return PhaseResult()

    if all_groups_failed:
        if Phase.GROUP in job.skipped:
            core.log.warn("Group phase skipped — the review reports what it has")
            why = Diagnosis(DiagnosisKind.SKIPPED, detail="--no-group")
        else:
            core.log.warn("All group agents failed — skipping synthesis")
            why = Diagnosis(DiagnosisKind.ALL_GROUPS_FAILED)
        # Recorded before the fallback is built, not after: the meta header the
        # fallback carries reads the status back off this file, so a verdict
        # written afterwards leaves the document claiming the run completed.
        # The all-groups-failed fallback is not post-processed: it is built
        # with pipeline_state and published directly.
        state.done.add(Phase.SYNTHESIS)
        state.failed[Phase.SYNTHESIS] = why
        _write_pipeline_state(job, state)
        _publish(
            job,
            _build_mechanical_fallback(
                job, group_count, merged.record, skipped_groups=n_skipped,
                pipeline_state=state,
            ),
            skipped_groups=n_skipped, total_groups=group_count,
            status=_fallback_status(job),
        )
        return PhaseResult()

    if Phase.SYNTHESIS in job.skipped:
        core.log.info("Synthesis skipped — using mechanical merge")
        _publish(
            job,
            _no_synthesis_record(
                job, _reconcile_and_verify(job, merged.record),
                group_count, SKIPPED_SUMMARY,
            ),
            skipped_groups=n_skipped, total_groups=group_count,
        )
        state.done.add(Phase.SYNTHESIS)
        _write_pipeline_state(job, state)
        _inject_failures_and_status(job.review_file, state)
        return PhaseResult()

    if settled:
        core.log.info("Synthesis skipped — no new findings, and every prior finding is settled")
        _write_settled_review(job, group_count, merged.record, skipped_groups=n_skipped)
        state.done.add(Phase.SYNTHESIS)
        _write_pipeline_state(job, state)
        _inject_failures_and_status(job.review_file, state)
        return PhaseResult()

    if cost_so_far > max_cost:
        core.log.warn("Using merged group output as final review (synthesis skipped due to budget)")
        # Post-processed like every other path that ships group output: the
        # budget that ran out buys agent turns, and evidence verification and
        # prior-finding reconciliation are local reads that cost none of it.
        # Skipping them here shipped findings nothing had checked against the
        # tree, on the run least able to afford an unchecked claim.
        _publish(
            job,
            _no_synthesis_record(
                job, _reconcile_and_verify(job, merged.record),
                group_count, BUDGET_SUMMARY,
            ),
            skipped_groups=n_skipped, total_groups=group_count,
        )
        state.done.add(Phase.SYNTHESIS)
        state.failed[Phase.SYNTHESIS] = Diagnosis(DiagnosisKind.BUDGET_EXCEEDED)
        _write_pipeline_state(job, state)
        _inject_failures_and_status(job.review_file, state)
        return PhaseResult()

    result = _phase_synthesis(
        job, holistic_content, group_count, merged,
        skipped_groups=n_skipped,
    )
    state.done.add(Phase.SYNTHESIS)
    if result.diagnosis is not None:
        state.failed[Phase.SYNTHESIS] = result.diagnosis
    _write_pipeline_state(job, state)
    _inject_failures_and_status(job.review_file, state)
    return result


def _unaccounted(job: ReviewJob, record: ReviewRecord) -> list[PriorFinding]:
    """The prior findings `record` leaves for synthesis: neither stated nor settled."""
    return passed_over(job.prior, record, job.wt_path, job.pr.head_sha,
                       changed=change_set(job))


def _nothing_new(job: ReviewJob, merged: Merged, failed_groups: list[GroupFailure]) -> bool:
    """Whether synthesis would only restate what the merge already settles.

    An incremental round, every group reported, the groups that ran produced no
    finding, and no prior finding is left for synthesis to account for. A count
    nobody took (`fresh` None) is not a zero. The disprove gate reads the same
    answer: with nothing new there is nothing it has not already challenged.
    """
    if not _is_incremental(job) or failed_groups or merged.fresh != 0:
        return False
    return not _unaccounted(job, merged.record)


def _phase_synthesis(
    job: ReviewJob, holistic_content: str,
    group_count: int, merged: Merged,
    skipped_groups: int = 0,
) -> PhaseResult:
    output = phase_output_path(job.review_file, Phase.SYNTHESIS)
    # Emptied, not just created: a resumed run's earlier attempt must not read
    # as this agent having written.
    Path(output).write_text("")

    max_turns = _synthesis_max_turns(merged.record)
    runner = PhaseRunner(job, Phase.SYNTHESIS)
    synthesis_log = runner.session_log
    # The same reconciliation the run ends with, asked one phase early, while
    # there is still an agent that can act on the answer. Afterwards the only
    # thing left to do about a prior finding nobody accounted for is report it.
    unaccounted = _unaccounted(job, merged.record)
    if unaccounted:
        core.log.dim(
            f"{len(unaccounted)} prior finding{plural(len(unaccounted))} unaccounted for "
            "— synthesis is asked to settle them"
        )
    synth_kwargs = dict(
        holistic_content=holistic_content, group_count=group_count,
        merged_content=merged.text, unaccounted_prior=unaccounted,
    )
    try:
        prompt = build_prompt(
            Phase.SYNTHESIS, job, max_turns=max_turns, **synth_kwargs,
        )
    except PromptTooLarge as exc:
        # The group findings are already on disk; the synthesis agent only
        # writes them up. Merging them mechanically loses the prose and keeps
        # every finding, which is the same trade the fallback below makes for
        # an agent that failed — and it is strictly better than discarding a
        # phase's worth of work because its cover letter would not fit.
        core.log.warn(f"Synthesis cannot be prompted ({exc}) — falling back to mechanical merge")
        _write_mechanical_fallback(
            job, group_count, merged.record, skipped_groups=skipped_groups,
        )
        return PhaseResult.of(
            synthesis_log, diagnosis=Diagnosis(DiagnosisKind.MECHANICAL_FALLBACK),
        )
    core.log.info(f"Phase 4: Synthesis ({max_turns} turns)...")
    core.log.blank()

    # `rc` tracks the latest attempt so the fallback warning below reports the
    # retry's exit code, not the first attempt's. `sent` is the prompt the last
    # invocation actually ran — an overflow rebuild replaces it, and repair
    # must quote that text rather than the original.
    rc = 0
    sent = prompt

    def invoke(text: str, turns: int) -> int:
        nonlocal rc, sent
        sent = text
        rc = runner.invoke(text, turns)
        return rc

    def invoke_logged(text: str) -> int:
        result = invoke(text, max_turns)
        core.log.blank()
        return result

    run_with_overflow_recovery(
        prompt,
        invoke=invoke_logged,
        after=lambda text: _retry_missing_output(
            invoke, text, synthesis_log, output,
            label="Synthesis", max_turns=max_turns,
        ),
        has_output=lambda: _has_output(output),
        rebuild=lambda ladder: build_prompt(
            Phase.SYNTHESIS, job, max_turns=max_turns,
            ladder_bytes=ladder, **synth_kwargs,
        ),
    )

    outcome = (
        ingest_artifact(
            output, sent, invoke, ReviewArtifact,
            diagnose=lambda: diagnose_missing_output(synthesis_log, output),
            check=lambda a: coverage_problems(a, job.focus, derive=needs_derived(job.focus)),
        )
        if _has_output(output) else ArtifactOutcome(None)
    )
    if outcome.ok:
        if outcome.errors:
            core.log.warn(
                "; ".join(e.render() for e in outcome.errors)
                + " — uncovered areas are published as not assessed")
        record = replace(
            outcome.artifact.to_record(
                checkbox=job.mode is Mode.SELF, declinable=declinable_sids(job.prior),
            ),
            # `Review gaps` and anything else the harness wrote into the merge
            # are the run's own sections; an agent has no field to copy them into.
            extra=merged.record.extra,
            focus=covered(job.focus, outcome.artifact.focus,
                          [f["path"] for f in job.pr.files]),
        )
        record = _restore_held_findings(merged.record, record)
        if _is_incremental(job):
            # Synthesis may add cross-cutting nits of its own. A carried one it
            # rewords keeps the prior's `sid`, which `is_new_nit` matches.
            record = drop_out_of_delta_nits(
                record, job.preflight.delta_files, prior_ids(job)).record
        _post_process_review(
            job, record, skipped_groups=skipped_groups, total_groups=group_count,
        )
        diagnosis = None
    else:
        reason = "no output" if not _has_output(output) else "incomplete output"
        detail = f"exited with code {rc} ({reason})" if rc != 0 else reason
        core.log.warn(f"Synthesis agent {detail} — falling back to mechanical merge")
        _write_mechanical_fallback(
            job, group_count, merged.record, skipped_groups=skipped_groups,
        )
        rendered = "; ".join(e.render() for e in outcome.errors)
        diagnosis = Diagnosis(
            DiagnosisKind.MECHANICAL_FALLBACK, detail=rendered,
        ) if outcome.errors else Diagnosis(DiagnosisKind.MECHANICAL_FALLBACK)

    # Charged whether the agent produced a review or the mechanical fallback
    # did: a synthesis that fell back still spent whatever its log records.
    return PhaseResult.of(synthesis_log, diagnosis=diagnosis)


def _carry_forward_prior_findings(
    prior: StoredReview | None, groups: list[Group], skip_indices: set[int],
) -> Merged | None:
    """Extract prior findings for skipped groups and return as merged content."""
    if prior is None or not skip_indices:
        return None
    carried = ReviewRecord()
    for i, grp in enumerate(groups, 1):
        if i not in skip_indices:
            continue
        scoped = _with_sids(_scoped(prior.record, grp.files))
        if scoped.findings:
            # D5: non-finding text in a severity section renders above that
            # section's findings. The heading used to live only in `text`;
            # mechanical-fallback review.md is rendered from the record.
            notes = tuple(
                SeveritySection(
                    severity=sev, note=f"### Carried forward: {grp.name}",
                )
                for sev in dict.fromkeys(f.severity for f in scoped.findings)
            )
            carried = replace(
                carried,
                findings=carried.findings + scoped.findings,
                severity_sections=carried.severity_sections + notes,
            )
    return Merged(carried) if carried.findings else None


def _identify_incremental_skips(
    groups: list[Group], delta_files: list[str],
) -> set[int]:
    """Return 1-based indices of groups with no files in the delta."""
    delta_set = set(delta_files)
    skips: set[int] = set()
    for i, grp in enumerate(groups, 1):
        if not any(f in delta_set for f in grp.files):
            skips.add(i)
    return skips


def _build_group_skips(
    incremental_skips: set[int], recovery_skips: set[int] | None,
) -> dict[int, GroupSkip]:
    """Why each skipped group is being skipped, by 1-based group index.

    Recovery wins where the two overlap: that group's output is already on
    disk, so reusing it beats re-deriving its findings from the prior review.
    No path produces that overlap today — a carried group never runs, so it
    never reaches `state.groups_done` — but the precedence is stated rather
    than assumed, because a dict merge silently picks one either way.
    """
    skips: dict[int, GroupSkip] = {i: GroupSkip.CARRIED for i in incremental_skips}
    skips.update({i: GroupSkip.RECOVERY for i in recovery_skips or ()})
    return skips


def _phase_disprove(job: ReviewJob, record: ReviewRecord) -> DisproveRun:
    counts = record.open_counts
    ms_count = counts[SEVERITY_MUST] + counts[SEVERITY_SHOULD]
    label = PHASES[Phase.DISPROVE].label
    if ms_count == 0:
        core.log.info(f"{label} skipped — no must-fix or should-fix findings")
        return DisproveRun(PhaseResult(), record)
    # The gate reads the record rendered with its evidence: the findings it
    # challenges, their locations, and the code each one quotes. Disprove judges
    # truth, not where to look, so Focus is omitted.
    result = run_phase(job, Phase.DISPROVE,
                       f"{label} — challenging {ms_count} must-fix/should-fix findings...",
                       review_content=render_body(
                           record, evidence=True, omit=frozenset({SECTION_FOCUS}),
                       ))
    if result.artifact is None:
        return DisproveRun(result, record)
    applied = apply_disprove(record, result.artifact)
    if applied.summary.get("falsified", 0) > 0:
        core.log.info(f"{label}: {applied.summary['survived']} survived, {applied.summary['falsified']} falsified")
        _log_disprove_falsified(applied.summary)
    else:
        core.log.info(f"{label}: all {applied.summary['survived']} findings survived")
    return DisproveRun(result, applied.record)


def _log_disprove_falsified(summary: dict) -> None:
    for fid in summary.get("falsified_ids", []):
        reason = summary.get("reasons", {}).get(fid, "")
        core.log.dim(f"  Falsified [{fid}]: {reason}")


def _run_disprove_gate(
    job: ReviewJob, state: PipelineState,
    disprove: bool | None, cost_so_far: float, max_cost: float,
    *, settled: bool = False,
) -> PhaseResult:
    """The disprove gate's session log and what the gate spent.

    Every path out of here records the gate done, the ones that decline to run
    it included. `_resolve_recovery` reads a state file with no disprove entry
    as a run that died inside the gate, and that reading only holds if a gate
    which reached a conclusion always says so.

    Only the two paths that wanted the gate and did not get it record a
    failure, so only those two reach the Agent Failures table and the recover
    hint: declining for a stated reason is a decision, and offering to retry a
    decision would send `--recover` after work nothing went wrong with.
    """
    result = PhaseResult()
    failure: Diagnosis | None = None

    if settled:
        core.log.info("nothing new to challenge; disprove skipped")
    elif not _should_disprove(job, disprove):
        core.log.info("Disprove gate off — keeping all findings")
    elif cost_so_far > max_cost:
        core.log.warn(
            f"Budget exceeded before the disprove gate "
            f"(${cost_so_far:.2f}/${max_cost:.2f}) — findings go unchallenged"
        )
        failure = Diagnosis(DiagnosisKind.BUDGET_EXCEEDED)
    else:
        stored = read_review(Path(job.review_file))
        counts = open_counts(ReviewDocument.from_stored(stored) if stored else None)
        ms_count = counts[SEVERITY_MUST] + counts[SEVERITY_SHOULD]
        if stored and (disprove is True or ms_count >= DISPROVE_MIN_FINDINGS):
            run = _phase_disprove(job, stored.record)
            write_review(Path(job.review_file), run.record, stored.meta)
            result, failure = run.result, run.result.diagnosis
        else:
            core.log.info(
                f"Skipping disprove — only {ms_count} M/S findings "
                f"(threshold: {DISPROVE_MIN_FINDINGS})"
            )

    state.done.add(Phase.DISPROVE)
    if failure:
        state.failed[Phase.DISPROVE] = failure
    _write_pipeline_state(job, state)
    _inject_failures_and_status(job.review_file, state)
    return result


def _phase_merge(
    group_outputs: list[str], failed_groups: list[GroupFailure],
    group_names: tuple[str, ...],
    *,
    declinable: frozenset[str],
) -> Merged:
    core.log.info("Phase 3: Merging findings...")
    ingested = _ingest_group_outputs(
        group_outputs, failed_groups, group_names, declinable,
    )
    record = merge_records(list(ingested.records))
    if ingested.gaps:
        listed = "The following file groups were not reviewed due to agent failure:\n" + "\n".join(
            f"- {f.group}: {f.diagnosis.message}" for f in ingested.gaps)
        record = replace(record, extra=record.extra + (ProseSection("Review gaps", listed),))
    # A rejection ingest added, that the caller did not already count as a
    # failed group, is not a counted zero — `_nothing_new` must not settle.
    ingest_gaps = {g.group for g in ingested.gaps} - {f.group for f in failed_groups}
    return Merged(record, fresh=None if ingest_gaps else len(record.visible))


def _without_out_of_delta_nits(merged: Merged, job: ReviewJob) -> Merged:
    """*merged* minus its new nits outside the delta, recounted."""
    filtered = drop_out_of_delta_nits(merged.record, job.preflight.delta_files, prior_ids(job))
    if not filtered.dropped:
        return merged
    return Merged(filtered.record, fresh=len(filtered.record.visible))


def _merge_round(
    job: ReviewJob, group_outputs: list[str], failed_groups: list[GroupFailure],
    group_names: tuple[str, ...], **merge_kwargs,
) -> Merged:
    """This round's group findings as one merge, without new nits outside the delta.

    *merge_kwargs* go to `_phase_merge` untouched, so a merge that grows a
    keyword does not have to grow this signature as well.
    """
    merged = _phase_merge(group_outputs, failed_groups, group_names, **merge_kwargs)
    if not _is_incremental(job):
        return merged
    return _without_out_of_delta_nits(merged, job)


@dataclass(frozen=True)
class _GroupIngest:
    records: tuple[ReviewRecord, ...]
    gaps: tuple[GroupFailure, ...]


def _malformed_gap(
    name: str, failed_names: set[str], errors: tuple[IngestError, ...],
    gaps: list[GroupFailure],
) -> None:
    if name in failed_names:
        return
    gaps.append(GroupFailure(name, Diagnosis(
        DiagnosisKind.MALFORMED_OUTPUT,
        detail="; ".join(e.render() for e in errors),
    )))
    failed_names.add(name)


def _ingest_group_outputs(
    group_outputs: list[str], failed_groups: list[GroupFailure],
    group_names: tuple[str, ...],
    declinable: frozenset[str],
) -> _GroupIngest:
    # group_outputs lists every group's path, so a rejected file is usually an
    # already-failed group rather than stale recovery output. A rejection whose
    # group is not already in failed_groups is a gap of its own.
    records: list[ReviewRecord] = []
    gaps = list(failed_groups)
    failed_names = {f.group for f in gaps}
    for i, path in enumerate(group_outputs):
        if not Path(path).exists():
            continue
        # A failed group's pre-created file is empty (or whitespace); it is
        # not a malformed artifact.
        if not Path(path).read_text().strip():
            continue
        try:
            # A carried decline is believed only for a finding the prior round held (D19).
            records.append(load_artifact(path, GroupArtifact).to_record(declinable=declinable))
        except IngestRejected as exc:
            core.log.warn(f"{path} rejected ({exc}) — excluding from merge")
            _malformed_gap(
                group_names[i] if i < len(group_names) else f"group-{i + 1}",
                failed_names, exc.errors, gaps)
    return _GroupIngest(tuple(records), tuple(gaps))
