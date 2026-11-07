"""The published text of a GitHub write, including files the command names.

``check_content`` needs one string: the command line (or argv) plus the
contents of body files a write statement points at. This module finds those
files from already-parsed write statements — ``gh.write_shapes`` — rather
than parsing the shell again. A file is read only when the path exists as a
regular file no larger than ``MAX_SCAN_BYTES``; missing, unreadable, stdin
(``-``), and oversized files are skipped. Arguments are never mutated.

The whole command text is scanned, including non-write statements — a
deliberate over-refusal, so a reference sitting next to a write cannot slip
through unread. Every statement also contributes ``cat`` operands and ``<``
input redirects; a substitution's body is a statement of its own, so
``$(< file)`` and ``$(cat file)`` are read the same way.
Redirections are read with one grammar, ``gh.shell_wrappers.REDIRECT``: an
fd-prefixed heredoc or here-string names no file, ``--body-file <(cmd)`` names
none either, and ``<`` inputs are read from the raw statement so a redirect
before the program (``< notes.md gh …``) is scanned too.

A write whose body is read from stdin (``--body-file -``) is only readable
when the producer's text is in the command: ``cat``, ``echo`` and ``printf``
are judged by their words, ``jq -n`` by the ``--rawfile``/``--slurpfile``
files it names. ``generated_stdin_body`` names any other producer.
"""

# doc-group: platform

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import gh.shell_lex
import gh.shell_wrappers
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

# `--body-file <(cmd)` reaches here as one word: the tokenizer keeps the
# substitution whole and makes `cmd` its own statement, whose operands are
# scanned there.
_PROCESS_SUBSTITUTION = ("<(", ">(")

_PLAIN_PRODUCERS = frozenset({"cat", "echo", "printf"})
_JQ_FILE_FLAGS = frozenset({"--rawfile", "--slurpfile"})
_NESTING_DEPTH = gh.write_shapes.REPARSE_DEPTH


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
        chunks.extend(_file_texts_from(_shell_file_paths(statement, words), cwd))
    return "\n".join(chunks)


def generated_stdin_body(command: str, depth: int = _NESTING_DEPTH) -> str:
    """The producer of a write's stdin body whose text the command does not hold, else empty.

    A statement that pipes into a GitHub write reading ``-`` qualifies unless
    the producer is ``cat``/``echo``/``printf`` or ``jq -n`` (whose named files
    are scanned). Text that ``bash -c``, ``eval`` or ``env -S`` re-parses is
    searched *depth* levels deep.
    """
    try:
        segments = gh.shell_lex.segments(command)
    except ValueError:
        return ""
    for first, second in zip(segments, segments[1:]):
        found = _unreadable_producer(first, second)
        if found:
            return found
    return _generator_in_reparsed(segments, depth)


def _unreadable_producer(first: gh.shell_lex.Segment, second: gh.shell_lex.Segment) -> str:
    """*first*'s program when it pipes a body into *second* without its text being in the command."""
    if not (first.pipes_out and _reads_stdin_body(second.words)):
        return ""
    producer = _producer(first.words)
    return "" if _plain_producer(producer, first.words) else producer


def _generator_in_reparsed(segments: Sequence[gh.shell_lex.Segment], depth: int) -> str:
    if depth <= 0:
        return ""
    for segment in segments:
        text = gh.write_shapes.reparsed_text(list(segment.words))
        found = generated_stdin_body(text, depth - 1) if text else ""
        if found:
            return found
    return ""


def _reads_stdin_body(words: Sequence[str]) -> bool:
    for statement in gh.write_shapes.expand_statement(list(words)):
        if gh.write_shapes.statement_is_write(statement) \
                and "-" in _named_files(_write_words(statement)):
            return True
    return False


def _producer(words: Sequence[str]) -> str:
    """The program a pipeline segment runs, as written; empty when there is none."""
    peeled = gh.write_shapes.peeled_statement(words)
    return peeled.words[0] if peeled.words else ""


def _plain_producer(producer: str, words: Sequence[str]) -> bool:
    if not producer:
        return True
    name = Path(producer).name
    if name in _PLAIN_PRODUCERS:
        return True
    return name == "jq" and _jq_is_null_input(_write_words(words))


def _jq_is_null_input(words: Sequence[str]) -> bool:
    return any(
        word == "--null-input" or (word.startswith("-") and not word.startswith("--") and "n" in word[1:])
        for word in words[1:]
    )


