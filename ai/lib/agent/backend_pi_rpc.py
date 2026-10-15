"""Pi RPC process plumbing for the Pi backend.

Owns the JSONL stdin/stdout protocol around a ``pi --mode rpc`` child:
sending commands, reading replies, classifying a fatal response, and
starting/stopping the process group. The event loop that consumes the
stream lives in ``agent.backend_pi``.
"""

# doc-group: backend

from __future__ import annotations

import json
import subprocess
import threading
from collections.abc import Iterator
from contextlib import contextmanager

import core.children
import core.log
import core.timeouts


# Serialises writes to the child's stdin. The stall watchdog sends its abort
# from its own thread while the stream loop may be sending a steer, and two
# unsynchronised write/flush pairs can interleave into one malformed JSONL
# line — which Pi answers with a parse error and drops, losing whichever
# command mattered. One lock around the whole line is enough because every
# command here is a single short write.
_STDIN_LOCK = threading.Lock()


def _send(proc: subprocess.Popen, command: dict) -> bool:
    """Write a JSONL command to the RPC process's stdin. True when it landed.

    Writing to a child that has already exited raises rather than failing
    quietly, and every caller here is capable of reaching a dead Pi: the very
    first `prompt` when Pi rejected its own flags and exited, and the mid-run
    `abort`/`steer` that follow a turn_end Pi emitted on its way out. An
    unguarded traceback from those is the same class of fault this module was
    fixed for — an early failure surfacing as something other than the error
    it is — so the failure is reported to the caller, which knows whether the
    command mattered.
    """
    try:
        with _STDIN_LOCK:
            proc.stdin.write(json.dumps(command) + "\n")
            proc.stdin.flush()
        return True
    except (BrokenPipeError, ValueError):
        return False


def _read_rpc_response(proc: subprocess.Popen, command_type: str) -> dict:
    """Read lines until we get a response for the given command type.

    Skips any interleaved events (shouldn't occur after agent_end,
    but handles gracefully).
    """
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if data.get("type") == "response" and data.get("command") == command_type:
            return data
    return {}


# Pi wraps every RPC reply in {type, command, success, data}. Only these two
# commands make a failure fatal to the run. Per Pi's RPC protocol a `prompt`
# reply with success:false means the prompt was rejected before acceptance, so
# no agent_start, no turn_end and no agent_end will ever follow, and a reader
# waiting for them waits out the whole timeout. `parse` is the same fault one
# step earlier: Pi could not read the command line at all. A failed `steer`,
# `follow_up` or `abort` is not fatal — _check_limits sends those mid-run, the
# agent that rejected one is still streaming, and the run still ends in
# agent_end. Ending the run there would discard the work of a healthy agent
# over a steering message it no longer needed.
_FATAL_RPC_COMMANDS = frozenset({"prompt", "parse"})

# Enough of a provider or auth error to act on. The text is a message meant for
# a human, not a payload, and a runaway one would otherwise land whole in the
# session log and in every ledger row that quotes it.
_RPC_ERROR_MAX_CHARS = 2000

# Pi was gone before its prompt could be written. Reported in the same shape as
# a refusal, because it is the same failure from the caller's side: the run
# never started and nothing downstream should wait for it. Prefer
# `not_served_message` when stderr names a missing model — this string is the
# remainder, not the diagnosis.
_PROMPT_UNDELIVERED = (
    "pi exited before the prompt could be sent — its stdin was already closed"
)

# The same failure when the child said why on its way out: followed by the
# first line of its stderr, which names the cause where the closed stdin only
# names the symptom.
_PROMPT_UNDELIVERED_BECAUSE = "pi exited before the prompt could be sent: "

# One stderr line, quoted into a failure reason a summary table renders.
_STDERR_LINE_MAX_CHARS = 300

# What a run Pi refused exits with when Pi's own status does not say so. A
# clean RPC shutdown after a rejected prompt is exit 0, and a caller reading
# only the status would take that for a successful run.
_RPC_ERROR_EXIT_CODE = 1


def _rpc_response_error(data: dict) -> str | None:
    """The error text of an RPC response that ends the run, or None.

    A non-fatal failure is warned about rather than returned: the run is still
    streaming and its work is worth more than the command Pi declined. Nothing
    is dropped in silence either way — the unconditional skip that preceded
    this is what let an auth failure consume a whole timeout with no output.

    ``success`` is compared against False by identity: a response that carries
    no ``success`` key is not a failure, and a truthiness test would read an
    absent key, a 0 or an empty string as one.
    """
    if data.get("success") is not False:
        return None
    command = data.get("command", "")
    detail = str(data.get("error") or f"pi rejected {command or 'a command'}")
    detail = detail[:_RPC_ERROR_MAX_CHARS]
    if command in _FATAL_RPC_COMMANDS:
        return detail
    core.log.warn(f"pi rpc {command or 'command'} failed: {detail}")
    return None


