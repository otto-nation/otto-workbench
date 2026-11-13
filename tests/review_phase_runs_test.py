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

from agent.types import EFFORT_PRESETS
import cli.review_orchestrate
from core.phases import Effort, Phase
from gh.types import PRContext, PRMetadata
import review.phases
import review.pipeline
from review.paths import read_review_meta
from review.types import (
    PhaseRun, Pipeline, PreflightData, ReviewJob, ReviewMeta, review_meta_from_dict,
)
from review_focus_support import DERIVED_FOCUS
from review_record_support import record, store_record

_PRESET = EFFORT_PRESETS[Effort.MEDIUM]
_BIG_PR = {
    "additions": _PRESET.multi_phase_line_threshold * 8,
    "deletions": _PRESET.multi_phase_line_threshold * 4,
    "changed_files": _PRESET.multi_phase_file_threshold * 12,
}


class _Trail:
    def __init__(self):
        self.decisions: list[tuple[str, str, dict]] = []

    def decision(self, name, detail, reason="", data=None):
        self.decisions.append((name, reason, data or {}))

    def info(self, *_args, **_kwargs):
        pass

    def span(self, *_args, **_kwargs):
        from contextlib import nullcontext
        return nullcontext()


def _job(tmp_path, **pr_overrides) -> ReviewJob:
    pr_fields = {
        "title": "t", "body": "", "head": "feat", "base": "main",
        "head_sha": "abc", "additions": 10, "deletions": 0,
        "changed_files": 1, "files": [{"path": "a.py", "additions": 10, "deletions": 0}],
    }
    pr_fields.update(pr_overrides)
    return ReviewJob(
        repo="org/repo", pr_number="42", pr=PRMetadata(**pr_fields),
        ctx=PRContext(), wt_path=str(tmp_path),
        review_file=str(tmp_path / "review.md"),
        session_log=str(tmp_path / "session.jsonl"),
        effort=Effort.MEDIUM,
    )


def _args() -> SimpleNamespace:
    return SimpleNamespace(
        max_parallel=2, max_cost=20.0, max_groups=None,
        disprove=None, fix=False, effort=None,
    )


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
        job = _job(tmp_path)

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
        job = _job(tmp_path, **_BIG_PR)
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

        trail = _Trail()
        with redirect_stdout(io.StringIO()):
            cli.review_orchestrate._run_phases(trail, _args(), job)
        phases = {r.phase: r for r in read_review_meta(Path(job.artifact_dir)).phases}
        assert {Phase.GROUP.value, Phase.SYNTHESIS.value} <= phases.keys()
        assert phases[Phase.GROUP.value].ran is False
        assert phases[Phase.SYNTHESIS.value].reason == "one group covers the PR"
        assert job.pipeline_run is Pipeline.SINGLE
