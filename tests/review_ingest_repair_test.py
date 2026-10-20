"""One repair turn: the agent is shown exactly what was rejected, once."""

import sys
from pathlib import Path

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from agent.diagnosis import Diagnosis, DiagnosisKind
from review.ingest import REPAIR_MAX_TURNS, IngestErrorKind, ingest_with_repair

BAD = "## Nit\n- **[N1]** `a` — x\n- **[N1]** `b` — y\n"
GOOD = "## Nit\n- **[N1]** `a` — x\n- **[N2]** `b` — y\n"
BAD_SYNTH = (
    "## Summary\nLooks good.\n\n"
    "## Nit\n- **[N1]** `a` — x\n- **[N1]** `b` — y\n\n"
    "## Verdict\nApprove.\n"
)
GOOD_SYNTH = (
    "## Summary\nLooks good.\n\n"
    "## Nit\n- **[N1]** `a` — x\n- **[N2]** `b` — y\n\n"
    "## Verdict\nApprove.\n"
)


class FakeAgent:
    """Rewrites the file to `fixed` when invoked, recording what it was told."""

    def __init__(self, path: Path, fixed: str, rc: int = 0):
        self.path, self.fixed, self.calls, self.rc = path, fixed, [], rc

    def __call__(self, text: str, turns: int) -> int:
        self.calls.append((text, turns))
        self.path.write_text(self.fixed)
        return self.rc


def test_clean_output_spends_no_repair_turn(tmp_path):
    path = tmp_path / "review.md"
    path.write_text(GOOD)
    agent = FakeAgent(path, GOOD)
    outcome = ingest_with_repair(str(path), "PROMPT", agent)
    assert outcome.ok and not outcome.repaired and agent.calls == []


def test_a_rejection_gets_one_turn_with_the_errors_quoted(tmp_path):
    path = tmp_path / "review.md"
    path.write_text(BAD)
    agent = FakeAgent(path, GOOD)
    outcome = ingest_with_repair(str(path), "PROMPT", agent)
    assert outcome.ok and outcome.repaired
    (text, turns), = agent.calls
    assert text.startswith("PROMPT") and "duplicate_id" in text and "N1" in text
    assert turns == REPAIR_MAX_TURNS


def test_a_second_rejection_is_a_failure_not_another_turn(tmp_path):
    path = tmp_path / "review.md"
    path.write_text(BAD)
    agent = FakeAgent(path, BAD)
    outcome = ingest_with_repair(str(path), "PROMPT", agent)
    assert not outcome.ok and len(agent.calls) == 1
    assert outcome.errors[0].kind is IngestErrorKind.DUPLICATE_ID
    assert outcome.diagnosis is None


def test_a_repair_that_exits_nonzero_carries_that_failure(tmp_path):
    path = tmp_path / "review.md"
    path.write_text(BAD)
    agent = FakeAgent(path, BAD, rc=1)
    outcome = ingest_with_repair(str(path), "PROMPT", agent)
    assert not outcome.ok and outcome.repaired
    assert outcome.errors == ()
    assert outcome.diagnosis.kind is DiagnosisKind.AGENT_ERROR
    assert "1" in outcome.diagnosis.detail


def test_a_repair_that_diagnoses_a_cause_carries_that_kind(tmp_path):
    path = tmp_path / "review.md"
    path.write_text(BAD)
    agent = FakeAgent(path, BAD, rc=1)
    quota = Diagnosis(DiagnosisKind.QUOTA_EXHAUSTED)
    outcome = ingest_with_repair(
        str(path), "PROMPT", agent, diagnose=lambda: quota,
    )
    assert not outcome.ok and outcome.diagnosis is quota


def test_a_group_whose_repair_fails_is_a_group_failure(tmp_path, monkeypatch):
    """Drive `_review_group` the way `review_pipeline_retry_test` stubs invoke."""
    import json

    import review.phases
    from gh.types import PRContext, PRMetadata
    from review.types import Group, ReviewJob

    def fake_invoke(inv, throttle=None):
        Path(inv.output_path).write_text(BAD)
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
            GOOD if "Your review was rejected" in inv.prompt else BAD
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
    assert "**[N2]**" in Path(group_output).read_text()
    assert any(
        p.startswith("PROMPT") and "Your review was rejected" in p for p in prompts
    )


