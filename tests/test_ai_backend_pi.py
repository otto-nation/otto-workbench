import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

from agent import backend_pi as ai_backend_pi
from conftest import FIXTURES_DIR
from test_ai_backend import _recording_popen


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

    def test_review_extension_exists_on_disk(self):
        """Both call sites gate on is_file(), so a stale path drops the guard silently."""
        assert ai_backend_pi.REVIEW_EXTENSION.is_file(), (
            f"{ai_backend_pi.REVIEW_EXTENSION} is missing — the review agent would run ungated"
        )

    def test_the_guard_is_written_against_pi_s_own_api(self):
        """Existing on disk is not the same as being loadable.

        The first version of this file imported `@anthropic-ai/pi`, exported an
        object literal with an `onToolCall` method, and returned `{blocked}`.
        None of those are Pi's API — the package is not installed, the factory
        is a default-exported function, and the blocking key is `block` — so
        every review agent ran ungated while `is_file()` above passed.
        """
        source = ai_backend_pi.REVIEW_EXTENSION.read_text()
        assert "@anthropic-ai/pi" not in source
        assert "@earendil-works/pi-coding-agent" in source
        assert "export default function" in source
        assert 'pi.on("tool_call"' in source
        assert "blocked: true" not in source

    def test_the_guard_is_not_installed_into_every_pi_session(self):
        """It gates on REVIEW_WORKTREE_DIR, which no interactive session sets.

        ai/pi/steps.sh installs everything under ai/pi/extensions/ into
        ~/.pi/agent/extensions, where Pi loads it in every session on the
        machine. This one belongs to the review pipeline and is passed with
        --extension instead.
        """
        assert ai_backend_pi.REVIEW_EXTENSION.parent.name == "extensions-cli"

    def test_the_guard_allows_the_dirs_the_invocation_named(self):
        """Gating on the worktree alone would refuse the review document.

        build_add_dirs returns [artifact_dir, wt_path], and the artifact dir is
        under ~/.local/state/workbench/reviews/ — outside the worktree by
        design. A guard armed with cwd alone blocks the one write every phase is
        dispatched to make, turning a fail-open into a fail-closed.
        """
        source = ai_backend_pi.REVIEW_EXTENSION.read_text()
        assert "REVIEW_ALLOWED_DIRS" in source
        assert "allowedDirs.some(" in source

    def test_the_guard_canonicalises_before_comparing(self):
        """resolve() does not follow symlinks, and /tmp is one on macOS.

        A root spelled /tmp/x against a path spelled /private/tmp/x/f names one
        directory, and a lexical relative() walks out through `..` and refuses
        the write.
        """
        source = ai_backend_pi.REVIEW_EXTENSION.read_text()
        assert "realpathSync" in source

    def test_the_guard_matches_pi_s_tool_names_and_input_fields(self):
        """Pi's built-in tools are lowercase and take `path`, not `file_path`.

        The original checked for "Write"/"Edit"/"Bash" and read
        `arguments.file_path`, so it would have matched nothing even had it
        loaded.
        """
        source = ai_backend_pi.REVIEW_EXTENSION.read_text()
        assert 'isToolCallEventType("write"' in source
        assert 'isToolCallEventType("bash"' in source
        assert "file_path" not in source


