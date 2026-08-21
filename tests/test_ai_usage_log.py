"""Tests for ai-usage-log, the shell bridge into the global usage ledger.

run-auto-task and the Taskfile's AI_COMMAND cannot route through ai_backend, so this
tool is the only thing standing between those calls and an unmeasured pipeline.
"""

import importlib.machinery
import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "ai" / "lib"))

import ai_usage

BIN = REPO_ROOT / "ai" / "claude" / "bin" / "ai-usage-log"


@pytest.fixture(scope="session")
def aul():
    loader = importlib.machinery.SourceFileLoader("ai_usage_log", str(BIN))
    spec = importlib.util.spec_from_loader("ai_usage_log", loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ai_usage_log"] = mod
    spec.loader.exec_module(mod)
    yield mod
    del sys.modules["ai_usage_log"]


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


def _run(aul, argv, stdin="", monkeypatch=None):
    monkeypatch.setattr(sys, "argv", ["ai-usage-log", *argv])
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    return aul.main()


class TestRender:
    def test_renders_assistant_prose(self, aul, monkeypatch, capsys):
        _run(aul, ["render"], json.dumps(ASSISTANT_TEXT) + "\n", monkeypatch)
        assert capsys.readouterr().out.strip() == "working on it"

    def test_passes_non_json_lines_through(self, aul, monkeypatch, capsys):
        """stderr is merged into this stream; dropping it hides failures."""
        _run(aul, ["render"], "npm ERR! something broke\n", monkeypatch)
        assert "npm ERR! something broke" in capsys.readouterr().out

    def test_tees_raw_stream(self, aul, monkeypatch, tmp_path, capsys):
        raw = tmp_path / "raw.jsonl"
        stdin = json.dumps(ASSISTANT_TEXT) + "\n" + json.dumps(RESULT_RECORD) + "\n"
        _run(aul, ["render", "--tee", str(raw)], stdin, monkeypatch)
        assert ai_usage.parse_session_log(str(raw)).cost == pytest.approx(0.5)

    def test_result_records_are_not_displayed(self, aul, monkeypatch, capsys):
        _run(aul, ["render"], json.dumps(RESULT_RECORD) + "\n", monkeypatch)
        assert capsys.readouterr().out == ""


class TestUnwrap:
    def test_extracts_reply_from_envelope(self, aul, monkeypatch, capsys):
        _run(aul, ["unwrap"], json.dumps(RESULT_RECORD), monkeypatch)
        assert capsys.readouterr().out == "the reply"

    def test_passes_prose_through_unchanged(self, aul, monkeypatch, capsys):
        """A non-Claude AI_COMMAND emits prose, not an envelope."""
        _run(aul, ["unwrap"], "just prose\nline two\n", monkeypatch)
        assert capsys.readouterr().out == "just prose\nline two\n"

    def test_tees_raw_response(self, aul, monkeypatch, tmp_path, capsys):
        raw = tmp_path / "raw.json"
        _run(aul, ["unwrap", "--tee", str(raw)], json.dumps(RESULT_RECORD), monkeypatch)
        assert json.loads(raw.read_text())["total_cost_usd"] == pytest.approx(0.5)


class TestRecord:
    @pytest.fixture
    def ledger(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path))
        return tmp_path / ai_usage.LEDGER_DIRNAME

    def _record(self, aul, monkeypatch, path, extra=()):
        argv = [
            "record", "--from-log", str(path), "--script", "run-auto-task",
            "--entry-point", "agent", "--task", "dream", *extra,
        ]
        return _run(aul, argv, "", monkeypatch)

    def _only(self, ledger):
        return json.loads(next(ledger.glob("*.jsonl")).read_text().strip())

    def test_records_from_jsonl_stream(self, aul, monkeypatch, tmp_path, ledger):
        raw = tmp_path / "raw.jsonl"
        raw.write_text(json.dumps(RESULT_RECORD) + "\n")
        self._record(aul, monkeypatch, raw)
        rec = self._only(ledger)
        assert rec["script"] == "run-auto-task"
        assert rec["task"] == "dream"
        assert rec["cost"] == pytest.approx(0.5)
        assert rec["cache_read_tokens"] == 300

    def test_records_from_single_envelope(self, aul, monkeypatch, tmp_path, ledger):
        """--output-format json writes one envelope with no trailing newline."""
        raw = tmp_path / "raw.json"
        raw.write_text(json.dumps(RESULT_RECORD))
        self._record(aul, monkeypatch, raw)
        assert self._only(ledger)["cost"] == pytest.approx(0.5)

    def test_prose_response_records_nothing(self, aul, monkeypatch, tmp_path, ledger):
        """A pluggable non-Claude binary reports no usage; a zero row would lie."""
        raw = tmp_path / "raw.txt"
        raw.write_text("just prose\n")
        self._record(aul, monkeypatch, raw)
        assert list(ledger.glob("*.jsonl")) == []

    def test_missing_file_records_nothing(self, aul, monkeypatch, tmp_path, ledger):
        self._record(aul, monkeypatch, tmp_path / "absent.jsonl")
        assert list(ledger.glob("*.jsonl")) == []

    def test_carries_repo_and_pr(self, aul, monkeypatch, tmp_path, ledger):
        raw = tmp_path / "raw.jsonl"
        raw.write_text(json.dumps(RESULT_RECORD) + "\n")
        self._record(aul, monkeypatch, raw, extra=["--repo", "o/r", "--pr", "42"])
        rec = self._only(ledger)
        assert (rec["repo"], rec["pr"]) == ("o/r", "42")
