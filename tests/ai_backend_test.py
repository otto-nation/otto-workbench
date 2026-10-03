"""Tests for ai_backend — dispatch, invocation shape, and usage ledger emission."""

import importlib
import io
import json
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import agent.backend
import agent.usage
import git.client


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(agent.usage, "_warned", False)
    return tmp_path / agent.usage.LEDGER_DIRNAME


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
    monkeypatch.setattr(agent.backend, "_get_module", lambda: mod)
    return mod


class TestBackendSelection:
    """Nothing is assumed. Both CLIs are plausible, so an unselected backend is
    unknown rather than one vendor's, and dispatch says so instead of picking.
    """

    @pytest.fixture(autouse=True)
    def _nothing_selects_a_backend(self, monkeypatch):
        """Undo conftest's pin: this class is about the selection itself.

        The suite-wide ``_pinned_backend`` sets AI_BACKEND so no other test
        depends on the machine's config. Here the absence is the subject, so
        both layers are cleared and each test sets back only what it is about.
        """
        monkeypatch.delenv("AI_BACKEND", raising=False)
        monkeypatch.setattr(agent.backend, "_configured_backend", lambda: None)

    def test_nothing_selected_is_none(self):
        assert agent.backend.selected_backend() is None

    def test_reads_env(self, monkeypatch):
        monkeypatch.setenv("AI_BACKEND", "pi")
        assert agent.backend.selected_backend() is agent.backend.Backend.PI

    def test_unrecognised_backend_is_not_a_fallback(self, monkeypatch):
        """A typo meant something specific and did not get it."""
        monkeypatch.setenv("AI_BACKEND", "not-a-backend")
        assert agent.backend.selected_backend() is None

    def test_empty_string_is_not_a_selection(self, monkeypatch):
        """`export AI_BACKEND=` is a real shape, and it selects nothing."""
        monkeypatch.setenv("AI_BACKEND", "")
        assert agent.backend.selected_backend() is None

    def test_empty_string_falls_through_to_config(self, monkeypatch):
        """Empty is falsy, unlike an unrecognised value: config still gets asked."""
        monkeypatch.setenv("AI_BACKEND", "")
        monkeypatch.setattr(
            agent.backend, "_configured_backend", lambda: agent.backend.Backend.PI,
        )
        assert agent.backend.selected_backend() is agent.backend.Backend.PI

    def test_config_supplies_the_backend_when_the_env_is_silent(self, monkeypatch):
        monkeypatch.delenv("AI_BACKEND", raising=False)
        monkeypatch.setattr(
            agent.backend, "_configured_backend", lambda: agent.backend.Backend.PI,
        )
        assert agent.backend.selected_backend() is agent.backend.Backend.PI

    def test_env_beats_config(self, monkeypatch):
        monkeypatch.setenv("AI_BACKEND", "claude")
        monkeypatch.setattr(
            agent.backend, "_configured_backend", lambda: agent.backend.Backend.PI,
        )
        assert agent.backend.selected_backend() is agent.backend.Backend.CLAUDE

    def test_dispatch_names_both_ways_to_set_it(self, monkeypatch):
        monkeypatch.delenv("AI_BACKEND", raising=False)
        with pytest.raises(agent.backend.BackendNotSelected) as exc:
            agent.backend._get_module()
        assert "AI_BACKEND" in str(exc.value)
        assert "agent.backend" in str(exc.value)

    def test_dispatch_names_the_invalid_value_on_a_typo(self, monkeypatch):
        """A typo'd AI_BACKEND is configured, just wrong — say what was set."""
        monkeypatch.setenv("AI_BACKEND", "cluade")
        with pytest.raises(agent.backend.BackendNotSelected, match="cluade"):
            agent.backend._get_module()

    def test_dispatch_names_the_invalid_value_from_config(self, tmp_path, monkeypatch):
        """A typo'd agent.backend is configured, just wrong — say what was set.

        load_config_or_default() cannot tell this apart from a config that
        never mentioned backend at all: serde's enum coercion raises on
        "cluade", load_config wraps that in ConfigError, and
        load_config_or_default swallows it into a bare-default config. The
        message has to come from the raw config dict instead.
        """
        monkeypatch.setenv("WORKBENCH_CONFIG_DIR", str(tmp_path))
        (tmp_path / "config.yml").write_text("agent:\n  backend: cluade\n")
        with pytest.raises(agent.backend.BackendNotSelected, match="cluade"):
            agent.backend._get_module()

    def test_is_available_is_false_rather_than_raising(self, monkeypatch):
        """The rebase paths ask this to decide whether to offer AI at all."""
        monkeypatch.delenv("AI_BACKEND", raising=False)
        assert agent.backend.is_available() is False

    def test_or_claude_passes_through_a_real_selection(self, monkeypatch):
        """The fallback only applies when nothing is selected."""
        monkeypatch.setenv("AI_BACKEND", "pi")
        assert agent.backend.selected_backend_or_claude() is agent.backend.Backend.PI

    def test_or_claude_falls_back_when_nothing_is_selected(self):
        """The shared fallback ``agent.templates`` and ``agent.retry`` both use."""
        assert agent.backend.selected_backend_or_claude() is agent.backend.Backend.CLAUDE


