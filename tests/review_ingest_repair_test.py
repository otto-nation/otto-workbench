"""One repair turn: the agent is shown exactly what was rejected, once."""

import json
import sys
from pathlib import Path

import pytest

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from agent.diagnosis import Diagnosis, DiagnosisKind
from review.artifacts import GroupArtifact
from review.ingest import (
    REPAIR_MAX_TURNS, IngestError, IngestErrorKind, IngestRejected,
    ingest_artifact, load_artifact,
)


class FakeAgent:
    """Rewrites the file to `fixed` when invoked, recording what it was told."""

    def __init__(self, path: Path, fixed: str, rc: int = 0):
        self.path, self.fixed, self.calls, self.rc = path, fixed, [], rc

    def __call__(self, text: str, turns: int) -> int:
        self.calls.append((text, turns))
        self.path.write_text(self.fixed)
        return self.rc


def test_a_group_whose_repair_fails_is_a_group_failure(tmp_path, monkeypatch):
    """Drive `_review_group` the way `review_pipeline_retry_test` stubs invoke."""
    import json

    import review.phases
    from gh.types import PRContext, PRMetadata
    from review.types import Group, ReviewJob

    def fake_invoke(inv, throttle=None):
        Path(inv.output_path).write_text(BAD_JSON)
        Path(inv.session_log).write_text(json.dumps({
            "type": "result", "subtype": "success", "num_turns": 3,
        }) + "\n")
        return 0

    monkeypatch.setattr(review.phases, "run_agent", fake_invoke)
    monkeypatch.setattr(review.phases, "build_prompt", lambda *a, **k: "PROMPT")
    job = ReviewJob(
        repo="org/repo", pr_number="1",
        pr=PRMetadata(
            title="t", body="", head="b", base="main", head_sha="abc",
            additions=1, deletions=1, changed_files=1, files=[],
        ),
        ctx=PRContext(), wt_path=str(tmp_path),
        review_file=str(tmp_path / "review.md"),
        session_log=str(tmp_path / "session.jsonl"),
    )
    _, _, failed = review.phases._review_group(
        1, Group(name="g1", files=["a.py"], lines=10),
        job, 1, "holistic",
    )
    assert failed is not None
    assert failed.diagnosis.kind is DiagnosisKind.MALFORMED_OUTPUT


def test_a_group_whose_repair_succeeds_is_done(tmp_path, monkeypatch):
    """First output is rejected; the repair turn writes valid output."""
    import json

    import review.phases
    from gh.types import PRContext, PRMetadata
    from review.types import Group, ReviewJob

    prompts = []

    def fake_invoke(inv, throttle=None):
        prompts.append(inv.prompt)
        Path(inv.session_log).write_text(json.dumps({
            "type": "result", "subtype": "success", "num_turns": 3,
        }) + "\n")
        Path(inv.output_path).write_text(
            GOOD_JSON if "Your output was rejected" in inv.prompt else BAD_JSON
        )
        return 0

    monkeypatch.setattr(review.phases, "run_agent", fake_invoke)
    monkeypatch.setattr(review.phases, "build_prompt", lambda *a, **k: "PROMPT")
    job = ReviewJob(
        repo="org/repo", pr_number="1",
        pr=PRMetadata(
            title="t", body="", head="b", base="main", head_sha="abc",
            additions=1, deletions=1, changed_files=1, files=[],
        ),
        ctx=PRContext(), wt_path=str(tmp_path),
        review_file=str(tmp_path / "review.md"),
        session_log=str(tmp_path / "session.jsonl"),
    )
    _, group_output, failed = review.phases._review_group(
        1, Group(name="g1", files=["a.py"], lines=10),
        job, 1, "holistic",
    )
    assert failed is None
    assert group_output.endswith("group-1.json")
    assert json.loads(Path(group_output).read_text())["findings"][0]["id"] == "N1"
    assert any(
        p.startswith("PROMPT") and "Your output was rejected" in p for p in prompts
    )


def test_a_group_whose_repair_hits_quota_is_not_malformed(tmp_path, monkeypatch):
    import json

    import review.phases
    from gh.types import PRContext, PRMetadata
    from review.types import Group, ReviewJob

    def fake_invoke(inv, throttle=None):
        if "Your output was rejected" in inv.prompt:
            Path(inv.session_log).write_text(json.dumps({
                "type": "system", "subtype": "api_retry", "error_status": 429,
            }) + "\n")
            Path(inv.output_path).write_text(BAD_JSON)
            return 1
        Path(inv.output_path).write_text(BAD_JSON)
        Path(inv.session_log).write_text(json.dumps({
            "type": "result", "subtype": "success", "num_turns": 3,
        }) + "\n")
        return 0

    monkeypatch.setattr(review.phases, "run_agent", fake_invoke)
    monkeypatch.setattr(review.phases, "build_prompt", lambda *a, **k: "PROMPT")
    job = ReviewJob(
        repo="org/repo", pr_number="1",
        pr=PRMetadata(
            title="t", body="", head="b", base="main", head_sha="abc",
            additions=1, deletions=1, changed_files=1, files=[],
        ),
        ctx=PRContext(), wt_path=str(tmp_path),
        review_file=str(tmp_path / "review.md"),
        session_log=str(tmp_path / "session.jsonl"),
    )
    _, _, failed = review.phases._review_group(
        1, Group(name="g1", files=["a.py"], lines=10),
        job, 1, "holistic",
    )
    assert failed is not None
    assert failed.diagnosis.kind is DiagnosisKind.QUOTA_EXHAUSTED


