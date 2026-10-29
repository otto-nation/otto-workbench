"""run_phase reads a JSON artifact: one repair turn, a typed result, a reported failure."""

import json
import sys
from pathlib import Path

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import review.phases
from agent.diagnosis import Diagnosis, DiagnosisKind
from core.phases import Phase
from review.artifacts import DisproveArtifact
from review.record import Disposition
from review.steps import _phase_disprove, _phase_merge
from review.types import Group
from review_record_support import finding, record

from review_phases_support import _job

_FOCUS = [{"title": "Retry", "rationale": "why", "paths": ["a.py"]}]
SCOUT_OK = json.dumps({"leads": [{"path": "a.py", "line": 3, "concern": "c"}], "focus": _FOCUS})
SCOUT_BAD = json.dumps({"leads": [{"path": "a.py:3", "concern": "c"}], "focus": _FOCUS})
SCOUT_NO_FOCUS = json.dumps({"leads": [{"path": "a.py", "line": 3, "concern": "c"}]})


def _fake_agent(monkeypatch, *documents):
    """Each invocation writes the next document to the phase's deliverable."""
    prompts = []

    def fake(inv, throttle=None):
        prompts.append(inv.prompt)
        Path(inv.output_path).write_text(documents[min(len(prompts), len(documents)) - 1])
        Path(inv.session_log).write_text(json.dumps(
            {"type": "result", "subtype": "success", "num_turns": 1}) + "\n")
        return 0

    monkeypatch.setattr(review.phases, "run_agent", fake)
    monkeypatch.setattr(review.phases, "build_prompt", lambda *a, **k: "PROMPT")
    return prompts


def test_a_rejected_scan_gets_one_repair_turn(tmp_path, monkeypatch):
    """X4: a malformed scan is repaired once instead of read as no leads."""
    prompts = _fake_agent(monkeypatch, SCOUT_BAD, SCOUT_OK)
    result = review.phases.run_phase(_job(tmp_path), Phase.SCOUT, "scanning...")
    assert len(prompts) == 2 and "line_in_path" in prompts[1]
    assert result.content.startswith("## Scout context") and "`a.py:3`" in result.content


def test_a_scan_rejected_twice_is_reported_and_the_run_goes_on(tmp_path, monkeypatch):
    _fake_agent(monkeypatch, SCOUT_BAD)
    result = review.phases.run_phase(_job(tmp_path), Phase.SCOUT, "scanning...")
    assert result.content == "" and result.artifact is None
    assert result.diagnosis.kind is DiagnosisKind.MALFORMED_OUTPUT


def test_disprove_hands_back_its_typed_artifact(tmp_path, monkeypatch):
    doc = json.dumps({"verdicts": [{"id": "M1", "outcome": "falsified", "reason": "r"}]})
    _fake_agent(monkeypatch, doc)
    result = review.phases.run_phase(_job(tmp_path), Phase.DISPROVE, "gate...", review_content="x")
    assert isinstance(result.artifact, DisproveArtifact)
    assert result.artifact.verdicts[0].id == "M1"


def test_a_resumed_scan_is_read_from_its_file(tmp_path):
    output = tmp_path / "scout.json"
    log = str(tmp_path / "scout.jsonl")
    output.write_text(SCOUT_OK)
    assert review.phases.reuse_scan(Phase.SCOUT, str(output), log).content.startswith(
        "## Scout context")
    output.write_text(SCOUT_BAD)
    assert review.phases.reuse_scan(Phase.SCOUT, str(output), log).content == ""


def test_a_scan_without_focus_gets_a_repair_turn(tmp_path, monkeypatch):
    prompts = _fake_agent(monkeypatch, SCOUT_NO_FOCUS, SCOUT_OK)
    result = review.phases.run_phase(_job(tmp_path), Phase.SCOUT, "scanning...")
    assert len(prompts) == 2 and "focus_count" in prompts[1]
    assert result.artifact is not None and result.artifact.focus[0].title == "Retry"


