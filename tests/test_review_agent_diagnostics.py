"""Tests for review_agent failure diagnosis."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

from agent import invoke as agent_invoke
from agent import backend as ai_backend
from agent import session as review_agent
from review import retry as review_retry
from agent.diagnosis import Diagnosis, DiagnosisKind


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


class TestSinglePassRead:
    def test_diagnosis_reads_the_log_once(self, tmp_path, monkeypatch):
        log_path = _write_log(
            tmp_path, _tool_use("Read", file_path="/tmp/a"), _result(),
        )
        reads = []
        real = review_agent.read_jsonl
        monkeypatch.setattr(
            review_agent, "read_jsonl",
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
            agent_invoke.ai_backend, "invoke_agent",
            lambda inv: captured.update(add_dirs=inv.add_dirs) or 0,
        )
        agent_invoke.run_agent(
            ai_backend.AgentInvocation(
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


def _pi_tool(name: str, **args) -> str:
    return json.dumps({"type": "tool_execution_start", "toolName": name, "args": args})


def _pi_result(subtype: str = "error_max_turns", num_turns: int = _TURNS) -> str:
    """A Pi run's result record, alongside the RPC events that mark the shape."""
    return json.dumps({"type": "result", "subtype": subtype, "num_turns": num_turns})


def _pi_text(text: str, *, role: str = "assistant") -> str:
    """An assistant turn carrying text and no tool call — a narrated write."""
    return json.dumps({
        "type": "turn_end",
        "message": {"role": role, "content": [{"type": "text", "text": text}]},
    })


def _pi_message(stop_reason: str = "", error: str = "") -> dict:
    message = {"role": "assistant", "content": []}
    if stop_reason:
        message["stopReason"] = stop_reason
    if error:
        message["errorMessage"] = error
    return message


def _pi_agent_end(stop_reason: str = "", error: str = "") -> str:
    """Pi's end-of-run envelope, carrying the last turn's stop reason.

    The shape Pi actually writes: the run's outcome hangs off the last message
    of the envelope, not off the `result` record beside it.
    """
    return json.dumps({
        "type": "agent_end", "messages": [_pi_message(stop_reason, error)],
    })


def _pi_agent_end_messages(*messages: dict) -> str:
    """An envelope holding several turns, for reading which one is consulted."""
    return json.dumps({"type": "agent_end", "messages": list(messages)})


