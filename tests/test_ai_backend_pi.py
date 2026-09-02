import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import ai_backend_pi


class TestBuildFixCmd:
    def test_base_command_uses_rpc_mode(self):
        cmd = ai_backend_pi._build_fix_cmd(ai_backend_pi.AgentInvocation(prompt=""))
        assert "--mode" in cmd
        assert "rpc" in cmd
        assert "-p" not in cmd

    def test_includes_tools(self):
        cmd = ai_backend_pi._build_fix_cmd(ai_backend_pi.AgentInvocation(prompt=""))
        assert "--tools" in cmd
        idx = cmd.index("--tools")
        assert cmd[idx + 1] == ai_backend_pi.PI_FIX_TOOLS

    def test_withholds_github_tools(self):
        cmd = ai_backend_pi._build_fix_cmd(ai_backend_pi.AgentInvocation(prompt=""))
        tools = cmd[cmd.index("--tools") + 1].split(",")
        assert [t for t in tools if t.startswith("gh_")] == []

    def test_grants_research_tools(self):
        cmd = ai_backend_pi._build_fix_cmd(ai_backend_pi.AgentInvocation(prompt=""))
        tools = cmd[cmd.index("--tools") + 1].split(",")
        assert "web_fetch" in tools
        assert "go_references" in tools

    def test_model_flag(self):
        cmd = ai_backend_pi._build_fix_cmd(
            ai_backend_pi.AgentInvocation(prompt="", model="sonnet"),
        )
        assert "--model" in cmd
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "sonnet"

    def test_thinking_level_flag(self):
        cmd = ai_backend_pi._build_fix_cmd(
            ai_backend_pi.AgentInvocation(prompt="", thinking="low"),
        )
        assert "--thinking" in cmd
        idx = cmd.index("--thinking")
        assert cmd[idx + 1] == "low"

    def test_no_optional_flags_when_none(self):
        cmd = ai_backend_pi._build_fix_cmd(ai_backend_pi.AgentInvocation(prompt=""))
        assert "--model" not in cmd
        assert "--thinking" not in cmd
        assert "--provider" not in cmd
        assert "--extension" not in cmd


class TestBuildAgentCmd:
    def test_includes_rpc_mode(self):
        cmd = ai_backend_pi._build_agent_cmd(ai_backend_pi.AgentInvocation(prompt=""))
        assert cmd[:2] == ["pi", "--mode"]
        assert cmd[2] == "rpc"

    def test_includes_tools(self):
        cmd = ai_backend_pi._build_agent_cmd(ai_backend_pi.AgentInvocation(prompt=""))
        assert "--tools" in cmd
        idx = cmd.index("--tools")
        assert cmd[idx + 1] == ai_backend_pi.PI_AGENT_TOOLS

    def test_grants_read_only_github_tools(self):
        cmd = ai_backend_pi._build_agent_cmd(ai_backend_pi.AgentInvocation(prompt=""))
        tools = cmd[cmd.index("--tools") + 1].split(",")
        assert "gh_pr_unresolved_comments" in tools
        assert "gh_ci_failures" in tools

    def test_thinking_level(self):
        cmd = ai_backend_pi._build_agent_cmd(
            ai_backend_pi.AgentInvocation(prompt="", thinking="high"),
        )
        assert "--thinking" in cmd
        idx = cmd.index("--thinking")
        assert cmd[idx + 1] == "high"

    def test_agent_appends_system_prompt(self, tmp_path, monkeypatch):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "test.md").write_text("# Test Agent\nDo things.")
        monkeypatch.setattr(ai_backend_pi, "AGENTS_DIR", agents_dir)
        # Ensure no skill file exists so fallback path is exercised
        empty_skills_dir = tmp_path / "skills"
        empty_skills_dir.mkdir()
        monkeypatch.setattr(ai_backend_pi, "AGENTS_SKILLS_DIR", empty_skills_dir)
        cmd = ai_backend_pi._build_agent_cmd(
            ai_backend_pi.AgentInvocation(prompt="", agent="test"),
        )
        assert "--append-system-prompt" in cmd
        idx = cmd.index("--append-system-prompt")
        assert cmd[idx + 1] == "# Test Agent\nDo things."

    def test_missing_agent_raises(self, tmp_path, monkeypatch):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        monkeypatch.setattr(ai_backend_pi, "AGENTS_DIR", agents_dir)
        empty_skills_dir = tmp_path / "skills"
        empty_skills_dir.mkdir()
        monkeypatch.setattr(ai_backend_pi, "AGENTS_SKILLS_DIR", empty_skills_dir)
        with pytest.raises(FileNotFoundError):
            ai_backend_pi._build_agent_cmd(
                ai_backend_pi.AgentInvocation(prompt="", agent="nonexistent"),
            )


