"""A phase's thinking level reaching the Claude CLI as `--effort`, and the ledger.

Not: how a phase resolves its level (`workbench_config_phases_test`), the Pi
backend's `--thinking` (`ai_backend_pi_cmd_test`).
"""

import json
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import agent.backend
import agent.backend_claude
import agent.usage
from agent.backend import AgentInvocation
from core.phases import Thinking


def _effort_of(cmd: list[str]) -> str | None:
    return cmd[cmd.index("--effort") + 1] if "--effort" in cmd else None


@pytest.mark.parametrize(("thinking", "expected"), [
    ("low", "low"), ("medium", "medium"), ("high", "high"), ("xhigh", "xhigh"), ("max", "max"),
    # No Claude spelling: the least effort the CLI takes, not its default.
    ("off", "low"), ("minimal", "low"),
    # The CLI ignores an unknown level with a warning; passing it changes nothing.
    ("extreme", None), (None, None), ("", None),
])
def test_each_thinking_level_maps_to_an_effort(thinking, expected):
    assert agent.backend_claude.effort(thinking) == expected


def test_every_thinking_level_has_an_answer():
    for level in Thinking:
        assert agent.backend_claude.effort(level) in agent.backend_claude.EFFORT_LEVELS


@pytest.mark.parametrize("build", [
    agent.backend_claude._build_agent_cmd, agent.backend_claude._build_fix_cmd,
])
def test_the_agent_modes_pass_the_level(build):
    cmd = build(AgentInvocation(prompt="", model="claude-opus-5-5", thinking="high"))
    assert _effort_of(cmd) == "high"


@pytest.mark.parametrize("build", [
    agent.backend_claude._build_agent_cmd, agent.backend_claude._build_fix_cmd,
])
def test_no_level_passes_no_flag(build):
    assert "--effort" not in build(AgentInvocation(prompt=""))


def test_the_prompt_passes_the_level_without_freeing_a_tool():
    # --disallowedTools is variadic, so what follows it must be a flag.
    cmd = agent.backend_claude._build_prompt_cmd(thinking="minimal")
    assert _effort_of(cmd) == "low"
    denied = cmd[cmd.index("--disallowedTools") + 1:cmd.index("--effort")]
    assert denied == list(agent.backend_claude.PROMPT_DENIED_TOOLS)


def test_prompt_hands_its_thinking_to_the_command(monkeypatch, tmp_path):
    seen = {}

    def run(cmd, **_kw):
        seen["cmd"] = cmd
        return types.SimpleNamespace(returncode=0, stdout='{"result": "ok"}', stderr="")

    monkeypatch.setattr(agent.backend_claude.agent.machine_env, "base_env", lambda cwd: {})
    monkeypatch.setattr(agent.backend_claude.subprocess, "run", run)
    agent.backend_claude.prompt("q", cwd=str(tmp_path), thinking="xhigh")
    assert _effort_of(seen["cmd"]) == "xhigh"


# ── The ledger ──────────────────────────────────────────────────────────────


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(agent.usage, "_warned", False)
    return tmp_path / agent.usage.LEDGER_DIRNAME


def _records(ledger: Path) -> list[dict]:
    return [json.loads(line) for f in sorted(ledger.glob("*.jsonl")) for line in f.read_text().splitlines()]


_RESULT = {"type": "result", "total_cost_usd": 0.5, "num_turns": 1,
           "usage": {"input_tokens": 10, "output_tokens": 5}}


def test_a_claude_call_records_the_effort_it_ran_at(ledger, tmp_path, monkeypatch):
    # The stream names no level, so the backend answers with the flag's level.
    backend = types.SimpleNamespace(
        invoke_agent=lambda inv: 0, ledger_provider=lambda: "anthropic",
        ledger_thinking=agent.backend_claude.ledger_thinking,
    )
    monkeypatch.setattr(agent.backend, "_get_module", lambda: backend)
    log = tmp_path / "session.jsonl"
    log.write_text(json.dumps(_RESULT) + "\n")
    agent.backend.invoke_agent(AgentInvocation(
        prompt="p", cwd=str(tmp_path), session_log=str(log), thinking="minimal"))
    [rec] = _records(ledger)
    assert rec["thinking"] == "low"


def test_a_prompt_records_the_effort_it_ran_at(ledger, tmp_path, monkeypatch):
    usage = agent.usage.SessionUsage(cost=0.1, input_tokens=1)
    backend = types.SimpleNamespace(
        prompt=lambda text, **kw: ("ok", 0, usage), ledger_provider=lambda: "anthropic",
        ledger_thinking=agent.backend_claude.ledger_thinking,
    )
    monkeypatch.setattr(agent.backend, "_get_module", lambda: backend)
    agent.backend.prompt("q", cwd=str(tmp_path), thinking="high")
    [rec] = _records(ledger)
    assert rec["thinking"] == "high"


def test_a_level_the_log_names_wins_over_the_backend(ledger, tmp_path, monkeypatch):
    def unused(_thinking):
        raise AssertionError("the log named the level; the backend need not be asked")

    usage = agent.usage.SessionUsage(cost=0.1, input_tokens=1, served_thinking="medium")
    backend = types.SimpleNamespace(
        prompt=lambda text, **kw: ("ok", 0, usage), ledger_provider=lambda: None,
        ledger_thinking=unused,
    )
    monkeypatch.setattr(agent.backend, "_get_module", lambda: backend)
    agent.backend.prompt("q", cwd=str(tmp_path), thinking="high")
    [rec] = _records(ledger)
    assert rec["thinking"] == "medium"
