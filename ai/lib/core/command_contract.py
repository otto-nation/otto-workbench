"""What one `pr` subcommand promises: flags by group, remote writes, exits, next steps.

A command's flags were described by its parser, its remote effects by prose
in a skill, its exit codes by a module docstring and its next steps by
nothing, so four readers learned four different commands. A
`CommandContract` is the one declaration; this module renders it four ways —
the `--help` epilog (`render_help`/`attach_help`), the docs block
(`render_markdown`), the machine schema (`schema`) and, at run time, the
`Next:` block a command prints after a non-zero exit (`print_next`).

`coverage_problems` is what keeps the declaration honest against the parser:
every documented flag sits in exactly one `FlagGroup`, every `RemoteEffect`
trigger names declared flags, and every contract says what exits 0, 1 and 2
mean. Which flags *touch the remote* cannot be read off a parser, so it is
asserted by the enforcement suite (`tests/cli_contract_test.py`) rather than
here.

Flag keys are the first option string (`--onto`, not `--base`) or, for a
positional, its `dest`. A trigger is `""` for the bare invocation or a
space-separated set of flag keys that together cause the write.

Layer 1: the dataclasses hold strings and `ExitCode`s only, so `cli` declares
contracts and `core.cli_reference` renders them without importing `cli`.
"""

# doc-group: platform

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass

from core.exit_codes import ExitCode

# Where the generated reference lives; every help epilog points a reader there.
REFERENCE_ANCHOR = "docs/ai-automation.md#pr-command-contracts"

# The exits every command can produce: success, a failure, and argparse's own.
_REQUIRED_EXITS = (ExitCode.OK, ExitCode.FAILURE, ExitCode.USAGE)


@dataclass(frozen=True)
class FlagGroup:
    """A titled help section and the flag keys it holds, in display order."""

    title: str
    flags: tuple[str, ...]


@dataclass(frozen=True)
class RemoteEffect:
    """One thing the command writes outside this machine.

    ``trigger`` — ``""`` for the bare invocation, else the flag keys that
    cause it. ``writes`` — what changes (a ref, a PR body, review comments).
    ``where`` — the remote it lands on. ``how`` — the exact mechanism, argv
    where there is one. ``on_refusal`` — what happens when the remote says
    no (a lease mismatch, a conflict, a closed PR) and what was written then.
    """

    trigger: str
    writes: str
    where: str
    how: str
    on_refusal: str


@dataclass(frozen=True)
class NextStep:
    """Lines to print after an exit, when the command's runtime state is ``when``.

    ``when`` is ``""`` for always, else a key the command passes to
    `print_next` (`pr rebase` passes ``"paused"`` or ``"clean"``). Lines are
    `str.format` templates over the fields the command supplies.
    """

    when: str
    lines: tuple[str, ...]


@dataclass(frozen=True)
class ExitOutcome:
    """What one exit code means for this command, and what to do after it."""

    code: ExitCode
    meaning: str
    next_steps: tuple[NextStep, ...] = ()


@dataclass(frozen=True)
class Example:
    """A literal invocation and the one-line result."""

    command: str
    does: str


@dataclass(frozen=True)
class EnumValue:
    """One value a JSON field can take, and what it means."""

    value: str
    meaning: str


@dataclass(frozen=True)
class EnumDoc:
    """The vocabulary of one JSON field."""

    field: str
    values: tuple[EnumValue, ...]


@dataclass(frozen=True)
class JsonOutput:
    """What goes to stdout: which object on which exit, and the enum vocabularies."""

    shape: str
    enums: tuple[EnumDoc, ...] = ()


@dataclass(frozen=True)
class CommandContract:
    """Everything a reader needs to run one command without reading its source."""

    summary: str
    groups: tuple[FlagGroup, ...]
    exits: tuple[ExitOutcome, ...]
    examples: tuple[Example, ...]
    remote_effects: tuple[RemoteEffect, ...] = ()
    json_output: JsonOutput | None = None
    notes: tuple[str, ...] = ()

    def outcome(self, code: int) -> ExitOutcome | None:
        """The declared outcome for *code*, or None when the contract is silent."""
        return next((o for o in self.exits if int(o.code) == int(code)), None)

    def next_steps(self, code: int, *, when: str = "", **fields: str) -> list[str]:
        """The filled-in next-step lines for *code* in state *when*."""
        found = self.outcome(code)
        if found is None:
            return []
        return [line.format(**fields)
                for step in found.next_steps if step.when in ("", when)
                for line in step.lines]


def flag_key(action: argparse.Action) -> str:
    """The key a contract uses for *action*: its first option string, else its dest."""
    return action.option_strings[0] if action.option_strings else action.dest


def anchor(invocation: str) -> str:
    """The markdown anchor `render_markdown` gives *invocation*'s heading."""
    return re.sub(r"[^a-z0-9]+", "-", f"{invocation} contract".lower()).strip("-")


def _trigger(trigger: str) -> str:
    return trigger or "(bare invocation)"