class TestToolAllowlists:
    """Our scripts own what reaches a PR, so no list may name a posting tool.

    The github-pr extension registers gh_pr_reply_comment, gh_pr_bulk_reply and
    gh_pr_post_comment alongside the read-only ones. Naming a tool is how the
    allowlist grants it, so leaving them out is the whole gate.
    """

    POSTING_TOOLS = ("gh_pr_reply_comment", "gh_pr_bulk_reply", "gh_pr_post_comment")

    @pytest.mark.parametrize("tool", POSTING_TOOLS)
    def test_agent_list_withholds_posting_tools(self, tool):
        assert tool not in ai_backend_pi.PI_AGENT_TOOLS.split(",")

    @pytest.mark.parametrize("tool", POSTING_TOOLS)
    def test_fix_list_withholds_posting_tools(self, tool):
        assert tool not in ai_backend_pi.PI_FIX_TOOLS.split(",")

    def test_both_lists_keep_the_built_ins(self):
        for name in ai_backend_pi.PI_TOOLS.split(","):
            assert name in ai_backend_pi.PI_AGENT_TOOLS.split(",")
            assert name in ai_backend_pi.PI_FIX_TOOLS.split(",")


class TestCheckLimits:
    class MockStdin:
        def __init__(self):
            self.commands = []
        def write(self, data):
            self.commands.append(json.loads(data.strip()))
        def flush(self):
            pass

    class MockProc:
        def __init__(self, stdin_cls):
            self.stdin = stdin_cls()

    def _make_proc(self):
        """Create a mock process with stdin that records writes."""
        return self.MockProc(self.MockStdin)

    def test_no_action_within_limits(self):
        proc = self._make_proc()
        stop, steered = ai_backend_pi._check_limits(proc, 5, 2.0, 10, 5.0)
        assert stop is None
        assert len(proc.stdin.commands) == 0

    def test_abort_at_max_turns(self):
        proc = self._make_proc()
        stop, steered = ai_backend_pi._check_limits(proc, 10, 2.0, 10, 5.0)
        assert stop == "max_turns"
        assert any(c["type"] == "abort" for c in proc.stdin.commands)

    def test_abort_over_budget(self):
        proc = self._make_proc()
        stop, steered = ai_backend_pi._check_limits(proc, 5, 5.1, 10, 5.0)
        assert stop == "max_budget"
        assert any(c["type"] == "abort" for c in proc.stdin.commands)

    def test_steer_at_80_pct_budget(self):
        proc = self._make_proc()
        stop, steered = ai_backend_pi._check_limits(proc, 5, 4.1, 10, 5.0)
        assert stop is None
        assert any(c["type"] == "steer" for c in proc.stdin.commands)

    def test_steer_at_exact_80_pct_budget_boundary(self):
        proc = self._make_proc()
        stop, steered = ai_backend_pi._check_limits(proc, 5, 4.0, 10, 5.0)
        assert stop is None
        assert steered is True
        assert any(c["type"] == "steer" for c in proc.stdin.commands)

    def test_steer_at_80_pct_turns(self):
        proc = self._make_proc()
        stop, steered = ai_backend_pi._check_limits(proc, 8, 2.0, 10, 5.0)
        assert stop is None
        assert any(c["type"] == "steer" for c in proc.stdin.commands)

    def test_no_steer_when_no_limits(self):
        proc = self._make_proc()
        stop, steered = ai_backend_pi._check_limits(proc, 100, 100.0, None, None)
        assert stop is None
        assert len(proc.stdin.commands) == 0

    def test_follow_up_on_abort(self):
        proc = self._make_proc()
        stop, steered = ai_backend_pi._check_limits(proc, 10, 2.0, 10, 5.0)
        assert stop == "max_turns"
        assert any(c["type"] == "follow_up" for c in proc.stdin.commands)

    def test_no_duplicate_steer_when_steered_true(self):
        proc = self._make_proc()
        # First call triggers steer
        stop, steered = ai_backend_pi._check_limits(proc, 8, 2.0, 10, 5.0, steered=False)
        assert stop is None
        assert steered is True
        first_count = len(proc.stdin.commands)
        # Second call with steered=True should not send another steer
        stop, steered = ai_backend_pi._check_limits(proc, 9, 2.0, 10, 5.0, steered=True)
        assert stop is None
        assert len(proc.stdin.commands) == first_count

    def test_steered_flag_returned_true_after_steer(self):
        proc = self._make_proc()
        stop, steered = ai_backend_pi._check_limits(proc, 8, 2.0, 10, 5.0, steered=False)
        assert steered is True

    def test_steered_flag_unchanged_when_within_limits(self):
        proc = self._make_proc()
        stop, steered = ai_backend_pi._check_limits(proc, 5, 2.0, 10, 5.0, steered=False)
        assert steered is False


