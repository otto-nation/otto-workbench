"""Synthesis in `review.steps`: the phase itself and the fallback around it."""

import sys
from pathlib import Path

import pytest

from conftest import git_out

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

# The module the `ro` fixture returns, imported so bin/local/select-pytest maps
# this suite to the code it reaches through `ro`.
import cli.review_orchestrate  # noqa: F401

from core.phases import Phase
from review.verdict import BUDGET_SUMMARY, FALLBACK_SUMMARY, MECHANICAL_NOTE, SKIPPED_SUMMARY


class TestPhaseSynthesis:
    def _make_job(self, ro, tmp_path, mode="pr"):
        return ro.ReviewJob(
            repo="org/repo", pr_number="42",
            pr=ro.PRMetadata(title="test PR", body="", head="feat", base="main",
                             head_sha="abc123", additions=100, deletions=50,
                             changed_files=10, files=[]),
            ctx=ro.PRContext(),
            wt_path=str(tmp_path / "wt"),
            review_file=str(tmp_path / "review.md"),
            session_log=str(tmp_path / "session.jsonl"),
            mode=ro.Mode(mode),
        )

    @staticmethod
    def _patch_pipeline(monkeypatch, **overrides):
        """Patch the module-level imports `_phase_synthesis` reaches through.

        The synthesis phase spans three modules: it builds its own prompt and
        post-processes its own findings in `review.steps`, writes the result
        through `review.outcome`, but invokes the agent through `PhaseRunner`,
        whose bindings live in `review.phases`. Each name is patched on the
        module that owns it — an explicit map, not a guess, because a name
        this maps wrong lands the patch on a module the code under test never
        reads and the test passes having mocked nothing.
        """
        import agent.retry
        import review.outcome
        import review.phases
        import review.steps
        owners = {
            "build_prompt": review.steps,
            "post_process_findings": review.outcome,
            "run_agent": review.phases,
            # `_retry_missing_output` is `agent.retry.retry_missing_output`
            # (aliased in `review.retry`), which recovers through its own
            # `try_recover_output` binding, not `review.phases`' — that one is
            # read only by `_review_group`, which this phase never calls.
            "try_recover_output": agent.retry,
        }
        defaults = {
            "build_prompt": lambda *a, **kw: "mock prompt",
            "post_process_findings": lambda *a, **kw: None,
        }
        defaults.update(overrides)
        for name, func in defaults.items():
            if name not in owners:
                raise ValueError(f"_patch_pipeline has no owner mapped for {name!r}")
            monkeypatch.setattr(owners[name], name, func)

    def test_successful_synthesis(self, ro, tmp_path, monkeypatch):
        job = self._make_job(ro, tmp_path)
        review_content = "# Review: org/repo#42 — test PR\n\n## Summary\nLooks good.\n\n## Verdict\nApprove.\n"

        def mock_invoke(inv, **kwargs):
            from pathlib import Path
            Path(job.review_file).write_text(review_content)
            Path(inv.session_log).write_text("")
            return 0

        self._patch_pipeline(monkeypatch, run_agent=mock_invoke)

        ro._phase_synthesis(job, "", 3, "merged content")

        from pathlib import Path
        result = Path(job.review_file).read_text()
        assert "## Summary" in result
        assert FALLBACK_SUMMARY not in result

    def test_a_successful_synthesis_quoting_both_literals_has_no_diagnosis(
        self, ro, tmp_path, monkeypatch,
    ):
        """A genuine agent review that discusses the fallback machinery in prose
        is not misread as one — the decision is made where the write happened,
        not by scanning the document for these literals."""
        job = self._make_job(ro, tmp_path)
        review_content = (
            "# Review: org/repo#42 — test PR\n\n## Summary\n"
            f"Looks good. Quotes {MECHANICAL_NOTE} and {FALLBACK_SUMMARY} in prose.\n\n"
            "## Verdict\nApprove.\n"
        )

        def mock_invoke(inv, **kwargs):
            Path(job.review_file).write_text(review_content)
            Path(inv.session_log).write_text("")
            return 0

        self._patch_pipeline(monkeypatch, run_agent=mock_invoke)

        result = ro._phase_synthesis(job, "", 3, "merged content")

        assert result.diagnosis is None

    def test_cost_comes_from_the_session_log(self, ro, tmp_path, monkeypatch):
        job = self._make_job(ro, tmp_path)
        review_content = "# Review\n\n## Summary\nLooks good.\n\n## Verdict\nApprove.\n"

        def mock_invoke(inv, **kwargs):
            Path(job.review_file).write_text(review_content)
            Path(inv.session_log).write_text(
                '{"type":"result","subtype":"success","total_cost_usd":2.5}\n')
            return 0

        self._patch_pipeline(monkeypatch, run_agent=mock_invoke)

        assert ro._phase_synthesis(job, "", 3, "merged content").cost == 2.5

    def test_a_fallen_back_synthesis_is_still_charged(self, ro, tmp_path, monkeypatch):
        """The agent ran and spent; the review file just came from the merge."""
        job = self._make_job(ro, tmp_path)

        def mock_invoke(inv, **kwargs):
            Path(inv.session_log).write_text(
                '{"type":"result","subtype":"success","total_cost_usd":2.5}\n')
            return 1

        self._patch_pipeline(
            monkeypatch,
            run_agent=mock_invoke,
            try_recover_output=lambda *a: False,
        )

        merged = "## Must fix\n- **[M1]** **`file.go:1`** — issue\n"
        result = ro._phase_synthesis(job, "", 3, merged)

        assert FALLBACK_SUMMARY in Path(job.review_file).read_text()
        assert result.cost == 2.5
        assert result.diagnosis == ro.Diagnosis(ro.DiagnosisKind.MECHANICAL_FALLBACK)

    def test_an_unpromptable_synthesis_falls_back_to_the_merge(
        self, ro, tmp_path, monkeypatch,
    ):
        """The findings are already on disk; only the write-up would not fit.

        Merging them mechanically keeps every finding and loses the prose,
        which is the same trade a synthesis agent that failed already makes —
        and strictly better than discarding a phase's worth of group reviews
        because their cover letter is over the budget.
        """
        from review.prompt import PromptTooLarge

        job = self._make_job(ro, tmp_path)
        invoked = []

        def boom(*_a, **_kw):
            raise PromptTooLarge("synthesis.md", 600_000)

        self._patch_pipeline(
            monkeypatch,
            build_prompt=boom,
            run_agent=lambda inv, **kw: invoked.append(inv),
        )

        merged = "## Must fix\n- **[M1]** **`file.go:1`** — issue\n"
        result = ro._phase_synthesis(job, "", 3, merged)

        assert invoked == []
        written = Path(job.review_file).read_text()
        assert FALLBACK_SUMMARY in written
        assert "file.go:1" in written
        assert result.diagnosis == ro.Diagnosis(ro.DiagnosisKind.MECHANICAL_FALLBACK)

    def test_agent_fails_no_output(self, ro, tmp_path, monkeypatch):
        job = self._make_job(ro, tmp_path)

        def mock_invoke(inv, **kwargs):
            from pathlib import Path
            Path(inv.session_log).write_text("")
            return 1

        self._patch_pipeline(
            monkeypatch,
            run_agent=mock_invoke,
            try_recover_output=lambda *a: False,
        )

        merged = "## Must fix\n- **[M1]** **`file.go:1`** — issue\n"
        ro._phase_synthesis(job, "", 3, merged)

        from pathlib import Path
        result = Path(job.review_file).read_text()
        assert FALLBACK_SUMMARY in result

    def test_incomplete_output_falls_back(self, ro, tmp_path, monkeypatch):
        job = self._make_job(ro, tmp_path)

        def mock_invoke(inv, **kwargs):
            from pathlib import Path
            # Write incomplete review (no Summary or Verdict headers)
            Path(job.review_file).write_text("# Review\nSome partial content\n")
            Path(inv.session_log).write_text("")
            return 0

        self._patch_pipeline(monkeypatch, run_agent=mock_invoke)

        merged = "## Should fix\n- **[S1]** **`api.go:10`** — cleanup\n"
        ro._phase_synthesis(job, "", 3, merged)

        from pathlib import Path
        result = Path(job.review_file).read_text()
        assert FALLBACK_SUMMARY in result

    def test_transient_error_retries_then_succeeds(self, ro, tmp_path, monkeypatch):
        job = self._make_job(ro, tmp_path)
        review_content = "# Review: org/repo#42 — test PR\n\n## Summary\nLooks good.\n\n## Verdict\nApprove.\n"
        calls = []

        def mock_invoke(inv, **kwargs):
            from pathlib import Path
            calls.append(len(calls))
            if len(calls) == 1:
                Path(inv.session_log).write_text(
                    '{"type":"result","subtype":"success","is_error":true,'
                    '"result":"API Error: Connection to the API was lost (FailedToOpenSocket)."}\n'
                )
                return 1
            Path(job.review_file).write_text(review_content)
            Path(inv.session_log).write_text("")
            return 0

        self._patch_pipeline(monkeypatch, run_agent=mock_invoke)

        ro._phase_synthesis(job, "", 3, "merged content")

        from pathlib import Path
        result = Path(job.review_file).read_text()
        assert "## Summary" in result
        assert FALLBACK_SUMMARY not in result
        assert len(calls) == 2

    def test_transient_error_retries_then_falls_back(self, ro, tmp_path, monkeypatch):
        job = self._make_job(ro, tmp_path)
        calls = []

        def mock_invoke(inv, **kwargs):
            from pathlib import Path
            calls.append(len(calls))
            Path(inv.session_log).write_text(
                '{"type":"result","subtype":"success","is_error":true,'
                '"result":"API Error: Connection to the API was lost (FailedToOpenSocket)."}\n'
            )
            return 1

        self._patch_pipeline(
            monkeypatch,
            run_agent=mock_invoke,
            try_recover_output=lambda *a: False,
        )

        merged = "## Must fix\n- **[M1]** **`file.go:1`** — issue\n"
        ro._phase_synthesis(job, "", 3, merged)

        from pathlib import Path
        result = Path(job.review_file).read_text()
        assert FALLBACK_SUMMARY in result
        assert len(calls) == 2

    def test_non_transient_error_does_not_retry(self, ro, tmp_path, monkeypatch):
        job = self._make_job(ro, tmp_path)
        calls = []

        def mock_invoke(inv, **kwargs):
            from pathlib import Path
            calls.append(len(calls))
            Path(inv.session_log).write_text(
                '{"type":"result","subtype":"success","is_error":true,'
                '"result":"agent error: something broke"}\n'
            )
            return 1

        self._patch_pipeline(
            monkeypatch,
            run_agent=mock_invoke,
            try_recover_output=lambda *a: False,
        )

        merged = "## Must fix\n- **[M1]** **`file.go:1`** — issue\n"
        ro._phase_synthesis(job, "", 3, merged)

        from pathlib import Path
        result = Path(job.review_file).read_text()
        assert FALLBACK_SUMMARY in result
        assert len(calls) == 1


