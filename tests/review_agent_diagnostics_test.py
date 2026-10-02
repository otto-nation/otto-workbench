"""Tests for review_agent failure diagnosis."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import agent.invoke
import agent.backend
import agent.session
import review.retry
from agent.diagnosis import Diagnosis, DiagnosisKind

from review_agent_diagnostics_support import (
    _TURNS,
    _NO_WRITE_SUFFIX,
    _write_log,
    _pi_tool,
    _pi_result,
    _pi_text,
)


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


class TestDiagnoseMissingOutput:
    def test_max_turns_without_write_tool_names_the_thrash(self, tmp_path):
        log_path = _write_log(
            tmp_path,
            _tool_use("Read", file_path="/tmp/wt/a.py"),
            _tool_use("Bash", command="ls"),
            _result(),
        )
        diagnosis = agent.session.diagnose_missing_output(log_path)
        assert diagnosis.kind is DiagnosisKind.MAX_TURNS
        assert diagnosis.no_write_tool

    def test_max_turns_with_edit_call_stays_plain(self, tmp_path):
        log_path = _write_log(
            tmp_path,
            _tool_use("Read", file_path="/tmp/out.md"),
            _tool_use("Edit", file_path="/tmp/out.md", old_string=""),
            _result(),
        )
        diagnosis = agent.session.diagnose_missing_output(log_path)
        assert diagnosis == Diagnosis(DiagnosisKind.MAX_TURNS, num_turns=_TURNS)

    def test_no_assistant_records_stays_plain(self, tmp_path):
        """Non-Claude backends log no tool_use — absence is not evidence."""
        log_path = _write_log(tmp_path, _result())
        diagnosis = agent.session.diagnose_missing_output(log_path)
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
        diagnosis = agent.session.diagnose_missing_output(log_path)
        assert diagnosis.kind is DiagnosisKind.AGENT_ERROR
        assert not diagnosis.no_write_tool
        assert not review.retry._is_retryable(diagnosis)

    def test_transient_crash_is_classified_apart_from_a_plain_one(self, tmp_path):
        log_path = _write_log(
            tmp_path,
            json.dumps({
                "type": "result", "subtype": "error", "is_error": True,
                "result": "API Error: Connection to the API was lost.",
            }),
        )
        diagnosis = agent.session.diagnose_missing_output(log_path)
        assert diagnosis.kind is DiagnosisKind.TRANSIENT
        assert review.retry._is_retryable(diagnosis)

    def test_clean_completion_without_a_write_is_labelled(self, tmp_path):
        log_path = _write_log(
            tmp_path,
            _tool_use("Read", file_path="/tmp/a"),
            json.dumps({"type": "result", "subtype": "success"}),
        )
        diagnosis = agent.session.diagnose_missing_output(log_path)
        assert diagnosis.kind is DiagnosisKind.COMPLETED
        assert diagnosis.no_write_tool
        assert review.retry._is_retryable(diagnosis)

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
        diagnosis = agent.session.diagnose_missing_output(log_path)
        assert diagnosis.no_write_tool
        assert review.retry._is_retryable(diagnosis)

    def test_missing_log_unchanged(self, tmp_path):
        diagnosis = agent.session.diagnose_missing_output(str(tmp_path / "nope.jsonl"))
        assert diagnosis == Diagnosis(DiagnosisKind.NO_SESSION_LOG)

    def test_no_result_record_unchanged(self, tmp_path):
        log_path = _write_log(tmp_path, _tool_use("Read", file_path="/tmp/a"))
        diagnosis = agent.session.diagnose_missing_output(log_path)
        assert diagnosis == Diagnosis(DiagnosisKind.NO_RESULT_RECORD)

    def test_quota_retry_without_a_result_is_quota_exhausted(self, tmp_path):
        log_path = _write_log(
            tmp_path,
            json.dumps({"type": "system", "subtype": "api_retry", "error_status": 429}),
        )
        diagnosis = agent.session.diagnose_missing_output(log_path)
        assert diagnosis == Diagnosis(DiagnosisKind.QUOTA_EXHAUSTED)


class TestSinglePassRead:
    def test_diagnosis_reads_the_log_once(self, tmp_path, monkeypatch):
        log_path = _write_log(
            tmp_path, _tool_use("Read", file_path="/tmp/a"), _result(),
        )
        reads = []
        real = agent.session.read_jsonl
        monkeypatch.setattr(
            agent.session, "read_jsonl",
            lambda p: (reads.append(p), real(p))[1],
        )
        agent.session.diagnose_missing_output(log_path)
        assert reads == [log_path]


class TestWritableDirs:
    """The agent may write to its own artifact dir and the worktree — nothing else.

    Granting the shared reviews root is what let scratch files land beside other
    reviews instead of inside the run that made them.
    """

    def _add_dirs(self, monkeypatch, artifact_dir: str) -> list[str]:
        captured = {}
        monkeypatch.setattr(
            agent.backend, "invoke_agent",
            lambda inv: captured.update(add_dirs=inv.add_dirs) or 0,
        )
        agent.invoke.run_agent(
            agent.backend.AgentInvocation(
                prompt="prompt",
                session_log="/tmp/session.jsonl",
                add_dirs=agent.session.build_add_dirs("/tmp/wt", artifact_dir),
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
        diagnosis = agent.session.diagnose_missing_output(log_path)
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
        diagnosis = agent.session.diagnose_missing_output(log_path)
        assert not diagnosis.no_write_tool

    def test_a_completed_pi_run_with_no_write_is_still_flagged(self, tmp_path):
        # The case the turn cap never catches: the agent stopped on its own.
        log_path = _write_log(
            tmp_path,
            _pi_tool("read", path="/wt/a.py"),
            json.dumps({"type": "agent_end"}),
            _pi_result(subtype="success"),
        )
        diagnosis = agent.session.diagnose_missing_output(log_path)
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
        diagnosis = agent.session.diagnose_missing_output(log_path)
        assert diagnosis.kind is DiagnosisKind.AGENT_ERROR
        assert not diagnosis.no_write_tool

    def test_a_log_of_neither_shape_still_reports_cannot_tell(self, tmp_path):
        # Absence of evidence is not evidence of absence for a backend whose
        # logs this module cannot read.
        log_path = _write_log(tmp_path, _result())
        diagnosis = agent.session.diagnose_missing_output(log_path)
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
        diagnosis = agent.session.diagnose_missing_output(
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
        diagnosis = agent.session.diagnose_missing_output(
            log_path, output_path="/out/review.md",
        )
        assert not diagnosis.no_write_tool


class TestTheNarrationScanIsOrderedCheaplyFirst:
    """The expensive half runs only for a run that called nothing.

    `_narrated_write_contents` counts fence depth, parses JSON and matches a
    heading regex over every assistant text block. `_called_any_tool` is a
    scan for one record type. The `and` puts the cheap test first, so the
    expensive one is reached only in the rare case whose answer is used.

    A lazy wrapper around the pair buys nothing on top of this and was tried:
    its test passed against the eager form too, because the short circuit —
    not the wrapper — is what skips the work.
    """

    def test_a_run_that_called_tools_never_reaches_the_scan(self, tmp_path, monkeypatch):
        def _boom(records):
            raise AssertionError("narration scan ran for a run that used tools")

        monkeypatch.setattr(agent.session, "_narrated_write_contents", _boom)
        log_path = _write_log(
            tmp_path,
            _pi_tool("read", path="/wt/a.py"),
            _pi_result(subtype="error_max_turns"),
        )
        # Reached the no-write branch — so the scan was skipped by the `and`,
        # not by an early return above it.
        assert agent.session.diagnose_missing_output(log_path).no_write_tool is True

    def test_a_run_that_called_nothing_does_reach_the_scan(self, tmp_path):
        """The other half: the cheap test must not suppress a real answer."""
        log_path = _write_log(
            tmp_path,
            _pi_text('write review.md "# Rev\n\n## Must fix\n- [M1] x\n"'),
            _pi_result(subtype="success"),
        )
        assert agent.session.diagnose_missing_output(log_path).narrated_call is True


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
        diagnosis = agent.session.diagnose_missing_output(log_path)
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
        diagnosis = agent.session.diagnose_missing_output(log_path)
        assert diagnosis.kind in _RETRYABLE_KINDS

    def test_the_agent_is_not_blamed_for_a_network_fault(self, tmp_path):
        """No `never called a file-writing tool` on a run that never got to."""
        log_path = _write_log(
            tmp_path,
            _pi_tool("read", path="/wt/a.py"),
            _pi_agent_end("error", "read ETIMEDOUT"),
            _pi_result(subtype="success"),
        )
        diagnosis = agent.session.diagnose_missing_output(log_path)
        assert not diagnosis.no_write_tool
        assert _NO_WRITE_SUFFIX not in diagnosis.message

    def test_an_unrecognised_error_is_still_an_error(self, tmp_path):
        """Not every envelope error is transient; it is still not a clean run."""
        log_path = _write_log(
            tmp_path,
            _pi_agent_end("error", "401 unauthorized"),
            _pi_result(subtype="success"),
        )
        diagnosis = agent.session.diagnose_missing_output(log_path)
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
        diagnosis = agent.session.diagnose_missing_output(log_path)
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
        diagnosis = agent.session.diagnose_missing_output(log_path)
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
        diagnosis = agent.session.diagnose_missing_output(
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
        diagnosis = agent.session.diagnose_missing_output(log_path)
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
        diagnosis = agent.session.diagnose_missing_output(log_path)
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
        diagnosis = agent.session.diagnose_missing_output(log_path)
        assert diagnosis.detail == "spawn ENOENT"


class TestDiagnosingANarratedCall:
    """A run that wrote its tool call out as text instead of calling it.

    Distinct from a plain no-write run: this agent believes it already wrote
    the file, so the hint naming the write mechanism tells it to do what it
    thinks it just did — which is how two consecutive attempts failed the
    same way.
    """

    def test_a_tool_less_run_that_narrated_is_flagged(self, tmp_path):
        log_path = _write_log(
            tmp_path,
            _pi_text('write review.md "# Rev\n\n## Must fix\n- [M1] x\n"'),
            _pi_result(subtype="success"),
        )
        diagnosis = agent.session.diagnose_missing_output(log_path)
        assert diagnosis.narrated_call is True
        assert diagnosis.no_write_tool is True
        assert "wrote its tool call as text" in diagnosis.message

    def test_a_run_that_called_tools_is_not_flagged(self, tmp_path):
        """Commentary containing a heading is not narration.

        Measured over the session logs on this machine, 3 of 75 runs that
        made real tool calls carry heading-bearing prose — one of them
        alongside 58 calls. Without the called-nothing half of the test they
        are told they narrated, about a mistake they did not make.
        """
        log_path = _write_log(
            tmp_path,
            _pi_tool("read", path="/wt/a.py"),
            _pi_text("Good, that checks out.\n\n## Next\nNow the caller.\n"),
            _pi_result(subtype="error_max_turns"),
        )
        diagnosis = agent.session.diagnose_missing_output(log_path)
        assert diagnosis.narrated_call is False

    def test_the_hint_names_the_mistake_not_the_mechanism(self, tmp_path):
        import agent.retry

        log_path = _write_log(
            tmp_path,
            _pi_text('write review.md "# Rev\n\n## Must fix\n- [M1] x\n"'),
            _pi_result(subtype="success"),
        )
        hint = agent.retry.hint_for(agent.session.diagnose_missing_output(log_path))
        assert "as text" in hint
        # Not the no-write hint, which would tell an agent that believes it
        # wrote the file to write the file.
        assert "A previous attempt finished without" not in hint

    def test_a_plain_no_write_run_still_gets_the_mechanism_hint(self, tmp_path):
        """The narrower hint must not swallow the case it sits in front of."""
        import agent.retry

        log_path = _write_log(
            tmp_path,
            _pi_tool("read", path="/wt/a.py"),
            _pi_result(subtype="success"),
        )
        diagnosis = agent.session.diagnose_missing_output(log_path)
        assert diagnosis.no_write_tool is True
        assert diagnosis.narrated_call is False
        assert "A previous attempt finished without" in agent.retry.hint_for(diagnosis)


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
        diagnosis = agent.session.diagnose_missing_output(
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
        diagnosis = agent.session.diagnose_missing_output(
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
        diagnosis = agent.session.diagnose_missing_output(
            log_path, output_path=str(tmp_path / "absent.md"),
        )
        assert diagnosis.kind is DiagnosisKind.AGENT_ERROR
        assert not diagnosis.deliverable_gone

    def test_a_missing_deliverable_does_not_change_retryability(self, tmp_path):
        """Reporting honesty, not a behaviour change: both answers must match."""
        import agent.retry

        output = tmp_path / "review.md"
        lines = (_pi_tool("read", path="/wt/a.py"), _pi_result(subtype="success"))
        without_file = agent.session.diagnose_missing_output(
            _write_log(tmp_path, *lines), output_path=str(output),
        )
        output.write_text("")
        with_file = agent.session.diagnose_missing_output(
            _write_log(tmp_path, *lines), output_path=str(output),
        )
        assert without_file.deliverable_gone
        assert not with_file.deliverable_gone
        assert agent.retry.is_retryable(with_file) == agent.retry.is_retryable(
            without_file,
        )
