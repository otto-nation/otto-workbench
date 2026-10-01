import dataclasses
import json
import os
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import agent.phases
import core.job_slots
import review.paths
import review.pipeline
import review.phases
import review.steps
from agent.registry import REVIEW_PHASES
from core.phases import AgentKind, Effort, Phase, Thinking
import agent.backend


class TestPhaseLogPath:
    def test_derives_into_the_review_directory(self, tmp_path):
        review_file = str(tmp_path / "review.md")
        assert review.paths.phase_log_path(review_file, Phase.HOLISTIC) == str(
            tmp_path / "holistic.jsonl"
        )

    def test_group_carries_its_index(self, tmp_path):
        review_file = str(tmp_path / "review.md")
        assert review.paths.phase_log_path(review_file, Phase.GROUP, 3) == str(
            tmp_path / "group-3.jsonl"
        )

    def test_group_without_an_index_raises(self, tmp_path):
        # Formatting None would yield `group-None.jsonl` — a wrong file
        # rather than an error, which is the failure this change removes.
        with pytest.raises(ValueError):
            review.paths.phase_log_path(str(tmp_path / "review.md"), Phase.GROUP)

    def test_single_has_no_path_of_its_own(self, tmp_path):
        assert review.paths.phase_log_path(str(tmp_path / "review.md"), Phase.SINGLE) == ""

    def test_non_indexed_phase_with_an_index_raises(self, tmp_path):
        # Formatting would ignore the index silently — `scout.jsonl` either
        # way — which is the same wrong-file failure the indexed case above
        # already raises on.
        with pytest.raises(ValueError):
            review.paths.phase_log_path(str(tmp_path / "review.md"), Phase.SCOUT, 3)


class TestPhaseOutputPath:
    def test_derives_into_the_review_directory(self, tmp_path):
        review_file = str(tmp_path / "review.md")
        assert review.paths.phase_output_path(review_file, Phase.HOLISTIC) == str(
            tmp_path / "holistic.md"
        )

    def test_group_carries_its_index(self, tmp_path):
        review_file = str(tmp_path / "review.md")
        assert review.paths.phase_output_path(review_file, Phase.GROUP, 2) == str(
            tmp_path / "group-2.md"
        )

    def test_group_without_an_index_raises(self, tmp_path):
        with pytest.raises(ValueError):
            review.paths.phase_output_path(str(tmp_path / "review.md"), Phase.GROUP)

    def test_non_indexed_phase_with_an_index_raises(self, tmp_path):
        with pytest.raises(ValueError):
            review.paths.phase_output_path(
                str(tmp_path / "review.md"), Phase.DISPROVE, 3
            )

    def test_a_phase_with_no_artifact_raises(self, tmp_path):
        # Unlike phase_log_path there is no caller-side fallback, and an
        # empty name would derive to the review *directory* — a wrong path
        # that reads as a real one.
        for phase in (Phase.SYNTHESIS, Phase.SINGLE, Phase.FIX):
            with pytest.raises(ValueError):
                review.paths.phase_output_path(str(tmp_path / "review.md"), phase)


def _job(tmp_path, effort=Effort.MEDIUM):
    from gh.types import PRContext, PRMetadata
    from review.types import ReviewJob

    return ReviewJob(
        repo="org/repo", pr_number="42",
        pr=PRMetadata("t", "", "head", "main", "abc123", 100, 5, 3, []),
        ctx=PRContext(), wt_path=str(tmp_path),
        review_file=str(tmp_path / "review.md"),
        session_log=str(tmp_path / "session.jsonl"),
        effort=effort,
    )


def _omitted_job(tmp_path, omitted=(), effort=Effort.MEDIUM):
    from review.types import PreflightData

    job = _job(tmp_path, effort)
    job.preflight = PreflightData(
        diff="", commit_log="", file_contents={}, file_permissions={},
        claude_md="", architecture_md="", omitted_files=list(omitted),
    )
    return job


