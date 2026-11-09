"""An incremental round with nothing new and nothing unsettled skips synthesis and the gate."""

import contextlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import review.claims  # noqa: E402
import review.outcome  # noqa: E402
import review.phases  # noqa: E402
import review.pipeline  # noqa: E402
import review.steps  # noqa: E402
from review.claim_types import ClaimVerdict  # noqa: E402
from core.phases import Mode, Phase  # noqa: E402
from review.phases import PhaseResult  # noqa: E402
from review.record import Disposition, ReviewRecord  # noqa: E402
from review.state import PipelineState  # noqa: E402
from review.store import StoredReview, read_review, write_review  # noqa: E402
from review.types import FindingOrigin, Group, PreflightData, ReviewMeta  # noqa: E402
from review.verdict import (  # noqa: E402
    BUDGET_SUMMARY, CLEAN_SUMMARY, CLEAN_VERDICT, SETTLED_SUMMARY,
    mechanical_verdict,
)
from review.verify import PostProcessed  # noqa: E402
from review_claims_support import claim  # noqa: E402
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


def _carried_claim_job(tmp_path, *, path="a.py", sha=None, delta=None, **kwargs):
    job = _job(tmp_path, **kwargs)
    job.pr.body = "retries back off"
    if delta is not None:
        job.preflight.delta_files = list(delta)
    c = claim(verdict=ClaimVerdict.CONTRADICTED)
    derived = finding(
        "S1", "Claim contradicted: retries back off",
        path=path, origin=FindingOrigin.CLAIM, stable_id=c.key,
    )
    meta_sha = review.claims.input_sha(job) if sha is None else sha
    job.prior = StoredReview(
        ReviewRecord(findings=(derived,), claims=(c,)),
        ReviewMeta(head_sha="p" * 40, claims_input_sha=meta_sha),
    )
    return job


def test_nothing_new_needs_an_incremental_round(tmp_path):
    assert not review.steps._nothing_new(_job(tmp_path, incremental=False), merged(fresh=0), [])


def test_nothing_new_needs_a_counted_zero(tmp_path):
    assert not review.steps._nothing_new(_job(tmp_path), merged(fresh=1), [])
    assert not review.steps._nothing_new(_job(tmp_path), merged(), [])


def test_nothing_new_is_false_with_a_failed_group(tmp_path):
    assert not review.steps._nothing_new(_job(tmp_path), merged(fresh=0), ["failure"])


def test_a_skipped_group_does_not_carry_a_derived_finding():
    prior = StoredReview(
        ReviewRecord(findings=(
            finding("S1", "a real bug", path="a.py", line=1),
            finding(
                "S2", "Claim contradicted: retries back off",
                path="a.py", origin=FindingOrigin.CLAIM,
            ),
        )),
        ReviewMeta(),
    )
    carried = review.steps._carry_forward_prior_findings(
        prior, [Group("g1", ["a.py"], 1)], {1},
    )
    assert carried is not None
    assert [f.id for f in carried.record.findings] == ["S1"]


def test_an_unaccounted_prior_finding_still_needs_synthesis(tmp_path):
    prior = StoredReview(ReviewRecord(findings=(finding("M1", "x", path="b.py", line=1),)),
                         ReviewMeta(head_sha="p" * 40))
    assert not review.steps._nothing_new(_job(tmp_path, prior), merged(fresh=0), [])


def test_an_open_derived_prior_finding_still_needs_synthesis(tmp_path):
    prior = StoredReview(
        ReviewRecord(findings=(
            finding(
                "S1", "Claim contradicted: retries back off",
                path="a.py", origin=FindingOrigin.CLAIM,
            ),
        )),
        ReviewMeta(head_sha="p" * 40),
    )
    assert not review.steps._nothing_new(_job(tmp_path, prior), merged(fresh=0), [])


def test_an_untouched_claim_finding_settles_without_the_writer(tmp_path):
    job = _carried_claim_job(tmp_path)
    assert review.steps._nothing_new(job, merged(fresh=0), [])


# passes-at-base: pins a case that must still force the writer
def test_an_unanchored_claim_finding_still_needs_the_writer(tmp_path):
    job = _carried_claim_job(tmp_path, path="")
    assert not review.steps._nothing_new(job, merged(fresh=0), [])


