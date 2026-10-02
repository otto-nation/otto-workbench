"""Tests for agent.backend_pi RPC handling: refused commands, a dead Pi, the process group."""

import io
import json
import signal
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import agent.backend_pi
from ai_backend_test import _recording_popen
from ai_backend_pi_steer_test import TestCheckLimits as _CheckLimits
from ai_backend_pi_steer_test import TestConsumeStreamTracksWrites as _TracksWrites

from ai_backend_pi_support import _event, _stats_response


# The cases below reach the stream mocks as TestCheckLimits.MockStdin and
# TestConsumeStreamTracksWrites.MockProc. Imported under those names the two
# test classes would be collected here a second time, so each name is bound to
# an uncollected subclass carrying the same nested mocks.
class TestCheckLimits(_CheckLimits):
    __test__ = False


class TestConsumeStreamTracksWrites(_TracksWrites):
    __test__ = False


# The refusal from the incident this path exists for: an auth failure Pi
# reported in one line and the reader then waited out for 25 minutes.
_AUTH_ERROR = (
    "No API key found for amazon-bedrock.\n\n"
    "Use /login to log into a provider..."
)


def _response(command, success, error=None):
    body = {"type": "response", "command": command, "success": success}
    if error is not None:
        body["error"] = error
    return json.dumps(body) + "\n"


class TestFatalRpcResponse:
    """A response Pi refuses ends the run instead of being waited out.

    Pi answers `prompt` with success:false when it rejects it before
    acceptance, and then emits nothing: no agent_start, no turn_end, no
    agent_end. A reader that skips response events waits for turn_end from an
    agent that already gave up, which is a full harness timeout of silence.
    """

    def _stream(self, lines, log_file=None):
        proc = TestConsumeStreamTracksWrites.MockProc(lines)
        return agent.backend_pi._consume_stream(proc, log_file or io.StringIO(), "")

    def test_a_rejected_prompt_ends_the_stream(self):
        stream = self._stream([
            _response("prompt", False, _AUTH_ERROR),
            _event("turn_end"),
            _event("agent_end"),
        ])
        assert stream.stop_reason == "error"
        assert stream.error == _AUTH_ERROR
        # The turn_end behind the refusal must not have been counted: a run
        # that kept reading is the bug, and a bare stop_reason check cannot
        # tell a break apart from a flag set on the way past.
        assert stream.turn_count == 0

    def test_the_loop_does_not_read_past_the_refusal(self):
        def lines():
            yield _response("prompt", False, _AUTH_ERROR)
            raise AssertionError("read past the fatal response")

        assert self._stream(lines()).stop_reason == "error"

    def test_the_actionable_half_of_the_error_survives(self):
        # "/login to log into a provider" is the half that says what to do.
        stream = self._stream([_response("prompt", False, _AUTH_ERROR)])
        assert "/login" in stream.error

    # The raw line was already logged before the skip this replaces; the test
    # pins that the new check did not move above the write and cost the session
    # log the one record that explains the failure.
    # passes-at-base: asserts logging this change was careful not to move
    def test_the_refusal_is_still_written_to_the_session_log(self):
        log_file = io.StringIO()
        self._stream([_response("prompt", False, _AUTH_ERROR)], log_file)
        assert "No API key found" in log_file.getvalue()

    def test_the_refusal_is_announced_on_stderr(self, capsys):
        self._stream([_response("prompt", False, _AUTH_ERROR)])
        assert "No API key found" in capsys.readouterr().err

    def test_a_parse_failure_ends_the_run(self):
        # Pi could not read the command line at all, so the prompt never
        # arrived — the same silence one step earlier.
        stream = self._stream([
            _response("parse", False, "Failed to parse command: Unexpected token"),
            _event("turn_end"),
        ])
        assert stream.stop_reason == "error"
        assert "Unexpected token" in stream.error

    def test_a_refusal_with_no_error_text_still_ends_the_run(self):
        # Without a fallback the detail is "", which is falsy, and the run
        # hangs on exactly the shape that came with no message.
        stream = self._stream([
            _response("prompt", False),
            _event("turn_end"),
        ])
        assert stream.stop_reason == "error"
        assert "prompt" in stream.error


