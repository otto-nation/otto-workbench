"""Forwarding signals to a child that may not exist yet.

Two wrappers in this package hold a lock for the duration of a command —
`job_slots_cli` holds a share of the test-parallelism pool, `tree_lock_cli`
holds the validation lock on a tree — and both run that command in its own
session so a terminal Ctrl-C does not reach it by process-group membership.
That makes the signal handler the only thing that can deliver a stop, and it
makes the *ordering* of the handler against the spawn load-bearing.

Both had the same bug: handlers installed after `Popen`, leaving a window in
which the child exists and the handler does not. A signal landing there is
taken with default disposition, so the wrapper dies without forwarding and the
child — alone in its new session — is never told to stop, while the lock the
wrapper was holding is released and the work runs on untracked.

One owner rather than a copy per wrapper. A second implementation of a
primitive this subtle is how the two come to disagree, and the bug above was
already present in both files in the same shape.

A third caller, `otto_queue.ticket`, runs its command in the *foreground*
instead: same process group as the terminal, Ctrl-C and hangup left to the
terminal, SIGTERM forwarded to the child's pid.
"""

# doc-group: platform

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys

import core.timeouts

# What a wrapper forwards. SIGINT is the terminal Ctrl-C, SIGTERM the ordinary
# ask-to-stop, and SIGHUP the closed terminal — each of which would otherwise
# kill the wrapper and orphan the child.
FORWARDED = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)

# What a terminal delivers to its whole foreground process group: Ctrl-C and a
# closed terminal. A foreground child already receives them, so a wrapper
# sharing its group swallows them rather than delivering each a second time.
TERMINAL_SIGNALS = (signal.SIGINT, signal.SIGHUP)


class SignalRelay:
    """Forwards signals to a child that may not exist yet.

    The two states are the whole of it. Before :meth:`forward_to`, a signal is
    recorded; after it, a signal is delivered to the child's process group, or
    its pid with `group=False`. The recorded ones are replayed the moment the
    child is known, so a signal taken in between is deferred rather than
    dropped — it was sent to stop this work, and the work is about to start.
    """

    def __init__(self, *, group: bool = True) -> None:
        self._proc: subprocess.Popen | None = None
        self._pending: list[int] = []
        self._group = group

    def handle(self, signum: int, _frame: object = None) -> None:
        """Deliver *signum* to the child (its group, unless built with ``group=False``), or hold it."""
        if self._proc is None:
            self._pending.append(signum)
            return
        try:
            if self._group:
                os.killpg(os.getpgid(self._proc.pid), signum)
            else:
                os.kill(self._proc.pid, signum)
        except ProcessLookupError:
            # The child is already gone. On macOS this also covers a zombie
            # (exited but not yet wait()ed): getpgid raises ESRCH there, and a
            # signal to a dead child is moot either way.
            pass

    def forward_to(self, proc: subprocess.Popen) -> None:
        """Name the child, and replay anything that arrived before it existed.

        The child is named first, so a signal landing during the replay below
        is delivered rather than recorded and the queue cannot grow while it is
        being drained.
        """
        self._proc = proc
        # Detached before draining, which the line above already makes
        # unnecessary: nothing can append once `_proc` is set. It is kept so
        # the drain stays correct if these two lines are ever reordered — that
        # order is load-bearing and nothing else says so.
        pending, self._pending = self._pending, []
        for signum in pending:
            self.handle(signum)


@contextlib.contextmanager
def forwarding_signals(signals: tuple[int, ...] = FORWARDED, *, group: bool = True):
    """Relay *signals* to the child for the duration of the block.

    Delivery is to the child's process group, or its pid with ``group=False``.

    Enter this *before* spawning, which is the whole point of the ordering:
    installing the handlers afterwards leaves a window in which the child
    exists and the handler does not, and a signal landing there kills the
    wrapper without ever reaching the child.

    A signal landing before the block is entered still kills the wrapper, and
    that is left alone: no child exists yet to orphan, and the caller's own
    lock release runs — as does the kernel dropping its flocks with the
    process. The window that had to be closed is the one where a child already
    exists.

    The previous handlers are restored on the way out, so a caller that runs
    several children in one process is not left with a relay pointing at a
    process that has already exited.
    """
    relay = SignalRelay(group=group)
    previous = {signum: signal.signal(signum, relay.handle) for signum in signals}
    try:
        yield relay
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def _ignore(_signum: int, _frame: object = None) -> None:
    return None


@contextlib.contextmanager
def _swallowing(signals: tuple[int, ...]):
    """Ignore *signals* in this process for the block, without passing the ignore to a child.

    A Python handler rather than `SIG_IGN`: an ignored disposition survives
    `exec`, so the child would never see a Ctrl-C, while a handler is reset to
    the default there.
    """
    previous = {signum: signal.signal(signum, _ignore) for signum in signals}
    try:
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def run_child(
    argv: list[str],
    *,
    env: dict[str, str] | None = None,
    origin: str = "run_child",
    cwd: str | None = None,
    foreground: bool = False,
) -> int:
    """Run *argv* and wait for it; 127 with a one-line message when it cannot start.

    By default the child runs in its own session, and SIGINT, SIGTERM and SIGHUP
    are forwarded to its group: the lock wrappers. With *foreground*, the child
    stays in this process's group with the terminal (stdio inherited, `/dev/tty`
    still its own), so it stays interactive. The terminal's own Ctrl-C and
    hangup reach it directly and are swallowed here, and SIGTERM is forwarded to
    its pid. `otto_queue.ticket` runs commands this way. In both modes
    :func:`forwarding_signals` wraps the spawn, so no window exists in which a
    child runs and no handler does. *origin* prefixes the error message.
    """
    forwarded = (signal.SIGTERM,) if foreground else FORWARDED
    swallowed = TERMINAL_SIGNALS if foreground else ()
    with forwarding_signals(forwarded, group=not foreground) as relay, _swallowing(swallowed):
        try:
            proc = subprocess.Popen(argv, start_new_session=not foreground, env=env, cwd=cwd)
        except OSError as exc:
            print(f"{origin}: cannot run {argv[0]}: {exc}", file=sys.stderr)
            return 127
        relay.forward_to(proc)
        code = proc.wait(timeout=core.timeouts.UNBOUNDED)
    # A negative returncode is -signal. sys.exit(-N) becomes 256-N;
    # callers expect the shell convention 128+N (SIGTERM -> 143).
    return 128 + (-code) if code < 0 else code
