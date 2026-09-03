"""Tests for the single-agent retry and multi-phase recovery resolution."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import review_gc
import review_outcome
import review_phases
import review_pipeline
import review_retry
import review_state
import review_types
from agent_diagnosis import Diagnosis, DiagnosisKind
from phases import Phase

_TURNS = 15
_MAX_TURNS = Diagnosis(DiagnosisKind.MAX_TURNS, num_turns=_TURNS)
_NO_WRITE = Diagnosis(DiagnosisKind.MAX_TURNS, num_turns=_TURNS, no_write_tool=True)
_TRANSIENT = Diagnosis(DiagnosisKind.TRANSIENT, detail="ECONNRESET")


def _result(subtype: str = "error_max_turns", **extra) -> str:
    return json.dumps({
        "type": "result", "subtype": subtype, "num_turns": _TURNS, **extra,
    })


def _write_log(tmp_path: Path, *lines: str) -> str:
    path = tmp_path / "session.jsonl"
    path.write_text("\n".join(lines) + "\n")
    return str(path)


def _make_job(
    tmp_path: Path, head_sha: str = "abc", session_log: str = "",
) -> review_pipeline.ReviewJob:
    return review_pipeline.ReviewJob(
        repo="org/repo", pr_number="1",
        pr=review_pipeline.PRMetadata(
            title="t", body="", head="b", base="main", head_sha=head_sha,
            additions=1, deletions=1, changed_files=1, files=[]),
        ctx=review_pipeline.PRContext(), wt_path=str(tmp_path),
        review_file=str(tmp_path / "review.md"),
        session_log=session_log or str(tmp_path / "session.jsonl"),
    )


class _Invoke:
    """Records each call and optionally writes output on a chosen attempt."""

    def __init__(self, output_path: str = "", write_on: int = 0, log_path: str = ""):
        self.calls: list[tuple[str, int]] = []
        self.output_path = output_path
        self.write_on = write_on
        self.log_path = log_path

    def __call__(self, prompt: str, turns: int) -> int:
        self.calls.append((prompt, turns))
        if self.log_path:
            Path(self.log_path).write_text(_result("success") + "\n")
        if self.write_on == len(self.calls):
            Path(self.output_path).write_text("## Summary\n")
        return 0


class TestRetryHintFor:
    def test_no_write_diagnosis_names_the_write_mechanism(self):
        assert review_retry._retry_hint_for(_NO_WRITE) == review_retry._NO_WRITE_HINT

    def test_plain_max_turns_gets_the_generic_hint(self):
        assert review_retry._retry_hint_for(_MAX_TURNS) == review_retry._RETRY_HINT

    def test_transient_error_gets_no_hint(self):
        assert review_retry._retry_hint_for(_TRANSIENT) == ""

    def test_missing_result_record_gets_no_hint(self):
        assert review_retry._retry_hint_for(
            Diagnosis(DiagnosisKind.NO_RESULT_RECORD),
        ) == ""


class TestIsRetryable:
    def test_clean_completion_without_a_write_is_retryable(self):
        """The motivating failure: finished cleanly, produced nothing."""
        diagnosis = Diagnosis(
            DiagnosisKind.COMPLETED, detail="success", no_write_tool=True,
        )
        assert review_retry._is_retryable(diagnosis)
        assert review_retry._retry_hint_for(diagnosis) == review_retry._NO_WRITE_HINT

    def test_clean_completion_that_wrote_nothing_observable_is_not_retryable(self):
        """Without the no-write flag there is no reason to expect a difference."""
        assert not review_retry._is_retryable(
            Diagnosis(DiagnosisKind.COMPLETED, detail="success"),
        )

    def test_no_write_completion_keeps_its_turn_budget(self):
        diagnosis = Diagnosis(
            DiagnosisKind.COMPLETED, detail="success", no_write_tool=True,
        )
        assert review_retry._retry_turns_for(diagnosis, 15) == 15


class TestRetryTurnsFor:
    def test_max_turns_doubles(self):
        assert review_retry._retry_turns_for(_MAX_TURNS, 15) == 30

    def test_doubling_is_capped_at_the_group_ceiling(self):
        assert review_retry._retry_turns_for(_MAX_TURNS, 20) == review_phases.RETRY_MAX_TURNS_GROUP

    def test_budget_above_the_ceiling_is_not_lowered(self):
        assert review_retry._retry_turns_for(_MAX_TURNS, 40) == 40

    def test_non_turn_failures_keep_their_budget(self):
        assert review_retry._retry_turns_for(_TRANSIENT, 15) == 15


class TestRetryMissingOutput:
    def _run(self, invoke, log_path, output_path, max_turns=_TURNS):
        return review_retry._retry_missing_output(
            invoke, "PROMPT", log_path, output_path,
            label="Test phase", max_turns=max_turns,
        )

    def test_existing_output_skips_the_retry(self, tmp_path):
        log_path = _write_log(tmp_path, _result())
        output = tmp_path / "out.md"
        output.write_text("## Summary\n")
        invoke = _Invoke()
        assert self._run(invoke, log_path, str(output)) is None
        assert invoke.calls == []

    def test_non_retryable_reason_returns_without_retrying(self, tmp_path):
        log_path = _write_log(tmp_path, _result("error", is_error=True, result="permission denied"))
        output = tmp_path / "out.md"
        invoke = _Invoke()
        diagnosis = self._run(invoke, log_path, str(output))
        assert diagnosis == Diagnosis(
            DiagnosisKind.AGENT_ERROR, detail="permission denied",
        )
        assert invoke.calls == []

    def test_retry_prefixes_the_hint_and_raises_the_budget(self, tmp_path):
        log_path = _write_log(tmp_path, _result())
        output = tmp_path / "out.md"
        invoke = _Invoke(str(output), write_on=1, log_path=log_path)
        assert self._run(invoke, log_path, str(output)) is None
        prompt, turns = invoke.calls[0]
        assert prompt == review_retry._RETRY_HINT + "PROMPT"
        assert turns == 30

    def test_no_write_diagnosis_selects_the_write_first_hint(self, tmp_path):
        log_path = _write_log(
            tmp_path,
            json.dumps({
                "type": "assistant",
                "message": {"content": [
                    {"type": "tool_use", "name": "Read", "input": {"file_path": "/tmp/a"}},
                ]},
            }),
            _result(),
        )
        output = tmp_path / "out.md"
        invoke = _Invoke(str(output), write_on=1, log_path=log_path)
        self._run(invoke, log_path, str(output))
        assert invoke.calls[0][0].startswith(review_retry._NO_WRITE_HINT)

    def test_retry_runs_once_and_reports_its_own_failure(self, tmp_path):
        log_path = _write_log(tmp_path, _result())
        output = tmp_path / "out.md"
        invoke = _Invoke(str(output), write_on=0, log_path=log_path)
        diagnosis = self._run(invoke, log_path, str(output))
        assert len(invoke.calls) == 1
        assert diagnosis == Diagnosis(DiagnosisKind.COMPLETED, detail="success")

    def test_both_attempts_survive_in_the_log(self, tmp_path):
        log_path = _write_log(tmp_path, _result())
        output = tmp_path / "out.md"
        invoke = _Invoke(str(output), write_on=1, log_path=log_path)
        self._run(invoke, log_path, str(output))
        records = [json.loads(l) for l in Path(log_path).read_text().splitlines() if l]
        assert [r["subtype"] for r in records] == ["error_max_turns", "success"]

    def test_retry_exit_code_reaches_the_failure_message(self, tmp_path, monkeypatch):
        """The reported exit code must come from the retry, not the first attempt."""
        log_path = _write_log(tmp_path, _result())
        job = _make_job(tmp_path, session_log=log_path)
        codes = iter([0, 3])
        monkeypatch.setattr(review_pipeline, "build_prompt", lambda *a, **k: "PROMPT")
        monkeypatch.setattr(review_phases, "run_agent", lambda *a, **k: next(codes))
        errors = []
        monkeypatch.setattr(review_pipeline.log, "error", errors.append)

        with pytest.raises(SystemExit):
            review_pipeline.run_single_agent(job)

        assert "exited with code 3" in errors[0]

    def test_denied_write_is_recovered_instead_of_retried(self, tmp_path):
        log_path = _write_log(tmp_path, json.dumps({
            "type": "result", "is_error": True,
            "permission_denials": [{
                "tool_name": "Write",
                "tool_input": {"content": "## Summary\nNo issues found."},
            }],
        }))
        output = tmp_path / "out.md"
        invoke = _Invoke()
        assert self._run(invoke, log_path, str(output)) is None
        assert invoke.calls == []
        assert "## Summary" in output.read_text()


class TestSingleAgentCleanup:
    """A single-agent run leaves the directory holding only its deliverable.

    The pipeline does not sweep — the orchestrator's scope does, once every
    phase is done — so the harness enters that scope the same way it does.
    """

    def _run(self, tmp_path, monkeypatch):
        job = _make_job(tmp_path)

        def _agent(*_args, **_kwargs) -> int:
            Path(job.review_file).write_text("## Summary\n")
            Path(job.session_log).write_text(_result("success") + "\n")
            return 0

        monkeypatch.setattr(review_pipeline, "build_prompt", lambda *a, **k: "PROMPT")
        monkeypatch.setattr(review_phases, "run_agent", _agent)
        with review_gc.cleaned_on_success(Path(job.artifact_dir)):
            review_pipeline.run_single_agent(job, disprove=False)
        return job

    def test_disprove_artifacts_do_not_survive(self, tmp_path, monkeypatch):
        (tmp_path / "disprove.md").write_text("- [M1] SURVIVES — real\n")
        (tmp_path / "disprove.jsonl").write_text(_result("success") + "\n")
        (tmp_path / "prompt-single.md").write_text("PROMPT")

        self._run(tmp_path, monkeypatch)

        assert not (tmp_path / "disprove.md").exists()
        assert not (tmp_path / "disprove.jsonl").exists()
        assert not (tmp_path / "prompt-single.md").exists()

    def test_the_deliverable_and_its_sidecars_are_kept(self, tmp_path, monkeypatch):
        job = self._run(tmp_path, monkeypatch)

        assert Path(job.review_file).exists()
        assert Path(job.session_log).exists()
        assert (tmp_path / "meta.json").exists()


_PINNED_SHA = "old1234"
_GROUPS = [
    review_types.Group(name="g1", files=["a.py"], lines=10),
    review_types.Group(name="g2", files=["b.py"], lines=10),
]


def _write_state(tmp_path: Path, **overrides) -> Path:
    """Write a pipeline state file.

    Defaults represent an incomplete run: holistic done, no groups done,
    group "0" failed with "quota", synthesis not yet attempted. Pass
    overrides to model other recovery shapes — a `done` set that names
    synthesis, alongside a different `groups_failed`, models synthesis
    crashing after the groups completed.
    """
    path = tmp_path / "pipeline.json"
    state = {
        "head_sha": _PINNED_SHA, "group_names": ["g1", "g2"],
        "done": ["holistic"], "failed": {},
        "groups_done": [], "groups_failed": {"0": "quota"},
    }
    path.write_text(json.dumps({**state, **overrides}))
    return path


class TestResolveRecoveryPinnedMetadata:
    """--recover pins job metadata to the failed run's SHA so state survives."""

    def test_pinned_metadata_resumes_the_partial_pipeline(self, tmp_path):
        state_path = _write_state(tmp_path)
        job = _make_job(tmp_path, head_sha=_PINNED_SHA)

        plan = review_state._resolve_recovery(job, _GROUPS)

        assert plan.state is not None
        assert plan.state.scanned is True
        assert plan.skip_groups is None
        assert plan.already_complete is False
        assert state_path.exists()

    def test_pinned_metadata_reruns_only_the_failed_groups(self, tmp_path):
        """The common failure shape: synthesis ran, one group errored out."""
        _write_state(
            tmp_path, groups_done=[0], groups_failed={"1": "quota"},
            done=["holistic", "synthesis"], failed={"synthesis": "crashed"},
        )
        job = _make_job(tmp_path, head_sha=_PINNED_SHA)

        plan = review_state._resolve_recovery(job, _GROUPS)

        assert plan.skip_groups == {0}
        assert plan.state is not None
        assert plan.state.groups_failed == {}
        # A phase being retried is neither done nor failed any more, so the
        # scan it does not retry is all that is left behind.
        assert plan.state.done == {Phase.HOLISTIC}
        assert plan.state.failed == {}

    def test_pinned_metadata_on_a_clean_run_recovers_nothing(self, tmp_path):
        _write_state(
            tmp_path, groups_done=[0, 1], groups_failed={},
            done=["holistic", "synthesis", "disprove"],
        )
        job = _make_job(tmp_path, head_sha=_PINNED_SHA)

        plan = review_state._resolve_recovery(job, _GROUPS)

        assert plan.state is None
        assert plan.skip_groups is None
        # The caller aborts on this rather than starting over, and told apart
        # from "no state" without re-reading the file.
        assert plan.already_complete is True

    def test_a_state_that_never_reached_the_gate_resumes_at_it(self, tmp_path):
        """The shape a run killed inside the disprove gate leaves behind.

        Synthesis is the last phase to record itself, so this used to read as a
        finished review and `--recover` declined it.
        """
        _write_state(
            tmp_path, groups_done=[0, 1], groups_failed={},
            done=["holistic", "synthesis"],
        )
        job = _make_job(tmp_path, head_sha=_PINNED_SHA)

        plan = review_state._resolve_recovery(job, _GROUPS)

        assert plan.already_complete is False
        assert plan.resume_at_gate is True
        # Every group succeeded, so the resumed run re-runs none of them and
        # keeps the synthesis they already paid for.
        assert plan.skip_groups == {0, 1}
        assert plan.state is not None
        assert plan.state.done == {Phase.HOLISTIC, Phase.SYNTHESIS}

    def test_a_failed_group_under_a_missing_gate_still_resynthesises(self, tmp_path):
        """A group re-running is new output, so the prior synthesis cannot stand."""
        _write_state(
            tmp_path, groups_done=[0], groups_failed={"1": "quota"},
            done=["holistic", "synthesis"],
        )
        job = _make_job(tmp_path, head_sha=_PINNED_SHA)

        plan = review_state._resolve_recovery(job, _GROUPS)

        assert plan.resume_at_gate is False
        assert plan.skip_groups == {0}

    def test_unpinned_metadata_discards_the_state(self, tmp_path):
        state_path = _write_state(tmp_path)
        job = _make_job(tmp_path, head_sha="new5678")

        plan = review_state._resolve_recovery(job, _GROUPS)

        assert plan.state is None
        assert plan.already_complete is False
        assert not state_path.exists()


class TestCompleteReviewReadsSectionConstants:
    """A finished review is recognised by the constant, not by a typed string.

    `is_complete_review` gates whether a resumed run re-enters synthesis. While
    it matched "## Summary" as a literal, renaming the section would have made
    every finished review look unfinished and sent every resumed run back
    through an agent it did not need.
    """

    def test_summary_section_marks_a_review_complete(self, tmp_path):
        from review_document import SECTION_SUMMARY
        f = tmp_path / "review.md"
        f.write_text(f"## {SECTION_SUMMARY}\n\nAll good.\n")
        assert review_outcome.is_complete_review(str(f))

    def test_verdict_section_marks_a_review_complete(self, tmp_path):
        from review_document import SECTION_VERDICT
        f = tmp_path / "review.md"
        f.write_text(f"## {SECTION_VERDICT}\n\nApprove.\n")
        assert review_outcome.is_complete_review(str(f))

    def test_a_body_with_neither_section_is_incomplete(self, tmp_path):
        f = tmp_path / "review.md"
        f.write_text("- **[M1]** **`x.py:1`** — a finding\n")
        assert not review_outcome.is_complete_review(str(f))