class TestNonFatalRpcResponse:
    """A command Pi declines mid-run is reported, not fatal.

    _check_limits sends steer, abort and follow_up while the agent is running,
    and Pi rejects one it no longer has anything to apply to. Ending the run
    there would discard a healthy agent's work over a message it did not need.
    """

    def _stream(self, lines):
        proc = TestConsumeStreamTracksWrites.MockProc(lines)
        return agent.backend_pi._consume_stream(proc, io.StringIO(), "")

    def test_a_failed_steer_does_not_end_the_run(self):
        stream = self._stream([
            _response("steer", False, "agent is not streaming"),
            _event("turn_end"),
            _event("turn_end"),
            _event("agent_end"),
        ])
        assert stream.stop_reason == "completed"
        assert stream.error is None
        assert stream.turn_count == 2

    def test_a_failed_abort_does_not_end_the_run(self):
        stream = self._stream([
            _response("abort", False, "nothing to abort"),
            _event("agent_end"),
        ])
        assert stream.stop_reason == "completed"
        assert stream.error is None

    def test_a_failed_steer_is_reported_rather_than_dropped(self, capsys):
        self._stream([
            _response("steer", False, "agent is not streaming"),
            _event("agent_end"),
        ])
        err = capsys.readouterr().err
        assert "steer" in err
        assert "agent is not streaming" in err

    def test_an_accepted_prompt_is_not_an_error(self):
        stream = self._stream([
            _response("prompt", True),
            _event("turn_end"),
            _event("agent_end"),
        ])
        assert stream.stop_reason == "completed"
        assert stream.error is None
        assert stream.turn_count == 1

    def test_a_response_carrying_no_success_field_is_not_an_error(self):
        # `is not False` rather than a truthiness test: an absent key is not a
        # refusal, and treating it as one would kill runs on any reply shape
        # Pi adds later.
        stream = self._stream([
            json.dumps({"type": "response", "command": "get_session_stats", "data": {}}) + "\n",
            _event("agent_end"),
        ])
        assert stream.stop_reason == "completed"
        assert stream.error is None


class _RefusingProc:
    """A Pi that refuses the prompt, then stays alive and silent.

    ``returncode`` is 0 because that is what a clean RPC shutdown reports, and
    reading only the status is how a refused run passed for a successful one.
    """

    class _Stdin(TestCheckLimits.MockStdin):
        def close(self):
            pass

    class _Stdout:
        """An iterable that closes, as a real Popen's stdout pipe is."""

        def __init__(self, lines):
            self._lines = iter(lines)
            self.closed = False

        def __iter__(self):
            return self._lines

        def __next__(self):
            return next(self._lines)

        def close(self):
            self.closed = True

    def __init__(self, lines, wait_hangs=False, returncode=0):
        self.stdout = self._Stdout(lines)
        self.stdin = self._Stdin()
        self.stderr = io.StringIO("")
        self.returncode = returncode
        self.wait_hangs = wait_hangs
        self.killed = False
        self.waits = []
        # A real Popen's pid; _kill_group signals this as a process group id
        # under start_new_session, which os.killpg is monkeypatched to record
        # rather than actually signal.
        self.pid = 424242

    def wait(self, timeout=None):
        self.waits.append(timeout)
        if self.wait_hangs and not self.killed:
            raise subprocess.TimeoutExpired("pi", timeout)
        return self.returncode

    def kill(self):
        self.killed = True

    # A real Popen is a context manager whose __exit__ closes the three pipes
    # and then reaps — with an *unbounded* wait for anything that is not a
    # KeyboardInterrupt. Mirrored rather than stubbed, so a path that enters
    # this object inherits the hang the real one would impose instead of
    # passing against a no-op.
    def __enter__(self):
        return self

    def __exit__(self, exc_type, *rest):
        self.stdout.close()
        if exc_type is KeyboardInterrupt:
            return False
        self.wait()
        return False