class TestSynthesisIsAskedAboutWhatTheGroupsPassedOver:
    """A prior finding the groups never mentioned reaches the last agent that can act.

    Before this, reconciliation ran only after synthesis had written the
    document, so a finding whose subject the tree still held left the run as a
    warning and left the review saying nothing about it — which the next round
    reads as the finding no longer existing.
    """

    BEFORE = "func handle() {\n    rows, _ := db.Query(sql)\n}\n"
    PRIOR_LINE = (
        "- **[M1]** **`handler.go:2`** — `rows, _ := db.Query(sql)` drops the error"
    )

    def _job(self, ro, tmp_path, prior_review):
        wt = tmp_path / "wt"
        wt.mkdir()
        git_out(wt, "init", "-q", "-b", "main")
        git_out(wt, "config", "user.email", "test@example.com")
        git_out(wt, "config", "user.name", "Test")
        git_out(wt, "config", "commit.gpgsign", "false")
        git_out(wt, "config", "core.hooksPath", str(tmp_path / "hooks"))
        (wt / "handler.go").write_text(self.BEFORE)
        git_out(wt, "add", "-A")
        git_out(wt, "commit", "-qm", "before")
        prior_sha = git_out(wt, "rev-parse", "HEAD").strip()

        job = TestPhaseSynthesis()._make_job(ro, tmp_path)
        job.wt_path = str(wt)
        job.prior_review = (
            f"# Review: org/repo#42 — t\n<!-- head_sha: {prior_sha} -->\n"
            f"## Must fix\n{prior_review}\n"
        )
        return job

    def _extras(self, ro, tmp_path, monkeypatch, merged, prior_review=PRIOR_LINE):
        job = self._job(ro, tmp_path, prior_review)
        seen = {}

        def capture(phase, j, **extra):
            seen.update(extra)
            return "mock prompt"

        def mock_invoke(inv, **kwargs):
            Path(job.review_file).write_text(
                "# Review\n\n## Summary\nok.\n\n## Verdict\nApprove.\n")
            Path(inv.session_log).write_text("")
            return 0

        TestPhaseSynthesis._patch_pipeline(
            monkeypatch, build_prompt=capture, run_agent=mock_invoke,
        )
        ro._phase_synthesis(job, "", 3, merged)
        return seen

    def test_a_finding_no_group_mentioned_is_handed_to_synthesis(
        self, ro, tmp_path, monkeypatch,
    ):
        extras = self._extras(
            ro, tmp_path, monkeypatch, "## Must fix\n- **[M9]** **`other.go:1`** — x\n",
        )
        assert [f.ref.finding_id for f in extras["unaccounted_prior"]] == ["M1"]

    def test_a_finding_the_groups_settled_is_not_asked_about_again(
        self, ro, tmp_path, monkeypatch,
    ):
        merged = "## Prior findings\n- **[M1]** `handler.go` — Fixed\n"
        assert self._extras(ro, tmp_path, monkeypatch, merged)["unaccounted_prior"] == []

    def test_a_prior_review_that_reported_nothing_hands_over_nothing(
        self, ro, tmp_path, monkeypatch,
    ):
        assert self._extras(
            ro, tmp_path, monkeypatch, "## Must fix\n", prior_review="",
        )["unaccounted_prior"] == []


