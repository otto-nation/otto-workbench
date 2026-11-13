"""`wiki`'s CommandContract: flags by subcommand, exits, examples, local writes.

The parser and command bodies live in `cli.wiki`. This module is the promise
`--help` and the docs render, so `wiki.py` stays under the line budget.
"""

# doc-group: cli

from __future__ import annotations

import core.command_contract
from core.exit_codes import ExitCode

CONTRACT = core.command_contract.CommandContract(
    summary="Mechanical operations over a compiled knowledge base: locate it, "
            "report on it, and write the tree without reading for meaning.",
    groups=(
        core.command_contract.FlagGroup(
            "init", ("-V", "--wiki", "directory", "--domain", "--audience",
                     "--vault", "--in-repo")),
        core.command_contract.FlagGroup("path", ()),
        core.command_contract.FlagGroup("status", ("--json",)),
        core.command_contract.FlagGroup("lint", ("--signals",)),
        core.command_contract.FlagGroup("signals", ()),
        core.command_contract.FlagGroup("sources", ("--new",)),
        core.command_contract.FlagGroup("index", ("--check",)),
        core.command_contract.FlagGroup(
            "backup", ("--list", "--restore", "--keep")),
        core.command_contract.FlagGroup("link", ()),
        core.command_contract.FlagGroup(
            "ingest", ("--stage", "--type", "--title")),
        core.command_contract.FlagGroup("archive", ("slug", "--force")),
    ),
    exits=(
        core.command_contract.ExitOutcome(
            ExitCode.OK,
            "the subcommand succeeded; lint and index --check also mean no findings"),
        core.command_contract.ExitOutcome(
            ExitCode.FAILURE,
            "a finding was reported (lint findings, stale index), or the "
            "operation failed",
            (core.command_contract.NextStep(
                "", ("wiki lint", "wiki index")),)),
        core.command_contract.ExitOutcome(
            ExitCode.USAGE,
            "no subcommand, a required flag or argument is missing, no "
            "knowledge base found from DIR/--wiki, or init could not tell "
            "where to create one",
            (core.command_contract.NextStep(
                "", ("wiki init --vault", "wiki init --in-repo",
                     "wiki init --wiki DIR")),)),
        core.command_contract.ExitOutcome(
            ExitCode.CANCELLED,
            "Ctrl-C interrupted the CLI"),
    ),
    examples=(
        core.command_contract.Example(
            "wiki init --in-repo",
            "create a knowledge base in the repo"),
        core.command_contract.Example(
            "wiki path", "print the resolved knowledge base directory"),
        core.command_contract.Example(
            "wiki status --json", "counts and recent activity as JSON"),
        core.command_contract.Example(
            "wiki lint --signals",
            "mechanical findings, then the counted signals"),
        core.command_contract.Example(
            "wiki signals", "counted evidence lint leaves to the reader"),
        core.command_contract.Example(
            "wiki sources --new", "raw sources that are new or changed"),
        core.command_contract.Example(
            "wiki index --check", "report whether the master index is stale"),
        core.command_contract.Example(
            "wiki backup --list", "list snapshots instead of making one"),
        core.command_contract.Example(
            "wiki link",
            "create or remove the browsing symlink named by wiki.link"),
        core.command_contract.Example(
            "wiki ingest --stage ./notes.md --title Notes",
            "copy a source into raw/ with frontmatter and a real hash"),
        core.command_contract.Example(
            "wiki archive old-article",
            "retire an article to archive/, keeping it readable"),
    ),
    remote_effects=(),
    notes=(
        "Writes stay on this machine: the knowledge base tree (init, ingest, "
        "archive, index), wiki.root via set_value when init --vault adopts a "
        "vault, the vault folder, backup snapshots, and the browsing symlink "
        "that link and init --vault may create.",
        "lint and index --check exit 1 when they have findings, not only when "
        "the write failed.",
    ),
    reference="docs/ai-automation.md#wiki-contract",
)