def render_help(contract: CommandContract) -> str:
    """The `--help` epilog: remote writes, exits with next steps, examples, JSON."""
    out: list[str] = []
    if contract.remote_effects:
        out.append("Writes remotely:")
        for e in contract.remote_effects:
            out.append(f"  {_trigger(e.trigger)}: {e.writes} on {e.where}")
            out.append(f"      how: {e.how}")
            out.append(f"      if refused: {e.on_refusal}")
    else:
        out.append("Writes remotely: nothing")
    out.append("")
    out.append("Exit codes:")
    for o in contract.exits:
        out.append(f"  {int(o.code):<3}{o.code.name.lower()} — {o.meaning}")
        for step in o.next_steps:
            label = f"Next ({step.when})" if step.when else "Next"
            out.append(f"       {label}: " + " → ".join(step.lines))
    out.append("")
    out.append("Examples:")
    width = max(len(x.command) for x in contract.examples)
    out += [f"  {x.command:<{width}}  # {x.does}" for x in contract.examples]
    if contract.json_output is not None:
        out += ["", "JSON on stdout:", f"  {contract.json_output.shape}"]
        for doc in contract.json_output.enums:
            out.append(f"  {doc.field}:")
            vw = max(len(v.value) for v in doc.values)
            out += [f"    {v.value:<{vw}}  {v.meaning}" for v in doc.values]
    if contract.notes:
        out += ["", "Notes:"] + [f"  {n}" for n in contract.notes]
    out += ["", f"Full reference: {REFERENCE_ANCHOR}"]
    return "\n".join(out)


def _regroup(parser: argparse.ArgumentParser, groups: Sequence[FlagGroup]) -> None:
    # Private argparse API (`_action_groups`, `_group_actions`), the same trade
    # core.cli_reference makes; test_attach_help_regroups_flags_and_keeps_newlines
    # fails first if an upgrade moves them.
    by_key = {flag_key(a): a for a in parser._actions}
    for group in groups:
        section = parser.add_argument_group(group.title)
        for key in group.flags:
            action = by_key.get(key)
            if action is None:
                continue
            for container in parser._action_groups:
                if action in container._group_actions:
                    container._group_actions.remove(action)
            section._group_actions.append(action)


def attach_help(parser: argparse.ArgumentParser, contract: CommandContract) -> None:
    """Make *parser*'s help show *contract*: grouped flags, then the epilog verbatim."""
    _regroup(parser, contract.groups)
    if not parser.description:
        parser.description = contract.summary
    parser.epilog = render_help(contract)
    parser.formatter_class = argparse.RawDescriptionHelpFormatter


def _cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def render_markdown(invocation: str, contract: CommandContract) -> str:
    """The docs block for *invocation* (e.g. ``pr rebase``), headed for `anchor`."""
    out = [f"#### {invocation} contract", "", contract.summary, ""]
    if contract.remote_effects:
        out += ["**Writes remotely**", "",
                "| Trigger | Writes | Where | How | If refused |",
                "|---|---|---|---|---|"]
        out += [f"| `{_cell(_trigger(e.trigger))}` | {_cell(e.writes)} | {_cell(e.where)} "
                f"| {_cell(e.how)} | {_cell(e.on_refusal)} |" for e in contract.remote_effects]
    else:
        out.append("**Writes remotely:** nothing.")
    out += ["", "**Exit codes**", "", "| Code | Meaning | Next |", "|---|---|---|"]
    for o in contract.exits:
        steps = "<br>".join(
            (f"*{s.when}:* " if s.when else "") + " → ".join(f"`{_cell(x)}`" for x in s.lines)
            for s in o.next_steps)
        out.append(f"| `{int(o.code)}` | {_cell(o.meaning)} | {steps} |")
    out += ["", "**Examples**", ""] + [f"- `{x.command}` — {x.does}" for x in contract.examples]
    if contract.json_output is not None:
        out += ["", f"**JSON on stdout:** {contract.json_output.shape}"]
        for doc in contract.json_output.enums:
            out += ["", f"| `{doc.field}` | Meaning |", "|---|---|"]
            out += [f"| `{v.value}` | {_cell(v.meaning)} |" for v in doc.values]
    if contract.notes:
        out += [""] + [f"> {n}" for n in contract.notes]
    return "\n".join(out)


def schema(contract: CommandContract) -> dict:
    """The machine-readable part: exit codes and remote effects."""
    return {
        "exit_codes": [{"code": int(o.code), "name": o.code.name.lower(), "meaning": o.meaning}
                       for o in contract.exits],
        "remote_effects": [{"trigger": e.trigger, "writes": e.writes, "where": e.where,
                            "how": e.how, "on_refusal": e.on_refusal}
                           for e in contract.remote_effects],
    }


def print_next(contract: CommandContract, code: int, *, when: str = "", **fields: str) -> None:
    """Print the `Next:` block for *code* on stderr; nothing when there are no steps.

    Plain print, not `core.log`: it must survive quiet mode and carry no colour,
    because an agent copies these lines into its next command.
    """
    lines = contract.next_steps(code, when=when, **fields)
    if not lines:
        return
    body = "".join(f"  {i}. {line}\n" for i, line in enumerate(lines, 1))
    sys.stderr.write("Next:\n" + body)
    sys.stderr.flush()


def coverage_problems(contract: CommandContract, documented: Sequence[str]) -> list[str]:
    """Every way *contract* disagrees with the flag keys its parser documents."""
    problems: list[str] = []
    grouped = [k for g in contract.groups for k in g.flags]
    declared = set(documented)
    for key in documented:
        if key not in grouped:
            problems.append(f"{key} is in no flag group")
    for key in sorted({k for k in grouped if grouped.count(k) > 1}):
        problems.append(f"{key} is in more than one flag group")
    for key in grouped:
        if key not in declared:
            problems.append(f"{key} is grouped but not declared by the parser")
    for e in contract.remote_effects:
        for key in e.trigger.split():
            if key not in declared:
                problems.append(f"remote effect trigger {key} is not a declared flag")
    codes = [int(o.code) for o in contract.exits]
    for code in _REQUIRED_EXITS:
        if int(code) not in codes:
            problems.append(f"exit {int(code)} ({code.name.lower()}) is not declared")
    if len(codes) != len(set(codes)):
        problems.append("an exit code is declared twice")
    return problems