def test_repair_starts_from_the_overflow_rebuilt_prompt(tmp_path, monkeypatch):
    """F8: the repair turn re-sends the rebuilt prompt, not the original."""
    import agent.retry
    import review.phases
    import review.pipeline
    from agent.diagnosis import Diagnosis, DiagnosisKind

    prompts = []

    def fake_build(*_args, **kwargs):
        if kwargs.get("ladder_bytes") is not None:
            return "SMALL"
        return "BIG-AND-LONGER"

    def fake_invoke(inv, throttle=None):
        prompts.append(inv.prompt)
        Path(inv.session_log).write_text("{}\n")
        if "Your output was rejected" in inv.prompt:
            Path(inv.output_path).write_text(GOOD_REVIEW_JSON)
        elif inv.prompt == "SMALL":
            Path(inv.output_path).write_text(BAD_REVIEW_JSON)
        return 0

    monkeypatch.setattr(review.pipeline, "build_prompt", fake_build)
    monkeypatch.setattr(review.phases, "run_agent", fake_invoke)
    monkeypatch.setattr(review.pipeline, "_post_process_review", lambda *a, **k: None)
    monkeypatch.setattr(review.pipeline, "_should_disprove", lambda *a, **k: False)
    monkeypatch.setattr(
        agent.retry, "diagnose_missing_output",
        lambda *a, **k: Diagnosis(
            DiagnosisKind.AGENT_ERROR,
            detail="prompt is too long: 200 tokens > 100 maximum",
        ),
    )
    job = review.pipeline.ReviewJob(
        repo="org/repo", pr_number="1",
        pr=review.pipeline.PRMetadata(
            title="t", body="", head="b", base="main", head_sha="abc",
            additions=1, deletions=1, changed_files=1, files=[],
        ),
        ctx=review.pipeline.PRContext(), wt_path=str(tmp_path),
        review_file=str(tmp_path / "review.md"),
        session_log=str(tmp_path / "session.jsonl"),
    )
    review.pipeline.run_single_agent(job, disprove=False)
    assert any(
        p.startswith("SMALL") and "Your output was rejected" in p for p in prompts
    )
    assert not any(
        p.startswith("BIG-AND-LONGER") and "Your output was rejected" in p
        for p in prompts
    )


def test_a_rejected_synthesis_that_repairs_publishes_the_agent_record(
    tmp_path, monkeypatch,
):
    """First output is rejected; the repair turn writes a publishable record."""
    import review.outcome
    import review.phases
    import review.steps
    from gh.types import PRContext, PRMetadata
    from review.steps import Merged
    from review.types import ReviewJob
    from review.verdict import FALLBACK_SUMMARY
    from review.verify import PostProcessed
    from review_record_support import finding, record

    prompts = []
    job = ReviewJob(
        repo="org/repo", pr_number="1",
        pr=PRMetadata(
            title="t", body="", head="b", base="main", head_sha="abc",
            additions=1, deletions=1, changed_files=1, files=[],
        ),
        ctx=PRContext(), wt_path=str(tmp_path),
        review_file=str(tmp_path / "review.md"),
        session_log=str(tmp_path / "session.jsonl"),
    )

    def mock_invoke(inv, throttle=None):
        prompts.append(inv.prompt)
        Path(inv.session_log).write_text("")
        Path(inv.output_path).write_text(
            GOOD_REVIEW_JSON if "Your output was rejected" in inv.prompt else BAD_REVIEW_JSON
        )
        return 0

    monkeypatch.setattr(review.phases, "run_agent", mock_invoke)
    monkeypatch.setattr(review.steps, "build_prompt", lambda *a, **k: "PROMPT")
    monkeypatch.setattr(
        review.outcome, "post_process_record",
        lambda record, *a, **k: PostProcessed(record, None),
    )
    merged = Merged(record(
        finding("N1", "x", path="a.py", line=1),
        finding("N2", "y", path="b.py", line=1),
        summary="Looks good.", verdict="Approve.",
    ))
    result = review.steps._phase_synthesis(job, "", 1, merged)
    assert result.diagnosis is None
    written = Path(job.review_file).read_text()
    assert FALLBACK_SUMMARY not in written
    assert "**[N2]**" in written
    assert any(
        p.startswith("PROMPT") and "Your output was rejected" in p for p in prompts
    )


def test_a_rejected_group_file_becomes_a_malformed_gap(tmp_path):
    from review.steps import _phase_merge

    path = tmp_path / "group-1.json"
    path.write_text('{"findings": [{"id": "M1"}]}')
    merged = _phase_merge([str(path)], [], group_names=("g1",), declinable=frozenset())
    assert any(
        "output rejected by ingest" in section.body for section in merged.record.extra
    )


