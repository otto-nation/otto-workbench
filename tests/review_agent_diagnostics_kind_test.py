"""Tests for agent.diagnosis: the message each kind renders and whether it is recoverable."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

from agent.diagnosis import Diagnosis, DiagnosisKind

from review_agent_diagnostics_support import _TURNS, _NO_WRITE_SUFFIX

_MAX_TURNS_REASON = f"agent hit max turns ({_TURNS})"


class TestDiagnosisMessage:
    """Every kind renders the exact string the pipeline emitted before typing.

    These messages reach the review file's Agent Failures table and a user's
    terminal, so the refactor has to be invisible in the output.
    """

    @pytest.mark.parametrize("diagnosis,expected", [
        (
            Diagnosis(DiagnosisKind.MAX_TURNS, num_turns=_TURNS),
            _MAX_TURNS_REASON,
        ),
        (
            Diagnosis(DiagnosisKind.MAX_TURNS, num_turns=_TURNS, no_write_tool=True),
            _MAX_TURNS_REASON + _NO_WRITE_SUFFIX,
        ),
        # The backend reported no turn count — rendered as "?", as it always was.
        (Diagnosis(DiagnosisKind.MAX_TURNS), "agent hit max turns (?)"),
        (
            Diagnosis(DiagnosisKind.COMPLETED, detail="success"),
            "agent completed (subtype=success) but did not write output",
        ),
        (
            Diagnosis(DiagnosisKind.COMPLETED, detail="success", no_write_tool=True),
            "agent completed (subtype=success) but did not write output" + _NO_WRITE_SUFFIX,
        ),
        (
            Diagnosis(DiagnosisKind.AGENT_ERROR, detail="spawn ENOENT"),
            "agent error: spawn ENOENT",
        ),
        (
            Diagnosis(DiagnosisKind.TRANSIENT, detail="ECONNRESET"),
            "agent error: ECONNRESET",
        ),
        (Diagnosis(DiagnosisKind.QUOTA_EXHAUSTED), "quota exhausted (429)"),
        (Diagnosis(DiagnosisKind.NO_SESSION_LOG), "no session log found"),
        (Diagnosis(DiagnosisKind.NO_RESULT_RECORD), "no result record in session log"),
        (Diagnosis(DiagnosisKind.BUDGET_EXCEEDED), "budget exceeded"),
        (Diagnosis(DiagnosisKind.OUTPUT_MISSING), "output missing"),
        (
            Diagnosis(DiagnosisKind.SKIPPED, detail="3 consecutive failures"),
            "skipped: 3 consecutive failures",
        ),
        # A reason read back from a state file written before failures were
        # structured — carried through verbatim.
        (
            Diagnosis(DiagnosisKind.UNKNOWN, detail="something the old code said"),
            "something the old code said",
        ),
    ])
    def test_renders_legacy_string(self, diagnosis, expected):
        assert diagnosis.message == expected

    def test_every_kind_renders(self):
        """No kind can be added without deciding how it reads."""
        for kind in DiagnosisKind:
            assert Diagnosis(kind).message


class TestDiagnosisRecoverable:
    def test_permission_denial_is_not_recoverable(self):
        diagnosis = Diagnosis(
            DiagnosisKind.AGENT_ERROR, detail="Permission denied writing /tmp/out.md",
        )
        assert not diagnosis.recoverable

    def test_turn_exhaustion_is_recoverable(self):
        assert Diagnosis(DiagnosisKind.MAX_TURNS, num_turns=_TURNS).recoverable

    def test_prompt_rejected_by_the_api_is_not_recoverable(self):
        """The API's own refusal reads the same as the local budget's.

        A prompt inside the byte ceiling can still exceed the token limit, so
        this arrives as a backend error rather than as `PROMPT_TOO_LARGE`.
        Recovery re-renders the same phase from the same commit, so it is the
        same prompt and the same refusal.
        """
        diagnosis = Diagnosis(
            DiagnosisKind.AGENT_ERROR, detail="API Error: Prompt is too long",
        )
        assert not diagnosis.recoverable

    def test_local_budget_refusal_is_not_recoverable(self):
        assert not Diagnosis(
            DiagnosisKind.PROMPT_TOO_LARGE, detail="group prompt is 512KB",
        ).recoverable
