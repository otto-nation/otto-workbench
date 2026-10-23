"""Tests for what a usage-ledger row says about a call: where it went, how hard
it thought, how much context it carried, and whether it failed.

Not: token and cost parsing (`ai_usage_test`), the first-turn reading
(`agent_usage_first_turn_test`), the rest of the stats table (`usage_stats_test`).
"""

import json
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import agent.backend
import agent.backend_claude
import agent.backend_pi
import agent.usage
import agent.usage_stats


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(agent.usage, "_warned", False)
    return tmp_path / agent.usage.LEDGER_DIRNAME


def _records(ledger_dir):
    return [json.loads(line)
            for f in sorted(ledger_dir.glob("*.jsonl"))
            for line in f.read_text().splitlines()]


def _write_jsonl(path, *records):
    path.write_text("".join(json.dumps(r) + "\n" for r in records))


def _claude_turn(input_tokens, cache_read=0, cache_write=0, *, msg_id=None, parent=None):
    rec = {"type": "assistant", "message": {"model": "claude-sonnet-5-5", "usage": {
        "input_tokens": input_tokens, "cache_read_input_tokens": cache_read,
        "cache_creation_input_tokens": cache_write,
    }}}
    if msg_id:
        rec["message"]["id"] = msg_id
    if parent:
        rec["parent_tool_use_id"] = parent
    return rec


def _pi_turn(input_tokens, cache_read=0, *, provider="anthropic", thinking="low"):
    return {"type": "message_end", "message": {
        "role": "assistant", "model": "claude-sonnet-5-5",
        "provider": provider, "thinkingLevel": thinking,
        "usage": {"input": input_tokens, "cacheRead": cache_read, "cacheWrite": 0},
    }}


_RESULT = {"type": "result", "total_cost_usd": 0.5, "num_turns": 3,
           "usage": {"input_tokens": 10, "output_tokens": 5}}


def _parse(tmp_path, *records):
    log = tmp_path / "session.jsonl"
    _write_jsonl(log, *records)
    return agent.usage.parse_session_log(str(log))


# ── What the log says ─────────────────────────────────────────────────────────


def test_a_claude_session_log_reports_its_largest_request(tmp_path):
    usage = _parse(tmp_path, _claude_turn(1_000, cache_read=40_000),
                   _claude_turn(2_000, cache_read=150_000, cache_write=8_000),
                   _claude_turn(500, cache_read=60_000), _RESULT)
    assert usage.peak_context_tokens == 160_000


def test_a_pi_session_log_reports_peak_provider_and_thinking(tmp_path):
    usage = _parse(tmp_path, _pi_turn(3_000, cache_read=20_000),
                   _pi_turn(1_000, cache_read=90_000, provider="openai", thinking="high"),
                   _RESULT)
    assert (usage.peak_context_tokens, usage.served_provider, usage.served_thinking) == (
        91_000, "openai", "high")


def test_a_subagent_request_is_not_the_sessions_peak(tmp_path):
    """Task-tool subagents stream their own assistant lines into the parent's log."""
    usage = _parse(tmp_path, _claude_turn(5_000),
                   _claude_turn(400_000, parent="toolu_1"), _RESULT)
    assert usage.peak_context_tokens == 5_000


def test_one_message_streamed_as_several_lines_is_one_request():
    records = [_claude_turn(5_000, msg_id="msg_1"), _claude_turn(5_000, msg_id="msg_1"),
               _claude_turn(6_000, msg_id="msg_2")]
    assert [t.input_tokens for t in agent.usage.served_turns(records)] == [5_000, 6_000]


def test_an_errored_request_with_no_usage_is_not_the_peak(tmp_path):
    assert _parse(tmp_path, _claude_turn(0), _claude_turn(7_000), _RESULT).peak_context_tokens == 7_000


def test_a_log_with_only_a_result_has_no_peak(tmp_path):
    """The `--output-format json` envelope carries no per-request usage."""
    usage = _parse(tmp_path, _RESULT)
    assert usage.peak_context_tokens is None
    assert usage.cost == pytest.approx(0.5)


def test_a_session_that_died_before_its_result_is_unmeasured(tmp_path):
    """Per-request lines alone must not read as a zero-cost measurement."""
    assert _parse(tmp_path, _claude_turn(9_000), _pi_turn(9_000)) == agent.usage.SessionUsage()


