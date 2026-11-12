"""The exit codes every `pr` command shares, and what each means in general.

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


class ExitCode(IntEnum):
    """A process exit status a `pr` command may return."""

    OK = 0
    FAILURE = 1
    USAGE = 2
    CONFLICTS = 3
    REFUSED = 4
    # sysexits.h EX_TEMPFAIL: retry later — a live lock holder, a spent quota.
    TEMPFAIL = 75

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
    ExitCode.TEMPFAIL: "Temporary: retry later (a live lock holder, a spent GitHub quota)",
}
