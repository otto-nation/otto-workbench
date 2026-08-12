import dataclasses
import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import review_common
import review_pipeline
import review_phases
from review_common import AgentKind, Effort, Phase, Thinking


class TestPhasesRegistry:
    def test_covers_every_phase(self):
        assert set(review_phases.PHASES) == set(Phase)

    def test_key_matches_spec_phase(self):
        for phase, spec in review_phases.PHASES.items():
            assert spec.phase is phase

    def test_spec_is_frozen(self):
        spec = review_phases.PHASES[Phase.GROUP]
        with pytest.raises(dataclasses.FrozenInstanceError):
            spec.max_turns = 99

    def test_every_phase_defaults_to_sonnet(self):
        assert {s.model for s in review_phases.PHASES.values()} == {"sonnet"}


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
        actual = {p: s.thinking for p, s in review_phases.PHASES.items()}
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
        actual = {p: s.max_turns for p, s in review_phases.PHASES.items()}
        assert actual == expected


class TestPhaseAgentPins:
    """Three phases are pinned to reviewer-lite regardless of --effort.

    They receive pre-collected data and do no context gathering, so raising
    effort must not upgrade them. A change to this mapping should be a
    deliberate edit to this test, not an incidental side effect.
    """

    def test_pinned_phases(self):
        pinned = {p for p, s in review_phases.PHASES.items() if s.agent is not None}
        assert pinned == {Phase.GROUP, Phase.SCOUT, Phase.DISPROVE}

    def test_pinned_phases_use_reviewer_lite(self):
        for phase in (Phase.GROUP, Phase.SCOUT, Phase.DISPROVE):
            assert review_phases.PHASES[phase].agent is AgentKind.REVIEWER_LITE

    def test_effort_derived_phases(self):
        derived = {
            p for p, s in review_phases.PHASES.items()
            if s.agent is None and not s.edits
        }
        assert derived == {Phase.SINGLE, Phase.HOLISTIC, Phase.SYNTHESIS}

    def test_only_the_fix_phase_edits(self):
        editing = {p for p, s in review_phases.PHASES.items() if s.edits}
        assert editing == {Phase.FIX}


class TestOmittedTurnBumpRegistry:
    """Which phases pay for omitted files is a property of the spec.

    Before, it was the presence or absence of `+ _omitted_turns(job)` at each
    call site — which is how the parallel group fan-out lost its bump. Changing
    this mapping should be a deliberate edit to this test.
    """

    def test_source_reading_phases_scale(self):
        scaling = {
            p for p, s in review_phases.PHASES.items() if s.scales_with_omitted
        }
        assert scaling == {Phase.SINGLE, Phase.HOLISTIC, Phase.SCOUT, Phase.GROUP}

    def test_a_new_phase_inherits_the_bump(self):
        """The default is on, so forgetting the flag over-budgets rather than under."""
        assert review_phases.PhaseSpec(Phase.GROUP).scales_with_omitted is True


class TestPhaseLogNames:
    """Each phase's session log is named after the phase.

    Adding a phase must not mean naming its log by hand, so these assert the
    convention over the enum rather than a hand-written list. The exception
    is the pinning test: it is what proves the convention renamed nothing.
    """

    def test_preserves_current_filenames(self):
        expected = {
            Phase.SINGLE: "",
            Phase.HOLISTIC: "holistic.jsonl",
            Phase.SCOUT: "scout.jsonl",
            Phase.GROUP: "group-{}.jsonl",
            Phase.SYNTHESIS: "synthesis.jsonl",
            Phase.DISPROVE: "disprove.jsonl",
            Phase.FIX: "fix.jsonl",
        }
        assert {p: p.log_filename for p in Phase} == expected

    def test_every_phase_but_single_has_a_distinct_log(self):
        names = [p.log_filename for p in Phase if p is not Phase.SINGLE]
        assert all(names)
        assert len(set(names)) == len(names)

    def test_single_names_no_log_of_its_own(self):
        # It writes to the job's log, which the caller may point anywhere.
        assert Phase.SINGLE.log_filename == ""

    def test_group_is_the_only_indexed_phase(self):
        indexed = {p for p in Phase if "{}" in p.log_filename}
        assert indexed == {Phase.GROUP}


