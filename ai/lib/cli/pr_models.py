"""`pr models`: the models a phase can be set to, as versioned JSON.

Exit codes, remote writes and examples are `CONTRACT` below. The JSON shape
is `agent.model_choices`.
"""

# doc-group: cli

from __future__ import annotations

import agent.model_choices
import config.workbench_config
import core.command_contract
import core.log
import core.report
import pr.context
from core.exit_codes import ExitCode
from core.tool_parser import EXIT_USAGE, ToolParser

SCRIPT = "pr models"

CONTRACT = core.command_contract.CommandContract(
    summary=(
        "The models a phase can be set to (JSON). Requires --json; "
        "without it exits 2. --repo-dir is the global flag."
    ),
    groups=(
        core.command_contract.FlagGroup("Output", ("--json",)),
    ),
    remote_effects=(),
    exits=(
        core.command_contract.ExitOutcome(
            ExitCode.OK,
            "a document printed; an unreadable catalogue is ok:false, not a failure",
        ),
        core.command_contract.ExitOutcome(
            ExitCode.FAILURE, "a config file could not be read"),
        core.command_contract.ExitOutcome(
            ExitCode.USAGE, "no --json",
        ),
    ),
    examples=(
        core.command_contract.Example(
            "pr models --json",
            "print the choices for this machine's global config",
        ),
        core.command_contract.Example(
            "pr models --json --repo-dir ~/git/o/r/main",
            "print the choices with that checkout's project tiers",
        ),
    ),
    json_output=core.command_contract.JsonOutput(
        shape="schema 1: phases, tiers, enabled, catalogue",
    ),
    notes=(
        "Writes nothing remotely. --repo-dir is a global flag. "
        "stdout is {schema, phases, tiers, enabled, catalogue}. "
        "A catalogue that cannot be read is catalogue.ok false with the reason.",
    ),
)


def build_parser() -> ToolParser:
    """This command's parser; `pr` reads it for arity and for `pr models --help`."""
    parser = ToolParser(
        prog=SCRIPT,
        description="Models a phase can be set to (JSON)",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Print the choices document",
    )
    return parser


def cmd_models(
    argv: list[str],
    _ctx: pr.context.ResolvedContext,
    *,
    original_repo_dir: str | None = None,
    **_kw,
) -> int:
    """Parse *argv* and emit the model-choices document for *original_repo_dir*."""
    args = build_parser().parse_args(argv)
    if not args.json:
        core.log.error(f"{SCRIPT} needs --json")
        return EXIT_USAGE
    try:
        doc = agent.model_choices.model_choices(original_repo_dir)
    except config.workbench_config.ConfigError as exc:
        core.log.error(str(exc))
        return ExitCode.FAILURE
    core.report.emit_json(doc.as_json(), compact=True)
    return 0
