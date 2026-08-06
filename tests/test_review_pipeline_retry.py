"""Tests for the single-agent retry and multi-phase recovery resolution."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import review_agent
import review_pipeline
import review_preflight

_TURNS = 15
_MAX_TURNS = f"agent hit max turns ({_TURNS})"
_NO_WRITE = f"{_MAX_TURNS} — {review_agent.DIAG_NO_WRITE_TOOL_CALL}"
_TRANSIENT = "agent error: ECONNRESET"


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
        reviews_dir=str(tmp_path),
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
        assert review_pipeline._retry_hint_for(_NO_WRITE) == review_pipeline._NO_WRITE_HINT

    def test_plain_max_turns_gets_the_generic_hint(self):
        assert review_pipeline._retry_hint_for(_MAX_TURNS) == review_pipeline._RETRY_HINT

    def test_transient_error_gets_no_hint(self):
        assert review_pipeline._retry_hint_for(_TRANSIENT) == ""

    def test_missing_result_record_gets_no_hint(self):
        assert review_pipeline._retry_hint_for(review_agent.DIAG_NO_RESULT_RECORD) == ""


class TestIsRetryable:
    def test_clean_completion_without_a_write_is_retryable(self):
        """The motivating failure: finished cleanly, produced nothing."""
        reason = (
            "agent completed (subtype=success) but did not write output"
            f" — {review_agent.DIAG_NO_WRITE_TOOL_CALL}"
        )
        assert review_pipeline._is_retryable(reason)
        assert review_pipeline._retry_hint_for(reason) == review_pipeline._NO_WRITE_HINT

    def test_clean_completion_that_wrote_nothing_observable_is_not_retryable(self):
        """Without the no-write suffix there is no reason to expect a difference."""
        assert not review_pipeline._is_retryable(
            "agent completed (subtype=success) but did not write output"
        )

    def test_no_write_completion_keeps_its_turn_budget(self):
        reason = f"agent completed (subtype=success) — {review_agent.DIAG_NO_WRITE_TOOL_CALL}"
        assert review_pipeline._retry_turns_for(reason, 15) == 15


class TestRetryTurnsFor:
    def test_max_turns_doubles(self):
        assert review_pipeline._retry_turns_for(_MAX_TURNS, 15) == 30

    def test_doubling_is_capped_at_the_group_ceiling(self):
        assert review_pipeline._retry_turns_for(_MAX_TURNS, 20) == review_pipeline.RETRY_MAX_TURNS_GROUP

    def test_budget_above_the_ceiling_is_not_lowered(self):
        assert review_pipeline._retry_turns_for(_MAX_TURNS, 40) == 40

    def test_non_turn_failures_keep_their_budget(self):
        assert review_pipeline._retry_turns_for(_TRANSIENT, 15) == 15


class TestRetryMissingOutput:
    def _run(self, invoke, log_path, output_path, max_turns=_TURNS):
        return review_pipeline._retry_missing_output(
            invoke, "PROMPT", log_path, output_path,
            label="Test phase", max_turns=max_turns,
        )

    def test_existing_output_skips_the_retry(self, tmp_path):
        log_path = _write_log(tmp_path, _result())
        output = tmp_path / "out.md"
        output.write_text("## Summary\n")
        invoke = _Invoke()
        assert self._run(invoke, log_path, str(output)) == ""
        assert invoke.calls == []

    def test_non_retryable_reason_returns_without_retrying(self, tmp_path):
        log_path = _write_log(tmp_path, _result("error", is_error=True, result="permission denied"))
        output = tmp_path / "out.md"
        invoke = _Invoke()
        reason = self._run(invoke, log_path, str(output))
        assert "permission denied" in reason
        assert invoke.calls == []

    def test_retry_prefixes_the_hint_and_raises_the_budget(self, tmp_path):
        log_path = _write_log(tmp_path, _result())
        output = tmp_path / "out.md"
        invoke = _Invoke(str(output), write_on=1, log_path=log_path)
        assert self._run(invoke, log_path, str(output)) == ""
        prompt, turns = invoke.calls[0]
        assert prompt == review_pipeline._RETRY_HINT + "PROMPT"
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
        assert invoke.calls[0][0].startswith(review_pipeline._NO_WRITE_HINT)

    def test_retry_runs_once_and_reports_its_own_failure(self, tmp_path):
        log_path = _write_log(tmp_path, _result())
        output = tmp_path / "out.md"
        invoke = _Invoke(str(output), write_on=0, log_path=log_path)
        reason = self._run(invoke, log_path, str(output))
        assert len(invoke.calls) == 1
        assert reason == "agent completed (subtype=success) but did not write output"

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
        monkeypatch.setattr(review_pipeline, "invoke_agent", lambda *a, **k: next(codes))
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
        assert self._run(invoke, log_path, str(output)) == ""
        assert invoke.calls == []
        assert "## Summary" in output.read_text()


_PINNED_SHA = "old1234"
_GROUPS = [
    review_preflight.Group(name="g1", files=["a.py"], lines=10),
    review_preflight.Group(name="g2", files=["b.py"], lines=10),
]


def _write_state(tmp_path: Path, **overrides) -> Path:
    path = tmp_path / "pipeline.json"
    state = {
        "head_sha": _PINNED_SHA, "group_names": ["g1", "g2"], "holistic_done": True,
        "groups_done": [], "groups_failed": {"0": "quota"},
        "synthesis_done": False, "synthesis_failed": "",
    }
    path.write_text(json.dumps({**state, **overrides}))
    return path


class TestResolveRecoveryPinnedMetadata:
    """--recover pins job metadata to the failed run's SHA so state survives."""

    def test_pinned_metadata_resumes_the_partial_pipeline(self, tmp_path):
        state_path = _write_state(tmp_path)
        job = _make_job(tmp_path, head_sha=_PINNED_SHA)

        _, skip_groups, skip_holistic, state = review_pipeline._resolve_recovery(
            job, _GROUPS,
        )

        assert state is not None
        assert skip_holistic is True
        assert skip_groups is None
        assert state_path.exists()

    def test_pinned_metadata_reruns_only_the_failed_groups(self, tmp_path):
        """The common failure shape: synthesis ran, one group errored out."""
        _write_state(
            tmp_path, groups_done=[0], groups_failed={"1": "quota"},
            synthesis_done=True, synthesis_failed="crashed",
        )
        job = _make_job(tmp_path, head_sha=_PINNED_SHA)

        _, skip_groups, _, state = review_pipeline._resolve_recovery(job, _GROUPS)

        assert skip_groups == {0}
        assert state is not None
        assert state.groups_failed == {}
        assert state.synthesis_done is False

    def test_pinned_metadata_on_a_clean_run_recovers_nothing(self, tmp_path):
        _write_state(
            tmp_path, groups_done=[0, 1], groups_failed={}, synthesis_done=True,
        )
        job = _make_job(tmp_path, head_sha=_PINNED_SHA)

        _, skip_groups, _, state = review_pipeline._resolve_recovery(job, _GROUPS)

        assert state is None
        assert skip_groups is None

    def test_unpinned_metadata_discards_the_state(self, tmp_path):
        state_path = _write_state(tmp_path)
        job = _make_job(tmp_path, head_sha="new5678")

        _, _, _, state = review_pipeline._resolve_recovery(job, _GROUPS)

        assert state is None
        assert not state_path.exists()
