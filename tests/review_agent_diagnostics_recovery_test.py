"""Tests for agent.session.try_recover_output: recovering a stray write from the session log."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import agent.session

from review_agent_diagnostics_support import _write_log, _pi_tool, _pi_result, _pi_text


class TestRecoveringAStrayWriteFromTheLog:
    """Findings an agent wrote somewhere other than the declared deliverable.

    Three runs wrote a complete review to a bare `review.md` in the worktree
    after a project-local guard refused the absolute path. The state-directory
    file stayed at zero bytes and the findings were discarded — but Pi records
    the whole document in the `tool_execution_start` that announced the write,
    so nothing had to be swept off disk to get them back.
    """

    def test_a_write_to_the_deliverable_path_is_recovered(self, tmp_path):
        output = tmp_path / "review.md"
        output.write_text("")
        log_path = _write_log(
            tmp_path,
            _pi_tool("write", path=str(output), content="## Must fix\n- [M1] x\n"),
            _pi_result(subtype="success"),
        )
        assert agent.session.try_recover_output(log_path, str(output)) is True
        assert "## Must fix" in output.read_text()

    def test_a_relative_write_of_the_same_name_is_recovered(self, tmp_path):
        """The defect itself: the stray file is `review.md`, not the full path."""
        output = tmp_path / "review.md"
        output.write_text("")
        log_path = _write_log(
            tmp_path,
            _pi_tool("write", path="review.md", content="## Must fix\n- [M1] x\n"),
            _pi_result(subtype="success"),
        )
        assert agent.session.try_recover_output(log_path, str(output)) is True
        assert "## Must fix" in output.read_text()

    def test_a_scratch_write_beside_it_is_not_the_deliverable(self, tmp_path):
        """Matched on the whole final component, so a neighbour cannot win."""
        output = tmp_path / "review.md"
        output.write_text("")
        log_path = _write_log(
            tmp_path,
            _pi_tool("write", path=str(tmp_path / "test123.txt"),
                     content="## Must fix\n- [M1] not the review\n"),
            _pi_result(subtype="success"),
        )
        assert agent.session.try_recover_output(log_path, str(output)) is False
        assert output.read_text() == ""

    def test_a_probe_without_headings_is_not_recovered(self, tmp_path):
        """The observed log's 4-byte "test" probe precedes the real document."""
        output = tmp_path / "review.md"
        output.write_text("")
        log_path = _write_log(
            tmp_path,
            _pi_tool("write", path="review.md", content="test"),
            _pi_result(subtype="success"),
        )
        assert agent.session.try_recover_output(log_path, str(output)) is False

    def test_the_last_qualifying_write_wins(self, tmp_path):
        """A refused write is retried, and the document grows across attempts."""
        output = tmp_path / "review.md"
        output.write_text("")
        log_path = _write_log(
            tmp_path,
            _pi_tool("write", path=str(output), content="## Must fix\n- draft\n"),
            _pi_tool("write", path="review.md", content="## Must fix\n- final\n"),
            _pi_result(subtype="success"),
        )
        assert agent.session.try_recover_output(log_path, str(output)) is True
        assert "final" in output.read_text()
        assert "draft" not in output.read_text()

    def test_a_narrated_write_is_recovered(self, tmp_path):
        """The shape neither other source sees: the document as assistant text.

        Three consecutive review runs ended this way against claude-sonnet-5
        on Vertex — the model spelled the `write` call out in prose and never
        called it, so the run reached agent_end with the findings present and
        no tool_execution_start to read them from.
        """
        output = tmp_path / "review.md"
        output.write_text("")
        doc = "# Self-Review: repo\n\n## Must fix\n- [ ] **[M1]** `f.sh:1` — x\n"
        log_path = _write_log(
            tmp_path,
            _pi_text('I already wrote the file.\n\nwrite review.md "' + doc + '"'),
            _pi_result(subtype="success"),
        )
        assert agent.session.try_recover_output(log_path, str(output)) is True
        body = output.read_text()
        assert "[M1]" in body
        # The narration is dropped and the document's own title survives: the
        # title is behind an opening quote, so a line-anchored search for a
        # heading skips it and takes the `## Must fix` below instead.
        assert body.startswith("# Self-Review: repo")
        assert "I already wrote" not in body

    def test_narration_structured_with_its_own_subheading_is_still_trimmed(self, tmp_path):
        """A model that headers its own commentary must not win the boundary.

        `_from_first_heading` used to match the first heading of any level,
        so a model narrating with a `## My plan` subheading ahead of the real
        `# Self-Review: ...` title had that plan section survive into the
        recovered document, verbatim, ahead of the document itself.
        """
        output = tmp_path / "review.md"
        output.write_text("")
        text = (
            "## My plan\nI will now write the review.\n\n"
            "# Self-Review: repo\n\n## Must fix\n- [M1] x\n"
        )
        log_path = _write_log(
            tmp_path,
            _pi_text(text),
            _pi_result(subtype="success"),
        )
        assert agent.session.try_recover_output(log_path, str(output)) is True
        body = output.read_text()
        assert body.startswith("# Self-Review: repo")
        assert "My plan" not in body

    def test_a_narrated_write_spelled_as_xml_is_recovered(self, tmp_path):
        """The other narration shape the module's docstring names but did not test.

        Some runs spell the tool call out as XML rather than prose, e.g.
        `<invoke name="write"><parameter name="content">...`. The document
        inside is recovered the same way as prose narration: by its own
        line-anchored heading, with the surrounding tags treated as preamble.
        """
        output = tmp_path / "review.md"
        output.write_text("")
        text = (
            '<invoke name="write">\n'
            '<parameter name="path">review.md</parameter>\n'
            '<parameter name="content">\n'
            "# Self-Review: repo\n\n## Must fix\n- [M1] x\n"
            "</parameter>\n</invoke>"
        )
        log_path = _write_log(
            tmp_path,
            _pi_text(text),
            _pi_result(subtype="success"),
        )
        assert agent.session.try_recover_output(log_path, str(output)) is True
        body = output.read_text()
        assert body.startswith("# Self-Review: repo")
        assert "[M1]" in body

    def test_a_fenced_document_is_unwrapped(self, tmp_path):
        output = tmp_path / "review.md"
        output.write_text("")
        log_path = _write_log(
            tmp_path,
            _pi_text("Here it is:\n\n```markdown\n## Must fix\n- [M1] x\n```\n"),
            _pi_result(subtype="success"),
        )
        assert agent.session.try_recover_output(log_path, str(output)) is True
        body = output.read_text()
        assert "[M1]" in body
        assert "```" not in body
        assert "Here it is" not in body

    def test_commentary_fenced_after_the_document_is_not_recovered(self, tmp_path):
        """A second fenced block after the review must not outrank it.

        The scan keeps the *last* qualifying candidate, so a reply that closes
        its review and then adds a ```bash``` aside recovered the aside. The
        heading test is applied per block for this reason, not only to the
        winner.
        """
        output = tmp_path / "review.md"
        output.write_text("")
        doc = "# Rev\n\n## Must fix\n- [M1] real\n"
        log_path = _write_log(
            tmp_path,
            _pi_text(
                # A ```markdown``` aside, not ```bash```: the caller's own
                # "## " gate already rejects an untitled block, so only an
                # aside that carries a heading of its own reaches the
                # comparison this test exists to pin.
                "Here:\n\n```markdown\n" + doc + "```\n\n"
                "For reference:\n\n```markdown\n## Notes\nrm -rf /\n```\n",
            ),
            _pi_result(subtype="success"),
        )
        assert agent.session.try_recover_output(log_path, str(output)) is True
        body = output.read_text()
        assert "[M1]" in body
        assert "rm -rf" not in body

    def test_the_last_of_two_documents_wins(self, tmp_path):
        """A redrafted document is the one to keep, as with a retried write."""
        output = tmp_path / "review.md"
        output.write_text("")
        log_path = _write_log(
            tmp_path,
            _pi_text(
                "Draft:\n\n```markdown\n# Rev\n\n## Must fix\n- draft\n```\n\n"
                "Final:\n\n```markdown\n# Rev\n\n## Must fix\n- final\n```\n",
            ),
            _pi_result(subtype="success"),
        )
        assert agent.session.try_recover_output(log_path, str(output)) is True
        body = output.read_text()
        assert "final" in body
        assert "draft" not in body

    def test_an_unclosed_fence_keeps_what_the_agent_produced(self, tmp_path):
        """A reply cut off mid-document is a partial review worth keeping."""
        output = tmp_path / "review.md"
        output.write_text("")
        log_path = _write_log(
            tmp_path,
            _pi_text("Here:\n\n```markdown\n# Rev\n\n## Must fix\n- [M1] x\n"),
            _pi_result(subtype="success"),
        )
        assert agent.session.try_recover_output(log_path, str(output)) is True
        assert "[M1]" in output.read_text()

    def test_a_deliverable_with_no_level_one_title_is_still_trimmed(self, tmp_path):
        """The scout artifact's format starts at `## Investigation Leads`.

        Anchoring the trim on `# ` alone stops trimming entirely for that
        deliverable, so the narration stays in front of the document. The
        anchor is the shallowest heading present, which is the document's
        own outline root whatever level it starts at.
        """
        output = tmp_path / "review.md"
        output.write_text("")
        log_path = _write_log(
            tmp_path,
            _pi_text(
                "I will write the file now.\n\n"
                "## Investigation Leads\n- **`a.py:1`** — x\n",
            ),
            _pi_result(subtype="success"),
        )
        assert agent.session.try_recover_output(log_path, str(output)) is True
        body = output.read_text()
        assert body.startswith("## Investigation Leads")
        assert "I will write" not in body

    def test_narration_with_its_own_subheading_is_trimmed_away(self, tmp_path):
        """A model that titles its own plan must not have the plan recovered."""
        output = tmp_path / "review.md"
        output.write_text("")
        log_path = _write_log(
            tmp_path,
            _pi_text(
                "## My plan\nI will now write it.\n\n"
                "# Self-Review: repo\n\n## Must fix\n- [M1] x\n",
            ),
            _pi_result(subtype="success"),
        )
        assert agent.session.try_recover_output(log_path, str(output)) is True
        body = output.read_text()
        assert body.startswith("# Self-Review: repo")
        assert "My plan" not in body

    def test_an_xml_shaped_call_loses_its_closing_tags(self, tmp_path):
        """The shape the docstring claimed but no test pinned.

        Observed in a real run: the model emitted `<write><path>...` and
        `<invoke name="bash">` as text. Recovered verbatim the document
        carries `</content></write>` on the end.
        """
        output = tmp_path / "review.md"
        output.write_text("")
        log_path = _write_log(
            tmp_path,
            _pi_text(
                "<write>\n<path>/abs/review.md</path>\n<content>\n"
                "# Rev\n\n## Must fix\n- [M1] x\n"
                "</content>\n</write>",
            ),
            _pi_result(subtype="success"),
        )
        assert agent.session.try_recover_output(log_path, str(output)) is True
        body = output.read_text()
        assert "[M1]" in body
        assert "</content>" not in body
        assert "</write>" not in body

    def test_a_json_shaped_call_is_decoded_not_trimmed(self, tmp_path):
        """A JSON narration carries the document as a string literal.

        Its newlines are a backslash and an `n`, so recovering the text
        verbatim yields one long line rather than a markdown document. No
        trim fixes that — the body has to be decoded.
        """
        output = tmp_path / "review.md"
        output.write_text("")
        log_path = _write_log(
            tmp_path,
            _pi_text(json.dumps({
                "command": "write",
                "path": "/abs/review.md",
                "content": "# Rev\n\n## Must fix\n- [M1] x\n",
            }, indent=2)),
            _pi_result(subtype="success"),
        )
        assert agent.session.try_recover_output(log_path, str(output)) is True
        body = output.read_text()
        assert "[M1]" in body
        assert "\\n" not in body
        assert body.startswith("# Rev\n")

    def test_a_document_ending_in_a_brace_keeps_it(self, tmp_path):
        """The trim is XML-tag only, so a code sample's last line survives.

        Stripping a trailing `}` or `"` as call syntax would eat the end of
        any document whose final line is a closing brace or a quote.
        """
        output = tmp_path / "review.md"
        output.write_text("")
        log_path = _write_log(
            tmp_path,
            _pi_text("# Rev\n\n## Must fix\n- [M1] see:\n\n    foo = {\n      bar: 1\n    }\n"),
            _pi_result(subtype="success"),
        )
        assert agent.session.try_recover_output(log_path, str(output)) is True
        assert output.read_text().rstrip().endswith("}")

    def test_a_document_quoting_json_is_not_decoded_as_a_call(self, tmp_path):
        """A review that mentions JSON is a document, not a narrated call."""
        output = tmp_path / "review.md"
        output.write_text("")
        log_path = _write_log(
            tmp_path,
            _pi_text('# Rev\n\n## Must fix\n- [M1] the config `{"a": 1}` is wrong\n'),
            _pi_result(subtype="success"),
        )
        assert agent.session.try_recover_output(log_path, str(output)) is True
        assert '{"a": 1}' in output.read_text()

    def test_a_fenced_document_with_a_nested_fence_is_not_truncated(self, tmp_path):
        """A Must-fix item's own evidence block nests a fence inside the outer one.

        A lazy `.*?` in `_FENCED_DOCUMENT` stops at the inner closing fence,
        dropping everything after it — exactly the shape the review format
        this pipeline generates asks agents to produce.
        """
        output = tmp_path / "review.md"
        output.write_text("")
        doc = (
            "## Must fix\n"
            "- [M1] evidence:\n\n"
            "```bash\necho hi\n```\n\n"
            "## Should fix\n"
            "- [S1] more\n"
        )
        log_path = _write_log(
            tmp_path,
            _pi_text("Here it is:\n\n```markdown\n" + doc + "```\n"),
            _pi_result(subtype="success"),
        )
        assert agent.session.try_recover_output(log_path, str(output)) is True
        body = output.read_text()
        assert "[M1]" in body
        assert "[S1]" in body

    def test_assistant_chatter_without_a_heading_is_not_recovered(self, tmp_path):
        """What keeps the text source from inventing a review out of a refusal."""
        output = tmp_path / "review.md"
        output.write_text("")
        log_path = _write_log(
            tmp_path,
            _pi_text("I could not complete the review. Please advise."),
            _pi_result(subtype="success"),
        )
        assert agent.session.try_recover_output(log_path, str(output)) is False
        assert output.read_text() == ""

    def test_the_prompt_is_not_mistaken_for_the_agents_output(self, tmp_path):
        """The user turn carries every heading the prompt does.

        An agent_end record holds the user message beside the assistant's, so
        a reader that took any text block would recover the prompt itself and
        report a review nobody wrote.
        """
        output = tmp_path / "review.md"
        output.write_text("")
        log_path = _write_log(
            tmp_path,
            _pi_text("## Review format\nWrite your findings here.", role="user"),
            _pi_result(subtype="success"),
        )
        assert agent.session.try_recover_output(log_path, str(output)) is False
        assert output.read_text() == ""

    def test_a_real_write_beats_narrated_text(self, tmp_path):
        """Narration is the weakest evidence, so it must not outrank a call."""
        output = tmp_path / "review.md"
        output.write_text("")
        log_path = _write_log(
            tmp_path,
            _pi_tool("write", path="review.md", content="## Must fix\n- from the tool\n"),
            _pi_text("## Must fix\n- from the narration\n"),
            _pi_result(subtype="success"),
        )
        assert agent.session.try_recover_output(log_path, str(output)) is True
        assert "from the tool" in output.read_text()
        assert "from the narration" not in output.read_text()

    def test_a_claude_denial_is_still_recovered(self, tmp_path):
        """The Claude source keeps working alongside the new Pi one."""
        output = tmp_path / "review.md"
        log_path = _write_log(
            tmp_path,
            json.dumps({
                "type": "result",
                "permission_denials": [
                    {"tool_input": {"content": "## Must fix\n- [M1] denied\n"}},
                ],
            }),
        )
        assert agent.session.try_recover_output(log_path, str(output)) is True
        assert "denied" in output.read_text()
