"""The sidecar's claim-answers ledger, folded at every write of review.json."""

import sys
from dataclasses import replace
from pathlib import Path

import pytest

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from review.paths import read_review_meta
from review.record import Disposition
from review.store import update_review, write_review
from review.types import ClaimAnswer, FindingOrigin, ReviewMeta
from review_claims_support import claim
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