def test_merging_keeps_the_peak_and_the_last_served_values():
    merged = agent.usage.merge([
        agent.usage.SessionUsage(cost=1.0, peak_context_tokens=50_000,
                                 served_provider="anthropic", served_thinking="low"),
        agent.usage.SessionUsage(cost=2.0, peak_context_tokens=80_000,
                                 served_provider="openai", served_thinking="high"),
        agent.usage.SessionUsage(cost=0.5),
    ])
    assert (merged.peak_context_tokens, merged.served_provider, merged.served_thinking) == (
        80_000, "openai", "high")


# ── The ledger row ────────────────────────────────────────────────────────────


def test_a_row_carries_what_the_log_says_served_it(ledger):
    agent.usage.record(
        script="s", entry_point="agent", backend="pi", model="m", exit_code=0,
        usage=agent.usage.SessionUsage(cost=1.0, peak_context_tokens=120_000,
                                       served_provider="openai", served_thinking="low"),
        provider="ignored-when-the-log-names-one",
    )
    [rec] = _records(ledger)
    assert (rec["provider"], rec["thinking"], rec["peak_context_tokens"]) == (
        "openai", "low", 120_000)


def test_a_row_falls_back_to_the_backends_provider_and_omits_the_rest(ledger):
    agent.usage.record(
        script="s", entry_point="agent", backend="claude", model="m", exit_code=0,
        usage=agent.usage.SessionUsage(cost=1.0), provider="anthropic",
    )
    [rec] = _records(ledger)
    assert rec["provider"] == "anthropic"
    assert {"thinking", "peak_context_tokens", "measured"}.isdisjoint(rec)


def test_an_unmeasured_failure_row_has_no_cost_to_misread(ledger):
    agent.usage.record(
        script="s", entry_point="agent", backend="claude", model="m", exit_code=1,
        usage=agent.usage.SessionUsage(), measured=False,
    )
    [rec] = _records(ledger)
    assert rec["measured"] is False
    assert {"cost", "input_tokens", "output_tokens"}.isdisjoint(rec)


@pytest.mark.parametrize(("env", "provider"), [
    ({}, "anthropic"),
    ({"CLAUDE_CODE_USE_VERTEX": "1"}, "google-vertex-claude"),
    ({"CLAUDE_CODE_USE_BEDROCK": "1"}, "amazon-bedrock"),
])
def test_claude_names_the_provider_its_cli_is_pointed_at(env, provider, monkeypatch):
    for var in ("CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_BEDROCK"):
        monkeypatch.delenv(var, raising=False)
    for var, value in env.items():
        monkeypatch.setenv(var, value)
    assert agent.backend_claude.ledger_provider() == provider


def test_pi_leaves_the_provider_to_its_log():
    assert agent.backend_pi.ledger_provider() is None


def _backend(monkeypatch, *, exit_code=0, ledger_provider=lambda: "from-backend"):
    backend = types.SimpleNamespace(invoke_agent=lambda inv: exit_code,
                                    ledger_provider=ledger_provider)
    monkeypatch.setattr(agent.backend, "_get_module", lambda: backend)
    return backend


def test_an_agent_call_records_what_its_log_says(ledger, tmp_path, monkeypatch):
    def unused():
        raise AssertionError("the log named the provider; the backend need not be asked")

    _backend(monkeypatch, ledger_provider=unused)
    log = tmp_path / "session.jsonl"
    _write_jsonl(log, _pi_turn(4_000, provider="openai", thinking="high"), _RESULT)

    agent.backend.invoke_agent(agent.backend.AgentInvocation(
        prompt="p", cwd=str(tmp_path), session_log=str(log), phase="scout",
    ))
    [rec] = _records(ledger)
    assert (rec["provider"], rec["thinking"], rec["phase"], rec["peak_context_tokens"]) == (
        "openai", "high", "scout", 4_000)


def test_an_agent_call_asks_the_backend_for_a_provider_the_log_omits(ledger, tmp_path, monkeypatch):
    _backend(monkeypatch)
    log = tmp_path / "session.jsonl"
    _write_jsonl(log, _claude_turn(4_000), _RESULT)
    agent.backend.invoke_agent(agent.backend.AgentInvocation(
        prompt="p", cwd=str(tmp_path), session_log=str(log)))
    [rec] = _records(ledger)
    assert rec["provider"] == "from-backend"


