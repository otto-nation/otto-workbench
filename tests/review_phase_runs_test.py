"""Phase outcomes persist on the sidecar, and the single path records them."""

import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import cli.review_orchestrate
from core.phases import Phase
import review.phase_runs
import review.phases
import review.pipeline
import review.steps
from review.paths import phase_output_path, read_review_meta
from review.phases import PhaseResult
from review.state import PipelineState
from review.types import (
    GroupSkip, PhaseRun, Pipeline, PreflightData, ReviewMeta,
    review_meta_from_dict,
)
from review_focus_support import DERIVED_FOCUS
from review_pipeline_support import BIG_PR, Trail, orchestrate_args, pipeline_job
from review_record_support import record, store_record


class TestSidecarLoadsPhases:
    def test_a_sidecar_without_phases_reads_as_empty(self):
        assert review_meta_from_dict({}).phases == ()
        assert review_meta_from_dict({"repo": "org/repo"}).phases == ()

    def test_two_phase_runs_round_trip(self, tmp_path):
        runs = (
            PhaseRun(phase=Phase.SINGLE.value, ran=True),
            PhaseRun(phase=Phase.DISPROVE.value, ran=False, reason="gate off"),
        )
        store_record(tmp_path / "review.md", record(summary="s"), ReviewMeta(phases=runs))
        assert read_review_meta(tmp_path).phases == runs

    def test_an_unknown_phase_string_still_loads(self):
        meta = review_meta_from_dict({
            "phases": [{"phase": "future_phase", "ran": True, "reason": ""}],
        })
        assert meta.phases == (PhaseRun(phase="future_phase", ran=True),)


class TestSingleAgentRecordsPhases:
    def test_a_clean_record_skips_disprove_with_the_zero_count_reason(
        self, tmp_path, monkeypatch,
    ):
        job = pipeline_job(tmp_path)

        def _agent(inv, *_args, **_kwargs) -> int:
            Path(inv.output_path).write_text(json.dumps({
                "summary": "Looks fine.",
                "findings": [],
                "focus": DERIVED_FOCUS,
                "claims": [],
                "behaviours": [],
            }))
            Path(job.session_log).write_text(
                json.dumps({"type": "result", "subtype": "success"}) + "\n")
            return 0

        monkeypatch.setattr(review.pipeline, "build_prompt", lambda *a, **k: "PROMPT")
        monkeypatch.setattr(review.phases, "run_agent", _agent)
        review.pipeline.run_single_agent(job, disprove=True)
        by_phase = {r.phase: r for r in job.phase_runs}
        assert by_phase[Phase.SINGLE.value].ran is True
        assert by_phase[Phase.DISPROVE.value].ran is False
        assert by_phase[Phase.DISPROVE.value].reason == (
            "no must-fix or should-fix finding"
        )


class TestHandoffPersistsPhases:
    def test_orchestrate_writes_the_handoff_phases_to_meta(
        self, tmp_path, monkeypatch,
    ):
        job = pipeline_job(tmp_path, **BIG_PR)
        job.preflight = PreflightData(
            diff="", commit_log="", file_contents={}, file_permissions={},
            instructions_md="", architecture_md="",
        )
        monkeypatch.setattr(review.pipeline, "_group_merge_cap", lambda j: 10 ** 9)
        monkeypatch.setattr(
            review.pipeline, "_resolve_recovery",
            lambda j, groups: SimpleNamespace(
                already_complete=False, cost_so_far=0.0, skip_groups=set(),
                state=None, resume_at_gate=False),
        )
        monkeypatch.setattr(
            review.pipeline, "_single_group_goes_single", lambda *a: True)

        def _single(j, disprove=None):
            store_record(
                j.review_file, record(summary="s"),
                ReviewMeta(head_sha=j.pr.head_sha),
            )

        monkeypatch.setattr(review.pipeline, "run_single_agent", _single)
        monkeypatch.setattr(
            cli.review_orchestrate, "run_static_analysis", lambda *a, **k: [])

        trail = Trail()
        with redirect_stdout(io.StringIO()):
            cli.review_orchestrate._run_phases(trail, orchestrate_args(), job)
        phases = {r.phase: r for r in read_review_meta(Path(job.artifact_dir)).phases}
        assert {Phase.GROUP.value, Phase.SYNTHESIS.value} <= phases.keys()
        assert phases[Phase.GROUP.value].ran is False
        assert phases[Phase.SYNTHESIS.value].reason == "one group covers the PR"
        assert job.pipeline_run is Pipeline.SINGLE


def _by_phase(job):
    return {r.phase: r for r in job.phase_runs}


def _stub_multi_tail(monkeypatch):
    monkeypatch.setattr(review.pipeline, "_group_merge_cap", lambda j: 10 ** 9)
    monkeypatch.setattr(review.pipeline, "_single_group_goes_single", lambda *a: False)
    monkeypatch.setattr(review.pipeline, "_write_pipeline_state", lambda *a: None)
    monkeypatch.setattr(review.pipeline, "_run_holistic_phase", lambda *a: PhaseResult())
    monkeypatch.setattr(
        review.pipeline, "hold_group_workers", lambda *a: __import__("contextlib").nullcontext(1))
    monkeypatch.setattr(
        review.pipeline, "_run_synthesis_or_fallback", lambda *a, **k: PhaseResult())
    monkeypatch.setattr(
        review.pipeline, "_run_disprove_gate", lambda *a, **k: PhaseResult())
    monkeypatch.setattr(review.pipeline, "_consolidate_logs", lambda *a, **k: None)
    monkeypatch.setattr(
        review.pipeline, "_merge_round", lambda *a, **k: SimpleNamespace(
            record=SimpleNamespace(visible=False), fresh=0, carry=lambda r: r))
    monkeypatch.setattr(review.pipeline, "_nothing_new", lambda *a: False)


