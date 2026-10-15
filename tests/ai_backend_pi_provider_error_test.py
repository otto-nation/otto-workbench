"""A provider error Pi reports in its stream is a failure, not an empty success.

When the provider fails after Pi's own retries — a Vertex 404 or 429 — Pi exits
0 and ends the run with an assistant message carrying no content,
``stopReason: "error"`` and an ``errorMessage``. Read as a reply, that is empty
text with exit 0: the ledger records a clean call, the caller's retry sees a
blank answer, and the reason is gone. The fixture is a live capture of exactly
that shape (see tests/fixtures/README-pi-fixtures.md).
"""

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import agent.backend_pi
import agent.usage
from agent.backend_events import pi_prompt_result, pi_run_error
from ai_backend_pi_rpc_test import _RefusingProc
from ai_backend_pi_support import FIXTURES, _stats_response


def _error_stream() -> str:
    return (FIXTURES / "pi_prompt_error_session.jsonl").read_text()


def _ok_stream() -> str:
    return (FIXTURES / "pi_prompt_session.jsonl").read_text()


def _agent_end(messages, will_retry=False) -> str:
    return json.dumps({"type": "agent_end", "messages": messages,
                       "willRetry": will_retry}) + "\n"


def _assistant(text="", stop="stop", error=None) -> dict:
    content = [{"type": "text", "text": text}] if text else []
    msg = {"role": "assistant", "content": content, "stopReason": stop}
    if error is not None:
        msg["errorMessage"] = error
    return msg


class TestPromptResult:
    def test_a_provider_error_is_reported_with_its_reason(self):
        result = pi_prompt_result(_error_stream())
        assert not result.ok
        assert result.error.startswith("404")
        assert "NOT_FOUND" in result.error
        assert result.text == ""

    def test_a_normal_reply_is_not_an_error(self):
        result = pi_prompt_result(_ok_stream())
        assert result.ok
        assert result.error is None
        assert result.text.startswith("The contents of `f.txt`")

    def test_a_failed_call_is_still_measured(self):
        # It cost nothing, and the ledger should say so beside a non-zero exit
        # rather than drop the row.
        usage = pi_prompt_result(_error_stream()).usage
        assert usage is not None
        assert usage.total_tokens == 0

    def test_an_attempt_pi_retried_into_success_is_not_an_error(self):
        stream = (
            _agent_end([_assistant(stop="error", error="429 quota")], will_retry=True)
            + _agent_end([_assistant(text="fine")])
        )
        result = pi_prompt_result(stream)
        assert result.ok
        assert result.text == "fine"

    def test_an_error_with_no_message_still_fails(self):
        result = pi_prompt_result(_agent_end([_assistant(stop="error")]))
        assert not result.ok
        assert result.error

    def test_an_earlier_errored_turn_does_not_fail_a_reply_that_finished(self):
        assert pi_run_error([json.loads(_agent_end([
            _assistant(stop="error", error="boom"), _assistant(text="recovered"),
        ]))]) == ""


class TestPromptExit:
    def _prompt(self, monkeypatch, stdout, returncode=0):
        class _Result:
            pass
        result = _Result()
        result.stdout, result.stderr, result.returncode = stdout, "", returncode
        monkeypatch.setattr(agent.backend_pi.subprocess, "run", lambda *a, **k: result)
        return agent.backend_pi.prompt("anything", cwd=".")

    def test_a_provider_error_exits_non_zero_with_the_reason(self, monkeypatch, capsys):
        reply, code, usage = self._prompt(monkeypatch, _error_stream())
        assert code != 0
        assert "NOT_FOUND" in reply
        assert "NOT_FOUND" in capsys.readouterr().err
        assert usage is not None

    def test_pi_s_own_status_wins_when_it_reported_one(self, monkeypatch):
        _reply, code, _usage = self._prompt(monkeypatch, _error_stream(), returncode=7)
        assert code == 7

    def test_a_normal_reply_exits_zero(self, monkeypatch):
        reply, code, _usage = self._prompt(monkeypatch, _ok_stream())
        assert code == 0
        assert reply.endswith("```")


class TestRpcProviderError:
    """The agent and fix modes read the same final message off agent_end."""

    def _run(self, monkeypatch, tmp_path, lines, entry_point="invoke_agent"):
        proc = _RefusingProc(lines)
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: proc)
        log = tmp_path / "s.jsonl"
        code = getattr(agent.backend_pi, entry_point)(agent.backend_pi.AgentInvocation(
            prompt="p", cwd=str(tmp_path), session_log=str(log),
        ))
        return code, proc, log

    def _errored(self):
        return [
            _agent_end([_assistant(stop="error", error="429 RESOURCE_EXHAUSTED")]),
            json.dumps(_stats_response()) + "\n",
        ]

    def test_the_stream_records_the_provider_error(self):
        proc = _RefusingProc(self._errored())
        stream = agent.backend_pi._consume_stream(proc, io.StringIO(), "")
        assert stream.stop_reason == "error"
        assert stream.provider_error == "429 RESOURCE_EXHAUSTED"
        # Not an RPC refusal: Pi ran, and the stats query is still owed.
        assert stream.error is None

    @pytest.mark.parametrize("entry_point", ["invoke_agent", "invoke_fix"])
    def test_a_provider_error_exits_non_zero(self, monkeypatch, tmp_path, entry_point):
        code, _proc, _log = self._run(monkeypatch, tmp_path, self._errored(), entry_point)
        assert code != 0

    def test_the_result_record_carries_the_reason(self, monkeypatch, tmp_path):
        _code, _proc, log = self._run(monkeypatch, tmp_path, self._errored())
        record = json.loads(log.read_text().splitlines()[-1])
        assert record["is_error"] is True
        assert record["error"] == "429 RESOURCE_EXHAUSTED"
        assert record["subtype"] == "error"

    def test_the_spend_before_the_error_is_still_measured(self, monkeypatch, tmp_path):
        _code, proc, log = self._run(monkeypatch, tmp_path, self._errored())
        assert [c for c in proc.stdin.commands if c["type"] == "get_session_stats"]
        assert agent.usage.parse_session_log(str(log)).cost > 0

    def test_a_retrying_agent_end_is_not_the_end_of_the_run(self):
        # The retry's own ending is the answer, so the reader has to reach it:
        # stopping at the first agent_end reports neither outcome correctly.
        proc = _RefusingProc([
            _agent_end([_assistant(stop="error", error="429 first")], will_retry=True),
            _agent_end([_assistant(stop="error", error="429 after retry")]),
        ])
        stream = agent.backend_pi._consume_stream(proc, io.StringIO(), "")
        assert stream.provider_error == "429 after retry"

    def test_a_retry_that_succeeds_completes_the_run(self):
        proc = _RefusingProc([
            _agent_end([_assistant(stop="error", error="429")], will_retry=True),
            _agent_end([_assistant(text="done")]),
        ])
        stream = agent.backend_pi._consume_stream(proc, io.StringIO(), "")
        assert stream.stop_reason == "completed"
        assert stream.provider_error is None

    def test_a_healthy_run_exits_zero(self, monkeypatch, tmp_path):
        code, _proc, _log = self._run(monkeypatch, tmp_path, [
            _agent_end([_assistant(text="done")]),
            json.dumps(_stats_response()) + "\n",
        ])
        assert code == 0
