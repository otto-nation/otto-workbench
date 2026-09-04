"""Tests for the subprocess result type and runner.

The properties here are what let a caller name the cause of a failure, which
is the whole point — a wrapper that drops stderr leaves every renderer with
nothing to print and every classifier reading the wrong stream.
"""

import os
import signal
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pytest

import proc
import timeouts
from proc import CmdResult


class TestCmdResult:
    def test_defaults_are_a_clean_success(self):
        r = CmdResult()
        assert r.ok
        assert r.stdout == ""
        assert r.stderr == ""

    def test_ok_is_false_for_any_non_zero_exit(self):
        assert not CmdResult(1).ok
        assert not CmdResult(-9).ok

    def test_detail_folds_stderr_onto_one_line(self):
        r = CmdResult(1, "", "gh: could not\n  reach github.com\n")
        assert r.detail == "gh: could not reach github.com"

    def test_detail_is_empty_when_the_command_said_nothing(self):
        assert CmdResult(1, "some output").detail == ""

    def test_combined_output_carries_both_streams(self):
        r = CmdResult(1, '{"message": "Not Found"}', "gh: Not Found (HTTP 404)")
        assert '{"message": "Not Found"}' in r.combined_output
        assert "gh: Not Found (HTTP 404)" in r.combined_output

    def test_combined_output_omits_an_empty_stream(self):
        assert CmdResult(1, "", "boom").combined_output == "boom"
        assert CmdResult(1, "boom").combined_output == "boom"

    def test_server_error_reads_a_status_line_on_stderr(self):
        # gh reports the 5xx on stderr and leaves stdout empty.
        assert CmdResult(1, "", "gh: Service unavailable (HTTP 503)").server_error

    def test_server_error_reads_a_status_line_on_stdout(self):
        assert CmdResult(1, "HTTP 502 Bad Gateway").server_error

    def test_a_client_error_is_not_a_server_error(self):
        assert not CmdResult(1, "", "gh: Not Found (HTTP 404)").server_error

    def test_a_bare_number_is_not_a_status_line(self):
        assert not CmdResult(1, "", "wrote 503 bytes").server_error

    def test_signalled_reads_the_negated_signal_number(self):
        assert CmdResult(-9).signalled
        assert CmdResult(-signal.SIGPIPE).signalled

    def test_an_ordinary_exit_is_not_signalled(self):
        assert not CmdResult(1).signalled
        assert not CmdResult(128).signalled
        assert not CmdResult().signalled


class TestFailureMessage:
    def test_the_exit_code_when_the_command_explained_nothing(self):
        """It is then the only evidence the failure left behind."""
        assert proc.failure_message("Failed to fetch the diff", CmdResult(1)) == (
            "Failed to fetch the diff (exit 1)"
        )

    def test_quotes_the_cause_the_command_gave(self):
        r = CmdResult(1, "", "fatal: not a git repository")
        msg = proc.failure_message("Failed to read the remote", r)
        assert msg == "Failed to read the remote: fatal: not a git repository"

    def test_a_server_error_says_to_wait(self):
        r = CmdResult(1, "", "gh: Service unavailable (HTTP 503)")
        msg = proc.failure_message("Failed to fetch the PR", r)
        assert "retry later" in msg
        assert "HTTP 503" in msg

    def test_a_server_error_on_stdout_is_annotated_too(self):
        # CmdResult.server_error reads both streams; the message must agree
        # with it or the retryable case renders as an ordinary failure.
        r = CmdResult(1, "HTTP 502 Bad Gateway")
        msg = proc.failure_message("Failed to fetch the PR", r)
        assert "retry later" in msg
        assert "HTTP 502 Bad Gateway" in msg

    def test_a_stdout_only_cause_is_capped(self):
        r = CmdResult(1, "HTTP 503 " + "x" * 500)
        assert len(proc.failure_message("Failed", r)) < 300

    def test_accepts_a_raw_completed_process(self):
        # Most of ai/ still calls subprocess.run directly; those call sites
        # report failures without converting first.
        r = subprocess.run(["sh", "-c", "echo nope >&2; exit 1"], capture_output=True, text=True)
        assert proc.failure_message("Failed", r) == "Failed: nope"


