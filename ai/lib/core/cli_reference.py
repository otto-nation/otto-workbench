"""Usage lines and flag tables rendered from a CLI's own argparse parsers.

A flag that is declared in a parser and described somewhere else drifts in one
direction only: the parser gains it, the description does not, and the CLI
ends up advertising less than it accepts. `pr rebase --base` worked for months
while three of the four places that described `pr rebase` named `--onto`
alone, and `pr create` was missing from the usage string agents read. Nothing
checked the one against the other, and a check would only have reported the
gap. This renders the description *from* the parser, so there is no second
copy to fall behind.

Two outputs, from one `CLIShape`:

- `usage_line` — every invocation on one line, joined by `  |  `. It is the
  `usage` a registry entry would otherwise hand-write, and it is what
  `tools.generated.md` and the MCP tool description show.
- `tables` — markdown: the global flags, then one table per command. It is
  what `docs/tools.md` shows under the script's own header.

A shape is a program name, an optional parser of global flags, and its
commands, each with its own parser. `shape_of` builds one from a plain
`ArgumentParser` — its subparsers become the commands and its own options the
globals — so a CLI built the ordinary way needs nothing written for it. A
dispatcher whose commands parse in other modules (`pr`, whose delegates each
own a parser) assembles its `CLIShape` itself.

What is shown is what argparse would accept and `--help` would print: an
option whose help is `SUPPRESS` stays out, as do the framework flags every
`ToolParser` script shares (`--help`, `--tool-schema`, `--debug`). Every
option string is shown, so an alias is documented the moment it is declared.
"""

# doc-group: platform

from __future__ import annotations

import argparse
import importlib
import importlib.machinery
import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path

from core.tool_parser import subparsers

# Private argparse API, used because argparse publishes no way to ask for what
# this renders: `_actions` (a parser's options in declaration order),
# `_choices_actions` (a command's help text), `_mutually_exclusive_groups`, and
# `_get_formatter()._expand_help` (`%(default)s` fields). `_actions` has been
# stable for a decade; the other three have moved between Python releases. The
# cases in tests/cli_reference_test.py that render a command's help, an
# exclusive group and a `%(default)s` help are what fail first on an upgrade
# that changes them.

# The option dests every ToolParser script shares. They belong to the framework,
# not to the command, and listing them under every command would bury the
# flags a reader came for.
_FRAMEWORK_DESTS = frozenset({"help", "tool_schema", "debug"})

# Actions that take no value on the command line.
_NO_VALUE = (
    argparse._StoreTrueAction,
    argparse._StoreFalseAction,
    argparse._StoreConstAction,
    argparse._AppendConstAction,
    argparse._CountAction,
    argparse._HelpAction,
    argparse._VersionAction,
)

# What joins invocations in a one-line usage. Two spaces either side, because
# that is what every hand-written registry `usage` already used.
USAGE_SEPARATOR = "  |  "


@dataclass(frozen=True)
class Command:
    """One subcommand: the name it is invoked as, its one-line help, its parser."""

    name: str
    help: str
    parser: argparse.ArgumentParser


@dataclass(frozen=True)
class CLIShape:
    """Everything a CLI accepts, as parsers.

    `globals` holds the flags accepted before (or, for a two-pass parser like
    `pr`, anywhere around) the command; `None` when there are none. A CLI with
    no subcommands has an empty `commands` and its options in `globals`.
    """

    prog: str
    globals: argparse.ArgumentParser | None = None
    commands: tuple[Command, ...] = field(default_factory=tuple)


def shape_of(parser: argparse.ArgumentParser, prog: str) -> CLIShape:
    """The shape of an ordinary argparse CLI named *prog*.

    Subparsers become commands, in declaration order, with the help their
    `add_parser(..., help=...)` gave; the top-level parser's own options are
    the globals. A parser with no subparsers is a single-command CLI: its
    options are the globals and it has no commands.
    """
    helps = {
        choice.dest: choice.help or ""
        for action in parser._actions if isinstance(action, argparse._SubParsersAction)
        for choice in action._choices_actions
    }
    seen: set[int] = set()
    commands = []
    for name, sub in subparsers(parser).items():
        # Aliases map to the same parser; document each command once, under
        # the name it was declared with.
        if id(sub) in seen:
            continue
        seen.add(id(sub))
        commands.append(Command(name, helps.get(name, ""), sub))
    return CLIShape(prog=prog, globals=parser, commands=tuple(commands))


