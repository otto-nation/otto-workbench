"""Tests for review_agent failure diagnosis."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import review_agent
import review_retry
from review_common import Diagnosis, DiagnosisKind


# Arbitrary — the diagnosis echoes whatever num_turns the result record carries,
# so the value only has to be distinguishable from the pipeline's turn defaults.
_TURNS = 16
_MAX_TURNS_REASON = f"agent hit max turns ({_TURNS})"
_NO_WRITE_SUFFIX = " — never called a file-writing tool"


def _tool_use(name: str, **inp) -> str:
    return json.dumps({
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "name": name, "input": inp}]},
    })


def _text(text: str) -> str:
    return json.dumps({
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": text}]},
    })


def _result(subtype: str = "error_max_turns", num_turns: int = _TURNS) -> str:
    return json.dumps({
        "type": "result", "subtype": subtype, "num_turns": num_turns,
    })


def _write_log(tmp_path: Path, *lines: str) -> str:
    path = tmp_path / "session.jsonl"
    path.write_text("\n".join(lines) + "\n")
    return str(path)


class TestDiagnoseMissingOutput:
    def test_max_turns_without_write_tool_names_the_thrash(self, tmp_path):
        log_path = _write_log(
            tmp_path,
            _tool_use("Read", file_path="/tmp/wt/a.py"),
            _tool_use("Bash", command="ls"),
            _result(),
        )
        diagnosis = review_agent.diagnose_missing_output(log_path)
        assert diagnosis.kind is DiagnosisKind.MAX_TURNS
        assert diagnosis.no_write_tool

    def test_max_turns_with_edit_call_stays_plain(self, tmp_path):
        log_path = _write_log(
            tmp_path,
            _tool_use("Read", file_path="/tmp/out.md"),
            _tool_use("Edit", file_path="/tmp/out.md", old_string=""),
            _result(),
        )
        diagnosis = review_agent.diagnose_missing_output(log_path)
        assert diagnosis == Diagnosis(DiagnosisKind.MAX_TURNS, num_turns=_TURNS)

    def test_no_assistant_records_stays_plain(self, tmp_path):
        """Non-Claude backends log no tool_use — absence is not evidence."""
        log_path = _write_log(tmp_path, _result())
        diagnosis = review_agent.diagnose_missing_output(log_path)
        assert diagnosis == Diagnosis(DiagnosisKind.MAX_TURNS, num_turns=_TURNS)

    def test_crash_is_not_labelled_a_no_write_failure(self, tmp_path):
        """The error explains the missing output; a retry would reproduce it."""
        log_path = _write_log(
            tmp_path,
            _tool_use("Read", file_path="/tmp/a"),
            json.dumps({
                "type": "result", "subtype": "error", "is_error": True,
                "result": "spawn ENOENT",
            }),
        )
        diagnosis = review_agent.diagnose_missing_output(log_path)
        assert diagnosis.kind is DiagnosisKind.AGENT_ERROR
        assert not diagnosis.no_write_tool
        assert not review_retry._is_retryable(diagnosis)

    def test_transient_crash_is_classified_apart_from_a_plain_one(self, tmp_path):
        log_path = _write_log(
            tmp_path,
            json.dumps({
                "type": "result", "subtype": "error", "is_error": True,
                "result": "API Error: Connection to the API was lost.",
            }),
        )
        diagnosis = review_agent.diagnose_missing_output(log_path)
        assert diagnosis.kind is DiagnosisKind.TRANSIENT
        assert review_retry._is_retryable(diagnosis)

    def test_clean_completion_without_a_write_is_labelled(self, tmp_path):
        log_path = _write_log(
            tmp_path,
            _tool_use("Read", file_path="/tmp/a"),
            json.dumps({"type": "result", "subtype": "success"}),
        )
        diagnosis = review_agent.diagnose_missing_output(log_path)
        assert diagnosis.kind is DiagnosisKind.COMPLETED
        assert diagnosis.no_write_tool
        assert review_retry._is_retryable(diagnosis)

    def test_refusal_without_any_tool_call_is_labelled(self, tmp_path):
        """A one-turn refusal calls no tool at all — the clearest no-write case.

        Regression: this used to fall through unlabelled, because an empty tool
        set was read as "cannot tell" rather than "called nothing", leaving the
        fix pass unable to retry an agent that simply declined the task.
        """
        log_path = _write_log(
            tmp_path,
            _text("I'm configured as a review-only assistant; I won't apply fixes."),
            json.dumps({"type": "result", "subtype": "success"}),
        )
        diagnosis = review_agent.diagnose_missing_output(log_path)
        assert diagnosis.no_write_tool
        assert review_retry._is_retryable(diagnosis)

    def test_missing_log_unchanged(self, tmp_path):
        diagnosis = review_agent.diagnose_missing_output(str(tmp_path / "nope.jsonl"))
        assert diagnosis == Diagnosis(DiagnosisKind.NO_SESSION_LOG)

    def test_no_result_record_unchanged(self, tmp_path):
        log_path = _write_log(tmp_path, _tool_use("Read", file_path="/tmp/a"))
        diagnosis = review_agent.diagnose_missing_output(log_path)
        assert diagnosis == Diagnosis(DiagnosisKind.NO_RESULT_RECORD)

    def test_quota_retry_without_a_result_is_quota_exhausted(self, tmp_path):
        log_path = _write_log(
            tmp_path,
            json.dumps({"type": "system", "subtype": "api_retry", "error_status": 429}),
        )
        diagnosis = review_agent.diagnose_missing_output(log_path)
        assert diagnosis == Diagnosis(DiagnosisKind.QUOTA_EXHAUSTED)


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


class TestSinglePassRead:
    def test_diagnosis_reads_the_log_once(self, tmp_path, monkeypatch):
        log_path = _write_log(
            tmp_path, _tool_use("Read", file_path="/tmp/a"), _result(),
        )
        reads = []
        real = review_agent._read_jsonl
        monkeypatch.setattr(
            review_agent, "_read_jsonl",
            lambda p: (reads.append(p), real(p))[1],
        )
        review_agent.diagnose_missing_output(log_path)
        assert reads == [log_path]


class TestWritableDirs:
    """The agent may write to its own artifact dir and the worktree — nothing else.

    Granting the shared reviews root is what let scratch files land beside other
    reviews instead of inside the run that made them.
    """

    def _add_dirs(self, monkeypatch, artifact_dir: str) -> list[str]:
        captured = {}
        monkeypatch.setattr(
            review_agent.ai_backend, "invoke_agent",
            lambda inv: captured.update(add_dirs=inv.add_dirs) or 0,
        )
        review_agent.invoke_agent(
            review_agent.AgentInvocation(
                prompt="prompt",
                session_log="/tmp/session.jsonl",
                add_dirs=review_agent.build_add_dirs("/tmp/wt", artifact_dir),
            ),
        )
        return captured["add_dirs"]

    def test_grants_the_artifact_dir_and_the_worktree(self, monkeypatch):
        assert self._add_dirs(monkeypatch, "/tmp/reviews/repo-1") == [
            "/tmp/reviews/repo-1", "/tmp/wt",
        ]

    def test_does_not_grant_the_reviews_root(self, monkeypatch):
        add_dirs = self._add_dirs(monkeypatch, "/tmp/reviews/repo-1")
        assert "/tmp/reviews" not in add_dirs
