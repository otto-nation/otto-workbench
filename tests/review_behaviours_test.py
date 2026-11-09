"""Behaviours as data: types, ingest kinds, the writer's caps, and check."""

import json
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import cli.review_orchestrate  # noqa: F401
from agent.diagnosis import Diagnosis, DiagnosisKind
from core.phases import Mode
import core.log
from review.behaviour_types import BehaviourReport
from review.behaviours import (
    behaviours_of, behaviours_problems, carried_behaviours, from_group,
    has_behaviour_source, merged_behaviours,
)
from review.budget import MAX_BEHAVIOURS
from review.claims import (
    check_warning, input_sha, open_derived_findings, writer_must_rejudge,
)
from review.merge import merge_records
from review.render import behaviours_body
from review.retry import GroupFailure
from review.steps import Merged, _carry_forward_prior_findings, _phase_merge
from review.verify import PostProcessed, verified_behaviours
from review.ingest import ArtifactOutcome, IngestError, IngestErrorKind
from review.record import Disposition, ReviewRecord, fold_claim_answers, load_record
from review.store import StoredReview
from review.types import FindingOrigin, Group, PreflightData, ReviewMeta
from review_behaviours_support import BEHAVIOUR_DOC, behaviour
from review_claims_support import claim
from review_convergence_support import review_job
from review_focus_support import DERIVED_FOCUS
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


_WRITER = {"summary": "s", "verdict": "Approve", "focus": DERIVED_FOCUS}
BAD_CLAIM = IngestError(IngestErrorKind.BAD_CLAIM, 0, "d", at="$.claims")


