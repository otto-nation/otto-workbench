"""Derived should-fix findings from verified contradicted or unsupported claims."""

import hashlib
import sys
from dataclasses import replace
from pathlib import Path

import pytest

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from core.phases import Mode
import review.claims
import review.merge
import review.verify
from review.claim_types import ClaimSource, ClaimVerdict
from review.grammar import FindingIdentity
from review.render import render_body
from review.types import FindingOrigin
from review_claims_support import claim
from review_convergence_support import review_job
from review_record_support import finding, record


@pytest.mark.parametrize(
    "claims, want",
    [
        (None, ()),
        ((), ()),
        ((claim(),), ()),
        ((claim(verdict=ClaimVerdict.UNVERIFIABLE),), ()),
        ((claim(verdict=ClaimVerdict.CONTRADICTED, finding="M1"),), ()),
        (
            (claim(verdict=ClaimVerdict.CONTRADICTED),),
            (("S1", "S", "a.py", "delay *= 2", "Claim contradicted: retries back off"),),
        ),
        (
            (claim(verdict=ClaimVerdict.UNSUPPORTED),),
            (("S1", "S", "", "", "Claim unsupported: retries back off"),),
        ),
        (
            (
                claim(verdict=ClaimVerdict.CONTRADICTED),
                claim(id="C2", verdict=ClaimVerdict.UNSUPPORTED),
            ),
            (
                ("S1", "S", "a.py", "delay *= 2", "Claim contradicted: retries back off"),
                ("S2", "S", "", "", "Claim unsupported: retries back off"),
            ),
        ),
        (
            (
                claim(),
                claim(id="C2", verdict=ClaimVerdict.CONTRADICTED),
            ),
            (
                ("S1", "S", "a.py", "delay *= 2", "Claim contradicted: retries back off"),
            ),
        ),
    ],
    ids=[
        "none",
        "empty",
        "supported",
        "unverifiable",
        "linked",
        "contradicted",
        "unsupported",
        "two-bad",
        "supported-then-contradicted",
    ],
)
def test_derived_findings(claims, want):
    got = review.claims.derived_findings(claims, checkbox=False)
    assert tuple(
        (f.id, f.severity, f.path, f.evidence, f.body.split("\n", 1)[0])
        for f in got
    ) == want
    for finding in got:
        assert finding.line is None
        assert finding.origin is FindingOrigin.CLAIM


def test_a_contradicted_derived_body_is_head_evidence_and_source():
    finding = review.claims.derived_findings(
        (claim(verdict=ClaimVerdict.CONTRADICTED),),
        checkbox=False,
    )[0]
    assert finding.body == (
        "Claim contradicted: retries back off\n"
        "the delay doubles\n"
        "Source: description"
    )


def test_a_derived_body_ends_with_its_source():
    for source in ClaimSource:
        body = review.claims.derived_findings(
            (claim(verdict=ClaimVerdict.CONTRADICTED, source=source),),
            checkbox=False,
        )[0].body
        assert body.splitlines()[-1] == f"Source: {source.value}"


def test_a_multi_line_claim_derives_a_one_line_head():
    finding = review.claims.derived_findings(
        (claim(verdict=ClaimVerdict.CONTRADICTED, text="a\n  b"),),
        checkbox=False,
    )[0]
    assert finding.body.split("\n", 1)[0] == "Claim contradicted: a b"


def test_the_same_claim_derives_the_same_identity():
    claims = (claim(verdict=ClaimVerdict.CONTRADICTED),)
    first = review.claims.derived_findings(claims, checkbox=False)
    second = review.claims.derived_findings(claims, checkbox=False)
    assert first[0].stable_id == second[0].stable_id
    assert first[0].stable_id
    f = first[0]
    assert f.stable_id == FindingIdentity.of_fields(f.path, None, f.body).stable_id
    desc = f.body.split("\n", 1)[0].lstrip()[:80].strip()
    key = f"{f.path.strip().lower()}:{desc.lower()}"
    assert f.stable_id == hashlib.sha256(key.encode()).hexdigest()[:8]
    unsupported = review.claims.derived_findings(
        (claim(verdict=ClaimVerdict.UNSUPPORTED),), checkbox=False,
    )[0]
    assert unsupported.path == ""
    assert unsupported.stable_id == FindingIdentity.of_fields(
        "", None, unsupported.body,
    ).stable_id
    desc = unsupported.body.split("\n", 1)[0].lstrip()[:80].strip()
    key = f":{desc.lower()}"
    assert unsupported.stable_id == hashlib.sha256(key.encode()).hexdigest()[:8]


