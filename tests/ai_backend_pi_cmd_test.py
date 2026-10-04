"""Tests for agent.backend_pi command building: tools, flags, extension, env, rules, preflight."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import agent.backend_pi
from ai_backend_test import _recording_popen


class TestBuildFixCmd:
    def test_base_command_uses_rpc_mode(self):
        cmd = agent.backend_pi._build_fix_cmd(agent.backend_pi.AgentInvocation(prompt=""))
        assert "--mode" in cmd
        assert "rpc" in cmd
        assert "-p" not in cmd

    def test_includes_tools(self):
        cmd = agent.backend_pi._build_fix_cmd(agent.backend_pi.AgentInvocation(prompt=""))
        assert "--tools" in cmd
        idx = cmd.index("--tools")
        assert cmd[idx + 1] == agent.backend_pi.PI_FIX_TOOLS

    def test_withholds_github_tools(self):
        cmd = agent.backend_pi._build_fix_cmd(agent.backend_pi.AgentInvocation(prompt=""))
        tools = cmd[cmd.index("--tools") + 1].split(",")
        assert [t for t in tools if t.startswith("gh_")] == []

    def test_grants_research_tools(self):
        cmd = agent.backend_pi._build_fix_cmd(agent.backend_pi.AgentInvocation(prompt=""))
        tools = cmd[cmd.index("--tools") + 1].split(",")
        assert "web_fetch" in tools
        assert "go_references" in tools

    def test_model_flag(self):
        cmd = agent.backend_pi._build_fix_cmd(
            agent.backend_pi.AgentInvocation(prompt="", model="sonnet"),
        )
        assert "--model" in cmd
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "sonnet"

    def test_thinking_level_flag(self):
        cmd = agent.backend_pi._build_fix_cmd(
            agent.backend_pi.AgentInvocation(prompt="", thinking="low"),
        )
        assert "--thinking" in cmd
        idx = cmd.index("--thinking")
        assert cmd[idx + 1] == "low"

    def test_no_optional_flags_when_none(self):
        cmd = agent.backend_pi._build_fix_cmd(agent.backend_pi.AgentInvocation(prompt=""))
        assert "--model" not in cmd
        assert "--thinking" not in cmd
        assert "--provider" not in cmd
        assert "--extension" not in cmd


class TestBuildAgentCmd:
    def test_includes_rpc_mode(self):
        cmd = agent.backend_pi._build_agent_cmd(agent.backend_pi.AgentInvocation(prompt=""))
        assert cmd[:2] == ["pi", "--mode"]
        assert cmd[2] == "rpc"

    def test_includes_tools(self):
        cmd = agent.backend_pi._build_agent_cmd(agent.backend_pi.AgentInvocation(prompt=""))
        assert "--tools" in cmd
        idx = cmd.index("--tools")
        assert cmd[idx + 1] == agent.backend_pi.PI_AGENT_TOOLS

    def test_grants_read_only_github_tools(self):
        cmd = agent.backend_pi._build_agent_cmd(agent.backend_pi.AgentInvocation(prompt=""))
        tools = cmd[cmd.index("--tools") + 1].split(",")
        assert "gh_pr_unresolved_comments" in tools
        assert "gh_ci_failures" in tools

    def test_thinking_level(self):
        cmd = agent.backend_pi._build_agent_cmd(
            agent.backend_pi.AgentInvocation(prompt="", thinking="high"),
        )
        assert "--thinking" in cmd
        idx = cmd.index("--thinking")
        assert cmd[idx + 1] == "high"

    def test_agent_appends_system_prompt(self, tmp_path, monkeypatch):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "test.md").write_text("# Test Agent\nDo things.")
        monkeypatch.setattr(agent.backend_pi, "AGENTS_DIR", agents_dir)
        # Ensure no skill file exists so fallback path is exercised
        empty_skills_dir = tmp_path / "skills"
        empty_skills_dir.mkdir()
        monkeypatch.setattr(agent.backend_pi, "AGENTS_SKILLS_DIR", empty_skills_dir)
        cmd = agent.backend_pi._build_agent_cmd(
            agent.backend_pi.AgentInvocation(prompt="", agent="test"),
        )
        assert "--append-system-prompt" in cmd
        idx = cmd.index("--append-system-prompt")
        assert cmd[idx + 1] == "# Test Agent\nDo things."

    def test_missing_agent_raises(self, tmp_path, monkeypatch):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        monkeypatch.setattr(agent.backend_pi, "AGENTS_DIR", agents_dir)
        empty_skills_dir = tmp_path / "skills"
        empty_skills_dir.mkdir()
        monkeypatch.setattr(agent.backend_pi, "AGENTS_SKILLS_DIR", empty_skills_dir)
        with pytest.raises(FileNotFoundError):
            agent.backend_pi._build_agent_cmd(
                agent.backend_pi.AgentInvocation(prompt="", agent="nonexistent"),
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
        assert tool not in agent.backend_pi.PI_AGENT_TOOLS.split(",")

    @pytest.mark.parametrize("tool", POSTING_TOOLS)
    def test_fix_list_withholds_posting_tools(self, tool):
        assert tool not in agent.backend_pi.PI_FIX_TOOLS.split(",")

    def test_both_lists_keep_the_built_ins(self):
        for name in agent.backend_pi.PI_TOOLS.split(","):
            assert name in agent.backend_pi.PI_AGENT_TOOLS.split(",")
            assert name in agent.backend_pi.PI_FIX_TOOLS.split(",")


class TestResolveSkillPath:
    def test_returns_skill_path_when_exists(self, tmp_path, monkeypatch):
        skills_dir = tmp_path / "pi" / "skills"
        reviewer_dir = skills_dir / "reviewer"
        reviewer_dir.mkdir(parents=True)
        skill_file = reviewer_dir / "SKILL.md"
        skill_file.write_text("---\nname: reviewer\n---\n# Reviewer")
        monkeypatch.setattr(agent.backend_pi, "AGENTS_SKILLS_DIR", skills_dir)
        assert agent.backend_pi._resolve_skill_path("reviewer") == skill_file

    def test_returns_none_when_no_skill(self, tmp_path, monkeypatch):
        skills_dir = tmp_path / "pi" / "skills"
        skills_dir.mkdir(parents=True)
        monkeypatch.setattr(agent.backend_pi, "AGENTS_SKILLS_DIR", skills_dir)
        assert agent.backend_pi._resolve_skill_path("reviewer") is None

    def test_returns_none_when_placeholder_present(self, tmp_path, monkeypatch):
        skills_dir = tmp_path / "pi" / "skills"
        reviewer_dir = skills_dir / "reviewer"
        reviewer_dir.mkdir(parents=True)
        skill_file = reviewer_dir / "SKILL.md"
        skill_file.write_text("---\nname: reviewer\n---\n<!-- AGENT_PROTOCOL_PLACEHOLDER: replaced by setup -->\n")
        monkeypatch.setattr(agent.backend_pi, "AGENTS_SKILLS_DIR", skills_dir)
        assert agent.backend_pi._resolve_skill_path("reviewer") is None


class TestBuildAgentCmdWithSkills:
    def test_uses_skill_flag_when_available(self, tmp_path, monkeypatch):
        skills_dir = tmp_path / "pi" / "skills"
        reviewer_dir = skills_dir / "reviewer"
        reviewer_dir.mkdir(parents=True)
        (reviewer_dir / "SKILL.md").write_text("---\nname: reviewer\n---\n# R")
        monkeypatch.setattr(agent.backend_pi, "AGENTS_SKILLS_DIR", skills_dir)
        cmd = agent.backend_pi._build_agent_cmd(
            agent.backend_pi.AgentInvocation(prompt="", agent="reviewer"),
        )
        assert "--skill" in cmd
        assert "--append-system-prompt" not in cmd

    def test_falls_back_to_append_system_prompt(self, tmp_path, monkeypatch):
        skills_dir = tmp_path / "empty_skills"
        skills_dir.mkdir()
        monkeypatch.setattr(agent.backend_pi, "AGENTS_SKILLS_DIR", skills_dir)
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "reviewer.md").write_text("# Reviewer agent")
        monkeypatch.setattr(agent.backend_pi, "AGENTS_DIR", agents_dir)
        cmd = agent.backend_pi._build_agent_cmd(
            agent.backend_pi.AgentInvocation(prompt="", agent="reviewer"),
        )
        assert "--append-system-prompt" in cmd
        assert "--skill" not in cmd


class TestProviderFlag:
    def test_agent_cmd_with_provider(self):
        cmd = agent.backend_pi._build_agent_cmd(
            agent.backend_pi.AgentInvocation(prompt="", provider="bedrock"),
        )
        assert "--provider" in cmd
        idx = cmd.index("--provider")
        assert cmd[idx + 1] == "bedrock"

    def test_agent_cmd_without_provider(self):
        cmd = agent.backend_pi._build_agent_cmd(agent.backend_pi.AgentInvocation(prompt=""))
        assert "--provider" not in cmd

    def test_fix_cmd_with_provider(self):
        cmd = agent.backend_pi._build_fix_cmd(
            agent.backend_pi.AgentInvocation(prompt="", provider="vertex"),
        )
        assert "--provider" in cmd
        idx = cmd.index("--provider")
        assert cmd[idx + 1] == "vertex"

    def test_prompt_cmd_with_provider(self):
        cmd = agent.backend_pi._build_prompt_cmd(provider="bedrock")
        assert "--provider" in cmd
        idx = cmd.index("--provider")
        assert cmd[idx + 1] == "bedrock"


class TestPromptCmdHasNoTools:
    """A stateless prompt cannot hold a tool, and the flag is the only thing saying so.

    ``-p`` is non-interactive, not tool-less: Pi's built-ins stay registered and
    ``--approve`` runs them without asking. So the shape documented as
    "stateless text-in/text-out" arrived holding a shell, and a rebase conflict
    resolver used it — ``git rebase --edit-todo`` from a tool call, ``vi`` on a
    pipe with no terminal, and ``prompt()`` runs UNBOUNDED, so the run sat there
    for 45 minutes until the job's timeout killed it and left a partial rebase
    behind.

    Every case here asserts our argv rather than Pi's behaviour, which is the
    shape of the original bug: the command was reviewed and what the CLI did
    with it was not. Two things cover that gap instead of a live call, which
    ``_no_live_backend`` in conftest refuses on purpose. Pi rejects an unknown
    option outright — ``pi --tools`` with no value exits non-zero on ``Unknown
    option`` — so a renamed flag fails every prompt call immediately rather
    than silently restoring the tools. And the behaviour itself was checked by
    hand when the flag went in: asked to run ``echo`` via bash, Pi runs it
    without ``--no-tools`` and answers the fallback word with it.
    """

    def test_the_flag_is_on_the_command(self):
        assert "--no-tools" in agent.backend_pi._build_prompt_cmd()

    def test_it_survives_every_other_knob(self):
        """A later flag added ahead of it must not displace it."""
        cmd = agent.backend_pi._build_prompt_cmd(
            model="sonnet", provider="bedrock", thinking="high",
        )
        assert "--no-tools" in cmd

    def test_the_agent_modes_keep_their_tools(self):
        """Only the prompt shape loses them — an agent with no tools does nothing."""
        inv = agent.backend_pi.AgentInvocation(prompt="")
        assert "--no-tools" not in agent.backend_pi._build_agent_cmd(inv)
        assert "--no-tools" not in agent.backend_pi._build_fix_cmd(inv)

    def test_it_reaches_the_subprocess(self, monkeypatch, tmp_path):
        seen = []
        monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: seen.append(cmd) or
                            subprocess.CompletedProcess(cmd, 0, "answer", ""))
        agent.backend_pi.prompt("ask", cwd=str(tmp_path))
        assert "--no-tools" in seen[0]


class TestPromptCmdThinking:
    """A stateless prompt is sized the same way the agent modes are.

    ``--thinking`` and ``--provider`` are global Pi flags, so the prompt shape
    honours both. Before those calls were phases they carried neither: nothing
    resolved a thinking level for them and the builder had no argument to take
    one through, so a prompt ran at whatever the CLI defaults to no matter what
    the operator set.
    """

    def test_the_thinking_level_reaches_the_flag(self):
        cmd = agent.backend_pi._build_prompt_cmd(thinking="high")
        assert cmd[cmd.index("--thinking") + 1] == "high"

    def test_no_flags_when_nothing_was_resolved(self):
        cmd = agent.backend_pi._build_prompt_cmd()
        assert "--thinking" not in cmd
        assert "--provider" not in cmd
        assert "--model" not in cmd

    def test_prompt_forwards_every_resolved_knob(self, monkeypatch, tmp_path):
        """The dispatch layer's arguments have to survive the trip to the CLI."""
        seen = []
        monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: seen.append(cmd) or
                            subprocess.CompletedProcess(cmd, 0, "answer", ""))
        agent.backend_pi.prompt(
            "ask", cwd=str(tmp_path), model="sonnet",
            thinking="low", provider="bedrock",
        )
        cmd = seen[0]
        assert cmd[cmd.index("--model") + 1] == "sonnet"
        assert cmd[cmd.index("--thinking") + 1] == "low"
        assert cmd[cmd.index("--provider") + 1] == "bedrock"