class TestGuardEnv:
    """The guard reads its bounds from the environment, and nothing else sets them.

    review-guard.ts fail-opens when REVIEW_WORKTREE_DIR is absent, so a backend
    that forgets it produces an ungated session rather than an error — the
    failure this whole class exists to catch is silent.
    """

    @pytest.mark.parametrize("entry_point", ["invoke_agent", "invoke_fix"])
    def test_the_worktree_is_the_invocation_cwd(self, monkeypatch, tmp_path, entry_point):
        seen = {}
        monkeypatch.setattr(subprocess, "Popen", _recording_popen(seen))
        getattr(ai_backend_pi, entry_point)(ai_backend_pi.AgentInvocation(
            prompt="p", cwd=str(tmp_path), session_log=str(tmp_path / "s.jsonl"),
        ))
        assert seen["env"]["REVIEW_WORKTREE_DIR"] == str(tmp_path)

    @pytest.mark.parametrize("entry_point", ["invoke_agent", "invoke_fix"])
    def test_add_dirs_reach_the_guard(self, monkeypatch, tmp_path, entry_point):
        """The artifact dir is outside the worktree, and must still be writable."""
        artifact = tmp_path / "reviews" / "pr-42"
        worktree = tmp_path / "wt"
        seen = {}
        monkeypatch.setattr(subprocess, "Popen", _recording_popen(seen))
        getattr(ai_backend_pi, entry_point)(ai_backend_pi.AgentInvocation(
            prompt="p", cwd=str(worktree), session_log=str(tmp_path / "s.jsonl"),
            add_dirs=[str(artifact), str(worktree)],
        ))
        allowed = seen["env"]["REVIEW_ALLOWED_DIRS"].split(os.pathsep)
        assert str(artifact) in allowed
        assert str(worktree) in allowed

    @pytest.mark.parametrize("entry_point", ["invoke_agent", "invoke_fix"])
    def test_no_add_dirs_leaves_the_list_unset(self, monkeypatch, tmp_path, entry_point):
        """An empty value would split to [''] and allow a relative path anywhere."""
        seen = {}
        monkeypatch.setattr(subprocess, "Popen", _recording_popen(seen))
        getattr(ai_backend_pi, entry_point)(ai_backend_pi.AgentInvocation(
            prompt="p", cwd=str(tmp_path), session_log=str(tmp_path / "s.jsonl"),
        ))
        assert "REVIEW_ALLOWED_DIRS" not in seen["env"]


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

    def test_the_steer_names_a_tool_pi_actually_has(self):
        """Regression: this steer used to prescribe Claude's Edit recipe.

        `old_string` is not a parameter Pi's edit tool accepts, and an empty
        `oldText` is rejected outright — so the steer spent an agent's last
        turns on a call that could not succeed. It must name `write`, which is
        in the tool list this module passes.
        """
        assert "old_string" not in ai_backend_pi._WRITE_FIRST
        assert "`write`" in ai_backend_pi._WRITE_FIRST
        assert "write" in ai_backend_pi.PI_TOOLS.split(",")


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


# ── Fixtures captured from a live Pi run ─────────────────────────────────────
# Hand-written fixtures agree with whatever the code already does, which is how
# every bug these cover survived: `args` read as `arguments`, session stats read
# off the response envelope, one prompt's two message costs read as one. See
# tests/fixtures/README-pi-fixtures.md for the recapture commands.

FIXTURES = FIXTURES_DIR


def _prompt_stream() -> str:
    return (FIXTURES / "pi_prompt_session.jsonl").read_text()


# The fixture's two message_end costs (0.0075423 + 0.00470015) summed. Shared
# by every assertion against the fixture's total so a recapture only needs
# updating here.
EXPECTED_PROMPT_COST = 0.01224245


def _stats_response() -> dict:
    return json.loads((FIXTURES / "pi_rpc_stats_response.json").read_text())


def _parsed_result_record(tmp_path, model):
    """Write a result record for the captured stats fixture and parse it back.

    Shared by the three envelope tests below so a future signature change to
    _write_result_record — a new positional argument, a renamed field — is a
    one-place edit instead of three.
    """
    from agent import usage as ai_usage

    log = tmp_path / "session.jsonl"
    ai_backend_pi._write_result_record(
        str(log), "completed", 1, 0.084319, 1234,
        _stats_response()["data"], model,
    )
    return ai_usage.parse_session_log(str(log))