def _writer_job(ro, tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir(exist_ok=True)
    (wt / "a.py").write_text("delay *= 2\n")
    return ro.ReviewJob(
        repo="org/repo", pr_number="42",
        pr=ro.PRMetadata(
            title="t", body="", head="feat", base="main", head_sha="abc123",
            additions=1, deletions=0, changed_files=1,
            files=[{"path": "a.py", "additions": 1, "deletions": 0}],
        ),
        ctx=ro.PRContext(), wt_path=str(wt),
        review_file=str(tmp_path / "review.md"),
        session_log=str(tmp_path / "session.jsonl"),
        mode=ro.Mode("pr"), focus=(),
    )


def _patch_writer(monkeypatch, run_agent):
    import agent.retry
    import review.outcome
    import review.phases
    import review.steps
    monkeypatch.setattr(review.steps, "build_prompt", lambda *a, **kw: "mock prompt")
    monkeypatch.setattr(
        review.outcome, "post_process_record",
        lambda rec, *a, **kw: PostProcessed(rec, None),
    )
    monkeypatch.setattr(review.phases, "run_agent", run_agent)
    monkeypatch.setattr(agent.retry, "try_recover_output", lambda *a, **k: False)


def test_a_writer_missing_behaviours_gets_a_repair_turn(ro, tmp_path, monkeypatch):
    job = _writer_job(ro, tmp_path)
    prompts = []

    def mock_invoke(inv, **kwargs):
        prompts.append(inv.prompt)
        doc = {**_WRITER, "behaviours": [BEHAVIOUR_DOC]}
        if "Your output was rejected" not in inv.prompt:
            doc = _WRITER
        Path(inv.output_path).write_text(json.dumps(doc))
        Path(inv.session_log).write_text("")
        return 0

    _patch_writer(monkeypatch, mock_invoke)
    result = ro._phase_synthesis(job, "", 3, Merged(record()))
    assert result.diagnosis is None
    assert len(prompts) == 2
    assert "missing_behaviours" in prompts[1]
    assert "was read" in prompts[1]
    assert [e.id for e in load_record(tmp_path).behaviours] == ["B1"]


def test_a_second_behaviours_miss_publishes_not_assessed(
    ro, tmp_path, monkeypatch, capsys,
):
    job = _writer_job(ro, tmp_path)

    def mock_invoke(inv, **kwargs):
        Path(inv.output_path).write_text(json.dumps(_WRITER))
        Path(inv.session_log).write_text("")
        return 0

    _patch_writer(monkeypatch, mock_invoke)
    result = ro._phase_synthesis(job, "", 3, Merged(record()))
    assert result.diagnosis is None
    rec = load_record(tmp_path)
    assert rec.behaviours is None
    assert "*Not assessed this round.*" in Path(job.review_file).read_text()
    err = capsys.readouterr().err
    assert " — behaviours are published as not assessed" in err


def test_check_warning_names_behaviours():
    job = SimpleNamespace(
        pr=SimpleNamespace(body=""), issue_link="", issue_context="", prior=None,
    )
    only = (BAD_BEHAVIOUR,)
    assert check_warning(only, job) == (
        f"{BAD_BEHAVIOUR.render()} — behaviours are published as not assessed"
    )
    carryable = SimpleNamespace(
        pr=SimpleNamespace(body="x"), issue_link="", issue_context="", prior=None,
    )
    carryable.prior = SimpleNamespace(
        record=SimpleNamespace(claims=(claim(),)),
        meta=SimpleNamespace(claims_input_sha=input_sha(carryable)),
    )
    warning = check_warning((BAD_CLAIM, BAD_BEHAVIOUR), carryable)
    assert warning.endswith(
        "prior claims are carried; behaviours are published as not assessed"
    )


def test_the_mechanical_fallback_publishes_group_behaviours(
    ro, tmp_path, monkeypatch,
):
    job = _writer_job(ro, tmp_path)
    tests = Path(job.wt_path) / "tests"
    tests.mkdir()
    (tests / "a_test.py").write_text("def test_backoff():\n    pass\n")
    (Path(job.wt_path) / "b.py").write_text("y = 1\n")
    job.pr.files.append({"path": "b.py", "additions": 1, "deletions": 0})
    g1 = record(behaviours=(behaviour(id="B9", text="one", paths=("a.py",)),))
    g2 = record(behaviours=(behaviour(
        id="B2", text="two", test_cited="tests/a_test.py::test_backoff",
        paths=("b.py",),
    ),))
    merged = merge_records([g1, g2])

    def mock_invoke(inv, **kwargs):
        Path(inv.session_log).write_text("")
        return 1

    _patch_writer(monkeypatch, mock_invoke)
    ro._phase_synthesis(job, "", 2, Merged(merged))
    rec = load_record(tmp_path)
    assert [e.id for e in rec.behaviours] == ["B1", "B2"]
    assert rec.behaviours[1].test_cited == "tests/a_test.py::test_backoff"


def test_a_pre_upgrade_group_leaves_behaviours_unassessed_on_fallback(
    ro, tmp_path, monkeypatch,
):
    job = _writer_job(ro, tmp_path)

    def mock_invoke(inv, **kwargs):
        Path(inv.session_log).write_text("")
        return 1

    _patch_writer(monkeypatch, mock_invoke)
    ro._phase_synthesis(job, "", 1, Merged(record(behaviours=None)))
    rec = load_record(tmp_path)
    assert rec.behaviours is None
    assert "*Not assessed this round.*" in Path(job.review_file).read_text()


def test_a_group_gap_leaves_merged_behaviours_unassessed(tmp_path):
    good = tmp_path / "group-1.json"
    good.write_text(json.dumps({
        "findings": [{"id": "N1", "path": "a.py", "line": 1, "body": "x"}],
        "behaviours": [BEHAVIOUR_DOC],
    }))
    failed = GroupFailure("g2", Diagnosis(DiagnosisKind.OUTPUT_MISSING))
    merged = _phase_merge(
        [str(good), str(tmp_path / "group-2.json")],
        [failed], ("g1", "g2"), declinable=frozenset(),
    )
    assert merged.record.behaviours is None


def test_a_skipped_group_carries_its_prior_behaviours():
    prior = StoredReview(
        record(behaviours=(
            behaviour(id="B1", text="kept", paths=("a.py",)),
            behaviour(id="B2", text="rerun", paths=("b.py",)),
        )),
        ReviewMeta(),
    )
    groups = [Group("g1", ["a.py"], 1), Group("g2", ["b.py"], 1)]
    carried = _carry_forward_prior_findings(prior, groups, {1})
    assert [e.text for e in carried.record.behaviours] == ["kept"]
    fresh = record(behaviours=(behaviour(id="B9", text="fresh", paths=("b.py",)),))
    folded = Merged(fresh).carry(carried.record)
    assert [e.id for e in folded.record.behaviours] == ["B1", "B2"]
    assert [e.text for e in folded.record.behaviours] == ["fresh", "kept"]
    none_prior = StoredReview(ReviewRecord(behaviours=None), ReviewMeta())
    none_carried = _carry_forward_prior_findings(none_prior, groups, {1})
    assert none_carried.record.behaviours is None
    assert Merged(fresh).carry(none_carried.record).record.behaviours is None


def test_an_all_skipped_merge_carries_prior_behaviours():
    merged = _phase_merge([], [], (), declinable=frozenset())
    assert merged.record.behaviours == ()
    prior = record(behaviours=(behaviour(id="B9", text="kept", paths=("a.py",)),))
    folded = merged.carry(prior)
    assert [e.id for e in folded.record.behaviours] == ["B1"]
    assert [e.text for e in folded.record.behaviours] == ["kept"]


def test_an_over_cap_behaviour_list_degrades(ro, tmp_path, monkeypatch):
    job = _writer_job(ro, tmp_path)
    over = {**_WRITER, "behaviours": [BEHAVIOUR_DOC] * (MAX_BEHAVIOURS + 1)}

    def mock_invoke(inv, **kwargs):
        Path(inv.output_path).write_text(json.dumps(over))
        Path(inv.session_log).write_text("")
        return 0

    _patch_writer(monkeypatch, mock_invoke)
    result = ro._phase_synthesis(job, "", 3, Merged(record()))
    assert result.diagnosis is None
    assert load_record(tmp_path).behaviours is None
