"""One repair turn: the agent is shown exactly what was rejected, once."""

import sys
from pathlib import Path

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from review.ingest import REPAIR_MAX_TURNS, IngestErrorKind, ingest_with_repair

BAD = "## Nit\n- **[N1]** `a` — x\n- **[N1]** `b` — y\n"
GOOD = "## Nit\n- **[N1]** `a` — x\n- **[N2]** `b` — y\n"


class FakeAgent:
    """Rewrites the file to `fixed` when invoked, recording what it was told."""

    def __init__(self, path: Path, fixed: str):
        self.path, self.fixed, self.calls = path, fixed, []

    def __call__(self, text: str, turns: int) -> int:
        self.calls.append((text, turns))
        self.path.write_text(self.fixed)
        return 0


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


def test_a_group_whose_repair_fails_is_a_group_failure(tmp_path, monkeypatch):
    """Drive `_review_group` the way `review_pipeline_retry_test` stubs invoke."""
    import json

    import review.phases as phases
    from agent.diagnosis import DiagnosisKind
    from gh.types import PRContext, PRMetadata
    from review.types import Group, ReviewJob

    def fake_invoke(inv, throttle=None):
        Path(inv.output_path).write_text(BAD)
        Path(inv.session_log).write_text(json.dumps({
            "type": "result", "subtype": "success", "num_turns": 3,
        }) + "\n")
        return 0

    monkeypatch.setattr(phases, "run_agent", fake_invoke)
    monkeypatch.setattr(phases, "build_prompt", lambda *a, **k: "PROMPT")
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
    _, _, failed = phases._review_group(
        1, Group(name="g1", files=["a.py"], lines=10),
        job, 1, "holistic",
    )
    assert failed is not None
    assert failed.diagnosis.kind is DiagnosisKind.MALFORMED_OUTPUT


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


def test_a_banner_group_file_becomes_a_malformed_gap(tmp_path):
    from agent.diagnosis import DiagnosisKind
    from review.render import BANNER
    from review.steps import _phase_merge

    path = tmp_path / "group-1.md"
    path.write_text(f"{BANNER}\n## Nit\n- **[N1]** `a` — x\n")
    merged = _phase_merge([str(path)], [])
    assert any(
        "output rejected by ingest" in section.body for section in merged.record.extra
    )
    assert DiagnosisKind.MALFORMED_OUTPUT.value == "malformed_output"


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