class TestPhaseLogPath:
    def test_derives_into_the_review_directory(self, tmp_path):
        review_file = str(tmp_path / "review.md")
        assert review_common.phase_log_path(review_file, Phase.HOLISTIC) == str(
            tmp_path / "holistic.jsonl"
        )

    def test_group_carries_its_index(self, tmp_path):
        review_file = str(tmp_path / "review.md")
        assert review_common.phase_log_path(review_file, Phase.GROUP, 3) == str(
            tmp_path / "group-3.jsonl"
        )

    def test_group_without_an_index_raises(self, tmp_path):
        # Formatting None would yield `group-None.jsonl` — a wrong file
        # rather than an error, which is the failure this change removes.
        with pytest.raises(ValueError):
            review_common.phase_log_path(str(tmp_path / "review.md"), Phase.GROUP)

    def test_single_has_no_path_of_its_own(self, tmp_path):
        assert review_common.phase_log_path(str(tmp_path / "review.md"), Phase.SINGLE) == ""


def _job(tmp_path, effort=Effort.MEDIUM):
    from review_preflight import PRContext, PRMetadata, ReviewJob

    return ReviewJob(
        repo="org/repo", pr_number="42",
        pr=PRMetadata("t", "", "head", "main", "abc123", 100, 5, 3, []),
        ctx=PRContext(), wt_path=str(tmp_path),
        review_file=str(tmp_path / "review.md"),
        session_log=str(tmp_path / "session.jsonl"),
        effort=effort,
    )


def _omitted_job(tmp_path, omitted=(), effort=Effort.MEDIUM):
    from review_preflight import PreflightData

    job = _job(tmp_path, effort)
    job.preflight = PreflightData(
        diff="", commit_log="", file_contents={}, file_permissions={},
        claude_md="", architecture_md="", omitted_files=list(omitted),
    )
    return job


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

    def test_max_turns_takes_the_omitted_bump(self, tmp_path):
        job = _omitted_job(tmp_path, omitted=["big.py", "huge.py"])
        runner = review_pipeline.PhaseRunner(job, Phase.SCOUT)
        expected = (
            review_phases.PHASES[Phase.SCOUT].max_turns
            + 2 * review_phases.OMITTED_FILE_TURNS
        )
        assert runner.max_turns == expected

    def test_max_turns_skips_the_bump_when_the_phase_opts_out(self, tmp_path):
        job = _omitted_job(tmp_path, omitted=["big.py", "huge.py"])
        runner = review_pipeline.PhaseRunner(job, Phase.DISPROVE)
        assert runner.max_turns == review_phases.PHASES[Phase.DISPROVE].max_turns

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
        runner = review_pipeline.PhaseRunner(
            _job(tmp_path, Effort.HIGH), Phase.GROUP, "/tmp/g.jsonl",
        )
        inv = runner.invocation("PROMPT", label="grp")
        assert inv.prompt == "PROMPT"
        assert inv.session_log == "/tmp/g.jsonl"
        assert inv.agent is AgentKind.REVIEWER_LITE
        assert inv.thinking is Thinking.HIGH
        assert inv.max_budget == 8.0
        assert inv.max_turns == 15
        assert inv.label == "grp"

    def test_max_turns_override(self, tmp_path):
        runner = review_pipeline.PhaseRunner(_job(tmp_path), Phase.GROUP, "/tmp/g.jsonl")
        assert runner.invocation("P", 42).max_turns == 42

    def test_session_log_defaults_to_the_jobs_own_log(self, tmp_path):
        job = _job(tmp_path)
        inv = review_pipeline.PhaseRunner(job, Phase.SINGLE).invocation("P")
        assert inv.session_log == job.session_log

    def test_add_dirs_grant_only_the_review_artifact_dir(self, tmp_path):
        # Never the shared reviews root: a root grant is how scratch files
        # ended up beside unrelated reviews.
        job = _job(tmp_path)
        runner = review_pipeline.PhaseRunner(job, Phase.SINGLE)
        assert runner.invocation("P").add_dirs == [job.artifact_dir, job.wt_path]


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

        rc = review_pipeline.PhaseRunner(job, Phase.GROUP).invoke("PROMPT")

        assert rc == 0
        assert job.throttle.waited
        inv = seen["inv"]
        assert inv.agent is AgentKind.REVIEWER_LITE
        assert inv.model == "sonnet"
        assert inv.thinking is Thinking.HIGH
        assert inv.max_budget == 8.0
        assert inv.max_turns == 15

    def test_invoke_matches_the_retry_callback_shape(self, tmp_path, monkeypatch):
        """`retry_missing_output` calls its callback as `invoke(prompt, turns)`."""
        seen = []
        monkeypatch.setattr(
            review_phases, "invoke_agent",
            lambda inv, throttle=None: seen.append(inv) or 0,
        )
        runner = review_pipeline.PhaseRunner(_job(tmp_path), Phase.GROUP, "/tmp/g.jsonl")
        runner.invoke("PROMPT", 33)
        assert seen[0].max_turns == 33
        assert seen[0].session_log == "/tmp/g.jsonl"


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