class TestFailureMessageForAKilledProcess:
    """A killed process says nothing, so the message is all the reader gets.

    This is the shape #970 surfaced: a whole-suite run on a loaded machine had
    `git commit --allow-empty -m initial failed` and nothing else to go on, and
    the first guess was a defect in git's arguments rather than the box being
    out of room.
    """

    def test_the_signal_is_named(self):
        msg = proc.failure_message("git commit failed", CmdResult(-signal.SIGKILL))
        assert msg == (
            "git commit failed — killed by SIGKILL (signal 9); "
            "the machine ended it, not the command — re-run rather than bisect"
        )

    def test_a_broken_pipe_is_named_too(self):
        msg = proc.failure_message("git fetch failed", CmdResult(-signal.SIGPIPE))
        assert "SIGPIPE (signal 13)" in msg
        assert "re-run rather than bisect" in msg

    def test_a_fault_signal_points_at_the_command(self):
        """SIGSEGV is the command's problem, and saying otherwise misdirects."""
        msg = proc.failure_message("the linter failed", CmdResult(-signal.SIGSEGV))
        assert "SIGSEGV (signal 11)" in msg
        assert "the machine ended it" not in msg

    def test_whatever_it_managed_to_say_is_still_quoted(self):
        r = CmdResult(-signal.SIGTERM, "", "warning: index is locked")
        msg = proc.failure_message("git add failed", r)
        assert msg.startswith("git add failed — killed by SIGTERM (signal 15)")
        assert msg.endswith(": warning: index is locked")

    def test_a_signal_this_platform_cannot_name_still_renders(self):
        assert "signal 77" in proc.failure_message("Failed", CmdResult(-77))

    def test_an_expired_bound_says_so_without_the_stderr(self):
        """A hand-built timeout result carries the code and nothing else."""
        msg = proc.failure_message("git fetch failed", CmdResult(proc.TIMEOUT_RETURNCODE))
        assert msg == "git fetch failed — the bound expired before the command answered"

    def test_a_real_timeout_keeps_the_bound_run_quoted(self):
        r = proc.run(["sleep", "5"], timeout=0.1)
        assert proc.failure_message("git fetch failed", r) == (
            "git fetch failed: timed out after 0.1s: sleep 5"
        )

    def test_a_real_signal_survives_the_round_trip(self):
        """Not a hand-built result — the negative code has to come from the OS."""
        r = proc.run(["sh", "-c", "kill -PIPE $$"], timeout=timeouts.QUICK)
        assert r.signalled
        assert "SIGPIPE (signal 13)" in proc.failure_message("git commit failed", r)


class TestRun:
    def test_captures_both_streams_and_the_exit_code(self):
        r = proc.run(["sh", "-c", "echo out; echo err >&2; exit 3"], timeout=timeouts.QUICK)
        assert r.returncode == 3
        assert r.stdout == "out\n"
        assert r.detail == "err"

    def test_does_not_raise_on_a_non_zero_exit(self):
        assert proc.run(["sh", "-c", "exit 1"], timeout=timeouts.QUICK).returncode == 1

    def test_runs_in_the_given_directory(self, tmp_path):
        r = proc.run(["pwd"], cwd=tmp_path, timeout=timeouts.QUICK)
        assert r.stdout.strip() == str(Path(tmp_path).resolve())

    def test_feeds_input_to_the_command(self):
        assert proc.run(["cat"], input_text="hello", timeout=timeouts.QUICK).stdout == "hello"

    def test_env_is_handed_to_the_command(self):
        r = proc.run(["sh", "-c", "echo $MARKER"], env={"MARKER": "set"},
                     timeout=timeouts.QUICK)
        assert r.stdout.strip() == "set"

    def test_env_replaces_rather_than_extends(self):
        """The point of passing one is being able to take a variable away."""
        os.environ["PROC_TEST_LEAK"] = "inherited"
        try:
            r = proc.run(["sh", "-c", "echo ${PROC_TEST_LEAK:-gone}"], env={},
                         timeout=timeouts.QUICK)
        finally:
            del os.environ["PROC_TEST_LEAK"]
        assert r.stdout.strip() == "gone"

    def test_the_parent_environment_is_inherited_by_default(self):
        os.environ["PROC_TEST_KEEP"] = "kept"
        try:
            r = proc.run(["sh", "-c", "echo ${PROC_TEST_KEEP:-gone}"], timeout=timeouts.QUICK)
        finally:
            del os.environ["PROC_TEST_KEEP"]
        assert r.stdout.strip() == "kept"

    def test_a_bound_is_required(self):
        """An omitted bound reads as nobody having thought about one."""
        with pytest.raises(TypeError):
            proc.run(["true"])

    def test_unbounded_is_spelled_out_and_runs(self):
        assert proc.run(["true"], timeout=timeouts.UNBOUNDED).ok


