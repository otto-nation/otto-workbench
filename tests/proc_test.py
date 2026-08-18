"""Tests for the subprocess result type and runner.

The properties here are what let a caller name the cause of a failure, which
is the whole of #740 — a wrapper that dropped stderr left every renderer with
nothing to print and every classifier reading the wrong stream.
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pytest

import proc
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
        # The #740 shape: gh reports the 5xx on stderr and leaves stdout empty.
        assert CmdResult(1, "", "gh: Service unavailable (HTTP 503)").server_error

    def test_server_error_reads_a_status_line_on_stdout(self):
        assert CmdResult(1, "HTTP 502 Bad Gateway").server_error

    def test_a_client_error_is_not_a_server_error(self):
        assert not CmdResult(1, "", "gh: Not Found (HTTP 404)").server_error

    def test_a_bare_number_is_not_a_status_line(self):
        assert not CmdResult(1, "", "wrote 503 bytes").server_error


class TestFailureMessage:
    def test_bare_action_when_the_command_explained_nothing(self):
        assert proc.failure_message("Failed to fetch the diff", CmdResult(1)) == (
            "Failed to fetch the diff"
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


class TestRun:
    def test_captures_both_streams_and_the_exit_code(self):
        r = proc.run(["sh", "-c", "echo out; echo err >&2; exit 3"])
        assert r.returncode == 3
        assert r.stdout == "out\n"
        assert r.detail == "err"

    def test_does_not_raise_on_a_non_zero_exit(self):
        assert proc.run(["sh", "-c", "exit 1"]).returncode == 1

    def test_runs_in_the_given_directory(self, tmp_path):
        assert proc.run(["pwd"], cwd=tmp_path).stdout.strip() == str(Path(tmp_path).resolve())

    def test_feeds_input_to_the_command(self):
        assert proc.run(["cat"], input_text="hello").stdout == "hello"

    def test_a_timeout_still_propagates(self):
        with pytest.raises(subprocess.TimeoutExpired):
            proc.run(["sleep", "5"], timeout=0.1)
