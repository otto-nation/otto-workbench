"""`pr`'s parser, its dispatcher, and the two commands that shape argv.

The entry point, and only the entry point. Every subcommand's work lives
below this layer: three in `cli.pr_commands`, six behind a `CommandSpec`
handler the registry names. What is here is the two-pass global parse, the
usage text, the ordering of resolve/register/fetch/lock, and the routing.

`cmd_review` and `cmd_comments` are here rather than in `cli.pr_commands`
because neither is a command in its own right: both shape argv ahead of a
delegate the registry already names — `--self` injection, mode routing — and
`cli.pr_commands` holds the three that `pr` genuinely performs itself.

`bin_dir` is a parameter, not something this module derives. Under
`WORKBENCH_AI_LIB_DIR` this file resolves inside the pinned checkout while
the entry point's own directory does not, so a path built from `__file__`
here would name a different tree's `ai/bin` than the shim the operator ran.
`ai/bin/pr` passes its own, matching `cli.review_modes` and
`review.publish.post`.

`main` returns an int and does not exit, like every other `cli.<name>.main`.
The shim does the `sys.exit`. `--tool-schema` is answered before anything
else, because `pr ci --tool-schema` still has to resolve the flag ahead of
dispatch, and a reader running `ai/bin/pr --tool-schema` directly should not
pay for a context resolution to get it. The MCP server no longer spawns this
binary to discover the tool; it imports `cli.schema.tool_schema` directly.
"""

# doc-group: cli

import argparse
import contextlib
import json
import os
import sys
from pathlib import Path

import cli.dispatch
import cli.review_modes
from cli.needs import Need
from cli.pr_batch import cmd_batch
from cli.pr_commands import (
    # Re-exported, not used here: the tests read `pr_cli.EXIT_BUDGET_EXHAUSTED`
    # to bind this module's exit code to the maintenance script's bash
    # comparison. See the constant's own comment in cli/pr_commands.py.
    EXIT_BUDGET_EXHAUSTED,  # noqa: F401
    cmd_fix,
    cmd_gc,
    cmd_status,
)
from cli.registry import COMMANDS, need_for
from cli.schema import (
    EXIT_USAGE,
    checked_schema_version,
    schema_contracts,
    subcommand_schema,
    tool_schema,
)
import pr.context
import pr.state
import pr.sync
import pr.push_intent
import core.cli_reference
import core.log
import core.tool_parser
import core.proc
import core.publishing
import config.workbench_projects
import core.run_lock
import review.listing
from core.trail import Trail, add_trail_args

# `gitenv` is a workbench-wide module, not an `ai/lib` one; see `pr.push_intent`
# for the path arithmetic, which is the same here.
_WORKBENCH_LIB = Path(__file__).resolve().parent.parent.parent.parent / "lib"
if _WORKBENCH_LIB.is_dir() and str(_WORKBENCH_LIB) not in sys.path:
    sys.path.insert(0, str(_WORKBENCH_LIB))
import gitenv  # noqa: E402

# The command a user types. A literal, not `Path(__file__).name`: this module
# is `pr.py` and the two happen to agree, but an error or a trail naming the
# module rather than the command is the kind of thing nobody notices until a
# rename makes it wrong.
SCRIPT = "pr"


def _is_pr_target(target: str | None) -> bool:
    """Check if target looks like a PR URL or number (not a branch name)."""
    if not target:
        return False
    return pr.context.is_pr_ref(target)



# ── Subcommands ─────────────────────────────────────────────────────────────



# `cmd_review` and `cmd_comments` stay here; see `cli.pr_commands`'s module
# docstring for why (and for which three subcommands moved there instead).

