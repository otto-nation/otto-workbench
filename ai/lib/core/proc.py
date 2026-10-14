"""One type for what a subprocess said, and one helper for running it.

`ai/` hand-rolls `subprocess.run(..., capture_output=True, text=True)` in
dozens of places, and each wrapper decides for itself what to keep. The ones
that return a positional tuple have no slot for stderr, so the cause of a
failure is gone before any caller can render it — `_gh_api` returning
`(returncode, stdout)` is why a 5xx from `gh` used to surface as an empty
message. Widening the tuple is not the fix: a caller reading fields by
name keeps working when a fourth thing needs carrying, and does not have to
learn the order.

`gh api` is the sharp case: it writes an API error body to stdout and its own
status line (`gh: ... (HTTP 503)`) to stderr, so a 404 is legible from stdout
while a 5xx or a dropped connection leaves stdout empty. A classifier reading
stdout alone calls that a success with no output.

So `run(cmd)` returns a frozen `CmdResult` carrying `returncode`, `stdout` and
`stderr`, and a caller reads what it needs by name:

| Read | What it gives you |
|---|---|
| `r.ok` | The command exited cleanly. |
| `r.detail` | `stderr` folded onto one line — what to quote in an error. |
| `r.combined_output` | Both streams, for classifying a failure by what it said. |
| `r.server_error` | The failure was a 5xx, so the remedy is to wait and retry. |
| `r.signalled` | A signal killed it; it never got to choose an exit code. |

`failure_message(action, r)` renders a failure without asserting a cause the
code has not established: it names the action, appends whatever the command
said, and calls out a 5xx separately, deciding that from `server_error` so the
message and a classifier reading the same result cannot disagree about which
stream the evidence was on. It accepts a raw `subprocess.CompletedProcess` too,
so a call site still running `subprocess.run` directly can report a failure
without converting first.

A signalled death is called out the same way, because it is the failure with
the least to say for itself: a process killed by SIGKILL or SIGPIPE writes
nothing on the way out, so every stream is empty and the bare action was all a
reader got — `git commit --allow-empty -m initial failed`, with no hint that
git never objected to anything and the machine simply ran out of room to
schedule it. The signal is named, and one that came from outside the process
says so; a fault signal (SIGSEGV, SIGABRT) does point at the command, so it
gets no such note. When the command explained nothing at all the exit code is
quoted, since it is then the only evidence there is.

An expired timeout is the same kind of answer. `run` converts it into a
`CmdResult` carrying `TIMEOUT_RETURNCODE` — the shell convention — with the bound
and the command quoted on stderr, and whatever the process wrote before it was
killed preserved on both streams. Raising instead would need a handler at each of
the call sites that has none; as a result code it degrades through
`out`/`ok`/`lines` exactly as any other failure does, and it is contract rather
than an implementation detail: the eval scorers tell a timed-out case from a
failed one by it.

Output that will not decode as UTF-8 is the third answer of that shape. It
arrives as a `UnicodeDecodeError` raised by the pipe rather than by anything
the caller did, so `run` decodes with replacement and the bytes come back as
text on the result. A non-UTF-8 file in a diff used to abort a review run at
post-processing, after every agent had been paid for.

A caller running something that spawns its own tree passes
`kill_process_group=True` and gets a child in a session of its own, so an
expired bound takes the whole group. Killing is not reaping: SIGKILL is posted
and may not have landed — `killpg` can be refused outright, and a child in
uninterruptible sleep does not receive it until it leaves that state — so every
wait after a kill on that path is bounded, and what outlived it is named on the
stderr the caller already reads. That path does not enter `Popen` as a context
manager for the same reason: `__exit__` reaps with an unbounded `wait()` for
anything but a `KeyboardInterrupt`, which would hang the unwinding of an
ordinary exception on exactly the group the kill failed to remove.

The default path still reaps unbounded, through `subprocess.run`'s own kill.
It is the narrower exposure by some way — one direct child, no `killpg` to be
refused, no grandchild to outlive the signal — and a `ceiling:` at that call
names what closing it would cost, which is a rewrite of how the suite stubs
`gh` rather than anything in this module.

Both of the first two are also *recorded*, in `MACHINE_KILLS`. Returning them as
ordinary results is right for the caller and is exactly what makes them
invisible to anyone watching from outside: a starved `git commit` comes back as
`COMMIT_FAILED`, the caller handles it as designed, and whatever goes wrong
afterwards carries no sign that the machine was the cause. So `run` appends a
`MachineKill` on its way past, and a reader that wants to know can ask — which
is what `tests/conftest.py` does when a test fails, so a contention casualty
arriving through the code under test is as recognisable as one arriving through
a fixture. Nothing about the result the caller gets changes, and no branch here
knows it is being observed.

Two things about the child are decided here rather than by each caller. Its
stdin is closed unless the caller passes text for it, because inheriting stdin
is how a subprocess reaches a stream its parent was in the middle of using —
the MCP server's stdin is the JSON-RPC transport, and a tool reading one byte
takes it out of the stream. And `kill_process_group` decides whether an expired
bound kills the direct child or the whole group: a caller running something
that spawns a tree of its own needs the group, or a timed-out call leaves the
tree running with nothing holding a handle to it.

The exit codes and `DETAIL_LIMIT` are conventions rather than choices, so
`bin/local/validate-magic-values` holds their monopoly: anywhere under `ai/`, a
literal 124, 127 or 130 written where something exits with it or reads it back
is rejected, and so is a slice bound that respells one of the caps —
`DETAIL_LIMIT` here or `trail.EXCERPT_LIMIT` next door. It reads the values out
of the modules that define them, so renaming a constant fails that check rather
than quietly retiring it.

Named `proc` rather than `cmd`: `ai/lib` goes on `sys.path` ahead of the
standard library, and a module called `cmd` there would shadow the stdlib
`cmd` that `pdb` imports.

Stdlib only, deliberately. This is the module everything else in `ai/lib`
should be free to depend on, and pulling in `log`, `agent.usage`, or
`workbench_paths` from here would make that impossible.
"""

