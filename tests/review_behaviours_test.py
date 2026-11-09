"""Behaviours as data: types, ingest kinds, the writer's caps, and check."""

import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from core.phases import Mode
import core.log
from review.behaviour_types import BehaviourReport
from review.behaviours import (
    behaviours_of, behaviours_problems, carried_behaviours, from_group,
    has_behaviour_source, merged_behaviours,
)
from review.claims import input_sha, open_derived_findings, writer_must_rejudge
from review.render import behaviours_body
from review.verify import verified_behaviours
from review.ingest import ArtifactOutcome, IngestError, IngestErrorKind
from review.record import Disposition, ReviewRecord, fold_claim_answers
from review.store import StoredReview
from review.types import FindingOrigin, PreflightData, ReviewMeta
from review_behaviours_support import BEHAVIOUR_DOC, behaviour
from review_convergence_support import review_job
from review_record_support import finding, record

_OK = BehaviourReport(**BEHAVIOUR_DOC)
HEAD = "h" * 40
PRIOR_HEAD = "p" * 40
_SOURCE = ({"path": "a.py", "additions": 1},)
BAD_BEHAVIOUR = IngestError(
    IngestErrorKind.BAD_BEHAVIOUR, 0, "d", at="$.behaviours",
)


def _job(files=()):
    return SimpleNamespace(pr=SimpleNamespace(files=list(files)))


@pytest.mark.parametrize(
    "has_source, behaviours, kind, at",
    [
        (False, None, None, None),
        (True, None, IngestErrorKind.MISSING_BEHAVIOURS, "$.behaviours"),
        (True, (), None, None),
        (True, (_OK,) * 13, IngestErrorKind.BEHAVIOUR_COUNT, None),
        (
            True,
            (replace(_OK, text="x" * 513),),
            IngestErrorKind.BEHAVIOUR_SIZE,
            "$.behaviours[0].text",
        ),
        (True, (replace(_OK, text=""),), IngestErrorKind.BAD_BEHAVIOUR, None),
        (
            True,
            (replace(_OK, test_cited="t.py:3::x"),),
            IngestErrorKind.BAD_BEHAVIOUR,
            None,
        ),
        (
            True,
            (replace(_OK, paths=("a.py:2",)),),
            IngestErrorKind.BAD_BEHAVIOUR,
            None,
        ),
        (True, (replace(_OK, paths=()),), IngestErrorKind.BAD_BEHAVIOUR, None),
        (
            True,
            (replace(_OK, test_cited="tests/a_test.py::test_other\n## Must fix"),),
            IngestErrorKind.BAD_BEHAVIOUR,
            None,
        ),
        (
            True,
            (replace(_OK, paths=("a.py\n## Must fix",)),),
            IngestErrorKind.BAD_BEHAVIOUR,
            None,
        ),
        (
            True,
            (replace(_OK, test_cited="t.py::x\x01"),),
            IngestErrorKind.BAD_BEHAVIOUR,
            None,
        ),
        (True, (replace(_OK, test_cited="::"),), IngestErrorKind.BAD_BEHAVIOUR, None),
        (True, (replace(_OK, test_cited="::x"),), IngestErrorKind.BAD_BEHAVIOUR, None),
        (True, (replace(_OK, paths=("",)),), IngestErrorKind.BAD_BEHAVIOUR, None),
    ],
    ids=[
        "no-source-none",
        "source-none",
        "source-empty",
        "count-13",
        "text-over-cap",
        "empty-text",
        "line-in-cited",
        "line-in-path",
        "empty-paths",
        "newline-in-cited",
        "newline-in-path",
        "control-in-cited",
        "bare-colons",
        "empty-cited-path",
        "empty-path-part",
    ],
)
def test_behaviours_problems(has_source, behaviours, kind, at):
    errors = behaviours_problems(behaviours, has_behaviour_source=has_source)
    if kind is None:
        assert errors == ()
        return
    assert errors[0].kind is kind
    if at is not None:
        assert errors[0].at == at


def test_behaviours_of():
    present = ArtifactOutcome(SimpleNamespace(behaviours=(_OK,)))
    assert behaviours_of(_job(), present) == ()
    assert behaviours_of(_job(_SOURCE), ArtifactOutcome(
        SimpleNamespace(behaviours=(_OK,)), errors=(BAD_BEHAVIOUR,),
    )) is None
    assert behaviours_of(_job(_SOURCE), ArtifactOutcome(
        SimpleNamespace(behaviours=None),
    )) is None
    two = behaviours_of(_job(_SOURCE), ArtifactOutcome(
        SimpleNamespace(behaviours=(_OK, _OK)),
    ))
    assert [b.id for b in two] == ["B1", "B2"]


