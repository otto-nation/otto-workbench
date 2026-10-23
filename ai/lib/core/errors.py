"""How an entry point ends on an error: one line for the operator, a traceback for a bug.

Every `ai/` entry point wraps its work in the same boundary: record the error
on the trail, then re-raise. That is right for a bug, because the traceback is
the evidence. It is wrong for a failure the operator fixes by configuring
something — no AI backend selected, a provider with no model — where the
traceback buries the one line that says what to set, and the run reads as a
crash. `OperatorError` marks that kind; `boundary` is the one place that tells
the two apart, so no entry point decides it for itself.

Not: what the operator should set (each error's own message says that).
"""

# doc-group: platform

from __future__ import annotations

import traceback

import core.log

# The exit status an operator error ends with: a failed run, not a usage error,
# since the arguments were fine and the machine's configuration was not.
OPERATOR_ERROR_EXIT = 1


class OperatorError(RuntimeError):
    """A failure the operator fixes by configuring something, not a bug.

    The message must name what to set; `boundary` prints it alone.
    """


def boundary(trail, exc: BaseException) -> int:
    """End an entry point on *exc*: report an `OperatorError`, re-raise anything else.

    An operator error is printed as one line, recorded as `operator_error`, and
    returned as `OPERATOR_ERROR_EXIT`. Anything else is recorded as
    `unexpected_error` with its traceback and re-raised with that traceback
    intact. Call it as `return core.errors.boundary(trail, exc)` from the
    entry point's `except Exception as exc:` clause.
    """
    if isinstance(exc, OperatorError):
        core.log.error(str(exc))
        trail.error("operator_error", str(exc))
        return OPERATOR_ERROR_EXIT
    trail.error("unexpected_error", str(exc), data={
        "traceback": "".join(traceback.format_exception(exc)),
    })
    raise exc