def test_a_failed_call_with_no_result_is_recorded_as_an_unmeasured_failure(
        ledger, tmp_path, monkeypatch):
    """The crash a FAILED count exists for: no result record, so no usage."""
    _backend(monkeypatch, exit_code=1)
    log = tmp_path / "session.jsonl"
    _write_jsonl(log, _claude_turn(4_000))
    agent.backend.invoke_agent(agent.backend.AgentInvocation(
        prompt="p", cwd=str(tmp_path), session_log=str(log), phase="group"))
    [rec] = _records(ledger)
    assert (rec["exit_code"], rec["measured"], rec["phase"]) == (1, False, "group")
    assert "cost" not in rec


def test_a_successful_call_with_no_result_records_nothing(ledger, tmp_path, monkeypatch):
    _backend(monkeypatch)
    log = tmp_path / "session.jsonl"
    _write_jsonl(log, _claude_turn(4_000))
    agent.backend.invoke_agent(agent.backend.AgentInvocation(
        prompt="p", cwd=str(tmp_path), session_log=str(log)))
    assert not ledger.exists() or _records(ledger) == []


def test_a_backend_that_cannot_name_a_provider_still_records_the_call(ledger, tmp_path, monkeypatch):
    def broken():
        raise RuntimeError("cannot say")

    _backend(monkeypatch, ledger_provider=broken)
    log = tmp_path / "session.jsonl"
    _write_jsonl(log, _RESULT)
    agent.backend.invoke_agent(agent.backend.AgentInvocation(
        prompt="p", cwd=str(tmp_path), session_log=str(log)))
    [rec] = _records(ledger)
    assert rec["cost"] == pytest.approx(0.5)
    assert "provider" not in rec


# ── Stats ─────────────────────────────────────────────────────────────────────


def _row(**fields):
    base = {"ts": "2026-10-23T00:00:00Z", "script": "review", "cost": 0.0, "exit_code": 0}
    return {**base, **fields}


def test_stats_count_failed_calls_measured_or_not():
    rows = agent.usage_stats.aggregate_usage(
        [_row(exit_code=1), {"ts": "2026-10-23T00:00:00Z", "script": "review",
                             "exit_code": 1, "measured": False}, _row(cost=2.0)], "script")
    [row] = rows
    assert (row.calls, row.failed, row.cost) == (3, 2, pytest.approx(2.0))


def test_the_total_counts_a_multi_model_failure_once():
    records = [_row(exit_code=1, cost=3.0, cost_by_model={"opus": 2.0, "haiku": 1.0})]
    by_model = agent.usage_stats.aggregate_usage(records, "model")
    total = agent.usage_stats.aggregate_total(records)
    assert [r.failed for r in by_model] == [1, 1]
    assert (total.calls, total.failed, total.cost) == (1, 1, pytest.approx(3.0))


def test_stats_report_the_largest_context_in_a_group():
    rows = agent.usage_stats.aggregate_usage(
        [_row(peak_context_tokens=90_000), _row(peak_context_tokens=210_000), _row()],
        "script")
    assert rows[0].peak_context_tokens == 210_000


def test_stats_have_no_peak_for_records_that_never_measured_one():
    [row] = agent.usage_stats.aggregate_usage([_row()], "script")
    assert row.peak_context_tokens is None


def test_stats_group_by_provider():
    rows = agent.usage_stats.aggregate_usage(
        [_row(provider="anthropic", cost=1.0), _row(provider="openai", cost=3.0), _row()],
        "provider")
    assert [(r.group, r.cost) for r in rows] == [
        ("openai", pytest.approx(3.0)), ("anthropic", pytest.approx(1.0)), ("—", 0.0)]


def test_the_table_shows_failed_and_peak_columns_and_totals():
    records = [_row(exit_code=1, peak_context_tokens=50_000),
               _row(script="describe", peak_context_tokens=150_000)]
    table = agent.usage_stats.format_stats_table(
        agent.usage_stats.aggregate_usage(records, "script"),
        agent.usage_stats.aggregate_total(records), "script")
    header, *body = table.splitlines()
    assert "FAILED" in header and "PEAK CTX" in header
    total = body[-1].split()
    assert (total[0], total[1], total[2]) == ("TOTAL", "2", "1")
    assert "150.0k" in body[-1]