class TestResolveSkillPath:
    def test_returns_skill_path_when_exists(self, tmp_path, monkeypatch):
        skills_dir = tmp_path / "pi" / "skills"
        reviewer_dir = skills_dir / "reviewer"
        reviewer_dir.mkdir(parents=True)
        skill_file = reviewer_dir / "SKILL.md"
        skill_file.write_text("---\nname: reviewer\n---\n# Reviewer")
        monkeypatch.setattr(ai_backend_pi, "AGENTS_SKILLS_DIR", skills_dir)
        assert ai_backend_pi._resolve_skill_path("reviewer") == skill_file

    def test_returns_none_when_no_skill(self, tmp_path, monkeypatch):
        skills_dir = tmp_path / "pi" / "skills"
        skills_dir.mkdir(parents=True)
        monkeypatch.setattr(ai_backend_pi, "AGENTS_SKILLS_DIR", skills_dir)
        assert ai_backend_pi._resolve_skill_path("reviewer") is None

    def test_returns_none_when_placeholder_present(self, tmp_path, monkeypatch):
        skills_dir = tmp_path / "pi" / "skills"
        reviewer_dir = skills_dir / "reviewer"
        reviewer_dir.mkdir(parents=True)
        skill_file = reviewer_dir / "SKILL.md"
        skill_file.write_text("---\nname: reviewer\n---\n<!-- AGENT_PROTOCOL_PLACEHOLDER: replaced by setup -->\n")
        monkeypatch.setattr(ai_backend_pi, "AGENTS_SKILLS_DIR", skills_dir)
        assert ai_backend_pi._resolve_skill_path("reviewer") is None