class TestRefusalReachesTheCaller:
    """invoke_agent turns a refusal into a failure a caller can see."""

    def _run(self, monkeypatch, tmp_path, proc, entry_point="invoke_agent"):
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: proc)
        return getattr(agent.backend_pi, entry_point)(agent.backend_pi.AgentInvocation(
            prompt="p", cwd=str(tmp_path), session_log=str(tmp_path / "s.jsonl"),
        ))

    def _refused(self):
        return _RefusingProc([_response("prompt", False, _AUTH_ERROR)])

    @pytest.mark.parametrize("entry_point", ["invoke_agent", "invoke_fix"])
    def test_a_refused_run_exits_non_zero(self, monkeypatch, tmp_path, entry_point):
        code = self._run(monkeypatch, tmp_path, self._refused(), entry_point)
        assert code != 0

    # passes-at-base: pins the status this change was careful not to clobber
    def test_pi_s_own_status_wins_when_it_reported_one(self, monkeypatch, tmp_path):
        proc = _RefusingProc([_response("prompt", False, _AUTH_ERROR)], returncode=3)
        assert self._run(monkeypatch, tmp_path, proc) == 3

    def test_the_error_path_does_not_ask_pi_for_session_stats(self, monkeypatch, tmp_path):
        # The query writes to a child that may be gone and then reads until a
        # reply that will never come — the hang, one line further down.
        proc = self._refused()
        self._run(monkeypatch, tmp_path, proc)
        assert not [c for c in proc.stdin.commands if c["type"] == "get_session_stats"]

    # passes-at-base: the happy path's stats query, which the error guard skips
    def test_a_healthy_run_still_asks_for_session_stats(self, monkeypatch, tmp_path):
        proc = _RefusingProc([
            _event("agent_end"),
            json.dumps(_stats_response()) + "\n",
        ])
        self._run(monkeypatch, tmp_path, proc)
        assert [c for c in proc.stdin.commands if c["type"] == "get_session_stats"]

    # passes-at-base: pins the unbounded wait this change scoped rather than took
    def test_a_completed_run_is_waited_for_however_long_it_takes(
        self, monkeypatch, tmp_path,
    ):
        # A bound on this path kills a healthy Pi that is merely slow to flush
        # under load, turning a run that already wrote its output into a
        # SIGKILL and a non-zero exit — worse than the wait it would shorten.
        proc = _RefusingProc([
            _event("agent_end"),
            json.dumps(_stats_response()) + "\n",
        ])
        self._run(monkeypatch, tmp_path, proc)
        assert proc.waits == [None], "the completed run's wait was bounded"

    def test_a_wedged_pi_is_killed_rather_than_waited_out(self, monkeypatch, tmp_path):
        proc = _RefusingProc(
            [_response("prompt", False, _AUTH_ERROR)], wait_hangs=True,
        )
        killpg_calls = []
        monkeypatch.setattr(
            "core.proc.os.killpg",
            lambda pid, sig: killpg_calls.append((pid, sig)),
        )
        self._run(monkeypatch, tmp_path, proc)
        assert killpg_calls == [(proc.pid, signal.SIGKILL)]
        assert proc.waits[0] is not None, "the wait was unbounded"

    def test_a_wedged_pi_whose_group_will_not_reap_does_not_crash_the_caller(
        self, monkeypatch, tmp_path,
    ):
        # SIGKILL almost always reaps promptly, but a process stuck in an
        # uninterruptible state can still fail to be reaped within QUICK. The
        # second wait's TimeoutExpired must not propagate — nothing further to
        # retry, and the caller has no handler for it.
        proc = _RefusingProc(
            [_response("prompt", False, _AUTH_ERROR)], wait_hangs=True,
        )
        monkeypatch.setattr("core.proc.os.killpg", lambda pid, sig: None)

        def _always_hangs(timeout=None):
            proc.waits.append(timeout)
            raise subprocess.TimeoutExpired("pi", timeout)

        proc.wait = _always_hangs
        code = self._run(monkeypatch, tmp_path, proc)
        assert code != 0

    def test_an_unreapable_group_does_not_block_on_its_stderr(
        self, monkeypatch, tmp_path,
    ):
        # Reading stderr blocks until the child closes it, and a group that
        # survived SIGKILL never does. Bounding the wait only moved the hang
        # two lines down unless the stderr read is skipped with it.
        reads = []

        class _BlockingStderr:
            def read(self):
                reads.append(True)
                raise AssertionError("read the stderr of a process still alive")

        proc = _RefusingProc(
            [_response("prompt", False, _AUTH_ERROR)], wait_hangs=True,
        )
        # Non-zero, so _log_stderr_on_failure would reach the read if called.
        proc.returncode = -9
        proc.stderr = _BlockingStderr()
        monkeypatch.setattr("core.proc.os.killpg", lambda pid, sig: None)

        def _always_hangs(timeout=None):
            proc.waits.append(timeout)
            raise subprocess.TimeoutExpired("pi", timeout)

        proc.wait = _always_hangs
        assert self._run(monkeypatch, tmp_path, proc) != 0
        assert reads == []

    def test_the_success_path_does_not_route_through_popen_exit(
        self, monkeypatch, tmp_path,
    ):
        # A real Popen.__exit__ calls self.wait() with no timeout on any exit
        # that isn't a KeyboardInterrupt. If the normal return path re-entered
        # that context manager, a group _wait_for_exit already gave up on
        # (reported and left after SIGKILL still would not reap) would hang
        # this call forever right after the warning — the same silent hang
        # this module exists to end, moved one level up. This double's
        # __exit__ mirrors that real behavior; the fix must never call it.
        class _ExitingProc(_RefusingProc):
            def __exit__(self, *exc_info):
                self.wait()
                return False

        proc = _ExitingProc(
            [_response("prompt", False, _AUTH_ERROR)], wait_hangs=True,
        )
        monkeypatch.setattr("core.proc.os.killpg", lambda pid, sig: None)

        def _always_hangs(timeout=None):
            proc.waits.append(timeout)
            raise subprocess.TimeoutExpired("pi", timeout)

        proc.wait = _always_hangs
        code = self._run(monkeypatch, tmp_path, proc)
        assert code != 0
        # Two bounded waits from _wait_for_exit (LOCAL, then QUICK after the
        # kill). A third entry means Popen.__exit__ ran its own self.wait().
        assert len(proc.waits) == 2, "the success path re-entered Popen as a context manager"