def _jq_files(words: Sequence[str]) -> tuple[str, ...]:
    """The FILE of every ``--rawfile NAME FILE`` / ``--slurpfile NAME FILE`` in *words* (peeled ``jq -n``)."""
    if not words or Path(words[0]).name != "jq" or not _jq_is_null_input(words):
        return ()
    return tuple(
        words[i + 2] for i, word in enumerate(words[:-2]) if word in _JQ_FILE_FLAGS)


def relative_body_paths(command: str) -> tuple[str, ...]:
    """Body-file paths in *command* that depend on the shell's directory: relative, or spelled through ``$PWD``/``$OLDPWD``."""
    try:
        statements = gh.write_shapes.statements(command)
    except ValueError:
        return ()
    paths: list[str] = []
    for statement in statements:
        words = _write_words(statement)
        if gh.write_shapes.statement_is_write(statement):
            paths.extend(_relative_named(words))
        paths.extend(path for path in _shell_file_paths(statement, words) if _is_relative(path))
    return tuple(paths)


def _expand_path(path: str) -> str:
    return os.path.expandvars(os.path.expanduser(path))


# `$PWD` and `$OLDPWD` are the shell's directory when the command runs, which a
# `cd` earlier in it moves; expanded here they would name the guard's instead.
_CWD_VAR_RE = re.compile(r"\$\{?(?:OLD)?PWD(?![A-Za-z0-9_])")


def _is_relative(path: str) -> bool:
    if _CWD_VAR_RE.search(path):
        return True
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
        if taken.path and not taken.path.startswith(_PROCESS_SUBSTITUTION):
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


def _shell_file_paths(statement: Sequence[str], words: Sequence[str]) -> tuple[str, ...]:
    """Paths ``cat``/``jq -n`` (in the peeled *words*) and ``<`` redirects (anywhere in *statement*) name."""
    return (*_cat_operands(words), *_jq_files(words), *_redirected(statement).inputs)


@dataclass(frozen=True)
class _Redirected:
    """A statement's words split into plain operands and ``<`` input paths."""

    operands: tuple[str, ...]
    inputs: tuple[str, ...]


def _redirected(words: Sequence[str]) -> _Redirected:
    """*words* with every redirection and its operand taken out; ``<`` operands kept as inputs."""
    operands: list[str] = []
    inputs: list[str] = []
    i = 0
    while i < len(words):
        found = gh.shell_wrappers.REDIRECT.fullmatch(words[i])
        i += 1
        if found is None:
            operands.append(words[i - 1])
            continue
        target = found.group(2)
        if not target and i < len(words):
            target = words[i]
            i += 1
        if target and found.group(1) == "<":
            inputs.append(target)
    return _Redirected(tuple(operands), tuple(inputs))


def _cat_operands(words: Sequence[str]) -> list[str]:
    if not words or Path(words[0]).name != "cat":
        return []
    return [word for word in _redirected(words[1:]).operands if not word.startswith("-")]


def _read_if_scannable(path: str, cwd: str | Path) -> str:
    if path == "-":
        # ceiling: a body piped from cat/echo/printf is judged by their words,
        # so `echo "$BODY" | gh … --body-file -` passes with $BODY's text unread.
        # Upgrade trigger: if a cross-group reference reaches GitHub through a
        # variable echoed into stdin.
        return ""
    # ceiling: a body path spelled through a variable the command assigns
    # (`F=$(mktemp); … --body-file "$F"`) is expanded in the guard's
    # environment, where it is unset or holds something else, so the file is
    # not read. A heredoc that fills it is still scanned as command text.
    # Refusing such paths would break the mktemp-and-heredoc pattern: 6.8% of
    # measured agent write commands use one, and ~5% of those resolve
    # statically. Upgrade trigger: if a cross-group reference reaches GitHub
    # through a variable body path.
    path = _expand_path(path)
    candidate = Path(path) if Path(path).is_absolute() else Path(cwd) / path
    try:
        if not candidate.is_file():
            return ""
        with candidate.open("rb") as handle:
            data = handle.read(MAX_SCAN_BYTES + 1)
    except OSError:
        return ""
    if len(data) > MAX_SCAN_BYTES:
        return ""
    return data.decode("utf-8", errors="replace")