class TestPreflightDispatch:
    def test_routes_to_claude_backend(self, monkeypatch):
        monkeypatch.setenv("AI_BACKEND", "claude")
        import agent.backend_claude
        monkeypatch.setattr(agent.backend_claude, "preflight", lambda models, trail: False)
        assert agent.backend.preflight({"claude-sonnet-5": ["group"]}, MagicMock()) is False

    def test_pi_backend_skips_vertex_quota(self, monkeypatch):
        """Regression: Vertex env left exported must not abort a Pi run."""
        monkeypatch.setenv("AI_BACKEND", "pi")
        monkeypatch.setenv("CLAUDE_CODE_USE_VERTEX", "1")
        monkeypatch.setenv("ANTHROPIC_VERTEX_PROJECT_ID", "proj")
        monkeypatch.setenv("CLOUD_ML_REGION", "us-east5")

        import agent.vertex_quota

        def _unreachable(*args, **kwargs):
            pytest.fail("Pi run reached the Vertex quota API")

        monkeypatch.setattr(agent.vertex_quota, "check_quota", _unreachable)
        assert agent.backend.preflight({"claude-sonnet-5": ["group"]}, MagicMock()) is True


class TestAgentInvocation:
    def test_all_backends_accept_the_same_object(self):
        """One object, three modules — a reordered field cannot misbind."""
        import inspect

        import agent.backend
        import agent.backend_claude
        import agent.backend_pi

        for mod in (agent.backend, agent.backend_claude, agent.backend_pi):
            for fn_name in ("invoke_agent", "invoke_fix"):
                params = list(inspect.signature(getattr(mod, fn_name)).parameters)
                assert params == ["inv"], f"{mod.__name__}.{fn_name} takes {params}"

    def test_defaults_leave_every_optional_field_unset(self):
        import agent.backend

        inv = agent.backend.AgentInvocation(prompt="hi")
        assert inv.cwd == ""
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
        assert inv.env is None
        assert inv.rules_home == ""

    def test_is_frozen(self):
        import dataclasses

        import agent.backend
        import pytest

        inv = agent.backend.AgentInvocation(prompt="hi")
        with pytest.raises(dataclasses.FrozenInstanceError):
            inv.prompt = "bye"

    def test_add_dirs_are_not_shared_between_instances(self):
        import agent.backend

        a = agent.backend.AgentInvocation(prompt="a")
        b = agent.backend.AgentInvocation(prompt="b")
        a.add_dirs.append("/tmp")
        assert b.add_dirs == []


