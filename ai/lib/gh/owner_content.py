"""The published text of a GitHub write, including files the command names.

``check_content`` needs one string: the command line (or argv) plus the
contents of body files a write statement points at. This module finds those
files from already-parsed write statements — ``gh.write_shapes`` — rather
than parsing the shell again. A file is read only when the path exists as a
regular file no larger than ``MAX_SCAN_BYTES``; missing, unreadable, stdin
(``-``), and oversized files are skipped. Arguments are never mutated.
"""

# doc-group: platform

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import gh.write_shapes

MAX_SCAN_BYTES = 256 * 1024

_FILE_FLAGS = ("--body-file", "--input", "--description-file")
_FILE_PREFIXES = tuple(f"{flag}=" for flag in _FILE_FLAGS)
_FIELD_WORDS = ("-F", "--field")
_FIELD_PREFIX = "--field="


def command_text(command: str, cwd: str | Path) -> str:
    """*command* plus the contents of body files its write statements name."""
    try:
        statements = gh.write_shapes.statements(command)
    except ValueError:
        return command
    chunks = [command]
    for statement in statements:
        if not gh.write_shapes.statement_is_write(statement):
            continue
        chunks.extend(_file_texts(_write_words(statement), cwd))
    return "\n".join(chunks)


def argv_text(args: Sequence[str], input_text: str | None, cwd: str | Path) -> str:
    """Joined *args*, *input_text* when given, and the same body-file contents."""
    chunks = [" ".join(args)]
    if input_text is not None:
        chunks.append(input_text)
    chunks.extend(_file_texts(args, cwd))
    return "\n".join(chunks)


def _write_words(statement: Sequence[str]) -> Sequence[str]:
    peeled = gh.write_shapes.peeled_statement(statement)
    return peeled.words or statement


def _file_texts(args: Sequence[str], cwd: str | Path) -> list[str]:
    texts: list[str] = []
    for path in _named_files(args):
        text = _read_if_scannable(path, cwd)
        if text:
            texts.append(text)
    return texts


def _named_files(args: Sequence[str]) -> tuple[str, ...]:
    """Paths body-file flags in *args* name. Never mutates *args*."""
    paths: list[str] = []
    i = 0
    while i < len(args):
        taken = _file_flag_path(args, i)
        if taken is None:
            i += 1
            continue
        path, nxt = taken
        if path:
            paths.append(path)
        i = nxt
    return tuple(paths)


def _file_flag_path(args: Sequence[str], i: int) -> tuple[str, int] | None:
    """``(path, next_index)`` when *args[i]* names a body file, else None."""
    word = args[i]
    if word in _FILE_FLAGS:
        return _next_value(args, i)
    for prefix in _FILE_PREFIXES:
        if word.startswith(prefix):
            return word[len(prefix):], i + 1
    if word in _FIELD_WORDS:
        path, nxt = _next_value(args, i)
        return _field_file(path), nxt
    if word.startswith(_FIELD_PREFIX):
        return _field_file(word[len(_FIELD_PREFIX):]), i + 1
    return None


def _next_value(args: Sequence[str], i: int) -> tuple[str, int]:
    if i + 1 >= len(args):
        return "", i + 1
    return args[i + 1], i + 2


def _field_file(value: str) -> str:
    """The path in a ``name=@file`` field value, else empty."""
    _, sep, rest = value.partition("=")
    if not sep or not rest.startswith("@") or len(rest) < 2:
        return ""
    return rest[1:]


def _read_if_scannable(path: str, cwd: str | Path) -> str:
    if path == "-":
        return ""
    candidate = Path(path) if Path(path).is_absolute() else Path(cwd) / path
    try:
        if not candidate.is_file() or candidate.stat().st_size > MAX_SCAN_BYTES:
            return ""
        return candidate.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