def _wait_for_exit(proc: subprocess.Popen, *, abandoned: bool) -> bool:
    """Wait for Pi to exit after its stdin was closed. True when it is gone.

    Bounded only for a run this module abandoned. Breaking out of the event
    loop on a fatal response leaves a child that is still alive with an unread
    stdout pipe, and waiting on that forever is the same silent hang the break
    exists to end, one line further down.

    A run that reached agent_end is waited for as long as it takes, which is
    what the code did before there was an error path at all. Pi's shutdown does
    real work — flushing its log, tearing down extensions — and this machine's
    own test runner documents subprocesses losing the scheduler for seconds
    under load. A bound here would kill a healthy run that had already written
    its output and report the whole thing as a failure, which is a worse
    outcome than the wait it would be shortening.

    The stop is `core.children.terminate`: SIGTERM to the group Pi leads, a
    grace period, then SIGKILL. TERM first because Pi stops the tool processes
    it started detached only on its own SIGTERM, and those lead groups of their
    own that no kill aimed at Pi's group reaches. A group that will not reap
    even after SIGKILL is reported and then left: raising would replace a
    failure the caller can act on with a traceback none of them handles, and
    there is no further signal to try.

    False is that last case, and it is the whole reason this returns anything.
    A caller that reads the child's stderr afterwards blocks until the child
    closes it, which a process that would not die for SIGKILL never does — so
    "reported and left" becomes a silent hang two lines later unless the caller
    is told to skip it.
    """
    if not abandoned:
        proc.wait(timeout=core.timeouts.UNBOUNDED)
        return True
    # The LOCAL wait precedes `terminate` here, unlike the exception path
    # (`_rpc_process`), which terminates at once: this run was abandoned on a
    # fatal response with stdin already closed, so Pi may be exiting on its own
    # and gets a short chance to do so cleanly. An exception leaving the block
    # is a stop or a failure that wants the child gone now.
    try:
        proc.wait(timeout=core.timeouts.LOCAL)
    except subprocess.TimeoutExpired:
        if not core.children.terminate(proc):
            core.log.warn(f"pi process group {proc.pid} did not reap after SIGKILL")
            return False
    return True


def _close_pipes(proc: subprocess.Popen) -> None:
    """Close the three pipes, tolerating one the child already broke.

    Popen.__exit__ does this, but it also reaps unbounded; this is the half
    worth having on a path that cannot afford to block.
    """
    for pipe in (proc.stdout, proc.stderr, proc.stdin):
        if pipe is None:
            continue
        try:
            pipe.close()
        except (BrokenPipeError, OSError, ValueError):
            pass


@contextmanager
def _rpc_process(cmd: list[str], **spawn) -> Iterator[subprocess.Popen]:
    """Start Pi in its own process session, stopping the group on the way out.

    ``start_new_session`` is what makes the child a group leader, so that a
    stop can reach the tools it spawned rather than only Pi itself. It also
    takes the child out of the terminal's foreground group and out of any
    supervisor's kill aimed at this process's group, so nothing but this
    process can stop it. Two things do: the `except` below, for an exception
    on the thread that owns the child, and `core.children.stop_all`, which the
    entry point's stop handler calls for a signal — which lands on the main
    thread, never on this one. `core.children.spawn` is what records the child
    for the second, and refuses to start one once a stop is under way.

    The pipes are closed and the child reaped by hand rather than by entering
    Popen. Its __exit__ reaps with an unbounded `self.wait()` for anything but
    a KeyboardInterrupt, so a group that survived SIGKILL would hang the
    unwinding of an ordinary exception — the same silent hang this module
    exists to avoid, on the one path with an exception already in flight. The
    success path does not come through here at all: `_wait_for_exit` has
    already waited, on the terms that path needs.
    """
    proc = core.children.spawn(cmd, start_new_session=True, **spawn)
    try:
        yield proc
    except BaseException:
        if not core.children.terminate(proc):
            core.log.warn(f"pi process group {proc.pid} did not reap after SIGKILL")
        _close_pipes(proc)
        raise
    finally:
        core.children.forget(proc)


def _get_stats_after_agent_end(proc: subprocess.Popen) -> dict:
    """Query get_session_stats after the agent has finished.

    Safe to call only after agent_end — no event interleaving.

    Returns the response's ``data``, not the response. Pi wraps every RPC reply
    in ``{type, command, success, data}``, and the caller wants the stats. The
    unwrap is here rather than at the read site because this function's name
    promises stats: reading ``tokens`` off the envelope silently yields an empty
    dict, which reaches the ledger as a run that cost money and spent no tokens.
    """
    if not _send(proc, {"type": "get_session_stats"}):
        return {}
    resp = _read_rpc_response(proc, "get_session_stats")
    data = resp.get("data")
    return data if data is not None else resp
