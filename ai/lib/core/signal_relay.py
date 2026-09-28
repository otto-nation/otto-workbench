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
"""

# doc-group: platform

from __future__ import annotations

import contextlib
import os
import signal
import subprocess

# What a wrapper forwards. SIGINT is the terminal Ctrl-C, SIGTERM the ordinary
# ask-to-stop, and SIGHUP the closed terminal — each of which would otherwise
# kill the wrapper and orphan the child.
FORWARDED = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


class SignalRelay:
    """Forwards signals to a child that may not exist yet.

    The two states are the whole of it. Before :meth:`forward_to`, a signal is
    recorded; after it, a signal is delivered to the child's process group. The
    recorded ones are replayed the moment the child is known, so a signal taken
    in between is deferred rather than dropped — it was sent to stop this work,
    and the work is about to start.
    """

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._pending: list[int] = []

    def handle(self, signum: int, _frame: object = None) -> None:
        """Deliver *signum* to the child, or hold it until there is one."""
        if self._proc is None:
            self._pending.append(signum)
            return
        try:
            os.killpg(os.getpgid(self._proc.pid), signum)
        except ProcessLookupError:
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
def forwarding_signals():
    """Relay :data:`FORWARDED` to the child for the duration of the block.

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
    relay = SignalRelay()
    previous = {signum: signal.signal(signum, relay.handle) for signum in FORWARDED}
    try:
        yield relay
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
