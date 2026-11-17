"""Tests for `cli.agent_worktree` — the hook-facing CLI that always exits 0."""

import io
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pytest  # noqa: E402

import cli.agent_worktree  # noqa: E402
import pr.agent_worktree  # noqa: E402
from pr.agent_worktree import ReportOutcome, ToolCall  # noqa: E402


@pytest.fixture
def calls(monkeypatch):
    """Replace report/clear with recorders; the list holds (name, args) per call."""
    seen: list[tuple[str, tuple]] = []

    def fake_report(call, env):
        seen.append(("report", (call, env)))
        return ReportOutcome.LABELLED

    def fake_clear(env):
        seen.append(("clear", (env,)))

    monkeypatch.setattr(pr.agent_worktree, "report", fake_report)
    monkeypatch.setattr(pr.agent_worktree, "clear", fake_clear)
    monkeypatch.setenv("HC_TERMINAL_ID", "t-1")
    return seen


def _stdin(monkeypatch, text: str) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(text))


def test_report_reads_the_payload_and_returns_zero(calls, monkeypatch):
    _stdin(monkeypatch, '{"tool_name":"Edit","tool_input":{"file_path":"/x/y"},"cwd":"/x"}')

    assert cli.agent_worktree.main(["report"]) == 0

    assert [name for name, _ in calls] == ["report"]
    call, env = calls[0][1]
    assert isinstance(call, ToolCall)
    assert call.tool_name == "Edit"
    assert call.file_path == "/x/y"
    assert env["HC_TERMINAL_ID"] == "t-1"


def test_clear_returns_zero_and_reads_no_stdin(calls, monkeypatch):
    _stdin(monkeypatch, "not json")

    assert cli.agent_worktree.main(["clear"]) == 0

    assert [name for name, _ in calls] == ["clear"]
    assert calls[0][1][0]["HC_TERMINAL_ID"] == "t-1"


def test_malformed_json_returns_zero_without_reporting(calls, monkeypatch):
    _stdin(monkeypatch, '{"tool_name": ')

    assert cli.agent_worktree.main(["report"]) == 0

    assert calls == []


def test_an_exception_in_report_is_a_silent_zero(monkeypatch, capsys):
    def boom(call, env):
        raise RuntimeError("boom")

    monkeypatch.setattr(pr.agent_worktree, "report", boom)
    _stdin(monkeypatch, "{}")

    assert cli.agent_worktree.main(["report"]) == 0

    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("argv", [[], ["bogus"]])
def test_a_usage_error_returns_zero_without_acting(calls, argv):
    assert cli.agent_worktree.main(argv) == 0

    assert calls == []
