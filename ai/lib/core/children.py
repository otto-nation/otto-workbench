"""The long-lived children this process started, and stopping all of them.

An agent run is a tree: this process, the agents it starts on worker threads,
and the tools each agent starts. Pi's agents lead sessions of their own, so a
supervisor's kill aimed at this process's group never reaches them — and the
cleanup each spawn site had was an `except BaseException` that only an
exception *on that worker thread* could trigger. Neither stop this process
receives arrives that way. SIGTERM's default disposition ended the process
with no Python code run at all; SIGINT raised in the main thread only, which
then waited in the pool's shutdown for agents nobody had told to stop. Either
way the agents ran on against the account with nothing holding a handle to
them.

So the stop is the owner's to deliver, and the owner needs to know what it
owns. `spawn` starts a child and records it; `forget` drops it once its owner
has reaped it. `stop_all` is what the entry point's signal handler calls: it
refuses every spawn from then on, asks every recorded child to stop, and kills
whatever is still there a grace period later.

`stop_all` signals and does not wait. It runs inside a signal handler on the
main thread, which may itself be the thread blocked reaping one of these
children — waiting there would wait on itself. Each owner keeps its own
`terminate` for the path it unwinds through, and the timer covers a child
whose owner never gets the chance.

TERM before KILL, everywhere. Pi stops the tool processes it started detached
only when it receives SIGTERM itself; a SIGKILL aimed at its group skips that
and leaves the tools running in groups of their own. The grace is the time
that cleanup gets.

Refusing new spawns matters as much as stopping the running ones. A thread
pool's shutdown still runs the work queued behind the agents it was waiting
on, so without the refusal a stopped review would start the next group's
agent on its way out.

Stdlib and `core.timeouts` only, like `core.proc`, which installs the handler
that calls this.
"""

# doc-group: platform

from __future__ import annotations

import atexit
import contextlib
import os
import signal
import subprocess
import threading
from collections.abc import Iterator, Sequence

import core.timeouts

# How long a child gets to act on SIGTERM before SIGKILL. Inside the five
# seconds a supervisor commonly allows between its own TERM and KILL — the Pi
# harness's `job_kill` among them — so the children are gone before this
# process is.
GRACE = 2.0


class StopRequested(RuntimeError):
    """A stop is under way, so no new child may start."""


# Reentrant because `stop_all` runs in a signal handler on the main thread,
# which may be interrupted while it already holds the lock in `spawn`.
_lock = threading.RLock()
# Each live child, and whether it leads a process group of its own.
_live: dict[subprocess.Popen, bool] = {}
_stopping = threading.Event()


def spawn(cmd: Sequence[str], **popen_kwargs) -> subprocess.Popen:
    """Start *cmd* and record it, or raise `StopRequested` during a stop.

    The check and the record are made under one lock with the spawn between
    them, so a stop cannot land after the check and miss the child.
    """
    with _lock:
        if _stopping.is_set():
            raise StopRequested(f"not starting {cmd[0]}: this process is stopping")
        proc = subprocess.Popen(cmd, **popen_kwargs)
        _live[proc] = bool(popen_kwargs.get("start_new_session"))
    return proc


def forget(proc: subprocess.Popen) -> None:
    """Stop tracking *proc*. Its owner calls this once it has been reaped.

    During a stop, the group a reaped leader led is swept with SIGKILL on the
    way out. The leader acting on SIGTERM says nothing about the rest of its
    group: a member that ignores TERM outlives it, its owner reaps the leader
    and forgets it, and the process can exit before `stop_all`'s timer fires.
    """
    with _lock:
        leads_group = _live.pop(proc, False)
    if leads_group and _stopping.is_set():
        _sweep_group(proc)


@contextlib.contextmanager
def owned(cmd: Sequence[str], **popen_kwargs) -> Iterator[subprocess.Popen]:
    """`spawn` for the block, `terminate` if it raises, `forget` however it ends.

    The block is responsible for reaping on its ordinary path. An exception
    leaving it — the `SystemExit` a stop handler raises on the main thread
    among them — stops the child on the way past rather than leaving it to
    run on unowned.
    """
    proc = spawn(cmd, **popen_kwargs)
    try:
        yield proc
    except BaseException:
        terminate(proc)
        raise
    finally:
        forget(proc)


