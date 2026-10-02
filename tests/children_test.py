"""Tests for stopping every child an agent run started.

The end-to-end cases are the subject: a real process shaped like a review run —
agents in sessions of their own, owned by thread-pool workers, more work queued
behind them — receives the stop a supervisor or a terminal sends, and nothing
it started may outlive it. That is the bug as it was found, and the shape a
unit test of `stop_all` alone cannot reach, because the defect was in which
thread an exception could arrive on.
"""

import contextlib
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest
from conftest import group_alive, group_gone_within

LIB_DIR = Path(__file__).resolve().parent.parent / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import core.children
import core.proc

# Two agents running and two queued behind them, as a review with more groups
# than workers has.
_WORKERS = 2
_QUEUED = 2

_RUN = textwrap.dedent("""\
    import sys
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path
    sys.path.insert(0, {lib!r})
    import core.children, core.proc

    out = Path({out!r})
    core.proc.install_stop_handler(lambda: None)

    def agent(n):
        marker = out / f"term-{{n}}"
        # Shaped like Pi: a group leader that cleans up on SIGTERM, with
        # processes of its own underneath it — one that dies on TERM, and one
        # forked while TERM was ignored, which inherits that and outlives the
        # leader. It reports ready itself, once the trap and both members
        # exist: a pid written by the parent after `spawn` returns comes
        # before any of them, and a stop landing in that window tests the
        # shell's startup rather than this module.
        script = (
            f'trap "" TERM; sleep 301 & '
            f'trap "echo term > {{marker}}; exit 0" TERM; sleep 300 & '
            f'echo $$ >> {{out / "pids"}}; wait'
        )
        with core.children.owned(["sh", "-c", script], start_new_session=True) as p:
            p.wait()

    with ThreadPoolExecutor(max_workers={workers}) as pool:
        for n in range({workers} + {queued}):
            pool.submit(agent, n)
""")


def _start_run(tmp_path):
    script = tmp_path / "run.py"
    script.write_text(_RUN.format(
        lib=str(LIB_DIR), out=str(tmp_path), workers=_WORKERS, queued=_QUEUED))
    return subprocess.Popen([sys.executable, str(script)])


def _wait_until(predicate, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def _pids(tmp_path):
    path = tmp_path / "pids"
    return [int(p) for p in path.read_text().split()] if path.exists() else []


def _clean_up(run, tmp_path):
    """Leave nothing behind when an assertion above failed partway."""
    if run.poll() is None:
        run.kill()
        run.wait()
    for pgid in filter(group_alive, _pids(tmp_path)):
        with contextlib.suppress(ProcessLookupError):
            os.killpg(pgid, signal.SIGKILL)


@pytest.mark.parametrize("stop, code", [
    (signal.SIGTERM, 128 + signal.SIGTERM),
    (signal.SIGINT, core.proc.INTERRUPT_RETURNCODE),
    (signal.SIGHUP, 128 + signal.SIGHUP),
])
def test_a_stopped_run_leaves_nothing_running(tmp_path, stop, code):
    run = _start_run(tmp_path)
    try:
        assert _wait_until(lambda: len(_pids(tmp_path)) == _WORKERS, 10), "agents never started"
        groups = _pids(tmp_path)
        run.send_signal(stop)
        # Inside the five seconds a supervisor allows before its own SIGKILL.
        assert run.wait(timeout=5) == code
        assert all(group_gone_within(g, 3) for g in groups), \
            "an agent's process group outlived the run"
        # Every agent was asked to stop rather than killed outright, so a
        # child with cleanup of its own got to run it.
        assert sorted(p.name for p in tmp_path.glob("term-*")) == ["term-0", "term-1"]
        # And nothing queued behind them started on the way out.
        assert len(_pids(tmp_path)) == _WORKERS
    finally:
        _clean_up(run, tmp_path)


def test_terminate_kills_a_child_that_ignores_sigterm():
    with core.children.owned(
        ["sh", "-c", 'trap "" TERM; sleep 300 & wait'], start_new_session=True,
    ) as proc:
        # Let the trap be installed before the TERM arrives.
        time.sleep(0.3)
        started = time.monotonic()
        assert core.children.terminate(proc, grace=0.5) is True
    assert proc.returncode == -signal.SIGKILL
    assert time.monotonic() - started < 0.5 + 5


def test_an_exception_in_the_owning_block_stops_the_child():
    with pytest.raises(KeyboardInterrupt):
        with core.children.owned(["sleep", "300"], start_new_session=True) as proc:
            raise KeyboardInterrupt
    assert proc.returncode is not None
    assert core.children._live == {}


def test_no_child_starts_once_a_stop_is_under_way():
    core.children.stop_all()
    with pytest.raises(core.children.StopRequested):
        core.children.spawn(["true"])


def test_a_child_already_reaped_is_never_signalled(monkeypatch):
    """Its pid may since belong to an unrelated process."""
    proc = core.children.spawn(["true"])
    proc.wait()
    sent = []
    monkeypatch.setattr(core.children.os, "killpg", lambda *a: sent.append(a))
    monkeypatch.setattr(core.children.os, "kill", lambda *a: sent.append(a))
    core.children.stop_all()
    assert sent == []
    core.children.forget(proc)
