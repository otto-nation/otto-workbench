"""run_phase reads a JSON artifact: one repair turn, a typed result, a reported failure."""

import json
import sys
from pathlib import Path

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import review.phases
from agent.diagnosis import DiagnosisKind
from core.phases import Phase
from review.artifacts import DisproveArtifact

from review_phases_support import _job

SCOUT_OK = json.dumps({"leads": [{"path": "a.py", "line": 3, "concern": "c"}]})
SCOUT_BAD = json.dumps({"leads": [{"path": "a.py:3", "concern": "c"}]})


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
    output.write_text(SCOUT_OK)
    assert review.phases.read_scan(Phase.SCOUT, str(output)).startswith("## Scout context")
    output.write_text(SCOUT_BAD)
    assert review.phases.read_scan(Phase.SCOUT, str(output)) == ""
