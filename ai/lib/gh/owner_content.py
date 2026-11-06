"""The published text of a GitHub write, including files the command names.

``check_content`` needs one string: the command line (or argv) plus the
contents of body files a write statement points at. This module finds those
files from already-parsed write statements — ``gh.write_shapes`` — rather
than parsing the shell again. A file is read only when the path exists as a
regular file no larger than ``MAX_SCAN_BYTES``; missing, unreadable, stdin
(``-``), and oversized files are skipped. Arguments are never mutated.

The whole command text is scanned, including non-write statements — a
deliberate over-refusal, so a reference sitting next to a write cannot slip
through unread. Every statement also contributes ``cat`` operands, ``<``
input redirects, and ``$(< file)`` / ``$(cat file)`` substitutions.
"""

# doc-group: platform

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import gh.write_shapes

# ceiling-permanent: files larger than MAX_SCAN_BYTES are unread. GitHub
# caps issue and comment bodies at 65,536 characters, well under this bound,
# so a body GitHub would accept is always scanned. Reading more would not
# catch a publishable body this misses.
MAX_SCAN_BYTES = 256 * 1024

_FILE_FLAGS = ("--body-file", "--input", "--description-file", "--notes-file")
_FILE_PREFIXES = tuple(f"{flag}=" for flag in _FILE_FLAGS)
_FIELD_WORDS = ("-F", "--field")
_FIELD_PREFIX = "--field="


@dataclass(frozen=True)
class _FlagPath:
    """A body-file path taken from *args[i]* and the index to resume at."""

    path: str
    next_index: int


def command_text(command: str, cwd: str | Path) -> str:
    """*command* plus the contents of body files its statements name."""
    try:
        statements = gh.write_shapes.statements(command)
    except ValueError:
        return command
    chunks = [command]
    for statement in statements:
        words = _write_words(statement)
        if gh.write_shapes.statement_is_write(statement):
            chunks.extend(_file_texts(words, cwd))
        chunks.extend(_file_texts_from(_shell_file_paths(words), cwd))
    return "\n".join(chunks)


def relative_body_paths(command: str) -> tuple[str, ...]:
    """Relative body-file paths statements in *command* name."""
    try:
        statements = gh.write_shapes.statements(command)
    except ValueError:
        return ()
    paths: list[str] = []
    for statement in statements:
        words = _write_words(statement)
        if gh.write_shapes.statement_is_write(statement):
            paths.extend(_relative_named(words))
        paths.extend(path for path in _shell_file_paths(words) if _is_relative(path))
    return tuple(paths)


def _expand_path(path: str) -> str:
    return os.path.expandvars(os.path.expanduser(path))


def _is_relative(path: str) -> bool:
    expanded = _expand_path(path)
    return bool(expanded) and expanded != "-" and not Path(expanded).is_absolute()


def _relative_named(args: Sequence[str]) -> tuple[str, ...]:
    return tuple(path for path in _named_files(args) if _is_relative(path))


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
    return _file_texts_from(_named_files(args), cwd)


def _file_texts_from(paths: Sequence[str], cwd: str | Path) -> list[str]:
    texts: list[str] = []
    for path in paths:
        text = _read_if_scannable(path, cwd)
        if text:
            texts.append(text)
    return texts


def _is_api(args: Sequence[str]) -> bool:
    inv = gh.write_shapes.Invocation(words=tuple(args))
    rest = inv.args if inv.program == "gh" else tuple(args)
    i = gh.write_shapes.skip_repo_flags(rest)
    return i < len(rest) and rest[i] == "api"


def _named_files(args: Sequence[str]) -> tuple[str, ...]:
    """Paths body-file flags in *args* name. Never mutates *args*."""
    paths: list[str] = []
    api = _is_api(args)
    i = 0
    while i < len(args):
        taken = _file_flag_path(args, i, api=api)
        if taken is None:
            i += 1
            continue
        if taken.path:
            paths.append(taken.path)
        i = taken.next_index
    return tuple(paths)


