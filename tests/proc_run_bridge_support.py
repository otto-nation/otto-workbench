"""Popen-shaped double that forwards to a test's ``subprocess.run`` stub.

Production ``core.proc.run`` always spawns through ``Popen``. Tests still
model the spawn as ``subprocess.run`` returning ``CompletedProcess``, so the
backend guard's ``guarded_popen`` bridges: when a test has replaced
``subprocess.run``, a Popen call hits that stub instead of a real process.
"""

from __future__ import annotations

import contextlib
import signal
import subprocess
import threading
from typing import Any

# Depth of the real stdlib ``run`` (the backend guard's inner call). A stub
# that still spawns goes through that path, which constructs Popen internally;
# that inner constructor must be real, not another double.
_in_real_run = threading.local()

# Depth of a stub invocation, for a stub that constructs Popen itself.
_in_stub = threading.local()


def _depth(holder: threading.local) -> int:
    return getattr(holder, "depth", 0)


def _push(holder: threading.local) -> None:
    holder.depth = _depth(holder) + 1


def _pop(holder: threading.local) -> None:
    holder.depth = _depth(holder) - 1


@contextlib.contextmanager
def real_run_section():
    """Mark the real ``subprocess.run`` body so its Popen is not bridged."""
    _push(_in_real_run)
    try:
        yield
    finally:
        _pop(_in_real_run)


# Never a live pid: production signals the non-group path via send_signal, not
# os.kill, so this value is never delivered to the kernel on that path.
_FAKE_PID = -1

# kwargs ``subprocess.run`` accepts that Popen may also have been given.
_RUN_KW = frozenset({
    "cwd", "env", "text", "errors", "encoding", "stdout", "stderr",
    "capture_output", "check", "shell", "universal_newlines",
})


class _ClosedPipe:
    """A pipe the double never opened: empty to read, discard on write."""

    def __iter__(self):
        return iter(())

    def close(self) -> None:
        return None

    def write(self, _data: str) -> int:
        return 0

    def flush(self) -> None:
        return None


class RunStubPopen:
    """A Popen stand-in whose ``communicate``/``wait`` call a ``run`` stub."""

    def __init__(self, stub, cmd, kwargs: dict[str, Any]) -> None:
        self._stub = stub
        self._cmd = cmd
        self._kwargs = kwargs
        self.args = cmd
        self.pid = _FAKE_PID
        self.returncode: int | None = None
        self.stdin = _ClosedPipe()
        self.stdout = _ClosedPipe()
        self.stderr = _ClosedPipe()
        self._done = False
        self._result: Any = None

    def communicate(self, input=None, timeout=None):
        result = self._invoke(input=input, timeout=timeout)
        return (getattr(result, "stdout", None), getattr(result, "stderr", None))

    def wait(self, timeout=None):
        if not self._done:
            self._invoke(timeout=timeout)
        return self.returncode

    def poll(self):
        return self.returncode

    def send_signal(self, sig) -> None:
        if self.returncode is None:
            self.returncode = -int(sig)

    def terminate(self) -> None:
        self.send_signal(signal.SIGTERM)

    def kill(self) -> None:
        self.send_signal(signal.SIGKILL)

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        if self.returncode is None:
            self.wait()
        return False

    def _invoke(self, input=None, timeout=None):
        if self._done:
            return self._result
        run_kwargs = {key: value for key, value in self._kwargs.items() if key in _RUN_KW}
        stdin = self._kwargs.get("stdin")
        if input is None and stdin is not None:
            run_kwargs["stdin"] = stdin
        run_kwargs["input"] = input
        run_kwargs["timeout"] = timeout
        # Set before the stub runs: a stub that raises leaves returncode None
        # and a later wait() returns None without re-invoking it.
        self._done = True
        _push(_in_stub)
        try:
            result = self._stub(self._cmd, **run_kwargs)
        finally:
            _pop(_in_stub)
        self._result = result
        self.returncode = getattr(result, "returncode", 0)
        return result


def maybe_bridge_popen(current_run, guarded_run, refuse, real_popen, cmd, args, kwargs):
    """Return a run-stub double when ``subprocess.run`` is no longer the guard.

    ``refuse`` and the real constructor run only when the test did not replace
    ``run``: a stub of ``run`` is how a test answers as the CLI would, and
    refusing the binary name would block that answer. A stub that still
    spawns re-enters here; the inner constructor is real.
    """
    if (current_run is not guarded_run
            and _depth(_in_real_run) == 0
            and _depth(_in_stub) == 0):
        return RunStubPopen(current_run, cmd, kwargs)
    refuse(cmd)
    return real_popen(cmd, *args, **kwargs)
