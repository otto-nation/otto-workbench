"""The sidecar's claim-answers ledger, folded at every write of review.json."""

import sys
from dataclasses import replace
from pathlib import Path

import pytest

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import review.claims
import review.outcome
from review.claim_types import ClaimVerdict
from review.paths import read_review_meta
from review.record import Disposition, ReviewRecord
from review.store import StoredReview, archive_prior, read_prior, read_review, update_review, write_review
from review.types import ClaimAnswer, FindingOrigin, PreflightData, ReviewMeta
from review_claims_support import claim
from review_convergence_support import review_job
from review_record_support import finding, record

HEAD = "h" * 40


def _write(tmp_path, rec, *, answers=None):
    meta = ReviewMeta(head_sha=HEAD, claim_answers=answers or {})
    write_review(tmp_path / "review.md", rec, meta)
    return read_review_meta(tmp_path)


def _claim_finding(c, disposition, *, origin=FindingOrigin.CLAIM, reason="r"):
    return finding(
        "S1", "x", origin=origin, stable_id=c.key, path="a.py",
        disposition=disposition, reason=reason,
    )


@pytest.mark.parametrize(
    "disposition",
    [Disposition.DECLINED, Disposition.SKIPPED, Disposition.FALSIFIED],
)
def test_writing_a_review_records_each_answered_claim_finding(tmp_path, disposition):
    c = claim()
    meta = _write(tmp_path, record(_claim_finding(c, disposition), claims=None))
    assert meta.claim_answers[c.key] == ClaimAnswer(disposition, "r", "a.py", HEAD)


@pytest.mark.parametrize(
    "disposition",
    [Disposition.OPEN, Disposition.UNREACHED, Disposition.FIXED],
)
def test_an_open_claim_finding_drops_its_answer(tmp_path, disposition):
    c = claim()
    carried = {c.key: ClaimAnswer(Disposition.DECLINED, "old", "a.py", HEAD)}
    meta = _write(
        tmp_path,
        record(_claim_finding(c, disposition, reason="new"), claims=None),
        answers=carried,
    )
    assert c.key not in meta.claim_answers


def test_assessed_claims_prune_answers_for_claims_that_are_gone(tmp_path):
    c = claim()
    carried = {
        c.key: ClaimAnswer(Disposition.DECLINED, "r", "a.py", HEAD),
        "deadbeef": ClaimAnswer(Disposition.SKIPPED, "s", "b.py", HEAD),
    }
    meta = _write(tmp_path, record(claims=(c,)), answers=carried)
    assert set(meta.claim_answers) == {c.key}


def test_unassessed_claims_keep_every_answer(tmp_path):
    c = claim()
    carried = {
        c.key: ClaimAnswer(Disposition.DECLINED, "r", "a.py", HEAD),
        "deadbeef": ClaimAnswer(Disposition.SKIPPED, "s", "b.py", HEAD),
    }
    meta = _write(tmp_path, record(claims=None), answers=carried)
    assert set(meta.claim_answers) == {c.key, "deadbeef"}


def test_a_writer_finding_never_records_an_answer(tmp_path):
    c = claim()
    meta = _write(
        tmp_path,
        record(
            _claim_finding(c, Disposition.DECLINED, origin=FindingOrigin.WRITER),
            claims=None,
        ),
    )
    assert meta.claim_answers == {}


def test_an_update_review_records_a_fix_pass_answer(tmp_path):
    c = claim()
    rec = record(_claim_finding(c, Disposition.OPEN, reason=""), claims=None)
    write_review(tmp_path / "review.md", rec, ReviewMeta(head_sha=HEAD))

    def skip(record):
        return replace(
            record,
            findings=(replace(record.findings[0], disposition=Disposition.SKIPPED, reason="needs a person"),),
        )

    stored = update_review(tmp_path / "review.md", skip)
    want = ClaimAnswer(Disposition.SKIPPED, "needs a person", "a.py", HEAD)
    assert stored.meta.claim_answers[c.key] == want
    assert stored.meta.claim_answers == read_review_meta(tmp_path).claim_answers


def test_an_empty_stable_id_is_not_folded(tmp_path):
    rec = record(
        finding(
            "S1", "x", origin=FindingOrigin.CLAIM, stable_id="",
            path="a.py", disposition=Disposition.DECLINED, reason="r",
        ),
        claims=None,
    )
    assert _write(tmp_path, rec).claim_answers == {}


PRIOR_HEAD = "p" * 40


def _preflight(delta, prior_head=PRIOR_HEAD):
    return PreflightData(
        diff="", commit_log="", file_contents={}, file_permissions={},
        instructions_md="", architecture_md="",
        delta_files=list(delta), prior_head_sha=prior_head,
    )


