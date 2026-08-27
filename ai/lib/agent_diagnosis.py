"""Why an agent run produced no output, classified once and rendered on demand.

Every backend invocation can end without the file it was asked to write, and
nine callers across the review pipeline, the fix engine and the retry policy
need to tell those endings apart. `DiagnosisKind` is the vocabulary they switch
on and `Diagnosis` is what they store, compare and print.

Nothing here is review-specific, which is why it is not in the review layer: a
diagnosis is a property of the invocation, and the pipeline that happens to be
running is not part of the reason it failed.
"""

# doc-group: pipeline

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import serde


class DiagnosisKind(StrEnum):
    """Why an agent run left no output.

    Retry policy switches on this, so a member is added when a *decision* needs
    to tell one outcome from another — not when a message needs new wording.
    """

    MAX_TURNS = "max_turns"
    COMPLETED = "completed"
    AGENT_ERROR = "agent_error"
    TRANSIENT = "transient"
    QUOTA_EXHAUSTED = "quota_exhausted"
    NO_SESSION_LOG = "no_session_log"
    NO_RESULT_RECORD = "no_result_record"
    # The three below are the pipeline's own verdicts, reached without ever
    # reading a session log: a group the pipeline declined to run, one abandoned
    # when the budget ran out, and one whose output vanished between passes.
    SKIPPED = "skipped"
    BUDGET_EXCEEDED = "budget_exceeded"
    OUTPUT_MISSING = "output_missing"
    # Reached before any agent starts: the phase's prompt is over the token
    # budget with every lever in `review_prompt` already pulled. Its own kind
    # because nothing an agent does changes it — the retry paths would re-render
    # the same bytes, so the phase is failed rather than attempted.
    PROMPT_TOO_LARGE = "prompt_too_large"
    # Synthesis's own outcomes. Recorded against the pipeline rather than an
    # agent, so neither has a session log behind it: no group produced usable
    # output, and a synthesis that degraded to the mechanical merge.
    ALL_GROUPS_FAILED = "all_groups_failed"
    MECHANICAL_FALLBACK = "mechanical_fallback"
    # Only reachable by reading a pipeline state file written before failures
    # were structured. `detail` holds that file's rendered message verbatim.
    UNKNOWN = "unknown"


# Prefixes every backend crash. Load-bearing beyond rendering: the no-write
# check and the transient-error check both use it to tell a crash apart from a
# run that ended on its own terms.
_AGENT_ERROR_PREFIX = "agent error:"

# A backend error whose text matches one of these will fail again the same way,
# so no amount of retrying or recovery helps. Matched against `Diagnosis.detail`
# the way `_TRANSIENT_ERROR_MARKERS` is — the error text is free-form, and these
# are the fragments of it that carry a verdict.
_NON_RECOVERABLE_ERROR_MARKERS = ("permission denied",)

_DIAGNOSIS_MESSAGES = {
    DiagnosisKind.QUOTA_EXHAUSTED: "quota exhausted (429)",
    DiagnosisKind.NO_SESSION_LOG: "no session log found",
    DiagnosisKind.NO_RESULT_RECORD: "no result record in session log",
    DiagnosisKind.BUDGET_EXCEEDED: "budget exceeded",
    DiagnosisKind.OUTPUT_MISSING: "output missing",
    DiagnosisKind.ALL_GROUPS_FAILED: "all groups failed",
    DiagnosisKind.MECHANICAL_FALLBACK: "mechanical fallback",
}

# Every constant message, reversed. A state file written before a message was a
# kind holds the rendered text; this reads it back as the kind it renders as,
# rather than burying it in `UNKNOWN`. Derived, so a new message is covered
# without a second edit. Kinds whose message interpolates `detail` are absent
# from the forward map and so stay verbatim under `UNKNOWN`, as before.
_MESSAGE_KINDS = {message: kind for kind, message in _DIAGNOSIS_MESSAGES.items()}

_NO_WRITE_TOOL_SUFFIX = "never called a file-writing tool"