class TestInvokeAgentRecords:
    def test_records_usage_from_session_log(
        self, ledger, fake_backend, tmp_path, monkeypatch,
    ):
        # The ledger stamps whichever backend is selected, so the selection has
        # to be explicit now that nothing defaults it.
        monkeypatch.setenv("AI_BACKEND", "claude")
        log = tmp_path / "session.jsonl"
        _session_log(log)
        agent.backend.invoke_agent(agent.backend.AgentInvocation(
            prompt="p", cwd=str(tmp_path), session_log=str(log),
            model="claude-sonnet-4-6",
        ))
        rec = _records(ledger)[0]
        assert rec["entry_point"] == "agent"
        assert rec["backend"] == "claude"
        assert rec["input_tokens"] == 100
        assert rec["cache_read_tokens"] == 5000
        assert rec["cost"] == pytest.approx(1.0)

    def test_an_unselected_backend_still_records_the_cost(
        self, ledger, fake_backend, tmp_path, monkeypatch,
    ):
        """The stamp is telemetry, and telemetry must not raise.

        _record swallows everything it throws, so raising on an unselected
        backend here would drop the ledger row silently rather than failing
        loudly — the opposite of what the selection change is for. The spend
        happened; it is recorded against an unknown backend.
        """
        monkeypatch.delenv("AI_BACKEND", raising=False)
        monkeypatch.setattr(agent.backend, "_configured_backend", lambda: None)
        log = tmp_path / "session.jsonl"
        _session_log(log)
        agent.backend.invoke_agent(agent.backend.AgentInvocation(
            prompt="p", cwd=str(tmp_path), session_log=str(log),
        ))
        rec = _records(ledger)[0]
        assert rec["backend"] == "unknown"
        assert rec["cost"] == pytest.approx(1.0)
        assert rec["exit_code"] == 0

    def test_records_on_nonzero_exit(self, ledger, fake_backend, tmp_path):
        """Failed calls cost money too — an unmeasured failure mode stays invisible."""
        log = tmp_path / "session.jsonl"
        _session_log(log)
        fake_backend.exit_code = 1
        agent.backend.invoke_agent(agent.backend.AgentInvocation(
            prompt="p", cwd=str(tmp_path), session_log=str(log),
        ))
        rec = _records(ledger)[0]
        assert rec["exit_code"] == 1
        assert rec["cost"] == pytest.approx(1.0)

    def test_records_task_label(self, ledger, fake_backend, tmp_path):
        log = tmp_path / "session.jsonl"
        _session_log(log)
        agent.backend.invoke_agent(agent.backend.AgentInvocation(
            prompt="p", cwd=str(tmp_path), session_log=str(log), task="review-group",
        ))
        assert _records(ledger)[0]["task"] == "review-group"

    def test_backend_receives_the_invocation_unchanged(self, ledger, fake_backend, tmp_path):
        """Ledger labels ride along on the invocation; the backend still sees one object."""
        log = tmp_path / "session.jsonl"
        _session_log(log)
        inv = agent.backend.AgentInvocation(
            prompt="p", cwd=str(tmp_path), session_log=str(log), task="review-group",
        )
        agent.backend.invoke_agent(inv)
        assert fake_backend.calls == [("invoke_agent", inv)]

    def test_returns_backend_exit_code(self, ledger, fake_backend, tmp_path):
        log = tmp_path / "session.jsonl"
        _session_log(log)
        fake_backend.exit_code = 42
        inv = agent.backend.AgentInvocation(
            prompt="p", cwd=str(tmp_path), session_log=str(log),
        )
        assert agent.backend.invoke_agent(inv) == 42

    def test_missing_session_log_records_nothing(self, ledger, fake_backend, tmp_path):
        """No usable usage source is better recorded as absent than as zero."""
        agent.backend.invoke_agent(agent.backend.AgentInvocation(
            prompt="p", cwd=str(tmp_path),
            session_log=str(tmp_path / "never-written.jsonl"),
        ))
        assert _records(ledger) == []


class TestInvokeFixRecords:
    def test_records_when_session_log_written(self, ledger, fake_backend, tmp_path):
        log = tmp_path / "fix.jsonl"
        _session_log(log, cost=0.25, input_tokens=10)
        agent.backend.invoke_fix(agent.backend.AgentInvocation(
            prompt="p", cwd=str(tmp_path), session_log=str(log),
        ))
        rec = _records(ledger)[0]
        assert rec["entry_point"] == "fix"
        assert rec["cost"] == pytest.approx(0.25)

    def test_no_session_log_records_nothing(self, ledger, fake_backend, tmp_path):
        agent.backend.invoke_fix(agent.backend.AgentInvocation(prompt="p", cwd=str(tmp_path)))
        assert _records(ledger) == []


class TestLedgerFailureIsolation:
    def test_ledger_error_does_not_break_the_call(self, fake_backend, tmp_path, monkeypatch):
        log = tmp_path / "session.jsonl"
        _session_log(log)

        def boom(**kwargs):
            raise RuntimeError("ledger exploded")

        monkeypatch.setattr(agent.usage, "record", boom)
        inv = agent.backend.AgentInvocation(
            prompt="p", cwd=str(tmp_path), session_log=str(log),
        )
        assert agent.backend.invoke_agent(inv) == 0


class TestScriptName:
    def test_defaults_to_argv0_basename(self, ledger, fake_backend, tmp_path, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["/usr/local/bin/pr-rebase", "--fix"])
        log = tmp_path / "session.jsonl"
        _session_log(log)
        agent.backend.invoke_agent(agent.backend.AgentInvocation(
            prompt="p", cwd=str(tmp_path), session_log=str(log),
        ))
        assert _records(ledger)[0]["script"] == "pr-rebase"


