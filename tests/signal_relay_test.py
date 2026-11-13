"""Tests for the shared child-process signal relay."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import core.signal_relay
from signal_relay_support import (
    BLOCKING_CHILD,
    assert_pending_signal_reaches_child,
)

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")


def test_a_signal_racing_the_spawn_still_reaches_the_child(monkeypatch):
    """A signal landing before the child exists is held, not dropped.

    The ordering bug this pins: with the handlers installed *after* the spawn,
    a signal in that window is taken with default disposition, the wrapper dies
    without forwarding, and the child — alone in its own session — runs on.

    Driven deterministically: `Popen` is wrapped so the wrapper signals *itself*
    at the instant the window would be open. The child blocks until SIGTERM so
    it cannot become a macOS zombie before replay.
    """
    assert_pending_signal_reaches_child(
        monkeypatch,
        core.signal_relay.run_child,
        core.signal_relay.subprocess,
        BLOCKING_CHILD,
    )


def test_the_relay_is_uninstalled_once_the_child_is_gone():
    """A wrapper that ran a child must not leave its handlers behind.

    The relay is only correct while there is a child to forward to. Left
    installed, a later signal would be swallowed by a closure pointing at a
    process that has already exited, so the restore is part of the contract
    rather than tidiness.
    """
    before = {
        signum: signal.getsignal(signum)
        for signum in core.signal_relay.FORWARDED
    }
    core.signal_relay.run_child([sys.executable, "-c", "pass"])
    after = {signum: signal.getsignal(signum) for signum in before}

    assert after == before


def test_a_command_that_cannot_run_exits_127(capsys):
    """A missing child is a message and 127, not a traceback.

    Both lock wrappers share this path; 127 is the shell's convention for
    command-not-found, which is what a caller of a wrapper script expects.
    """
    code = core.signal_relay.run_child(
        ["no-such-command-xyz"], origin="job_slots_cli",
    )
    assert code == 127
    err = capsys.readouterr().err
    assert "job_slots_cli: cannot run no-such-command-xyz" in err
    assert "Traceback" not in err


def test_a_foreground_child_shares_the_wrappers_process_group(tmp_path):
    out = tmp_path / "pgid"
    code = core.signal_relay.run_child(
        [sys.executable, "-c", f"import os; open({str(out)!r}, 'w').write(str(os.getpgid(0)))"],
        foreground=True)
    assert code == 0
    assert int(out.read_text()) == os.getpgid(0)


def test_a_foreground_child_runs_in_the_given_directory(tmp_path):
    out = tmp_path / "cwd"
    code = core.signal_relay.run_child(
        [sys.executable, "-c", f"import os; open({str(out)!r}, 'w').write(os.getcwd())"],
        cwd=str(tmp_path), foreground=True)
    assert code == 0
    assert Path(out.read_text()).resolve() == tmp_path.resolve()


def test_a_foreground_wrapper_swallows_sigint_and_forwards_sigterm(tmp_path):
    """SIGINT at the wrapper must not abort it; SIGTERM still stops the child.

    Signalling this pytest process would abort the session at base and under
    revert, so the wrapper runs in a driver subprocess.
    """
    marker = tmp_path / "started"
    driver = "\n".join([
        "import sys",
        f"sys.path.insert(0, {LIB_DIR!r})",
        "import core.signal_relay",
        "raise SystemExit(core.signal_relay.run_child(",
        "    [sys.executable, '-c',",
        "     'import os,pathlib,sys,time;"
        " pathlib.Path(sys.argv[1]).write_text(str(os.getpid()));"
        " time.sleep(30)',",
        "     sys.argv[1]],",
        "    foreground=True))",
    ])
    proc = subprocess.Popen([sys.executable, "-c", driver, str(marker)], start_new_session=True)
    try:
        deadline = time.monotonic() + 10
        while not marker.exists() and proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert marker.exists(), f"driver exited {proc.poll()} before the child started"
        proc.send_signal(signal.SIGINT)
        time.sleep(0.3)
        assert proc.poll() is None, "the wrapper did not survive SIGINT"
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=30) == 128 + signal.SIGTERM
    finally:
        _reap_foreground_driver(proc)


def test_a_foreground_run_restores_every_handler_it_touched():
    watched = (*core.signal_relay.FORWARDED, *core.signal_relay.TERMINAL_SIGNALS)
    before = {signum: signal.getsignal(signum) for signum in watched}
    core.signal_relay.run_child([sys.executable, "-c", "pass"], foreground=True)
    assert {signum: signal.getsignal(signum) for signum in watched} == before


def test_output_takes_the_childs_stdout_and_stderr_and_stdin_is_empty(tmp_path):
    log = tmp_path / "log"
    script = ("import sys; print('out', flush=True); print('err', file=sys.stderr, flush=True); "
              "print(repr(sys.stdin.read()), flush=True)")
    with open(log, "wb") as out:
        code = core.signal_relay.run_child([sys.executable, "-c", script], output=out)
    assert code == 0
    assert log.read_text().split() == ["out", "err", "''"]


def test_output_also_gets_the_cannot_run_message(tmp_path, capsys):
    log = tmp_path / "log"
    with open(log, "wb") as out:
        code = core.signal_relay.run_child(["no-such-command-xyz"], origin="otto-queue", output=out)
    assert code == 127
    assert "otto-queue: cannot run no-such-command-xyz" in log.read_text()
    assert "otto-queue: cannot run no-such-command-xyz" in capsys.readouterr().err


def _reap_foreground_driver(proc: subprocess.Popen) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, signal.SIGKILL)
    proc.wait(timeout=5)
