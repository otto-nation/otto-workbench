"""Transparent command wrappers — ``env``, ``sudo``, ``time``, ``command``, ``exec`` — peeled off.

``sudo env GH_REPO=x time gh issue comment …`` runs ``gh issue comment`` with
``GH_REPO=x``. ``peel`` strips wrappers in a loop until the head word is not
one (matched on its basename, so ``/usr/bin/time`` is ``time``), lifting each
wrapper's ``NAME=value`` assignments and ``env -u`` removals on the way. The
write-shape predicate and the target resolver both call it, so the two cannot
disagree about which program a statement runs.

Option parsing is confident or it is nothing. Each wrapper has an explicit
table of its options: which take no value, which take one (``-u bob``,
``-ubob``, ``--user bob``, ``--user=bob``), and which take one only when it is
attached (``xargs -e[EOF]``). A short-option cluster is read character by
character: a value-taking character takes the rest of the cluster as its value,
or the next word when it is last (``sudo -iu bob``). Any option outside the
table — an unknown flag, a GNU long-option abbreviation — makes the statement
**opaque**: which word is the program cannot be known, and the caller falls
back to searching the words. So does an option that changes directory or
re-parses a string — ``env -C/--chdir``, ``env -S/--split-string``,
``sudo -D/--chdir``, ``sudo -R/--chroot`` — since the target of a write it runs
cannot be read from the text even when the program can. ``sudo -e`` (edit a
file) and ``sudo -h`` (a host, or help) are opaque too: neither runs the words
that follow as a command.

``command -v``/``-V`` looks a name up and runs nothing; ``Peeled.lookup`` says
so.

``XARGS`` is the same kind of table for ``xargs``. It is not peeled: its
arguments come from stdin, so a write under it has no target to resolve, but
which program it runs is still worth knowing — ``xargs grep 'pr create'`` is a
read.
"""

# doc-group: platform

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\+?=")


@dataclass(frozen=True)
class OptionSpec:
    """One program's options; anything not listed is unknown, so the statement is opaque."""

    flags: str = ""
    values: str = ""
    attached: str = ""
    long_flags: frozenset[str] = frozenset()
    long_values: frozenset[str] = frozenset()
    long_attached: frozenset[str] = frozenset()
    opaque: frozenset[str] = frozenset()
    lookup: frozenset[str] = frozenset()
    unsets: frozenset[str] = frozenset()
    dash_is_flag: bool = False


@dataclass(frozen=True)
class Options:
    """The options read off the front of an argv, each with its value, and the words after them."""

    found: tuple[tuple[str, str], ...]
    rest: tuple[str, ...]

    def names(self) -> frozenset[str]:
        return frozenset(name for name, _ in self.found)


@dataclass(frozen=True)
class Peeled:
    """What a statement runs once its wrappers are stripped.

    *opaque* means a wrapper option could not be read with confidence, or
    changes directory or re-parses a string; *words* is then the wrapper
    onward and is not to be classified. *lookup* means the statement runs
    nothing (``command -v``).
    """

    env: Mapping[str, str] = field(default_factory=dict)
    words: tuple[str, ...] = ()
    unset: frozenset[str] = frozenset()
    opaque: bool = False
    lookup: bool = False


ENV = OptionSpec(
    flags="i0v", values="uCSP",
    long_flags=frozenset({
        "--ignore-environment", "--null", "--debug", "--list-signal-handling", "--help", "--version"}),
    long_values=frozenset({"--unset", "--chdir", "--split-string"}),
    long_attached=frozenset({"--block-signal", "--default-signal", "--ignore-signal"}),
    opaque=frozenset({"-C", "--chdir", "-S", "--split-string"}),
    unsets=frozenset({"-u", "--unset"}),
    dash_is_flag=True,
)
SUDO = OptionSpec(
    flags="AbBEHiKklnNPSsVve", values="ugCpUrtTDRh",
    long_flags=frozenset({
        "--askpass", "--background", "--bell", "--edit", "--set-home", "--login",
        "--remove-timestamp", "--reset-timestamp", "--list", "--non-interactive",
        "--preserve-groups", "--stdin", "--shell", "--version", "--validate", "--help"}),
    long_values=frozenset({
        "--user", "--group", "--close-from", "--chdir", "--chroot", "--prompt",
        "--other-user", "--role", "--type", "--command-timeout", "--host"}),
    long_attached=frozenset({"--preserve-env"}),
    opaque=frozenset({"-D", "--chdir", "-R", "--chroot", "-h", "--host", "-e", "--edit"}),
)
TIME = OptionSpec(
    flags="pvqaVlh", values="fo",
    long_flags=frozenset({"--portability", "--verbose", "--quiet", "--append", "--version", "--help"}),
    long_values=frozenset({"--format", "--output"}),
)
COMMAND = OptionSpec(flags="pvV", lookup=frozenset({"-v", "-V"}))
EXEC = OptionSpec(flags="cl", values="a")
XARGS = OptionSpec(
    flags="0ortpx", values="adEILnPs", attached="eil",
    long_flags=frozenset({
        "--null", "--interactive", "--no-run-if-empty", "--exit", "--open-tty",
        "--show-limits", "--verbose", "--version", "--help"}),
    long_values=frozenset({
        "--arg-file", "--delimiter", "--max-args", "--max-procs", "--max-chars",
        "--process-slot-var"}),
    long_attached=frozenset({"--eof", "--replace", "--max-lines"}),
)
WRAPPERS: Mapping[str, OptionSpec] = {
    "env": ENV, "sudo": SUDO, "time": TIME, "command": COMMAND, "exec": EXEC,
}


