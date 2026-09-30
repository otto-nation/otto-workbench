"""Running the repo's own checks against what a fix pass just edited.

The pipeline had two agents and no runner. One answers findings, the other
checks those answers — and `verify-fixes.md` says outright that a path nothing
named covers is *not verified*, not a reason to run everything. So nothing in
the pass ever asked the question a regression answers to: **did this break
something nobody named?**

Both of the regressions that motivated this module were that question. A fix
pass deleted an import its own file never used — correctly, as far as that file
went — and two tests in another file read it off the module. A second fix
reworded an error message to name a symbol's new home; the reword was right and
the test asserting the old wording was not carried with it. Neither is a false
claim, so neither was reachable by making the claim-checker stricter. One was
reported ``Fixed [N1]``, the other ``Fixed [N2]``, in a commit whose body said
``4 fixed, 0 skipped``.

**Why a declared command rather than a hardcoded runner.** `fix.engine` runs in
every repo the workbench drives, and `bin/local/select-pytest` exists in exactly
one of them. Hardcoding this repo's selector into the shared engine would invent
a definition of green for every other repo — the thing `testing.md` forbids. So
the repo names its own command in `fix.verify_command`, and a repo that names
nothing gets exactly the pass it got before this module existed, plus a line
saying nothing ran.

**Why it only ever demotes.** A green run says the selected tests pass with the
agent's edits in the tree. It does not say any individual fix works — that is
the verify gate's question, and it is answered per item against a claim. This
runs once for the whole pass, so its verdict is the batch's and cannot be
attributed to one item: `ItemOutcome.verified` is set to False on a red run and
left alone on a green one. Promoting on green would credit every item in the
batch with evidence that belongs to none of them, which is the path-attribution
ceiling `fix.reconcile` already documents, walked from the other end.

**Why before the commit and not after.** The commit body is the artifact people
read, and a body that says ``4 fixed`` over a red suite is the whole defect. So
this runs between the agent and the landing, and the summary is rendered from
outcomes it has already touched. Selection is by the committed diff and
execution is against the worktree, which is what makes that ordering work: the
agent's edits are uncommitted but they are *in the tree the tests import*, and
the files it may touch are restricted to the branch's own (`fix.scope`), which
the committed diff already names.

The command is argv, split with `shlex` and run without a shell. Not a security
boundary — the agent that just ran had unrestricted bash — but a repo's gate is
one command, and accepting a shell line would invite the `&&` chain that becomes
a second definition of green living in a YAML string.
"""

# doc-group: platform

from __future__ import annotations

import os
import shlex
import signal
import subprocess
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from core import log
from core.trail import Trail, tinfo, twarn
from pr.fix import ItemOutcome

# What of a failing run's output is worth carrying into a commit body and a
# terminal. The tail rather than the head: pytest and bats both put the summary
# of what failed at the end, and a head-clip of a verbose runner is the banner.
OUTPUT_TAIL_CHARS = 2000

# The hedge a red run puts on every fix the pass claimed. Phrased as the state
# of the tree rather than as an accusation about the item — one run cannot tell
# which of sixteen items broke it, and saying "this fix is wrong" sixteen times
# for one regression is a caveat readers learn to skip.
RED_DETAIL = "the repo's checks are red with this pass's changes in the tree"

# Said once in the summary when the repo declares no command, so the reader
# knows the difference between "checked and clean" and "nobody looked". Without
# this line those two render identically, which is the state that let a red
# suite ship under `4 fixed, 0 skipped`.
NOT_DECLARED_NOTE = (
    "No verification command declared (fix.verify_command) — "
    "nothing was run against these changes."
)


class SuiteStatus(StrEnum):
    """How the declared command ended, in the terms a summary reports.

    ``ERROR`` and ``TIMED_OUT`` are kept apart from ``RED`` because they are not
    findings about the code. A command that cannot be started is a broken
    declaration and a timeout is a budget, and reporting either as a red suite
    would send a reader looking for a regression that is not there — while
    reporting them as green would be the silence this module exists to end.
    """

    # The pass had nothing worth checking — it claimed no fixes, or changed no
    # files. Distinct from NOT_DECLARED, and the distinction is load-bearing:
    # this one is silent, because a note about checks nobody was going to run
    # would land on every pass that fixes nothing, and a caveat that appears
    # everywhere is read nowhere. NOT_DECLARED is the case that must speak.
    NOT_ATTEMPTED = "not_attempted"
    # There was work to check and the repo names no command for checking it.
    NOT_DECLARED = "not_declared"
    # Ran, exit 0.
    GREEN = "green"
    # Ran, non-zero exit.
    RED = "red"
    # Killed at `fix.verify_timeout`.
    TIMED_OUT = "timed_out"
    # Could not be started, or the declaration could not be parsed.
    ERROR = "error"