def _detect_source() -> str:
    """detect.ts, the SDK-free half of the guard that holds its predicates."""
    return (agent.backend_pi.REVIEW_EXTENSION.parent / "detect.ts").read_text()


class TestExtensionFlag:
    def test_agent_cmd_with_extension(self):
        cmd = agent.backend_pi._build_agent_cmd(
            agent.backend_pi.AgentInvocation(prompt=""), extension="/path/to/review-guard.ts",
        )
        assert "--extension" in cmd
        idx = cmd.index("--extension")
        assert cmd[idx + 1] == "/path/to/review-guard.ts"

    def test_agent_cmd_without_extension(self):
        cmd = agent.backend_pi._build_agent_cmd(agent.backend_pi.AgentInvocation(prompt=""))
        assert "--extension" not in cmd

    def test_fix_cmd_with_extension(self):
        cmd = agent.backend_pi._build_fix_cmd(
            agent.backend_pi.AgentInvocation(prompt=""), extension="/path/to/review-guard.ts",
        )
        assert "--extension" in cmd
        idx = cmd.index("--extension")
        assert cmd[idx + 1] == "/path/to/review-guard.ts"

    def test_prompt_cmd_does_not_accept_extension(self):
        """_build_prompt_cmd intentionally omits --extension (stateless, no tool gating)."""
        import inspect
        sig = inspect.signature(agent.backend_pi._build_prompt_cmd)
        assert "extension" not in sig.parameters

    def test_review_extension_exists_on_disk(self):
        """Both call sites gate on is_file(), so a stale path drops the guard silently."""
        assert agent.backend_pi.REVIEW_EXTENSION.is_file(), (
            f"{agent.backend_pi.REVIEW_EXTENSION} is missing — the review agent would run ungated"
        )

    def test_the_guard_is_written_against_pi_s_own_api(self):
        """Existing on disk is not the same as being loadable.

        The first version of this file imported `@anthropic-ai/pi`, exported an
        object literal with an `onToolCall` method, and returned `{blocked}`.
        None of those are Pi's API — the package is not installed, the factory
        is a default-exported function, and the blocking key is `block` — so
        every review agent ran ungated while `is_file()` above passed.
        """
        source = agent.backend_pi.REVIEW_EXTENSION.read_text()
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
        assert agent.backend_pi.REVIEW_EXTENSION.parent.name == "extensions-cli"

    def test_the_guard_allows_the_dirs_the_invocation_named(self):
        """Gating on the worktree alone would refuse the review document.

        build_add_dirs returns [artifact_dir, wt_path], and the artifact dir is
        under ~/.local/state/workbench/reviews/ — outside the worktree by
        design. A guard armed with cwd alone blocks the one write every phase is
        dispatched to make, turning a fail-open into a fail-closed.
        """
        source = agent.backend_pi.REVIEW_EXTENSION.read_text()
        assert "REVIEW_ALLOWED_DIRS" in source
        assert "allowedDirs.some(" in source

    def test_the_guard_canonicalises_before_comparing(self):
        """resolve() does not follow symlinks, and /tmp is one on macOS.

        A root spelled /tmp/x against a path spelled /private/tmp/x/f names one
        directory, and a lexical relative() walks out through `..` and refuses
        the write.

        Read from detect.ts, where `canonical` and `within` live: review-guard.ts
        imports the Pi SDK and loads only inside a session, so the predicates
        moved to the sibling that plain `node` can load and the behaviour itself
        is asserted in tests/pi_extensions_review_guard.bats. What is checked here is that
        the extension still routes through them rather than comparing lexically.
        """
        source = _detect_source()
        assert "realpathSync" in source
        assert "export function within(" in source

        extension = agent.backend_pi.REVIEW_EXTENSION.read_text()
        assert "within(dir, event.input.path)" in extension

    def test_the_guard_matches_pi_s_tool_names_and_input_fields(self):
        """Pi's built-in tools are lowercase and take `path`, not `file_path`.

        The original checked for "Write"/"Edit"/"Bash" and read
        `arguments.file_path`, so it would have matched nothing even had it
        loaded.
        """
        source = agent.backend_pi.REVIEW_EXTENSION.read_text()
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
        monkeypatch.delenv("REVIEW_WORKTREE_DIR", raising=False)
        seen = {}
        monkeypatch.setattr(subprocess, "Popen", _recording_popen(seen))
        getattr(agent.backend_pi, entry_point)(agent.backend_pi.AgentInvocation(
            prompt="p", cwd=str(tmp_path), session_log=str(tmp_path / "s.jsonl"),
        ))
        assert seen["env"]["REVIEW_WORKTREE_DIR"] == str(tmp_path)

    @pytest.mark.parametrize("entry_point", ["invoke_agent", "invoke_fix"])
    def test_add_dirs_reach_the_guard(self, monkeypatch, tmp_path, entry_point):
        """The artifact dir is outside the worktree, and must still be writable."""
        monkeypatch.delenv("REVIEW_ALLOWED_DIRS", raising=False)
        artifact = tmp_path / "reviews" / "pr-42"
        worktree = tmp_path / "wt"
        seen = {}
        monkeypatch.setattr(subprocess, "Popen", _recording_popen(seen))
        getattr(agent.backend_pi, entry_point)(agent.backend_pi.AgentInvocation(
            prompt="p", cwd=str(worktree), session_log=str(tmp_path / "s.jsonl"),
            add_dirs=[str(artifact), str(worktree)],
        ))
        allowed = seen["env"]["REVIEW_ALLOWED_DIRS"].split(os.pathsep)
        assert str(artifact) in allowed
        assert str(worktree) in allowed

    @pytest.mark.parametrize("entry_point", ["invoke_agent", "invoke_fix"])
    def test_no_add_dirs_leaves_the_list_unset(self, monkeypatch, tmp_path, entry_point):
        """An empty value would split to [''] and allow a relative path anywhere."""
        monkeypatch.delenv("REVIEW_ALLOWED_DIRS", raising=False)
        seen = {}
        monkeypatch.setattr(subprocess, "Popen", _recording_popen(seen))
        getattr(agent.backend_pi, entry_point)(agent.backend_pi.AgentInvocation(
            prompt="p", cwd=str(tmp_path), session_log=str(tmp_path / "s.jsonl"),
        ))
        assert "REVIEW_ALLOWED_DIRS" not in seen["env"]