def cmd_review(argv: list[str], ctx: pr.context.ResolvedContext, *,
               bin_dir: Path,
               original_pr: str | None = None,
               original_branch: str | None = None,
               schema_version: int | None = None,
               **_kw) -> int:
    """Run a review, or handle one of the mode flags in `review_modes.MODES`."""
    given = cli.review_modes.flags_given(argv)
    if len(given) > 1:
        core.log.error(f"{SCRIPT}: {cli.review_modes.flags_prose()} are mutually exclusive")
        return 1

    if given:
        flag = given[0]
        handler = cli.review_modes.MODES[flag].handler
        if handler:
            return handler([a for a in argv if a != flag], ctx,
                           bin_dir=bin_dir, schema_version=schema_version)

    has_self = "--self" in argv
    positionals, _ = cli.dispatch.split_argv(argv)
    has_pr_target = (
        any(_is_pr_target(p) for p in positionals)
        or _is_pr_target(original_pr)
        or ctx.pr_number is not None
    )
    inject = ["--self"] if not has_self and not has_pr_target and not positionals else []
    return core.publishing.call_entry_point(
        "cli.review_entry:main",
        cli.dispatch.delegate_argv(COMMANDS["review"], inject + list(argv), ctx,
                               original_pr=original_pr,
                               original_branch=original_branch),
        # `pr` installed the identical handler at its own entry point, and
        # `signal.signal` neither chains nor restores — a second install
        # would replace the one that reports the interrupt for the whole
        # invocation with one that reports it for the review alone.
        install_signal_handler=False,
    )


def _invocation(command: str, argv: list[str]) -> str:
    """This invocation as the user typed it, for an error that quotes it back.

    A mode flag is part of the invocation's identity — `review` and `review
    --list` are different commands to everyone but argparse — so an error about
    one has to name the flag or it reads as being about the other.
    """
    modes = cli.review_modes.flags_given(argv) if command == "review" else []
    return " ".join(filter(None, [SCRIPT, command, *modes[:1]]))


def cmd_comments(argv: list[str], ctx: pr.context.ResolvedContext, *,
                 original_pr: str | None = None,
                 original_branch: str | None = None,
                 **_kw) -> int:
    """Delegate to review-threads, routing --triage, --fix, --settle and --finish flags."""
    return core.publishing.call_entry_point(
        "cli.review_threads:main",
        cli.dispatch.delegate_argv(COMMANDS["comments"], list(argv), ctx,
                               original_pr=original_pr,
                               original_branch=original_branch),
    )


def cmd_create(argv: list[str], ctx: pr.context.ResolvedContext, **kw) -> int:
    """Run `pr create`'s handler, imported only when create is dispatched.

    A lazy hop rather than an import: `cli.pr_create` pulls in the content
    generator and the agent layer, and `pr --help` must load no delegate
    (test_pr_help_imports_no_delegate). The registry's handler string is the
    one name for it.
    """
    return cli.dispatch.resolve(COMMANDS["create"].handler)(argv, ctx, **kw)


# ── CLI ─────────────────────────────────────────────────────────────────────

# Custom handlers for commands that need more than passthrough.
_CUSTOM = {
    "create":   cmd_create,
    "status":   cmd_status,
    "review":   cmd_review,
    "comments": cmd_comments,
    "fix":      cmd_fix,
    "batch":    cmd_batch,
    "gc":       cmd_gc,
}

def build_global_parser() -> argparse.ArgumentParser:
    """The flags `pr` accepts around any command, parsed before the command is.

    One parser for both the first pass of `main` and the reference rendered
    from it, so the flags documented as global are the flags the first pass
    consumes — `--worktree` included, which no hand-written usage ever named.
    """
    parser = argparse.ArgumentParser(prog=SCRIPT, add_help=False)
    parser.add_argument("--repo-dir", "--worktree", dest="repo_dir", metavar="path",
                        help="Git worktree to act on; detected from the current "
                             "directory when omitted")
    parser.add_argument("--branch", metavar="name",
                        help="Branch to act on, resolved to the worktree it is "
                             "checked out in")
    parser.add_argument("--pr", metavar="num|url", help="PR number or URL to act on")
    # Global rather than declared on the invocation that serves a contract,
    # for three reasons. It describes the caller ("what do you speak"), not the
    # subcommand ("what do you want"). It generalizes to `pr status`, which
    # stamps a version nobody reads and is the obvious next document. And it is
    # consumed by this first parse, so its value never reaches `extra`, where
    # the positional scan below would read a bare `1` as PR #1 — `pr` has no
    # arity source of truth for the flags it handles itself.
    parser.add_argument("--schema-version", dest="schema_version", metavar="n",
                        help="Serve a versioned JSON document on stdout instead of "
                             "a human table, where the command has a contract")
    add_trail_args(parser)
    return parser


