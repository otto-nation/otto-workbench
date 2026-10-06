"""Tests for ai-usage-log, the shell bridge into the global usage ledger.

run-auto-task cannot route through ai_backend, so this
tool is the only thing standing between those calls and an unmeasured pipeline.
"""

import io
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "ai" / "lib"))

import agent.usage  # noqa: E402
import cli.ai_usage_log  # noqa: E402

RESULT_RECORD = {
    "type": "result",
    "result": "the reply",
    "total_cost_usd": 0.5,
    "modelUsage": {
        "claude-opus-5": {
            "inputTokens": 10, "outputTokens": 20,
            "cacheReadInputTokens": 300, "costUSD": 0.5,
        },
    },
}

ASSISTANT_TEXT = {
    "type": "assistant",
    "message": {"content": [{"type": "text", "text": "working on it"}]},
}


def _run(argv, stdin="", monkeypatch=None):
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    return cli.ai_usage_log.main(list(argv))


class TestRender:
    def test_renders_assistant_prose(self, monkeypatch, capsys):
        _run(["render"], json.dumps(ASSISTANT_TEXT) + "\n", monkeypatch)
        assert capsys.readouterr().out.strip() == "working on it"

    def test_passes_non_json_lines_through(self, monkeypatch, capsys):
        """stderr is merged into this stream; dropping it hides failures."""
        _run(["render"], "npm ERR! something broke\n", monkeypatch)
        assert "npm ERR! something broke" in capsys.readouterr().out

    def test_tees_raw_stream(self, monkeypatch, tmp_path, capsys):
        raw = tmp_path / "raw.jsonl"
        stdin = json.dumps(ASSISTANT_TEXT) + "\n" + json.dumps(RESULT_RECORD) + "\n"
        _run(["render", "--tee", str(raw)], stdin, monkeypatch)
        assert agent.usage.parse_session_log(str(raw)).cost == pytest.approx(0.5)

    def test_result_records_are_not_displayed(self, monkeypatch, capsys):
        _run(["render"], json.dumps(RESULT_RECORD) + "\n", monkeypatch)
        assert capsys.readouterr().out == ""


class TestUnwrap:
    def test_extracts_reply_from_envelope(self, monkeypatch, capsys):
        _run(["unwrap"], json.dumps(RESULT_RECORD), monkeypatch)
        assert capsys.readouterr().out == "the reply"

    def test_passes_prose_through_unchanged(self, monkeypatch, capsys):
        """A reply with no JSON envelope passes through intact."""
        _run(["unwrap"], "just prose\nline two\n", monkeypatch)
        assert capsys.readouterr().out == "just prose\nline two\n"

    def test_tees_raw_response(self, monkeypatch, tmp_path, capsys):
        raw = tmp_path / "raw.json"
        _run(["unwrap", "--tee", str(raw)], json.dumps(RESULT_RECORD), monkeypatch)
        assert json.loads(raw.read_text())["total_cost_usd"] == pytest.approx(0.5)


class TestRecord:
    @pytest.fixture
    def ledger(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path))
        return tmp_path / agent.usage.LEDGER_DIRNAME

    def _record(self, monkeypatch, path, extra=()):
        argv = [
            "record", "--from-log", str(path), "--script", "run-auto-task",
            "--entry-point", "agent", "--task", "dream", *extra,
        ]
        return _run(argv, "", monkeypatch)

    def _only(self, ledger):
        return json.loads(next(ledger.glob("*.jsonl")).read_text().strip())

    def test_records_from_jsonl_stream(self, monkeypatch, tmp_path, ledger):
        raw = tmp_path / "raw.jsonl"
        raw.write_text(json.dumps(RESULT_RECORD) + "\n")
        self._record(monkeypatch, raw)
        rec = self._only(ledger)
        assert rec["script"] == "run-auto-task"
        assert rec["task"] == "dream"
        assert rec["cost"] == pytest.approx(0.5)
        assert rec["cache_read_tokens"] == 300

    def test_records_from_single_envelope(self, monkeypatch, tmp_path, ledger):
        """--output-format json writes one envelope with no trailing newline."""
        raw = tmp_path / "raw.json"
        raw.write_text(json.dumps(RESULT_RECORD))
        self._record(monkeypatch, raw)
        assert self._only(ledger)["cost"] == pytest.approx(0.5)

    def test_records_from_an_envelope_spread_over_lines(self, monkeypatch, tmp_path, ledger):
        """No single line is a record, so only the whole-file envelope read finds it."""
        raw = tmp_path / "raw.json"
        raw.write_text(json.dumps(RESULT_RECORD, indent=2))
        self._record(monkeypatch, raw)
        assert self._only(ledger)["cost"] == pytest.approx(0.5)

    def test_prose_response_records_nothing(self, monkeypatch, tmp_path, ledger):
        """A response with no usage record (plain prose) records nothing; a zero row would lie."""
        raw = tmp_path / "raw.txt"
        raw.write_text("just prose\n")
        self._record(monkeypatch, raw)
        assert list(ledger.glob("*.jsonl")) == []

    def test_missing_file_records_nothing(self, monkeypatch, tmp_path, ledger):
        self._record(monkeypatch, tmp_path / "absent.jsonl")
        assert list(ledger.glob("*.jsonl")) == []

    def test_carries_repo_and_pr(self, monkeypatch, tmp_path, ledger):
        raw = tmp_path / "raw.jsonl"
        raw.write_text(json.dumps(RESULT_RECORD) + "\n")
        self._record(monkeypatch, raw, extra=["--repo", "o/r", "--pr", "42"])
        rec = self._only(ledger)
        assert (rec["repo"], rec["pr"]) == ("o/r", "42")
