"""Transparent command wrappers — ``env``, ``sudo``, ``time``, ``timeout`` and the rest — peeled off.

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

The wrappers are ``env``, ``sudo``, ``doas``, ``time``, ``command``, ``exec``,
``timeout``, ``nice``, ``nohup``, ``stdbuf``, ``ionice``, ``chrt`` and
``taskset``. ``timeout DURATION``, ``chrt PRIORITY`` and ``taskset MASK`` take
one positional before the program; ``nice -5`` is nice's legacy numeric
option.

``command -v``/``-V`` looks a name up and runs nothing; ``Peeled.lookup`` says
so, as it does for the forms of ``ionice``, ``chrt``, ``taskset`` and ``doas``
that act on a pid or a config file instead of running a command. ``doas -s``
runs a shell and is opaque.

Which inherited variables reach the program is tracked too, because the
target resolver reads ``GH_REPO``/``GH_HOST`` from the process environment.
``env -i``/``env -`` clears it for certain: the program sees only what is
assigned after the clear. ``sudo`` (without ``-E``/``--preserve-env``) and
``doas`` reset it by default, but sudoers ``env_keep`` or doas ``keepenv`` may
pass a variable through, so whether they cleared it is not knowable from the
text. ``Peeled.views`` then holds both cases, and the resolver unions them.

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
    positionals: int = 0
    numeric: bool = False
    clears: frozenset[str] = frozenset()
    resets: bool = False
    preserves: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Options:
    """The options read off the front of an argv, each with its value, and the words after them."""

    found: tuple[tuple[str, str], ...]
    rest: tuple[str, ...]

    def names(self) -> frozenset[str]:
        return frozenset(name for name, _ in self.found)


@dataclass(frozen=True)
class EnvView:
    """One case of the environment a program runs under.

    *env* is what the statement assigned; *inherits* is which names of the
    process environment still reach the program — None for all of them.
    """

    env: Mapping[str, str] = field(default_factory=dict)
    inherits: frozenset[str] | None = None

    def admits(self, name: str) -> bool:
        return self.inherits is None or name in self.inherits


@dataclass(frozen=True)
class Peeled:
    """What a statement runs once its wrappers are stripped.

    *opaque* means a wrapper option could not be read with confidence, or
    changes directory or re-parses a string; *words* is then the wrapper
    onward and is not to be classified. *lookup* means the statement runs
    nothing (``command -v``). *env* and *inherits* are the environment when
    no wrapper reset it; *alternatives* are the cases where a wrapper that
    may reset it (``sudo``, ``doas``) did.
    """

    env: Mapping[str, str] = field(default_factory=dict)
    words: tuple[str, ...] = ()
    unset: frozenset[str] = frozenset()
    opaque: bool = False
    lookup: bool = False
    inherits: frozenset[str] | None = None
    alternatives: tuple[EnvView, ...] = ()

    @property
    def views(self) -> tuple[EnvView, ...]:
        """Every case of the environment the program may run under, the unreset one first."""
        return (EnvView(self.env, self.inherits), *self.alternatives)


ENV = OptionSpec(
    flags="i0v", values="uCSP",
    long_flags=frozenset({
        "--ignore-environment", "--null", "--debug", "--list-signal-handling", "--help", "--version"}),
    long_values=frozenset({"--unset", "--chdir", "--split-string"}),
    long_attached=frozenset({"--block-signal", "--default-signal", "--ignore-signal"}),
    opaque=frozenset({"-C", "--chdir", "-S", "--split-string"}),
    unsets=frozenset({"-u", "--unset"}),
    dash_is_flag=True,
    clears=frozenset({"-i", "--ignore-environment", "-"}),
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
    resets=True,
    preserves=frozenset({"-E", "--preserve-env"}),
)
DOAS = OptionSpec(
    flags="Lns", values="uC", opaque=frozenset({"-s"}), lookup=frozenset({"-L", "-C"}), resets=True)
TIME = OptionSpec(
    flags="pvqaVlh", values="fo",
    long_flags=frozenset({"--portability", "--verbose", "--quiet", "--append", "--version", "--help"}),
    long_values=frozenset({"--format", "--output"}),
)
COMMAND = OptionSpec(flags="pvV", lookup=frozenset({"-v", "-V"}))
EXEC = OptionSpec(flags="cl", values="a")
_HELP = frozenset({"--help", "--version"})
TIMEOUT = OptionSpec(
    flags="fpv", values="ks",
    long_flags=_HELP | {"--foreground", "--preserve-status", "--verbose"},
    long_values=frozenset({"--kill-after", "--signal"}),
    positionals=1,
)
NICE = OptionSpec(values="n", long_flags=_HELP, long_values=frozenset({"--adjustment"}), numeric=True)
NOHUP = OptionSpec(long_flags=_HELP)
STDBUF = OptionSpec(values="ioe", long_flags=_HELP, long_values=frozenset({"--input", "--output", "--error"}))
IONICE = OptionSpec(
    flags="t", values="cnpPu",
    long_flags=_HELP | {"--ignore"},
    long_values=frozenset({"--class", "--classdata", "--pid", "--pgid", "--uid"}),
    lookup=frozenset({"-p", "-P", "-u", "--pid", "--pgid", "--uid"}),
)
CHRT = OptionSpec(
    flags="abdfiormRpv", values="TPD",
    long_flags=_HELP | {
        "--all-tasks", "--batch", "--deadline", "--fifo", "--idle", "--other", "--rr",
        "--reset-on-fork", "--max", "--pid", "--verbose"},
    long_values=frozenset({"--sched-runtime", "--sched-period", "--sched-deadline"}),
    lookup=frozenset({"-m", "--max", "-p", "--pid"}),
    positionals=1,
)
TASKSET = OptionSpec(
    flags="acp", long_flags=_HELP | {"--all-tasks", "--cpu-list", "--pid"},
    lookup=frozenset({"-p", "--pid"}), positionals=1,
)
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
    "env": ENV, "sudo": SUDO, "doas": DOAS, "time": TIME, "command": COMMAND, "exec": EXEC,
    "timeout": TIMEOUT, "nice": NICE, "nohup": NOHUP, "stdbuf": STDBUF, "ionice": IONICE,
    "chrt": CHRT, "taskset": TASKSET,
}
_NUMERIC_OPTION = re.compile(r"^--?\d+$")


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
    if spec.numeric and _NUMERIC_OPTION.match(args[i]):
        found.append(("-n", args[i].lstrip("-")))
        return i + 1
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


@dataclass
class _Case:
    """One case of the environment while peeling: assignments and inherited names."""

    env: dict[str, str]
    inherits: frozenset[str] | None = None


def _preserved(spec: OptionSpec, options: Options) -> frozenset[str] | None:
    """The names a resetting wrapper keeps, or None when it keeps the whole environment."""
    values = [value for name, value in options.found if name in spec.preserves]
    if not all(values):
        return None
    return frozenset(part for value in values for part in value.split(",") if part)


def _reset(case: _Case, kept: frozenset[str]) -> _Case:
    inherits = kept if case.inherits is None else case.inherits & kept
    return _Case({k: v for k, v in case.env.items() if k in kept}, inherits)


def _environment(spec: OptionSpec, options: Options, cases: list[_Case]) -> list[_Case]:
    """*cases* after one wrapper's clears and resets."""
    if options.names() & spec.clears:
        return [_Case({}, frozenset()) for _ in cases]
    if not spec.resets:
        return cases
    kept = _preserved(spec, options)
    if kept is None:
        return cases
    return cases + [_reset(case, kept) for case in cases]