# doc-group: platform

from __future__ import annotations

import contextlib
import os
import re
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import IO, TextIO

import core.children
import core.timeouts

# gh reports a transport failure as "HTTP 503: ..." on stderr, whether it came
# from REST or GraphQL, and git over https reports one as "HTTP 502" too.
# Matching the status line is enough to tell a server outage from a local
# misconfiguration.
_SERVER_ERROR_RE = re.compile(r"\bHTTP 5\d\d\b")

# How much of a stream to quote when it is the only account of a failure there
# is. stdout is a payload rather than a sentence, so it needs a bound; stderr is
# quoted whole because a command that writes an essay there is the exception.
#
# The bound on a console line generally, not only on the ones built here: a
# caller printing its own preview of a command's output is answering the same
# question, for a reader who still has the scrollback. What goes into a trail
# record instead takes `trail.EXCERPT_LIMIT`, which is wider because nobody
# reading it later has the terminal.
DETAIL_LIMIT = 200

# How many lines of a command's output are worth showing when the output is
# itself the failure. A pre-push hook that fails is often a whole test suite and
# the line naming which gate failed is the last one, so the head is the wrong
# end: it keeps the banner and loses the verdict. This is the bound both readers
# take — the console line `push.report` prints and the excerpt a trail record
# carries — because they are answering the same question at different widths.
TAIL_LINES = 20

# What `run` reports when a command outlived its timeout. 124 is the shell
# convention for a timeout kill, and it is contract rather than an
# implementation detail: the eval scorers distinguish a timed-out case from a
# failed one by this code.
TIMEOUT_RETURNCODE = 124

# What a process exits with when it was interrupted at the keyboard, and what an
# executable that is not on PATH comes back as. Both are shell convention in the
# same family as the timeout code above, and both were being written as bare
# numbers at the call sites that produce or read them — a raw `sys.exit(130)`
# says nothing about why that number, and the reader who has to know is the one
# reading `128 + SIGINT` off it.
INTERRUPT_RETURNCODE = 130
MISSING_RETURNCODE = 127


# The stops an entry point answers: the terminal's Ctrl-C, a supervisor's
# ordinary ask-to-stop (the Pi harness's `job_kill`, CI cancellation), and the
# closed terminal. Each used to end this process and leave its agents running.
STOP_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)

# `128 + N` for a death by signal N, read off the one code already named for it
# rather than spelled as a second literal.
_SIGNAL_EXIT_BASE = INTERRUPT_RETURNCODE - signal.SIGINT


def install_stop_handler(announce: Callable[[], None]) -> None:
    """Stop this process's children and exit quietly on any of `STOP_SIGNALS`.

    Installing a handler is process-level state, so it belongs to whoever owns
    the process — an entry point, never a library reached mid-run. That is why
    this is a function an entry point calls rather than something a module does
    on import: `signal.signal` overwrites without chaining and nothing restores
    it, so two installers in one process means the second silently wins for the
    rest of its life. Call it before the first child is spawned; a stop landing
    earlier finds nothing to orphan.

    The children go first. `core.children.stop_all` TERMs every child this
    process recorded and refuses new ones, so the `SystemExit` raised next
    unwinds through owners whose agents are already stopping, and a thread
    pool's shutdown drains its queue without starting anything. The exit is
    `128 + N`, the shell's code for a death by signal N.

    A repeated stop is someone who will not wait for the first: the children
    are killed outright and the process leaves with `os._exit`, skipping the
    unwinding the first stop started.

    `announce` says the run was interrupted. It is a callback rather than a
    `log` call because this module is stdlib-only by declaration — see the
    module docstring — and every caller already depends on `log` anyway.
    """
    def stop(signum: int, _frame: object) -> None:
        repeated = core.children.stopping()
        core.children.stop_all()
        code = _SIGNAL_EXIT_BASE + signum
        if repeated:
            os._exit(code)
        # A closed terminal (SIGHUP) is a stderr that may no longer be there;
        # the exit code is the contract, so the announcement cannot cost it.
        with contextlib.suppress(OSError):
            announce()
        sys.exit(code)

    for signum in STOP_SIGNALS:
        signal.signal(signum, stop)