class TestPiLogsAreReadableForWrites:
    """A Pi run that wrote nothing used to be indistinguishable from one that worked.

    Pi emits RPC events rather than Claude's `assistant` records, so the
    no-write diagnosis never fired for it: the only thing that could trigger a
    retry was exhausting the turn cap, and a run that circled and gave up early
    was written off as a completed review with an empty file.
    """

    def test_pi_run_without_a_write_is_named_a_no_write_failure(self, tmp_path):
        log_path = _write_log(
            tmp_path,
            _pi_tool("read", path="/wt/a.py"),
            _pi_tool("read", path="/wt/a.py"),
            json.dumps({"type": "turn_end"}),
            _pi_result(),
        )
        diagnosis = review_agent.diagnose_missing_output(log_path)
        assert diagnosis.kind is DiagnosisKind.MAX_TURNS
        assert diagnosis.no_write_tool

    def test_pi_run_that_wrote_stays_plain(self, tmp_path):
        log_path = _write_log(
            tmp_path,
            _pi_tool("read", path="/wt/a.py"),
            _pi_tool("write", path="/out/review.md"),
            json.dumps({"type": "turn_end"}),
            _pi_result(),
        )
        diagnosis = review_agent.diagnose_missing_output(log_path)
        assert not diagnosis.no_write_tool

    def test_a_completed_pi_run_with_no_write_is_still_flagged(self, tmp_path):
        # The case the turn cap never catches: the agent stopped on its own.
        log_path = _write_log(
            tmp_path,
            _pi_tool("read", path="/wt/a.py"),
            json.dumps({"type": "agent_end"}),
            _pi_result(subtype="success"),
        )
        diagnosis = review_agent.diagnose_missing_output(log_path)
        assert diagnosis.kind is DiagnosisKind.COMPLETED
        assert diagnosis.no_write_tool

    def test_a_pi_crash_is_not_labelled_a_no_write_failure(self, tmp_path):
        log_path = _write_log(
            tmp_path,
            _pi_tool("read", path="/wt/a.py"),
            json.dumps({"type": "turn_end"}),
            json.dumps({
                "type": "result", "subtype": "error", "is_error": True,
                "result": "spawn ENOENT",
            }),
        )
        diagnosis = review_agent.diagnose_missing_output(log_path)
        assert diagnosis.kind is DiagnosisKind.AGENT_ERROR
        assert not diagnosis.no_write_tool

    def test_a_log_of_neither_shape_still_reports_cannot_tell(self, tmp_path):
        # Absence of evidence is not evidence of absence for a backend whose
        # logs this module cannot read.
        log_path = _write_log(tmp_path, _result())
        diagnosis = review_agent.diagnose_missing_output(log_path)
        assert not diagnosis.no_write_tool

    def test_a_scratch_write_is_not_the_deliverable(self, tmp_path):
        """A /tmp probe must not clear no_write_tool for the declared output.

        The live stream already asks pi_wrote_output(data, output_path); the
        post-run diagnosis still asked pi_write_tool_used (any write). A probe
        then diagnosed as bare COMPLETED and was not retried.
        """
        log_path = _write_log(
            tmp_path,
            _pi_tool("read", path="/wt/a.py"),
            _pi_tool("write", path="/tmp/probe.py"),
            json.dumps({"type": "turn_end"}),
            _pi_result(subtype="success"),
        )
        diagnosis = review_agent.diagnose_missing_output(
            log_path, output_path="/out/review.md",
        )
        assert diagnosis.kind is DiagnosisKind.COMPLETED
        assert diagnosis.no_write_tool

    def test_a_write_to_the_deliverable_still_clears_the_flag(self, tmp_path):
        """The counterpart: the declared file being written is not a thrash."""
        log_path = _write_log(
            tmp_path,
            _pi_tool("write", path="/out/review.md"),
            json.dumps({"type": "turn_end"}),
            _pi_result(subtype="success"),
        )
        diagnosis = review_agent.diagnose_missing_output(
            log_path, output_path="/out/review.md",
        )
        assert not diagnosis.no_write_tool