class TestAnnotationsResolve:
    """Every annotation in review_phases names the type it means.

    The module runs under PEP 563, so a wrong or stale annotation is inert
    until something reads it — `_run_skipped_groups` carried `dict` for a
    `PipelineState` and nothing noticed. `get_type_hints` reads them all.
    """

    @staticmethod
    def _own_functions():
        """Every function review_phases defines, methods included."""
        import inspect

        owned = [
            (name, obj) for name, obj in vars(review_phases).items()
            if getattr(obj, "__module__", None) == "review_phases"
        ]
        return [
            (name, obj) for name, obj in owned if inspect.isfunction(obj)
        ] + [
            (f"{name}.{method_name}", method)
            for name, cls in owned if inspect.isclass(cls)
            for method_name, method in vars(cls).items()
            if inspect.isfunction(method)
        ]

    def test_every_function_signature_resolves(self):
        import typing

        unresolved = {}
        for name, obj in self._own_functions():
            try:
                typing.get_type_hints(obj)
            except NameError as exc:
                unresolved[name] = str(exc)
        assert unresolved == {}

    def test_the_walk_reaches_the_runners_methods(self):
        names = [name for name, _ in self._own_functions()]
        assert "PhaseRunner.invoke" in names
        assert "PhaseRunner.invocation" in names

    def test_skipped_group_sweep_takes_the_pipeline_state(self):
        import typing
        hints = typing.get_type_hints(review_phases._run_skipped_groups)
        assert hints["pipeline_state"] == review_phases.PipelineState | None


def _capture_invocations(monkeypatch):
    """Record each invocation and leave a real session log behind.

    The group writes no findings, so `_review_group` takes its no-output
    branch and diagnoses the log — which has to exist. The lock is what
    makes the same fake safe for the parallel fan-out.
    """
    seen = []
    lock = threading.Lock()

    def fake_invoke(inv, throttle=None):
        with lock:
            seen.append(inv)
        Path(inv.session_log).write_text(json.dumps({
            "type": "result", "subtype": "success", "num_turns": 3,
        }) + "\n")
        return 0

    monkeypatch.setattr(review_phases, "invoke_agent", fake_invoke)
    monkeypatch.setattr(review_phases, "build_prompt", lambda *a, **k: "PROMPT")
    return seen


class TestPhaseTurnBudgets:
    """`phase_turns` is the single owner, and `PhaseRunner` reports what it says.

    Driven off the registry rather than a list of phases, so a phase added
    later is covered here without an edit.
    """

    def test_every_phase_matches_its_spec(self, tmp_path):
        job = _omitted_job(tmp_path, omitted=["big.py", "huge.py"])
        bump = 2 * review_phases.OMITTED_FILE_TURNS
        for phase, spec in review_phases.PHASES.items():
            expected = spec.max_turns + (bump if spec.scales_with_omitted else 0)
            assert review_phases.phase_turns(phase, job) == expected, phase

    def test_the_runner_reports_what_phase_turns_resolves(self, tmp_path):
        job = _omitted_job(tmp_path, omitted=["big.py"])
        for phase in review_phases.PHASES:
            runner = review_pipeline.PhaseRunner(job, phase)
            assert runner.max_turns == review_phases.phase_turns(phase, job), phase

    def test_nothing_bumps_with_no_omitted_files(self, tmp_path):
        job = _omitted_job(tmp_path)
        for phase, spec in review_phases.PHASES.items():
            assert review_phases.phase_turns(phase, job) == spec.max_turns, phase

    def test_an_opted_out_effort_bumps_nothing(self, tmp_path):
        """`--effort low` skips omitted files entirely, so no phase pays for them."""
        job = _omitted_job(tmp_path, omitted=["big.py"], effort=Effort.LOW)
        for phase, spec in review_phases.PHASES.items():
            assert review_phases.phase_turns(phase, job) == spec.max_turns, phase