def _documented(parser: argparse.ArgumentParser,
                hidden: frozenset[str] = frozenset()) -> list[argparse.Action]:
    """The actions of *parser* a reader should see, in declaration order.

    *hidden* is option strings documented elsewhere — a dispatcher's global
    flags, which its delegates redeclare so they can run alone. Under the
    dispatcher they are the globals' flags, and listing them again under every
    command would say each command has its own.
    """
    shown = []
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            continue
        if action.help is argparse.SUPPRESS or action.dest in _FRAMEWORK_DESTS:
            continue
        if action.option_strings and set(action.option_strings) <= hidden:
            continue
        shown.append(action)
    return shown


def _global_strings(shape: CLIShape) -> frozenset[str]:
    """Every option string the shape's global parser documents."""
    if shape.globals is None or not shape.commands:
        return frozenset()
    return frozenset(s for a in _documented(shape.globals) for s in a.option_strings)


def _metavar(action: argparse.Action) -> str:
    """How an action's value is spelled: `<ref>`, `<a|b>`, or a given metavar."""
    if action.choices is not None and action.metavar is None:
        return "<" + "|".join(str(c) for c in action.choices) + ">"
    name = action.metavar if isinstance(action.metavar, str) else action.dest
    if name.startswith("<"):
        return name
    # One spelling for every value, whatever case its parser chose: `<thread-id>`
    # beside `<base>`, not `<THREAD_ID>`.
    return "<" + name.lower().replace("_", "-") + ">"


def _value(action: argparse.Action) -> str:
    """The value part of one action's synopsis, per its nargs; empty if none."""
    if isinstance(action, _NO_VALUE) or action.nargs == 0:
        return ""
    meta = _metavar(action)
    if action.nargs == argparse.OPTIONAL:
        return f"[{meta}]"
    if action.nargs == argparse.ZERO_OR_MORE:
        return f"[{meta} ...]"
    if action.nargs == argparse.ONE_OR_MORE:
        return f"{meta} ..."
    if action.nargs in (argparse.REMAINDER, argparse.PARSER):
        return "..."
    if isinstance(action.nargs, int) and action.nargs > 1:
        return " ".join([meta] * action.nargs)
    return meta


def _spelling(action: argparse.Action) -> str:
    """One action as it appears in a synopsis, without brackets."""
    if not action.option_strings:
        return _value(action)
    value = _value(action)
    flags = "|".join(action.option_strings)
    return f"{flags} {value}" if value else flags


def _groups(parser: argparse.ArgumentParser) -> dict[int, argparse._MutuallyExclusiveGroup]:
    """Each action's mutually exclusive group, keyed by the action's id."""
    owner = {}
    for group in parser._mutually_exclusive_groups:
        for action in group._group_actions:
            owner[id(action)] = group
    return owner


def synopsis(parser: argparse.ArgumentParser, hidden: frozenset[str] = frozenset()) -> str:
    """Every documented argument of *parser*, as one synopsis string.

    Optional flags are bracketed; required ones are not; positionals come last,
    as argparse parses them. A mutually exclusive group is one bracket with its
    members separated by ` | `, so the synopsis says the two cannot combine.
    """
    owner = _groups(parser)
    shown = _documented(parser, hidden)
    parts: list[str] = []
    done: set[int] = set()
    for action in shown:
        if not action.option_strings or id(action) in done:
            continue
        group = owner.get(id(action))
        members = [a for a in (group._group_actions if group else [action]) if a in shown]
        done.update(id(a) for a in members)
        text = " | ".join(_spelling(a) for a in members)
        required = group.required if group else action.required
        parts.append(text if required else f"[{text}]")
    # A positional's brackets come from its nargs, in _value.
    parts.extend(_spelling(a) for a in shown if not a.option_strings)
    return " ".join(parts)


def _invocation(prog: str, name: str | None, parser: argparse.ArgumentParser,
                hidden: frozenset[str] = frozenset()) -> str:
    """One invocation: program, command, synopsis, with no doubled spaces."""
    return " ".join(p for p in (prog, name, synopsis(parser, hidden)) if p)


def usage_line(shape: CLIShape) -> str:
    """Every invocation of *shape* on one line.

    One per command, in declaration order; a CLI with no commands is one
    invocation of its global flags. The globals are not repeated on every
    command — they are named once, in `tables`, and on the command line they
    are optional by construction.
    """
    if not shape.commands:
        return _invocation(shape.prog, None, shape.globals) if shape.globals else shape.prog
    hidden = _global_strings(shape)
    return USAGE_SEPARATOR.join(
        _invocation(shape.prog, c.name, c.parser, hidden) for c in shape.commands
    )


def _cell(text: str) -> str:
    """Text safe inside a markdown table cell: pipes escaped, lines joined."""
    return " ".join(text.split()).replace("|", "\\|")


def _help(parser: argparse.ArgumentParser, action: argparse.Action) -> str:
    """The action's help with argparse's `%(default)s`-style fields expanded."""
    if not action.help:
        return ""
    return parser._get_formatter()._expand_help(action)