# Signals that mean something outside the process ended it: the OOM killer and
# a supervisor's kill (SIGKILL, SIGTERM), a reader that went away (SIGPIPE), an
# operator or a CI cancellation (SIGINT, SIGHUP, SIGQUIT), a scheduler's CPU
# cap (SIGXCPU). None of them is anything the command chose, so a message that
# names one should say where to look instead. The fault signals — SIGSEGV,
# SIGBUS, SIGABRT, SIGILL, SIGFPE — are deliberately absent: those do point at
# the command, and telling a reader otherwise is the misdirection this exists
# to prevent.
#
# Public (no leading underscore) so a caller with its own signal-death
# reporting can name the set it is classifying against; `externally_killed`
# below is the predicate to reach for, and what `tests/conftest.py`'s
# `run_checked` asks rather than hand-copying the membership test.
EXTERNAL_SIGNALS = frozenset({
    signal.SIGHUP, signal.SIGINT, signal.SIGQUIT, signal.SIGPIPE,
    signal.SIGTERM, signal.SIGKILL, signal.SIGXCPU,
})

# What to append when the signal came from outside. Contention is the common
# cause on a loaded machine — an oversubscribed box kills what it cannot
# schedule — and the reader's next move is to re-run rather than to bisect.
_EXTERNAL_KILL_NOTE = "the machine ended it, not the command — re-run rather than bisect"


def externally_killed(returncode: int) -> bool:
    """The process was ended from outside rather than by a fault of its own.

    The one place the `EXTERNAL_SIGNALS` split is spelled. `_killed_message`,
    `run`'s record below and `tests/conftest.py`'s `run_checked` all ask the
    same question, and a fourth signal added to the set has to reach every one
    of them at once or they start disagreeing about whether the machine or the
    command is the suspect.
    """
    return returncode < 0 and -returncode in EXTERNAL_SIGNALS


def signal_description(returncode: int) -> str:
    """`SIGPIPE (signal 13)` for the signal behind a negative return code.

    Falls back to the bare number for a signal this platform has no name for,
    which is rarer than a `ValueError` escaping into an error path is welcome.
    Public for the same reason as `EXTERNAL_SIGNALS`: `tests/conftest.py`
    reuses it rather than keeping its own copy.
    """
    number = -returncode
    try:
        return f"{signal.Signals(number).name} (signal {number})"
    except ValueError:
        return f"signal {number}"


@dataclass(frozen=True)
class MachineKill:
    """A command `run` watched the machine end, rather than the command end itself.

    Both halves are needed to act on it: the command says which part of the
    work was lost, and the cause says whether the bound expired or a signal
    arrived. Neither is recoverable from the `CmdResult` a moment later — a
    killed process writes nothing, and `TIMEOUT_RETURNCODE` on its own does not
    say what timed out.
    """

    cmd: str
    cause: str

    def __str__(self) -> str:
        return f"{self.cmd} — {self.cause}"


# How many kills to keep. A bound rather than a list because `run` is called by
# long-lived things — an MCP server, a review pass over dozens of files — and an
# unbounded record of a failure mode nobody drains is a leak. The oldest go
# first: a reader asking about a failure wants the kills nearest to it.
MACHINE_KILL_LIMIT = 32

# Every command `run` saw the machine end, most recent last. Written on the way
# past and never read here — `run`'s contract is unchanged and no caller has to
# know this exists. `tests/conftest.py` clears it before each test and reports
# whatever it holds when one fails, which is how a starved subprocess inside the
# code under test stops being indistinguishable from a real assertion failure.
MACHINE_KILLS: deque[MachineKill] = deque(maxlen=MACHINE_KILL_LIMIT)