class TestPreflight:
    def test_always_passes(self):
        """Pi resolves models itself — Vertex quota is not its config surface."""
        assert agent.backend_pi.preflight({"claude-sonnet-5": ["group"]}, None) is True


class TestBareFlags:
    """The Pi equivalent of the Claude backend's --bare.

    A dispatched agent is not an interactive session: loading this machine's
    AGENTS.md cost ~39k tokens of preamble on every turn of every phase, and an
    agent handed the interactive rulebook reaches for the interactive workflow.
    """

    def test_agent_cmd_disables_context_file_discovery(self):
        cmd = agent.backend_pi._build_agent_cmd(agent.backend_pi.AgentInvocation(prompt=""))
        assert "--no-context-files" in cmd

    def test_agent_cmd_disables_skill_discovery(self):
        cmd = agent.backend_pi._build_agent_cmd(agent.backend_pi.AgentInvocation(prompt=""))
        assert "--no-skills" in cmd

    def test_fix_cmd_disables_both_too(self):
        cmd = agent.backend_pi._build_fix_cmd(agent.backend_pi.AgentInvocation(prompt=""))
        assert "--no-context-files" in cmd
        assert "--no-skills" in cmd

    def test_prompt_cmd_disables_both_too(self):
        # The gap that broke triage. A prompt is the shape that can least
        # afford the interactive rulebook: its answer is parsed, not read, and
        # the superpowers bootstrap's "announce a skill first" turns a bare-JSON
        # contract into a prose preamble with no JSON in it at all.
        cmd = agent.backend_pi._build_prompt_cmd()
        assert "--no-context-files" in cmd
        assert "--no-skills" in cmd

    def test_neither_cmd_disables_extensions(self):
        # --no-extensions would deregister the provider that serves the run and
        # strip every gh_*/web_* tool the agent list grants, leaving a review
        # with no provider and a truncated toolset.
        agent_cmd = agent.backend_pi._build_agent_cmd(agent.backend_pi.AgentInvocation(prompt=""))
        fix = agent.backend_pi._build_fix_cmd(agent.backend_pi.AgentInvocation(prompt=""))
        assert "--no-extensions" not in agent_cmd
        assert "--no-extensions" not in fix

    def test_explicit_skill_still_passed_alongside_no_skills(self, tmp_path, monkeypatch):
        # --no-skills turns off *discovery*; an explicit --skill still loads.
        # Were that not so, the agent definition would be silently dropped.
        skills = tmp_path / "skills" / "reviewer"
        skills.mkdir(parents=True)
        (skills / "SKILL.md").write_text("---\nname: reviewer\n---\nbody\n")
        monkeypatch.setattr(agent.backend_pi, "AGENTS_SKILLS_DIR", tmp_path / "skills")
        cmd = agent.backend_pi._build_agent_cmd(
            agent.backend_pi.AgentInvocation(prompt="", agent="reviewer"),
        )
        assert "--no-skills" in cmd
        assert "--skill" in cmd