@dataclass(frozen=True)
class SuiteResult:
    """What the repo's checks said about the tree the pass is about to commit."""

    status: SuiteStatus = SuiteStatus.NOT_ATTEMPTED
    command: str = ""
    duration_s: float = 0.0
    # The tail of combined stdout/stderr, already clipped. Empty on every
    # status but RED and ERROR — a green run's output is noise in a commit body.
    output_tail: str = ""

    @property
    def ran(self) -> bool:
        """Whether the command started and reached a verdict about the code."""
        return self.status in (SuiteStatus.GREEN, SuiteStatus.RED)

    @property
    def demotes(self) -> bool:
        """Whether this result withdraws the pass's claim to have fixed anything.

        Only a red run does. A timeout and a failure to start leave the claims
        where the verify gate put them: neither is evidence about the code, and
        demoting on them would make a slow machine look like a broken branch.
        They are still reported — `note` says so — so the reader is told the
        checks did not answer rather than told they passed.
        """
        return self.status is SuiteStatus.RED

    @property
    def reportable(self) -> bool:
        """Whether this result is worth a line in a summary a person reads."""
        return self.status is not SuiteStatus.NOT_ATTEMPTED

    @property
    def note(self) -> str:
        """The one line the commit body and the terminal carry for this result."""
        if self.status is SuiteStatus.NOT_ATTEMPTED:
            return ""
        if self.status is SuiteStatus.NOT_DECLARED:
            return NOT_DECLARED_NOTE
        if self.status is SuiteStatus.GREEN:
            return f"Checks green: {self.command} ({self.duration_s:.0f}s)"
        if self.status is SuiteStatus.RED:
            return f"Checks RED: {self.command} ({self.duration_s:.0f}s)"
        if self.status is SuiteStatus.TIMED_OUT:
            return (
                f"Checks did not finish: {self.command} timed out after "
                f"{self.duration_s:.0f}s — nothing was established either way"
            )
        return (
            f"Checks could not run: {self.command} — {self.output_tail or 'no detail'}"
        )


def _clip_tail(text: str, limit: int = OUTPUT_TAIL_CHARS) -> str:
    """The last `limit` characters of `text`, marked when it had to give.

    Clipped in Python on captured output rather than by piping the runner
    through `tail`, which would report the filter's exit status instead of the
    suite's — the masking `testing.md` names, and the one mistake this whole
    module exists to stop making.
    """
    marker = "[...]\n"
    stripped = text.strip()
    if len(stripped) <= limit:
        return stripped
    # The marker is inside the budget, not added to it: a caller that sized
    # `limit` against a commit body would find the clip overran the one number
    # it was given.
    return marker + stripped[-(limit - len(marker)):]


def run(
    workdir: Path,
    command: str,
    timeout_s: int,
    trail: Trail | None = None,
) -> SuiteResult:
    """Run the repo's declared checks in `workdir` and report what they said.

    Never raises for the command's own behaviour. A pass whose verification
    blew up must still land its work — the edits are real whatever the runner
    did, and losing them to a broken declaration would be a worse failure than
    the one this module prevents.
    """
    if not command.strip():
        return SuiteResult(status=SuiteStatus.NOT_DECLARED)

    try:
        argv = shlex.split(command)
    except ValueError as exc:
        return SuiteResult(
            status=SuiteStatus.ERROR, command=command,
            output_tail=f"could not parse fix.verify_command: {exc}",
        )
    if not argv:
        # A declaration that parsed to nothing — a lone `#` comment, say.
        # Carried with its text rather than reported as an absent command, so
        # its author is not sent looking for a key they had already set.
        return SuiteResult(
            status=SuiteStatus.ERROR, command=command,
            output_tail="fix.verify_command parsed to an empty argv",
        )

    log.info(f"Verifying the pass against the repo's checks: {command}")
    started = time.monotonic()
    try:
        result = _invoke(argv, workdir, command, timeout_s, started)
    except OSError as exc:
        # The command could not be started at all — missing, not executable, a
        # bad interpreter line. A broken declaration, not a red branch.
        result = SuiteResult(
            status=SuiteStatus.ERROR, command=command,
            duration_s=time.monotonic() - started, output_tail=str(exc),
        )

    _report(result, trail)
    return result


# How long the runner gets to shut itself down after TERM before it is killed.
# Short: the verdict is already decided by the timeout, and this is only about
# not orphaning the tree.
_TERM_GRACE_S = 5