def reference_shape() -> core.cli_reference.CLIShape:
    """Everything `pr` accepts, for the rendered usage line and flag tables.

    A command with a parser factory is documented from that parser — its
    subparser here declares nothing and forwards argv whole — and every other
    command from its subparser, which is where its flags (or their absence)
    are declared. A command in COMMANDS with neither is a registry this module
    cannot parse, and fails here rather than rendering as a command with no
    flags.
    """
    subs = core.tool_parser.subparsers(_build_parser())
    commands = tuple(
        core.cli_reference.Command(name, spec.help, _reference_parser(name, subs))
        for name, spec in COMMANDS.items()
    )
    return core.cli_reference.CLIShape(
        prog=SCRIPT, globals=build_global_parser(), commands=commands,
    )


def _reference_parser(name: str, subs: dict[str, argparse.ArgumentParser]) -> argparse.ArgumentParser:
    """The parser that documents `pr <name>`, per `reference_shape`."""
    if not cli.dispatch.has_parser_factory(name):
        if name not in subs:
            raise RuntimeError(f"pr: '{name}' has neither a parser factory nor a subparser")
        return subs[name]
    parser = cli.dispatch.resolve(cli.dispatch.PARSER_FACTORIES[name])()
    # `review`'s handler routes the mode flags before its parser runs, so `pr
    # review` accepts more than `review` does — the one command whose
    # dispatcher adds flags of its own.
    if name == "review":
        return cli.review_modes.reference_parser(parser)
    return parser


def _build_parser() -> argparse.ArgumentParser:
    """Build the command parser.

    A command with a parser factory has a subparser that declares no flags of
    its own — its argv is forwarded whole and `pr <command> --help` is answered
    by the factory's parser — so add_help is left off for those. That is every
    delegate and `create`, whose handler runs here but whose parser is its own.

    The subparsers are not returned alongside: what a command declares is read
    back off the built parser with tool_parser.subparsers, which is what keeps
    `takes_target` honest.
    """
    parser = argparse.ArgumentParser(
        prog=SCRIPT,
        description="PR lifecycle management",
        add_help=False,
    )
    parser.add_argument("-h", "--help", action="store_true", dest="help")

    sub = parser.add_subparsers(dest="command")
    for name, spec in COMMANDS.items():
        sub.add_parser(name, help=spec.help,
                       add_help=not cli.dispatch.has_parser_factory(name))
    return parser


def _build_usage() -> str:
    max_name = max(len(n) for n in COMMANDS)
    cmd_lines = "\n".join(
        f"  {name:<{max_name + 2}}{spec.help}"
        for name, spec in COMMANDS.items()
    )
    contracts = ", ".join(schema_contracts())
    versions = ", ".join(str(v) for v in review.listing.SCHEMA_VERSIONS)
    return f"""\
pr — PR lifecycle management

Usage: pr [global flags] <command> [flags]

Commands:
{cmd_lines}

Global flags (auto-detected from CWD when omitted):
  --repo-dir PATH    Git worktree directory
  --branch NAME      Branch name
  --pr NUM|URL       PR number or URL

Contract flags:
  --schema-version N  Serve a versioned JSON document on stdout instead of a
                      human table. Honored by {contracts}; serving {versions}.

Run 'pr <command> -h' for details on a specific command."""


def _reject_a_target_that_resolves_to_nothing(
    command: str, argv: list[str], need: Need,
    pr_arg: str | None, branch: str | None,
) -> None:
    """Exit if a command that resolves nothing was handed a target anyway.

    Depth NONE has nowhere to put a target, so honouring one is not an option —
    the choice is between ignoring it and refusing it. Ignoring is the worse
    answer by a distance: `pr review 123 --list` reads as "the reviews for
    #123" and would come back with every review on the machine, which is a
    wrong answer that looks exactly like a right one.
    """
    given = pr_arg or branch
    if need.depth is not pr.context.ContextDepth.NONE or not given:
        return
    core.log.error(f"{SCRIPT}: {_invocation(command, argv)} answers from your state "
              f"root and takes no PR or branch — drop {given!r}")
    sys.exit(EXIT_USAGE)