def test_checkbox_follows_the_mode():
    claims = (claim(verdict=ClaimVerdict.CONTRADICTED),)
    assert review.claims.derived_findings(claims, checkbox=True)[0].checkbox is True
    assert review.claims.derived_findings(claims, checkbox=False)[0].checkbox is False


def _settle_job(tmp_path, *, mode=Mode.SELF):
    (tmp_path / "a.py").write_text("delay *= 2\n")
    return review_job(tmp_path, tmp_path / "review", files=("a.py",), mode=mode)


def test_a_contradicted_claim_derives_a_should_fix(tmp_path):
    rec = record(
        finding("S1", "writer"),
        claims=(
            claim(id="C1", verdict=ClaimVerdict.CONTRADICTED),
            claim(id="C2"),
        ),
    )
    out = review.claims.settle(_settle_job(tmp_path), rec)
    derived = [f for f in out.findings if f.origin is FindingOrigin.CLAIM]
    assert len(derived) == 1
    assert derived[0].id == "S2"
    assert derived[0].severity == "S"
    assert out.claims[0].finding == "S2"


def test_a_claim_whose_code_does_not_verify_derives_nothing(tmp_path):
    rec = record(claims=(
        claim(id="C1", verdict=ClaimVerdict.CONTRADICTED, snippet="nope()"),
    ))
    out = review.claims.settle(_settle_job(tmp_path), rec)
    assert out.claims[0].verdict is ClaimVerdict.UNVERIFIABLE
    assert "cited code not found" in out.claims[0].evidence
    assert not any(f.origin is FindingOrigin.CLAIM for f in out.findings)


def test_identical_unsupported_claims_link_to_their_own_findings(tmp_path):
    rec = record(claims=(
        claim(id="C1", verdict=ClaimVerdict.UNSUPPORTED),
        claim(id="C2", verdict=ClaimVerdict.UNSUPPORTED, source=ClaimSource.TICKET),
    ))
    out = review.claims.settle(_settle_job(tmp_path), rec)
    assert out.claims[0].finding == "S1"
    assert out.claims[1].finding == "S2"
    assert out.finding("S1").body.splitlines()[-1] == "Source: description"
    assert out.finding("S2").body.splitlines()[-1] == "Source: ticket"


def test_settle_checkbox_follows_the_mode(tmp_path):
    rec = record(claims=(claim(verdict=ClaimVerdict.CONTRADICTED),))
    self_out = review.claims.settle(_settle_job(tmp_path), rec)
    pr_out = review.claims.settle(_settle_job(tmp_path, mode=Mode.PR), rec)
    self_derived = [f for f in self_out.findings if f.origin is FindingOrigin.CLAIM]
    pr_derived = [f for f in pr_out.findings if f.origin is FindingOrigin.CLAIM]
    assert self_derived[0].checkbox is True
    assert pr_derived[0].checkbox is False


def test_settle_links_each_claim_to_its_finding(tmp_path):
    rec = record(claims=(
        claim(id="C1", verdict=ClaimVerdict.CONTRADICTED),
        claim(id="C2", verdict=ClaimVerdict.UNSUPPORTED),
    ))
    out = review.claims.settle(_settle_job(tmp_path), rec)
    assert out.claims[0].finding == "S1"
    assert out.claims[1].finding == "S2"
    body = render_body(out)
    c1 = body[body.index("[C1]"):body.index("[C2]")]
    assert "Finding: **[S1]**" in c1


def test_settle_is_idempotent(tmp_path):
    rec = record(claims=(claim(verdict=ClaimVerdict.CONTRADICTED),))
    job = _settle_job(tmp_path)
    once = review.claims.settle(job, rec)
    assert review.claims.settle(job, once) == once


