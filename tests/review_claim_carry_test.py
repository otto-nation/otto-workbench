"""A round without the writer keeps the prior claims ledger when inputs match."""

import sys
from pathlib import Path

import pytest

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import review.claims
import review.outcome
import review.steps
from core.phases import Mode, Phase
from review.claim_types import ClaimVerdict
from review.paths import read_review_meta
from review.record import Disposition, ReviewRecord
from review.state import PipelineState
from review.store import StoredReview, read_review
from review.types import ClaimAnswer, FindingOrigin, PreflightData, ReviewMeta
from review.phases import PhaseResult
from review_claims_support import claim
from review_convergence_support import merged, review_job
from review_record_support import finding, record

HEAD = "h" * 40
PRIOR_HEAD = "p" * 40
BODY = "retries back off"


def _preflight(delta=("b.py",)):
    return PreflightData(
        diff="", commit_log="", file_contents={}, file_permissions={},
        instructions_md="", architecture_md="",
        delta_files=list(delta), prior_head_sha=PRIOR_HEAD,
    )


def _job(
    tmp_path, *, claims=None, findings=(), answers=None, sha=None,
    delta=("b.py",), skip_phases=frozenset(), snippet=True,
):
    (tmp_path / "a.py").write_text("delay *= 2\n" if snippet else "unrelated\n")
    job = review_job(
        tmp_path, tmp_path / "review", files=("a.py",), head_sha=HEAD,
        preflight=_preflight(delta), mode=Mode.PR,
    )
    job.pr.body = BODY
    job.skip_phases = skip_phases
    meta_sha = review.claims.input_sha(job) if sha is None else sha
    job.prior = StoredReview(
        record(*findings, claims=claims),
        ReviewMeta(
            head_sha=PRIOR_HEAD, claims_input_sha=meta_sha,
            claim_answers=answers or {},
        ),
    )
    return job


def _stored(job):
    stored = read_review(Path(job.review_file))
    assert stored is not None
    return stored


def _state():
    return PipelineState(
        head_sha=HEAD, group_names=["g1", "g2"],
        done={Phase.HOLISTIC}, groups_done=[1],
    )


def test_a_settled_round_carries_the_claims_and_their_findings(tmp_path):
    c = claim(verdict=ClaimVerdict.CONTRADICTED)
    job = _job(tmp_path, claims=(c,))
    review.outcome._write_settled_review(job, 1, ReviewRecord())
    stored = _stored(job)
    assert tuple(x.verdict for x in stored.record.claims) == (ClaimVerdict.CONTRADICTED,)
    derived = [f for f in stored.record.visible if f.origin is FindingOrigin.CLAIM]
    assert len(derived) == 1
    assert derived[0].severity == "S"
    assert derived[0].disposition is Disposition.OPEN
    assert stored.record.claims[0].finding == derived[0].id
    assert read_review_meta(Path(job.artifact_dir)).claims_input_sha == (
        review.claims.input_sha(job)
    )
    assert stored.record.open_counts["S"] == 1


def test_a_carried_claim_finding_keeps_its_answer(tmp_path):
    c = claim(verdict=ClaimVerdict.CONTRADICTED)
    answers = {c.key: ClaimAnswer(Disposition.DECLINED, "author's call", "a.py", PRIOR_HEAD)}
    job = _job(tmp_path, claims=(c,), answers=answers)
    review.outcome._write_settled_review(job, 1, ReviewRecord())
    got = next(f for f in _stored(job).record.findings if f.origin is FindingOrigin.CLAIM)
    assert got.disposition is Disposition.DECLINED
    assert got.reason == "author's call"


def test_an_expired_declined_answer_is_re_raised_open_on_a_settled_round(tmp_path):
    c = claim(verdict=ClaimVerdict.CONTRADICTED)
    answers = {c.key: ClaimAnswer(Disposition.DECLINED, "author's call", "a.py", PRIOR_HEAD)}
    job = _job(tmp_path, claims=(c,), answers=answers, delta=("a.py",))
    review.outcome._write_settled_review(job, 1, ReviewRecord())
    got = next(f for f in _stored(job).record.findings if f.origin is FindingOrigin.CLAIM)
    assert got.disposition is Disposition.OPEN


# passes-at-base: pins that changed claim inputs still publish not-assessed
def test_a_changed_description_carries_no_claims(tmp_path):
    c = claim(verdict=ClaimVerdict.CONTRADICTED)
    job = _job(tmp_path, claims=(c,), sha="changed")
    review.outcome._write_settled_review(job, 1, ReviewRecord())
    stored = _stored(job)
    assert stored.record.claims is None
    assert not any(f.origin is FindingOrigin.CLAIM for f in stored.record.findings)


def test_a_carried_claim_whose_code_moved_turns_unverifiable(tmp_path):
    c = claim(verdict=ClaimVerdict.CONTRADICTED)
    job = _job(tmp_path, claims=(c,), snippet=False)
    review.outcome._write_settled_review(job, 1, ReviewRecord())
    stored = _stored(job)
    assert stored.record.claims[0].verdict is ClaimVerdict.UNVERIFIABLE
    assert not any(f.origin is FindingOrigin.CLAIM for f in stored.record.findings)