def _dispatch(args, ctx: pr.context.ResolvedContext, extra: list[str], global_args, *,
              need: Need,
              bin_dir: Path,
              original_pr: str | None,
              original_branch: str | None) -> int:
    """Start the trail and route to the command's handler or backing script."""
    trail = Trail.start(
        script=SCRIPT,
        context={"repo": ctx.repo, "pr": ctx.pr_number, "branch": ctx.branch},
        debug=getattr(global_args, "debug", False),
        record=need.records_a_trail,
    )
    try:
        trail.decision(
            "dispatch",
            f"routing to {args.command}",
            reason="matched command registry",
            data={"subcommand": args.command},
        )

        local = _CUSTOM.get(args.command)
        if local:
            return local(extra, ctx,
                         trail=trail,
                         bin_dir=bin_dir,
                         original_pr=original_pr,
                         original_branch=original_branch,
                         schema_version=global_args.schema_version)
        spec = COMMANDS[args.command]
        assert spec.handler, \
            (f"pr: command {args.command!r} has no handler and no wrapper — "
             f"register it in _CUSTOM or give its CommandSpec a handler")
        return core.publishing.call_entry_point(
            spec.handler,
            cli.dispatch.delegate_argv(spec, extra, ctx,
                                   original_pr=original_pr,
                                   original_branch=original_branch),
        )
    except Exception as exc:
        trail.error("unexpected_error", str(exc))
        raise
    finally:
        trail.finish()