class TestPhaseRunnerResolution:
    def test_pinned_phase_ignores_effort(self, tmp_path):
        for effort in Effort:
            runner = review.pipeline.PhaseRunner(_job(tmp_path, effort), Phase.GROUP, 1)
            assert runner.agent is AgentKind.REVIEWER_LITE

    def test_editing_phase_takes_no_agent_at_any_effort(self, tmp_path):
        for effort in Effort:
            runner = review.pipeline.PhaseRunner(_job(tmp_path, effort), Phase.FIX)
            assert runner.agent is None

    def test_unpinned_phase_follows_effort(self, tmp_path):
        low = review.pipeline.PhaseRunner(_job(tmp_path, Effort.LOW), Phase.HOLISTIC)
        assert low.agent is AgentKind.REVIEWER_LITE
        high = review.pipeline.PhaseRunner(_job(tmp_path, Effort.HIGH), Phase.HOLISTIC)
        assert high.agent is AgentKind.REVIEWER

    def test_budget_comes_from_effort(self, tmp_path):
        runner = review.pipeline.PhaseRunner(_job(tmp_path, Effort.HIGH), Phase.SCOUT)
        assert runner.budget == 8.0

    def test_thinking_prefers_effort_override(self, tmp_path, monkeypatch):
        monkeypatch.delenv("WORKBENCH_AI_GROUP_THINKING", raising=False)
        monkeypatch.delenv("WORKBENCH_AI_THINKING", raising=False)
        runner = review.pipeline.PhaseRunner(_job(tmp_path, Effort.HIGH), Phase.GROUP, 1)
        assert runner.thinking is Thinking.HIGH

    def test_thinking_falls_back_to_phase_default(self, tmp_path, monkeypatch):
        monkeypatch.delenv("WORKBENCH_AI_GROUP_THINKING", raising=False)
        monkeypatch.delenv("WORKBENCH_AI_THINKING", raising=False)
        runner = review.pipeline.PhaseRunner(_job(tmp_path, Effort.MEDIUM), Phase.GROUP, 1)
        assert runner.thinking is Thinking.LOW

    def test_max_turns_comes_from_phase(self, tmp_path):
        runner = review.pipeline.PhaseRunner(_job(tmp_path, Effort.MEDIUM), Phase.SCOUT)
        assert runner.max_turns == 10

    def test_max_turns_takes_the_omitted_bump(self, tmp_path):
        job = _omitted_job(tmp_path, omitted=["big.py", "huge.py"])
        runner = review.pipeline.PhaseRunner(job, Phase.SCOUT)
        expected = (
            review.phases.PHASES[Phase.SCOUT].max_turns
            + 2 * agent.phases.OMITTED_FILE_TURNS
        )
        assert runner.max_turns == expected

    def test_max_turns_skips_the_bump_when_the_phase_opts_out(self, tmp_path):
        job = _omitted_job(tmp_path, omitted=["big.py", "huge.py"])
        runner = review.pipeline.PhaseRunner(job, Phase.DISPROVE)
        assert runner.max_turns == review.phases.PHASES[Phase.DISPROVE].max_turns

    def test_provider_reads_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WORKBENCH_AI_PROVIDER", "vertex")
        runner = review.pipeline.PhaseRunner(_job(tmp_path), Phase.GROUP, 1)
        assert runner.provider == "vertex"

    def test_model_reads_phase_env_key(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WORKBENCH_AI_SCOUT_MODEL", "claude-haiku-4-5")
        runner = review.pipeline.PhaseRunner(_job(tmp_path), Phase.SCOUT)
        assert runner.model == "claude-haiku-4-5"

    def test_thinking_reads_phase_env_key(self, tmp_path, monkeypatch):
        # Every existing call site resolved thinking through
        # _resolve_thinking_level, which layers a per-phase (and a global)
        # env override on top of the effort/phase default — PhaseRunner must
        # keep that layering rather than reading _phase_thinking() bare.
        monkeypatch.setenv("WORKBENCH_AI_GROUP_THINKING", "xhigh")
        runner = review.pipeline.PhaseRunner(_job(tmp_path), Phase.GROUP, 1)
        assert runner.thinking == "xhigh"

    def test_thinking_reads_global_env_key(self, tmp_path, monkeypatch):
        monkeypatch.delenv("WORKBENCH_AI_GROUP_THINKING", raising=False)
        monkeypatch.setenv("WORKBENCH_AI_THINKING", "xhigh")
        runner = review.pipeline.PhaseRunner(_job(tmp_path), Phase.GROUP, 1)
        assert runner.thinking == "xhigh"


class TestPhaseRunnerInvocation:
    def test_carries_resolved_values(self, tmp_path, monkeypatch):
        monkeypatch.delenv("WORKBENCH_AI_GROUP_THINKING", raising=False)
        monkeypatch.delenv("WORKBENCH_AI_THINKING", raising=False)
        runner = review.pipeline.PhaseRunner(
            _job(tmp_path, Effort.HIGH), Phase.GROUP, 1,
        )
        inv = runner.invocation("PROMPT", label="grp")
        assert inv.prompt == "PROMPT"
        assert inv.session_log == str(tmp_path / "group-1.jsonl")
        assert inv.agent is AgentKind.REVIEWER_LITE
        assert inv.thinking is Thinking.HIGH
        assert inv.max_budget == 8.0
        # Read from the resolver rather than pinned: the subject here is that
        # PhaseRunner forwards the resolved budget, not what the arithmetic
        # makes it. `phase_turns` owns that, and test_agent_phases asserts it.
        assert inv.max_turns == agent.phases.phase_turns(Phase.GROUP, Effort.HIGH)
        assert inv.label == "grp"

    def test_max_turns_override(self, tmp_path):
        runner = review.pipeline.PhaseRunner(_job(tmp_path), Phase.GROUP, 1)
        assert runner.invocation("P", 42).max_turns == 42

    def test_add_dirs_grant_only_the_review_artifact_dir(self, tmp_path):
        # Never the shared reviews root: a root grant is how scratch files
        # ended up beside unrelated reviews.
        job = _job(tmp_path)
        runner = review.pipeline.PhaseRunner(job, Phase.SINGLE)
        assert runner.invocation("P").add_dirs == [job.artifact_dir, job.wt_path]

    def test_log_comes_from_the_phase(self, tmp_path):
        runner = review.pipeline.PhaseRunner(_job(tmp_path), Phase.HOLISTIC)
        assert runner.session_log == str(tmp_path / "holistic.jsonl")

    def test_group_log_carries_the_index(self, tmp_path):
        runner = review.pipeline.PhaseRunner(_job(tmp_path), Phase.GROUP, 3)
        assert runner.session_log == str(tmp_path / "group-3.jsonl")

    def test_group_without_an_index_raises(self, tmp_path):
        with pytest.raises(ValueError):
            review.pipeline.PhaseRunner(_job(tmp_path), Phase.GROUP)

    def test_single_honours_a_session_log_outside_the_review_dir(self, tmp_path):
        # `review-orchestrate --session-log` may point anywhere. Deriving the
        # log from the phase must not override the caller's choice.
        job = _job(tmp_path)
        job.session_log = str(tmp_path / "elsewhere" / "custom.jsonl")
        runner = review.pipeline.PhaseRunner(job, Phase.SINGLE)
        assert runner.session_log == job.session_log


class TestPhaseRunnerReachesBackend:
    """PhaseRunner.invoke() must reach ai_backend.invoke_agent with the
    fully-resolved AgentInvocation and must wait on the job's throttle.

    Every other pipeline test stubs review_pipeline.run_agent, which swallows
    any argument shape. Patching one layer deeper, at ai_backend.invoke_agent,
    keeps the seam between PhaseRunner and the backend under test.
    """

    class _RecordingThrottle:
        def __init__(self):
            self.waited = False

        def wait_if_needed(self):
            self.waited = True

    def test_invoke_forwards_invocation_and_throttle(self, tmp_path, monkeypatch):
        monkeypatch.delenv("WORKBENCH_AI_GROUP_THINKING", raising=False)
        monkeypatch.delenv("WORKBENCH_AI_THINKING", raising=False)
        monkeypatch.delenv("WORKBENCH_AI_GROUP_MODEL", raising=False)
        monkeypatch.delenv("WORKBENCH_AI_MODEL", raising=False)
        monkeypatch.delenv("ANTHROPIC_DEFAULT_SONNET_MODEL", raising=False)

        import agent.invoke

        seen = {}

        def fake_backend_invoke(inv):
            seen["inv"] = inv
            return 0

        monkeypatch.setattr(agent.backend, "invoke_agent", fake_backend_invoke)

        job = _job(tmp_path, Effort.HIGH)
        job.throttle = self._RecordingThrottle()

        rc = review.pipeline.PhaseRunner(job, Phase.GROUP, 1).invoke("PROMPT")

        assert rc == 0
        assert job.throttle.waited
        inv = seen["inv"]
        assert inv.agent is AgentKind.REVIEWER_LITE
        assert inv.model == "sonnet"
        assert inv.thinking is Thinking.HIGH
        assert inv.max_budget == 8.0
        # As above: the seam under test is what reaches the backend, and the
        # turn budget it carries is `phase_turns`' answer for this effort.
        assert inv.max_turns == agent.phases.phase_turns(Phase.GROUP, Effort.HIGH)

    def test_invoke_matches_the_retry_callback_shape(self, tmp_path, monkeypatch):
        """`retry_missing_output` calls its callback as `invoke(prompt, turns)`."""
        seen = []
        monkeypatch.setattr(
            review.phases, "run_agent",
            lambda inv, throttle=None: seen.append(inv) or 0,
        )
        runner = review.pipeline.PhaseRunner(_job(tmp_path), Phase.GROUP, 1)
        runner.invoke("PROMPT", 33)
        assert seen[0].max_turns == 33
        assert seen[0].session_log == str(tmp_path / "group-1.jsonl")


class TestNoDuplicateDefaults:
    """One owner per default. A second copy drifts silently."""

    def test_run_wide_defaults_live_with_the_pipeline_that_spends_them(self):
        # Both bound what one run may consume, and `review-orchestrate` offers
        # both as flags off these values — a second copy elsewhere would answer
        # for whichever import a caller reached first.
        assert hasattr(review.pipeline, "DEFAULT_MAX_COST")
        assert hasattr(review.pipeline, "DEFAULT_MAX_PARALLEL")

    def test_phase_artifact_names_are_not_also_constants(self):
        # `PhaseSpec.log_filename` and `PhaseSpec.output_filename` are the one
        # owner each. A second module-level string holding the same value — under
        # any name — would be a second owner, and the two would drift.
        artifact_names = {
            name
            for p in REVIEW_PHASES
            for name in (
                review.phases.PHASES[p].log_filename,
                review.phases.PHASES[p].output_filename,
            )
            if name
        }
        duplicates = {
            f"{mod.__name__}.{name}"
            for mod in (review.paths,)
            for name, value in vars(mod).items()
            if isinstance(value, str) and value in artifact_names
        }
        assert duplicates == set()


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
            (name, obj) for name, obj in vars(review.phases).items()
            if getattr(obj, "__module__", None) == "review.phases"
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
        hints = typing.get_type_hints(review.phases._run_skipped_groups)
        assert hints["pipeline_state"] == review.phases.PipelineState | None


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

    monkeypatch.setattr(review.phases, "run_agent", fake_invoke)
    monkeypatch.setattr(review.phases, "build_prompt", lambda *a, **k: "PROMPT")
    return seen


class TestPhaseTurnBudgets:
    """`job_turns` is the single owner, and `PhaseRunner` reports what it says.

    Driven off the review domain rather than a list of phases, so a review
    phase added later is covered here without an edit. A phase belonging to
    another entry point has no `ReviewJob` to be sized against.
    """

    def test_every_phase_matches_its_spec(self, tmp_path):
        job = _omitted_job(tmp_path, omitted=["big.py", "huge.py"])
        bump = 2 * agent.phases.OMITTED_FILE_TURNS
        for phase in REVIEW_PHASES:
            spec = review.phases.PHASES[phase]
            expected = spec.max_turns + (bump if spec.scales_with_omitted else 0)
            assert review.phases.job_turns(phase, job) == expected, phase

    def test_the_runner_reports_what_job_turns_resolves(self, tmp_path):
        job = _omitted_job(tmp_path, omitted=["big.py"])
        for phase in REVIEW_PHASES:
            # A fan-out phase derives its log from an index, so it needs one.
            index = 1 if "{}" in review.phases.PHASES[phase].log_filename else None
            runner = review.pipeline.PhaseRunner(job, phase, index)
            assert runner.max_turns == review.phases.job_turns(phase, job), phase

    def test_nothing_bumps_with_no_omitted_files(self, tmp_path):
        job = _omitted_job(tmp_path)
        for phase in REVIEW_PHASES:
            spec = review.phases.PHASES[phase]
            assert review.phases.job_turns(phase, job) == spec.max_turns, phase

    def test_an_opted_out_effort_bumps_nothing(self, tmp_path):
        """`--effort low` skips omitted files entirely, so no phase pays for them."""
        job = _omitted_job(tmp_path, omitted=["big.py"], effort=Effort.LOW)
        for phase in REVIEW_PHASES:
            spec = review.phases.PHASES[phase]
            assert review.phases.job_turns(phase, job) == spec.max_turns, phase


class TestExecutorsUseTheResolvedBudget:
    """Each executor must hand the agent the budget its spec resolves to.

    A phase that recomputes its own budget is how the parallel group fan-out
    came to disagree with the serial one, so the assertion is against
    `job_turns`, not against a literal.
    """

    def _first_invocation(self, monkeypatch, run):
        seen = _capture_invocations(monkeypatch)
        run()
        assert seen, "executor never reached the agent"
        return seen[0]

    @pytest.mark.parametrize("phase", [Phase.HOLISTIC, Phase.SCOUT])
    def test_a_scan_takes_the_budget_of_the_phase_it_was_handed(
        self, phase, tmp_path, monkeypatch,
    ):
        job = _omitted_job(tmp_path, omitted=["big.py", "huge.py"])
        inv = self._first_invocation(
            monkeypatch, lambda: review.phases.run_phase(job, phase, "scanning..."),
        )
        assert inv.max_turns == review.phases.job_turns(phase, job)

    def test_disprove_does_not_pay_for_omitted_files(self, tmp_path, monkeypatch):
        job = _omitted_job(tmp_path, omitted=["big.py", "huge.py"])
        Path(job.review_file).write_text(
            "## Must fix\n- [ ] **[M1]** must fix something\n")
        inv = self._first_invocation(
            monkeypatch, lambda: review.steps._phase_disprove(job),
        )
        assert inv.max_turns == review.phases.PHASES[Phase.DISPROVE].max_turns


class TestGroupTurnBudget:
    """The group budget is resolved when the group runs, not when the module loads."""

    def _run(self, job, **kwargs):
        from review.types import Group

        return review.phases._review_group(
            1, Group(name="g1", files=["a.py"], lines=10),
            job, 1, "holistic", **kwargs,
        )

    def test_default_budget_includes_the_omitted_file_bump(self, tmp_path, monkeypatch):
        seen = _capture_invocations(monkeypatch)
        self._run(_omitted_job(tmp_path, omitted=["big.py", "huge.py"]))
        expected = review.phases.PHASES[Phase.GROUP].max_turns + 2 * agent.phases.OMITTED_FILE_TURNS
        assert seen[0].max_turns == expected

    def test_default_budget_is_the_phase_budget_with_nothing_omitted(self, tmp_path, monkeypatch):
        seen = _capture_invocations(monkeypatch)
        self._run(_omitted_job(tmp_path))
        assert seen[0].max_turns == review.phases.PHASES[Phase.GROUP].max_turns

    def test_explicit_budget_still_wins(self, tmp_path, monkeypatch):
        seen = _capture_invocations(monkeypatch)
        self._run(_omitted_job(tmp_path, omitted=["big.py"]), max_turns=99)
        assert seen[0].max_turns == 99

    def test_budget_follows_the_registry_at_call_time(self, tmp_path, monkeypatch):
        """An import-time default would freeze the old value here."""
        seen = _capture_invocations(monkeypatch)
        monkeypatch.setitem(
            review.phases.PHASES, Phase.GROUP,
            dataclasses.replace(review.phases.PHASES[Phase.GROUP], max_turns=99),
        )
        self._run(_omitted_job(tmp_path))
        assert seen[0].max_turns == 99


class TestParallelGroupTurnBudget:
    """The parallel fan-out must resolve the same default budget as the
    serial path — it forwards no `max_turns` of its own."""

    def test_parallel_groups_get_the_default_budget(self, tmp_path, monkeypatch):
        from review.types import Group

        seen = _capture_invocations(monkeypatch)
        job = _omitted_job(tmp_path, omitted=["big.py"])
        groups = [
            Group(name="g1", files=["a.py"], lines=10),
            Group(name="g2", files=["b.py"], lines=10),
        ]
        review.phases._run_parallel_reviews(
            groups, job, len(groups), "holistic", workers=2,
            skip_groups={}, pipeline_state=None,
        )

        expected = review.phases.PHASES[Phase.GROUP].max_turns + agent.phases.OMITTED_FILE_TURNS
        assert len(seen) == 2
        assert all(inv.max_turns == expected for inv in seen)


class TestPromptTooLargeFailsThePhase:
    """A prompt over the budget fails its phase instead of being sent anyway.

    Logging "EXCEEDS budget" and invoking the agent regardless spends the
    phase's whole cost on a request the model truncates or rejects, and then
    reports whatever comes back as the phase's finding. Nothing an agent does
    changes the byte count, so the phase is failed before it starts and costs
    nothing.
    """

    @staticmethod
    def _raising(monkeypatch):
        from review.prompt import PromptTooLarge

        def boom(*_args, **_kwargs):
            raise PromptTooLarge("scout.md", 600_000)

        monkeypatch.setattr(review.phases, "build_prompt", boom)

    def test_a_scan_reports_it_and_never_reaches_the_agent(self, tmp_path, monkeypatch):
        from agent.diagnosis import DiagnosisKind

        seen = _capture_invocations(monkeypatch)
        self._raising(monkeypatch)
        result = review.phases.run_phase(_job(tmp_path), Phase.SCOUT, "scanning...")
        assert seen == []
        assert result.content == ""
        assert result.diagnosis.kind is DiagnosisKind.PROMPT_TOO_LARGE
        assert "585KB" in result.diagnosis.message

    def test_a_group_reports_it_as_a_group_failure(self, tmp_path, monkeypatch):
        from agent.diagnosis import DiagnosisKind
        from review.types import Group

        seen = _capture_invocations(monkeypatch)
        self._raising(monkeypatch)
        _, _, failure = review.phases._review_group(
            1, Group(name="g1", files=["a.py"], lines=10),
            _job(tmp_path), 1, "holistic",
        )
        assert seen == []
        assert failure is not None
        assert failure.diagnosis.kind is DiagnosisKind.PROMPT_TOO_LARGE

    def test_recovery_cannot_fix_it(self):
        """`--recover` re-renders the same phase at the same commit."""
        from agent.diagnosis import Diagnosis, DiagnosisKind

        assert not Diagnosis(DiagnosisKind.PROMPT_TOO_LARGE, detail="x").recoverable


class TestReadScan:
    """`read_scan`'s three outcomes: transformed, verbatim, and refused.

    `PhaseScan.read` defaults to `None` rather than to an identity function —
    `None` means the raw artifact *is* what the next phase needs, not that the
    scan forgot to declare a reader. `read_scan` is the only thing that has to
    know the difference, so it is the only thing this class pins.
    """

    def test_a_scan_with_a_reader_transforms_the_raw_text(self):
        """SCOUT is the one real phase whose scan supplies a `read`."""
        raw = "## Investigation leads\n- **`app.py:10`** — a real concern\n"
        result = review.phases.read_scan(Phase.SCOUT, raw)
        assert "Investigation leads from scout scan" in result
        assert "app.py:10" in result
        assert result != raw

    def test_a_scan_with_no_reader_returns_the_raw_text_unchanged(self):
        """HOLISTIC's scan declares no `read` of its own.

        A real phase rather than a hand-built `PhaseScan`: HOLISTIC already
        exercises the `read is None` branch in production, so nothing here
        needs to be synthetic to pin what `None` means.
        """
        raw = "Whatever the holistic scan wrote, unparsed."
        assert review.phases.read_scan(Phase.HOLISTIC, raw) == raw

    def test_a_phase_with_no_scan_of_its_own_raises(self):
        """SINGLE has a registry entry but declares no scan at all.

        `read_scan` only reaches the table when `raw` is non-empty — an empty
        artifact returns `""` regardless of phase, which is why this passes
        real content rather than relying on that short-circuit.
        """
        with pytest.raises(ValueError, match="declares no scan of its own"):
            review.phases.read_scan(Phase.SINGLE, "some content")


class TestGroupWorkerSlots:
    """Group agents draw from the machine slot pool, not a load average.

    The load average lags the load it reports, so two pipelines starting
    together both read an idle box and both take the cap. A slot is held, so
    the second sees what the first took.
    """

    @pytest.fixture(autouse=True)
    def _pool_in_tmp(self, tmp_path, monkeypatch):
        """Scratch pool, and no inherited markers from the suite's own claim."""
        monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path / "state"))
        monkeypatch.delenv(core.job_slots.LOCK_ENV, raising=False)
        monkeypatch.delenv(core.job_slots.GRANT_ENV, raising=False)
        yield

    def test_an_explicit_count_wins_over_the_pool(self):
        with review.pipeline.hold_group_workers(5, requested=1) as workers:
            assert workers == 1

    def test_an_explicit_count_still_cannot_exceed_the_group_count(self):
        with review.pipeline.hold_group_workers(2, requested=8) as workers:
            assert workers == 2

    def test_an_explicit_count_does_not_take_slots(self):
        """--max-parallel skips the pool, the way TEST_JOBS does on run-tests."""
        with review.pipeline.hold_group_workers(5, requested=4):
            assert core.job_slots.GRANT_ENV not in os.environ

    def test_an_idle_pool_grants_the_cap_not_the_machine(self):
        """want is the cap: an 18-core box would otherwise hand out 17."""
        with review.pipeline.hold_group_workers(10, cores=18) as workers:
            assert workers == review.pipeline.MAX_PARALLEL_CAP

    def test_a_smaller_group_count_is_not_rounded_up(self):
        with review.pipeline.hold_group_workers(2, cores=18) as workers:
            assert workers == 2

    def _claim_beside(self, monkeypatch, want):
        """What a suite claiming `want` gets while this phase holds its slots.

        The marker is dropped first so the second claim is a real one rather
        than the pass-through a nested run-tests takes.
        """
        monkeypatch.delenv(core.job_slots.LOCK_ENV, raising=False)
        with core.job_slots.claim(want, 2, 18) as other:
            return other

    def test_a_concurrent_claim_sees_the_slots_this_one_holds(self, monkeypatch):
        """The whole point: held capacity is visible, a load average is not."""
        with review.pipeline.hold_group_workers(10, cores=18) as workers:
            assert workers == 4
            # 18 cores is 17 slots, 4 of them held here, so a suite asking for
            # more than the 13 left is held to what remains. Asking for 13 or
            # fewer would be granted in full either way and would prove nothing
            # about the slots this phase took.
            assert self._claim_beside(monkeypatch, 16) == core.job_slots.pool_size(18) - 4

    def test_the_slots_are_held_for_the_body_not_just_counted(self):
        """A count computed and released before the fan-out holds nothing."""
        with review.pipeline.hold_group_workers(4, cores=18):
            assert os.environ.get(core.job_slots.GRANT_ENV) is not None
        assert core.job_slots.GRANT_ENV not in os.environ

    def test_a_nested_run_tests_can_size_itself_from_the_grant(self):
        """run-tests skips its own claim under the marker and reads the grant.

        Without the grant exported it would skip the pool *and* fall back to
        its full JOBS, which is the one combination that oversubscribes.
        """
        with review.pipeline.hold_group_workers(10, cores=18) as workers:
            assert os.environ[core.job_slots.GRANT_ENV] == str(workers)
            assert os.environ[core.job_slots.LOCK_ENV] == str(workers)


