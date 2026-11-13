"""`pr stack`: the repo's open PRs as a stack graph, as versioned JSON.

Exit codes, remote writes and examples are `CONTRACT` below.
"""

# doc-group: cli

from __future__ import annotations

import core.command_contract
import core.report
import core.serde
from core.exit_codes import ExitCode
from core.tool_parser import ToolParser
import pr.context
import pr.stack

SCRIPT = "pr stack"
SCHEMA_VERSION = 1

CONTRACT = core.command_contract.CommandContract(
    summary="The repo's PR stack graph (JSON)",
    groups=(),
    remote_effects=(),
    exits=(
        core.command_contract.ExitOutcome(
            ExitCode.OK, "the graph (or one PR's ladder) printed"),
        core.command_contract.ExitOutcome(
            ExitCode.FAILURE, "no checkout, or the graph could not be loaded"),
        core.command_contract.ExitOutcome(
            ExitCode.USAGE, "bad pr-level flags"),
    ),
    examples=(
        core.command_contract.Example("pr stack", "the whole repo's graph"),
        core.command_contract.Example(
            "pr stack --pr 42", "PR 42's ladder, bottom first"),
    ),
    notes=(
        "Writes nothing remotely. --repo-dir/--branch/--pr are global flags. "
        "stdout is {schema_version, trunk, nodes, cycles}.",
    ),
)


def build_parser() -> ToolParser:
    """This command's parser; `pr` reads it for arity and for `pr stack --help`."""
    parser = ToolParser(prog=SCRIPT, description="Show the repo's PR stack graph (JSON)")
    parser.add_argument("--repo-dir", "--worktree", dest="repo_dir", metavar="PATH",
                        help="Git worktree directory")
    parser.add_argument("--branch", metavar="NAME", help="Branch name (injected by pr dispatcher)")
    parser.add_argument("--pr", metavar="NUM|URL",
                        help="Print only this PR's ladder, bottom first")
    return parser


def run(*, repo: str, cwd: str, pr_number: int | None, schema_version: int) -> int:
    graph = pr.stack.load_graph(repo, cwd)
    nodes = pr.stack.ladder(graph, pr_number) if pr_number else graph.nodes
    core.report.emit_json({
        "schema_version": schema_version, "trunk": graph.trunk,
        "nodes": [core.serde.to_dict(n) for n in nodes], "cycles": graph.cycles,
    })
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    ctx = pr.context.resolve_at(pr.context.ContextDepth.LOCAL, repo_dir=args.repo_dir,
                                branch=args.branch, pr_ref=args.pr)
    return run(repo=ctx.repo, cwd=str(ctx.require_worktree()),
               pr_number=ctx.pr_number if args.pr else None,
               schema_version=SCHEMA_VERSION)
