"""Derived should-fix findings from verified contradicted or unsupported claims."""

import sys
from pathlib import Path

import pytest

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import review.claims
from review.claim_types import ClaimSource, ClaimVerdict
from review.types import FindingOrigin
from review_claims_support import claim


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


def test_checkbox_follows_the_mode():
    claims = (claim(verdict=ClaimVerdict.CONTRADICTED),)
    assert review.claims.derived_findings(claims, checkbox=True)[0].checkbox is True
    assert review.claims.derived_findings(claims, checkbox=False)[0].checkbox is False
