"""Claims as data: types, ingest kinds, the writer's caps, check, and fingerprint."""

import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from review.budget import MAX_CLAIM_BYTES
from review.claim_types import ClaimEntry, ClaimReport, ClaimSource, ClaimVerdict
from review.claims import check_warning, claims_of, claims_problems, input_sha
from review.ingest import ArtifactOutcome, IngestError, IngestErrorKind
from review_claims_support import CLAIM_DOC, claim

_OK = ClaimReport(**CLAIM_DOC)


def test_a_claim_entry_coerces_its_enums():
    entry = ClaimEntry("C1", "ticket", "t", "unsupported", "e")
    assert entry.source is ClaimSource.TICKET
    assert entry.verdict is ClaimVerdict.UNSUPPORTED


def test_a_claim_key_hashes_source_and_collapsed_text():
    entry = claim(source=ClaimSource.DESCRIPTION, text="Retries  back\noff")
    assert entry.key == hashlib.sha256(
        b"claim\0description\0retries back off",
    ).hexdigest()[:8]


def test_a_claim_key_ignores_case_and_wrapping():
    compact = claim(text="retries back off")
    wrapped = claim(text="Retries  back\noff")
    shouted = claim(text="RETRIES BACK OFF")
    assert compact.key == wrapped.key == shouted.key


def test_the_verdicts_are_the_four_on_disk_values():
    assert [v.value for v in ClaimVerdict] == [
        "supported", "contradicted", "unsupported", "unverifiable",
    ]


@pytest.mark.parametrize(
    "has_source, claims, finding_ids, kind, at",
    [
        (False, (_OK,), frozenset(), None, None),
        (True, None, frozenset(), IngestErrorKind.MISSING_CLAIMS, "$.claims"),
        (True, (), frozenset(), None, None),
        (True, (_OK,) * 13, frozenset(), IngestErrorKind.CLAIM_COUNT, None),
        (
            True,
            (replace(_OK, snippet="x" * (MAX_CLAIM_BYTES + 1)),),
            frozenset(),
            IngestErrorKind.CLAIM_SIZE,
            "$.claims[0].snippet",
        ),
        (True, (replace(_OK, evidence=""),), frozenset(), IngestErrorKind.BAD_CLAIM, None),
        (True, (replace(_OK, snippet=""),), frozenset(), IngestErrorKind.BAD_CLAIM, None),
        (True, (replace(_OK, snippet="  \n"),), frozenset(), IngestErrorKind.BAD_CLAIM, None),
        (
            True,
            (replace(_OK, verdict=ClaimVerdict.CONTRADICTED, path="a.py:3"),),
            frozenset(),
            IngestErrorKind.BAD_CLAIM,
            None,
        ),
        (
            True,
            (replace(_OK, verdict=ClaimVerdict.UNSUPPORTED, path=""),),
            frozenset(),
            None,
            None,
        ),
        (
            True,
            (replace(_OK, finding="S9"),),
            frozenset({"S1"}),
            IngestErrorKind.BAD_CLAIM,
            None,
        ),
        (True, (replace(_OK, finding="S1"),), frozenset({"S1"}), None, None),
    ],
    ids=[
        "no-source-entries",
        "source-none",
        "source-empty",
        "count-13",
        "snippet-over-cap",
        "empty-evidence",
        "supported-no-snippet",
        "supported-whitespace-snippet",
        "contradicted-line-in-path",
        "unsupported-no-path",
        "finding-absent",
        "finding-present",
    ],
)
def test_claims_problems(has_source, claims, finding_ids, kind, at):
    errors = claims_problems(claims, finding_ids, has_claim_source=has_source)
    if kind is None:
        assert errors == ()
        return
    assert errors[0].kind is kind
    if at is not None:
        assert errors[0].at == at


def _job(*, body="", issue_link="", issue_context=""):
    return SimpleNamespace(
        pr=SimpleNamespace(body=body),
        issue_link=issue_link,
        issue_context=issue_context,
    )


def test_input_sha_is_empty_without_a_source():
    assert input_sha(_job()) == ""


def test_input_sha_moves_with_the_body_and_the_issue():
    empty = input_sha(_job())
    body = input_sha(_job(body="retries back off"))
    issue = input_sha(_job(issue_context="the ticket"))
    assert body != empty
    assert issue != empty
    assert body != issue


def test_input_sha_projects_title_and_body_only():
    linear = json.dumps({
        "title": "Retries", "description": "Back off",
        "state": {"name": "In Progress"}, "labels": [{"name": "bug"}],
        "attachments": [{"url": "x"}],
    })
    churned = json.dumps({
        "title": "Retries", "description": "Back off",
        "state": {"name": "Done"}, "labels": [], "attachments": [],
    })
    github = json.dumps({
        "title": "Retries", "body": "Back off", "state": "OPEN",
    })
    assert input_sha(_job(issue_context=linear)) == input_sha(
        _job(issue_context=churned),
    )
    assert input_sha(_job(issue_context=linear)) == input_sha(
        _job(issue_context=github),
    )
    titled = json.dumps({"title": "Other", "description": "Back off"})
    bodied = json.dumps({"title": "Retries", "description": "Changed"})
    assert input_sha(_job(issue_context=linear)) != input_sha(
        _job(issue_context=titled),
    )
    assert input_sha(_job(issue_context=linear)) != input_sha(
        _job(issue_context=bodied),
    )


BAD_CLAIM = IngestError(IngestErrorKind.BAD_CLAIM, 0, "d", at="$.claims")
MISSING_COVERAGE = IngestError(IngestErrorKind.MISSING_COVERAGE, 0, "d", at="$.focus")


def test_check_warning():
    import review.focus
    only_claim = (BAD_CLAIM,)
    assert check_warning(only_claim) == (
        f"{BAD_CLAIM.render()} — claims are published as not assessed"
    )
    mixed = (MISSING_COVERAGE, BAD_CLAIM)
    warning = check_warning(mixed)
    assert "published as not assessed; claims are published as not assessed" in warning
    only_focus = (MISSING_COVERAGE,)
    assert check_warning(only_focus) == review.focus.check_warning(only_focus)


def test_claims_of():
    present = ArtifactOutcome(SimpleNamespace(claims=(_OK,)))
    assert claims_of(_job(), present) == ()
    assert claims_of(_job(body="B"), ArtifactOutcome(
        SimpleNamespace(claims=(_OK,)), errors=(BAD_CLAIM,),
    )) is None
    entries = claims_of(_job(body="B"), ArtifactOutcome(
        SimpleNamespace(claims=(_OK,)), errors=(MISSING_COVERAGE,),
    ))
    assert entries == (claim(),)
    two = claims_of(_job(body="B"), ArtifactOutcome(
        SimpleNamespace(claims=(_OK, _OK)),
    ))
    assert [c.id for c in two] == ["C1", "C2"]
