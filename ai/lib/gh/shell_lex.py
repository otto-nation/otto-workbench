"""How a command line splits into statements the way bash reads it.

Heredoc bodies are dropped, quoting is honoured, and the operators ``;``,
``&``, ``|``, ``(``, ``)`` and a newline end a statement. An ``&`` or ``|``
that belongs to a redirect operator (``2>&1``, ``&>x``, ``>|``) stays part of
its word, and a substitution stays whole in its statement while its body
follows as statements of its own. ``segments`` keeps which statements pipe into the next one;
``split_statements`` is the same split as plain words. Imports nothing from
``gh``: the shape classifier and the content scanner both build on it.

Only ``<<-`` lets a heredoc terminator be indented. Accepting indentation for
a plain ``<<`` would end the body early on a line that happens to be the
marker word, and scan the rest of the body as commands. A here-string
(``<<<word``) opens no heredoc.
"""

# doc-group: platform

from __future__ import annotations

import re
import shlex
from collections.abc import Sequence
from dataclasses import dataclass

_OPERATOR_CHARS = ";&|()\n"
_PIPES = frozenset({"|", "|&"})
_HEREDOC_OPEN = re.compile(r"(?<!<)<<(-?)\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)")
# Private-use stand-ins the masking pass writes so shlex keeps a redirect
# operator or a substitution whole; `_unmask` restores them after lexing.
_AMP, _BAR, _OPEN, _CLOSE = "\ue000", "\ue001", "\ue002", "\ue003"


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


def _redirect_char(command: str, i: int) -> str | None:
    """The stand-in for an unquoted ``&``/``|`` at *i* that belongs to a redirect operator."""
    before = command[i - 1] if i else ""
    after = command[i + 1] if i + 1 < len(command) else ""
    if command[i] == "&" and (before in ("<", ">") or after == ">"):
        return _AMP
    if command[i] == "|" and before == ">":
        return _BAR
    return None


def _substitution_end(command: str, start: int) -> int:
    """Index just past the ``)`` closing the substitution whose body starts at *start*."""
    depth, quote, i = 1, "", start
    while i < len(command):
        ch = command[i]
        if quote:
            i += 2 if ch == "\\" and quote == '"' else 1
            quote = "" if ch == quote else quote
            continue
        if ch == "\\":
            i += 2
            continue
        quote = ch if ch in "'\"" else ""
        depth += (ch == "(") - (ch == ")")
        i += 1
        if depth == 0:
            return i
    return len(command)


def _span_end(command: str, i: int, quote: str) -> int | None:
    """Where the substitution opening at *i* ends, or None when none opens there.

    A backtick and ``$(`` open one unquoted or inside double quotes; ``<(`` and
    ``>(`` only unquoted. The caller never asks inside single quotes.
    """
    pair = command[i:i + 2]
    if command[i] == "`":
        end = command.find("`", i + 1)
        return len(command) if end == -1 else end + 1
    if pair == "$(" or (not quote and pair in ("<(", ">(")):
        return _substitution_end(command, i + 2)
    return None


@dataclass(frozen=True)
class _Masked:
    """*text* with redirect operators and substitutions made opaque to shlex."""

    text: str
    spans: tuple[str, ...]


def _mask(command: str) -> _Masked:
    """*command* with redirect-operator ``&``/``|`` and every substitution made opaque to shlex.

    Each substitution becomes one placeholder naming its index in ``spans``,
    so the word that holds it keeps its place and its flags stay in the
    statement. Single quotes shield everything; a backslash escapes one char.
    """
    out: list[str] = []
    spans: list[str] = []
    quote, i = "", 0
    while i < len(command):
        ch = command[i]
        if ch == "\\" and quote != "'":
            out.append(command[i:i + 2])
            i += 2
            continue
        end = None if quote == "'" else _span_end(command, i, quote)
        if end is not None:
            spans.append(command[i:end])
            out.append(f"{_OPEN}{len(spans) - 1}{_CLOSE}")
            i = end
            continue
        if ch in "'\"" and quote in ("", ch):
            quote = "" if quote else ch
        stand_in = _redirect_char(command, i) if ch in "&|" and not quote else None
        out.append(stand_in or ch)
        i += 1
    return _Masked("".join(out), tuple(spans))


_SPAN_RE = re.compile(f"{_OPEN}(\\d+){_CLOSE}")


def _unmask(token: str, spans: Sequence[str]) -> str:
    token = token.replace(_AMP, "&").replace(_BAR, "|")
    return _SPAN_RE.sub(lambda found: spans[int(found.group(1))], token)


def _span_body(span: str) -> str:
    """The command inside a substitution's delimiters."""
    if span.startswith("`"):
        return span[1:-1] if len(span) > 1 and span.endswith("`") else span[1:]
    return span[2:-1] if span.endswith(")") else span[2:]


def _flush(segments: list[Segment], words: list[str], separator: str) -> None:
    if words:
        segments.append(Segment(tuple(words), separator in _PIPES))


def segments(command: str) -> list[Segment]:
    """*command* as statements, each recording whether it pipes into the next.

    A substitution (``$(…)``, ``<(…)``, ``>(…)``, backticks) stays inside the
    word that holds it, and its body is split the same way and follows the
    outer statements. POSIX quoting applies, so an operator inside quotes is an
    argument. Heredoc
    bodies are dropped first — they are content, not commands, and an
    unbalanced quote in one must not make the shape unknown. Raises
    ``ValueError`` on an unbalanced quote — a command whose shape is unknown.
    """
    masked = _mask(_drop_heredoc_bodies(command))
    lexer = shlex.shlex(masked.text, posix=True, punctuation_chars=_OPERATOR_CHARS)
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
        words.append(_unmask(token, masked.spans))
    _flush(found, words, "")
    return found + [nested for span in masked.spans for nested in segments(_span_body(span))]


def split_statements(command: str) -> list[list[str]]:
    """*command* as statements of words, split on shell operators and newlines.

    Raises ``ValueError`` on an unbalanced quote, as ``segments`` does.
    """
    return [list(segment.words) for segment in segments(command)]