class TestCwdIsRequired:
    """An AI call with no cwd runs in whichever worktree launched the process.

    On a bare repo with sibling worktrees that is another live branch. A
    `pr rebase --fix` targeting one worktree ran bats in another and truncated
    its copy of pr-rebase to 431 lines.
    """

    def test_invoke_agent_refuses_an_empty_cwd(self, fake_backend):
        with pytest.raises(ValueError, match="requires a non-empty cwd"):
            agent.backend.invoke_agent(agent.backend.AgentInvocation(prompt="p"))
        assert fake_backend.calls == [], "backend was reached despite the guard"

    def test_invoke_fix_refuses_an_empty_cwd(self, fake_backend):
        with pytest.raises(ValueError, match="requires a non-empty cwd"):
            agent.backend.invoke_fix(agent.backend.AgentInvocation(prompt="p"))
        assert fake_backend.calls == []

    def test_prompt_refuses_an_empty_cwd(self, fake_backend):
        with pytest.raises(ValueError, match="requires a non-empty cwd"):
            agent.backend.prompt("hi", cwd="")
        assert fake_backend.calls == []

    def test_prompt_requires_cwd_as_a_keyword(self):
        """No positional slot to fill by accident, and no default to inherit."""
        import inspect

        param = inspect.signature(agent.backend.prompt).parameters["cwd"]
        assert param.kind is inspect.Parameter.KEYWORD_ONLY
        assert param.default is inspect.Parameter.empty

    @pytest.mark.parametrize("call", [
        lambda cwd: agent.backend.prompt("hi", cwd=cwd),
        lambda cwd: agent.backend.invoke_agent(
            agent.backend.AgentInvocation(prompt="p", cwd=cwd),
        ),
        lambda cwd: agent.backend.invoke_fix(
            agent.backend.AgentInvocation(prompt="p", cwd=cwd),
        ),
    ], ids=["prompt", "invoke_agent", "invoke_fix"])
    def test_a_nonexistent_cwd_is_named_rather_than_inherited(
        self, fake_backend, tmp_path, call,
    ):
        """A typo'd path must not fall through to Popen's inherited directory."""
        with pytest.raises(ValueError, match="not a directory"):
            call(str(tmp_path / "gone"))
        assert fake_backend.calls == []

    def test_prompt_forwards_cwd_to_the_backend(self, fake_backend, tmp_path):
        agent.backend.prompt("hi", cwd=str(tmp_path))
        assert fake_backend.calls[0][2]["cwd"] == str(tmp_path)


class TestBackendsRunInTheGivenDirectory:
    """The cwd must reach subprocess, not just the invocation object."""

    def test_claude_prompt_passes_cwd_to_subprocess(self, monkeypatch, tmp_path):
        import agent.backend_claude

        seen = {}

        def fake_run(cmd, **kwargs):
            seen.update(kwargs)
            return subprocess.CompletedProcess(cmd, 0, '{"result": "ok"}', "")

        monkeypatch.setattr(subprocess, "run", fake_run)
        agent.backend_claude.prompt("hi", cwd=str(tmp_path))
        assert seen["cwd"] == str(tmp_path)

    def test_pi_prompt_passes_cwd_to_subprocess(self, monkeypatch, tmp_path):
        import agent.backend_pi

        seen = {}

        def fake_run(cmd, **kwargs):
            seen.update(kwargs)
            return subprocess.CompletedProcess(cmd, 0, "ok", "")

        monkeypatch.setattr(subprocess, "run", fake_run)
        agent.backend_pi.prompt("hi", cwd=str(tmp_path))
        assert seen["cwd"] == str(tmp_path)

    @pytest.mark.parametrize("entry_point", ["invoke_agent", "invoke_fix"])
    def test_claude_agents_run_in_the_invocation_cwd(
        self, monkeypatch, tmp_path, entry_point,
    ):
        import agent.backend_claude

        seen = {}

        class FakeProc:
            returncode = 0
            stdin = io.StringIO()
            stdout = io.StringIO("")
            stderr = io.StringIO("")

            def wait(self, timeout=None):
                return 0

        def fake_popen(cmd, **kwargs):
            seen.update(kwargs)
            return FakeProc()

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        log = tmp_path / "s.jsonl"
        getattr(agent.backend_claude, entry_point)(agent.backend.AgentInvocation(
            prompt="p", cwd=str(tmp_path), session_log=str(log),
        ))
        assert seen["cwd"] == str(tmp_path)