@dataclass(frozen=True)
class CmdResult:
    """What a command exited with and what it wrote to each stream.

    Every field defaults, so a test or a short-circuit can build one without
    running anything.
    """

    returncode: int = 0
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        """The command exited cleanly."""
        return self.returncode == 0

    @property
    def detail(self) -> str:
        """What the command said about a failure, folded onto one line.

        stderr, because that is where a command explains itself; stdout is the
        answer it was asked for. `combined_output` is the one to match patterns
        against.
        """
        return " ".join((self.stderr or "").split())

    @property
    def combined_output(self) -> str:
        """Both streams, for classifying a failure by what it said.

        Which stream carries the cause depends on the failure. `gh api` writes
        an API error body to stdout and its own status line to stderr, so a 404
        is legible from stdout alone while a 503 or a dropped connection leaves
        stdout empty and says everything on stderr. Reading one of them is how
        a transport failure classified as an unknown error with nothing to
        print.
        """
        return "\n".join(part for part in (self.stdout, self.stderr) if part)

    @property
    def server_error(self) -> bool:
        """The failure was the far end's, so the answer is to wait and retry."""
        return bool(_SERVER_ERROR_RE.search(self.combined_output))

    @property
    def signalled(self) -> bool:
        """A signal killed the process before it could choose an exit code.

        `subprocess` reports that as the negated signal number, so a return
        code below zero is the only evidence there is — a killed process
        usually writes nothing on the way out, leaving both streams empty and
        every message about it indistinguishable from a command that failed
        quietly on purpose.
        """
        return self.returncode < 0


def failure_message(action: str, r: CmdResult | subprocess.CompletedProcess) -> str:
    """Error text for a failed command, quoting what the command said.

    Asserting a cause the code has not established — "cannot determine the repo
    from the git remote" — sends the operator to the wrong place whenever the
    real fault is auth, the network, or a GitHub outage. So name the action that
    failed and let the command's own stderr name the cause. A 5xx is called out
    separately because it is the one case where the answer is to wait rather
    than to change anything.

    A signal death and an expired bound are called out for the opposite reason:
    the command has nothing to say, and the bare action reads as though it
    failed on its own terms. Naming the signal, and saying when it came from
    outside the process, is what stops a killed `git commit` from being
    investigated as a git problem. Anything else that explained nothing at all
    is rendered with its exit code, since that is then the only evidence.

    Accepts a raw `CompletedProcess` too: most of `ai/` still calls
    `subprocess.run` directly, and those call sites should not have to convert
    before they can report a failure.
    """
    if not isinstance(r, CmdResult):
        r = CmdResult(r.returncode, r.stdout or "", r.stderr or "")
    if r.server_error:
        # `server_error` reads both streams, so this branch is reached by a 5xx
        # that arrived on stdout with nothing on stderr. Quote stdout in that
        # case rather than annotate a retry and then say nothing about it.
        detail = r.detail or " ".join(r.stdout.split())[:DETAIL_LIMIT]
        return f"{action} — server error, retry later: {detail}"
    if r.signalled:
        return _killed_message(action, r)
    if not r.detail and r.returncode == TIMEOUT_RETURNCODE:
        return f"{action} — the bound expired before the command answered"
    if not r.detail:
        return f"{action} (exit {r.returncode})"
    return f"{action}: {r.detail}"


def tail(text: str, *, lines: int = TAIL_LINES, limit: int | None = None,
         indent: str = "") -> str:
    """The last few meaningful lines of what a command printed.

    At most *lines* of them, each prefixed with *indent*, and no more than
    *limit* characters in total when one is given — whole lines are dropped from
    the front until what is left fits, and a single line longer than the cap
    keeps its own last *limit* characters rather than being dropped to nothing.
    The indent is not counted against the cap; it is the reader's margin, not
    the command's output.

    Blank lines go because a command splits itself across both streams and
    `combined_output` joins them — an empty stream would otherwise contribute a
    gap that reads as missing output.
    """
    kept = [line.rstrip() for line in text.splitlines() if line.strip()][-lines:]
    if limit is not None:
        kept = _within(kept, limit)
    return "\n".join(f"{indent}{line}" for line in kept)


def _within(lines: list[str], limit: int) -> list[str]:
    """The last of *lines* that fit in *limit* characters once joined."""
    kept: list[str] = []
    used = 0
    for line in reversed(lines):
        # The newline every line but the first contributes to the join.
        cost = len(line) + (1 if kept else 0)
        if used + cost > limit:
            break
        kept.insert(0, line)
        used += cost
    if kept:
        return kept
    # Nothing fit, so the last line is longer than the whole cap. Its own tail
    # is still the most useful thing to keep — an empty excerpt would say
    # nothing at all about a failure that printed one very long line.
    return [lines[-1][-limit:]] if lines else []


def _killed_message(action: str, r: CmdResult) -> str:
    """`failure_message` for a process a signal ended.

    Whatever it managed to write first is still quoted — a command killed
    part-way through often explains the state it was left in — but the signal
    leads, because it is what the exit code cannot say.
    """
    killed = f"{action} — killed by {signal_description(r.returncode)}"
    if externally_killed(r.returncode):
        killed = f"{killed}; {_EXTERNAL_KILL_NOTE}"
    if not r.detail:
        return killed
    return f"{killed}: {r.detail}"


def _record_kill(cmd: list[str], cause: str) -> None:
    """Note that the machine ended *cmd*, for whoever asks about it later."""
    MACHINE_KILLS.append(MachineKill(" ".join(str(part) for part in cmd), cause))


