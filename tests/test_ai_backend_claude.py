import json
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import ai_backend_claude


class TestLoadAgentDef:
    def test_returns_none_for_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ai_backend_claude, "_AGENTS_DIR", tmp_path)
        assert ai_backend_claude._load_agent_def("nonexistent") is None

    def test_parses_frontmatter(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ai_backend_claude, "_AGENTS_DIR", tmp_path)
        (tmp_path / "my-agent.md").write_text(
            "---\nname: my-agent\ndescription: A test agent\n---\n\nYou are helpful."
        )
        result = ai_backend_claude._load_agent_def("my-agent")
        assert result == {"description": "A test agent", "prompt": "You are helpful."}

    def test_no_frontmatter(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ai_backend_claude, "_AGENTS_DIR", tmp_path)
        (tmp_path / "plain.md").write_text("Just a prompt with no frontmatter.")
        result = ai_backend_claude._load_agent_def("plain")
        assert result == {"description": "plain", "prompt": "Just a prompt with no frontmatter."}

    def test_description_with_extra_fields(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ai_backend_claude, "_AGENTS_DIR", tmp_path)
        (tmp_path / "full.md").write_text(
            "---\nname: full\ndescription: Full agent\nmodel: inherit\nsource: test\n---\n\nBody here."
        )
        result = ai_backend_claude._load_agent_def("full")
        assert result["description"] == "Full agent"
        assert result["prompt"] == "Body here."


class TestBuildAgentCmd:
    def test_base_flags(self):
        cmd = ai_backend_claude._build_agent_cmd(ai_backend_claude.AgentInvocation(prompt=""))
        assert "--bare" in cmd
        assert "--output-format" in cmd
        assert "stream-json" in cmd

    def test_builtin_agent_no_agents_json(self):
        cmd = ai_backend_claude._build_agent_cmd(
            ai_backend_claude.AgentInvocation(prompt="", agent="Explore"),
        )
        assert "--agent" in cmd
        idx = cmd.index("--agent")
        assert cmd[idx + 1] == "Explore"
        assert "--agents" not in cmd

    def test_custom_agent_injects_agents_json(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ai_backend_claude, "_AGENTS_DIR", tmp_path)
        (tmp_path / "reviewer-lite.md").write_text(
            "---\nname: reviewer-lite\ndescription: Lightweight reviewer\n---\n\nReview code."
        )
        cmd = ai_backend_claude._build_agent_cmd(
            ai_backend_claude.AgentInvocation(prompt="", agent="reviewer-lite"),
        )
        assert "--agents" in cmd
        agents_idx = cmd.index("--agents")
        agents_json = json.loads(cmd[agents_idx + 1])
        assert "reviewer-lite" in agents_json
        assert agents_json["reviewer-lite"]["description"] == "Lightweight reviewer"
        assert agents_json["reviewer-lite"]["prompt"] == "Review code."
        assert "--agent" in cmd
        agent_idx = cmd.index("--agent")
        assert cmd[agent_idx + 1] == "reviewer-lite"

    def test_agents_json_before_agent_flag(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ai_backend_claude, "_AGENTS_DIR", tmp_path)
        (tmp_path / "test.md").write_text("---\nname: test\ndescription: Test\n---\n\nPrompt.")
        cmd = ai_backend_claude._build_agent_cmd(
            ai_backend_claude.AgentInvocation(prompt="", agent="test"),
        )
        assert cmd.index("--agents") < cmd.index("--agent")

    def test_model_and_max_turns(self):
        cmd = ai_backend_claude._build_agent_cmd(ai_backend_claude.AgentInvocation(
            prompt="", model="sonnet", max_turns=15, max_budget=5.0,
        ))
        assert "--model" in cmd
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "sonnet"
        assert "--max-turns" in cmd
        assert "--max-budget-usd" in cmd

    def test_add_dirs(self):
        cmd = ai_backend_claude._build_agent_cmd(
            ai_backend_claude.AgentInvocation(prompt="", add_dirs=["/a", "/b"]),
        )
        pairs = [(cmd[i], cmd[i + 1]) for i in range(len(cmd) - 1) if cmd[i] == "--add-dir"]
        assert pairs == [("--add-dir", "/a"), ("--add-dir", "/b")]