class TestAPiTransportFailureIsNotACompletedRun:
    """Pi records a failed API call on `agent_end`, not on `result`.

    The `result` record keeps saying `subtype=success, is_error=False`, so a
    reader of that alone calls a network fault a completed run: the message
    blames the agent for never writing, and the run is not retried, because
    COMPLETED and TRANSIENT differ on whether a second attempt is worth
    making. A self-review died this way three times before anyone read the log.
    """

    def test_a_transport_error_on_the_envelope_is_read_as_transient(self, tmp_path):
        log_path = _write_log(
            tmp_path,
            _pi_tool("read", path="/wt/a.py"),
            _pi_agent_end(
                "error",
                "request to https://oauth2.googleapis.com/token failed, "
                "reason: read ETIMEDOUT",
            ),
            _pi_result(subtype="success"),
        )
        diagnosis = review_agent.diagnose_missing_output(log_path)
        assert diagnosis.kind is DiagnosisKind.TRANSIENT
        assert "ETIMEDOUT" in diagnosis.message

    def test_such_a_run_is_retryable(self, tmp_path):
        """The half that matters: COMPLETED stops the run, TRANSIENT retries it."""
        from agent.retry import _RETRYABLE_KINDS

        log_path = _write_log(
            tmp_path,
            _pi_agent_end("error", "socket hang up"),
            _pi_result(subtype="success"),
        )
        diagnosis = review_agent.diagnose_missing_output(log_path)
        assert diagnosis.kind in _RETRYABLE_KINDS

    def test_the_agent_is_not_blamed_for_a_network_fault(self, tmp_path):
        """No `never called a file-writing tool` on a run that never got to."""
        log_path = _write_log(
            tmp_path,
            _pi_tool("read", path="/wt/a.py"),
            _pi_agent_end("error", "read ETIMEDOUT"),
            _pi_result(subtype="success"),
        )
        diagnosis = review_agent.diagnose_missing_output(log_path)
        assert not diagnosis.no_write_tool
        assert _NO_WRITE_SUFFIX not in diagnosis.message

    def test_an_unrecognised_error_is_still_an_error(self, tmp_path):
        """Not every envelope error is transient; it is still not a clean run."""
        log_path = _write_log(
            tmp_path,
            _pi_agent_end("error", "401 unauthorized"),
            _pi_result(subtype="success"),
        )
        diagnosis = review_agent.diagnose_missing_output(log_path)
        assert diagnosis.kind is DiagnosisKind.AGENT_ERROR

    # passes-at-base: the genuine no-write case, which this change preserves
    def test_a_clean_run_that_wrote_nothing_still_reads_as_completed(self, tmp_path):
        """The envelope check must not relabel the case it sits next to."""
        log_path = _write_log(
            tmp_path,
            _pi_tool("read", path="/wt/a.py"),
            _pi_agent_end(),
            _pi_result(subtype="success"),
        )
        diagnosis = review_agent.diagnose_missing_output(log_path)
        assert diagnosis.kind is DiagnosisKind.COMPLETED
        assert diagnosis.no_write_tool

    # passes-at-base: turn exhaustion is read off `result` before the envelope
    def test_turn_exhaustion_is_not_relabelled_a_crash(self, tmp_path):
        """An aborted last turn is how the turn cap looks from the envelope.

        Reading the envelope ahead of `result` would call every truncated run
        a crash and lose the turn count the retry budget is raised from.
        """
        log_path = _write_log(
            tmp_path,
            _pi_tool("read", path="/wt/a.py"),
            _pi_agent_end("aborted", "Request was aborted."),
            _pi_result(),
        )
        diagnosis = review_agent.diagnose_missing_output(log_path)
        assert diagnosis.kind is DiagnosisKind.MAX_TURNS
        assert diagnosis.num_turns == _TURNS

    # passes-at-base: a bound on the new check, which base does not make at all
    def test_an_earlier_turn_s_error_does_not_fail_a_run_that_finished(self, tmp_path):
        """Only the last turn ended the run; an earlier fault was recovered."""
        log_path = _write_log(
            tmp_path,
            _pi_agent_end("error", "read ETIMEDOUT"),
            _pi_tool("write", path="/out/review.md"),
            _pi_agent_end(),
            _pi_result(subtype="success"),
        )
        diagnosis = review_agent.diagnose_missing_output(
            log_path, output_path="/out/review.md",
        )
        assert diagnosis.kind is DiagnosisKind.COMPLETED

    # passes-at-base: a bound on the new check, which base does not make at all
    def test_a_mid_turn_error_the_run_continued_past_is_not_a_failure(self, tmp_path):
        """Only the envelope's last message says how the run ended.

        The deliberate trade in `pi_run_error`: recall for precision. A turn
        that hit a transient fault and carried on to finish is a run that
        worked, and reading any message would fail it on a fault it recovered
        from. Pinned because it is a design choice rather than an oversight,
        and the docstring saying so is not a test.
        """
        log_path = _write_log(
            tmp_path,
            _pi_tool("read", path="/wt/a.py"),
            _pi_agent_end_messages(
                _pi_message("error", "read ETIMEDOUT"),
                _pi_message(),
            ),
            _pi_result(subtype="success"),
        )
        diagnosis = review_agent.diagnose_missing_output(log_path)
        assert diagnosis.kind is DiagnosisKind.COMPLETED

    def test_the_last_message_of_the_last_envelope_is_the_one_read(self, tmp_path):
        """The counterpart: an error there did end the run, and is reported."""
        log_path = _write_log(
            tmp_path,
            _pi_tool("read", path="/wt/a.py"),
            _pi_agent_end_messages(
                _pi_message(),
                _pi_message("error", "read ETIMEDOUT"),
            ),
            _pi_result(subtype="success"),
        )
        diagnosis = review_agent.diagnose_missing_output(log_path)
        assert diagnosis.kind is DiagnosisKind.TRANSIENT

    # passes-at-base: a bound on the new check, which base does not make at all
    def test_a_result_that_reports_its_own_error_keeps_that_detail(self, tmp_path):
        """`result` is the better-attributed of the two when it has an error."""
        log_path = _write_log(
            tmp_path,
            _pi_agent_end("error", "read ETIMEDOUT"),
            json.dumps({
                "type": "result", "subtype": "error", "is_error": True,
                "result": "spawn ENOENT",
            }),
        )
        diagnosis = review_agent.diagnose_missing_output(log_path)
        assert diagnosis.detail == "spawn ENOENT"