class TestPiRunsInItsOwnGroup:
    """Pi leads its own session, and nothing escapes when the caller is cut off.

    `_wait_for_exit` signals the whole group so the tools Pi spawned die with
    it. That flag also takes Pi out of the terminal's foreground group, so a
    Ctrl-C no longer reaches it: the interrupt lands on this process alone, and
    without a kill on the way out the agent keeps running against the account
    with nothing holding a handle to it.
    """

    def test_pi_is_started_as_a_group_leader(self, monkeypatch, tmp_path):
        seen = {}
        monkeypatch.setattr(subprocess, "Popen", _recording_popen(seen))
        agent.backend_pi.invoke_agent(agent.backend_pi.AgentInvocation(
            prompt="p", cwd=str(tmp_path), session_log=str(tmp_path / "s.jsonl"),
        ))
        assert seen["start_new_session"] is True

    @pytest.mark.parametrize("entry_point", ["invoke_agent", "invoke_fix"])
    def test_an_interrupt_kills_the_group_before_propagating(
        self, monkeypatch, tmp_path, entry_point,
    ):
        # Without the kill the interrupt unwinds this process and leaves Pi
        # detached and billing — the harm `start_new_session` newly makes
        # possible, since a Ctrl-C no longer reaches a child in its own group.
        proc = _RefusingProc([])
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: proc)
        killpg_calls = []
        monkeypatch.setattr(
            "core.proc.os.killpg",
            lambda pid, sig: killpg_calls.append((pid, sig)),
        )

        def _interrupted(*a, **kw):
            raise KeyboardInterrupt

        monkeypatch.setattr(agent.backend_pi, "_consume_stream", _interrupted)

        with pytest.raises(KeyboardInterrupt):
            getattr(agent.backend_pi, entry_point)(agent.backend_pi.AgentInvocation(
                prompt="p", cwd=str(tmp_path), session_log=str(tmp_path / "s.jsonl"),
            ))
        assert killpg_calls == [(proc.pid, signal.SIGKILL)]

    def _interrupted_run(self, monkeypatch, tmp_path, proc):
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: proc)
        monkeypatch.setattr("core.proc.os.killpg", lambda pid, sig: None)

        def _interrupted(*a, **kw):
            raise KeyboardInterrupt

        monkeypatch.setattr(agent.backend_pi, "_consume_stream", _interrupted)
        with pytest.raises(KeyboardInterrupt):
            agent.backend_pi.invoke_agent(agent.backend_pi.AgentInvocation(
                prompt="p", cwd=str(tmp_path), session_log=str(tmp_path / "s.jsonl"),
            ))

    def test_an_interrupt_closes_the_pipes_and_reaps_the_child(
        self, monkeypatch, tmp_path,
    ):
        # The kill alone collects neither: the descriptors stay open and the
        # child stays a zombie until GC. Asserted as the outcome rather than as
        # "Popen.__exit__ ran", because entering Popen is exactly what this
        # path must not do — its reap is unbounded.
        proc = _RefusingProc([])
        self._interrupted_run(monkeypatch, tmp_path, proc)
        assert proc.stdout.closed
        assert proc.waits, "the child was never reaped"

    # A KeyboardInterrupt takes Popen.__exit__'s own bounded arm, so the double
    # cannot show the hang here; the non-interrupt case this guards against is
    # the reachable one, confirmed separately against a real child.
    # passes-at-base: the interrupt arm was already bounded by CPython itself
    def test_an_interrupt_does_not_hang_on_a_group_that_survives_sigkill(
        self, monkeypatch, tmp_path,
    ):
        # Popen.__exit__ reaps with an unbounded wait() for anything that is
        # not a KeyboardInterrupt, so entering it here would hang the unwinding
        # of an ordinary exception on a child that would not die.
        proc = _RefusingProc([], wait_hangs=True)

        def _always_hangs(timeout=None):
            proc.waits.append(timeout)
            raise subprocess.TimeoutExpired("pi", timeout)

        proc.wait = _always_hangs
        self._interrupted_run(monkeypatch, tmp_path, proc)
        assert all(t is not None for t in proc.waits), "an unbounded wait ran"


