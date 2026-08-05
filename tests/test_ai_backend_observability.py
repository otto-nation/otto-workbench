"""Tests for machine-readable output from the prompt and fix entry points.

Only invoke_agent asked for --output-format, so prompt() and invoke_fix() produced
nothing parseable and every pr-rebase and review-threads call went unmeasured.
"""

import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import ai_backend
import ai_backend_claude
import ai_usage

RESULT_ENVELOPE = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": "the answer",
    "total_cost_usd": 0.42,
    "duration_ms": 1800,
    "modelUsage": {
        "claude-sonnet-4-6": {
            "inputTokens": 12, "outputTokens": 340,
            "cacheReadInputTokens": 9000, "cacheCreationInputTokens": 100,
            "costUSD": 0.42,
        },
    },
}


class TestBuildPromptCmd:
    def test_requests_json_output(self):
        cmd = ai_backend_claude._build_prompt_cmd()
        assert "--output-format" in cmd
        assert cmd[cmd.index("--output-format") + 1] == "json"

    def test_output_format_requires_print_flag(self):
        """--output-format only works with --print; -p must already be present."""
        cmd = ai_backend_claude._build_prompt_cmd()
        assert "-p" in cmd

    def test_model_still_passed(self):
        cmd = ai_backend_claude._build_prompt_cmd(model="claude-opus-4-6")
        assert cmd[cmd.index("--model") + 1] == "claude-opus-4-6"


class TestBuildFixCmd:
    def test_requests_stream_json_output(self):
        cmd = ai_backend_claude._build_fix_cmd(add_dirs=[])
        assert "--output-format" in cmd
        assert cmd[cmd.index("--output-format") + 1] == "stream-json"


class TestPromptParsesEnvelope:
    def _run(self, monkeypatch, stdout, returncode=0):
        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, returncode, stdout, "")
        monkeypatch.setattr(subprocess, "run", fake_run)
        return ai_backend_claude.prompt("hi")

    def test_returns_result_text_not_raw_json(self, monkeypatch):
        text, code, usage = self._run(monkeypatch, json.dumps(RESULT_ENVELOPE))
        assert text == "the answer"
        assert code == 0

    def test_extracts_usage(self, monkeypatch):
        _, _, usage = self._run(monkeypatch, json.dumps(RESULT_ENVELOPE))
        assert usage.cost == pytest.approx(0.42)
        assert usage.input_tokens == 12
        assert usage.cache_read_tokens == 9000
        assert usage.cost_by_model == pytest.approx({"claude-sonnet-4-6": 0.42})

    def test_non_json_stdout_falls_back_to_raw(self, monkeypatch):
        """A CLI output change must degrade to today's behavior, not break callers."""
        text, code, usage = self._run(monkeypatch, "plain text reply")
        assert text == "plain text reply"
        assert code == 0
        assert usage is None

    def test_envelope_without_result_key_falls_back_to_raw(self, monkeypatch):
        payload = json.dumps({"type": "result", "total_cost_usd": 0.1})
        text, _, usage = self._run(monkeypatch, payload)
        assert text == payload
        assert usage.cost == pytest.approx(0.1)

    def test_failure_exit_code_preserved(self, monkeypatch):
        _, code, _ = self._run(monkeypatch, "", returncode=2)
        assert code == 2


class TestPromptRecordsToLedger:
    @pytest.fixture
    def ledger(self, tmp_path, monkeypatch):
        d = tmp_path / "usage"
        monkeypatch.setattr(ai_usage, "LEDGER_DIR", d)
        monkeypatch.setattr(ai_usage, "_warned", False)
        return d

    def test_prompt_records_usage(self, ledger, monkeypatch):
        mod = types.SimpleNamespace(
            prompt=lambda text, **kw: (
                "answer", 0,
                ai_usage.SessionUsage(cost=0.42, input_tokens=12, cache_read_tokens=9000),
            ),
        )
        monkeypatch.setattr(ai_backend, "_get_module", lambda: mod)
        text, code = ai_backend.prompt("hi", task="conflict-resolve")
        assert (text, code) == ("answer", 0)
        rec = json.loads(next(ledger.glob("*.jsonl")).read_text().strip())
        assert rec["entry_point"] == "prompt"
        assert rec["task"] == "conflict-resolve"
        assert rec["cost"] == pytest.approx(0.42)
        assert rec["cache_read_tokens"] == 9000

    def test_unmeasured_prompt_records_nothing(self, ledger, monkeypatch):
        """A zero row reads as a free call; an absent row reads as unmeasured."""
        mod = types.SimpleNamespace(prompt=lambda text, **kw: ("answer", 0, None))
        monkeypatch.setattr(ai_backend, "_get_module", lambda: mod)
        assert ai_backend.prompt("hi") == ("answer", 0)
        assert list(ledger.glob("*.jsonl")) == []

    def test_backend_returning_pair_still_works(self, ledger, monkeypatch):
        """A backend that has not adopted the usage triple must not crash dispatch."""
        mod = types.SimpleNamespace(prompt=lambda text, **kw: ("answer", 0))
        monkeypatch.setattr(ai_backend, "_get_module", lambda: mod)
        assert ai_backend.prompt("hi") == ("answer", 0)
        assert list(ledger.glob("*.jsonl")) == []


class TestFixWritesSessionLog:
    def _fake_proc(self, monkeypatch, lines, returncode=0):
        class FakeProc:
            def __init__(self):
                self.stdout = iter(lines)
                self.stdin = types.SimpleNamespace(write=lambda s: None, close=lambda: None)
                self.returncode = returncode

            def wait(self):
                return self.returncode

        monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: FakeProc())

    def test_writes_session_log(self, monkeypatch, tmp_path, capsys):
        log = tmp_path / "fix.jsonl"
        lines = [json.dumps(RESULT_ENVELOPE) + "\n"]
        self._fake_proc(monkeypatch, lines)
        ai_backend_claude.invoke_fix("p", session_log=str(log), add_dirs=[])
        assert ai_usage.parse_session_log(str(log)).cost == pytest.approx(0.42)

    def test_no_session_log_path_does_not_crash(self, monkeypatch, capsys):
        self._fake_proc(monkeypatch, [json.dumps(RESULT_ENVELOPE) + "\n"])
        assert ai_backend_claude.invoke_fix("p", add_dirs=[]) == 0

    def test_streams_assistant_text_not_raw_json(self, monkeypatch, capsys):
        event = {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "patching the file"}]},
        }
        self._fake_proc(monkeypatch, [json.dumps(event) + "\n"])
        ai_backend_claude.invoke_fix("p", add_dirs=[])
        err = capsys.readouterr().err
        assert "patching the file" in err
        assert '"type"' not in err

    def test_streams_tool_labels(self, monkeypatch, capsys):
        event = {
            "type": "assistant",
            "message": {"content": [
                {"type": "tool_use", "name": "Read", "input": {"file_path": "/a/b/main.go"}},
            ]},
        }
        self._fake_proc(monkeypatch, [json.dumps(event) + "\n"])
        ai_backend_claude.invoke_fix("p", add_dirs=[])
        assert "Read main.go" in capsys.readouterr().err
