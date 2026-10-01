import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import agent.backend_claude
from core.phases import Phase
from test_ai_backend import _recording_popen
import agent.vertex_quota

_FIX_PHASE = Phase.FIX


class TestLoadAgentDef:
    def test_returns_none_for_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(agent.backend_claude, "_AGENTS_DIR", tmp_path)
        assert agent.backend_claude._load_agent_def("nonexistent") is None

    def test_parses_frontmatter(self, tmp_path, monkeypatch):
        monkeypatch.setattr(agent.backend_claude, "_AGENTS_DIR", tmp_path)
        (tmp_path / "my-agent.md").write_text(
            "---\nname: my-agent\ndescription: A test agent\n---\n\nYou are helpful."
        )
        result = agent.backend_claude._load_agent_def("my-agent")
        assert result == {"description": "A test agent", "prompt": "You are helpful."}

    def test_no_frontmatter(self, tmp_path, monkeypatch):
        monkeypatch.setattr(agent.backend_claude, "_AGENTS_DIR", tmp_path)
        (tmp_path / "plain.md").write_text("Just a prompt with no frontmatter.")
        result = agent.backend_claude._load_agent_def("plain")
        assert result == {"description": "plain", "prompt": "Just a prompt with no frontmatter."}

    def test_description_with_extra_fields(self, tmp_path, monkeypatch):
        monkeypatch.setattr(agent.backend_claude, "_AGENTS_DIR", tmp_path)
        (tmp_path / "full.md").write_text(
            "---\nname: full\ndescription: Full agent\nmodel: inherit\nsource: test\n---\n\nBody here."
        )
        result = agent.backend_claude._load_agent_def("full")
        assert result["description"] == "Full agent"
        assert result["prompt"] == "Body here."


class TestBuildAgentCmd:
    def test_base_flags(self):
        cmd = agent.backend_claude._build_agent_cmd(agent.backend_claude.AgentInvocation(prompt=""))
        assert "--bare" in cmd
        assert "--output-format" in cmd
        assert "stream-json" in cmd

    def test_builtin_agent_no_agents_json(self):
        cmd = agent.backend_claude._build_agent_cmd(
            agent.backend_claude.AgentInvocation(prompt="", agent="Explore"),
        )
        assert "--agent" in cmd
        idx = cmd.index("--agent")
        assert cmd[idx + 1] == "Explore"
        assert "--agents" not in cmd

    def test_custom_agent_injects_agents_json(self, tmp_path, monkeypatch):
        monkeypatch.setattr(agent.backend_claude, "_AGENTS_DIR", tmp_path)
        (tmp_path / "reviewer-lite.md").write_text(
            "---\nname: reviewer-lite\ndescription: Lightweight reviewer\n---\n\nReview code."
        )
        cmd = agent.backend_claude._build_agent_cmd(
            agent.backend_claude.AgentInvocation(prompt="", agent="reviewer-lite"),
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
        monkeypatch.setattr(agent.backend_claude, "_AGENTS_DIR", tmp_path)
        (tmp_path / "test.md").write_text("---\nname: test\ndescription: Test\n---\n\nPrompt.")
        cmd = agent.backend_claude._build_agent_cmd(
            agent.backend_claude.AgentInvocation(prompt="", agent="test"),
        )
        assert cmd.index("--agents") < cmd.index("--agent")

    def test_model_and_max_turns(self):
        cmd = agent.backend_claude._build_agent_cmd(agent.backend_claude.AgentInvocation(
            prompt="", model="sonnet", max_turns=15, max_budget=5.0,
        ))
        assert "--model" in cmd
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "sonnet"
        assert "--max-turns" in cmd
        assert "--max-budget-usd" in cmd

    def test_add_dirs(self):
        cmd = agent.backend_claude._build_agent_cmd(
            agent.backend_claude.AgentInvocation(prompt="", add_dirs=["/a", "/b"]),
        )
        pairs = [(cmd[i], cmd[i + 1]) for i in range(len(cmd) - 1) if cmd[i] == "--add-dir"]
        assert pairs == [("--add-dir", "/a"), ("--add-dir", "/b")]


class TestPromptStderr:
    def test_stderr_logged_on_failure(self, monkeypatch, capsys, tmp_path):
        fake_result = types.SimpleNamespace(stdout="", returncode=1, stderr="API rate limit exceeded")
        monkeypatch.setattr(
            agent.backend_claude.subprocess, "run",
            lambda *a, **kw: fake_result,
        )
        stdout, rc, _ = agent.backend_claude.prompt("test prompt", cwd=str(tmp_path))
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
            agent.backend_claude.subprocess, "run",
            lambda *a, **kw: fake_result,
        )
        _, rc, _ = agent.backend_claude.prompt("test prompt", cwd=str(tmp_path))
        assert rc == 1
        assert "Claude usage limit reached" in capsys.readouterr().err

    def test_whitespace_stderr_does_not_hide_stdout(self, monkeypatch, capsys, tmp_path):
        fake_result = types.SimpleNamespace(
            stdout="Claude usage limit reached", returncode=1, stderr="\n",
        )
        monkeypatch.setattr(
            agent.backend_claude.subprocess, "run",
            lambda *a, **kw: fake_result,
        )
        agent.backend_claude.prompt("test prompt", cwd=str(tmp_path))
        assert "Claude usage limit reached" in capsys.readouterr().err

    def test_silent_failure_logs_the_exit_code(self, monkeypatch, capsys, tmp_path):
        fake_result = types.SimpleNamespace(stdout="", returncode=143, stderr="")
        monkeypatch.setattr(
            agent.backend_claude.subprocess, "run",
            lambda *a, **kw: fake_result,
        )
        _, rc, _ = agent.backend_claude.prompt("test prompt", cwd=str(tmp_path))
        assert rc == 143
        assert "143" in capsys.readouterr().err

    def test_failure_detail_is_truncated(self, monkeypatch, capsys, tmp_path):
        fake_result = types.SimpleNamespace(stdout="", returncode=1, stderr="x" * 10000)
        monkeypatch.setattr(
            agent.backend_claude.subprocess, "run",
            lambda *a, **kw: fake_result,
        )
        agent.backend_claude.prompt("test prompt", cwd=str(tmp_path))
        err = capsys.readouterr().err
        assert err.count("x") == agent.backend_claude._FAILURE_DETAIL_MAX_CHARS

    def test_stderr_not_logged_on_success(self, monkeypatch, capsys, tmp_path):
        fake_result = type("R", (), {"stdout": "response", "returncode": 0, "stderr": ""})()
        monkeypatch.setattr(
            agent.backend_claude.subprocess, "run",
            lambda *a, **kw: fake_result,
        )
        stdout, rc, _ = agent.backend_claude.prompt("test prompt", cwd=str(tmp_path))
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

        monkeypatch.setattr(agent.vertex_quota, "run_preflight", fake_run)
        models = {"claude-sonnet-5": ["group"]}
        assert agent.backend_claude.preflight(models, object()) is False
        assert seen["models"] == models


