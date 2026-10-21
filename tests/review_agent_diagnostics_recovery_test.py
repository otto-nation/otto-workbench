"""Tests for agent.session.try_recover_output: recovering a stray write from the session log."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import agent.session

from review_agent_diagnostics_support import _write_log, _pi_tool, _pi_result, _pi_text


class TestRecoveringAJsonArtifact:
    """A `.json` deliverable is salvaged as a JSON object, never trimmed as markdown."""

    DOC = json.dumps({"summary": "## Summary inside a string", "findings": []})

    def test_a_narrated_json_document_is_recovered_verbatim(self, tmp_path):
        output = tmp_path / "synthesis.json"
        output.write_text("")
        log_path = _write_log(tmp_path, _pi_text(f"Here is the file:\n{self.DOC}\nDone."),
                              _pi_result(subtype="success"))
        assert agent.session.try_recover_output(log_path, str(output)) is True
        assert json.loads(output.read_text()) == json.loads(self.DOC)

    def test_a_fenced_json_document_is_unwrapped(self, tmp_path):
        output = tmp_path / "scout.json"
        output.write_text("")
        log_path = _write_log(tmp_path, _pi_text(f"```json\n{self.DOC}\n```"),
                              _pi_result(subtype="success"))
        assert agent.session.try_recover_output(log_path, str(output)) is True
        assert json.loads(output.read_text()) == json.loads(self.DOC)

    def test_markdown_is_not_a_json_deliverable(self, tmp_path):
        output = tmp_path / "group-1.json"
        output.write_text("")
        log_path = _write_log(tmp_path, _pi_tool("write", path=str(output), content="## Must fix\n- x\n"),
                              _pi_result(subtype="success"))
        assert agent.session.try_recover_output(log_path, str(output)) is False

    PLAIN = json.dumps({"summary": "no heading in here", "findings": []})

    def test_a_real_json_write_is_recovered(self, tmp_path):
        output = tmp_path / "group-1.json"
        output.write_text("")
        log_path = _write_log(tmp_path, _pi_tool("write", path="group-1.json", content=self.PLAIN),
                              _pi_result(subtype="success"))
        assert agent.session.try_recover_output(log_path, str(output)) is True
        assert json.loads(output.read_text()) == json.loads(self.PLAIN)