class TestWritingToADeadPi:
    """Pi can exit before a command is written; that is not a traceback.

    `_send` writes to the child's stdin, which raises once the far end is gone.
    Every caller can reach a dead Pi: the first prompt when Pi rejected its own
    flags and exited, and the mid-run abort/steer that follow a turn_end Pi
    emitted on the way out.
    """

    class _DeadStdin:
        def write(self, data):
            raise BrokenPipeError(32, "Broken pipe")

        def flush(self):
            pass

        def close(self):
            pass

    def test_send_reports_a_broken_pipe_rather_than_raising(self):
        proc = _RefusingProc([])
        proc.stdin = self._DeadStdin()
        assert agent.backend_pi._send(proc, {"type": "abort"}) is False

    def test_send_reports_success_when_the_write_lands(self):
        proc = _RefusingProc([])
        assert agent.backend_pi._send(proc, {"type": "abort"}) is True

    def test_a_closed_stdin_is_not_a_traceback_either(self):
        # A file object closed under us raises ValueError, not BrokenPipeError.
        proc = _RefusingProc([])
        proc.stdin = io.StringIO()
        proc.stdin.close()
        assert agent.backend_pi._send(proc, {"type": "abort"}) is False

    def test_the_limit_abort_survives_a_pi_that_already_exited(self):
        # _check_limits fires after a turn_end Pi may have emitted on its way
        # out. An unguarded write here crashed the run at its turn ceiling.
        proc = _RefusingProc([])
        proc.stdin = self._DeadStdin()
        stop, _ = agent.backend_pi._check_limits(proc, 10, 2.0, 10, 5.0)
        assert stop == "max_turns"

    @pytest.mark.parametrize("entry_point", ["invoke_agent", "invoke_fix"])
    def test_an_undeliverable_prompt_is_reported_not_waited_on(
        self, monkeypatch, tmp_path, entry_point,
    ):
        # Pi died before the prompt could be written, so there is no stream
        # coming. Consuming one would wait out the whole timeout for events
        # that will never arrive — this issue's own failure, one step earlier.
        proc = _RefusingProc([])
        proc.stdin = self._DeadStdin()
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: proc)

        def _must_not_run(*a, **kw):
            raise AssertionError("consumed a stream from a pi that never got the prompt")

        monkeypatch.setattr(agent.backend_pi, "_consume_stream", _must_not_run)
        log = tmp_path / "s.jsonl"
        code = getattr(agent.backend_pi, entry_point)(agent.backend_pi.AgentInvocation(
            prompt="p", cwd=str(tmp_path), session_log=str(log),
        ))
        assert code != 0
        assert "exited before the prompt" in log.read_text()

    def test_the_undelivered_prompt_diagnoses_as_an_error(self, monkeypatch, tmp_path):
        import agent.session
        from agent.diagnosis import DiagnosisKind

        proc = _RefusingProc([])
        proc.stdin = self._DeadStdin()
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: proc)
        log = tmp_path / "s.jsonl"
        agent.backend_pi.invoke_agent(agent.backend_pi.AgentInvocation(
            prompt="p", cwd=str(tmp_path), session_log=str(log),
        ))
        diagnosis = agent.session.diagnose_missing_output(str(log))
        assert diagnosis.kind is DiagnosisKind.AGENT_ERROR

    def test_stats_are_not_asked_of_a_pi_that_cannot_be_written_to(self):
        # The query writes, then reads until a reply. A child that never got
        # the question will not answer it, and on a live-but-silent child that
        # read blocks with no bound — so the send failing must skip the read
        # rather than fall through to it.
        proc = _RefusingProc([])
        proc.stdin = self._DeadStdin()

        class _NeverAnswers:
            def __iter__(self):
                return self

            def __next__(self):
                raise AssertionError("read a pi that never received the query")

            def close(self):
                pass

        proc.stdout = _NeverAnswers()
        assert agent.backend_pi._get_stats_after_agent_end(proc) == {}