class TestEveryCommandCarriesAnAddDir:
    """`--add-dir` is what puts the operator's rules back under `--bare`.

    `--bare` skips CLAUDE.md auto-discovery; passing any `--add-dir` restores
    memory loading wholesale, not just access to the named directory. Verified
    against Claude Code 2.1.265 by running the fix flag set with tools
    suppressed: with `--add-dir` the agent quotes the workbench rules back, and
    without it the same prompt answers that it has no such instructions.

    So a command built without one loses every coding rule the agent runs
    under, with no error and no missing file to notice. Nothing in the CLI's
    documented contract promises this coupling, which is exactly why it is
    pinned here — if a Claude upgrade breaks it, this test is where the
    assumption is written down, even though it cannot itself detect that.
    """

    def _fix_cmd(self, **kwargs):
        return agent.backend_claude._build_fix_cmd(
            agent.backend_claude.AgentInvocation(prompt="", **kwargs),
        )

    def test_a_fix_command_passes_every_directory_it_was_given(self):
        cmd = self._fix_cmd(add_dirs=["/tmp/wt", "/tmp/artifacts"])
        assert cmd.count("--add-dir") == 2
        for d in ("/tmp/wt", "/tmp/artifacts"):
            assert cmd[cmd.index(d) - 1] == "--add-dir"

    def test_an_agent_command_passes_every_directory_it_was_given(self):
        cmd = agent.backend_claude._build_agent_cmd(
            agent.backend_claude.AgentInvocation(prompt="", add_dirs=["/tmp/wt"]),
        )
        assert cmd.count("--add-dir") == 1
        assert cmd[cmd.index("/tmp/wt") - 1] == "--add-dir"

    def test_the_fix_runner_never_hands_the_backend_an_empty_list(self):
        """The invariant lives in `run_fix`, which falls back to cwd.

        A caller passing no directories is the case that would silently drop
        the rules, so the fallback is the thing worth pinning rather than the
        command builder's faithful rendering of whatever it is handed.
        """
        import agent.invoke

        captured = {}

        def fake_invoke_fix(inv):
            captured["add_dirs"] = inv.add_dirs
            return 0

        import agent.backend as ai_backend
        original = ai_backend.invoke_fix
        ai_backend.invoke_fix = fake_invoke_fix
        try:
            agent.invoke.run_fix(
                _FIX_PHASE, "prompt", cwd="/tmp/the-worktree",
                session_log="", produced=lambda: True,
            )
        finally:
            ai_backend.invoke_fix = original

        assert captured["add_dirs"] == ["/tmp/the-worktree"]