class TestSessionStatsEnvelope:
    """get_session_stats returns {type, command, success, data} — read the data."""

    def test_stats_are_unwrapped_from_the_response(self):
        # Reading `tokens` off the envelope yields {} and a record of four
        # zeroes, which reaches the ledger as a run that cost money and spent
        # no tokens. Nothing else in the pipeline can tell that apart from a
        # genuinely cache-free call.
        response = _stats_response()

        class _Proc:
            stdin = io.StringIO()

            def __init__(self):
                self.stdout = io.StringIO(json.dumps(response) + "\n")

        stats = ai_backend_pi._get_stats_after_agent_end(_Proc())

        assert stats["tokens"]["cacheWrite"] == 33710
        assert stats["cost"] == 0.084319
        assert "data" not in stats, "the envelope was returned instead of its data"

    def test_a_null_data_field_falls_back_to_the_envelope_not_none(self):
        # An explicit "data": null (e.g. on success: false) must not surface
        # as None — _write_result_record would then crash on
        # stats.get("tokens", {}) instead of degrading gracefully.
        response = {"type": "response", "command": "get_session_stats", "success": False, "data": None}

        class _Proc:
            stdin = io.StringIO()

            def __init__(self):
                self.stdout = io.StringIO(json.dumps(response) + "\n")

        stats = ai_backend_pi._get_stats_after_agent_end(_Proc())

        assert stats is not None
        assert stats.get("tokens", {}) == {}

    def test_an_empty_data_field_is_not_replaced_by_the_envelope(self):
        # {} is falsy but present — `or` treats it the same as a missing key
        # and returns the envelope, which then reads `success`/`type` as
        # if they were stats. Presence, not truthiness, decides the fallback.
        response = {"type": "response", "command": "get_session_stats", "success": True, "data": {}}

        class _Proc:
            stdin = io.StringIO()

            def __init__(self):
                self.stdout = io.StringIO(json.dumps(response) + "\n")

        stats = ai_backend_pi._get_stats_after_agent_end(_Proc())

        assert stats == {}
        assert "success" not in stats, "the envelope was returned instead of its (empty) data"

    def test_result_record_carries_the_real_token_counts(self, tmp_path):
        parsed = _parsed_result_record(tmp_path, "claude-opus-5")
        assert parsed.cost == pytest.approx(0.084319)
        assert parsed.input_tokens == 2
        assert parsed.output_tokens == 4
        assert parsed.cache_write_tokens == 33710
        # The whole point: the session moved 33,716 tokens and the ledger says so.
        assert parsed.total_tokens == 33716

    def test_result_record_attributes_cost_to_a_model(self, tmp_path):
        # Without modelUsage every Pi row is blank under `otto-log stats --by model`.
        parsed = _parsed_result_record(tmp_path, "claude-opus-5")
        assert parsed.cost_by_model == {"claude-opus-5": pytest.approx(0.084319)}

    def test_an_unnamed_model_still_records_cost(self, tmp_path):
        parsed = _parsed_result_record(tmp_path, None)
        assert parsed.cost == pytest.approx(0.084319)
        assert parsed.cost_by_model == {}

    def test_invoke_agent_s_model_key_matches_prompt_s_model_key(self, tmp_path):
        # invoke_agent/invoke_fix write modelUsage keyed on stream.model, which
        # _consume_stream fills from message_end's own model field. If that
        # ever drifted back to the caller-requested alias, the same physical
        # run would land under two different cost_by_model keys depending on
        # whether it went through prompt() or invoke_agent() — splitting
        # `otto-log stats --by model` for the same model.
        from agent import usage as ai_usage
        from agent.backend_events import pi_prompt_result

        class _Proc:
            stdin = io.StringIO()

            def __init__(self):
                self.stdout = io.StringIO(_prompt_stream())

        stream = ai_backend_pi._consume_stream(_Proc(), io.StringIO(), "")

        log = tmp_path / "session.jsonl"
        ai_backend_pi._write_result_record(
            str(log), stream.stop_reason, stream.turn_count,
            stream.accumulated_cost, 1234, {}, stream.model or "claude-opus-5",
        )
        parsed = ai_usage.parse_session_log(str(log))

        _, prompt_usage = pi_prompt_result(_prompt_stream())

        assert stream.model == "claude-haiku-4-5@20251001"
        assert set(parsed.cost_by_model) == set(prompt_usage.cost_by_model)