class TestRecoveringAStrayWriteFromTheLog:
    """Findings an agent wrote somewhere other than the declared deliverable.

    Three runs wrote a complete review to a bare `review.md` in the worktree
    after a project-local guard refused the absolute path. The state-directory
    file stayed at zero bytes and the findings were discarded — but Pi records
    the whole document in the `tool_execution_start` that announced the write,
    so nothing had to be swept off disk to get them back.
    """

    def test_a_write_to_the_deliverable_path_is_recovered(self, tmp_path):
        output = tmp_path / "review.md"
        output.write_text("")
        log_path = _write_log(
            tmp_path,
            _pi_tool("write", path=str(output), content="## Must fix\n- [M1] x\n"),
            _pi_result(subtype="success"),
        )
        assert review_agent.try_recover_output(log_path, str(output)) is True
        assert "## Must fix" in output.read_text()

    def test_a_relative_write_of_the_same_name_is_recovered(self, tmp_path):
        """The defect itself: the stray file is `review.md`, not the full path."""
        output = tmp_path / "review.md"
        output.write_text("")
        log_path = _write_log(
            tmp_path,
            _pi_tool("write", path="review.md", content="## Must fix\n- [M1] x\n"),
            _pi_result(subtype="success"),
        )
        assert review_agent.try_recover_output(log_path, str(output)) is True
        assert "## Must fix" in output.read_text()

    def test_a_scratch_write_beside_it_is_not_the_deliverable(self, tmp_path):
        """Matched on the whole final component, so a neighbour cannot win."""
        output = tmp_path / "review.md"
        output.write_text("")
        log_path = _write_log(
            tmp_path,
            _pi_tool("write", path=str(tmp_path / "test123.txt"),
                     content="## Must fix\n- [M1] not the review\n"),
            _pi_result(subtype="success"),
        )
        assert review_agent.try_recover_output(log_path, str(output)) is False
        assert output.read_text() == ""

    def test_a_probe_without_headings_is_not_recovered(self, tmp_path):
        """The observed log's 4-byte "test" probe precedes the real document."""
        output = tmp_path / "review.md"
        output.write_text("")
        log_path = _write_log(
            tmp_path,
            _pi_tool("write", path="review.md", content="test"),
            _pi_result(subtype="success"),
        )
        assert review_agent.try_recover_output(log_path, str(output)) is False

    def test_the_last_qualifying_write_wins(self, tmp_path):
        """A refused write is retried, and the document grows across attempts."""
        output = tmp_path / "review.md"
        output.write_text("")
        log_path = _write_log(
            tmp_path,
            _pi_tool("write", path=str(output), content="## Must fix\n- draft\n"),
            _pi_tool("write", path="review.md", content="## Must fix\n- final\n"),
            _pi_result(subtype="success"),
        )
        assert review_agent.try_recover_output(log_path, str(output)) is True
        assert "final" in output.read_text()
        assert "draft" not in output.read_text()

    def test_a_narrated_write_is_recovered(self, tmp_path):
        """The shape neither other source sees: the document as assistant text.

        Three consecutive review runs ended this way against claude-sonnet-5
        on Vertex — the model spelled the `write` call out in prose and never
        called it, so the run reached agent_end with the findings present and
        no tool_execution_start to read them from.
        """
        output = tmp_path / "review.md"
        output.write_text("")
        doc = "# Self-Review: repo\n\n## Must fix\n- [ ] **[M1]** `f.sh:1` — x\n"
        log_path = _write_log(
            tmp_path,
            _pi_text('I already wrote the file.\n\nwrite review.md "' + doc + '"'),
            _pi_result(subtype="success"),
        )
        assert review_agent.try_recover_output(log_path, str(output)) is True
        body = output.read_text()
        assert "[M1]" in body
        # The narration is dropped and the document's own title survives: the
        # title is behind an opening quote, so a line-anchored search for a
        # heading skips it and takes the `## Must fix` below instead.
        assert body.startswith("# Self-Review: repo")
        assert "I already wrote" not in body

    def test_narration_structured_with_its_own_subheading_is_still_trimmed(self, tmp_path):
        """A model that headers its own commentary must not win the boundary.

        `_from_first_heading` used to match the first heading of any level,
        so a model narrating with a `## My plan` subheading ahead of the real
        `# Self-Review: ...` title had that plan section survive into the
        recovered document, verbatim, ahead of the document itself.
        """
        output = tmp_path / "review.md"
        output.write_text("")
        text = (
            "## My plan\nI will now write the review.\n\n"
            "# Self-Review: repo\n\n## Must fix\n- [M1] x\n"
        )
        log_path = _write_log(
            tmp_path,
            _pi_text(text),
            _pi_result(subtype="success"),
        )
        assert review_agent.try_recover_output(log_path, str(output)) is True
        body = output.read_text()
        assert body.startswith("# Self-Review: repo")
        assert "My plan" not in body

    def test_a_narrated_write_spelled_as_xml_is_recovered(self, tmp_path):
        """The other narration shape the module's docstring names but did not test.

        Some runs spell the tool call out as XML rather than prose, e.g.
        `<invoke name="write"><parameter name="content">...`. The document
        inside is recovered the same way as prose narration: by its own
        line-anchored heading, with the surrounding tags treated as preamble.
        """
        output = tmp_path / "review.md"
        output.write_text("")
        text = (
            '<invoke name="write">\n'
            '<parameter name="path">review.md</parameter>\n'
            '<parameter name="content">\n'
            "# Self-Review: repo\n\n## Must fix\n- [M1] x\n"
            "</parameter>\n</invoke>"
        )
        log_path = _write_log(
            tmp_path,
            _pi_text(text),
            _pi_result(subtype="success"),
        )
        assert review_agent.try_recover_output(log_path, str(output)) is True
        body = output.read_text()
        assert body.startswith("# Self-Review: repo")
        assert "[M1]" in body

    def test_a_fenced_document_is_unwrapped(self, tmp_path):
        output = tmp_path / "review.md"
        output.write_text("")
        log_path = _write_log(
            tmp_path,
            _pi_text("Here it is:\n\n```markdown\n## Must fix\n- [M1] x\n```\n"),
            _pi_result(subtype="success"),
        )
        assert review_agent.try_recover_output(log_path, str(output)) is True
        body = output.read_text()
        assert "[M1]" in body
        assert "```" not in body
        assert "Here it is" not in body

    def test_commentary_fenced_after_the_document_is_not_recovered(self, tmp_path):
        """A second fenced block after the review must not outrank it.

        The scan keeps the *last* qualifying candidate, so a reply that closes
        its review and then adds a ```bash``` aside recovered the aside. The
        heading test is applied per block for this reason, not only to the
        winner.
        """
        output = tmp_path / "review.md"
        output.write_text("")
        doc = "# Rev\n\n## Must fix\n- [M1] real\n"
        log_path = _write_log(
            tmp_path,
            _pi_text(
                # A ```markdown``` aside, not ```bash```: the caller's own
                # "## " gate already rejects an untitled block, so only an
                # aside that carries a heading of its own reaches the
                # comparison this test exists to pin.
                "Here:\n\n```markdown\n" + doc + "```\n\n"
                "For reference:\n\n```markdown\n## Notes\nrm -rf /\n```\n",
            ),
            _pi_result(subtype="success"),
        )
        assert review_agent.try_recover_output(log_path, str(output)) is True
        body = output.read_text()
        assert "[M1]" in body
        assert "rm -rf" not in body

    def test_the_last_of_two_documents_wins(self, tmp_path):
        """A redrafted document is the one to keep, as with a retried write."""
        output = tmp_path / "review.md"
        output.write_text("")
        log_path = _write_log(
            tmp_path,
            _pi_text(
                "Draft:\n\n```markdown\n# Rev\n\n## Must fix\n- draft\n```\n\n"
                "Final:\n\n```markdown\n# Rev\n\n## Must fix\n- final\n```\n",
            ),
            _pi_result(subtype="success"),
        )
        assert review_agent.try_recover_output(log_path, str(output)) is True
        body = output.read_text()
        assert "final" in body
        assert "draft" not in body

    def test_an_unclosed_fence_keeps_what_the_agent_produced(self, tmp_path):
        """A reply cut off mid-document is a partial review worth keeping."""
        output = tmp_path / "review.md"
        output.write_text("")
        log_path = _write_log(
            tmp_path,
            _pi_text("Here:\n\n```markdown\n# Rev\n\n## Must fix\n- [M1] x\n"),
            _pi_result(subtype="success"),
        )
        assert review_agent.try_recover_output(log_path, str(output)) is True
        assert "[M1]" in output.read_text()

    def test_a_deliverable_with_no_level_one_title_is_still_trimmed(self, tmp_path):
        """The scout artifact's format starts at `## Investigation Leads`.

        Anchoring the trim on `# ` alone stops trimming entirely for that
        deliverable, so the narration stays in front of the document. The
        anchor is the shallowest heading present, which is the document's
        own outline root whatever level it starts at.
        """
        output = tmp_path / "review.md"
        output.write_text("")
        log_path = _write_log(
            tmp_path,
            _pi_text(
                "I will write the file now.\n\n"
                "## Investigation Leads\n- **`a.py:1`** — x\n",
            ),
            _pi_result(subtype="success"),
        )
        assert review_agent.try_recover_output(log_path, str(output)) is True
        body = output.read_text()
        assert body.startswith("## Investigation Leads")
        assert "I will write" not in body

    def test_narration_with_its_own_subheading_is_trimmed_away(self, tmp_path):
        """A model that titles its own plan must not have the plan recovered."""
        output = tmp_path / "review.md"
        output.write_text("")
        log_path = _write_log(
            tmp_path,
            _pi_text(
                "## My plan\nI will now write it.\n\n"
                "# Self-Review: repo\n\n## Must fix\n- [M1] x\n",
            ),
            _pi_result(subtype="success"),
        )
        assert review_agent.try_recover_output(log_path, str(output)) is True
        body = output.read_text()
        assert body.startswith("# Self-Review: repo")
        assert "My plan" not in body

    def test_an_xml_shaped_call_loses_its_closing_tags(self, tmp_path):
        """The shape the docstring claimed but no test pinned.

        Observed in a real run: the model emitted `<write><path>...` and
        `<invoke name="bash">` as text. Recovered verbatim the document
        carries `</content></write>` on the end.
        """
        output = tmp_path / "review.md"
        output.write_text("")
        log_path = _write_log(
            tmp_path,
            _pi_text(
                "<write>\n<path>/abs/review.md</path>\n<content>\n"
                "# Rev\n\n## Must fix\n- [M1] x\n"
                "</content>\n</write>",
            ),
            _pi_result(subtype="success"),
        )
        assert review_agent.try_recover_output(log_path, str(output)) is True
        body = output.read_text()
        assert "[M1]" in body
        assert "</content>" not in body
        assert "</write>" not in body

    def test_a_json_shaped_call_is_decoded_not_trimmed(self, tmp_path):
        """A JSON narration carries the document as a string literal.

        Its newlines are a backslash and an `n`, so recovering the text
        verbatim yields one long line rather than a markdown document. No
        trim fixes that — the body has to be decoded.
        """
        output = tmp_path / "review.md"
        output.write_text("")
        log_path = _write_log(
            tmp_path,
            _pi_text(json.dumps({
                "command": "write",
                "path": "/abs/review.md",
                "content": "# Rev\n\n## Must fix\n- [M1] x\n",
            }, indent=2)),
            _pi_result(subtype="success"),
        )
        assert review_agent.try_recover_output(log_path, str(output)) is True
        body = output.read_text()
        assert "[M1]" in body
        assert "\\n" not in body
        assert body.startswith("# Rev\n")

    def test_a_document_ending_in_a_brace_keeps_it(self, tmp_path):
        """The trim is XML-tag only, so a code sample's last line survives.

        Stripping a trailing `}` or `"` as call syntax would eat the end of
        any document whose final line is a closing brace or a quote.
        """
        output = tmp_path / "review.md"
        output.write_text("")
        log_path = _write_log(
            tmp_path,
            _pi_text("# Rev\n\n## Must fix\n- [M1] see:\n\n    foo = {\n      bar: 1\n    }\n"),
            _pi_result(subtype="success"),
        )
        assert review_agent.try_recover_output(log_path, str(output)) is True
        assert output.read_text().rstrip().endswith("}")

    def test_a_document_quoting_json_is_not_decoded_as_a_call(self, tmp_path):
        """A review that mentions JSON is a document, not a narrated call."""
        output = tmp_path / "review.md"
        output.write_text("")
        log_path = _write_log(
            tmp_path,
            _pi_text('# Rev\n\n## Must fix\n- [M1] the config `{"a": 1}` is wrong\n'),
            _pi_result(subtype="success"),
        )
        assert review_agent.try_recover_output(log_path, str(output)) is True
        assert '{"a": 1}' in output.read_text()

    def test_a_fenced_document_with_a_nested_fence_is_not_truncated(self, tmp_path):
        """A Must-fix item's own evidence block nests a fence inside the outer one.

        A lazy `.*?` in `_FENCED_DOCUMENT` stops at the inner closing fence,
        dropping everything after it — exactly the shape the review format
        this pipeline generates asks agents to produce.
        """
        output = tmp_path / "review.md"
        output.write_text("")
        doc = (
            "## Must fix\n"
            "- [M1] evidence:\n\n"
            "```bash\necho hi\n```\n\n"
            "## Should fix\n"
            "- [S1] more\n"
        )
        log_path = _write_log(
            tmp_path,
            _pi_text("Here it is:\n\n```markdown\n" + doc + "```\n"),
            _pi_result(subtype="success"),
        )
        assert review_agent.try_recover_output(log_path, str(output)) is True
        body = output.read_text()
        assert "[M1]" in body
        assert "[S1]" in body

    def test_assistant_chatter_without_a_heading_is_not_recovered(self, tmp_path):
        """What keeps the text source from inventing a review out of a refusal."""
        output = tmp_path / "review.md"
        output.write_text("")
        log_path = _write_log(
            tmp_path,
            _pi_text("I could not complete the review. Please advise."),
            _pi_result(subtype="success"),
        )
        assert review_agent.try_recover_output(log_path, str(output)) is False
        assert output.read_text() == ""

    def test_the_prompt_is_not_mistaken_for_the_agents_output(self, tmp_path):
        """The user turn carries every heading the prompt does.

        An agent_end record holds the user message beside the assistant's, so
        a reader that took any text block would recover the prompt itself and
        report a review nobody wrote.
        """
        output = tmp_path / "review.md"
        output.write_text("")
        log_path = _write_log(
            tmp_path,
            _pi_text("## Review format\nWrite your findings here.", role="user"),
            _pi_result(subtype="success"),
        )
        assert review_agent.try_recover_output(log_path, str(output)) is False
        assert output.read_text() == ""

    def test_a_real_write_beats_narrated_text(self, tmp_path):
        """Narration is the weakest evidence, so it must not outrank a call."""
        output = tmp_path / "review.md"
        output.write_text("")
        log_path = _write_log(
            tmp_path,
            _pi_tool("write", path="review.md", content="## Must fix\n- from the tool\n"),
            _pi_text("## Must fix\n- from the narration\n"),
            _pi_result(subtype="success"),
        )
        assert review_agent.try_recover_output(log_path, str(output)) is True
        assert "from the tool" in output.read_text()
        assert "from the narration" not in output.read_text()

    def test_a_claude_denial_is_still_recovered(self, tmp_path):
        """The Claude source keeps working alongside the new Pi one."""
        output = tmp_path / "review.md"
        log_path = _write_log(
            tmp_path,
            json.dumps({
                "type": "result",
                "permission_denials": [
                    {"tool_input": {"content": "## Must fix\n- [M1] denied\n"}},
                ],
            }),
        )
        assert review_agent.try_recover_output(log_path, str(output)) is True
        assert "denied" in output.read_text()