def test_a_group_whose_repair_hits_quota_is_not_malformed(tmp_path, monkeypatch):
    import json

    import review.phases
    from gh.types import PRContext, PRMetadata
    from review.types import Group, ReviewJob

    def fake_invoke(inv, throttle=None):
        if "Your review was rejected" in inv.prompt:
            Path(inv.session_log).write_text(json.dumps({
                "type": "system", "subtype": "api_retry", "error_status": 429,
            }) + "\n")
            Path(inv.output_path).write_text(BAD)
            return 1
        Path(inv.output_path).write_text(BAD)
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
        if "Your review was rejected" in inv.prompt:
            Path(inv.output_path).write_text(GOOD)
        elif inv.prompt == "SMALL":
            Path(inv.output_path).write_text(BAD)
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
        p.startswith("SMALL") and "Your review was rejected" in p for p in prompts
    )
    assert not any(
        p.startswith("BIG-AND-LONGER") and "Your review was rejected" in p
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
    from review.ingest import ingest_review
    from review.steps import Merged
    from review.types import ReviewJob
    from review.verdict import FALLBACK_SUMMARY
    from review.verify import PostProcessed

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
        Path(job.review_file).write_text(
            GOOD_SYNTH if "Your review was rejected" in inv.prompt else BAD_SYNTH
        )
        return 0

    monkeypatch.setattr(review.phases, "run_agent", mock_invoke)
    monkeypatch.setattr(review.steps, "build_prompt", lambda *a, **k: "PROMPT")
    monkeypatch.setattr(
        review.outcome, "post_process_record",
        lambda record, *a, **k: PostProcessed(record, None),
    )
    merged = Merged(ingest_review(GOOD, strict=False), GOOD)
    result = review.steps._phase_synthesis(job, "", 1, merged)
    assert result.diagnosis is None
    written = Path(job.review_file).read_text()
    assert FALLBACK_SUMMARY not in written
    assert "**[N2]**" in written
    assert any(
        p.startswith("PROMPT") and "Your review was rejected" in p for p in prompts
    )


def test_a_banner_group_file_becomes_a_malformed_gap(tmp_path):
    from review.render import BANNER
    from review.steps import _phase_merge

    path = tmp_path / "group-1.md"
    path.write_text(f"{BANNER}\n## Nit\n- **[N1]** `a` — x\n")
    merged = _phase_merge([str(path)], [], group_names=("g1",))
    assert any(
        "output rejected by ingest" in section.body for section in merged.record.extra
    )


def test_a_rejection_past_the_group_names_falls_back_to_a_numbered_label(tmp_path):
    from review.render import BANNER
    from review.steps import _phase_merge

    path = tmp_path / "group-1.md"
    path.write_text(f"{BANNER}\n## Nit\n- **[N1]** `a` — x\n")
    merged = _phase_merge([str(path)], [], group_names=())
    assert any("group-1" in section.body for section in merged.record.extra)


def test_an_already_failed_group_is_not_a_second_gap_row(tmp_path):
    from agent.diagnosis import Diagnosis, DiagnosisKind
    from review.render import BANNER
    from review.retry import GroupFailure
    from review.steps import _phase_merge

    path = tmp_path / "group-1.md"
    path.write_text(f"{BANNER}\n## Nit\n- **[N1]** `a` — x\n")
    existing = GroupFailure("g1", Diagnosis(DiagnosisKind.OUTPUT_MISSING))
    merged = _phase_merge([str(path)], [existing], group_names=("g1",))
    gaps = "\n".join(section.body for section in merged.record.extra)
    assert gaps.count("g1:") == 1
    assert "output missing" in gaps
    assert "output rejected by ingest" not in gaps
