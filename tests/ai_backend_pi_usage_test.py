"""Tests for agent.backend_pi usage: session stats, prompt cost and tool labels."""

import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import agent.backend_pi

from ai_backend_pi_support import FIXTURES, _stats_response


def _prompt_stream() -> str:
    return (FIXTURES / "pi_prompt_session.jsonl").read_text()


# The fixture's two message_end costs (0.0075423 + 0.00470015) summed. Shared
# by every assertion against the fixture's total so a recapture only needs
# updating here.
EXPECTED_PROMPT_COST = 0.01224245


def _parsed_result_record(tmp_path, model):
    """Write a result record for the captured stats fixture and parse it back.

    Shared by the three envelope tests below so a future signature change to
    _write_result_record — a new positional argument, a renamed field — is a
    one-place edit instead of three.
    """
    import agent.usage

    log = tmp_path / "session.jsonl"
    agent.backend_pi._write_result_record(
        str(log), "completed", 1, 0.084319, 1234,
        _stats_response()["data"], model,
    )
    return agent.usage.parse_session_log(str(log))


class TestSessionStatsEnvelope:
    """get_session_stats returns {type, command, success, data} — read the data."""

    def test_stats_are_unwrapped_from_the_response(self):
        # Reading `tokens` off the envelope yields {} and a record of four
        # zeroes, which reaches the ledger as a run that cost money and spent
        # no tokens. Nothing else in the pipeline can tell that apart from a
        # genuinely cache-free call.
        response = _stats_response()

        class _Proc:
            stdin = io.StringIO()

            def __init__(self):
                self.stdout = io.StringIO(json.dumps(response) + "\n")

        stats = agent.backend_pi._get_stats_after_agent_end(_Proc())

        assert stats["tokens"]["cacheWrite"] == 33710
        assert stats["cost"] == 0.084319
        assert "data" not in stats, "the envelope was returned instead of its data"

    def test_a_null_data_field_falls_back_to_the_envelope_not_none(self):
        # An explicit "data": null (e.g. on success: false) must not surface
        # as None — _write_result_record would then crash on
        # stats.get("tokens", {}) instead of degrading gracefully.
        response = {"type": "response", "command": "get_session_stats", "success": False, "data": None}

        class _Proc:
            stdin = io.StringIO()

            def __init__(self):
                self.stdout = io.StringIO(json.dumps(response) + "\n")

        stats = agent.backend_pi._get_stats_after_agent_end(_Proc())

        assert stats is not None
        assert stats.get("tokens", {}) == {}

    def test_an_empty_data_field_is_not_replaced_by_the_envelope(self):
        # {} is falsy but present — `or` treats it the same as a missing key
        # and returns the envelope, which then reads `success`/`type` as
        # if they were stats. Presence, not truthiness, decides the fallback.
        response = {"type": "response", "command": "get_session_stats", "success": True, "data": {}}

        class _Proc:
            stdin = io.StringIO()

            def __init__(self):
                self.stdout = io.StringIO(json.dumps(response) + "\n")

        stats = agent.backend_pi._get_stats_after_agent_end(_Proc())

        assert stats == {}
        assert "success" not in stats, "the envelope was returned instead of its (empty) data"

    def test_result_record_carries_the_real_token_counts(self, tmp_path):
        parsed = _parsed_result_record(tmp_path, "claude-opus-5")
        assert parsed.cost == pytest.approx(0.084319)
        assert parsed.input_tokens == 2
        assert parsed.output_tokens == 4
        assert parsed.cache_write_tokens == 33710
        # The whole point: the session moved 33,716 tokens and the ledger says so.
        assert parsed.total_tokens == 33716

    def test_result_record_attributes_cost_to_a_model(self, tmp_path):
        # Without modelUsage every Pi row is blank under `otto-log stats --by model`.
        parsed = _parsed_result_record(tmp_path, "claude-opus-5")
        assert parsed.cost_by_model == {"claude-opus-5": pytest.approx(0.084319)}

    def test_an_unnamed_model_still_records_cost(self, tmp_path):
        parsed = _parsed_result_record(tmp_path, None)
        assert parsed.cost == pytest.approx(0.084319)
        assert parsed.cost_by_model == {}

    def test_invoke_agent_s_model_key_matches_prompt_s_model_key(self, tmp_path):
        # invoke_agent/invoke_fix write modelUsage keyed on stream.model, which
        # _consume_stream fills from message_end's own model field. If that
        # ever drifted back to the caller-requested alias, the same physical
        # run would land under two different cost_by_model keys depending on
        # whether it went through prompt() or invoke_agent() — splitting
        # `otto-log stats --by model` for the same model.
        import agent.usage
        from agent.backend_events import pi_prompt_result

        class _Proc:
            stdin = io.StringIO()

            def __init__(self):
                self.stdout = io.StringIO(_prompt_stream())

        stream = agent.backend_pi._consume_stream(_Proc(), io.StringIO(), "")

        log = tmp_path / "session.jsonl"
        agent.backend_pi._write_result_record(
            str(log), stream.stop_reason, stream.turn_count,
            stream.accumulated_cost, 1234, {}, stream.model or "claude-opus-5",
        )
        parsed = agent.usage.parse_session_log(str(log))

        _, prompt_usage = pi_prompt_result(_prompt_stream())

        assert stream.model == "claude-haiku-4-5@20251001"
        assert set(parsed.cost_by_model) == set(prompt_usage.cost_by_model)