class TestPromptCarriesReadableDirs:
    """Pi has no --add-dir, so the directories reach it in the prompt or not."""

    def _sent_prompt(self, monkeypatch, tmp_path, entry_point, add_dirs):
        proc = _RefusingProc([_response("prompt", False, _AUTH_ERROR)])
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: proc)
        getattr(agent.backend_pi, entry_point)(agent.backend_pi.AgentInvocation(
            prompt="review this", cwd=str(tmp_path),
            session_log=str(tmp_path / "s.jsonl"), add_dirs=add_dirs,
        ))
        return proc.stdin.commands[0]["message"]

    @pytest.mark.parametrize("entry_point", ["invoke_agent", "invoke_fix"])
    # passes-at-base: pins prompt text the _prompt_with_dirs extraction preserves
    def test_the_dirs_and_the_prompt_both_reach_pi(
        self, monkeypatch, tmp_path, entry_point,
    ):
        message = self._sent_prompt(
            monkeypatch, tmp_path, entry_point, ["/tmp/artifacts"],
        )
        assert "/tmp/artifacts" in message
        assert message.endswith("review this")

    @pytest.mark.parametrize("entry_point", ["invoke_agent", "invoke_fix"])
    # passes-at-base: as above, for the no-directories case
    def test_no_dirs_sends_the_prompt_alone(
        self, monkeypatch, tmp_path, entry_point,
    ):
        assert self._sent_prompt(monkeypatch, tmp_path, entry_point, []) == "review this"


class TestRefusalDiagnosis:
    """The session log says a refused run crashed, and why."""

    def _diagnose(self, tmp_path, error):
        import agent.session

        log = tmp_path / "session.jsonl"
        agent.backend_pi._write_result_record(
            str(log), "error", 0, 0.0, 12, {}, None, error=error,
        )
        return agent.session.diagnose_missing_output(str(log))

    def test_a_refusal_diagnoses_as_an_agent_error(self, tmp_path):
        from agent.diagnosis import DiagnosisKind

        diagnosis = self._diagnose(tmp_path, _AUTH_ERROR)
        assert diagnosis.kind is DiagnosisKind.AGENT_ERROR
        assert "No API key found" in diagnosis.detail

    def test_a_refusal_is_not_retried(self, tmp_path):
        import agent.retry

        # A second attempt against a provider with no key fails identically,
        # and the whole point of the fix is not to spend a second timeout.
        assert agent.retry.is_retryable(self._diagnose(tmp_path, _AUTH_ERROR)) is False

    def test_a_transient_refusal_is_retried(self, tmp_path):
        import agent.retry
        from agent.diagnosis import DiagnosisKind

        diagnosis = self._diagnose(tmp_path, "ECONNREFUSED connecting to the API")
        assert diagnosis.kind is DiagnosisKind.TRANSIENT
        assert agent.retry.is_retryable(diagnosis) is True

    def test_the_record_carries_the_error_text(self, tmp_path):
        from agent.session import read_jsonl

        log = tmp_path / "session.jsonl"
        agent.backend_pi._write_result_record(
            str(log), "error", 0, 0.0, 12, {}, None, error=_AUTH_ERROR,
        )
        record = read_jsonl(str(log))[-1]
        assert record["is_error"] is True
        assert record["subtype"] == "error"
        assert record["error"] == _AUTH_ERROR

    # passes-at-base: guards is_error against reclassifying turn exhaustion
    def test_a_turn_exhausted_record_is_still_not_an_error(self, tmp_path):
        from agent.diagnosis import DiagnosisKind
        from agent.session import diagnose_missing_output, read_jsonl

        log = tmp_path / "session.jsonl"
        agent.backend_pi._write_result_record(str(log), "max_turns", 10, 3.5, 1, {})
        assert read_jsonl(str(log))[-1]["is_error"] is False
        assert diagnose_missing_output(str(log)).kind is DiagnosisKind.MAX_TURNS