def _description(parser: argparse.ArgumentParser, action: argparse.Action,
                 owner: dict[int, argparse._MutuallyExclusiveGroup]) -> str:
    """The Description cell: help, then what the declaration adds to it."""
    help_text = _help(parser, action)
    said = help_text.lower()
    notes = [help_text.rstrip(".")] if help_text else []
    if action.required and action.option_strings:
        notes.append("Required")
    if (isinstance(action, (argparse._AppendAction, argparse._AppendConstAction, argparse._CountAction))
            and "repeatable" not in said):
        notes.append("Repeatable")
    # A default the help already states, or one that means "unset", adds
    # nothing a reader can use.
    default = action.default
    if (not isinstance(action, _NO_VALUE) and default not in (None, argparse.SUPPRESS, [], "")
            and "default" not in said):
        notes.append(f"Default: `{default}`")
    group = owner.get(id(action))
    if group:
        others = [a.option_strings[0] for a in group._group_actions
                  if a is not action and a.option_strings and a.help is not argparse.SUPPRESS]
        if others:
            notes.append("Not with " + ", ".join(f"`{o}`" for o in others))
    return _cell(". ".join(n for n in notes if n) + ("." if notes else ""))


def _flag_cell(action: argparse.Action) -> str:
    """The Flag cell: every option string, then the value it takes."""
    value = _value(action)
    if not action.option_strings:
        return f"`{value}`"
    flags = ", ".join(f"`{o}`" for o in action.option_strings)
    return f"{flags} `{value}`" if value else flags


def _table(parser: argparse.ArgumentParser, hidden: frozenset[str] = frozenset()) -> str:
    """The Flag/Description table for *parser*, or "" when it documents nothing."""
    owner = _groups(parser)
    rows = []
    for action in _documented(parser, hidden):
        rows.append(f"| {_cell(_flag_cell(action))} | {_description(parser, action, owner)} |")
    if not rows:
        return ""
    return "| Flag | Description |\n|------|-------------|\n" + "\n".join(rows)


def tables(shape: CLIShape) -> str:
    """Markdown flag tables for *shape*: globals first, then each command.

    A command with nothing to document still gets its line, saying so, so a
    reader scanning for a command finds it rather than concluding it does not
    exist. Bold text rather than headings: this lands under a script's
    `###` section, and a heading here would be a section of its own.
    """
    out = []
    hidden = _global_strings(shape)
    if shape.globals is not None:
        table = _table(shape.globals)
        if table:
            label = "Global flags" if shape.commands else "Flags"
            out.append(f"**{label}**\n\n{table}")
    for command in shape.commands:
        head = f"**`{shape.prog} {command.name}`**"
        if command.help:
            head += f" — {_cell(command.help)}"
        table = _table(command.parser, hidden)
        out.append(f"{head}\n\n{table}" if table else f"{head}\n\nTakes no flags.")
    return "\n\n".join(out)


def load(spec: str, prog: str, root: Path | str) -> CLIShape:
    """The shape a registry `parser:` field names, for the tool called *prog*.

    *spec* is `<module>:<attr>` for a module importable from `ai/lib`, or
    `<path>:<attr>` — a path relative to *root*, told apart by its `/` — for a
    script whose parser lives in the script itself. The attribute is called
    when it is callable; what it returns is a `CLIShape`, used as is, or an
    `ArgumentParser`, read with `shape_of`.

    Any failure raises: a `parser:` field naming something that does not load
    is a registry that documents nothing, and rendering it as an empty
    reference would read as a CLI with no flags.
    """
    target, sep, attr = spec.rpartition(":")
    if not sep or not target or not attr:
        raise ValueError(f"parser {spec!r}: expected <module-or-path>:<attr>")
    if "/" in target:
        path = Path(root) / target
        name = "_cli_reference_" + path.name.replace("-", "_").replace(".", "_")
        loader = importlib.machinery.SourceFileLoader(name, str(path))
        module_spec = importlib.util.spec_from_loader(name, loader)
        module = importlib.util.module_from_spec(module_spec)
        sys.modules[name] = module
        try:
            loader.exec_module(module)
        except BaseException:
            # Registered before it runs so the script's own imports and
            # dataclasses can find it; a script that raised is half-initialised
            # and must not stay findable under that name.
            sys.modules.pop(name, None)
            raise
    else:
        module = importlib.import_module(target)
    obj = getattr(module, attr)
    if callable(obj) and not isinstance(obj, (argparse.ArgumentParser, CLIShape)):
        obj = obj()
    if isinstance(obj, CLIShape):
        return obj
    if isinstance(obj, argparse.ArgumentParser):
        return shape_of(obj, prog)
    raise TypeError(f"parser {spec!r} gave {type(obj).__name__}, not a parser or CLIShape")