class TestAMissingDeliverableIsNamedAsSuch:
    """A pre-created file that is gone is not the same as one left empty.

    `review.phases._touch` creates the deliverable before every phase, so an
    empty one is the ordinary shape of a run that wrote nothing and the
    existing message already says so. Absent means something removed it. The
    flag is reporting only — retryability does not read it.
    """

    def test_a_missing_deliverable_is_named(self, tmp_path):
        log_path = _write_log(
            tmp_path,
            _pi_tool("read", path="/wt/a.py"),
            _pi_result(subtype="success"),
        )
        diagnosis = review_agent.diagnose_missing_output(
            log_path, output_path=str(tmp_path / "absent.md"),
        )
        assert diagnosis.deliverable_gone
        assert "no longer there" in diagnosis.message

    def test_a_pre_created_empty_deliverable_is_not_named_gone(self, tmp_path):
        """The common case stays quiet, or every failure carries the suffix."""
        output = tmp_path / "review.md"
        output.write_text("")
        log_path = _write_log(
            tmp_path,
            _pi_tool("read", path="/wt/a.py"),
            _pi_result(subtype="success"),
        )
        diagnosis = review_agent.diagnose_missing_output(
            log_path, output_path=str(output),
        )
        assert not diagnosis.deliverable_gone
        assert "no longer there" not in diagnosis.message

    def test_a_crash_is_not_annotated_with_it(self, tmp_path):
        """The error already explains the missing output; restating it buries it."""
        log_path = _write_log(
            tmp_path,
            json.dumps({
                "type": "result", "subtype": "error", "is_error": True,
                "result": "spawn ENOENT",
            }),
        )
        diagnosis = review_agent.diagnose_missing_output(
            log_path, output_path=str(tmp_path / "absent.md"),
        )
        assert diagnosis.kind is DiagnosisKind.AGENT_ERROR
        assert not diagnosis.deliverable_gone

    def test_a_missing_deliverable_does_not_change_retryability(self, tmp_path):
        """Reporting honesty, not a behaviour change: both answers must match."""
        from agent import retry as agent_retry

        output = tmp_path / "review.md"
        lines = (_pi_tool("read", path="/wt/a.py"), _pi_result(subtype="success"))
        without_file = review_agent.diagnose_missing_output(
            _write_log(tmp_path, *lines), output_path=str(output),
        )
        output.write_text("")
        with_file = review_agent.diagnose_missing_output(
            _write_log(tmp_path, *lines), output_path=str(output),
        )
        assert without_file.deliverable_gone
        assert not with_file.deliverable_gone
        assert agent_retry.is_retryable(with_file) == agent_retry.is_retryable(
            without_file,
        )
