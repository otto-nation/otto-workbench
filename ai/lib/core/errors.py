"""How a run ends on an error: one line for the operator, a traceback for a bug.

A failure the operator fixes by configuring something — no AI backend selected,
a provider with no model, no pi installed — is not a bug, and a traceback
buries the one line that says what to set. `OperatorError` marks that kind.

Two pieces, split by where they act:

- `reraise` is every entry point's `except` clause. It records the error on the
  trail once and re-raises it, so a caller running the entry point in-process
  stops as it would have on a spawn. `pr fix` must not go on to its CI pass
  after its review pass found no backend; returning an exit code here would
  make "this machine cannot run AI" read like "this pass found problems".
- The process hook, installed when this module is imported, is the one place
  that decides how an uncaught error is shown. An `OperatorError` is printed as
  its message alone; anything else gets Python's usual traceback. The
  interpreter exits 1 either way. Every class that subclasses `OperatorError`
  imports this module, so the hook is in place wherever one can be raised.

Not: what the operator should set (each error's own message says that).
"""

# doc-group: platform

from __future__ import annotations

import sys
import traceback
from typing import NoReturn

import core.log

# Set on an exception once a trail has recorded it. Entry points nest — `pr`
# runs `review` in-process, which runs `review-orchestrate` — and each has its
# own boundary, so without the mark one failure became one record per level.
_RECORDED_ATTR = "_workbench_trail_recorded"


class OperatorError(RuntimeError):
    """A failure the operator fixes by configuring something, not a bug.

    The message must name what to set; the process hook prints it alone.
    """


def reraise(trail, exc: BaseException) -> NoReturn:
    """Record *exc* on *trail*, unless an inner boundary already did, then re-raise it.

    An `OperatorError` is recorded as `operator_error` with its message;
    anything else as `unexpected_error` with its traceback. Call it as the whole
    body of an entry point's `except Exception as exc:` clause — it re-raises
    the exception being handled, so the traceback is the original one.
    """
    if not getattr(exc, _RECORDED_ATTR, False):
        if isinstance(exc, OperatorError):
            trail.error("operator_error", str(exc))
        else:
            trail.error("unexpected_error", str(exc), data={
                "traceback": "".join(traceback.format_exception(exc)),
            })
        # An exception type that refuses attributes is recorded again by an
        # outer boundary, which is a duplicate record, not a lost one.
        try:
            setattr(exc, _RECORDED_ATTR, True)
        except (AttributeError, TypeError):
            pass
    raise


def _excepthook(exc_type, exc, tb, _previous=sys.excepthook) -> None:
    """Print an uncaught `OperatorError` as one line; defer anything else."""
    if isinstance(exc, OperatorError):
        core.log.error(str(exc))
        return
    _previous(exc_type, exc, tb)


# Installed on import, deliberately: an entry point cannot forget a hook it
# never has to install, and the shims under ai/bin hold nothing but the call.
sys.excepthook = _excepthook