def _recording_popen(seen):
    """A Popen stand-in that records its kwargs and streams nothing back.

    ``wait`` takes Popen's own ``timeout`` because a backend is entitled to
    bound it; a fake that accepts only the bare call fails every caller that
    does, for a reason about the fake rather than about the backend. The
    context-manager methods are here for the same reason: a real Popen is one,
    and a backend that enters it is using the API as documented.
    """

    class FakeProc:
        returncode = 0
        stdin = io.StringIO()
        stdout = io.StringIO("")
        stderr = io.StringIO("")

        def wait(self, timeout=None):
            return 0

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

    def popen(cmd, **kwargs):
        seen.update(kwargs)
        return FakeProc()

    return popen


class TestAgentsAreRecordedForTheStopHandler:
    """Every agent a backend starts goes through `core.children`.

    The stop handler can only stop what it was told about. A backend that
    spawned with `subprocess.Popen` directly would run fine and pass every
    other test here, then outlive a killed review.
    """

    @pytest.mark.parametrize("backend", ["agent.backend_claude", "agent.backend_pi"])
    @pytest.mark.parametrize("entry_point", ["invoke_agent", "invoke_fix"])
    def test_the_agent_is_spawned_through_the_registry(
        self, monkeypatch, tmp_path, backend, entry_point,
    ):
        import core.children

        module = importlib.import_module(backend)
        monkeypatch.setattr(subprocess, "Popen", _recording_popen({}))
        spawned = []
        real_spawn = core.children.spawn

        def recording_spawn(cmd, **kwargs):
            spawned.append(cmd)
            return real_spawn(cmd, **kwargs)

        monkeypatch.setattr(core.children, "spawn", recording_spawn)
        getattr(module, entry_point)(agent.backend.AgentInvocation(
            prompt="p", cwd=str(tmp_path), session_log=str(tmp_path / "s.jsonl"),
        ))
        assert len(spawned) == 1
        assert core.children.live() == [], "the agent was never forgotten"


class TestBackendsGetTheInvocationEnv:
    """The env must reach subprocess, not just the invocation object.

    The skill eval hands its recording shims to the agent through `env["PATH"]`
    and has no other way in. A backend that drops the field runs the driven
    session against the real binaries: nothing is traced, and the case scores
    zero for a reason its own trace cannot explain.
    """

    # The Pi backend attaches the review guard to these two entry points and
    # adds the roots it gates on, so its env is the invocation's plus those.
    # Both backends additionally pin the editor variables — see
    # `TestAgentsCannotBeHandedAnEditor`.

    @pytest.mark.parametrize("entry_point", ["invoke_agent", "invoke_fix"])
    def test_claude_gets_the_invocation_env_with_only_the_editors_added(
        self, monkeypatch, tmp_path, entry_point,
    ):
        module = importlib.import_module("agent.backend_claude")
        seen = {}
        monkeypatch.setattr(subprocess, "Popen", _recording_popen(seen))
        getattr(module, entry_point)(agent.backend.AgentInvocation(
            prompt="p", cwd=str(tmp_path),
            session_log=str(tmp_path / "s.jsonl"),
            env={"PATH": "/stub:/usr/bin"},
        ))
        assert seen["env"]["PATH"] == "/stub:/usr/bin"
        assert set(seen["env"]) == {"PATH", *git.client.EDITOR_VARS}

    @pytest.mark.parametrize("entry_point", ["invoke_agent", "invoke_fix"])
    def test_pi_extends_the_invocation_env_without_replacing_it(
        self, monkeypatch, tmp_path, entry_point,
    ):
        """The guard's roots are added; the caller's own keys survive.

        Merging over os.environ instead would put the real PATH behind the
        eval's shims and score every driven case against the live binaries.
        """
        module = importlib.import_module("agent.backend_pi")
        seen = {}
        monkeypatch.setattr(subprocess, "Popen", _recording_popen(seen))
        getattr(module, entry_point)(agent.backend.AgentInvocation(
            prompt="p", cwd=str(tmp_path),
            session_log=str(tmp_path / "s.jsonl"),
            env={"PATH": "/stub:/usr/bin"},
        ))
        assert seen["env"]["PATH"] == "/stub:/usr/bin"
        assert seen["env"]["REVIEW_WORKTREE_DIR"] == str(tmp_path)
        assert set(seen["env"]) == {
            "PATH", "REVIEW_WORKTREE_DIR", *git.client.EDITOR_VARS,
        }

    @pytest.mark.parametrize("entry_point", ["invoke_agent", "invoke_fix"])
    def test_claude_inherits_when_env_is_unset(
        self, monkeypatch, tmp_path, entry_point,
    ):
        """None means inherit — the field must not turn every call into a scrub."""
        monkeypatch.setenv("A_PARENT_VAR", "kept")
        module = importlib.import_module("agent.backend_claude")
        seen = {}
        monkeypatch.setattr(subprocess, "Popen", _recording_popen(seen))
        getattr(module, entry_point)(agent.backend.AgentInvocation(
            prompt="p", cwd=str(tmp_path),
            session_log=str(tmp_path / "s.jsonl"),
        ))
        assert seen["env"]["A_PARENT_VAR"] == "kept"

    @pytest.mark.parametrize("entry_point", ["invoke_agent", "invoke_fix"])
    def test_pi_inherits_the_parent_env_when_unset(
        self, monkeypatch, tmp_path, entry_point,
    ):
        """Unset still inherits: os.environ plus the guard's roots, not a scrub."""
        monkeypatch.setenv("A_PARENT_VAR", "kept")
        module = importlib.import_module("agent.backend_pi")
        seen = {}
        monkeypatch.setattr(subprocess, "Popen", _recording_popen(seen))
        getattr(module, entry_point)(agent.backend.AgentInvocation(
            prompt="p", cwd=str(tmp_path),
            session_log=str(tmp_path / "s.jsonl"),
        ))
        assert seen["env"]["A_PARENT_VAR"] == "kept"
        assert seen["env"]["REVIEW_WORKTREE_DIR"] == str(tmp_path)