def test_unassessed_claims_settle_to_nothing(tmp_path):
    rec = record(claims=None)
    assert review.claims.settle(_settle_job(tmp_path), rec) is rec


def test_a_dangling_claim_link_is_treated_as_no_link(tmp_path):
    rec = record(
        finding("S1", "writer"),
        claims=(
            claim(id="C1", verdict=ClaimVerdict.CONTRADICTED, finding="M9"),
            claim(id="C2", verdict=ClaimVerdict.CONTRADICTED, text="other claim"),
        ),
    )
    out = review.claims.settle(_settle_job(tmp_path), rec)
    assert out.claims[0].finding == "S2"
    assert out.claims[1].finding == "S3"
    assert out.finding("S2").origin is FindingOrigin.CLAIM
    assert out.finding("S3").origin is FindingOrigin.CLAIM


def test_settle_guard_fires_when_the_tail_is_a_writer_finding(tmp_path, monkeypatch):
    rec = record(
        finding("S1", "writer"),
        claims=(claim(id="C1", verdict=ClaimVerdict.CONTRADICTED),),
    )
    real = review.merge.append_findings

    def reorder(base, added):
        out = real(base, added)
        writer = tuple(f for f in out.findings if f.origin is not FindingOrigin.CLAIM)
        derived = tuple(f for f in out.findings if f.origin is FindingOrigin.CLAIM)
        return replace(out, findings=derived + writer)

    monkeypatch.setattr(review.merge, "append_findings", reorder)
    with pytest.raises(RuntimeError, match=r"found 1 with origins \('WRITER',\)"):
        review.claims.settle(_settle_job(tmp_path), rec)


def test_a_claim_whose_snippet_is_missing_becomes_unverifiable(tmp_path):
    (tmp_path / "a.py").write_text("delay *= 2\n")
    claims = (
        claim(id="C1", snippet="delay *= 2"),
        claim(id="C2", verdict=ClaimVerdict.CONTRADICTED, snippet="nope()"),
        claim(id="C3", snippet="nope()"),
    )
    c1, c2, c3 = review.verify.verified_claims(claims, str(tmp_path), ["a.py"])
    assert c1.verdict is ClaimVerdict.SUPPORTED
    assert c2.verdict is ClaimVerdict.UNVERIFIABLE
    assert c3.verdict is ClaimVerdict.UNVERIFIABLE
    assert "cited code not found" in c2.evidence
    assert "cited code not found" in c3.evidence


def test_supported_claims_are_unverifiable_without_a_worktree():
    got = review.verify.verified_claims((claim(),), "", [])
    assert got[0].verdict is ClaimVerdict.UNVERIFIABLE
    assert "no worktree" in got[0].evidence


def test_a_blank_or_comment_only_snippet_is_unverifiable(tmp_path):
    (tmp_path / "a.py").write_text("delay *= 2\n")
    claims = (
        claim(id="C1", snippet="  \n"),
        claim(id="C2", snippet="# not code"),
    )
    got = review.verify.verified_claims(claims, str(tmp_path), ["a.py"])
    assert {c.verdict for c in got} == {ClaimVerdict.UNVERIFIABLE}


def test_a_comment_only_snippet_reason_is_not_evidence_mismatch(tmp_path):
    (tmp_path / "a.py").write_text("delay *= 2\n")
    got = review.verify.verified_claims(
        (claim(id="C1", snippet="# not code"),), str(tmp_path), ["a.py"],
    )
    evidence = got[0].evidence
    assert "snippet has no matchable lines" in evidence
    assert "evidence mismatch" not in evidence


def test_unsupported_and_unverifiable_claims_are_not_checked(tmp_path):
    (tmp_path / "a.py").write_text("delay *= 2\n")
    claims = (
        claim(id="C1", verdict=ClaimVerdict.UNSUPPORTED, snippet="nope()"),
        claim(id="C2", verdict=ClaimVerdict.UNVERIFIABLE, snippet="also()"),
    )
    assert review.verify.verified_claims(claims, str(tmp_path), ["a.py"]) == claims


def test_unassessed_claims_pass_through(tmp_path):
    assert review.verify.verified_claims(None, str(tmp_path), ["a.py"]) is None