def _delta_job(tmp_path, delta, *, head_sha=HEAD, prior=None, prior_head=PRIOR_HEAD):
    return review_job(
        tmp_path, tmp_path / "review", files=("a.py",), head_sha=head_sha,
        prior=prior, preflight=_preflight(delta, prior_head),
    )


def test_an_anchored_answer_expires_when_its_file_changes(tmp_path):
    c = claim()
    answers = {c.key: ClaimAnswer(Disposition.DECLINED, "r", "a.py", PRIOR_HEAD)}
    live = review.claims.live_answers(_delta_job(tmp_path, ["a.py"]), answers)
    assert c.key not in live


def test_an_anchored_answer_stands_while_its_file_is_untouched(tmp_path):
    c = claim()
    answers = {c.key: ClaimAnswer(Disposition.DECLINED, "r", "a.py", PRIOR_HEAD)}
    live = review.claims.live_answers(_delta_job(tmp_path, ["b.py"]), answers)
    assert live[c.key] == answers[c.key]


def test_an_unanchored_answer_outlives_any_change(tmp_path):
    c = claim()
    answers = {c.key: ClaimAnswer(Disposition.SKIPPED, "r", "", PRIOR_HEAD)}
    live = review.claims.live_answers(_delta_job(tmp_path, ["a.py"]), answers)
    assert c.key in live


def test_an_answer_given_at_this_head_stands(tmp_path):
    c = claim()
    answers = {c.key: ClaimAnswer(Disposition.DECLINED, "r", "a.py", HEAD)}
    live = review.claims.live_answers(_delta_job(tmp_path, ["a.py"]), answers)
    assert c.key in live


def test_a_full_review_expires_anchored_answers(tmp_path):
    c = claim()
    job = review_job(
        tmp_path, tmp_path / "review", files=("a.py",), head_sha=HEAD,
        prior=StoredReview(record(), ReviewMeta(head_sha=PRIOR_HEAD)),
    )
    answers = {c.key: ClaimAnswer(Disposition.DECLINED, "r", "a.py", PRIOR_HEAD)}
    assert c.key not in review.claims.live_answers(job, answers)


def _round_job(tmp_path, review_dir, *, claims_text="retries back off"):
    job = review_job(
        tmp_path, review_dir, files=("a.py",), head_sha=HEAD,
        prior=read_prior(review_dir), preflight=_preflight(["b.py"], HEAD),
    )
    job.issue_context = claims_text
    return job


def test_a_declined_claim_finding_survives_a_clean_round(tmp_path):
    c = claim(verdict=ClaimVerdict.CONTRADICTED)
    rec = record(
        finding(
            "S1", "x", origin=FindingOrigin.CLAIM, stable_id=c.key,
            path="a.py", disposition=Disposition.DECLINED, reason="author's call",
        ),
        claims=(c,),
    )
    review_dir = tmp_path / "review"
    write_review(review_dir / "review.md", rec, ReviewMeta(head_sha=HEAD))
    archive_prior(review_dir)
    (tmp_path / "a.py").write_text("delay *= 2\n")
    review.outcome._write_clean_review(_round_job(tmp_path, review_dir), 1, ReviewRecord())
    answer = read_review_meta(review_dir).claim_answers[c.key]
    assert answer.disposition is Disposition.DECLINED
    job3 = _delta_job(
        tmp_path, ["b.py"], head_sha=HEAD, prior_head=HEAD,
        prior=read_review(review_dir / "review.md"),
    )
    out = review.claims.settle(job3, record(claims=(c,)))
    got = next(f for f in out.findings if f.origin is FindingOrigin.CLAIM)
    assert got.disposition is Disposition.DECLINED
    assert got.reason == "author's call"


def test_a_falsified_claim_finding_stays_falsified_after_a_settled_round(tmp_path):
    c = claim(verdict=ClaimVerdict.CONTRADICTED)
    rec = record(
        finding(
            "S1", "x", origin=FindingOrigin.CLAIM, stable_id=c.key,
            path="a.py", disposition=Disposition.FALSIFIED, reason="gate",
        ),
        claims=(c,),
    )
    review_dir = tmp_path / "review"
    write_review(review_dir / "review.md", rec, ReviewMeta(head_sha=HEAD))
    archive_prior(review_dir)
    (tmp_path / "a.py").write_text("delay *= 2\n")
    review.outcome._write_settled_review(_round_job(tmp_path, review_dir), 1, ReviewRecord())
    answer = read_review_meta(review_dir).claim_answers[c.key]
    assert answer.disposition is Disposition.FALSIFIED
    job3 = _delta_job(
        tmp_path, ["b.py"], head_sha=HEAD, prior_head=HEAD,
        prior=read_review(review_dir / "review.md"),
    )
    out = review.claims.settle(job3, record(claims=(c,)))
    assert not any(f.origin is FindingOrigin.CLAIM for f in out.findings)
    assert out.claims[0].finding == ""
    assert out.claims[0].verdict is ClaimVerdict.CONTRADICTED
