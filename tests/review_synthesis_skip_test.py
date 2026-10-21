"""An incremental round with nothing new and nothing unsettled skips synthesis and the gate."""

import contextlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import review.outcome  # noqa: E402
import review.pipeline  # noqa: E402
import review.steps  # noqa: E402
from core.phases import Mode, Phase  # noqa: E402
from review.phases import PhaseResult  # noqa: E402
from review.record import ReviewRecord  # noqa: E402
from review.state import PipelineState  # noqa: E402
from review.store import StoredReview, read_review, write_review  # noqa: E402
from review.types import PreflightData, ReviewMeta  # noqa: E402
from review.verdict import SETTLED_SUMMARY  # noqa: E402
from review.verify import PostProcessed  # noqa: E402
from review_convergence_support import finding, merged, review_job  # noqa: E402

CARRIED = finding("S1", "carried from a skipped group", path="a.py", line=3, sid="sids1")


def _job(tmp_path, prior=None, incremental=True, mode=Mode.SELF):
    job = review_job(tmp_path, tmp_path / "r", head_sha="h" * 40, prior=prior, mode=mode)
    job.preflight = PreflightData(
        diff="", commit_log="", file_contents={}, file_permissions={},
        instructions_md="", architecture_md="",
        delta_files=["b.py"], prior_head_sha="p" * 40 if incremental else "")
    return job


def _state():
    return PipelineState(head_sha="h" * 40, group_names=["g1", "g2"],
                         done={Phase.HOLISTIC}, groups_done=[1])


def test_nothing_new_needs_an_incremental_round(tmp_path):
    assert not review.steps._nothing_new(_job(tmp_path, incremental=False), merged(fresh=0), [])


def test_nothing_new_needs_a_counted_zero(tmp_path):
    assert not review.steps._nothing_new(_job(tmp_path), merged(fresh=1), [])
    assert not review.steps._nothing_new(_job(tmp_path), merged(), [])


def test_nothing_new_is_false_with_a_failed_group(tmp_path):
    assert not review.steps._nothing_new(_job(tmp_path), merged(fresh=0), ["failure"])


def test_an_unaccounted_prior_finding_still_needs_synthesis(tmp_path):
    prior = StoredReview(ReviewRecord(findings=(finding("M1", "x", path="b.py", line=1),)),
                         ReviewMeta(head_sha="p" * 40))
    assert not review.steps._nothing_new(_job(tmp_path, prior), merged(fresh=0), [])


def test_nothing_new_with_everything_settled(tmp_path):
    assert review.steps._nothing_new(_job(tmp_path), merged(fresh=0), [])


def test_nothing_new_skips_synthesis_and_states_a_verdict(tmp_path, monkeypatch):
    """PR mode, because a self-review states no verdict on any path (`states_verdict`)."""
    job = _job(tmp_path, mode=Mode.PR)
    monkeypatch.setattr(review.steps, "_write_pipeline_state", lambda *a: None)
    monkeypatch.setattr(review.outcome, "post_process_record",
                        lambda record, *a, **k: PostProcessed(record, None))
    monkeypatch.setattr(review.steps, "_phase_synthesis",
                        lambda *a, **k: pytest.fail("synthesis ran with nothing new"))
    result = review.steps._run_synthesis_or_fallback(
        job, _state(), "", 2, merged(ReviewRecord(findings=(CARRIED,)), fresh=0), [], 1,
        0.0, 20.0, settled=True)
    assert result.cost == 0.0
    stored = read_review(Path(job.review_file))
    assert stored.record.summary == f"1 finding across 2 groups. {SETTLED_SUMMARY}"
    assert stored.record.verdict
    assert [f.id for f in stored.record.findings] == ["S1"]


def test_a_settled_round_skips_the_disprove_gate(tmp_path, monkeypatch):
    """A must-fix on disk and `disprove=True` would run the gate; settled says it has nothing new."""
    job = _job(tmp_path, mode=Mode.PR)
    write_review(Path(job.review_file), ReviewRecord(findings=(CARRIED,)), ReviewMeta())
    state = _state()
    monkeypatch.setattr(review.steps, "_write_pipeline_state", lambda *a: None)
    monkeypatch.setattr(review.steps, "_inject_failures_and_status", lambda *a: None)
    monkeypatch.setattr(review.steps, "_phase_disprove",
                        lambda *a, **k: pytest.fail("the gate ran with nothing new"))
    result = review.steps._run_disprove_gate(job, state, True, 0.0, 20.0, settled=True)
    assert result.cost == 0.0
    assert Phase.DISPROVE in state.done and Phase.DISPROVE not in state.failed


def test_run_multi_phase_hands_settled_to_synthesis_and_the_gate(tmp_path, monkeypatch):
    job = review_job(tmp_path, tmp_path / "r", head_sha="h" * 40, files=["a.py"])
    seen = {}
    monkeypatch.setattr(review.pipeline, "_group_merge_cap", lambda j: 10 ** 9)
    monkeypatch.setattr(review.pipeline, "_resolve_recovery", lambda j, groups: SimpleNamespace(
        already_complete=False, cost_so_far=0.0, skip_groups=set(), state=None,
        resume_at_gate=False))
    monkeypatch.setattr(review.pipeline, "_write_pipeline_state", lambda *a: None)
    monkeypatch.setattr(review.pipeline, "_run_holistic_phase", lambda *a: PhaseResult())
    monkeypatch.setattr(review.pipeline, "hold_group_workers",
                        lambda *a: contextlib.nullcontext(1))
    monkeypatch.setattr(review.pipeline, "_run_group_phase",
                        lambda *a: SimpleNamespace(outputs=[], failures=[], cost=0.0))
    monkeypatch.setattr(review.pipeline, "_merge_round", lambda *a, **k: merged(fresh=0))
    monkeypatch.setattr(review.pipeline, "_nothing_new", lambda *a: True)
    monkeypatch.setattr(review.pipeline, "_run_synthesis_or_fallback",
                        lambda *a, **k: seen.update(synthesis=k) or PhaseResult())
    monkeypatch.setattr(review.pipeline, "_run_disprove_gate",
                        lambda *a, **k: seen.update(gate=k) or PhaseResult())
    monkeypatch.setattr(review.pipeline, "_consolidate_logs", lambda *a, **k: None)
    review.pipeline.run_multi_phase(job, max_parallel=1)
    assert seen["synthesis"].get("settled") is True
    assert seen["gate"].get("settled") is True
