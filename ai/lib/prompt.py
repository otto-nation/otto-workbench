"""Terminal questions, for the few commands that have one to ask.

Separate from ``log`` because that module owns output and this one owns
input. Both readers fall back to ``/dev/tty``: these run inside commands
whose stdin is often a pipe, and a piped stdin does not mean there is
nobody at the keyboard.

Every function has a no-answer value — ``False`` and ``""`` — so a caller
in a hook, a CI job, or a subprocess gets a usable result instead of an
exception. No answer is never consent.
"""

from __future__ import annotations

import sys
from enum import StrEnum

_DEV_TTY = "/dev/tty"


class _Terminal(StrEnum):
    """Where a question can be put, once one has been found."""

    STDIN = "stdin"
    DEV_TTY = "dev_tty"


def _find_terminal() -> _Terminal | None:
    """The terminal to ask on, or ``None`` when there is nobody to ask.

    One probe for both readers: ``interactive`` answers whether asking is
    worth it and ``_read`` does the asking, so a change to what counts as a
    terminal has to reach both or they disagree about the same session.
    """
    try:
        if sys.stdin.isatty():
            return _Terminal.STDIN
    except (EOFError, OSError):
        return None
    try:
        with open(_DEV_TTY):
            return _Terminal.DEV_TTY
    except (EOFError, OSError):
        return None


def _read(question: str) -> str | None:
    """One line from the user, or ``None`` when there is nobody to ask."""
    terminal = _find_terminal()
    if terminal is None:
        return None
    if terminal is _Terminal.STDIN:
        try:
            return input(question)
        except (EOFError, OSError):
            return None
    try:
        with open(_DEV_TTY) as tty:
            print(question, end="", flush=True, file=sys.stderr)
            line = tty.readline()
    except (EOFError, OSError):
        return None
    # readline() returns "" only at end of input and "\n" for a bare Enter, so
    # the test precedes the strip that would make the two indistinguishable.
    # Ctrl-D here means what it means at an input() prompt: no answer.
    if not line:
        return None
    return line.strip()


def interactive() -> bool:
    """Whether there is a terminal to ask a question on.

    Callers use this to choose between prompting and reporting, so the
    report can name the config key instead of asking a question nobody
    will see.
    """
    return _find_terminal() is not None


def confirm(question: str) -> bool:
    """A yes/no question defaulting to yes. No answer is no."""
    answer = _read(f"{question} [Y/n] ")
    if answer is None:
        return False
    return answer.lower() in ("", "y", "yes")


def ask(question: str) -> str:
    """A free-text question. No answer is the empty string."""
    answer = _read(question)
    return "" if answer is None else answer.strip()
