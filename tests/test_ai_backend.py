"""Tests for ai_backend — dispatch, invocation shape, and usage ledger emission."""

import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import ai_backend
import ai_usage


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    d = tmp_path / "usage"
    monkeypatch.setattr(ai_usage, "LEDGER_DIR", d)
    monkeypatch.setattr(ai_usage, "_warned", False)
    return d


def _records(ledger_dir):
    return [
        json.loads(line)
        for f in sorted(ledger_dir.glob("*.jsonl"))
        for line in f.read_text().splitlines()
    ]


def _session_log(path, *, cost=1.0, input_tokens=100, output_tokens=200,
                 cache_read=5000, cache_write=300, model="claude-sonnet-4-6"):
    Path(path).write_text(json.dumps({
        "type": "result",
        "total_cost_usd": cost,
        "duration_ms": 12000,
        "modelUsage": {
            model: {
                "inputTokens": input_tokens, "outputTokens": output_tokens,
                "cacheReadInputTokens": cache_read, "cacheCreationInputTokens": cache_write,
                "costUSD": cost,
            },
        },
    }) + "\n")


@pytest.fixture
def fake_backend(monkeypatch):
    """Stub backend module recording calls and returning a scripted exit code."""
    calls = []
    mod = types.SimpleNamespace(exit_code=0)

    def invoke_agent(inv):
        calls.append(("invoke_agent", inv))
        return mod.exit_code

    def invoke_fix(inv):
        calls.append(("invoke_fix", inv))
        return mod.exit_code

    def prompt_fn(text, **kwargs):
        calls.append(("prompt", text, kwargs))
        return "response", mod.exit_code

    mod.invoke_agent = invoke_agent
    mod.invoke_fix = invoke_fix
    mod.prompt = prompt_fn
    mod.calls = calls
    monkeypatch.setattr(ai_backend, "_get_module", lambda: mod)
    return mod


class TestBackendSelection:
    def test_defaults_to_claude(self, monkeypatch):
        monkeypatch.delenv("AI_BACKEND", raising=False)
        assert ai_backend._backend() is ai_backend.Backend.CLAUDE

    def test_reads_env(self, monkeypatch):
        monkeypatch.setenv("AI_BACKEND", "pi")
        assert ai_backend._backend() is ai_backend.Backend.PI

    def test_unrecognised_backend_falls_back_to_claude(self, monkeypatch):
        monkeypatch.setenv("AI_BACKEND", "not-a-backend")
        assert ai_backend._backend() is ai_backend.Backend.CLAUDE


class TestPreflightDispatch:
    def test_routes_to_claude_backend(self, monkeypatch):
        monkeypatch.setenv("AI_BACKEND", "claude")
        import ai_backend_claude
        monkeypatch.setattr(ai_backend_claude, "preflight", lambda models, trail: False)
        assert ai_backend.preflight({"claude-sonnet-5": ["group"]}, MagicMock()) is False

    def test_pi_backend_skips_vertex_quota(self, monkeypatch):
        """Regression: Vertex env left exported must not abort a Pi run."""
        monkeypatch.setenv("AI_BACKEND", "pi")
        monkeypatch.setenv("CLAUDE_CODE_USE_VERTEX", "1")
        monkeypatch.setenv("ANTHROPIC_VERTEX_PROJECT_ID", "proj")
        monkeypatch.setenv("CLOUD_ML_REGION", "us-east5")

        import vertex_quota

        def _unreachable(*args, **kwargs):
            pytest.fail("Pi run reached the Vertex quota API")

        monkeypatch.setattr(vertex_quota, "check_quota", _unreachable)
        assert ai_backend.preflight({"claude-sonnet-5": ["group"]}, MagicMock()) is True


class TestAgentInvocation:
    def test_all_backends_accept_the_same_object(self):
        """One object, three modules — a reordered field cannot misbind."""
        import inspect

        import ai_backend
        import ai_backend_claude
        import ai_backend_pi

        for mod in (ai_backend, ai_backend_claude, ai_backend_pi):
            for fn_name in ("invoke_agent", "invoke_fix"):
                params = list(inspect.signature(getattr(mod, fn_name)).parameters)
                assert params == ["inv"], f"{mod.__name__}.{fn_name} takes {params}"

    def test_defaults_leave_every_optional_field_unset(self):
        import ai_backend

        inv = ai_backend.AgentInvocation(prompt="hi")
        assert inv.session_log == ""
        assert inv.add_dirs == []
        assert inv.agent is None
        assert inv.max_turns is None
        assert inv.max_budget is None
        assert inv.model == ""
        assert inv.thinking is None
        assert inv.provider is None
        assert inv.label == ""
        assert inv.task is None
        assert inv.repo is None
        assert inv.pr is None

    def test_is_frozen(self):
        import dataclasses

        import ai_backend
        import pytest

        inv = ai_backend.AgentInvocation(prompt="hi")
        with pytest.raises(dataclasses.FrozenInstanceError):
            inv.prompt = "bye"

    def test_add_dirs_are_not_shared_between_instances(self):
        import ai_backend

        a = ai_backend.AgentInvocation(prompt="a")
        b = ai_backend.AgentInvocation(prompt="b")
        a.add_dirs.append("/tmp")
        assert b.add_dirs == []


