"""Shared helpers for the signal-relay spawn-race tests.

Both lock wrappers used to copy this race, and both used a child that could
exit before replay — on macOS that is a zombie whose getpgid raises, which
the relay swallows. One helper, one child that cannot die early.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from collections.abc import Callable
from typing import Any

# Stays alive until SIGTERM. The trap is installed before sleep so a replay
# cannot land on default disposition during interpreter startup, and exit 0
# keeps the assertion on delivery rather than on 128+TERM.
BLOCKING_CHILD = ["sh", "-c", "trap 'exit 0' TERM; sleep 30"]


def assert_pending_signal_reaches_child(
    monkeypatch: Any,
    run_child: Callable[..., int],
    popen_module: Any,
    *run_args: Any,
    delay: float = 0.5,
    **run_kwargs: Any,
) -> None:
    """Drive a SIGTERM through *run_child* at the instant Popen returns.

    *delay* sits between the real spawn and returning the handle, which is the
    window where a ``python -c pass`` child becomes a zombie on macOS and the
    replay's getpgid raises. The blocking child cannot exit in that window;
    killpg is recorded *and* delivered so wait() returns.

    Cost: this patches ``os.killpg`` process-wide for the call and sleeps
    *delay* (0.5s by default) on every call, which is wall time added to the
    suite. It has one caller, so that is cheap; a second caller should pass a
    shorter *delay* or share the call.
    """
    real_popen = subprocess.Popen
    real_killpg = os.killpg
    delivered: list[int] = []

    def signalling_popen(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        os.kill(os.getpid(), signal.SIGTERM)
        proc = real_popen(*args, **kwargs)
        if delay:
            time.sleep(delay)
        return proc

    def recording_killpg(pgid: int, signum: int) -> None:
        delivered.append(signum)
        real_killpg(pgid, signum)

    monkeypatch.setattr(popen_module, "Popen", signalling_popen)
    monkeypatch.setattr(os, "killpg", recording_killpg)

    code = run_child(*run_args, **run_kwargs)

    assert delivered == [signal.SIGTERM], (
        "the signal taken before the child existed was dropped rather than "
        "forwarded once it did"
    )
    assert code == 0