def _file_flag_path(args: Sequence[str], i: int, *, api: bool) -> _FlagPath | None:
    """The body file *args[i]* names, else None."""
    word = args[i]
    if word in _FILE_FLAGS:
        path, nxt = _next_value(args, i)
        return _FlagPath(path, nxt)
    for prefix in _FILE_PREFIXES:
        if word.startswith(prefix):
            return _FlagPath(word[len(prefix):], i + 1)
    if word in _FIELD_WORDS:
        path, nxt = _next_value(args, i)
        if word == "-F" and not api and "=" not in path:
            return _FlagPath(path, nxt)
        return _FlagPath(_field_file(path), nxt)
    if word.startswith(_FIELD_PREFIX):
        return _FlagPath(_field_file(word[len(_FIELD_PREFIX):]), i + 1)
    if word.startswith("-F") and not word.startswith("--") and word != "-F":
        rest = word[2:].removeprefix("=")
        if not api and "=" not in rest:
            return _FlagPath(rest, i + 1)
        return _FlagPath(_field_file(rest), i + 1)
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


def _shell_file_paths(words: Sequence[str]) -> tuple[str, ...]:
    """Paths ``cat``, ``<`` redirects, and ``$(< f)`` / ``$(cat f)`` name."""
    paths: list[str] = []
    paths.extend(_cat_operands(words))
    paths.extend(_redirect_operands(words))
    paths.extend(_subst_operands(words))
    return tuple(paths)


def _cat_operands(words: Sequence[str]) -> list[str]:
    if not words or Path(words[0]).name != "cat":
        return []
    return _cat_input_paths(words[1:])


def _output_redirect_consumes_next(word: str) -> bool:
    if word in (">", ">>", "&>", ">&"):
        return True
    if word.endswith(">>"):
        return word[:-2].isdigit() and bool(word[:-2])
    if word.endswith(">"):
        return word[:-1].isdigit() and bool(word[:-1])
    return False


def _is_shell_redirection(word: str) -> bool:
    if word in ("<", "0<"):
        return True
    if word.startswith(("<<", "<(", ">(", ">", "&>")):
        return True
    if ">" not in word:
        return False
    left, _, _ = word.partition(">")
    return left.isdigit()


def _cat_input_paths(operands: Sequence[str]) -> list[str]:
    paths: list[str] = []
    skip_next = False
    for word in operands:
        if skip_next:
            skip_next = False
            continue
        if _output_redirect_consumes_next(word):
            skip_next = True
            continue
        if _is_shell_redirection(word) or word == "-" or word.startswith("-"):
            continue
        paths.append(word)
    return paths


def _redirect_operands(words: Sequence[str]) -> list[str]:
    paths: list[str] = []
    for i, word in enumerate(words):
        if word in ("<", "0<") and i + 1 < len(words):
            paths.append(words[i + 1])
        elif word.startswith("<") and not word.startswith("<<") and len(word) > 1:
            paths.append(word[1:])
    return paths


def _subst_operands(words: Sequence[str]) -> list[str]:
    paths: list[str] = []
    for word in words:
        inner = _subst_inner(word)
        if inner is None:
            continue
        if inner.startswith("<"):
            paths.append(inner[1:].strip())
            continue
        parts = inner.split()
        paths.extend(_cat_operands(parts))
    return paths


def _subst_inner(word: str) -> str | None:
    if word.startswith("$(") and word.endswith(")") and len(word) > 3:
        return word[2:-1].strip()
    return None


def _read_if_scannable(path: str, cwd: str | Path) -> str:
    if path == "-":
        # ceiling: stdin (--body-file -) is unread here. A pipe from `cat file`
        # is covered by scanning cat's operand; a pipe from a generator is not.
        # Upgrade trigger: if generated bodies start reaching gh through stdin
        # instead of a path.
        return ""
    path = _expand_path(path)
    candidate = Path(path) if Path(path).is_absolute() else Path(cwd) / path
    try:
        if not candidate.is_file() or candidate.stat().st_size > MAX_SCAN_BYTES:
            return ""
        return candidate.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