def test_a_carried_claim_keeps_its_writer_link_by_identity(tmp_path):
    c = claim(verdict=ClaimVerdict.CONTRADICTED, finding="M1")
    prior_writer = finding("M1", "writer bug", stable_id="abcd1234")
    job = _job(tmp_path, claims=(c,), findings=(prior_writer,))
    merged_record = ReviewRecord(findings=(
        finding("M1", "other bug", stable_id="eeeeeeee"),
        finding("M2", "writer bug", sid="abcd1234", stable_id="ffffffff"),
    ))
    review.outcome._write_settled_review(job, 1, merged_record)
    stored = _stored(job)
    assert stored.record.claims[0].finding == "M2"
    assert not any(f.origin is FindingOrigin.CLAIM for f in stored.record.findings)


def test_a_carried_link_to_a_derived_finding_is_cleared_and_re_derives(tmp_path):
    c = claim(verdict=ClaimVerdict.CONTRADICTED, finding="S1")
    prior_derived = finding(
        "S1", "Claim contradicted: retries back off",
        origin=FindingOrigin.CLAIM, stable_id="abcd1234", path="a.py",
    )
    job = _job(tmp_path, claims=(c,), findings=(prior_derived,))
    merged_record = ReviewRecord(findings=(
        finding("M1", "writer bug", stable_id="abcd1234"),
    ))
    review.outcome._write_settled_review(job, 1, merged_record)
    stored = _stored(job)
    derived = [f for f in stored.record.visible if f.origin is FindingOrigin.CLAIM]
    assert len(derived) == 1
    assert stored.record.claims[0].finding == derived[0].id


def test_a_carried_link_to_a_vanished_writer_finding_derives(tmp_path):
    c = claim(verdict=ClaimVerdict.CONTRADICTED, finding="M1")
    prior_writer = finding("M1", "writer bug", stable_id="abcd1234")
    job = _job(tmp_path, claims=(c,), findings=(prior_writer,))
    review.outcome._write_settled_review(job, 1, ReviewRecord())
    stored = _stored(job)
    derived = [f for f in stored.record.visible if f.origin is FindingOrigin.CLAIM]
    assert len(derived) == 1
    assert stored.record.claims[0].finding == derived[0].id


def test_a_no_synthesis_round_keeps_an_open_claim_finding(tmp_path, monkeypatch):
    c = claim(verdict=ClaimVerdict.CONTRADICTED)
    prior_finding = finding(
        "S1", "Claim contradicted: retries back off",
        origin=FindingOrigin.CLAIM, stable_id=c.key, path="a.py",
    )
    job = _job(
        tmp_path, claims=(c,), findings=(prior_finding,),
        skip_phases=frozenset({Phase.SYNTHESIS}),
    )
    monkeypatch.setattr(review.steps, "_write_pipeline_state", lambda *a: None)
    monkeypatch.setattr(review.steps, "_inject_failures_and_status", lambda *a: None)
    review.steps._run_synthesis_or_fallback(
        job, _state(), "", 2, merged(fresh=0), [], 0, 0.0, 20.0,
    )
    stored = _stored(job)
    derived = [f for f in stored.record.visible if f.origin is FindingOrigin.CLAIM]
    assert len(derived) == 1
    assert derived[0].disposition is Disposition.OPEN


def test_a_degraded_writer_round_carries_the_prior_claims(tmp_path):
    c = claim(verdict=ClaimVerdict.CONTRADICTED)
    job = _job(tmp_path, claims=(c,))
    review.outcome._post_process_review(job, ReviewRecord())
    stored = _stored(job)
    assert stored.record.claims is not None
    assert tuple(x.verdict for x in stored.record.claims) == (ClaimVerdict.CONTRADICTED,)
    derived = [f for f in stored.record.visible if f.origin is FindingOrigin.CLAIM]
    assert len(derived) == 1
    assert stored.record.claims[0].finding == derived[0].id


@pytest.mark.parametrize("disposition", [
    Disposition.DECLINED, Disposition.SKIPPED, Disposition.FALSIFIED,
])
def test_an_expired_sidecar_answer_does_not_write_a_clean_review(
    tmp_path, monkeypatch, disposition,
):
    c = claim(verdict=ClaimVerdict.CONTRADICTED)
    job = _job(
        tmp_path, findings=(),
        answers={c.key: ClaimAnswer(disposition, "r", "a.py", PRIOR_HEAD)},
        delta=("a.py",),
    )
    monkeypatch.setattr(review.steps, "_write_pipeline_state", lambda *a: None)
    monkeypatch.setattr(review.steps, "_write_clean_review",
                        lambda *a, **k: pytest.fail("clean review dropped the expired answer"))
    ran = []
    monkeypatch.setattr(review.steps, "_phase_synthesis",
                        lambda *a, **k: ran.append(True) or PhaseResult())
    groups = merged(fresh=0)
    review.steps._run_synthesis_or_fallback(
        job, _state(), "", 2, groups, [], 0, 0.0, 20.0,
        settled=review.steps._nothing_new(job, groups, []))
    assert ran
