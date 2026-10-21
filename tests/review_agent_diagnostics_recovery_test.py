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

    def test_a_scratch_write_beside_it_is_not_the_deliverable(self, tmp_path):
        """Matched on the whole final component, so a neighbour cannot win."""
        output = tmp_path / "review.json"
        output.write_text("")
        log_path = _write_log(
            tmp_path,
            _pi_tool("write", path=str(tmp_path / "test123.txt"), content=self.DOC),
            _pi_result(subtype="success"),
        )
        assert agent.session.try_recover_output(log_path, str(output)) is False
        assert output.read_text() == ""

    def test_a_write_to_the_deliverable_path_is_recovered(self, tmp_path):
        output = tmp_path / "review.json"
        output.write_text("")
        log_path = _write_log(
            tmp_path,
            _pi_tool("write", path=str(output), content=self.DOC),
            _pi_result(subtype="success"),
        )
        assert agent.session.try_recover_output(log_path, str(output)) is True
        assert json.loads(output.read_text()) == json.loads(self.DOC)

    def test_the_last_qualifying_write_wins(self, tmp_path):
        """A refused write is retried, and the document grows across attempts."""
        output = tmp_path / "review.json"
        output.write_text("")
        draft = json.dumps({"summary": "draft", "findings": []})
        final = json.dumps({"summary": "final", "findings": []})
        log_path = _write_log(
            tmp_path,
            _pi_tool("write", path=str(output), content=draft),
            _pi_tool("write", path="review.json", content=final),
            _pi_result(subtype="success"),
        )
        assert agent.session.try_recover_output(log_path, str(output)) is True
        assert json.loads(output.read_text()) == json.loads(final)

    def test_a_real_write_beats_narrated_text(self, tmp_path):
        """Narration is the weakest evidence, so it must not outrank a call."""
        output = tmp_path / "review.json"
        output.write_text("")
        tool = json.dumps({"summary": "from the tool", "findings": []})
        narrated = json.dumps({"summary": "from the narration", "findings": []})
        log_path = _write_log(
            tmp_path,
            _pi_tool("write", path="review.json", content=tool),
            _pi_text(narrated),
            _pi_result(subtype="success"),
        )
        assert agent.session.try_recover_output(log_path, str(output)) is True
        body = output.read_text()
        assert "from the tool" in body
        assert "from the narration" not in body

    def test_the_prompt_is_not_mistaken_for_the_agents_output(self, tmp_path):
        """The user turn carries the prompt; only assistant text is a candidate."""
        output = tmp_path / "review.json"
        output.write_text("")
        log_path = _write_log(
            tmp_path,
            _pi_text(self.DOC, role="user"),
            _pi_result(subtype="success"),
        )
        assert agent.session.try_recover_output(log_path, str(output)) is False
        assert output.read_text() == ""

    def test_a_json_shaped_call_is_decoded_not_trimmed(self, tmp_path):
        """A JSON narration carries the document as a string literal."""
        output = tmp_path / "review.json"
        output.write_text("")
        inner = json.dumps({"summary": "decoded", "findings": []}, indent=2)
        log_path = _write_log(
            tmp_path,
            _pi_text(json.dumps({
                "command": "write",
                "path": "/abs/review.json",
                "content": inner,
            }, indent=2)),
            _pi_result(subtype="success"),
        )
        assert agent.session.try_recover_output(log_path, str(output)) is True
        body = output.read_text()
        assert json.loads(body) == json.loads(inner)
        assert "\\n" not in body

    def test_a_document_quoting_json_is_not_decoded_as_a_call(self, tmp_path):
        """A review that mentions JSON is a document, not a narrated call."""
        output = tmp_path / "review.json"
        output.write_text("")
        doc = json.dumps({"summary": 'the config {"a": 1} is wrong', "findings": []})
        log_path = _write_log(
            tmp_path,
            _pi_text(doc),
            _pi_result(subtype="success"),
        )
        assert agent.session.try_recover_output(log_path, str(output)) is True
        assert json.loads(output.read_text())["summary"] == 'the config {"a": 1} is wrong'