class TestPromptUsage:
    """A prompt call is measured, or it is invisible to the ledger."""

    def test_prompt_command_asks_for_the_json_stream(self):
        # Bare `-p` emits prose and no usage, which is how 953 prompt-shaped
        # calls would have gone unrecorded.
        cmd = ai_backend_pi._build_prompt_cmd(model="haiku")
        assert "--mode" in cmd and cmd[cmd.index("--mode") + 1] == "json"
        assert cmd.index("-p") < cmd.index("--mode")

    def test_reply_is_the_final_assistant_text(self):
        from agent.backend_events import pi_prompt_result

        text, _ = pi_prompt_result(_prompt_stream())
        assert text == "The contents of `f.txt` are:\n\n```\nhello\n```"

    def test_an_earlier_assistant_turn_does_not_win(self):
        # The captured fixture's first assistant message is a tool call with no
        # text, so it cannot tell first-from-last apart on its own. This stream
        # gives both turns text: taking the first would answer with the model
        # thinking aloud before it had the file.
        from agent.backend_events import pi_prompt_result

        stream = json.dumps({
            "type": "agent_end",
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "read it"}]},
                {"role": "assistant", "content": [{"type": "text", "text": "Let me look."}]},
                {"role": "toolResult", "content": [{"type": "text", "text": "hello"}]},
                {"role": "assistant", "content": [{"type": "text", "text": "It says hello."}]},
            ],
        })

        text, _ = pi_prompt_result(stream)
        assert text == "It says hello."

    def test_cost_sums_every_message_not_just_the_last(self):
        # The fixture is a tool-using prompt: two assistant messages, two costs
        # (0.0075423 and 0.00470015). Taking the last would report 38% of it.
        from agent.backend_events import pi_prompt_result

        _, usage = pi_prompt_result(_prompt_stream())
        assert usage.cost == pytest.approx(EXPECTED_PROMPT_COST)
        assert usage.cost > 0.0075423, "only the last message_end was counted"

    def test_every_token_column_is_populated(self):
        from agent.backend_events import pi_prompt_result

        _, usage = pi_prompt_result(_prompt_stream())
        assert usage.input_tokens == 23
        assert usage.output_tokens == 382
        assert usage.cache_read_tokens == 78282
        assert usage.cache_write_tokens == 1985

    def test_prose_stdout_degrades_to_unmeasured(self):
        # A Pi output-format change should cost the measurement, not the call.
        from agent.backend_events import pi_prompt_result

        text, usage = pi_prompt_result("just the answer, no JSON here")
        assert text == "just the answer, no JSON here"
        assert usage is None

    def test_json_without_message_end_is_unmeasured_not_free(self):
        # A zero-cost row reads as a call that genuinely cost nothing, which is
        # a different claim from one nobody measured.
        from agent.backend_events import pi_prompt_result

        stream = '{"type":"agent_end","messages":[{"role":"assistant","content":[{"type":"text","text":"hi"}]}]}'
        text, usage = pi_prompt_result(stream)
        assert text == "hi"
        assert usage is None

    def test_prompt_returns_the_usage_it_parsed(self, monkeypatch):
        captured = _prompt_stream()

        class _Result:
            stdout = captured
            returncode = 0

        monkeypatch.setattr(ai_backend_pi.subprocess, "run", lambda *a, **k: _Result())
        reply, code, usage = ai_backend_pi.prompt("anything", cwd=".")

        assert code == 0
        assert reply.endswith("```")
        assert usage is not None, "prompt dropped the usage it was handed"
        assert usage.cost == pytest.approx(EXPECTED_PROMPT_COST)


class TestPiToolLabels:
    """Labels are built from Pi's own event shape, not Claude's."""

    def test_a_real_tool_event_labels(self):
        from agent.backend_events import _pi_tool_label

        events = [
            json.loads(line) for line in _prompt_stream().splitlines() if line.strip()
        ]
        starts = [e for e in events if e.get("type") == "tool_execution_start"]
        assert starts, "fixture carries no tool_execution_start to label"

        # Pi spells the bag `args` and the path `path`. Reading Claude's
        # `arguments`/`file_path` finds nothing and every label falls back to
        # the bare tool name, which no hand-written fixture would reveal.
        assert _pi_tool_label(starts[0]) == "Read f.txt"

    def test_claude_spelling_still_labels(self):
        from agent.backend_events import _pi_tool_label

        assert _pi_tool_label(
            {"toolName": "read", "arguments": {"file_path": "/a/b/c.txt"}}
        ) == "Read c.txt"

    def test_an_empty_arg_bag_is_the_answer_not_a_miss(self):
        # Pi sent `args: {}` — a tool called with no arguments. Falling through
        # to Claude's spelling would answer from a bag this event did not have.
        from agent.backend_events import _pi_tool_label

        assert _pi_tool_label(
            {"toolName": "read", "args": {}, "arguments": {"file_path": "/a/z.txt"}}
        ) == "Read "

    def test_a_tool_with_no_arg_bag_still_labels(self):
        from agent.backend_events import _pi_tool_label

        assert _pi_tool_label({"toolName": "bash"}) == "Bash"