def basename(word: str) -> str:
    """The program name of an argv head: ``/usr/bin/env`` is ``env``."""
    return word.rsplit("/", 1)[-1]


def lift_assignments(words: Sequence[str], env: dict[str, str]) -> int:
    """Move leading ``NAME=value``/``NAME+=value`` words into *env*; return the first other index."""
    i = 0
    while i < len(words) and ASSIGNMENT.match(words[i]):
        name, _, value = words[i].partition("=")
        env[name.removesuffix("+")] = value
        i += 1
    return i


def _long(spec: OptionSpec, args: Sequence[str], i: int, found: list) -> int | None:
    name, eq, value = args[i].partition("=")
    if name in spec.long_values:
        if eq:
            found.append((name, value))
            return i + 1
        if i + 1 >= len(args):
            return None
        found.append((name, args[i + 1]))
        return i + 2
    if name in spec.long_attached or (name in spec.long_flags and not eq):
        found.append((name, value))
        return i + 1
    return None


def _short_value(spec: OptionSpec, char: str, rest: str, args: Sequence[str], i: int,
                 found: list) -> int | None:
    if rest or char in spec.attached:
        found.append((f"-{char}", rest))
        return i + 1
    if i + 1 >= len(args):
        return None
    found.append((f"-{char}", args[i + 1]))
    return i + 2


def _cluster(spec: OptionSpec, args: Sequence[str], i: int, found: list) -> int | None:
    word = args[i]
    for at in range(1, len(word)):
        char = word[at]
        if char in spec.values or char in spec.attached:
            return _short_value(spec, char, word[at + 1:], args, i, found)
        if char not in spec.flags:
            return None
        found.append((f"-{char}", ""))
    return i + 1


def _option(spec: OptionSpec, args: Sequence[str], i: int, found: list) -> int | None:
    if args[i] == "-":
        if not spec.dash_is_flag:
            return None
        found.append(("-", ""))
        return i + 1
    if args[i].startswith("--"):
        return _long(spec, args, i, found)
    return _cluster(spec, args, i, found)


def parse_options(spec: OptionSpec, args: Sequence[str]) -> Options | None:
    """*args*'s leading options per *spec*, or None when any is not in it."""
    found: list[tuple[str, str]] = []
    i = 0
    while i < len(args) and args[i].startswith("-"):
        if args[i] == "--":
            return Options(tuple(found), tuple(args[i + 1:]))
        step = _option(spec, args, i, found)
        if step is None:
            return None
        i = step
    return Options(tuple(found), tuple(args[i:]))


def _apply_unsets(spec: OptionSpec, options: Options, env: dict[str, str], unset: set[str]) -> None:
    for name, value in options.found:
        if name in spec.unsets:
            env.pop(value, None)
            unset.add(value)


def peel(env: Mapping[str, str], words: Sequence[str]) -> Peeled:
    """*words* with every leading transparent wrapper stripped, and the env it runs under."""
    current = dict(env)
    unset: set[str] = set()
    words = tuple(words)
    while words and basename(words[0]) in WRAPPERS:
        spec = WRAPPERS[basename(words[0])]
        options = parse_options(spec, words[1:])
        if options is None or options.names() & spec.opaque:
            return Peeled(current, words, frozenset(unset), opaque=True)
        if options.names() & spec.lookup:
            return Peeled(current, words, frozenset(unset), lookup=True)
        _apply_unsets(spec, options, current, unset)
        start = lift_assignments(options.rest, current)
        unset -= set(current)
        words = options.rest[start:]
    return Peeled(current, words, frozenset(unset))