def stopping() -> bool:
    """Whether `stop_all` has been called in this process."""
    return _stopping.is_set()


def _signal(proc: subprocess.Popen, sig: int) -> None:
    """Send *sig* to the group *proc* leads, or to *proc* alone if it leads none.

    A child already reaped is skipped: its pid may since have been reused, and
    a signal sent by number would land on a stranger. An unreaped child cannot
    have its pid reused, so a zombie is safe to signal. A child that is gone,
    or that this process may not signal, has nothing further to try.

    Only a child recorded as a group leader is signalled by group, so the
    safety of `killpg` rests on that record and not on a pid that names no
    group failing with ESRCH. A child that leads one is tried by group first
    and by pid second.
    """
    if proc.returncode is not None:
        return
    with _lock:
        leads_group = _live.get(proc, False)
    if leads_group:
        try:
            os.killpg(proc.pid, sig)
            return
        except ProcessLookupError:
            pass
        except PermissionError:
            return
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.kill(proc.pid, sig)


def terminate(proc: subprocess.Popen, grace: float = GRACE) -> bool:
    """Stop and reap *proc* from the thread that owns it. True when it is gone.

    SIGTERM, *grace* seconds to act on it, then SIGKILL and a bounded reap.
    A group leader that went on TERM has its group swept afterwards, for the
    member that ignored the signal it acted on. False is a child that
    outlived SIGKILL — uninterruptible sleep, or a group this process may not
    signal — which the caller reports; there is no further signal to try.
    """
    _signal(proc, signal.SIGTERM)
    try:
        proc.wait(timeout=grace)
        with _lock:
            leads_group = _live.get(proc, False)
        if leads_group:
            _sweep_group(proc)
        return True
    except subprocess.TimeoutExpired:
        pass
    _signal(proc, signal.SIGKILL)
    try:
        proc.wait(timeout=core.timeouts.QUICK)
    except subprocess.TimeoutExpired:
        return False
    return True


def live() -> list[subprocess.Popen]:
    """The children recorded and not yet forgotten, in the order they started."""
    with _lock:
        return list(_live)


def _sweep_group(proc: subprocess.Popen) -> None:
    """SIGKILL whatever is left of the group a reaped leader led.

    Safe only for a child that was started as a group leader: a group id stays
    reserved while the group has members, so the number reaches either those
    members or nobody. For any other child the pid is free for reuse once it
    is reaped, and could by now lead a stranger's group.
    """
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, signal.SIGKILL)


def _kill(procs: list[subprocess.Popen]) -> None:
    for proc in procs:
        _signal(proc, signal.SIGKILL)


def stop_all() -> None:
    """Refuse new children, TERM every live one, and KILL them after `GRACE`.

    Returns at once; see the module docstring for why it cannot wait. A second
    call is a second stop request from someone who did not want to wait the
    first one out, and kills immediately.

    The KILL takes the children recorded now rather than re-reading the
    registry when it fires. Nothing can join it once the stop is under way,
    and a child its owner reaped in the meantime is skipped by `_signal`.
    """
    repeated = _stopping.is_set()
    _stopping.set()
    procs = live()
    if repeated:
        _kill(procs)
        return
    for proc in procs:
        _signal(proc, signal.SIGTERM)
    timer = threading.Timer(GRACE, _kill, args=(procs,))
    timer.daemon = True
    timer.start()


def _terminate_survivors() -> None:
    for proc in live():
        terminate(proc)


# Defense in depth for a child whose owner never reaped it — a thread that died
# on an exception the spawn site did not anticipate. On an ordinary exit every
# owner has already called `forget` and this does nothing. It runs after the
# interpreter has joined its non-daemon threads, so no owner is left to reap
# concurrently and `terminate` can wait out the grace itself.
atexit.register(_terminate_survivors)