class TestInvokeAgentRecords:
    def test_records_usage_from_session_log(self, ledger, fake_backend, tmp_path):
        log = tmp_path / "session.jsonl"
        _session_log(log)
        ai_backend.invoke_agent(ai_backend.AgentInvocation(
            prompt="p", session_log=str(log), model="claude-sonnet-4-6",
        ))
        rec = _records(ledger)[0]
        assert rec["entry_point"] == "agent"
        assert rec["backend"] == "claude"
        assert rec["input_tokens"] == 100
        assert rec["cache_read_tokens"] == 5000
        assert rec["cost"] == pytest.approx(1.0)
        assert rec["exit_code"] == 0

    def test_records_on_nonzero_exit(self, ledger, fake_backend, tmp_path):
        """Failed calls cost money too — an unmeasured failure mode stays invisible."""
        log = tmp_path / "session.jsonl"
        _session_log(log)
        fake_backend.exit_code = 1
        ai_backend.invoke_agent(ai_backend.AgentInvocation(prompt="p", session_log=str(log)))
        rec = _records(ledger)[0]
        assert rec["exit_code"] == 1
        assert rec["cost"] == pytest.approx(1.0)

    def test_records_task_label(self, ledger, fake_backend, tmp_path):
        log = tmp_path / "session.jsonl"
        _session_log(log)
        ai_backend.invoke_agent(ai_backend.AgentInvocation(
            prompt="p", session_log=str(log), task="review-group",
        ))
        assert _records(ledger)[0]["task"] == "review-group"

    def test_backend_receives_the_invocation_unchanged(self, ledger, fake_backend, tmp_path):
        """Ledger labels ride along on the invocation; the backend still sees one object."""
        log = tmp_path / "session.jsonl"
        _session_log(log)
        inv = ai_backend.AgentInvocation(prompt="p", session_log=str(log), task="review-group")
        ai_backend.invoke_agent(inv)
        assert fake_backend.calls == [("invoke_agent", inv)]

    def test_returns_backend_exit_code(self, ledger, fake_backend, tmp_path):
        log = tmp_path / "session.jsonl"
        _session_log(log)
        fake_backend.exit_code = 42
        inv = ai_backend.AgentInvocation(prompt="p", session_log=str(log))
        assert ai_backend.invoke_agent(inv) == 42

    def test_missing_session_log_records_nothing(self, ledger, fake_backend, tmp_path):
        """No usable usage source is better recorded as absent than as zero."""
        ai_backend.invoke_agent(ai_backend.AgentInvocation(
            prompt="p", session_log=str(tmp_path / "never-written.jsonl"),
        ))
        assert _records(ledger) == []


class TestInvokeFixRecords:
    def test_records_when_session_log_written(self, ledger, fake_backend, tmp_path):
        log = tmp_path / "fix.jsonl"
        _session_log(log, cost=0.25, input_tokens=10)
        ai_backend.invoke_fix(ai_backend.AgentInvocation(prompt="p", session_log=str(log)))
        rec = _records(ledger)[0]
        assert rec["entry_point"] == "fix"
        assert rec["cost"] == pytest.approx(0.25)

    def test_no_session_log_records_nothing(self, ledger, fake_backend):
        ai_backend.invoke_fix(ai_backend.AgentInvocation(prompt="p"))
        assert _records(ledger) == []


class TestLedgerFailureIsolation:
    def test_ledger_error_does_not_break_the_call(self, fake_backend, tmp_path, monkeypatch):
        log = tmp_path / "session.jsonl"
        _session_log(log)

        def boom(**kwargs):
            raise RuntimeError("ledger exploded")

        monkeypatch.setattr(ai_usage, "record", boom)
        inv = ai_backend.AgentInvocation(prompt="p", session_log=str(log))
        assert ai_backend.invoke_agent(inv) == 0


class TestScriptName:
    def test_defaults_to_argv0_basename(self, ledger, fake_backend, tmp_path, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["/usr/local/bin/pr-rebase", "--fix"])
        log = tmp_path / "session.jsonl"
        _session_log(log)
        ai_backend.invoke_agent(ai_backend.AgentInvocation(prompt="p", session_log=str(log)))
        assert _records(ledger)[0]["script"] == "pr-rebase"


class TestBuildAddDirs:
    def test_artifact_dir_and_worktree_only(self):
        import review_agent

        dirs = review_agent.build_add_dirs("/wt", "/reviews/pr-42")
        assert dirs == ["/reviews/pr-42", "/wt"]