# passes-at-base: pins a case that must still force the writer
def test_a_touched_claim_finding_still_needs_the_writer(tmp_path):
    job = _carried_claim_job(tmp_path, delta=["a.py"])
    assert not review.steps._nothing_new(job, merged(fresh=0), [])


# passes-at-base: pins a case that must still force the writer
def test_changed_claim_inputs_still_need_the_writer(tmp_path):
    job = _carried_claim_job(tmp_path, sha="x")
    assert not review.steps._nothing_new(job, merged(fresh=0), [])


def test_a_fixed_derived_prior_finding_does_not_block_nothing_new(tmp_path):
    prior = StoredReview(
        ReviewRecord(findings=(
            finding(
                "S1", "Claim contradicted: retries back off",
                path="a.py", origin=FindingOrigin.CLAIM,
                disposition=Disposition.FIXED,
            ),
        )),
        ReviewMeta(head_sha="p" * 40),
    )
    assert review.steps._nothing_new(_job(tmp_path, prior), merged(fresh=0), [])


@pytest.mark.parametrize("disposition", [Disposition.DECLINED, Disposition.SKIPPED])
def test_an_answered_derived_prior_finding_does_not_block_nothing_new(
    tmp_path, disposition,
):
    prior = StoredReview(
        ReviewRecord(findings=(
            finding(
                "S1", "Claim contradicted: retries back off",
                path="a.py", origin=FindingOrigin.CLAIM,
                disposition=disposition,
            ),
        )),
        ReviewMeta(head_sha="p" * 40),
    )
    assert review.steps._nothing_new(_job(tmp_path, prior), merged(fresh=0), [])


def test_nothing_new_with_everything_settled(tmp_path):
    assert review.steps._nothing_new(_job(tmp_path), merged(fresh=0), [])


def test_a_rejected_group_is_not_a_counted_zero(tmp_path):
    """A merge-time ingest rejection is not `fresh=0` and does not settle the round."""
    path = tmp_path / "group-1.json"
    path.write_text("not json")
    out = review.steps._phase_merge([str(path)], [], ("g1",), declinable=frozenset())
    assert out.fresh is None
    assert not review.steps._nothing_new(_job(tmp_path), out, [])


def test_an_untouched_open_claim_finding_is_never_a_clean_review(
    tmp_path, monkeypatch,
):
    job = _carried_claim_job(tmp_path, mode=Mode.PR)
    monkeypatch.setattr(review.steps, "_write_pipeline_state", lambda *a: None)
    monkeypatch.setattr(review.steps, "_inject_failures_and_status", lambda *a: None)
    monkeypatch.setattr(review.steps, "_write_clean_review",
                        lambda *a, **k: pytest.fail("clean review dropped the derived finding"))
    wrote = []
    monkeypatch.setattr(review.steps, "_write_settled_review",
                        lambda *a, **k: wrote.append("settled"))
    monkeypatch.setattr(review.steps, "_phase_synthesis",
                        lambda *a, **k: pytest.fail("writer ran for an untouched claim finding"))
    groups = merged(fresh=0)
    review.steps._run_synthesis_or_fallback(
        job, _state(), "", 2, groups, [], 0,
        0.0, 20.0, settled=review.steps._nothing_new(job, groups, []))
    assert wrote == ["settled"]


def test_an_open_derived_prior_finding_does_not_write_a_clean_review(
    tmp_path, monkeypatch,
):
    """Groups added nothing; the prior holds only a derived finding. Writer runs."""
    prior = StoredReview(
        ReviewRecord(findings=(
            finding(
                "S1", "Claim contradicted: retries back off",
                path="a.py", origin=FindingOrigin.CLAIM,
            ),
        )),
        ReviewMeta(head_sha="p" * 40),
    )
    job = _job(tmp_path, prior=prior, mode=Mode.PR)
    monkeypatch.setattr(review.steps, "_write_pipeline_state", lambda *a: None)
    monkeypatch.setattr(review.steps, "_inject_failures_and_status", lambda *a: None)
    monkeypatch.setattr(review.steps, "_write_clean_review",
                        lambda *a, **k: pytest.fail("clean review dropped the derived finding"))
    monkeypatch.setattr(review.steps, "_write_settled_review",
                        lambda *a, **k: pytest.fail("settled path dropped the derived finding"))
    ran = []
    monkeypatch.setattr(review.steps, "_phase_synthesis",
                        lambda *a, **k: ran.append(True) or PhaseResult())
    groups = merged(fresh=0)
    result = review.steps._run_synthesis_or_fallback(
        job, _state(), "", 2, groups, [], 0,
        0.0, 20.0, settled=review.steps._nothing_new(job, groups, []))
    assert ran
    assert result.cost == 0.0