class TestScanRecordsPhases:
    def test_an_incremental_scan_records_the_skip_reason(self, tmp_path, monkeypatch):
        job = pipeline_job(tmp_path)
        monkeypatch.setattr(review.steps, "_write_pipeline_state", lambda *a: None)
        state = PipelineState(head_sha="abc", group_names=["g"])
        review.steps._run_holistic_phase(job, 4, state, incremental=True)
        scan = _by_phase(job)[Phase.SCOUT.value]
        assert scan.ran is False
        assert scan.reason == "incremental review"

    def test_a_reused_scan_records_exists(self, tmp_path, monkeypatch):
        job = pipeline_job(tmp_path)
        monkeypatch.setattr(review.steps, "_write_pipeline_state", lambda *a: None)
        monkeypatch.setattr(review.steps, "reuse_scan", lambda *a: PhaseResult())
        Path(phase_output_path(job.review_file, Phase.SCOUT)).write_text("{}")
        state = PipelineState(
            head_sha="abc", group_names=["g"], done={Phase.SCOUT})
        review.steps._run_holistic_phase(job, 4, state, incremental=False)
        scan = _by_phase(job)[Phase.SCOUT.value]
        assert scan.ran is True
        assert scan.reason == "exists"


class TestGroupRecordsPhases:
    def test_no_group_records_the_skip(self, tmp_path, monkeypatch):
        job = pipeline_job(tmp_path, **BIG_PR)
        job.skip_phases = frozenset({Phase.GROUP})
        job.preflight = PreflightData(
            diff="", commit_log="", file_contents={}, file_permissions={},
            instructions_md="", architecture_md="",
        )
        _stub_multi_tail(monkeypatch)
        monkeypatch.setattr(
            review.pipeline, "_resolve_recovery",
            lambda j, groups: SimpleNamespace(
                already_complete=False, cost_so_far=0.0, skip_groups=set(),
                state=None, resume_at_gate=False),
        )
        monkeypatch.setattr(
            review.pipeline, "_run_group_phase",
            lambda *a: (_ for _ in ()).throw(AssertionError("groups ran")))
        review.pipeline.run_multi_phase(job, max_parallel=1)
        group = _by_phase(job)[Phase.GROUP.value]
        assert group.ran is False
        assert group.reason == "--no-group"

    def test_recovery_groups_record_resumed(self, tmp_path, monkeypatch):
        job = pipeline_job(tmp_path, **BIG_PR)
        job.preflight = PreflightData(
            diff="", commit_log="", file_contents={}, file_permissions={},
            instructions_md="", architecture_md="",
        )
        _stub_multi_tail(monkeypatch)
        monkeypatch.setattr(
            review.pipeline, "_resolve_recovery",
            lambda j, groups: SimpleNamespace(
                already_complete=False, cost_so_far=0.0, skip_groups={1},
                state=PipelineState(head_sha=job.pr.head_sha, group_names=["g"]),
                resume_at_gate=False),
        )
        monkeypatch.setattr(
            review.pipeline, "_run_group_phase",
            lambda *a: SimpleNamespace(outputs=[], failures=[], cost=0.0))
        review.pipeline.run_multi_phase(job, max_parallel=1)
        group = _by_phase(job)[Phase.GROUP.value]
        assert group.ran is True
        assert group.reason == "resumed from prior attempt"

    def test_carried_groups_name_the_count(self):
        assert review.phase_runs.group_ran_reason(
            {1: GroupSkip.CARRIED, 2: GroupSkip.CARRIED},
        ) == "2 groups carried forward"
        assert review.phase_runs.group_ran_reason(
            {1: GroupSkip.CARRIED},
        ) == "1 group carried forward"
        assert review.phase_runs.group_ran_reason(
            {1: GroupSkip.RECOVERY, 2: GroupSkip.CARRIED},
        ) == "resumed from prior attempt"


class TestDisproveGateRecordsPhases:
    def test_gate_off_records_the_reason(self, tmp_path, monkeypatch):
        job = pipeline_job(tmp_path)
        job.skip_phases = frozenset({Phase.DISPROVE})
        state = PipelineState(head_sha="abc", group_names=["g"])
        monkeypatch.setattr(review.steps, "_write_pipeline_state", lambda *a: None)
        monkeypatch.setattr(review.steps, "_inject_failures_and_status", lambda *a: None)
        review.steps._run_disprove_gate(job, state, None, 0.0, 20.0)
        gate = _by_phase(job)[Phase.DISPROVE.value]
        assert gate.ran is False
        assert gate.reason == "gate off"


class TestPhasesLine:
    def test_no_runs_renders_nothing(self):
        assert review.phase_runs.phases_line(()) == ""
        assert review.phase_runs.phases_line([]) == ""

    def test_a_one_group_handoff_with_no_findings(self):
        reason = "one group covers the PR"
        line = review.phase_runs.phases_line((
            PhaseRun("scout", False, reason),
            PhaseRun("holistic", False, reason),
            PhaseRun("group", False, reason),
            PhaseRun("synthesis", False, reason),
            PhaseRun("single", True),
            PhaseRun("disprove", False, "no must-fix or should-fix finding"),
        ))
        assert line == (
            "Phases: single agent (one group covers the PR) · "
            "scan, groups, synthesis not run · "
            "disprove not run (no must-fix or should-fix finding)"
        )

    def test_ran_phases_then_skips_grouped_by_reason(self):
        line = review.phase_runs.phases_line((
            PhaseRun("holistic", True),
            PhaseRun("group", True),
            PhaseRun("synthesis", False, "settled"),
            PhaseRun("disprove", False, "settled"),
        ))
        assert line == "Phases: scan, groups · synthesis, disprove not run (settled)"