def _arm(tmp_path, name="arm", body="# general\nKeep this.\n"):
    home = tmp_path / name
    (home / "rules").mkdir(parents=True)
    (home / "rules" / "general.md").write_text(body)
    return home


class TestRulesHomeIsANoop:
    """Pi does not map ``rules_home`` to a config-dir env var."""

    @pytest.mark.parametrize("entry_point", ["invoke_agent", "invoke_fix"])
    def test_rules_home_does_not_set_pi_coding_agent_dir(
            self, monkeypatch, tmp_path, entry_point):
        monkeypatch.delenv("PI_CODING_AGENT_DIR", raising=False)
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
        seen = {}
        monkeypatch.setattr(subprocess, "Popen", _recording_popen(seen))
        getattr(agent.backend_pi, entry_point)(agent.backend_pi.AgentInvocation(
            prompt="p", cwd=str(tmp_path),
            session_log=str(tmp_path / "s.jsonl"),
            rules_home=str(_arm(tmp_path)),
        ))
        assert "PI_CODING_AGENT_DIR" not in seen["env"]
        assert "CLAUDE_CONFIG_DIR" not in seen["env"]


class TestRulesHomePrefix:
    """``rules_home`` is injected as ``--append-system-prompt``, or omitted when empty."""

    def test_empty_rules_home_argv_is_byte_identical_to_today(self):
        fix = agent.backend_pi._build_fix_cmd(agent.backend_pi.AgentInvocation(prompt=""))
        agent_cmd = agent.backend_pi._build_agent_cmd(agent.backend_pi.AgentInvocation(prompt=""))
        empty_fix = agent.backend_pi._build_fix_cmd(
            agent.backend_pi.AgentInvocation(prompt="", rules_home=""),
        )
        empty_agent = agent.backend_pi._build_agent_cmd(
            agent.backend_pi.AgentInvocation(prompt="", rules_home=""),
        )
        assert fix == empty_fix
        assert agent_cmd == empty_agent
        assert fix == [
            "pi", "--mode", "rpc", "--no-session", "--approve", "--verbose",
            "--tools", agent.backend_pi.PI_FIX_TOOLS,
            "--no-context-files", "--no-skills",
        ]
        assert "--append-system-prompt" not in agent_cmd
        assert agent_cmd == [
            "pi", "--mode", "rpc", "--no-session", "--approve", "--verbose",
            "--tools", agent.backend_pi.PI_AGENT_TOOLS,
            "--no-context-files", "--no-skills",
        ]

    def test_rules_home_is_appended_as_a_file_and_keeps_bare_flags(self, tmp_path):
        home = _arm(tmp_path)
        cmd = agent.backend_pi._build_fix_cmd(
            agent.backend_pi.AgentInvocation(prompt="", rules_home=str(home)),
        )
        assert "--no-context-files" in cmd
        assert "--no-skills" in cmd
        assert "--append-system-prompt" in cmd
        blob = Path(cmd[cmd.index("--append-system-prompt") + 1])
        assert blob.is_file()
        text = blob.read_text()
        assert "Keep this." in text
        assert "general.md" in text

    def test_missing_rules_home_fails_loudly(self, tmp_path):
        from agent.rule_prefix import RulePrefixError
        with pytest.raises(RulePrefixError, match="rules/"):
            agent.backend_pi._build_fix_cmd(
                agent.backend_pi.AgentInvocation(
                    prompt="", rules_home=str(tmp_path / "missing"),
                ),
            )

    def test_relative_rules_home_is_rejected(self, tmp_path):
        from agent.rule_prefix import RulePrefixError
        with pytest.raises(RulePrefixError, match="absolute"):
            agent.backend_pi._build_fix_cmd(
                agent.backend_pi.AgentInvocation(
                    prompt="", rules_home="relative/arm",
                ),
            )

    @pytest.mark.parametrize("entry_point", ["invoke_agent", "invoke_fix"])
    def test_prefix_file_exists_at_spawn_with_the_rule_text(
            self, monkeypatch, tmp_path, entry_point):
        seen = {}

        def popen(cmd, **kwargs):
            path = Path(cmd[cmd.index("--append-system-prompt") + 1])
            seen["readable"] = path.is_file()
            seen["text"] = path.read_text()
            return _recording_popen({})(cmd, **kwargs)

        monkeypatch.setattr(subprocess, "Popen", popen)
        getattr(agent.backend_pi, entry_point)(agent.backend_pi.AgentInvocation(
            prompt="p", cwd=str(tmp_path),
            session_log=str(tmp_path / "s.jsonl"),
            rules_home=str(_arm(tmp_path)),
        ))
        assert seen["readable"] is True
        assert "Keep this." in seen["text"]
        assert "general.md" in seen["text"]

    @pytest.mark.parametrize("entry_point", ["invoke_agent", "invoke_fix"])
    def test_no_prefix_file_remains_after_invoke(
            self, monkeypatch, tmp_path, entry_point):
        seen = {}
        inner = _recording_popen(seen)

        def popen(cmd, **kwargs):
            seen["path"] = cmd[cmd.index("--append-system-prompt") + 1]
            return inner(cmd, **kwargs)

        monkeypatch.setattr(subprocess, "Popen", popen)
        getattr(agent.backend_pi, entry_point)(agent.backend_pi.AgentInvocation(
            prompt="p", cwd=str(tmp_path),
            session_log=str(tmp_path / "s.jsonl"),
            rules_home=str(_arm(tmp_path)),
        ))
        assert seen["path"]
        assert not Path(seen["path"]).exists()

    @pytest.mark.parametrize("entry_point", ["invoke_agent", "invoke_fix"])
    def test_exception_between_materialize_and_spawn_still_removes_the_file(
            self, monkeypatch, tmp_path, entry_point):
        captured = {}
        real = agent.backend_pi.materialize_rule_prefix

        def wrapping(home):
            path = real(home)
            captured["path"] = path
            return path

        monkeypatch.setattr(agent.backend_pi, "materialize_rule_prefix", wrapping)

        def boom(*_a, **_k):
            raise RuntimeError("between materialize and spawn")

        monkeypatch.setattr(agent.backend_pi, "_spawn_env", boom)
        with pytest.raises(RuntimeError, match="between materialize and spawn"):
            getattr(agent.backend_pi, entry_point)(agent.backend_pi.AgentInvocation(
                prompt="p", cwd=str(tmp_path),
                session_log=str(tmp_path / "s.jsonl"),
                rules_home=str(_arm(tmp_path)),
            ))
        assert captured["path"]
        assert not Path(captured["path"]).exists()
