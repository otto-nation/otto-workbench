"""The gate every outward-facing write passes through.

A PR reply, a summary comment, a tracking issue — each one is visible to other
people the moment it lands, and a wrong one has to be retracted in front of the
reviewer. So the default is to draft: callers print what they would have sent and
report failure, and nothing leaves the machine until the entrypoint opts in.

One flag owns this for the whole process. Modules that write externally
(`pr_comments`, `review_issue`) ask here rather than carrying their own switch.

A hold overrides it. Some things a run learns mid-way — an unanswered question
about whether the work should exist at all — mean nothing more should leave the
machine, whatever the entrypoint was told. `hold` closes the gate for good, so
the two only ever compose in the safe direction.
"""

from __future__ import annotations

import log

_enabled = False
_held = ""


def enable() -> None:
    """Let external writes through for the rest of the process."""
    global _enabled
    _enabled = True


def hold(reason: str) -> None:
    """Close the gate for the rest of the process, whatever `--post` asked for.

    Monotonic: the first reason sticks and nothing reopens the gate. What
    justifies a hold is a question no later stage of the same run can answer,
    so a run that reopened its own gate would be answering it itself.
    """
    global _held
    if _held:
        return
    _held = reason
    log.info(f"Publishing held — {reason}. Nothing further leaves the machine.")


def held() -> str:
    """Why the gate is being held shut, or empty if it is not."""
    return _held


def enabled() -> bool:
    """Whether writes reach the outside world.

    Callers use this to keep their logging honest: a draft is not a failure,
    so error paths must not fire when the gate is closed.
    """
    return _enabled and not _held


def draft(action: str, body: str = "") -> None:
    """Record what would have been written, to stderr."""
    log.info(f"DRAFT (not published) — {action}")
    for line in body.splitlines():
        log.dim(line)
