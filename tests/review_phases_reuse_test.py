"""reuse_scan loads the file a resume finds, or refuses a phase with no scan."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import review.phases
from core.phases import Phase
from review.artifacts import ScoutArtifact

_FOCUS = [{"title": "Retry", "rationale": "why", "paths": ["app.py"]}]
SCOUT_OK = json.dumps({
    "leads": [{"path": "app.py", "line": 10, "concern": "c"}],
    "focus": _FOCUS,
})


def test_a_scan_with_a_reader_renders_the_file(tmp_path):
    output = tmp_path / "scout.json"
    output.write_text(SCOUT_OK)
    result = review.phases.reuse_scan(Phase.SCOUT, str(output), str(tmp_path / "s.jsonl"))
    assert "Investigation leads from scout scan" in result.content
    assert "`app.py:10`" in result.content


def test_a_malformed_file_is_read_as_nothing(tmp_path):
    output = tmp_path / "holistic.json"
    output.write_text("not json")
    assert review.phases.reuse_scan(
        Phase.HOLISTIC, str(output), str(tmp_path / "h.jsonl"),
    ).content == ""


def test_a_phase_with_no_scan_of_its_own_raises(tmp_path):
    """SINGLE has a registry entry but declares no scan at all."""
    with pytest.raises(ValueError, match="declares no scan of its own"):
        review.phases.reuse_scan(
            Phase.SINGLE, str(tmp_path / "missing.json"), str(tmp_path / "s.jsonl"),
        )


def test_reuse_scan_returns_the_artifact_it_read(tmp_path):
    output = tmp_path / "scout.json"
    output.write_text(SCOUT_OK)
    result = review.phases.reuse_scan(Phase.SCOUT, str(output), str(tmp_path / "s.jsonl"))
    assert isinstance(result.artifact, ScoutArtifact)
    assert result.artifact.focus[0].title == "Retry"
    assert result.output == str(output)


def test_reuse_scan_reads_a_pre_focus_scan_as_nothing(tmp_path):
    output = tmp_path / "scout.json"
    output.write_text('{"leads": [{"path": "app.py", "line": 10, "concern": "c"}]}')
    result = review.phases.reuse_scan(Phase.SCOUT, str(output), str(tmp_path / "s.jsonl"))
    assert result.content == ""
    assert result.artifact is None