class TestExecutorsUseTheResolvedBudget:
    """Each executor must hand the agent the budget its spec resolves to.

    A phase that recomputes its own budget is how the parallel group fan-out
    came to disagree with the serial one, so the assertion is against
    `phase_turns`, not against a literal.
    """

    def _first_invocation(self, monkeypatch, run):
        seen = _capture_invocations(monkeypatch)
        run()
        assert seen, "executor never reached the agent"
        return seen[0]

    def test_holistic(self, tmp_path, monkeypatch):
        job = _omitted_job(tmp_path, omitted=["big.py", "huge.py"])
        inv = self._first_invocation(
            monkeypatch, lambda: review_phases._phase_holistic(job, 3),
        )
        assert inv.max_turns == review_phases.phase_turns(Phase.HOLISTIC, job)

    def test_scout(self, tmp_path, monkeypatch):
        job = _omitted_job(tmp_path, omitted=["big.py", "huge.py"])
        inv = self._first_invocation(
            monkeypatch, lambda: review_phases._phase_scout(job, 3),
        )
        assert inv.max_turns == review_phases.phase_turns(Phase.SCOUT, job)

    def test_disprove_does_not_pay_for_omitted_files(self, tmp_path, monkeypatch):
        job = _omitted_job(tmp_path, omitted=["big.py", "huge.py"])
        Path(job.review_file).write_text("- [ ] **[M1]** must fix something\n")
        inv = self._first_invocation(
            monkeypatch, lambda: review_phases._phase_disprove(job),
        )
        assert inv.max_turns == review_phases.PHASES[Phase.DISPROVE].max_turns


class TestGroupTurnBudget:
    """The group budget is resolved when the group runs, not when the module loads."""

    def _run(self, job, **kwargs):
        from review_preflight import Group

        return review_phases._review_group(
            1, Group(name="g1", files=["a.py"], lines=10),
            job, 1, "holistic", **kwargs,
        )

    def test_default_budget_includes_the_omitted_file_bump(self, tmp_path, monkeypatch):
        seen = _capture_invocations(monkeypatch)
        self._run(_omitted_job(tmp_path, omitted=["big.py", "huge.py"]))
        expected = review_phases.PHASES[Phase.GROUP].max_turns + 2 * review_phases.OMITTED_FILE_TURNS
        assert seen[0].max_turns == expected

    def test_default_budget_is_the_phase_budget_with_nothing_omitted(self, tmp_path, monkeypatch):
        seen = _capture_invocations(monkeypatch)
        self._run(_omitted_job(tmp_path))
        assert seen[0].max_turns == review_phases.PHASES[Phase.GROUP].max_turns

    def test_explicit_budget_still_wins(self, tmp_path, monkeypatch):
        seen = _capture_invocations(monkeypatch)
        self._run(_omitted_job(tmp_path, omitted=["big.py"]), max_turns=99)
        assert seen[0].max_turns == 99

    def test_budget_follows_the_registry_at_call_time(self, tmp_path, monkeypatch):
        """An import-time default would freeze the old value here."""
        seen = _capture_invocations(monkeypatch)
        monkeypatch.setitem(
            review_phases.PHASES, Phase.GROUP,
            dataclasses.replace(review_phases.PHASES[Phase.GROUP], max_turns=99),
        )
        self._run(_omitted_job(tmp_path))
        assert seen[0].max_turns == 99


class TestParallelGroupTurnBudget:
    """The parallel fan-out must resolve the same default budget as the
    serial path — it forwards no `max_turns` of its own."""

    def test_parallel_groups_get_the_default_budget(self, tmp_path, monkeypatch):
        from review_preflight import Group

        seen = _capture_invocations(monkeypatch)
        job = _omitted_job(tmp_path, omitted=["big.py"])
        groups = [
            Group(name="g1", files=["a.py"], lines=10),
            Group(name="g2", files=["b.py"], lines=10),
        ]
        review_phases._run_parallel_reviews(
            groups, job, len(groups), "holistic", workers=2,
            skip_groups=None, pipeline_state=None,
        )

        expected = review_phases.PHASES[Phase.GROUP].max_turns + review_phases.OMITTED_FILE_TURNS
        assert len(seen) == 2
        assert all(inv.max_turns == expected for inv in seen)