def test_a_no_group_round_with_an_open_derived_finding_is_not_all_groups_failed(
    tmp_path, monkeypatch,
):
    """Zero groups is not every group failing; the writer re-judges the prior."""
    prior = StoredReview(
        ReviewRecord(findings=(
            finding(
                "S1", "Claim contradicted: retries back off",
                path="a.py", origin=FindingOrigin.CLAIM,
            ),
        )),
        ReviewMeta(head_sha="p" * 40),
    )
    job = _job(tmp_path, prior=prior, mode=Mode.PR)
    state = _state()
    monkeypatch.setattr(review.steps, "_write_pipeline_state", lambda *a: None)
    monkeypatch.setattr(review.steps, "_inject_failures_and_status", lambda *a: None)
    monkeypatch.setattr(review.steps, "_write_clean_review",
                        lambda *a, **k: pytest.fail("clean review dropped the derived finding"))
    monkeypatch.setattr(review.steps, "_write_settled_review",
                        lambda *a, **k: pytest.fail("settled path dropped the derived finding"))
    monkeypatch.setattr(review.steps, "_build_mechanical_fallback",
                        lambda *a, **k: pytest.fail("mechanical fallback for a no-group round"))
    ran = []
    monkeypatch.setattr(review.steps, "_phase_synthesis",
                        lambda *a, **k: ran.append(True) or PhaseResult())
    groups = merged(fresh=0)
    result = review.steps._run_synthesis_or_fallback(
        job, state, "", 0, groups, [], 0,
        0.0, 20.0, settled=review.steps._nothing_new(job, groups, []))
    assert ran
    assert result.cost == 0.0
    assert Phase.SYNTHESIS not in state.failed


def test_a_round_with_no_open_derived_finding_still_writes_a_clean_review(
    tmp_path, monkeypatch,
):
    job = _job(tmp_path, mode=Mode.PR)
    monkeypatch.setattr(review.steps, "_write_pipeline_state", lambda *a: None)
    monkeypatch.setattr(review.steps, "_phase_synthesis",
                        lambda *a, **k: pytest.fail("synthesis ran on a clean round"))
    result = review.steps._run_synthesis_or_fallback(
        job, _state(), "", 2, merged(fresh=0), [], 0,
        0.0, 20.0, settled=False)
    assert result.cost == 0.0
    stored = read_review(Path(job.review_file))
    assert stored.record.summary == f"No findings across 2 groups. {CLEAN_SUMMARY}"
    assert stored.record.verdict == CLEAN_VERDICT.strip()


def test_nothing_new_skips_synthesis_and_states_a_verdict(tmp_path, monkeypatch):
    """PR mode, because a self-review states no verdict on any path (`states_verdict`)."""
    job = _job(tmp_path, mode=Mode.PR)
    monkeypatch.setattr(review.steps, "_write_pipeline_state", lambda *a: None)
    reconciled = []
    monkeypatch.setattr(review.outcome, "post_process_record",
                        lambda record, *a, **k: reconciled.append(record) or PostProcessed(record, None))
    monkeypatch.setattr(review.steps, "_phase_synthesis",
                        lambda *a, **k: pytest.fail("synthesis ran with nothing new"))
    result = review.steps._run_synthesis_or_fallback(
        job, _state(), "", 2, merged(ReviewRecord(findings=(CARRIED,)), fresh=0), [], 1,
        0.0, 20.0, settled=True)
    assert result.cost == 0.0
    stored = read_review(Path(job.review_file))
    assert stored.record.summary == f"1 finding across 2 groups. {SETTLED_SUMMARY}"
    assert stored.record.verdict == mechanical_verdict(stored.record.open_counts).strip()
    assert reconciled, "reconciliation did not run"
    assert [f.id for f in stored.record.findings] == ["S1"]