FINDING = {"id": "M1", "path": "a.py", "line": 1, "body": "x", "evidence": "x = 1"}
GROUP_OK = json.dumps({"triage": [{"path": "a.py", "note": "t"}], "findings": [FINDING]})
GROUP_BAD = json.dumps({"findings": [{**FINDING, "severity": "M"}]})


def test_a_rejected_group_output_gets_one_repair_turn(tmp_path, monkeypatch):
    prompts = _fake_agent(monkeypatch, GROUP_BAD, GROUP_OK)
    grp = Group(name="g1", files=["a.py"], lines=1)
    _, output, failed = review.phases._review_group(1, grp, _job(tmp_path), 1, "")
    assert failed is None and output.endswith("group-1.json")
    assert len(prompts) == 2 and "$.findings[0].severity" in prompts[1]


def test_a_group_rejected_twice_is_a_malformed_output_failure(tmp_path, monkeypatch):
    _fake_agent(monkeypatch, GROUP_BAD)
    grp = Group(name="g1", files=["a.py"], lines=1)
    _, _, failed = review.phases._review_group(1, grp, _job(tmp_path), 1, "")
    assert failed.diagnosis.kind is DiagnosisKind.MALFORMED_OUTPUT


def test_the_merge_honours_a_group_s_decline_only_for_a_prior_finding(tmp_path):
    """D19: the runner, not the agent, decides whether a decline names a prior finding."""
    carried = {**FINDING, "sid": "3f9a2c1d", "declined": "documented tradeoff"}
    path = tmp_path / "group-1.json"
    path.write_text(json.dumps({"findings": [carried]}))
    held = _phase_merge([str(path)], [], ("g1",), declinable=frozenset({"3f9a2c1d"}))
    invented = _phase_merge([str(path)], [], ("g1",), declinable=frozenset())
    assert held.record.findings[0].disposition is Disposition.DECLINED
    assert invented.record.findings[0].disposition is Disposition.OPEN


def test_missing_file_skipped(tmp_path):
    path = tmp_path / "group-1.json"
    path.write_text(GROUP_OK)
    merged = _phase_merge(
        [str(path), str(tmp_path / "group-2.json")],
        [], ("g1", "g2"), declinable=frozenset(),
    )
    assert [f.id for f in merged.record.findings] == ["M1"]
    assert all(s.heading != "Review gaps" for s in merged.record.extra)


def test_a_whitespace_only_group_file_is_skipped_silently(tmp_path, capsys):
    """A failed group's pre-created empty file is not a malformed artifact."""
    good = tmp_path / "group-1.json"
    good.write_text(GROUP_OK)
    empty = tmp_path / "group-2.json"
    empty.write_text("  \n")
    merged = _phase_merge(
        [str(good), str(empty)], [], ("g1", "g2"), declinable=frozenset(),
    )
    seen = capsys.readouterr()
    assert "invalid_json" not in seen.out + seen.err
    assert "rejected" not in seen.out + seen.err
    assert [f.id for f in merged.record.findings] == ["M1"]
    assert all(s.heading != "Review gaps" for s in merged.record.extra)


def test_a_doc_rewritten_mid_run_merges_the_current_bytes(tmp_path):
    path = tmp_path / "group-1.json"
    path.write_text(GROUP_OK)
    path.write_text(json.dumps({
        "triage": [{"path": "a.py", "note": "t"}],
        "findings": [FINDING, {"id": "S1", "path": "a.py", "line": 2, "body": "later finding"}],
    }))
    merged = _phase_merge([str(path)], [], ("g1",), declinable=frozenset())
    assert any(f.id == "S1" and f.body == "later finding" for f in merged.record.findings)


def test_a_disprove_rejected_twice_leaves_the_record_unchanged(tmp_path, monkeypatch):
    rec = record(finding("M1", "x"))
    monkeypatch.setattr(
        "review.steps.run_phase",
        lambda *a, **k: review.phases.PhaseResult(
            diagnosis=Diagnosis(DiagnosisKind.MALFORMED_OUTPUT),
        ),
    )
    assert _phase_disprove(_job(tmp_path), rec).record == rec
