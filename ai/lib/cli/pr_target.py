"""`pr target`: one PR target's session — what it waits on, how to open it, and verdicts in it.

    pr target report     the target's report: a one-entry sessions document (JSON)
    pr target session [--json]   open the target's pi session in its worktree (--json prints how)
    pr target note PR --finding R:S --verdict V --reason TEXT   record a verdict in the target's history

The target is the branch `pr` resolves with git alone (the global `--repo-dir`
/ `--branch`). Nothing here asks GitHub, takes a lock, or writes outside the
target's history. What the report holds is `batch.target_report`.
"""

# doc-group: cli

from __future__ import annotations

import argparse
import sys

import batch.target_report
import batch.target_session
import cli.pr_batch_session
import core.command_contract
import core.report
import pr.context
import pr.state
from batch.model import Verdict
from core.exit_codes import ExitCode

SCRIPT = "pr target"

CONTRACT = core.command_contract.CommandContract(
    summary="One PR target's session: report prints what it waits on (JSON); "
            "session opens its pi session. Reads saved state only; --repo-dir/--branch "
            "are the global flags.",
    groups=(core.command_contract.FlagGroup("session", ("session --json",)),
            core.command_contract.FlagGroup("note", ("note pr", "note --finding",
                                                      "note --verdict", "note --reason",
                                                      "note --command"))),
    remote_effects=(),
    exits=(
        core.command_contract.ExitOutcome(ExitCode.OK, "a document printed"),
        core.command_contract.ExitOutcome(
            ExitCode.FAILURE,
            "no origin to key the target on; session: no worktree holds the branch, "
            "or pi could not start; note: the history could not be written"),
        core.command_contract.ExitOutcome(
            ExitCode.USAGE,
            "bad flags, or a note naming a PR or finding this target does not have, "
            "or an empty reason"),
    ),
    examples=(
        core.command_contract.Example(
            "pr target report", "what this branch's PR waits on, and the commands that act on it"),
        core.command_contract.Example(
            "pr target session", "open this branch's target session in its worktree"),
        core.command_contract.Example(
            "pr target note 7 --finding ci_failing:ci --verdict deferred --reason 'flaky runner'",
            "record a verdict; changes no state"),
    ),
    notes=("Writes nothing remotely. report prints {schema_version, sessions} with at most "
           "one entry, in pr batch report's sessions shape.",),
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=SCRIPT, description=__doc__.split("\n")[0],
        epilog="\n".join(__doc__.split("\n")[1:]).strip(),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("report", help="Print what this target waits on (JSON), read offline")
    s = sub.add_parser("session", help="Open this target's pi session, or print how to (--json)")
    s.add_argument("--json", action="store_true",
                   help="Print {schema_version, session_id, name, cwd, argv, env} instead of "
                        "starting pi; UIs open the session from this, never their own argv")
    n = sub.add_parser("note", help="Record a verdict on one finding in the target's history; "
                                    "changes no state")
    n.add_argument("pr", help="The target's PR: owner/repo#N or N")
    n.add_argument("--finding", type=cli.pr_batch_session.finding_arg, required=True,
                   metavar="REASON:STEP", help="The finding as the report names it")
    n.add_argument("--verdict", choices=[v.value for v in Verdict], required=True,
                   help="What was decided")
    n.add_argument("--reason", required=True, help="Why, in a sentence; must not be empty")
    n.add_argument("--command", dest="commands", action="append", default=[], metavar="CMD",
                   help="A command run to act on it, as typed; repeatable")
    return parser


def cmd_target(argv: list[str], ctx: pr.context.ResolvedContext, *, trail, **_kw) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "session":
        return _cmd_session(args, ctx, trail)
    if args.command == "note":
        return _cmd_note(args, ctx, trail)
    return _cmd_report(ctx)


def _fail(message: str) -> int:
    print(f"pr target: {message}", file=sys.stderr)
    return int(ExitCode.FAILURE)


def _cmd_session(args, ctx: pr.context.ResolvedContext, trail) -> int:
    try:
        recipe = batch.target_session.recipe(ctx)
    except batch.target_session.TargetSessionError as exc:
        return _fail(str(exc))
    if args.json:
        core.report.emit_json(recipe.to_dict())
        return int(ExitCode.OK)
    return cli.pr_batch_session.exec_recipe(recipe, _fail, before_exec=trail.finish)


def _cmd_note(args, ctx: pr.context.ResolvedContext, trail) -> int:
    try:
        rec = batch.target_session.note(
            pr.state.load_state(ctx.target_dir), ctx.target_dir, args.pr, args.finding,
            Verdict(args.verdict), args.reason, args.commands, run=trail.invocation)
    except batch.target_session.TargetSessionError as exc:
        print(f"pr target: {exc}", file=sys.stderr)
        return int(ExitCode.USAGE)
    except OSError as exc:
        return _fail(f"could not record the note for {ctx.target_dir.name}: {exc}")
    core.report.emit_json(rec)
    return int(ExitCode.OK)


def _cmd_report(ctx: pr.context.ResolvedContext) -> int:
    worktree = str(ctx.worktree_root) if ctx.worktree_root else ""
    core.report.emit_json(batch.target_report.report(pr.state.load_state(ctx.target_dir), worktree))
    return int(ExitCode.OK)