def test_a_rejection_past_the_group_names_falls_back_to_a_numbered_label(tmp_path):
    from review.steps import _phase_merge

    path = tmp_path / "group-1.json"
    path.write_text('{"findings": [{"id": "M1"}]}')
    merged = _phase_merge([str(path)], [], group_names=(), declinable=frozenset())
    assert any("group-1" in section.body for section in merged.record.extra)


def test_an_already_failed_group_is_not_a_second_gap_row(tmp_path):
    from agent.diagnosis import Diagnosis, DiagnosisKind
    from review.retry import GroupFailure
    from review.steps import _phase_merge

    path = tmp_path / "group-1.json"
    path.write_text('{"findings": [{"id": "M1"}]}')
    existing = GroupFailure("g1", Diagnosis(DiagnosisKind.OUTPUT_MISSING))
    merged = _phase_merge([str(path)], [existing], group_names=("g1",), declinable=frozenset())
    gaps = "\n".join(section.body for section in merged.record.extra)
    assert gaps.count("g1:") == 1
    assert "output missing" in gaps
    assert "output rejected by ingest" not in gaps


def test_an_error_names_its_json_path_or_its_line_and_column():
    assert IngestError(IngestErrorKind.SCHEMA_VIOLATION, 0, "is required", at="$.summary").render() == \
        "$.summary: schema_violation — is required"
    assert IngestError(IngestErrorKind.INVALID_JSON, 3, "Expecting ','", column=7).render() == \
        "line 3, column 7: invalid_json — Expecting ','"
    assert IngestError(IngestErrorKind.DUPLICATE_ID, 4, "x").render() == "line 4: duplicate_id — x"


GOOD_JSON = json.dumps({"findings": [{"id": "N1", "path": "a.py", "line": 1, "body": "x"}]})
BAD_JSON = json.dumps({"findings": [{"id": "N1", "path": "a.py:1", "body": "x"}]})


GOOD_REVIEW_JSON = json.dumps({
    "summary": "Looks good.",
    "findings": [
        {"id": "N1", "path": "a.py", "line": 1, "body": "x"},
        {"id": "N2", "path": "b.py", "line": 1, "body": "y"},
    ],
    "verdict": "Approve.",
})
BAD_REVIEW_JSON = json.dumps({
    "summary": "Looks good.",
    "findings": [
        {"id": "N1", "path": "a.py", "line": 1, "body": "x"},
        {"id": "N1", "path": "b.py", "line": 1, "body": "y"},
    ],
    "verdict": "Approve.",
})


def _rejected(tmp_path, text: str):
    path = tmp_path / "group-1.json"
    path.write_text(text)
    with pytest.raises(IngestRejected) as exc:
        load_artifact(str(path), GroupArtifact)
    return exc.value.errors


def test_text_that_is_not_json_names_the_decoder_s_line_and_column(tmp_path):
    (e,) = _rejected(tmp_path, '{\n  "findings": [\n')
    assert (e.kind, e.line, e.column) == (IngestErrorKind.INVALID_JSON, 3, 1)


def test_a_fenced_document_is_told_to_drop_the_fence(tmp_path):
    (e,) = _rejected(tmp_path, "```json\n{}\n```")
    assert e.kind is IngestErrorKind.INVALID_JSON and "code fence" in e.detail


def test_a_schema_violation_names_its_json_path(tmp_path):
    (e,) = _rejected(tmp_path, json.dumps({"findings": [{"id": "N1", "body": 3}]}))
    assert (e.kind, e.at) == (IngestErrorKind.SCHEMA_VIOLATION, "$.findings[0].body")


def test_a_semantic_problem_is_rejected_after_the_schema_passes(tmp_path):
    (e,) = _rejected(tmp_path, BAD_JSON)
    assert (e.kind, e.at) == (IngestErrorKind.LINE_IN_PATH, "$.findings[0].path")


def test_a_valid_document_loads_as_its_artifact(tmp_path):
    path = tmp_path / "group-1.json"
    path.write_text(GOOD_JSON)
    assert load_artifact(str(path), GroupArtifact).findings[0].path == "a.py"


def test_a_rejected_artifact_gets_one_turn_with_the_errors_quoted(tmp_path):
    path = tmp_path / "group-1.json"
    path.write_text(BAD_JSON)
    agent = FakeAgent(path, GOOD_JSON)
    outcome = ingest_artifact(str(path), "PROMPT", agent, GroupArtifact)
    assert outcome.ok and outcome.repaired
    (text, turns), = agent.calls
    assert "line_in_path" in text and "$.findings[0].path" in text and turns == REPAIR_MAX_TURNS


def test_a_second_artifact_rejection_is_a_failure_not_another_turn(tmp_path):
    path = tmp_path / "group-1.json"
    path.write_text(BAD_JSON)
    agent = FakeAgent(path, BAD_JSON)
    outcome = ingest_artifact(str(path), "PROMPT", agent, GroupArtifact)
    assert not outcome.ok and len(agent.calls) == 1
    assert outcome.errors[0].kind is IngestErrorKind.LINE_IN_PATH