class TestBuildAgentCmdWithSkills:
    def test_uses_skill_flag_when_available(self, tmp_path, monkeypatch):
        skills_dir = tmp_path / "pi" / "skills"
        reviewer_dir = skills_dir / "reviewer"
        reviewer_dir.mkdir(parents=True)
        (reviewer_dir / "SKILL.md").write_text("---\nname: reviewer\n---\n# R")
        monkeypatch.setattr(ai_backend_pi, "AGENTS_SKILLS_DIR", skills_dir)
        cmd = ai_backend_pi._build_agent_cmd(
            ai_backend_pi.AgentInvocation(prompt="", agent="reviewer"),
        )
        assert "--skill" in cmd
        assert "--append-system-prompt" not in cmd

    def test_falls_back_to_append_system_prompt(self, tmp_path, monkeypatch):
        skills_dir = tmp_path / "empty_skills"
        skills_dir.mkdir()
        monkeypatch.setattr(ai_backend_pi, "AGENTS_SKILLS_DIR", skills_dir)
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "reviewer.md").write_text("# Reviewer agent")
        monkeypatch.setattr(ai_backend_pi, "AGENTS_DIR", agents_dir)
        cmd = ai_backend_pi._build_agent_cmd(
            ai_backend_pi.AgentInvocation(prompt="", agent="reviewer"),
        )
        assert "--append-system-prompt" in cmd
        assert "--skill" not in cmd


class TestProviderFlag:
    def test_agent_cmd_with_provider(self):
        cmd = ai_backend_pi._build_agent_cmd(
            ai_backend_pi.AgentInvocation(prompt="", provider="bedrock"),
        )
        assert "--provider" in cmd
        idx = cmd.index("--provider")
        assert cmd[idx + 1] == "bedrock"

    def test_agent_cmd_without_provider(self):
        cmd = ai_backend_pi._build_agent_cmd(ai_backend_pi.AgentInvocation(prompt=""))
        assert "--provider" not in cmd

    def test_fix_cmd_with_provider(self):
        cmd = ai_backend_pi._build_fix_cmd(
            ai_backend_pi.AgentInvocation(prompt="", provider="vertex"),
        )
        assert "--provider" in cmd
        idx = cmd.index("--provider")
        assert cmd[idx + 1] == "vertex"

    def test_prompt_cmd_with_provider(self):
        cmd = ai_backend_pi._build_prompt_cmd(provider="bedrock")
        assert "--provider" in cmd
        idx = cmd.index("--provider")
        assert cmd[idx + 1] == "bedrock"


class TestPromptCmdThinking:
    """A stateless prompt is sized the same way the agent modes are.

    ``--thinking`` and ``--provider`` are global Pi flags, so the prompt shape
    honours both. Before those calls were phases they carried neither: nothing
    resolved a thinking level for them and the builder had no argument to take
    one through, so a prompt ran at whatever the CLI defaults to no matter what
    the operator set.
    """

    def test_the_thinking_level_reaches_the_flag(self):
        cmd = ai_backend_pi._build_prompt_cmd(thinking="high")
        assert cmd[cmd.index("--thinking") + 1] == "high"

    def test_no_flags_when_nothing_was_resolved(self):
        cmd = ai_backend_pi._build_prompt_cmd()
        assert "--thinking" not in cmd
        assert "--provider" not in cmd
        assert "--model" not in cmd

    def test_prompt_forwards_every_resolved_knob(self, monkeypatch, tmp_path):
        """The dispatch layer's arguments have to survive the trip to the CLI."""
        seen = []
        monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: seen.append(cmd) or
                            subprocess.CompletedProcess(cmd, 0, "answer", ""))
        ai_backend_pi.prompt(
            "ask", cwd=str(tmp_path), model="sonnet",
            thinking="low", provider="bedrock",
        )
        cmd = seen[0]
        assert cmd[cmd.index("--model") + 1] == "sonnet"
        assert cmd[cmd.index("--thinking") + 1] == "low"
        assert cmd[cmd.index("--provider") + 1] == "bedrock"


