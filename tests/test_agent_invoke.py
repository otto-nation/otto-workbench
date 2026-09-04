"""Tests for agent_invoke — the one owner of an agent invocation.

``agent_registry`` says what a phase is set to and ``agent_phases`` says what it
resolves to; here the subject is what the three runners do with that. Chiefly:
that each phase reaches only the runner its shape names, that what the phase
resolved to is what the backend is told, and that a runner spends nothing the
shape has no use for.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

from agent import invoke as agent_invoke
from agent import phases as agent_phases
from agent import backend as ai_backend
from core.phases import Phase, PhaseShape


def _answers(*replies):
    """A backend stub returning each reply in turn, recording the prompts it saw."""
    seen = []
    remaining = list(replies)

    def prompt(text, **kwargs):
        seen.append((text, kwargs))
        return remaining.pop(0) if remaining else ("", 0)

    return prompt, seen


@pytest.fixture
def backend(monkeypatch):
    """Install a prompt stub and hand back the calls it recorded."""

    def install(*replies):
        prompt, seen = _answers(*replies)
        monkeypatch.setattr(ai_backend, "prompt", prompt)
        return seen

    return install


class TestRunPromptResult:
    def test_reports_the_answer_and_its_verdict(self, backend, tmp_path):
        backend(("the answer", 0))
        result = agent_invoke.run_prompt(
            Phase.DESCRIBE, "ask", cwd=tmp_path, usable=lambda t: True,
        )
        assert result == agent_invoke.PromptResult("the answer", 0, True)
        assert result.ok

    def test_an_unusable_answer_is_not_ok(self, backend, tmp_path):
        backend(("nonsense", 0), ("nonsense", 0))
        result = agent_invoke.run_prompt(
            Phase.DESCRIBE, "ask", cwd=tmp_path, usable=lambda t: False,
        )
        assert result.exit_code == 0
        assert not result.usable
        assert not result.ok

    def test_a_failed_call_is_never_usable(self, backend, tmp_path):
        """There is no answer to judge, whatever the predicate would say of "".

        Reporting usable=True on a failure would let a caller reading `.usable`
        consume the empty string the backend returned with its error.
        """
        backend(("", 1))
        result = agent_invoke.run_prompt(
            Phase.DESCRIBE, "ask", cwd=tmp_path, usable=lambda t: True,
        )
        assert not result.usable
        assert not result.ok

    def test_the_verdict_judges_the_answer_that_came_back(self, backend, tmp_path):
        """After a retry that is the second answer, which is the one returned.

        A verdict computed against the first would call the result unusable
        while handing the caller an answer that parses perfectly well.
        """
        backend(("nonsense", 0), ("42", 0))
        result = agent_invoke.run_prompt(
            Phase.DESCRIBE, "ask", cwd=tmp_path, usable=lambda t: t.isdigit(),
        )
        assert (result.text, result.usable) == ("42", True)


class TestRunPromptRetry:
    def test_an_unparseable_answer_earns_one_retry(self, backend, tmp_path):
        seen = backend(("nonsense", 0), ("42", 0))
        result = agent_invoke.run_prompt(
            Phase.DESCRIBE, "ask", cwd=tmp_path, usable=lambda t: t.isdigit(),
        )
        assert result.text == "42"
        assert result.ok
        assert len(seen) == 2

    def test_the_retry_carries_the_original_prompt(self, backend, tmp_path):
        seen = backend(("nonsense", 0), ("42", 0))
        agent_invoke.run_prompt(
            Phase.DESCRIBE, "ask", cwd=tmp_path, usable=lambda t: t.isdigit(),
        )
        assert seen[1][0].endswith("ask")
        assert seen[1][0] != "ask"

    def test_a_failed_call_is_not_retried(self, backend, tmp_path):
        """The backend already reported why, and the same call would reproduce it."""
        seen = backend(("", 1))
        agent_invoke.run_prompt(
            Phase.DESCRIBE, "ask", cwd=tmp_path, usable=lambda t: t.isdigit(),
        )
        assert len(seen) == 1


class TestRunPromptResolution:
    """What the phase resolved to is what the backend is told."""

    def _kwargs(self, backend, tmp_path, **kw):
        seen = backend(("ok", 0))
        agent_invoke.run_prompt(
            Phase.DESCRIBE, "ask", cwd=tmp_path, usable=lambda t: True, **kw,
        )
        return seen[0][1]

    def test_passes_the_resolved_model_and_thinking_level(self, backend, tmp_path):
        kwargs = self._kwargs(backend, tmp_path)
        assert kwargs["model"] == agent_phases.phase_model(Phase.DESCRIBE, None)
        assert kwargs["thinking"] == agent_phases.phase_thinking(Phase.DESCRIBE)
        assert kwargs["provider"] == agent_phases.phase_provider()

    def test_an_env_override_reaches_the_backend(self, backend, tmp_path, monkeypatch):
        """Being a phase is what earns these calls an operator-movable model."""
        monkeypatch.setenv(Phase.DESCRIBE.model_env_key, "claude-haiku-4-5")
        assert self._kwargs(backend, tmp_path)["model"] == "claude-haiku-4-5"

    def test_the_working_directory_is_passed_as_a_string(self, backend, tmp_path):
        # ai_backend stats it and the CLI is spawned in it; a Path would reach
        # neither as itself.
        assert self._kwargs(backend, tmp_path)["cwd"] == str(tmp_path)

    def test_the_ledger_task_defaults_to_the_phase(self, backend, tmp_path):
        assert self._kwargs(backend, tmp_path)["task"] == "describe"

    def test_a_caller_may_bill_to_a_narrower_task(self, backend, tmp_path):
        # One rebase phase spans six of these, so the ledger separates them.
        kwargs = self._kwargs(backend, tmp_path, task="lockfile-regen")
        assert kwargs["task"] == "lockfile-regen"

    def test_the_pr_coordinates_are_forwarded(self, backend, tmp_path):
        kwargs = self._kwargs(backend, tmp_path, repo="o/r", pr="7")
        assert (kwargs["repo"], kwargs["pr"]) == ("o/r", "7")


class TestRunPromptSpendsNothingItCannotUse:
    """A stateless call has no turn loop and no cap the pipeline enforces.

    ``test_agent_registry`` pins no ``max_turns`` or ``max_budget`` for a
    prompt-shaped phase on the grounds that nothing reads them. This is what
    makes that true — without it, the registry's silence would only mean the
    fields were never looked at *yet*.
    """

    @pytest.mark.parametrize("resolver", ["phase_turns", "phase_budget"])
    def test_neither_sizing_resolver_is_consulted(
        self, backend, tmp_path, monkeypatch, resolver,
    ):
        def refuse(*args, **kwargs):
            raise AssertionError(f"run_prompt consulted {resolver}")

        monkeypatch.setattr(agent_phases, resolver, refuse)
        backend(("ok", 0))
        agent_invoke.run_prompt(
            Phase.DESCRIBE, "ask", cwd=tmp_path, usable=lambda t: True,
        )

    def test_the_backend_is_told_no_turn_or_budget_ceiling(self, backend, tmp_path):
        seen = backend(("ok", 0))
        agent_invoke.run_prompt(
            Phase.DESCRIBE, "ask", cwd=tmp_path, usable=lambda t: True,
        )
        assert "max_turns" not in seen[0][1]
        assert "max_budget" not in seen[0][1]


class TestShapeGuards:
    """A phase reaches exactly the runner its spec names.

    The three entry points differ in what they hand the agent — tools, write
    access to the branch, neither — so running a phase through the wrong one
    grants it powers nobody declared. The error names both shapes because the
    fix is always one of the two: change the call, or change the spec.
    """

    @pytest.mark.parametrize("phase", [
        p for p, s in agent_invoke.PHASES.items() if s.shape is not PhaseShape.PROMPT
    ])
    def test_run_prompt_refuses_a_phase_of_another_shape(self, phase, tmp_path):
        with pytest.raises(ValueError, match="run_prompt"):
            agent_invoke.run_prompt(
                phase, "ask", cwd=tmp_path, usable=lambda t: True,
            )

    @pytest.mark.parametrize("phase", [
        p for p, s in agent_invoke.PHASES.items() if s.shape is not PhaseShape.FIX
    ])
    def test_run_fix_refuses_a_phase_of_another_shape(self, phase, tmp_path):
        with pytest.raises(ValueError, match="run_fix"):
            agent_invoke.run_fix(
                phase, "fix it", cwd=tmp_path,
                session_log=str(tmp_path / "s.jsonl"), produced=None,
            )

    def test_the_refusal_names_the_shape_the_spec_gave(self, tmp_path):
        with pytest.raises(ValueError, match="prompt"):
            agent_invoke.run_fix(
                Phase.DESCRIBE, "fix it", cwd=tmp_path,
                session_log=str(tmp_path / "s.jsonl"), produced=None,
            )

    def test_a_guard_rejects_before_the_backend_is_reached(self, monkeypatch, tmp_path):
        def refuse(*args, **kwargs):
            raise AssertionError("the backend was reached for a mis-shaped phase")

        monkeypatch.setattr(ai_backend, "prompt", refuse)
        with pytest.raises(ValueError):
            agent_invoke.run_prompt(
                Phase.FIX, "ask", cwd=tmp_path, usable=lambda t: True,
            )


class TestRunFixWithoutAGuard:
    """``produced=None`` is a caller with no signal that work landed."""

    def _invocation(self, monkeypatch, calls):
        monkeypatch.setattr(
            ai_backend, "invoke_fix",
            lambda inv: calls.append(inv) or 0,
        )

    def test_the_pass_runs_once_and_is_not_retried(self, monkeypatch, tmp_path):
        calls = []
        self._invocation(monkeypatch, calls)
        result = agent_invoke.run_fix(
            Phase.CI_FIX, "fix it", cwd=tmp_path,
            session_log=str(tmp_path / "s.jsonl"), produced=None,
        )
        assert len(calls) == 1
        assert result == agent_invoke.FixResult(0, None)
        assert result.ok

    def test_the_phase_sizes_the_pass_when_the_caller_does_not(
        self, monkeypatch, tmp_path,
    ):
        calls = []
        self._invocation(monkeypatch, calls)
        agent_invoke.run_fix(
            Phase.CI_FIX, "fix it", cwd=tmp_path,
            session_log=str(tmp_path / "s.jsonl"), produced=None,
        )
        assert calls[0].max_turns == agent_phases.phase_turns(Phase.CI_FIX)
        assert calls[0].max_budget == agent_phases.phase_budget(Phase.CI_FIX)

    def test_a_caller_that_sized_the_pass_itself_keeps_its_numbers(
        self, monkeypatch, tmp_path,
    ):
        """It already put the turn count in the prompt; the two must agree."""
        calls = []
        self._invocation(monkeypatch, calls)
        agent_invoke.run_fix(
            Phase.CI_FIX, "fix it", cwd=tmp_path,
            session_log=str(tmp_path / "s.jsonl"), produced=None,
            max_turns=7, max_budget=0.25,
        )
        assert (calls[0].max_turns, calls[0].max_budget) == (7, 0.25)

    def test_the_worktree_is_the_only_directory_granted_by_default(
        self, monkeypatch, tmp_path,
    ):
        calls = []
        self._invocation(monkeypatch, calls)
        agent_invoke.run_fix(
            Phase.CI_FIX, "fix it", cwd=tmp_path,
            session_log=str(tmp_path / "s.jsonl"), produced=None,
        )
        assert calls[0].add_dirs == [str(tmp_path)]


class TestRunAgentQuotaRetry:
    """A 429 is the one failure worth trying again unchanged."""

    def _agent(self, monkeypatch, codes, *, quota=True):
        calls = []
        remaining = list(codes)
        monkeypatch.setattr(
            ai_backend, "invoke_agent",
            lambda inv: calls.append(inv) or remaining.pop(0),
        )
        monkeypatch.setattr(agent_invoke.review_agent, "is_quota_error",
                            lambda log_path: quota)
        monkeypatch.setattr(agent_invoke.time, "sleep", lambda seconds: None)
        return calls

    def _inv(self, tmp_path, model="sonnet"):
        return ai_backend.AgentInvocation(
            prompt="p", cwd=str(tmp_path), model=model,
            session_log=str(tmp_path / "s.jsonl"),
        )

    def test_a_quota_failure_is_retried_once(self, monkeypatch, tmp_path):
        calls = self._agent(monkeypatch, [1, 0])
        assert agent_invoke.run_agent(self._inv(tmp_path)) == 0
        assert len(calls) == 2

    def test_any_other_failure_is_returned_as_is(self, monkeypatch, tmp_path):
        calls = self._agent(monkeypatch, [1], quota=False)
        assert agent_invoke.run_agent(self._inv(tmp_path)) == 1
        assert len(calls) == 1

    def test_a_success_is_not_retried(self, monkeypatch, tmp_path):
        calls = self._agent(monkeypatch, [0])
        assert agent_invoke.run_agent(self._inv(tmp_path)) == 0
        assert len(calls) == 1

    def test_a_shared_throttle_holds_the_other_agents_back(
        self, monkeypatch, tmp_path,
    ):
        self._agent(monkeypatch, [1, 0])
        throttle = agent_invoke.QuotaThrottle()
        agent_invoke.run_agent(self._inv(tmp_path), throttle=throttle)
        # The backoff the failure set is what a sibling agent waits out.
        assert throttle._resume_at > 0