def test_a_settled_over_budget_round_still_ships_settled(tmp_path, monkeypatch):
    """Settled is decided before the budget; an over-budget settled round is still settled."""
    job = _job(tmp_path, mode=Mode.PR)
    state = _state()
    monkeypatch.setattr(review.steps, "_write_pipeline_state", lambda *a: None)
    monkeypatch.setattr(review.outcome, "post_process_record",
                        lambda record, *a, **k: PostProcessed(record, None))
    monkeypatch.setattr(review.steps, "_phase_synthesis",
                        lambda *a, **k: pytest.fail("synthesis ran when settled"))
    result = review.steps._run_synthesis_or_fallback(
        job, state, "", 2, merged(ReviewRecord(findings=(CARRIED,)), fresh=0), [], 1,
        25.0, 20.0, settled=True)
    assert result.cost == 0.0
    stored = read_review(Path(job.review_file))
    assert SETTLED_SUMMARY in stored.record.summary
    assert BUDGET_SUMMARY not in stored.record.summary
    assert Phase.SYNTHESIS in state.done and Phase.SYNTHESIS not in state.failed


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


@pytest.mark.parametrize("nothing_new", [True, False], ids=["settled", "unsettled"])
def test_run_multi_phase_hands_settled_to_synthesis_and_the_gate(
    tmp_path, monkeypatch, nothing_new,
):
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
    monkeypatch.setattr(review.pipeline, "_nothing_new", lambda *a: nothing_new)
    monkeypatch.setattr(review.pipeline, "_run_synthesis_or_fallback",
                        lambda *a, **k: seen.update(synthesis=k) or PhaseResult())
    monkeypatch.setattr(review.pipeline, "_run_disprove_gate",
                        lambda *a, **k: seen.update(gate=k) or PhaseResult())
    monkeypatch.setattr(review.pipeline, "_consolidate_logs", lambda *a, **k: None)
    review.pipeline.run_multi_phase(job, max_parallel=1)
    assert seen["synthesis"].get("settled") is nothing_new
    assert seen["gate"].get("settled") is nothing_new


def test_phase_one_focus_reaches_the_groups(tmp_path, monkeypatch):
    monkeypatch.setattr(review.steps, "HOLISTIC_MIN_GROUPS", 1)
    job = review_job(tmp_path, tmp_path / "r", head_sha="h" * 40, files=["a.py"])
    prompts = []
    scout_doc = json.dumps({
        "focus": [{"title": "Auth-retry-token", "rationale": "why", "paths": ["a.py"]}],
    })
    group_doc = json.dumps({
        "findings": [{"id": "N1", "path": "a.py", "line": 1, "body": "n"}],
    })

    def fake_agent(inv, throttle=None):
        prompts.append(inv.prompt)
        Path(inv.session_log).write_text(
            json.dumps({"type": "result", "subtype": "success", "num_turns": 1}) + "\n")
        body = scout_doc if Path(inv.output_path).name.startswith("scout") else group_doc
        Path(inv.output_path).write_text(body)
        return 0

    monkeypatch.setattr(review.phases, "run_agent", fake_agent)
    monkeypatch.setattr(review.pipeline, "_group_merge_cap", lambda j: 10 ** 9)
    monkeypatch.setattr(review.pipeline, "_resolve_recovery", lambda j, groups: SimpleNamespace(
        already_complete=False, cost_so_far=0.0, skip_groups=set(), state=None,
        resume_at_gate=False))
    monkeypatch.setattr(review.pipeline, "_write_pipeline_state", lambda *a: None)
    monkeypatch.setattr(review.pipeline, "hold_group_workers",
                        lambda *a: contextlib.nullcontext(1))
    monkeypatch.setattr(review.pipeline, "_run_synthesis_or_fallback",
                        lambda *a, **k: PhaseResult())
    monkeypatch.setattr(review.pipeline, "_run_disprove_gate",
                        lambda *a, **k: PhaseResult())
    monkeypatch.setattr(review.pipeline, "_consolidate_logs", lambda *a, **k: None)
    review.pipeline.run_multi_phase(job, max_parallel=1)
    group_prompts = [p for p in prompts if "GROUP" in p]
    assert group_prompts and any("Auth-retry-token" in p for p in group_prompts)
