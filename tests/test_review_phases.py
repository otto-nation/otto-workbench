import dataclasses
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import review_pipeline
from review_common import AgentKind, Effort, Phase, Thinking


class TestPhasesRegistry:
    def test_covers_every_phase(self):
        assert set(review_pipeline.PHASES) == set(Phase)

    def test_key_matches_spec_phase(self):
        for phase, spec in review_pipeline.PHASES.items():
            assert spec.phase is phase

    def test_spec_is_frozen(self):
        spec = review_pipeline.PHASES[Phase.GROUP]
        with pytest.raises(dataclasses.FrozenInstanceError):
            spec.max_turns = 99

    def test_every_phase_defaults_to_sonnet(self):
        assert {s.model for s in review_pipeline.PHASES.values()} == {"sonnet"}


class TestPhaseThinkingDefaults:
    def test_preserves_current_levels(self):
        expected = {
            Phase.SINGLE: Thinking.MEDIUM,
            Phase.HOLISTIC: Thinking.MEDIUM,
            Phase.SCOUT: Thinking.LOW,
            Phase.GROUP: Thinking.LOW,
            Phase.SYNTHESIS: Thinking.MEDIUM,
            Phase.DISPROVE: Thinking.MEDIUM,
            Phase.FIX: Thinking.LOW,
        }
        actual = {p: s.thinking for p, s in review_pipeline.PHASES.items()}
        assert actual == expected


class TestPhaseMaxTurnsDefaults:
    def test_preserves_current_budgets(self):
        expected = {
            Phase.SINGLE: 15,
            Phase.HOLISTIC: 15,
            Phase.SCOUT: 10,
            Phase.GROUP: 15,
            Phase.SYNTHESIS: 15,
            Phase.DISPROVE: 15,
            Phase.FIX: 20,
        }
        actual = {p: s.max_turns for p, s in review_pipeline.PHASES.items()}
        assert actual == expected


class TestPhaseAgentPins:
    """Three phases are pinned to reviewer-lite regardless of --effort.

    They receive pre-collected data and do no context gathering, so raising
    effort must not upgrade them. A change to this mapping should be a
    deliberate edit to this test, not an incidental side effect.
    """

    def test_pinned_phases(self):
        pinned = {p for p, s in review_pipeline.PHASES.items() if s.agent is not None}
        assert pinned == {Phase.GROUP, Phase.SCOUT, Phase.DISPROVE}

    def test_pinned_phases_use_reviewer_lite(self):
        for phase in (Phase.GROUP, Phase.SCOUT, Phase.DISPROVE):
            assert review_pipeline.PHASES[phase].agent is AgentKind.REVIEWER_LITE

    def test_effort_derived_phases(self):
        derived = {
            p for p, s in review_pipeline.PHASES.items()
            if s.agent is None and not s.edits
        }
        assert derived == {Phase.SINGLE, Phase.HOLISTIC, Phase.SYNTHESIS}

    def test_only_the_fix_phase_edits(self):
        editing = {p for p, s in review_pipeline.PHASES.items() if s.edits}
        assert editing == {Phase.FIX}


def _job(tmp_path, effort=Effort.MEDIUM):
    from review_preflight import PRContext, PRMetadata, ReviewJob

    return ReviewJob(
        repo="org/repo", pr_number="42",
        pr=PRMetadata("t", "", "head", "main", "abc123", 100, 5, 3, []),
        ctx=PRContext(), wt_path=str(tmp_path),
        review_file=str(tmp_path / "review.md"),
        session_log=str(tmp_path / "session.jsonl"),
        reviews_dir=str(tmp_path),
        effort=effort,
    )