def _apply(spec: OptionSpec, options: Options, cases: list[_Case], unset: set[str]) -> int:
    """Apply one wrapper's unsets and assignments to every case; the index after the assignments."""
    rest = options.rest[spec.positionals:]
    removed = [value for name, value in options.found if name in spec.unsets]
    unset.update(removed)
    for case in cases:
        for name in removed:
            case.env.pop(name, None)
    start = 0
    for case in cases:
        start = lift_assignments(rest, case.env)
    unset -= set(cases[0].env)
    return spec.positionals + start


def _peeled(cases: list[_Case], words: tuple[str, ...], unset: set[str], **flags) -> Peeled:
    alternatives = tuple(dict.fromkeys(
        (tuple(sorted(c.env.items())), c.inherits) for c in cases[1:]
        if (c.env, c.inherits) != (cases[0].env, cases[0].inherits)))
    views = tuple(EnvView(dict(env), inherits) for env, inherits in alternatives)
    return Peeled(dict(cases[0].env), words, frozenset(unset), inherits=cases[0].inherits,
                  alternatives=views, **flags)


def peel(env: Mapping[str, str], words: Sequence[str]) -> Peeled:
    """*words* with every leading transparent wrapper stripped, and the env it runs under."""
    cases = [_Case(dict(env))]
    unset: set[str] = set()
    words = tuple(words)
    while words and basename(words[0]) in WRAPPERS:
        spec = WRAPPERS[basename(words[0])]
        options = parse_options(spec, words[1:])
        if options is None or options.names() & spec.opaque:
            return _peeled(cases, words, unset, opaque=True)
        if options.names() & spec.lookup:
            return _peeled(cases, words, unset, lookup=True)
        if len(options.rest) < spec.positionals:
            return _peeled(cases, words, unset, opaque=True)
        cases = _environment(spec, options, cases)
        words = options.rest[_apply(spec, options, cases, unset):]
    return _peeled(cases, words, unset)
