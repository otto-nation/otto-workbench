"""How a command line splits into statements the way bash reads it.

Heredoc bodies are dropped, quoting is honoured, and the operators ``;``,
``&``, ``|``, ``(``, ``)`` and a newline end a statement. ``segments`` keeps
which statements pipe into the next one; ``split_statements`` is the same
split as plain words. Imports nothing from ``gh``: the shape classifier and
the content scanner both build on it.

Only ``<<-`` lets a heredoc terminator be indented. Accepting indentation for
a plain ``<<`` would end the body early on a line that happens to be the
marker word, and scan the rest of the body as commands. A here-string
(``<<<word``) opens no heredoc.
"""

# doc-group: platform

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

_OPERATOR_CHARS = ";&|()\n"
_PIPES = frozenset({"|", "|&"})
_HEREDOC_OPEN = re.compile(r"(?<!<)<<(-?)\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)")


@dataclass(frozen=True)
class Segment:
    """One statement's words, and whether it pipes its output into the next."""

    words: tuple[str, ...]
    pipes_out: bool = False


def _heredoc_terminator(dash: str, marker: str) -> re.Pattern[str]:
    indentable = r"\s*" if dash else ""
    return re.compile(rf"^{indentable}{re.escape(marker)}\s*$")


def _after_heredoc_line(line: str, terminator: re.Pattern[str]) -> re.Pattern[str] | None:
    """None once *line* is the terminator; otherwise keep skipping."""
    if terminator.match(line):
        return None
    return terminator


def _drop_heredoc_bodies(command: str) -> str:
    """Drop heredoc bodies the same way the agent guards do."""
    kept: list[str] = []
    terminator: re.Pattern[str] | None = None
    for line in command.split("\n"):
        if terminator is not None:
            terminator = _after_heredoc_line(line, terminator)
            continue
        kept.append(line)
        opened = _HEREDOC_OPEN.search(line)
        if opened is not None:
            terminator = _heredoc_terminator(opened.group(1), opened.group(2))
    return "\n".join(kept)


def _flush(segments: list[Segment], words: list[str], separator: str) -> None:
    if words:
        segments.append(Segment(tuple(words), separator in _PIPES))


def segments(command: str) -> list[Segment]:
    """*command* as statements, each recording whether it pipes into the next.

    POSIX quoting applies, so an operator inside quotes is an argument. Heredoc
    bodies are dropped first — they are content, not commands, and an
    unbalanced quote in one must not make the shape unknown. Raises
    ``ValueError`` on an unbalanced quote — a command whose shape is unknown.
    """
    lexer = shlex.shlex(_drop_heredoc_bodies(command), posix=True, punctuation_chars=_OPERATOR_CHARS)
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    lexer.commenters = ""
    found: list[Segment] = []
    words: list[str] = []
    for token in lexer:
        if token and set(token) <= set(_OPERATOR_CHARS):
            _flush(found, words, token)
            words = []
            continue
        words.append(token)
    _flush(found, words, "")
    return found


def split_statements(command: str) -> list[list[str]]:
    """*command* as statements of words, split on shell operators and newlines.

    Raises ``ValueError`` on an unbalanced quote, as ``segments`` does.
    """
    return [list(segment.words) for segment in segments(command)]