def _text(value: str | bytes | None) -> str:
    """Whatever a killed process left on a stream, as text."""
    if value is None:
        return ""
    return value if isinstance(value, str) else value.decode(errors="replace")


def _kill_group(process: subprocess.Popen) -> str:
    """SIGKILL the group *process* leads, and say what may have survived.

    Returns the empty string when the signal landed or there was nothing left
    to signal, and a note for the timeout detail when it did not. Nothing here
    raises: this runs on the way to returning a timeout result, and an
    exception would replace that result with a failure no call site has a
    branch for.

    `start_new_session` made the child a group leader, so its pid is the group
    id. A process that exited between the bound expiring and this call leaves
    nothing to signal — the race, not a failure. A group this process may not
    signal is the one case worth telling the caller about, because something is
    then still running with nothing holding a handle to it.

    Signalling is not reaping, which is why `_reap` exists and why every caller
    here pairs the two. SIGKILL is posted and this returns; the caller then
    decides how long it is prepared to wait for the group to act on it.
    """
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return ""
    except PermissionError:
        return (f"\nprocess group {process.pid} could not be signalled and may "
                f"still be running")
    return ""


def _reap(process: subprocess.Popen) -> str:
    """Wait briefly for a killed *process*, and say so when it does not go.

    A bound rather than a bare `wait()`, because the kill that precedes this is
    asynchronous and not guaranteed to have landed at all. Three states reach
    here and only one of them is a process that is dying: `killpg` succeeded
    and SIGKILL is merely posted; `killpg` raised `PermissionError` and the
    group is alive and unsignalled; or the child is in uninterruptible sleep,
    where SIGKILL is not delivered until it leaves. An unbounded wait treats
    all three as the first, and the last two then hang the caller — which on
    the MCP path is a tool call that has already exceeded its budget.

    `QUICK` is the bound because this is only ever a wait for an already-dead
    process to be reaped, not for work: a group that has had SIGKILL and is
    still there after five seconds is not about to finish.

    Returns a note for the timeout detail, empty when the process is gone, so
    what outlived the kill reaches the same stderr the caller already reads.
    Nothing raises, for the same reason `_kill_group` does not.
    """
    try:
        process.wait(timeout=core.timeouts.QUICK)
    except subprocess.TimeoutExpired:
        return (f"\nprocess {process.pid} did not exit after SIGKILL and may "
                f"still be running")
    return ""


def _close_quietly(pipe: IO | None) -> None:
    """Close *pipe* if there is one, tolerating one the child already broke."""
    if pipe is None:
        return
    try:
        pipe.close()
    except (BrokenPipeError, OSError, ValueError):
        pass


def _close_pipes(process: subprocess.Popen) -> None:
    """Close the three pipes, tolerating one the child already broke.

    `Popen.__exit__` does this too, but it also reaps unbounded; this is the
    half worth having on a path that cannot afford to block. `communicate` has
    already closed them on the success path, so the second close is a no-op
    there and this is called unconditionally rather than on the error paths
    alone — one exit discipline is easier to keep right than three.
    """
    for pipe in (process.stdout, process.stderr, process.stdin):
        _close_quietly(pipe)


def _timed_out(cmd: list[str], timeout: float | None, exc: subprocess.TimeoutExpired,
               survivors: str) -> CmdResult:
    """The result an expired bound comes back as, from either spawn path."""
    detail = f"timed out after {timeout:g}s: {' '.join(cmd)}{survivors}"
    partial = _text(exc.stderr)
    return CmdResult(
        returncode=TIMEOUT_RETURNCODE,
        stdout=_text(exc.stdout),
        stderr=f"{detail}\n{partial}" if partial else detail,
    )