@dataclass(frozen=True)
class Diagnosis:
    """A single agent run's failure, classified once and rendered on demand.

    Frozen because two of the pipeline's decisions compare diagnoses — the
    consecutive-failure abort and the all-groups-failed circuit breaker — and
    one of them puts them in a set. The abort asks `same_reason_as` rather than
    comparing the whole record, so a detail that measures the failure instead of
    naming it does not read as a new reason each time.
    """

    kind: DiagnosisKind
    no_write_tool: bool = False
    detail: str = ""
    # None when the backend reported no turn count; rendered as "?".
    num_turns: int | None = None

    @property
    def message(self) -> str:
        """The human-readable reason, as it appears in logs and review files."""
        return self._base_message() + (
            f" — {_NO_WRITE_TOOL_SUFFIX}" if self.no_write_tool else ""
        )

    def _base_message(self) -> str:
        if self.kind is DiagnosisKind.MAX_TURNS:
            turns = self.num_turns if self.num_turns is not None else "?"
            return f"agent hit max turns ({turns})"
        if self.kind is DiagnosisKind.COMPLETED:
            return f"agent completed (subtype={self.detail}) but did not write output"
        if self.kind in (DiagnosisKind.AGENT_ERROR, DiagnosisKind.TRANSIENT):
            return f"{_AGENT_ERROR_PREFIX} {self.detail}"
        if self.kind is DiagnosisKind.SKIPPED:
            return f"skipped: {self.detail}"
        if self.kind is DiagnosisKind.PROMPT_TOO_LARGE:
            return f"prompt too large: {self.detail}"
        if self.kind is DiagnosisKind.UNKNOWN:
            # A legacy state file could hold an empty reason; the failures table
            # gets a word rather than a blank cell.
            return self.detail or DiagnosisKind.UNKNOWN.value
        return _DIAGNOSIS_MESSAGES[self.kind]

    def same_reason_as(self, other: "Diagnosis | None") -> bool:
        """Whether `other` failed for the same reason, for counting a streak.

        Two diagnoses that are equal always answer yes, and a `None` — no prior
        failure — always answers no. The detail is normally part of the reason,
        one agent error being a different cause from another. A prompt over the
        budget is the exception: its detail is the measurement, and every group
        measures a different number of kilobytes. Comparing whole records there
        makes a run structurally unable to prompt any group look like a run
        failing for a new reason each time, so the consecutive-failure abort
        never trips and every remaining group is visited.
        """
        if other is None:
            return False
        if self.kind is not other.kind:
            return False
        if self.kind is DiagnosisKind.PROMPT_TOO_LARGE:
            return True
        return self == other

    @property
    def recoverable(self) -> bool:
        """Whether `pr review --recover` could plausibly do better than this run.

        A prompt over the budget is not: recovery re-renders the same phase from
        the same commit, so it produces the same oversized prompt. What changes
        the answer is a smaller review, not a second attempt at this one.
        """
        if self.kind is DiagnosisKind.PROMPT_TOO_LARGE:
            return False
        lowered = self.detail.lower()
        return not any(m in lowered for m in _NON_RECOVERABLE_ERROR_MARKERS)

    @classmethod
    def _from_raw(cls, raw) -> "Diagnosis | None":
        """Rebuild a diagnosis from any shape a state file can hold.

        `serde` hands the whole field over here rather than assuming a dict,
        because reviews live in `~/.local/state/workbench/reviews/` and outlive the
        code that wrote them. A file written before diagnoses were typed holds
        the rendered reason — recover the kind where the text names one, and
        keep the rest verbatim under `UNKNOWN`, so a `--recover` run against an
        in-flight review still renders its failures.

        Returns None for a raw value that records no failure at all: an optional
        field written before it was optional holds `""`, and reading that as a
        blank diagnosis would turn a clean run into a failed one.
        """
        if not raw:
            return None
        if isinstance(raw, cls):
            return raw
        if isinstance(raw, dict):
            return serde.from_dict(cls, raw)
        text = str(raw)
        if text in _MESSAGE_KINDS:
            return cls(_MESSAGE_KINDS[text])
        return cls(DiagnosisKind.UNKNOWN, detail=text)

    @classmethod
    def _raw_schema(cls, object_schema: dict) -> dict:
        """Both shapes `_from_raw` reads, for anything that publishes a schema
        over a diagnosis. The bare string is the pre-typed form a review file
        written by an older run still holds, and a schema naming only the
        object would call that file invalid where the reader accepts it."""
        return {"oneOf": [object_schema, {"type": "string"}]}