class TestThePromptShapeGrantsNoTools:
    """A stateless prompt must not be able to run a tool, on either backend.

    The Pi backend states this with `--no-tools`, after one of its prompt calls
    ran `git rebase --edit-todo` and blocked on `vi`. This backend needs its own
    statement, because the obvious reading of the code is wrong: `claude -p
    --bare` carries neither `--permission-mode acceptEdits` nor an allowlist,
    and a tool call is *not* refused by the default mode.

    Verified against Claude Code 2.1.265. Asked to run `echo`, the bare command
    ran it and recorded nothing in `permission_denials`; asked to name its
    tools it answered "Bash\nEdit\nRead". With `--disallowedTools Bash Edit
    Write` the same question answers "Read" alone. So this path had the same
    hole the Pi one did, and the denylist is what closes it.
    """

    def _prompt_cmd(self, **kwargs):
        return agent.backend_claude._build_prompt_cmd(**kwargs)

    def test_the_executing_tools_are_denied(self):
        cmd = self._prompt_cmd()
        denied = cmd[cmd.index("--disallowedTools") + 1:]
        for tool in agent.backend_claude.PROMPT_DENIED_TOOLS:
            assert tool in denied

    def test_bash_is_among_them(self):
        """The one that hung a rebase — named rather than left to the list."""
        assert "Bash" in agent.backend_claude.PROMPT_DENIED_TOOLS

    def test_the_denial_survives_a_model(self):
        """`--model` takes a value, so it must not be parsed as another tool."""
        cmd = self._prompt_cmd(model="claude-opus-4-6")
        assert cmd[cmd.index("--model") + 1] == "claude-opus-4-6"
        denied = cmd[cmd.index("--disallowedTools") + 1:cmd.index("--model")]
        assert list(agent.backend_claude.PROMPT_DENIED_TOOLS) == denied

    def test_the_agent_modes_keep_their_tools(self):
        """The contrast is the point — an agent with no tools does nothing."""
        inv = agent.backend_claude.AgentInvocation(prompt="", add_dirs=["/tmp/wt"])
        for cmd in (
            agent.backend_claude._build_agent_cmd(inv),
            agent.backend_claude._build_fix_cmd(inv),
        ):
            assert "--allowedTools" in cmd
            assert cmd[cmd.index("--permission-mode") + 1] == "acceptEdits"


class TestRulesHome:
    """``rules_home`` maps to ``CLAUDE_CONFIG_DIR`` at spawn, not at the eval layer."""

    @pytest.mark.parametrize("entry_point", ["invoke_agent", "invoke_fix"])
    def test_rules_home_becomes_claude_config_dir_in_the_spawned_env(
            self, monkeypatch, tmp_path, entry_point):
        home = tmp_path / "cc-trimmed"
        home.mkdir()
        seen = {}
        monkeypatch.setattr(subprocess, "Popen", _recording_popen(seen))
        getattr(agent.backend_claude, entry_point)(
            agent.backend_claude.AgentInvocation(
                prompt="p", cwd=str(tmp_path),
                session_log=str(tmp_path / "s.jsonl"),
                add_dirs=[str(tmp_path)],
                rules_home=str(home),
            ),
        )
        assert seen["env"]["CLAUDE_CONFIG_DIR"] == str(home)
        assert Path(seen["env"]["CLAUDE_CONFIG_DIR"]).is_absolute()
        assert "PATH" in seen["env"]
        assert "HOME" in seen["env"]
        assert seen["env"]["HOME"] == os.environ["HOME"]

    @pytest.mark.parametrize("entry_point", ["invoke_agent", "invoke_fix"])
    def test_empty_rules_home_does_not_inject_claude_config_dir(
            self, monkeypatch, tmp_path, entry_point):
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
        seen = {}
        monkeypatch.setattr(subprocess, "Popen", _recording_popen(seen))
        getattr(agent.backend_claude, entry_point)(
            agent.backend_claude.AgentInvocation(
                prompt="p", cwd=str(tmp_path),
                session_log=str(tmp_path / "s.jsonl"),
            ),
        )
        assert "CLAUDE_CONFIG_DIR" not in seen["env"]

    def test_a_relative_rules_home_is_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="must be absolute"):
            agent.backend_claude._spawn_env(
                agent.backend_claude.AgentInvocation(
                    prompt="p", cwd=str(tmp_path), rules_home="relative/cc",
                ),
            )

    def test_add_dir_is_unchanged_when_rules_home_is_set(self):
        inv = agent.backend_claude.AgentInvocation(
            prompt="", add_dirs=["/tmp/wt"], rules_home="/tmp/cc",
        )
        for builder in (
            agent.backend_claude._build_fix_cmd,
            agent.backend_claude._build_agent_cmd,
        ):
            cmd = builder(inv)
            assert cmd.count("--add-dir") == 1
            assert cmd[cmd.index("/tmp/wt") - 1] == "--add-dir"
