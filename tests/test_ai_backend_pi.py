import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "claude" / "lib"))

import ai_backend_pi


class TestBuildFixCmd:
    def test_base_command_uses_rpc_mode(self):
        cmd = ai_backend_pi._build_fix_cmd()
        assert "--mode" in cmd
        assert "rpc" in cmd
        assert "-p" not in cmd

    def test_includes_tools(self):
        cmd = ai_backend_pi._build_fix_cmd()
        assert "--tools" in cmd
        idx = cmd.index("--tools")
        assert cmd[idx + 1] == ai_backend_pi.PI_TOOLS

    def test_model_flag(self):
        cmd = ai_backend_pi._build_fix_cmd(model="sonnet")
        assert "--model" in cmd
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "sonnet"

    def test_thinking_level_flag(self):
        cmd = ai_backend_pi._build_fix_cmd(thinking_level="low")
        assert "--thinking" in cmd
        idx = cmd.index("--thinking")
        assert cmd[idx + 1] == "low"

    def test_no_optional_flags_when_none(self):
        cmd = ai_backend_pi._build_fix_cmd()
        assert "--model" not in cmd
        assert "--thinking" not in cmd


class TestBuildAgentCmd:
    def test_includes_rpc_mode(self):
        cmd = ai_backend_pi._build_agent_cmd()
        assert cmd[:2] == ["pi", "--mode"]
        assert cmd[2] == "rpc"

    def test_thinking_level(self):
        cmd = ai_backend_pi._build_agent_cmd(thinking_level="high")
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
        monkeypatch.setattr(ai_backend_pi, "PI_SKILLS_DIR", empty_skills_dir)
        cmd = ai_backend_pi._build_agent_cmd(agent="test")
        assert "--append-system-prompt" in cmd
        idx = cmd.index("--append-system-prompt")
        assert cmd[idx + 1] == "# Test Agent\nDo things."

    def test_missing_agent_raises(self, tmp_path, monkeypatch):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        monkeypatch.setattr(ai_backend_pi, "AGENTS_DIR", agents_dir)
        empty_skills_dir = tmp_path / "skills"
        empty_skills_dir.mkdir()
        monkeypatch.setattr(ai_backend_pi, "PI_SKILLS_DIR", empty_skills_dir)
        with pytest.raises(FileNotFoundError):
            ai_backend_pi._build_agent_cmd(agent="nonexistent")


class TestCheckLimits:
    def _make_proc(self):
        """Create a mock process with stdin that records writes."""
        class MockStdin:
            def __init__(self):
                self.commands = []
            def write(self, data):
                self.commands.append(json.loads(data.strip()))
            def flush(self):
                pass

        class MockProc:
            def __init__(self):
                self.stdin = MockStdin()

        return MockProc()

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
        monkeypatch.setattr(ai_backend_pi, "PI_SKILLS_DIR", skills_dir)
        assert ai_backend_pi._resolve_skill_path("reviewer") == skill_file

    def test_returns_none_when_no_skill(self, tmp_path, monkeypatch):
        skills_dir = tmp_path / "pi" / "skills"
        skills_dir.mkdir(parents=True)
        monkeypatch.setattr(ai_backend_pi, "PI_SKILLS_DIR", skills_dir)
        assert ai_backend_pi._resolve_skill_path("reviewer") is None


class TestBuildAgentCmdWithSkills:
    def test_uses_skill_flag_when_available(self, tmp_path, monkeypatch):
        skills_dir = tmp_path / "pi" / "skills"
        reviewer_dir = skills_dir / "reviewer"
        reviewer_dir.mkdir(parents=True)
        (reviewer_dir / "SKILL.md").write_text("---\nname: reviewer\n---\n# R")
        monkeypatch.setattr(ai_backend_pi, "PI_SKILLS_DIR", skills_dir)
        cmd = ai_backend_pi._build_agent_cmd(agent="reviewer")
        assert "--skill" in cmd
        assert "--append-system-prompt" not in cmd

    def test_falls_back_to_append_system_prompt(self, tmp_path, monkeypatch):
        skills_dir = tmp_path / "empty_skills"
        skills_dir.mkdir()
        monkeypatch.setattr(ai_backend_pi, "PI_SKILLS_DIR", skills_dir)
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "reviewer.md").write_text("# Reviewer agent")
        monkeypatch.setattr(ai_backend_pi, "AGENTS_DIR", agents_dir)
        cmd = ai_backend_pi._build_agent_cmd(agent="reviewer")
        assert "--append-system-prompt" in cmd
        assert "--skill" not in cmd
