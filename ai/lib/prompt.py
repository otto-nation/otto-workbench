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


def _read(question: str) -> str | None:
    """One line from the user, or ``None`` when there is nobody to ask."""
    try:
        if sys.stdin.isatty():
            return input(question)
    except (EOFError, OSError):
        return None
    try:
        with open("/dev/tty") as tty:
            print(question, end="", flush=True, file=sys.stderr)
            return tty.readline().strip()
    except (EOFError, OSError):
        return None


def interactive() -> bool:
    """Whether there is a terminal to ask a question on.

    Callers use this to choose between prompting and reporting, so the
    report can name the config key instead of asking a question nobody
    will see.
    """
    try:
        if sys.stdin.isatty():
            return True
    except (EOFError, OSError):
        return False
    try:
        with open("/dev/tty"):
            return True
    except (EOFError, OSError):
        return False


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
