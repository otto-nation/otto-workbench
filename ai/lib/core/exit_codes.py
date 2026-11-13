"""The exit codes contracted CLIs declare, and what each means in general.

One owner for the numbers. Before this module the same seven values were
spelled in eight places — `EXIT_USAGE` twice, 75 as both `EXIT_BUSY` and
`EXIT_BUDGET_EXHAUSTED`, 4 as both `REFUSAL_EXIT` and `EXIT_SUPERSEDED` — and
each copy carried a comment explaining why it matched another. The old names
survive as aliases of these members, so callers read the meaning they always
did while the number has one home.

What a code means *for one command* — exit 4 is "already landed" for
`pr rebase` and "superseded" for `pr review` — belongs to that command's
`core.command_contract.CommandContract`; `summary` here is only the class.

Layer 1, so `core.run_lock` can use it as well as `cli`.
"""

# doc-group: platform

from __future__ import annotations

from enum import IntEnum

import core.proc


class ExitCode(IntEnum):
    """A process exit status a contracted command may return."""

    OK = 0
    FAILURE = 1
    USAGE = 2
    CONFLICTS = 3
    REFUSED = 4
    # sysexits.h EX_UNAVAILABLE: a service this needs is not there — no queue
    # worker could take the job, so nothing ran and the caller may run it itself.
    UNAVAILABLE = 69
    # sysexits.h EX_TEMPFAIL: retry later — a live lock holder, a spent quota.
    TEMPFAIL = 75
    # 128 + SIGINT: cancelled before the work finished, or Ctrl-C. core.proc owns
    # the shell convention, and validate-magic-values reads it there.
    CANCELLED = core.proc.INTERRUPT_RETURNCODE

    @property
    def summary(self) -> str:
        """One line on what this class of exit means, whatever the command."""
        return _SUMMARIES[self]


_SUMMARIES = {
    ExitCode.OK: "Success",
    ExitCode.FAILURE: "Failure: the command ran and could not do what was asked",
    ExitCode.USAGE: "Usage error: the arguments could not be understood (argparse's code)",
    ExitCode.CONFLICTS: "Conflicts a person must look at; a JSON report is on stdout",
    ExitCode.REFUSED: "Refused on purpose before changing anything; JSON names the override",
    ExitCode.UNAVAILABLE: "Unavailable: a service it needs is not there; nothing ran",
    ExitCode.TEMPFAIL: "Temporary: retry later (a live lock holder, a spent GitHub quota)",
    ExitCode.CANCELLED: "Cancelled: interrupted or the work was stopped before it finished",
}