BACKENDS = ["agent.backend_claude", "agent.backend_pi"]


class TestAgentsCannotBeHandedAnEditor:
    """No agent subprocess inherits an editor, on either backend.

    An agent holds a shell, so it reaches git with argv this process never
    chose. `-c core.editor=true` covers the git calls the rebase driver makes
    and none of the ones an agent makes for itself: a resolver ran
    `git rebase --edit-todo` from a tool call, git opened the operator's
    `GIT_EDITOR` on a pipe with no terminal, and the run blocked for 45 minutes
    until the job timeout killed it.
    """

    @pytest.mark.parametrize("module_name", BACKENDS)
    @pytest.mark.parametrize("entry_point", ["invoke_agent", "invoke_fix"])
    def test_an_inherited_editor_is_overridden(
        self, monkeypatch, tmp_path, module_name, entry_point,
    ):
        for var in git.client.EDITOR_VARS:
            monkeypatch.setenv(var, "vim")
        module = importlib.import_module(module_name)
        seen = {}
        monkeypatch.setattr(subprocess, "Popen", _recording_popen(seen))
        getattr(module, entry_point)(agent.backend.AgentInvocation(
            prompt="p", cwd=str(tmp_path),
            session_log=str(tmp_path / "s.jsonl"),
        ))
        for var in git.client.EDITOR_VARS:
            assert seen["env"][var] == git.client.NO_EDITOR

    @pytest.mark.parametrize("module_name", BACKENDS)
    @pytest.mark.parametrize("entry_point", ["invoke_agent", "invoke_fix"])
    def test_a_caller_supplied_env_is_pinned_too(
        self, monkeypatch, tmp_path, module_name, entry_point,
    ):
        """The eval harness builds a whole env; it must not be the way back in."""
        module = importlib.import_module(module_name)
        seen = {}
        monkeypatch.setattr(subprocess, "Popen", _recording_popen(seen))
        getattr(module, entry_point)(agent.backend.AgentInvocation(
            prompt="p", cwd=str(tmp_path),
            session_log=str(tmp_path / "s.jsonl"),
            env={"PATH": "/stub", "GIT_EDITOR": "vim"},
        ))
        assert seen["env"]["GIT_EDITOR"] == git.client.NO_EDITOR
        assert seen["env"]["PATH"] == "/stub"


class TestBuildAddDirs:
    def test_artifact_dir_and_worktree_only(self):
        import agent.session

        dirs = agent.session.build_add_dirs("/wt", "/reviews/pr-42")
        assert dirs == ["/reviews/pr-42", "/wt"]