class TestRunTimeout:
    """An expired bound is an answer, not an exception.

    Every caller already handles a non-zero exit, and a timed-out command has
    produced no usable output — which is what a non-zero exit already means to
    all of them. Raising instead would need a handler at each of the call sites
    that has none.
    """

    def test_an_expired_bound_comes_back_as_a_result(self):
        r = proc.run(["sleep", "5"], timeout=0.1)
        assert r.returncode == proc.TIMEOUT_RETURNCODE
        assert not r.ok

    def test_the_bound_and_the_command_are_named(self):
        r = proc.run(["sleep", "5"], timeout=0.1)
        assert "timed out after 0.1s" in r.stderr
        assert "sleep 5" in r.stderr

    def test_what_the_command_managed_to_say_is_kept(self):
        """A command that times out mid-answer often explains itself first."""
        r = proc.run(["sh", "-c", "echo partial; echo why >&2; sleep 5"], timeout=0.5)
        assert r.returncode == proc.TIMEOUT_RETURNCODE
        assert r.stdout == "partial\n"
        assert "why" in r.stderr

    def test_a_timeout_is_distinguishable_from_an_ordinary_failure(self):
        """The eval scorers separate the two by this code."""
        assert proc.run(["false"], timeout=timeouts.QUICK).returncode != proc.TIMEOUT_RETURNCODE


class TestExternallyKilled:
    """One predicate for the machine-or-command split, asked from three places."""

    def test_a_signal_from_outside_is_the_machine(self):
        assert proc.externally_killed(-signal.SIGKILL)
        assert proc.externally_killed(-signal.SIGPIPE)

    def test_a_fault_signal_still_points_at_the_command(self):
        assert not proc.externally_killed(-signal.SIGSEGV)
        assert not proc.externally_killed(-signal.SIGABRT)

    def test_an_ordinary_exit_is_not_a_kill(self):
        assert not proc.externally_killed(0)
        assert not proc.externally_killed(1)
        assert not proc.externally_killed(proc.TIMEOUT_RETURNCODE)


class TestMachineKills:
    """What `run` records on its way past a command the machine ended.

    The result the caller gets is deliberately ordinary, which is what leaves a
    starved subprocess unattributable. The record is the only trace, so a
    reader — `tests/conftest.py` first — can say the machine was involved
    instead of inferring it from a downstream failure that looks real.
    """

    def test_an_expired_bound_is_recorded(self):
        proc.MACHINE_KILLS.clear()

        proc.run(["sleep", "5"], timeout=0.1)

        assert [str(kill) for kill in proc.MACHINE_KILLS] == [
            "sleep 5 — timed out after 0.1s"
        ]

    def test_an_external_signal_is_recorded(self):
        proc.MACHINE_KILLS.clear()

        proc.run(["bash", "-c", "kill -PIPE $$"], timeout=timeouts.QUICK)

        assert str(proc.MACHINE_KILLS[-1]).endswith("— killed by SIGPIPE (signal 13)")

    def test_a_fault_signal_is_not_recorded(self):
        """SIGABRT is the command's own doing, and naming the machine for it is
        the misdirection `externally_killed` exists to prevent."""
        proc.MACHINE_KILLS.clear()

        r = proc.run(["bash", "-c", "kill -ABRT $$"], timeout=timeouts.QUICK)

        assert r.signalled
        assert not proc.MACHINE_KILLS

    def test_an_ordinary_failure_is_not_recorded(self):
        proc.MACHINE_KILLS.clear()

        proc.run(["false"], timeout=timeouts.QUICK)

        assert not proc.MACHINE_KILLS

    def test_the_result_the_caller_gets_is_unchanged(self):
        """Nothing here is allowed to become a second way for `run` to answer."""
        proc.MACHINE_KILLS.clear()

        r = proc.run(["sleep", "5"], timeout=0.1)

        assert r.returncode == proc.TIMEOUT_RETURNCODE
        assert "timed out after 0.1s: sleep 5" in r.stderr

    def test_the_record_is_bounded(self):
        """`run` is called by long-lived things, and nothing here drains it."""
        proc.MACHINE_KILLS.clear()

        for _ in range(proc.MACHINE_KILL_LIMIT + 3):
            proc.MACHINE_KILLS.append(proc.MachineKill("git status", "timed out"))

        assert len(proc.MACHINE_KILLS) == proc.MACHINE_KILL_LIMIT