class TestPromptStderr:
    def test_stderr_logged_on_failure(self, monkeypatch, capsys, tmp_path):
        fake_result = types.SimpleNamespace(stdout="", returncode=1, stderr="API rate limit exceeded")
        monkeypatch.setattr(
            ai_backend_claude.subprocess, "run",
            lambda *a, **kw: fake_result,
        )
        stdout, rc, _ = ai_backend_claude.prompt("test prompt", cwd=str(tmp_path))
        assert rc == 1
        captured = capsys.readouterr()
        assert "API rate limit exceeded" in captured.err

    def test_stdout_logged_when_stderr_is_empty(self, monkeypatch, capsys, tmp_path):
        """A failure reported on stdout is still reported.

        The CLI puts some refusals there with an empty stderr, and a caller that
        only prints a bare exit code leaves nothing to diagnose.
        """
        fake_result = types.SimpleNamespace(
            stdout="Claude usage limit reached", returncode=1, stderr="",
        )
        monkeypatch.setattr(
            ai_backend_claude.subprocess, "run",
            lambda *a, **kw: fake_result,
        )
        _, rc, _ = ai_backend_claude.prompt("test prompt", cwd=str(tmp_path))
        assert rc == 1
        assert "Claude usage limit reached" in capsys.readouterr().err

    def test_whitespace_stderr_does_not_hide_stdout(self, monkeypatch, capsys, tmp_path):
        fake_result = types.SimpleNamespace(
            stdout="Claude usage limit reached", returncode=1, stderr="\n",
        )
        monkeypatch.setattr(
            ai_backend_claude.subprocess, "run",
            lambda *a, **kw: fake_result,
        )
        ai_backend_claude.prompt("test prompt", cwd=str(tmp_path))
        assert "Claude usage limit reached" in capsys.readouterr().err

    def test_silent_failure_logs_the_exit_code(self, monkeypatch, capsys, tmp_path):
        fake_result = types.SimpleNamespace(stdout="", returncode=143, stderr="")
        monkeypatch.setattr(
            ai_backend_claude.subprocess, "run",
            lambda *a, **kw: fake_result,
        )
        _, rc, _ = ai_backend_claude.prompt("test prompt", cwd=str(tmp_path))
        assert rc == 143
        assert "143" in capsys.readouterr().err

    def test_failure_detail_is_truncated(self, monkeypatch, capsys, tmp_path):
        fake_result = types.SimpleNamespace(stdout="", returncode=1, stderr="x" * 10000)
        monkeypatch.setattr(
            ai_backend_claude.subprocess, "run",
            lambda *a, **kw: fake_result,
        )
        ai_backend_claude.prompt("test prompt", cwd=str(tmp_path))
        err = capsys.readouterr().err
        assert err.count("x") == ai_backend_claude._FAILURE_DETAIL_MAX_CHARS

    def test_stderr_not_logged_on_success(self, monkeypatch, capsys, tmp_path):
        fake_result = type("R", (), {"stdout": "response", "returncode": 0, "stderr": ""})()
        monkeypatch.setattr(
            ai_backend_claude.subprocess, "run",
            lambda *a, **kw: fake_result,
        )
        stdout, rc, _ = ai_backend_claude.prompt("test prompt", cwd=str(tmp_path))
        assert rc == 0
        assert stdout == "response"
        captured = capsys.readouterr()
        assert captured.err == ""


class TestPreflight:
    def test_delegates_to_vertex_quota(self, monkeypatch):
        seen = {}

        def fake_run(models, trail):
            seen["models"] = models
            return False

        monkeypatch.setattr(ai_backend_claude.vertex_quota, "run_preflight", fake_run)
        models = {"claude-sonnet-5": ["group"]}
        assert ai_backend_claude.preflight(models, object()) is False
        assert seen["models"] == models
