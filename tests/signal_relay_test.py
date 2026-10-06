"""Tests for the shared child-process signal relay."""

from __future__ import annotations

import signal
import sys

import core.signal_relay
from signal_relay_support import (
    BLOCKING_CHILD,
    assert_pending_signal_reaches_child,
)


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
