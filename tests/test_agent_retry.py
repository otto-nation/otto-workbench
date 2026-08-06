"""Tests for the thrash guard shared across the pr scripts.

review_pipeline's own retry behaviour is covered in test_review_pipeline_retry;
these cover the generalisations the other pr scripts depend on — an arbitrary
`produced` predicate and the log-less prompt path.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

from conftest import write_thrash_log
import agent_retry
import review_agent
import review_pipeline

_TURNS = 15
_MAX_TURNS = f"agent hit max turns ({_TURNS})"
_NO_WRITE = f"agent completed (subtype=success) — {review_agent.DIAG_NO_WRITE_TOOL_CALL}"


def _write_log(tmp_path: Path, payload: dict) -> str:
    path = tmp_path / "session.jsonl"
    path.write_text(json.dumps(payload) + "\n")
    return str(path)


class TestPipelineDelegatesToSharedGuard:
    """review_pipeline must not carry a second copy of the guard."""

    def test_retryability_is_the_same_function(self):
        assert review_pipeline._is_retryable is agent_retry.is_retryable

    def test_retry_driver_is_the_same_function(self):
        assert review_pipeline._retry_missing_output is agent_retry.retry_missing_output

    def test_hints_are_the_same_strings(self):
        assert review_pipeline._NO_WRITE_HINT is agent_retry.NO_WRITE_HINT
        assert review_pipeline._RETRY_HINT is agent_retry.RETRY_HINT
        assert review_pipeline._FIX_RETRY_HINT is agent_retry.FIX_RETRY_HINT

    def test_group_retry_ceiling_comes_from_the_shared_cap(self):
        assert review_pipeline.RETRY_MAX_TURNS_GROUP == agent_retry.RETRY_MAX_TURNS


class TestRetryUnproductive:
    """The `produced` predicate lets a fix pass report progress its own way."""

    def test_no_retry_when_the_predicate_is_already_satisfied(self, tmp_path):
        calls = []
        reason = agent_retry.retry_unproductive(
            lambda p, t: calls.append((p, t)) or 0,
            "PROMPT", write_thrash_log(tmp_path / "session.jsonl"),
            label="fix", max_turns=_TURNS, produced=lambda: True,
        )
        assert reason == ""
        assert calls == []

    def test_retries_once_when_nothing_was_produced(self, tmp_path):
        """`produced` is False going into the retry and True coming out of it."""
        log_path = write_thrash_log(tmp_path / "session.jsonl")
        calls = []
        output = []

        def invoke(prompt, turns):
            calls.append((prompt, turns))
            output.append(True)
            return 0

        reason = agent_retry.retry_unproductive(
            invoke, "PROMPT", log_path,
            label="fix", max_turns=_TURNS,
            produced=lambda: bool(output),
        )
        assert reason == ""
        assert len(calls) == 1

    def test_a_retry_that_also_produces_nothing_is_not_retried_again(self, tmp_path):
        """One retry is the cap — and the second diagnosis is reported, not swallowed."""
        calls = []
        reason = agent_retry.retry_unproductive(
            lambda p, t: calls.append(p) or 0,
            "PROMPT", write_thrash_log(tmp_path / "session.jsonl"),
            label="fix", max_turns=_TURNS, produced=lambda: False,
        )
        assert len(calls) == 1
        assert review_agent.DIAG_NO_WRITE_TOOL_CALL in reason

    def test_retry_prompt_carries_the_selected_hint(self, tmp_path):
        log_path = write_thrash_log(tmp_path / "session.jsonl")
        calls = []
        agent_retry.retry_unproductive(
            lambda p, t: calls.append((p, t)) or 0,
            "PROMPT", log_path,
            label="fix", max_turns=_TURNS, produced=lambda: False,
            hint_select=lambda reason: agent_retry.FIX_RETRY_HINT,
        )
        assert len(calls) == 1
        assert calls[0][0] == agent_retry.FIX_RETRY_HINT + "PROMPT"

    def test_recover_runs_before_the_run_is_written_off(self, tmp_path):
        salvaged = []
        calls = []
        reason = agent_retry.retry_unproductive(
            lambda p, t: calls.append(p) or 0,
            "PROMPT", write_thrash_log(tmp_path / "session.jsonl"),
            label="fix", max_turns=_TURNS,
            produced=lambda: bool(salvaged),
            recover=lambda: salvaged.append(True),
        )
        assert reason == ""
        assert calls == []

    def test_non_retryable_reason_is_returned_without_a_second_attempt(self, tmp_path):
        log_path = _write_log(tmp_path, {
            "type": "result", "subtype": "error", "is_error": True,
            "result": "permission denied",
        })
        calls = []
        reason = agent_retry.retry_unproductive(
            lambda p, t: calls.append(p) or 0,
            "PROMPT", log_path,
            label="fix", max_turns=_TURNS, produced=lambda: False,
        )
        assert calls == []
        assert "permission denied" in reason


class TestRunGuarded:
    """`retry_unproductive` is post-hoc; `run_guarded` owns the first attempt."""

    def test_first_attempt_runs_even_when_the_predicate_is_satisfied(self, tmp_path):
        """A leftover artifact from an earlier pass must not skip the run."""
        calls = []
        reason = agent_retry.run_guarded(
            lambda p, t: calls.append((p, t)) or 0,
            "PROMPT", write_thrash_log(tmp_path / "session.jsonl"),
            label="fix", max_turns=_TURNS, produced=lambda: True,
        )
        assert reason == ""
        assert calls == [("PROMPT", _TURNS)]

    def test_unproductive_first_attempt_is_followed_by_a_hinted_retry(self, tmp_path):
        calls = []
        agent_retry.run_guarded(
            lambda p, t: calls.append(p) or 0,
            "PROMPT", write_thrash_log(tmp_path / "session.jsonl"),
            label="fix", max_turns=_TURNS, produced=lambda: False,
            hint_select=lambda reason: agent_retry.FIX_RETRY_HINT,
        )
        assert calls == ["PROMPT", agent_retry.FIX_RETRY_HINT + "PROMPT"]


class TestRetryBlankResponse:
    """The prompt path has no session log — the answer is the only signal."""

    def test_usable_answer_is_returned_untouched(self):
        calls = []

        def call(prompt):
            calls.append(prompt)
            return "{}", 0

        assert agent_retry.retry_blank_response(
            call, "PROMPT", label="triage", usable=lambda s: True,
        ) == ("{}", 0)
        assert calls == ["PROMPT"]

    def test_unparseable_answer_earns_one_retry_with_a_hint(self):
        calls = []

        def call(prompt):
            calls.append(prompt)
            return ("{}", 0) if len(calls) == 2 else ("sorry", 0)

        out, rc = agent_retry.retry_blank_response(
            call, "PROMPT", label="triage", usable=lambda s: s == "{}",
        )
        assert (out, rc) == ("{}", 0)
        assert calls[1] == agent_retry.BLANK_RESPONSE_HINT + "PROMPT"

    def test_retries_at_most_once(self):
        calls = []

        def call(prompt):
            calls.append(prompt)
            return "sorry", 0

        out, rc = agent_retry.retry_blank_response(
            call, "PROMPT", label="triage", usable=lambda s: False,
        )
        assert (out, rc) == ("sorry", 0)
        assert len(calls) == 2

    def test_nonzero_exit_is_not_retried(self):
        """The backend already reported why; the same call would reproduce it."""
        calls = []

        def call(prompt):
            calls.append(prompt)
            return "", 1

        assert agent_retry.retry_blank_response(
            call, "PROMPT", label="triage", usable=lambda s: False,
        ) == ("", 1)
        assert len(calls) == 1


class TestSharedRetryability:
    def test_clean_completion_without_a_write_is_retryable(self):
        assert agent_retry.is_retryable(_NO_WRITE)

    def test_max_turns_is_retryable(self):
        assert agent_retry.is_retryable(_MAX_TURNS)

    def test_skipped_is_not_retryable(self):
        assert not agent_retry.is_retryable("skipped: 3 consecutive failures")

    # hint_for priority order: no-write > max-turns > nothing.
    # These three tests pin that ordering — changing precedence must update all three.

    def test_no_write_hint_beats_the_max_turns_hint(self):
        both = f"{_MAX_TURNS} — {review_agent.DIAG_NO_WRITE_TOOL_CALL}"
        assert agent_retry.hint_for(both) == agent_retry.NO_WRITE_HINT

    def test_max_turns_alone_still_gets_the_max_turns_hint(self):
        """The priority above must not have swallowed the less specific case."""
        assert agent_retry.hint_for(_MAX_TURNS) == agent_retry.RETRY_HINT

    def test_a_reason_with_no_matching_hint_adds_nothing(self):
        assert agent_retry.hint_for("agent error: overloaded") == ""