class TestRunSynthesisOrFallback:
    """What the synthesis step records in state, and what it reports spending."""

    # A merge with one finding in it, so the step has something to carry: the
    # clean-review shortcut returns before synthesis and would answer for every
    # test below whatever the branch under test does.
    MERGED = "## Should fix\n- **[S1]** **`api.go:10`** — cleanup\n"

    def _make_state(self, ro):
        return ro.PipelineState(
            head_sha="abc123",
            group_names=["grp-1"],
            done={Phase.HOLISTIC},
            groups_done=[1],
        )

    def _make_job(self, ro, tmp_path, mode="pr", skip_phases=frozenset()):
        return ro.ReviewJob(
            repo="org/repo", pr_number="42",
            pr=ro.PRMetadata(title="test PR", body="", head="feat", base="main",
                             head_sha="abc123", additions=100, deletions=50,
                             changed_files=10, files=[]),
            ctx=ro.PRContext(),
            wt_path=str(tmp_path / "wt"),
            review_file=str(tmp_path / "review.md"),
            session_log=str(tmp_path / "session.jsonl"),
            mode=ro.Mode(mode), skip_phases=skip_phases,
        )

    def test_a_diagnosed_synthesis_lands_in_state_failed(self, ro, tmp_path, monkeypatch):
        """`_run_synthesis_or_fallback` copies a phase's diagnosis into `state.failed`."""
        import review.pipeline
        import review.steps

        job = self._make_job(ro, tmp_path, mode="self")
        state = self._make_state(ro)

        def mock_synthesis(job, holistic, count, merged, skipped_groups=0):
            ro._build_mechanical_fallback(
                job, count, merged, skipped_groups=skipped_groups,
            ).write(job.review_file)
            return review.pipeline.PhaseResult(
                str(tmp_path / "synthesis.jsonl"),
                diagnosis=ro.Diagnosis(ro.DiagnosisKind.MECHANICAL_FALLBACK),
            )

        monkeypatch.setattr(review.steps, "_phase_synthesis", mock_synthesis)
        monkeypatch.setattr(review.steps, "_write_pipeline_state", lambda *a: None)

        merged = self.MERGED
        ro._run_synthesis_or_fallback(
            job, state, "", 1, merged, [], 0, 0.0, 20.0,
        )
        assert state.failed == {Phase.SYNTHESIS: ro.Diagnosis(ro.DiagnosisKind.MECHANICAL_FALLBACK)}

    def test_a_synthesised_review_quoting_the_note_is_not_a_fallback(
        self, ro, tmp_path, monkeypatch,
    ):
        """A review whose prose contains MECHANICAL_NOTE was still written by the agent.

        The diagnosis is the pipeline's own record of which path wrote the
        file, so a review discussing the fallback machinery is not diagnosed
        as one.
        """
        import review.pipeline
        import review.steps

        job = self._make_job(ro, tmp_path)
        state = self._make_state(ro)

        def mock_synthesis(job, holistic, count, merged, skipped_groups=0):
            Path(job.review_file).write_text(
                "# Review\n\n## Summary\nThe merge path stamps "
                f"{MECHANICAL_NOTE} and {FALLBACK_SUMMARY} on the document.\n\n"
                "## Verdict\nApprove\n"
            )
            return review.pipeline.PhaseResult(str(tmp_path / "synthesis.jsonl"))

        monkeypatch.setattr(review.steps, "_phase_synthesis", mock_synthesis)
        monkeypatch.setattr(review.steps, "_write_pipeline_state", lambda *a: None)

        merged = self.MERGED
        ro._run_synthesis_or_fallback(
            job, state, "", 1, merged, [], 0, 0.0, 20.0,
        )
        assert Phase.SYNTHESIS not in state.failed

    def test_synthesis_reports_what_its_log_records(self, ro, tmp_path, monkeypatch):
        import review.pipeline
        import review.steps

        job = self._make_job(ro, tmp_path)
        state = self._make_state(ro)

        def mock_synthesis(job, holistic, count, merged, skipped_groups=0):
            from pathlib import Path
            Path(job.review_file).write_text(
                "# Review\n\n## Summary\nok\n\n## Verdict\nApprove\n")
            return review.pipeline.PhaseResult(str(tmp_path / "synthesis.jsonl"), 1.25)

        monkeypatch.setattr(review.steps, "_phase_synthesis", mock_synthesis)
        monkeypatch.setattr(review.steps, "_write_pipeline_state", lambda *a: None)

        merged = self.MERGED
        result = ro._run_synthesis_or_fallback(
            job, state, "", 1, merged, [], 0, 0.0, 20.0,
        )
        assert result.cost == 1.25

    def test_a_clean_review_spends_nothing(self, ro, tmp_path, monkeypatch):
        import review.pipeline
        import review.steps

        job = self._make_job(ro, tmp_path)
        state = self._make_state(ro)
        monkeypatch.setattr(review.steps, "_write_pipeline_state", lambda *a: None)

        result = ro._run_synthesis_or_fallback(
            job, state, "", 1, "No findings.\n", [], 0, 0.0, 20.0,
        )
        assert result == review.pipeline.PhaseResult()

    def test_a_mention_of_a_finding_id_is_not_a_finding(self, ro, tmp_path, monkeypatch):
        """The merge declares nothing, so the review is clean — a triage note
        naming a prior ID used to send the run to synthesis with no findings."""
        import review.pipeline
        import review.steps

        job = self._make_job(ro, tmp_path)
        state = self._make_state(ro)
        monkeypatch.setattr(review.steps, "_write_pipeline_state", lambda *a: None)
        monkeypatch.setattr(
            review.steps, "_phase_synthesis",
            lambda *a, **kw: pytest.fail("synthesis ran for a review with no findings"))

        result = ro._run_synthesis_or_fallback(
            job, state, "", 1,
            "## File Triage\n- `api.go` — reviewed, [M1] was fixed here\n", [],
            0, 0.0, 20.0,
        )
        assert result == review.pipeline.PhaseResult()

    def test_a_synthesis_skipped_on_budget_spends_nothing(self, ro, tmp_path, monkeypatch):
        import review.pipeline
        import review.steps

        job = self._make_job(ro, tmp_path)
        state = self._make_state(ro)
        monkeypatch.setattr(review.steps, "_write_pipeline_state", lambda *a: None)

        merged = self.MERGED
        result = ro._run_synthesis_or_fallback(
            job, state, "", 1, merged, [], 0, 25.0, 20.0,
        )
        assert result == review.pipeline.PhaseResult()

    def test_no_group_reports_partial_not_a_failed_run(self, ro, tmp_path, monkeypatch):
        """A group phase the operator switched off is not the pipeline failing.

        Every group is unreviewed either way, so the honest verdict is partial
        with `skipped` as the reason — `all groups failed` would blame the
        agents for a review nobody asked to run.
        """
        import review.pipeline
        import review.steps

        job = self._make_job(
            ro, tmp_path, skip_phases=frozenset({ro.Phase.GROUP}))
        state = self._make_state(ro)
        monkeypatch.setattr(review.steps, "_write_pipeline_state", lambda *a: None)

        merged = self.MERGED
        skipped = ro.Diagnosis(ro.DiagnosisKind.SKIPPED, detail="--no-group")
        ro._run_synthesis_or_fallback(
            job, state, "", 1, merged, [ro.GroupFailure("grp-1", skipped)],
            0, 0.0, 20.0,
        )
        assert state.failed == {Phase.SYNTHESIS: skipped}
        assert state.status is ro.ReviewStatus.PARTIAL

    def test_no_synthesis_writes_the_mechanical_merge(self, ro, tmp_path, monkeypatch):
        """`--no-synthesis` reaches the review file without an agent."""
        import review.pipeline
        import review.steps

        job = self._make_job(
            ro, tmp_path, skip_phases=frozenset({ro.Phase.SYNTHESIS}))
        # The merge is post-processed like any other review, and that drops a
        # finding whose file it cannot find.
        (tmp_path / "wt").mkdir()
        (tmp_path / "wt" / "api.go").write_text("\n" * 20)
        state = self._make_state(ro)
        monkeypatch.setattr(review.steps, "_write_pipeline_state", lambda *a: None)
        monkeypatch.setattr(
            review.steps, "_phase_synthesis",
            lambda *a, **kw: pytest.fail("synthesis ran despite --no-synthesis"))

        merged = self.MERGED
        result = ro._run_synthesis_or_fallback(
            job, state, "", 1, merged, [], 0, 0.0, 20.0,
        )
        assert result == review.pipeline.PhaseResult()
        assert Phase.SYNTHESIS in state.done
        assert state.failed == {}
        assert "api.go:10" in Path(job.review_file).read_text()

    def test_no_synthesis_writes_a_summary_the_gate_can_resume_from(
        self, ro, tmp_path, monkeypatch,
    ):
        """`--no-synthesis` leaves a review `is_complete_review` accepts.

        Without the section a run resumed at the disprove gate reads its own
        review as unfinished and re-enters synthesis to rewrite it.
        """
        import review.pipeline
        import review.steps

        job = self._make_job(
            ro, tmp_path, skip_phases=frozenset({ro.Phase.SYNTHESIS}))
        (tmp_path / "wt").mkdir()
        (tmp_path / "wt" / "api.go").write_text("\n" * 20)
        state = self._make_state(ro)
        monkeypatch.setattr(review.steps, "_write_pipeline_state", lambda *a: None)

        ro._run_synthesis_or_fallback(
            job, state, "", 1, self.MERGED, [], 0, 0.0, 20.0,
        )

        written = Path(job.review_file).read_text()
        assert "## Summary" in written
        assert SKIPPED_SUMMARY in written
        # The operator stopped synthesis; no agent failed.
        assert FALLBACK_SUMMARY not in written
        assert ro.is_complete_review(job.review_file)

    def test_a_budget_cut_off_writes_a_summary_naming_the_budget(
        self, ro, tmp_path, monkeypatch,
    ):
        """The budget path says why synthesis did not run, not that it failed."""
        import review.pipeline
        import review.steps

        job = self._make_job(ro, tmp_path)
        (tmp_path / "wt").mkdir()
        (tmp_path / "wt" / "api.go").write_text("\n" * 20)
        state = self._make_state(ro)
        monkeypatch.setattr(review.steps, "_write_pipeline_state", lambda *a: None)

        ro._run_synthesis_or_fallback(
            job, state, "", 1, self.MERGED, [], 0, 25.0, 20.0,
        )

        written = Path(job.review_file).read_text()
        assert "## Summary" in written
        assert BUDGET_SUMMARY in written
        assert FALLBACK_SUMMARY not in written
        assert ro.is_complete_review(job.review_file)

    def test_a_budget_cut_off_still_checks_its_findings_against_the_tree(
        self, ro, tmp_path, monkeypatch,
    ):
        """The run least able to afford an unchecked claim still checks them.

        Evidence verification and prior-finding reconciliation read the work
        tree and spend none of the budget that ran out, so the path that ships
        group output on a cut-off post-processes it like every other one.
        """
        import review.pipeline
        import review.steps

        job = self._make_job(ro, tmp_path)
        (tmp_path / "wt").mkdir()
        (tmp_path / "wt" / "api.go").write_text("\n" * 20)
        state = self._make_state(ro)
        monkeypatch.setattr(review.steps, "_write_pipeline_state", lambda *a: None)

        ro._run_synthesis_or_fallback(
            job, state, "", 1, self.MERGED, [], 0, 25.0, 20.0,
        )

        assert job.verification is not None

    @pytest.mark.parametrize(
        ("skipped", "cost_so_far"), [(True, 0.0), (False, 25.0)],
        ids=["no-synthesis", "budget"],
    )
    def test_neither_no_synthesis_path_states_a_verdict(
        self, ro, tmp_path, monkeypatch, skipped, cost_so_far,
    ):
        """Neither path weighed the review, so neither approves or blocks it."""
        import review.pipeline
        import review.steps

        job = self._make_job(
            ro, tmp_path,
            skip_phases=frozenset({ro.Phase.SYNTHESIS}) if skipped else frozenset(),
        )
        (tmp_path / "wt").mkdir()
        (tmp_path / "wt" / "api.go").write_text("\n" * 20)
        state = self._make_state(ro)
        monkeypatch.setattr(review.steps, "_write_pipeline_state", lambda *a: None)

        ro._run_synthesis_or_fallback(
            job, state, "", 1, self.MERGED, [], 0, cost_so_far, 20.0,
        )

        assert "## Verdict" not in Path(job.review_file).read_text()