def _run_in_own_group(cmd: list[str], timeout: float | None, input_text: str | None,
                      spawn: dict) -> CmdResult:
    """Run *cmd* as a group leader, killing the whole group if the bound expires.

    `subprocess.run` cannot express this. Its timeout handler kills the direct
    child and it never yields the pid, so the group id a caller would need to
    reach the rest of the tree is gone by the time the exception arrives —
    which is the whole of why this path is hand-rolled rather than sharing the
    one above.

    An expired bound is not the only way out of `communicate`. A
    `KeyboardInterrupt`, or a `ValueError` from writing to a stream the child
    already closed, leaves through the bare clause below, which kills the group
    on the way and lets the exception continue — `subprocess.run` does the same
    for the same reason. Without it the tree this function exists to contain
    would outlive the call that started it.

    Popen is not entered as a context manager, and that is the other half of
    the same argument. `__exit__` closes the pipes — worth having — and then
    reaps with an unbounded `self.wait()` for anything but a `KeyboardInterrupt`,
    so a group that survived its SIGKILL would block the unwinding of an
    ordinary exception: the hang this function exists to prevent, reached while
    handling the error that was going to report it. The two halves are done by
    hand instead, `_reap` before `_close_pipes`, both bounded and neither able
    to raise.
    """
    stdin = subprocess.PIPE if input_text is not None else subprocess.DEVNULL
    process = subprocess.Popen(cmd, start_new_session=True, stdin=stdin, **spawn)
    try:
        stdout, stderr = process.communicate(input_text, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        survivors = _kill_group(process) + _reap(process)
        _close_pipes(process)
        return _timed_out(cmd, timeout, exc, survivors)
    except BaseException:
        _kill_group(process)
        _reap(process)
        _close_pipes(process)
        raise
    _close_pipes(process)
    return CmdResult(returncode=process.returncode, stdout=stdout or "", stderr=stderr or "")


# What the relayed path appends to stderr when a pump was still reading at the
# join deadline. Kept clear of every `git.push` refusal marker — a push result's
# output is classified by substring, and this line must not change the verdict.
_LATE_OUTPUT_NOTE = ("output was still arriving {bound:g}s after the command "
                     "ended — something it started holds the pipe; the "
                     "captured output stops here")

_RELAY_ERRORS = (BrokenPipeError, OSError, ValueError)


class _Tee:
    """Where the relayed path's pumps copy each line, until it is detached.

    One lock serialises the two pumps' writes so a stdout line and a stderr line
    cannot interleave mid-line on the relay. `detach` is the end of the call:
    from then on a pump still draining a pipe something else holds open keeps
    reading — a writer that blocks on a full pipe is not ours to wedge — but
    neither keeps the line nor shows it, so `run` returning is where the
    caller's terminal stops receiving this command's output.

    `detach` does not take the lock. A relay write can block (a stopped
    terminal, a full pipe to a parent that is not reading), and teardown waiting
    on it would be the unbounded wait the join deadline exists to prevent. The
    price is that one write already past the check when `detach` runs may still
    land after `run` returns; nothing after it can.
    """

    def __init__(self, relay: TextIO) -> None:
        self._relay = relay
        self._lock = threading.Lock()
        self._detached = threading.Event()

    def detach(self) -> None:
        """Stop keeping and relaying lines; see the class docstring."""
        self._detached.set()

    def copy(self, line: str, parts: list[str]) -> None:
        """Keep *line* in *parts* and show it on the relay, unless detached."""
        if self._detached.is_set():
            return
        parts.append(line)
        # A relay that is closed or broken must not cost the capture.
        with contextlib.suppress(*_RELAY_ERRORS), self._lock:
            if self._detached.is_set():
                return
            self._relay.write(line)
            self._relay.flush()


def _pump(pipe: TextIO, parts: list[str], tee: _Tee) -> None:
    """Copy *pipe* into *parts* and onto the relay, line by line, until EOF.

    Text mode translates a bare `\\r` to `\\n`, so a progress line redrawn in
    place arrives as a line of its own — on the relay and in the result alike,
    which is what `communicate` on the unrelayed path does to it too.

    The pump owns its pipe and closes it on the way out. Nothing else may: a
    close from another thread waits on the buffer lock a blocked read holds.
    """
    try:
        for line in pipe:
            tee.copy(line, parts)
    except _RELAY_ERRORS:
        pass
    finally:
        _close_quietly(pipe)


def _feed_stdin(stdin: TextIO, input_text: str) -> None:
    """Write *input_text* to the child and close its stdin (runs on a thread).

    It owns stdin for the same reason a pump owns its pipe: a write blocked on a
    child that never reads holds the buffer lock, and a close from the main
    thread would wait on it for as long as anything holds the read end open.
    """
    try:
        # A child that exited early closes its end; that is its answer, not ours.
        with contextlib.suppress(*_RELAY_ERRORS):
            stdin.write(input_text)
    finally:
        _close_quietly(stdin)


def _stop(process: subprocess.Popen, kill_process_group: bool) -> str:
    """SIGKILL the child or its group, then reap. See `_run_relayed`."""
    if kill_process_group:
        return _kill_group(process) + _reap(process)
    process.kill()
    return _reap(process)


def _finish_pumps(process: subprocess.Popen, pumps: list[threading.Thread],
                  tee: _Tee) -> str:
    """Join the pumps under one deadline, detach the tee, and say what is late.

    The deadline is `core.timeouts.QUICK`, read at call time and shared by every
    thread, so the whole teardown is bounded by one tier rather than one per
    pipe. A process the child started can inherit the pipe ends and hold them
    after the child is gone — a hook's background job, a killed child's
    grandchild — and an unbounded join would wait for it.

    A thread still running at the deadline owns its pipe, so nothing is closed
    under it (see `_pump`); the daemon closes it when the holder lets go. The
    tee is detached either way, which is what makes the result an account
    of everything relayed before `run` returned, and stops late output
    reaching the caller's terminal after it. Returns a note for stderr when a
    thread was late — the captured output is then known to be short — and the
    empty string otherwise.
    """
    bound = core.timeouts.QUICK
    deadline = time.monotonic() + bound
    for pump in pumps:
        # Not started (a failed `start`) or already done; either way no wait.
        if pump.is_alive():
            pump.join(max(0.0, deadline - time.monotonic()))
    tee.detach()
    # Only the two output pumps (listed first) say whether output is short; a
    # stdin feeder still held by a child that never reads loses nothing.
    if any(pump.is_alive() for pump in pumps[:2]):
        return "\n" + _LATE_OUTPUT_NOTE.format(bound=bound)
    if not any(pump.is_alive() for pump in pumps):
        _close_pipes(process)
    return ""


def _run_relayed(cmd: list[str], timeout: float | None, input_text: str | None,
                 spawn: dict, relay: TextIO, *, kill_process_group: bool) -> CmdResult:
    """Run *cmd*, teeing both streams to *relay* while still capturing them.

    `subprocess.run` only yields the pipes after the child exits, which is why a
    captured `git push` hid a twenty-minute pre-push hook. This path reads as
    the child writes, on one thread per pipe, and feeds stdin on a third so a
    child that never reads cannot hold the wait past its bound.

    Every way out goes through `_finish_pumps`, so every way out is bounded and
    leaves nothing relaying afterwards. The threads are started inside the
    guarded block: a `start` that fails, or an interrupt landing between two of
    them, kills the child like any other exception rather than leaving it
    writing into pipes nobody reads.

    The result has the unrelayed path's shape: the same decoding (both share
    `spawn`), the same `TIMEOUT_RETURNCODE` result through `_timed_out`, and
    `MACHINE_KILLS` recorded on the same two occasions.
    """
    stdin = subprocess.PIPE if input_text is not None else subprocess.DEVNULL
    process = subprocess.Popen(
        cmd, stdin=stdin, start_new_session=kill_process_group, bufsize=1, **spawn,
    )
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    tee = _Tee(relay)
    pumps = [
        threading.Thread(target=_pump, args=(process.stdout, stdout_parts, tee), daemon=True),
        threading.Thread(target=_pump, args=(process.stderr, stderr_parts, tee), daemon=True),
    ]
    if process.stdin is not None:
        pumps.append(threading.Thread(target=_feed_stdin, args=(process.stdin, input_text),
                                      daemon=True))
    try:
        for pump in pumps:
            pump.start()
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        survivors = _stop(process, kill_process_group)
        survivors += _finish_pumps(process, pumps, tee)
        _record_kill(cmd, f"timed out after {timeout:g}s")
        exc = subprocess.TimeoutExpired(
            cmd, timeout, output="".join(stdout_parts), stderr="".join(stderr_parts),
        )
        return _timed_out(cmd, timeout, exc, survivors)
    except BaseException:
        _stop(process, kill_process_group)
        _finish_pumps(process, pumps, tee)
        raise
    late = _finish_pumps(process, pumps, tee)
    if externally_killed(process.returncode):
        _record_kill(cmd, f"killed by {signal_description(process.returncode)}")
    return CmdResult(
        returncode=process.returncode,
        stdout="".join(stdout_parts),
        stderr="".join(stderr_parts) + late,
    )


def run(
    cmd: list[str],
    *,
    timeout: float | None,
    cwd: str | Path | None = None,
    input_text: str | None = None,
    env: dict[str, str] | None = None,
    kill_process_group: bool = False,
    relay: TextIO | None = None,
) -> CmdResult:
    """Run *cmd*, capturing both streams, and return what it said.

    Never raises on a non-zero exit — the exit code is one of the three things
    the caller gets back, not an exception. An expired timeout is the same
    answer for the same reason: the command produced no usable output, which is
    what a non-zero exit already means to every caller here. It comes back as
    `TIMEOUT_RETURNCODE` with the bound and the command quoted on stderr, so
    `git.client.out`/`ok`/`lines` degrade to their defaults exactly as they do
    for any other failure and no call site needs a handler it does not have.

    Whatever the process managed to write before it was killed is preserved —
    a command that timed out mid-answer often explains itself in the part that
    arrived.

    Output that is not valid UTF-8 is decoded with replacement rather than
    raising, for the same reason: bytes the pipe cannot decode arrive as a
    `UnicodeDecodeError` out of `communicate`, which is not a failure any
    caller here has a handler for. A review run reached post-processing, having
    paid for every agent, and was killed there by `git show` reading a latin-1
    shell script; `git diff` on a worktree holding one does the same before an
    agent starts. Undecodable bytes are text the command wrote, so they belong
    in `stdout` alongside the exit code, not in an exception. `_text` already
    decodes a killed process's streams this way, so the policy is now the same
    on both paths out of here.

    Replacement rather than `surrogateescape`, which round-trips to bytes but
    re-raises on the way out — writing the review file, or encoding a prompt as
    JSON — moving the abort somewhere with less context than here. The lossy
    case that would matter is a path: `core.quotePath=false` has git emit raw
    filename bytes, and U+FFFD does not re-encode to the name git gave. That
    surfaces as a loud `git add` failure (`pathspec ... did not match`, exit
    128) at call sites that all check it, not as a silent mis-stage.

    An expired bound and a death on an external signal are both appended to
    `MACHINE_KILLS` as they pass. That is the only trace either leaves: handing
    them back as ordinary results is what the callers need and is also what
    makes them unattributable afterwards.

    `timeout` is a tier from `timeouts`, not a number, and it has no default:
    an omitted bound is indistinguishable from nobody having thought about one,
    so opting out is spelled `timeouts.UNBOUNDED` and shows up in review. See
    that module for why the number itself does not belong to the caller.

    `env` replaces the child's environment outright rather than extending the
    parent's — `subprocess.run`'s own contract, and the only shape that lets a
    caller *remove* a variable. `eval.task` builds its fixture repo with the
    inherited `GIT_DIR` and friends stripped, and merging over `os.environ`
    here would put them back and quietly make the fixture a worktree of this
    checkout. A caller that wants to add rather than replace passes
    `os.environ | {...}`.

    The child's stdin is closed unless *input_text* gives it something to read.
    Inheriting it is how a subprocess reaches a stream its parent was in the
    middle of using: the MCP server's own stdin is the JSON-RPC transport its
    client writes requests into, and a tool reading one byte takes that byte
    out of the stream and kills the session on a parse error naming no tool.
    Nothing here is interactive, so there is nothing for an inherited stdin to
    be for.

    `kill_process_group` decides what an expired bound kills. By default the
    direct child, which is `subprocess.run`'s behaviour and right for a command
    that is one process. A caller running something that spawns its own tree
    passes True and gets the child in a session of its own, so the whole group
    goes: an MCP tool call drives agents, and signalling the direct child alone
    left them running against the account with nothing holding a handle to
    them. SIGKILL with no grace window either way — the budget has already
    expired, and a TERM-then-KILL ladder doubles the worst case on a call that
    is already late.

    `relay` is a live tee: each line the child writes is copied to it (typically
    `sys.stderr`) as it arrives, and still collected on the result. Without it
    both streams stay in the pipe until the child exits, which is how a captured
    `git push` hid a pre-push hook for the length of the hook. The default path
    stays silent: most callers want the result, not a second copy on the
    terminal.
    """
    spawn = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "errors": "replace",
        "cwd": cwd,
        "env": env,
    }
    if relay is not None:
        return _run_relayed(
            cmd, timeout, input_text, spawn, relay,
            kill_process_group=kill_process_group,
        )
    if kill_process_group:
        return _run_in_own_group(cmd, timeout, input_text, spawn)
    # `input` and `stdin` are mutually exclusive to `subprocess.run` — it opens
    # the pipe itself when there is something to write, so naming the closed
    # stream is only ours to do when there is not.
    if input_text is None:
        spawn["stdin"] = subprocess.DEVNULL
    # ceiling: this path inherits `subprocess.run`'s unbounded reap — on a bound
    # it SIGKILLs the direct child and then waits for it with no bound of its
    # own, so a child that cannot take the signal hangs the call, which is the
    # defect `_reap` closes on the group path. Much narrower here: the target is
    # this process's own child, so there is no `killpg` to be refused and no
    # grandchild to outlive the signal, leaving uninterruptible sleep on a
    # stalled mount as the only way in. Left because the cost is not in this
    # function: `subprocess.run` is the seam 58 stubs across 8 test files patch
    # to answer as `gh` would, handing back `CompletedProcess`, so taking the
    # spawn over here swaps that for a Popen double and rewrites all of them —
    # a refactor of the suite's mocking strategy rather than a bug fix.
    # Upgrade trigger: when a caller on this path is seen hanging past its
    # bound, or once those stubs move to a Popen-shaped double for any other
    # reason, at which point this becomes the same three-line change
    # `_run_in_own_group` took.
    try:
        completed = subprocess.run(cmd, input=input_text, timeout=timeout, **spawn)
    except subprocess.TimeoutExpired as exc:
        _record_kill(cmd, f"timed out after {timeout:g}s")
        # `subprocess.run` has already killed the child and reaped it; what is
        # left to do is turn the exception into the result every caller reads.
        return _timed_out(cmd, timeout, exc, "")
    if externally_killed(completed.returncode):
        _record_kill(cmd, f"killed by {signal_description(completed.returncode)}")
    return CmdResult(returncode=completed.returncode,
                     stdout=completed.stdout or "", stderr=completed.stderr or "")