class TestExtensionFlag:
    def test_agent_cmd_with_extension(self):
        cmd = ai_backend_pi._build_agent_cmd(
            ai_backend_pi.AgentInvocation(prompt=""), extension="/path/to/review-guard.ts",
        )
        assert "--extension" in cmd
        idx = cmd.index("--extension")
        assert cmd[idx + 1] == "/path/to/review-guard.ts"

    def test_agent_cmd_without_extension(self):
        cmd = ai_backend_pi._build_agent_cmd(ai_backend_pi.AgentInvocation(prompt=""))
        assert "--extension" not in cmd

    def test_fix_cmd_with_extension(self):
        cmd = ai_backend_pi._build_fix_cmd(
            ai_backend_pi.AgentInvocation(prompt=""), extension="/path/to/review-guard.ts",
        )
        assert "--extension" in cmd
        idx = cmd.index("--extension")
        assert cmd[idx + 1] == "/path/to/review-guard.ts"

    def test_prompt_cmd_does_not_accept_extension(self):
        """_build_prompt_cmd intentionally omits --extension (stateless, no tool gating)."""
        import inspect
        sig = inspect.signature(ai_backend_pi._build_prompt_cmd)
        assert "extension" not in sig.parameters


class TestWriteAwareSteer:
    """The 80% steer names the write mechanism when nothing has been written."""

    def _steer_text(self, *args):
        """The message of the single steer command sent by _check_limits."""
        proc = TestCheckLimits.MockProc(TestCheckLimits.MockStdin)
        ai_backend_pi._check_limits(proc, *args)
        steers = [c for c in proc.stdin.commands if c["type"] == "steer"]
        assert len(steers) == 1
        return steers[0]["message"]

    def test_unwritten_agent_is_told_how_to_write(self):
        text = self._steer_text(8, 2.0, 10, 5.0, False, False)
        assert ai_backend_pi._WRITE_FIRST in text
        assert ai_backend_pi._WRAP_UP not in text

    def test_written_agent_is_told_to_wrap_up(self):
        text = self._steer_text(8, 2.0, 10, 5.0, False, True)
        assert ai_backend_pi._WRAP_UP in text
        assert ai_backend_pi._WRITE_FIRST not in text

    def test_warning_context_is_kept_in_both_messages(self):
        assert "8/10 turns" in self._steer_text(8, 2.0, 10, 5.0, False, False)
        assert "8/10 turns" in self._steer_text(8, 2.0, 10, 5.0, False, True)

    def test_budget_steer_is_also_write_aware(self):
        text = self._steer_text(5, 4.1, 10, 5.0, False, False)
        assert ai_backend_pi._WRITE_FIRST in text
        assert "4.10/5.00 USD" in text

    def test_default_assumes_nothing_was_written(self):
        """Callers that cannot observe tool calls get the safe message."""
        assert ai_backend_pi._WRITE_FIRST in self._steer_text(8, 2.0, 10, 5.0)


class TestConsumeStreamTracksWrites:
    """_consume_stream is what tells _check_limits whether a write happened."""

    class MockProc:
        def __init__(self, lines):
            self.stdout = iter(lines)
            self.stdin = TestCheckLimits.MockStdin()

    def _steer_message(self, tool_name):
        lines = [json.dumps({
            "type": "message_update",
            "content": [{"type": "toolCall", "name": tool_name, "arguments": {}}],
        })]
        lines += [json.dumps({"type": "turn_end"})] * 8
        lines.append(json.dumps({"type": "agent_end"}))
        proc = self.MockProc([l + "\n" for l in lines])
        ai_backend_pi._consume_stream(proc, io.StringIO(), "", max_turns=10)
        steers = [c for c in proc.stdin.commands if c["type"] == "steer"]
        assert len(steers) == 1
        return steers[0]["message"]

    def test_edit_call_earns_the_wrap_up_message(self):
        assert ai_backend_pi._WRAP_UP in self._steer_message("edit")

    def test_read_only_run_earns_the_write_first_message(self):
        assert ai_backend_pi._WRITE_FIRST in self._steer_message("read")


class TestPreflight:
    def test_always_passes(self):
        """Pi resolves models itself — Vertex quota is not its config surface."""
        assert ai_backend_pi.preflight({"claude-sonnet-5": ["group"]}, None) is True