class TestPromptUsage:
    """A prompt call is measured, or it is invisible to the ledger."""

    def test_prompt_command_asks_for_the_json_stream(self):
        # Bare `-p` emits prose and no usage, which is how 953 prompt-shaped
        # calls would have gone unrecorded.
        cmd = agent.backend_pi._build_prompt_cmd(model="haiku")
        assert "--mode" in cmd and cmd[cmd.index("--mode") + 1] == "json"
        assert cmd.index("-p") < cmd.index("--mode")

    def test_reply_is_the_final_assistant_text(self):
        from agent.backend_events import pi_prompt_result

        text, _ = pi_prompt_result(_prompt_stream())
        assert text == "The contents of `f.txt` are:\n\n```\nhello\n```"

    def test_an_earlier_assistant_turn_does_not_win(self):
        # The captured fixture's first assistant message is a tool call with no
        # text, so it cannot tell first-from-last apart on its own. This stream
        # gives both turns text: taking the first would answer with the model
        # thinking aloud before it had the file.
        from agent.backend_events import pi_prompt_result

        stream = json.dumps({
            "type": "agent_end",
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "read it"}]},
                {"role": "assistant", "content": [{"type": "text", "text": "Let me look."}]},
                {"role": "toolResult", "content": [{"type": "text", "text": "hello"}]},
                {"role": "assistant", "content": [{"type": "text", "text": "It says hello."}]},
            ],
        })

        text, _ = pi_prompt_result(stream)
        assert text == "It says hello."

    def test_cost_sums_every_message_not_just_the_last(self):
        # The fixture is a tool-using prompt: two assistant messages, two costs
        # (0.0075423 and 0.00470015). Taking the last would report 38% of it.
        from agent.backend_events import pi_prompt_result

        _, usage = pi_prompt_result(_prompt_stream())
        assert usage.cost == pytest.approx(EXPECTED_PROMPT_COST)
        assert usage.cost > 0.0075423, "only the last message_end was counted"

    def test_every_token_column_is_populated(self):
        from agent.backend_events import pi_prompt_result

        _, usage = pi_prompt_result(_prompt_stream())
        assert usage.input_tokens == 23
        assert usage.output_tokens == 382
        assert usage.cache_read_tokens == 78282
        assert usage.cache_write_tokens == 1985

    def test_prose_stdout_degrades_to_unmeasured(self):
        # A Pi output-format change should cost the measurement, not the call.
        from agent.backend_events import pi_prompt_result

        text, usage = pi_prompt_result("just the answer, no JSON here")
        assert text == "just the answer, no JSON here"
        assert usage is None

    def test_json_without_message_end_is_unmeasured_not_free(self):
        # A zero-cost row reads as a call that genuinely cost nothing, which is
        # a different claim from one nobody measured.
        from agent.backend_events import pi_prompt_result

        stream = '{"type":"agent_end","messages":[{"role":"assistant","content":[{"type":"text","text":"hi"}]}]}'
        text, usage = pi_prompt_result(stream)
        assert text == "hi"
        assert usage is None

    def test_prompt_returns_the_usage_it_parsed(self, monkeypatch):
        captured = _prompt_stream()

        class _Result:
            stdout = captured
            returncode = 0

        monkeypatch.setattr(agent.backend_pi.subprocess, "run", lambda *a, **k: _Result())
        reply, code, usage = agent.backend_pi.prompt("anything", cwd=".")

        assert code == 0
        assert reply.endswith("```")
        assert usage is not None, "prompt dropped the usage it was handed"
        assert usage.cost == pytest.approx(EXPECTED_PROMPT_COST)


class TestPiToolLabels:
    """Labels are built from Pi's own event shape, not Claude's."""

    def test_a_real_tool_event_labels(self):
        from agent.backend_events import _pi_tool_label

        events = [
            json.loads(line) for line in _prompt_stream().splitlines() if line.strip()
        ]
        starts = [e for e in events if e.get("type") == "tool_execution_start"]
        assert starts, "fixture carries no tool_execution_start to label"

        # Pi spells the bag `args` and the path `path`. Reading Claude's
        # `arguments`/`file_path` finds nothing and every label falls back to
        # the bare tool name, which no hand-written fixture would reveal.
        assert _pi_tool_label(starts[0]) == "Read f.txt"

    def test_claude_spelling_still_labels(self):
        from agent.backend_events import _pi_tool_label

        assert _pi_tool_label(
            {"toolName": "read", "arguments": {"file_path": "/a/b/c.txt"}}
        ) == "Read c.txt"

    def test_an_empty_arg_bag_is_the_answer_not_a_miss(self):
        # Pi sent `args: {}` — a tool called with no arguments. Falling through
        # to Claude's spelling would answer from a bag this event did not have.
        from agent.backend_events import _pi_tool_label

        assert _pi_tool_label(
            {"toolName": "read", "args": {}, "arguments": {"file_path": "/a/z.txt"}}
        ) == "Read "

    def test_a_tool_with_no_arg_bag_still_labels(self):
        from agent.backend_events import _pi_tool_label

        assert _pi_tool_label({"toolName": "bash"}) == "Bash"
