"""How an entry point reports a GitHub write refused at the owner boundary.

`gh.client.run` raises `gh.owners.OwnerBoundaryError` before gh starts, from
whatever depth the write was made at. An entry point that let it escape would
print a traceback over the one sentence that says what to do; one that caught
it as a generic failure would hide that the refusal was policy rather than a
fault. `reports_owner_boundary` wraps a `main` so the sentence is printed and
the exit is `OWNER_BOUNDARY_EXIT` — 1, because 2 is already `EXIT_USAGE`.
The guard protocol (`owners-check`) keeps 2 for a refusal.
"""

# doc-group: cli

from __future__ import annotations

import functools
import sys
from collections.abc import Callable

import gh.owners

OWNER_BOUNDARY_EXIT = 1


def reports_owner_boundary(main: Callable[..., int]) -> Callable[..., int]:
    """*main*, printing an owner-boundary refusal and exiting 1 instead of raising."""
    @functools.wraps(main)
    def wrapper(*args, **kwargs) -> int:
        try:
            return main(*args, **kwargs)
        except gh.owners.OwnerBoundaryError as exc:
            print(f"✗ Refused at the owner boundary: {exc}", file=sys.stderr)
            return OWNER_BOUNDARY_EXIT
    return wrapper


def record_exception(trail, exc: BaseException) -> None:
    """Record *exc* on *trail*. A boundary refusal is policy, not a fault."""
    action = (
        "owner_boundary" if isinstance(exc, gh.owners.OwnerBoundaryError)
        else "unexpected_error"
    )
    trail.error(action, str(exc))
