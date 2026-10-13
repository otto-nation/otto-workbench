"""Tests for `proc.run`'s relayed path: a live tee that still captures.

Split from `proc_test.py` along the path it covers. Every exit from the relayed
path is bounded and leaves nothing writing to the caller's terminal afterwards;
the tests here hold each of those exits — a grandchild holding a pipe, a child
that never reads its input, a thread that would not start — as well as the
ordinary capture.
"""

import subprocess
import sys
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pytest

import core.proc
import core.timeouts


class _ListDest:
    """A relay that keeps every write, for assertions after the command exits."""

    def __init__(self):
        self.chunks: list[str] = []

    def write(self, text):
        self.chunks.append(text)

    def flush(self):
        return None

    @property
    def text(self) -> str:
        return "".join(self.chunks)


class TestRunRelay:
    """Live tee: the caller sees output as it is produced, and still gets it back."""

    def test_output_reaches_the_relay_before_the_command_exits(self):
        marker = "RELAY_MARKER"
        seen_at: list[float] = []

        class Dest:
            def write(self, text):
                if marker in text:
                    seen_at.append(time.monotonic())

            def flush(self):
                return None

        pause = 0.6
        result = core.proc.run(
            ["sh", "-c", f"echo {marker}; sleep {pause}"],
            timeout=core.timeouts.QUICK,
            relay=Dest(),
        )
        done = time.monotonic()
        assert result.ok
        assert marker in result.stdout
        assert seen_at, "marker never reached the relay"
        assert done - seen_at[0] >= pause / 2

    def test_timeout_returns_though_a_grandchild_holds_the_pipe(self, monkeypatch):
        monkeypatch.setattr(core.timeouts, "QUICK", 0.5)
        start = time.monotonic()
        result = core.proc.run(
            ["sh", "-c", "sleep 5 & sleep 5"],
            timeout=0.3,
            relay=_ListDest(),
        )
        assert result.returncode == core.proc.TIMEOUT_RETURNCODE
        assert time.monotonic() - start < 4

    def test_early_exit_child_does_not_break_input(self):
        result = core.proc.run(
            ["true"], timeout=core.timeouts.QUICK, relay=_ListDest(),
            input_text="x" * 1_000_000,
        )
        assert result.ok

    def test_timeout_applies_when_child_never_reads_input(self):
        start = time.monotonic()
        result = core.proc.run(
            ["sleep", "30"], timeout=0.5, relay=_ListDest(),
            input_text="x" * 1_000_000,
        )
        assert result.returncode == core.proc.TIMEOUT_RETURNCODE
        assert time.monotonic() - start < 10

    def test_a_failure_is_still_captured(self):
        dest = _ListDest()
        result = core.proc.run(
            ["sh", "-c", "echo boom >&2; exit 7"],
            timeout=core.timeouts.QUICK,
            relay=dest,
        )
        assert result.returncode == 7
        assert "boom" in result.stderr
        assert "boom" in dest.text


class TestRelayTeardown:
    """What happens after the child is gone, while something it started is not."""

    def test_a_grandchild_holding_stdin_does_not_hang_teardown(self, monkeypatch):
        """The feeder owns stdin; closing it from the main thread waits on its write.

        The grandchild keeps the read end of stdin open after its parent is
        killed, so a write that filled the pipe never returns, and a close from
        teardown would block on that write's buffer lock until the grandchild
        exits — the whole sleep, rather than the join deadline.
        """
        monkeypatch.setattr(core.timeouts, "QUICK", 0.5)
        start = time.monotonic()
        result = core.proc.run(
            ["sh", "-c", "sleep 8 <&0 & sleep 8"],
            timeout=0.3, relay=_ListDest(), input_text="x" * 1_000_000,
        )
        assert result.returncode == core.proc.TIMEOUT_RETURNCODE
        assert time.monotonic() - start < 4

    def test_nothing_reaches_the_relay_after_run_returns(self, monkeypatch):
        """Late output from a held pipe is neither shown nor kept once `run` is done.

        Otherwise the grandchild's lines land in the middle of whatever the caller
        prints next — for `git push`, inside its own report of the push.
        """
        monkeypatch.setattr(core.timeouts, "QUICK", 0.5)
        dest = _ListDest()
        result = core.proc.run(
            ["sh", "-c",
             "(i=0; while [ $i -lt 8 ]; do echo late; i=$((i+1)); sleep 0.25; done) &"
             " echo done"],
            timeout=10, relay=dest,
        )
        written = len(dest.chunks)
        time.sleep(1.0)

        assert result.ok
        assert "done" in result.stdout
        assert len(dest.chunks) == written, dest.chunks[written:]

    def test_output_cut_off_at_the_deadline_says_so(self, monkeypatch):
        """A result short of what the command wrote must not read as complete."""
        monkeypatch.setattr(core.timeouts, "QUICK", 0.5)
        result = core.proc.run(
            ["sh", "-c", "sleep 3 & echo done"], timeout=10, relay=_ListDest(),
        )
        assert result.ok
        assert "still arriving" in result.stderr

    def test_a_complete_result_carries_no_late_note(self):
        result = core.proc.run(["echo", "done"], timeout=core.timeouts.QUICK,
                               relay=_ListDest())
        assert "still arriving" not in result.stderr

    def test_a_thread_that_will_not_start_kills_the_child(self, monkeypatch):
        """A failed `start` is an exception like any other: the child goes with it.

        Left outside the guarded block, the child is never killed and nobody
        reads its pipes — `sleep 30` here, a whole pre-push hook in production.
        """
        real_start = threading.Thread.start
        relay_threads: list[threading.Thread] = []

        def flaky_start(thread):
            if getattr(thread, "_target", None) is core.proc._pump:
                relay_threads.append(thread)
                if len(relay_threads) == 2:
                    raise RuntimeError("can't start new thread")
            real_start(thread)

        spawned: list[subprocess.Popen] = []
        popen = subprocess.Popen

        def recording_popen(*args, **kwargs):
            spawned.append(popen(*args, **kwargs))
            return spawned[-1]

        monkeypatch.setattr(threading.Thread, "start", flaky_start)
        monkeypatch.setattr(subprocess, "Popen", recording_popen)
        try:
            with pytest.raises(RuntimeError, match="can't start"):
                core.proc.run(["sleep", "30"], timeout=10, relay=_ListDest())
            assert spawned and spawned[0].returncode is not None
        finally:
            for child in spawned:
                child.kill()
                child.wait()