def main(argv: list[str] | None = None, *, bin_dir: Path) -> int:
    """Run the invocation *argv* describes, and return its exit code.

    `bin_dir` is the entry point's own `ai/bin`; see the module docstring for
    why this is given rather than derived.
    """
    argv = list(sys.argv[1:] if argv is None else argv)

    # Before the interrupt handler and before any parse: `ai/bin/pr
    # --tool-schema` answers a question about syntax, and that must not
    # resolve a context or take a lock to do it. The MCP server no longer
    # runs this to discover the tool — it imports `cli.schema.tool_schema`
    # directly — but a reader invoking the binary directly still needs this
    # answered up front.
    #
    # A subcommand named ahead of the flag answers for itself. `pr ci
    # --tool-schema` reports `CIDomain` where `pr --tool-schema` reports the
    # union, which declares no output schema at all because eight of the nine
    # print prose. Without this the two documents are reachable by one string
    # and the narrower one by none, so a skill cannot cite the contract it
    # depends on.
    if "--tool-schema" in argv:
        named = next((a for a in argv if not a.startswith("-")), "")
        doc = subcommand_schema(named) if named else None
        json.dump(doc or tool_schema(), sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    core.proc.install_stop_handler(core.log.interrupted)

    # Two-pass parse: extract global flags first, then route the subcommand.
    # Argparse subparsers swallow flags after the subcommand name, so
    # `pr rebase --repo-dir /path` would lose --repo-dir without this.
    global_args, remaining = build_global_parser().parse_known_args(argv)

    parser = _build_parser()

    args, extra = parser.parse_known_args(remaining)
    args.repo_dir = global_args.repo_dir
    args.branch = global_args.branch
    args.pr = global_args.pr

    usage = _build_usage()

    if args.help:
        print(usage)
        return 0

    if not args.command:
        print(usage, file=sys.stderr)
        return 0

    # `pr create` is a one-command process: an inherited GIT_DIR (it is
    # exported when a hook invokes us) would make every git call from here on
    # answer for another repository — context resolution, and helpers that
    # take no env, like git_remote.remote_branch_ref_exists and git.push.
    # Clearing it here, ahead of the first git call, is the single owner; it is
    # safe because nothing else runs in this process after the command.
    if args.command == "create":
        for name in gitenv.GIT_ENV_OVERRIDES:
            os.environ.pop(name, None)

    # The one place a push nobody verified is asked about. Every push on this
    # machine passes the global pre-push hook, which records what it is about to
    # send; `pr` is the workbench's git surface and is always somewhere that can
    # print, so this is where the remote is asked whether those pushes landed.
    # Here rather than inside a subcommand: it belongs to no one command, and it
    # runs before the context is resolved so a report about an earlier push is
    # not withheld by a command that goes on to fail for reasons of its own. It
    # costs one failed `stat` when nothing is pending, which is nearly always.
    #
    # Caught because every subcommand passes through here on its way to work
    # that has nothing to do with pushing: an exception escaping a side-feature
    # would take `pr` down whole, and leave it down until the state file was
    # cleared by hand. The warning names the exception, so a bug is still said
    # out loud rather than swallowed.
    try:
        pr.push_intent.reconcile()
    except Exception as exc:
        core.log.warn(f"could not reconcile recorded pushes: {exc}")

    spec = COMMANDS[args.command]
    # The command's own parser prints its own help, in this process. It is
    # asked for the parser rather than run with `--help`, because a delegate
    # `main` does more than parse before argparse ever sees the flag. Keyed on
    # the factory, not on `script`: `create` has a parser and no script.
    if {"-h", "--help"} & set(extra) and cli.dispatch.has_parser_factory(spec.name):
        cli.dispatch.print_delegate_help(spec)
        return 0

    original_pr = getattr(args, "pr", None)
    original_branch = getattr(args, "branch", None)

    # Classify the first positional (if any) as PR or branch when no explicit
    # --pr/--branch flag was given and the command takes a target at all. The
    # delegate is only asked for its flag arity once a bare token exists to be
    # ambiguous about, so the common case (`pr review`, `pr comments --triage`)
    # spawns nothing.
    ambiguous = (original_pr is None and original_branch is None
                 and spec.takes_target)
    if ambiguous and any(not a.startswith("-") for a in extra):
        idx = cli.dispatch.positional_index(extra, cli.dispatch.delegate_value_flags(spec))
        if idx >= 0:
            original_pr, original_branch = pr.context.classify_target(extra[idx])
            extra.pop(idx)

    if global_args.schema_version is not None:
        global_args.schema_version = checked_schema_version(
            global_args.schema_version, args.command, extra,
        )

    # The command's declaration drives all three axes from here. Read after the
    # positional scan above, because that scan is what turns a bare token into
    # the --pr that can escalate the declared depth.
    need = need_for(spec, extra)
    _reject_a_target_that_resolves_to_nothing(
        args.command, extra, need, original_pr, original_branch,
    )

    ctx = pr.context.resolve_at(
        need.depth,
        pr_ref=original_pr,
        branch=original_branch,
        repo_dir=getattr(args, "repo_dir", None),
    )

    # The CLI-only half of the project registry: a repo whose whole workbench
    # use is `pr` never opens a Claude session, so the SessionStart hook never
    # sees it. The root is already resolved, so this costs no subprocess.
    if ctx.worktree_root:
        config.workbench_projects.register(ctx.worktree_root)

    if need.update:
        ctx = pr.sync.update_to_remote(ctx)

    # The lock is acquired before _dispatch so contention costs no trail
    # artifacts.
    #
    # The target's lock only. Which checkout a run writes to is the handler's
    # question, not this one's: `pr review 2973` resolves a worktree here and
    # then reviews in one of its own, so a checkout lock taken at this level
    # would name a tree the run never touches and refuse a concurrent review of
    # a different PR launched from the same directory. Every handler that does
    # write to the resolved checkout takes it — pr-rebase, pr-describe,
    # ci-check, review-threads, and claude-review's plain --self.
    try:
        lock = (
            core.run_lock.acquire(
                ctx.target_dir,
                command=" ".join([SCRIPT] + argv),
                started=pr.state.now_iso(),
            )
            if need.lock else contextlib.nullcontext()
        )
        with lock:
            return _dispatch(args, ctx, extra, global_args,
                             need=need,
                             bin_dir=bin_dir,
                             original_pr=original_pr,
                             original_branch=original_branch)
    except core.run_lock.LockBusy as exc:
        core.run_lock.report_busy(exc)
        return 1