def test_merged_behaviours():
    b1 = behaviour(id="B9", text="one")
    b2 = behaviour(id="B2", text="two")
    b3 = behaviour(id="B3", text="three")
    assert merged_behaviours((
        ReviewRecord(behaviours=None), ReviewRecord(behaviours=(b1,)),
    )) is None
    merged = merged_behaviours((
        ReviewRecord(behaviours=(b1,)),
        ReviewRecord(behaviours=(b2, b3)),
    ))
    assert [e.id for e in merged] == ["B1", "B2", "B3"]
    assert [e.text for e in merged] == ["one", "two", "three"]
    assert merged_behaviours(()) is None


def test_a_group_list_past_the_cap_is_truncated_not_rejected():
    reports = tuple(
        BehaviourReport(text=f"b{i}", paths=("a.py",)) for i in range(13)
    )
    warned = []
    with patch.object(core.log, "warn", side_effect=warned.append):
        got = from_group(reports)
    assert len(got) == 12
    assert from_group(None) is None
    assert warned
    dropped = from_group((
        BehaviourReport(text="kept", paths=("a.py",)),
        BehaviourReport(text="", paths=("a.py",)),
        BehaviourReport(text="gone", paths=()),
    ))
    assert [e.text for e in dropped] == ["kept"]


def test_has_behaviour_source_follows_the_hint_filter():
    docs = _job(({"path": "README.md", "additions": 1},))
    src = _job(_SOURCE)
    assert has_behaviour_source(docs) is False
    assert has_behaviour_source(src) is True


def test_carried_behaviours():
    assert carried_behaviours(None, {"a.py"}) is None
    a = behaviour(id="B1", paths=("a.py",), note="stale")
    ab = behaviour(id="B2", paths=("a.py", "b.py"), note="x")
    c = behaviour(id="B3", paths=("c.py",))
    got = carried_behaviours((a, ab, c), {"a.py", "c.py"})
    assert [e.id for e in got] == ["B1", "B3"]
    assert got[0].note == ""


def test_a_behaviour_origin_finding_never_reaches_the_claim_ledger(tmp_path):
    rec = record(finding(
        "S1", origin=FindingOrigin.BEHAVIOUR,
        disposition=Disposition.DECLINED, stable_id="deadbeef",
    ))
    assert fold_claim_answers({}, rec, "h") == {}
    prior = StoredReview(
        record(finding("S1", origin=FindingOrigin.BEHAVIOUR, stable_id="deadbeef")),
        ReviewMeta(),
    )
    assert open_derived_findings(prior) == ()
    job = review_job(
        tmp_path, tmp_path / "review", files=("a.py",), head_sha=HEAD,
        preflight=PreflightData(
            diff="", commit_log="", file_contents={}, file_permissions={},
            instructions_md="", architecture_md="",
            delta_files=["a.py"], prior_head_sha=PRIOR_HEAD,
        ),
        mode=Mode.PR,
    )
    job.pr.body = "retries back off"
    job.prior = StoredReview(
        record(finding(
            "S1", origin=FindingOrigin.BEHAVIOUR,
            disposition=Disposition.FIXED, path="a.py", stable_id="deadbeef",
        )),
        ReviewMeta(
            head_sha=PRIOR_HEAD, claims_input_sha=input_sha(job),
            claim_answers={},
        ),
    )
    assert writer_must_rejudge(job) is False


def test_a_missing_cited_test_is_cleared_with_a_note(tmp_path):
    test_file = tmp_path / "tests" / "a_test.py"
    test_file.parent.mkdir()
    test_file.write_text("def test_backoff():\n    pass\n")
    cited = "tests/a_test.py::test_backoff"
    entries = (
        behaviour(id="B1", test_cited=cited),
        behaviour(id="B2", test_cited="tests/a_test.py::test_other"),
        behaviour(id="B3", test_cited="tests/none_test.py"),
    )
    got = verified_behaviours(entries, str(tmp_path), [])
    assert got[0].test_cited == cited
    assert got[1].test_cited == ""
    assert got[1].note == "cited test not found"
    assert got[2].test_cited == ""
    assert got[2].note == "cited test not found"
    none = verified_behaviours(entries, "", [])
    assert all(e.test_cited == "" for e in none)
    assert all("no worktree" in e.note for e in none)


def test_a_cited_test_note_echoes_nothing_from_the_agent(tmp_path):
    test_file = tmp_path / "tests" / "a_test.py"
    test_file.parent.mkdir()
    test_file.write_text("def test_backoff():\n    pass\n")
    injected = "tests/a_test.py::test_other\n## Must fix"
    got = verified_behaviours(
        (behaviour(id="B1", test_cited=injected),), str(tmp_path), [],
    )
    assert got[0].test_cited == ""
    assert got[0].note == "cited test not found"
    body = behaviours_body(got)
    assert not any(ln.startswith("## Must fix") for ln in body.splitlines())