class TestPhaseRunnerResolution:
    def test_pinned_phase_ignores_effort(self, tmp_path):
        for effort in Effort:
            runner = review_pipeline.PhaseRunner(_job(tmp_path, effort), Phase.GROUP)
            assert runner.agent is AgentKind.REVIEWER_LITE

    def test_editing_phase_takes_no_agent_at_any_effort(self, tmp_path):
        for effort in Effort:
            runner = review_pipeline.PhaseRunner(_job(tmp_path, effort), Phase.FIX)
            assert runner.agent is None

    def test_unpinned_phase_follows_effort(self, tmp_path):
        low = review_pipeline.PhaseRunner(_job(tmp_path, Effort.LOW), Phase.HOLISTIC)
        assert low.agent is AgentKind.REVIEWER_LITE
        high = review_pipeline.PhaseRunner(_job(tmp_path, Effort.HIGH), Phase.HOLISTIC)
        assert high.agent is AgentKind.REVIEWER

    def test_budget_comes_from_effort(self, tmp_path):
        runner = review_pipeline.PhaseRunner(_job(tmp_path, Effort.HIGH), Phase.SCOUT)
        assert runner.budget == 8.0

    def test_thinking_prefers_effort_override(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CLAUDE_REVIEW_GROUP_THINKING", raising=False)
        monkeypatch.delenv("CLAUDE_REVIEW_THINKING", raising=False)
        runner = review_pipeline.PhaseRunner(_job(tmp_path, Effort.HIGH), Phase.GROUP)
        assert runner.thinking is Thinking.HIGH

    def test_thinking_falls_back_to_phase_default(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CLAUDE_REVIEW_GROUP_THINKING", raising=False)
        monkeypatch.delenv("CLAUDE_REVIEW_THINKING", raising=False)
        runner = review_pipeline.PhaseRunner(_job(tmp_path, Effort.MEDIUM), Phase.GROUP)
        assert runner.thinking is Thinking.LOW

    def test_max_turns_comes_from_phase(self, tmp_path):
        runner = review_pipeline.PhaseRunner(_job(tmp_path, Effort.MEDIUM), Phase.SCOUT)
        assert runner.max_turns == 10

    def test_provider_reads_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_REVIEW_PROVIDER", "vertex")
        runner = review_pipeline.PhaseRunner(_job(tmp_path), Phase.GROUP)
        assert runner.provider == "vertex"

    def test_model_reads_phase_env_key(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_REVIEW_SCOUT_MODEL", "claude-haiku-4-5")
        runner = review_pipeline.PhaseRunner(_job(tmp_path), Phase.SCOUT)
        assert runner.model == "claude-haiku-4-5"

    def test_thinking_reads_phase_env_key(self, tmp_path, monkeypatch):
        # Every existing call site resolved thinking through
        # _resolve_thinking_level, which layers a per-phase (and a global)
        # env override on top of the effort/phase default — PhaseRunner must
        # keep that layering rather than reading _phase_thinking() bare.
        monkeypatch.setenv("CLAUDE_REVIEW_GROUP_THINKING", "xhigh")
        runner = review_pipeline.PhaseRunner(_job(tmp_path), Phase.GROUP)
        assert runner.thinking == "xhigh"

    def test_thinking_reads_global_env_key(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CLAUDE_REVIEW_GROUP_THINKING", raising=False)
        monkeypatch.setenv("CLAUDE_REVIEW_THINKING", "xhigh")
        runner = review_pipeline.PhaseRunner(_job(tmp_path), Phase.GROUP)
        assert runner.thinking == "xhigh"


class TestPhaseRunnerInvocation:
    def test_carries_resolved_values(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CLAUDE_REVIEW_GROUP_THINKING", raising=False)
        monkeypatch.delenv("CLAUDE_REVIEW_THINKING", raising=False)
        runner = review_pipeline.PhaseRunner(_job(tmp_path, Effort.HIGH), Phase.GROUP)
        inv = runner.invocation("PROMPT", "/tmp/g.jsonl", label="grp")
        assert inv.prompt == "PROMPT"
        assert inv.session_log == "/tmp/g.jsonl"
        assert inv.agent is AgentKind.REVIEWER_LITE
        assert inv.thinking is Thinking.HIGH
        assert inv.max_budget == 8.0
        assert inv.max_turns == 15
        assert inv.label == "grp"

    def test_max_turns_override(self, tmp_path):
        runner = review_pipeline.PhaseRunner(_job(tmp_path), Phase.GROUP)
        inv = runner.invocation("P", "/tmp/g.jsonl", max_turns=42)
        assert inv.max_turns == 42

    def test_review_file_widens_add_dirs(self, tmp_path):
        job = _job(tmp_path)
        runner = review_pipeline.PhaseRunner(job, Phase.SINGLE)
        inv = runner.invocation(
            "P", job.session_log, review_file="/elsewhere/review.md",
        )
        assert "/elsewhere" in inv.add_dirs


class TestPhaseRunnerReachesBackend:
    """PhaseRunner.invoke() must reach ai_backend.invoke_agent with the
    fully-resolved AgentInvocation and must wait on the job's throttle.

    Every other pipeline test stubs review_pipeline.invoke_agent (or
    review_agent.invoke_agent), which swallows any argument shape. Patching
    one layer deeper, at ai_backend.invoke_agent, keeps the seam between
    PhaseRunner and the backend under test.
    """

    class _RecordingThrottle:
        def __init__(self):
            self.waited = False

        def wait_if_needed(self):
            self.waited = True

    def test_invoke_forwards_invocation_and_throttle(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CLAUDE_REVIEW_GROUP_THINKING", raising=False)
        monkeypatch.delenv("CLAUDE_REVIEW_THINKING", raising=False)
        monkeypatch.delenv("CLAUDE_REVIEW_GROUP_MODEL", raising=False)
        monkeypatch.delenv("CLAUDE_REVIEW_MODEL", raising=False)
        monkeypatch.delenv("ANTHROPIC_DEFAULT_SONNET_MODEL", raising=False)

        import review_agent

        seen = {}

        def fake_backend_invoke(inv):
            seen["inv"] = inv
            return 0

        monkeypatch.setattr(review_agent.ai_backend, "invoke_agent", fake_backend_invoke)

        job = _job(tmp_path, Effort.HIGH)
        job.throttle = self._RecordingThrottle()

        rc = review_pipeline.PhaseRunner(job, Phase.GROUP).invoke(
            "PROMPT", job.session_log,
        )

        assert rc == 0
        assert job.throttle.waited
        inv = seen["inv"]
        assert inv.agent is AgentKind.REVIEWER_LITE
        assert inv.model == "sonnet"
        assert inv.thinking is Thinking.HIGH
        assert inv.max_budget == 8.0
        assert inv.max_turns == 15


class TestNoDuplicateDefaults:
    """One owner per default. A second copy drifts silently."""

    def test_max_cost_defined_once(self):
        import review_preflight

        assert not hasattr(review_preflight, "DEFAULT_MAX_COST")
        assert hasattr(review_pipeline, "DEFAULT_MAX_COST")

    def test_turn_and_budget_defaults_not_in_preflight(self):
        import review_preflight

        for name in (
            "DEFAULT_MAX_TURNS",
            "DEFAULT_MAX_TURNS_GROUP",
            "DEFAULT_MAX_TURNS_HOLISTIC",
            "DEFAULT_MAX_TURNS_SYNTHESIS",
            "DEFAULT_MAX_TURNS_SINGLE",
            "DEFAULT_MAX_BUDGET_PER_AGENT",
        ):
            assert not hasattr(review_preflight, name), f"{name} is a stale copy"
