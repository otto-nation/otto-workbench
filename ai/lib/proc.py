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

Named `proc` rather than `cmd`: `ai/lib` goes on `sys.path` ahead of the
standard library, and a module called `cmd` there would shadow the stdlib
`cmd` that `pdb` imports.

Stdlib only, deliberately. This is the module everything else in `ai/lib`
should be free to depend on, and pulling in `log`, `ai_usage`, or
`workbench_paths` from here would make that impossible.
"""

# doc-group: platform

from __future__ import annotations

import re
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path

# gh reports a transport failure as "HTTP 503: ..." on stderr, whether it came
# from REST or GraphQL, and git over https reports one as "HTTP 502" too.
# Matching the status line is enough to tell a server outage from a local
# misconfiguration.
_SERVER_ERROR_RE = re.compile(r"\bHTTP 5\d\d\b")

# How much of a stream to quote when it is the only account of a failure there
# is. stdout is a payload rather than a sentence, so it needs a bound; stderr is
# quoted whole because a command that writes an essay there is the exception.
DETAIL_LIMIT = 200

# What `run` reports when a command outlived its timeout. 124 is the shell
# convention for a timeout kill, and it is contract rather than an
# implementation detail: the eval scorers distinguish a timed-out case from a
# failed one by this code.
TIMEOUT_RETURNCODE = 124

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
# reporting — `tests/conftest.py`'s `run_checked`, which cannot import a test
# fixture's stdlib-only helper from anywhere else — classifies from this set
# rather than hand-copying it and drifting from a future addition here.
EXTERNAL_SIGNALS = frozenset({
    signal.SIGHUP, signal.SIGINT, signal.SIGQUIT, signal.SIGPIPE,
    signal.SIGTERM, signal.SIGKILL, signal.SIGXCPU,
})

# What to append when the signal came from outside. Contention is the common
# cause on a loaded machine — an oversubscribed box kills what it cannot
# schedule — and the reader's next move is to re-run rather than to bisect.
_EXTERNAL_KILL_NOTE = "the machine ended it, not the command — re-run rather than bisect"


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


def _killed_message(action: str, r: CmdResult) -> str:
    """`failure_message` for a process a signal ended.

    Whatever it managed to write first is still quoted — a command killed
    part-way through often explains the state it was left in — but the signal
    leads, because it is what the exit code cannot say.
    """
    killed = f"{action} — killed by {signal_description(r.returncode)}"
    if -r.returncode in EXTERNAL_SIGNALS:
        killed = f"{killed}; {_EXTERNAL_KILL_NOTE}"
    if not r.detail:
        return killed
    return f"{killed}: {r.detail}"


def _text(value: str | bytes | None) -> str:
    """Whatever a killed process left on a stream, as text."""
    if value is None:
        return ""
    return value if isinstance(value, str) else value.decode(errors="replace")


def run(
    cmd: list[str],
    *,
    timeout: float | None,
    cwd: str | Path | None = None,
    input_text: str | None = None,
    env: dict[str, str] | None = None,
) -> CmdResult:
    """Run *cmd*, capturing both streams, and return what it said.

    Never raises on a non-zero exit — the exit code is one of the three things
    the caller gets back, not an exception. An expired timeout is the same
    answer for the same reason: the command produced no usable output, which is
    what a non-zero exit already means to every caller here. It comes back as
    `TIMEOUT_RETURNCODE` with the bound and the command quoted on stderr, so
    `git_client.out`/`ok`/`lines` degrade to their defaults exactly as they do
    for any other failure and no call site needs a handler it does not have.

    Whatever the process managed to write before it was killed is preserved —
    a command that timed out mid-answer often explains itself in the part that
    arrived.

    `timeout` is a tier from `timeouts`, not a number, and it has no default:
    an omitted bound is indistinguishable from nobody having thought about one,
    so opting out is spelled `timeouts.UNBOUNDED` and shows up in review. See
    that module for why the number itself does not belong to the caller.

    `env` replaces the child's environment outright rather than extending the
    parent's — `subprocess.run`'s own contract, and the only shape that lets a
    caller *remove* a variable. `eval_task` builds its fixture repo with the
    inherited `GIT_DIR` and friends stripped, and merging over `os.environ`
    here would put them back and quietly make the fixture a worktree of this
    checkout. A caller that wants to add rather than replace passes
    `os.environ | {...}`.
    """
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, cwd=cwd, timeout=timeout,
            input=input_text, env=env,
        )
    except subprocess.TimeoutExpired as exc:
        detail = f"timed out after {timeout:g}s: {' '.join(cmd)}"
        partial = _text(exc.stderr)
        return CmdResult(
            returncode=TIMEOUT_RETURNCODE,
            stdout=_text(exc.stdout),
            stderr=f"{detail}\n{partial}" if partial else detail,
        )
    return CmdResult(returncode=r.returncode, stdout=r.stdout or "", stderr=r.stderr or "")