def _invoke(
    argv: list[str], workdir: Path, command: str,
    timeout_s: int, started: float,
) -> SuiteResult:
    """Run `argv` to completion or to the timeout, and read its exit status.

    `Popen` with `start_new_session` rather than `subprocess.run(timeout=...)`,
    because a test runner is a process *tree*. `run`'s timeout kills the direct
    child only, and this repo's runner forks up to twelve parallel workers — a
    timeout there would leave them running against a worktree the pass is about
    to commit, competing with whatever the operator does next. The new session
    makes the child a group leader so the whole tree can be signalled.

    A non-zero exit is the answer this exists to report, not an exception; the
    output is captured rather than piped to a filter, so the status read below
    is the runner's own and not some `tail`'s.
    """
    proc = subprocess.Popen(
        argv, cwd=workdir, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        _terminate_tree(proc)
        return SuiteResult(
            status=SuiteStatus.TIMED_OUT, command=command,
            duration_s=time.monotonic() - started,
        )

    green = proc.returncode == 0
    return SuiteResult(
        status=SuiteStatus.GREEN if green else SuiteStatus.RED,
        command=command,
        duration_s=time.monotonic() - started,
        output_tail="" if green else _clip_tail((stdout or "") + (stderr or "")),
    )


def _terminate_tree(proc: subprocess.Popen) -> None:
    """Signal the whole process group, falling back to the child alone.

    TERM before KILL so a runner that handles it can stop its own workers
    tidily. The group may already be gone — the leader can exit while a worker
    holds the pipe open — so `ProcessLookupError` is an ordinary outcome here
    rather than a failure, and both passes tolerate it.

    Returns once the tree is reaped. A KILL that still leaves `communicate`
    blocked has nothing further to escalate to, so the second pass falls
    through rather than looping: a wedged uninterruptible child is a kernel
    state, not something another signal reaches.
    """
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(os.getpgid(proc.pid), sig)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        try:
            proc.communicate(timeout=_TERM_GRACE_S)
        except subprocess.TimeoutExpired:
            continue
        return


def _report(result: SuiteResult, trail: Trail | None) -> None:
    """Put the verdict on the terminal and the trail before anything acts on it."""
    data = {
        "status": str(result.status),
        "command": result.command,
        "duration_s": round(result.duration_s, 1),
    }
    if result.status is SuiteStatus.GREEN:
        log.info(result.note)
        tinfo(trail, "fix_verify_suite", result.note, data=data)
        return
    log.warn(result.note)
    twarn(trail, "fix_verify_suite", result.note,
          data={**data, "output_tail": result.output_tail})


def apply_to(outcomes: list[ItemOutcome], result: SuiteResult) -> int:
    """Withdraw the pass's verification claims when the repo's checks are red.

    Returns how many outcomes were demoted, for the caller's log line.

    Demotion is to ``verified = False``, not out of ``FIXED``. The distinction
    is the one `ItemOutcome.verified` already draws: a falsified fix is demoted
    out of FIXED by the verify gate, which was told what to check and found it
    wrong. This ran one command over the whole pass and knows only that the tree
    is red — the edit may well be the right one, with a stale test beside it, as
    it was for the message reword this module was written for. Claiming to know
    which would be inventing an attribution the run cannot support.

    A detail the gate already wrote is kept and this reason is appended, because
    the gate's is about the item and this one is about the tree; a reader
    chasing a red suite needs both and neither replaces the other.
    """
    if not result.demotes:
        return 0
    demoted = 0
    for outcome in outcomes:
        if not outcome.outcome.counts_as_fixed:
            continue
        outcome.verified = False
        outcome.verify_detail = (
            f"{outcome.verify_detail}; {RED_DETAIL}"
            if outcome.verify_detail else RED_DETAIL
        )
        demoted += 1
    if demoted:
        log.warn(
            f"The repo's checks are red — {demoted} claimed "
            f"fix{'es' if demoted != 1 else ''} recorded as unverified. "
            "At least one of them is wrong, or a test beside one is stale."
        )
    return demoted


def should_run(outcomes: list[ItemOutcome], changed: set[str] | None) -> bool:
    """Whether this pass produced anything worth running the repo's checks over.

    Two ways to have nothing to verify, and they are not the same: a pass that
    claimed no fixes has made no assertion to withdraw, and a pass that changed
    no files has left the tree exactly as the last run of these checks found it.
    Either way the run would cost minutes to restate what is already known.

    ``changed is None`` is the unreadable-worktree case, where the pass commits
    nothing — there is no tree state to attribute a result to, so this declines
    rather than running the checks over somebody else's edits.
    """
    if not changed:
        return False
    return any(o.outcome.counts_as_fixed for o in outcomes)


__all__ = [
    "NOT_DECLARED_NOTE",
    "RED_DETAIL",
    "SuiteResult",
    "SuiteStatus",
    "apply_to",
    "run",
    "should_run",
]
