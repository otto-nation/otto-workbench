"""The gate every outward-facing write passes through.

A PR reply, a summary comment, a tracking issue — each one is visible to other
people the moment it lands, and a wrong one has to be retracted in front of the
reviewer. So the default is to draft: callers print what they would have sent and
report failure, and nothing leaves the machine until the entrypoint opts in.

One flag owns this for the whole process. Modules that write externally
(`pr_comments`, `review_issue`) ask here rather than carrying their own switch.
"""

from __future__ import annotations

import log

_enabled = False


def enable() -> None:
    """Let external writes through for the rest of the process."""
    global _enabled
    _enabled = True


def enabled() -> bool:
    """Whether writes reach the outside world.

    Callers use this to keep their logging honest: a draft is not a failure,
    so error paths must not fire when the gate is closed.
    """
    return _enabled


def draft(action: str, body: str = "") -> None:
    """Record what would have been written, to stderr."""
    log.info(f"DRAFT (not published) — {action}")
    for line in body.splitlines():
        log.dim(line)