class TestParallelFailFast:
    """A systemic fault must not burn every queued group agent."""

    def test_queued_groups_are_skipped_after_consecutive_same_failures(
        self, tmp_path, monkeypatch,
    ):
        from agent.diagnosis import Diagnosis, DiagnosisKind
        from review.retry import GroupFailure
        from review.types import Group

        started = []
        boom = Diagnosis(DiagnosisKind.AGENT_ERROR, detail="boom")

        def fake_review(i, grp, *args, **kwargs):
            started.append(grp.name)
            return i, "out", GroupFailure(grp.name, boom)

        monkeypatch.setattr(review.phases, "_review_group", fake_review)
        groups = [
            Group(name=f"g{i}", files=[f"{i}.py"], lines=10)
            for i in range(1, 7)
        ]
        failed = review.phases._run_parallel_reviews(
            groups, _job(tmp_path), len(groups), "holistic", workers=3,
            skip_groups={}, pipeline_state=None,
        )
        # In-flight groups may finish after the abort fires; groups not yet
        # submitted must not start. Without the fail-fast, all six run.
        assert len(started) < len(groups)
        assert "g6" not in started
        skipped = {f.group for f in failed if f.diagnosis.kind is DiagnosisKind.SKIPPED}
        assert skipped


class TestValidateGroupOutputTolerance:
    """A6b's net: _validate_group_output already accepts a short complete
    doc, an empty doc, and whatever is on disk at the call.
    """

    # passes-at-base: one known heading is already enough for the merge to read
    def test_a_short_complete_group_doc_validates(self, tmp_path):
        f = tmp_path / "group.md"
        f.write_text("## Must fix\n- **[M1]** **`a.py:1`** — issue\n")
        assert review.phases._validate_group_output(str(f), "g") is True

    # passes-at-base: empty output is already not a failure here
    def test_an_empty_group_doc_validates(self, tmp_path):
        f = tmp_path / "group.md"
        f.write_text("")
        assert review.phases._validate_group_output(str(f), "g") is True

    # passes-at-base: the function reads the path each call, so a rewrite is a later read
    def test_a_doc_rewritten_mid_run_validates_the_current_bytes(self, tmp_path):
        f = tmp_path / "group.md"
        f.write_text("## Must fix\n- **[M1]** finding\n")
        assert review.phases._validate_group_output(str(f), "g") is True
        f.write_text("not a document")
        assert review.phases._validate_group_output(str(f), "g") is False
        f.write_text("## Nit\n- **[N1]** nit\n")
        assert review.phases._validate_group_output(str(f), "g") is True
