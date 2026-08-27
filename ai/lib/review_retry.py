"""Retry and diagnosis routing for the review pipeline.

Everything here decides *what to do* about an agent that produced nothing —
whether the failure is worth another attempt, how many turns that attempt gets,
how the reason renders, and when a run of failures is systemic enough to stop
the pipeline. None of it runs a phase, so it stays callable from the phase
executors and the orchestration layer alike.

The hints, the retryability test and the retry driver are shared with the other
`pr` scripts — see agent_retry. Aliased here so the review modules keep reading
the way they always have.
"""

# doc-group: pipeline

from __future__ import annotations

from dataclasses import dataclass

import agent_retry
from agent_diagnosis import Diagnosis, DiagnosisKind
from review_agent import CONSECUTIVE_FAIL_THRESHOLD, _is_model_error

_has_output = agent_retry.has_output

_RETRY_HINT = agent_retry.RETRY_HINT
_NO_WRITE_HINT = agent_retry.NO_WRITE_HINT

_retry_hint_for = agent_retry.hint_for
_retry_turns_for = agent_retry.turns_for
_retry_missing_output = agent_retry.retry_missing_output
_is_retryable = agent_retry.is_retryable


def _render_reason(diagnosis: "Diagnosis | None") -> str:
    """The rendered reason for a phase that produced nothing.

    A `None` only reaches here when the output disappeared after the retry
    driver had already confirmed it — worth reporting, not worth crashing on.
    """
    return diagnosis.message if diagnosis else "unknown"


@dataclass(frozen=True)
class GroupFailure:
    """One group review that produced nothing, and why.

    Carried rather than the rendered message because the retry pass, the
    consecutive-failure abort, and the circuit breaker all decide from the
    diagnosis; only the merge and the failures table render it.
    """

    group: str
    diagnosis: Diagnosis


def _was_skipped(failure: "GroupFailure") -> bool:
    return failure.diagnosis.kind is DiagnosisKind.SKIPPED


def _check_serial_abort(
    i: int, group_count: int, diagnosis: Diagnosis, log_path: str,
    consecutive: int, last: "Diagnosis | None",
) -> "tuple[str, int, Diagnosis | None]":
    if _is_model_error(log_path):
        return f"Model not available — aborting remaining {group_count - i} groups", 0, None
    consecutive = consecutive + 1 if diagnosis.same_reason_as(last) else 1
    if consecutive >= CONSECUTIVE_FAIL_THRESHOLD:
        return f"{CONSECUTIVE_FAIL_THRESHOLD} consecutive failures ({diagnosis.message}) — aborting remaining {group_count - i} groups", 0, None
    return "", consecutive, diagnosis
