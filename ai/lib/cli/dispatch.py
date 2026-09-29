"""Call a `pr` subcommand's handler in this process.

`pr` used to run its delegates as child processes: build an argv, spawn
`ai/bin/<script>`, read the returncode. This module is what replaced the
spawn, and its job is to keep the two properties the process boundary was
providing for free — because those were load-bearing, and nothing else was
holding them.

**However a handler ends, the caller gets an int.** A child that called
`sys.exit` was still just a returncode to its parent. In-process, that same
`sys.exit` is a `SystemExit` unwinding through `pr` itself: a review that
exits 0 because the operator declined a prompt would take the whole of
`pr fix` with it, skipping the CI and describe passes and reporting success.

**What a handler publishes is scoped to that handler.** `publishing` is a
process global. Five entry points call `enable()` when their own `--post`
says so, and as separate processes that was the end of it. In one process,
`pr fix`'s review pass opening the gate would leave it open for the describe
pass, which would then edit the PR body nobody asked it to post.

Both are enforced by `core.publishing.call_entry_point`, which every caller
invokes directly — there is deliberately no `dispatch.call` alias. Two names
for one seam means a test has to know which one its subject reached for, and
a patch on the wrong one passes while testing nothing. The machinery is at
layer 1 because `review.invoke` needs it too and cannot import this package.

What lives here is the argv side: which flags a delegate is told to resolve,
and how a bare token in its argv is classified before anyone knows what the
delegate's flags mean.

Neither property is the delegate's to maintain. A handler that forgets either
is still correct, and a new one cannot reintroduce the leak by omission.
"""

# doc-group: cli

from __future__ import annotations

import importlib

from cli.registry import CommandSpec
from core import publishing
from core import tool_parser
from pr import context as pr_context

# The parser factory that answers "which of this command's options consume a
# following token", per subcommand. Not a CommandSpec field: the three
# scriptless commands have no delegate parser at all — their parser is one of
# the entry point's own subparsers — so a field would be filled for five
# entries and structurally empty for three, which is the two-meanings-in-one-
# field shape the handler seam was careful to avoid.
#
# Not derivable from `handler` either, though four of the five would work:
# `review`'s handler is the wrapper that injects `--self` and routes the mode
# flags, and it lives in a different module than the parser whose arity is
# being asked about.
PARSER_FACTORIES = {
    "ci": "cli.ci_check:build_parser",
    "review": "cli.claude_review:build_parser",
    "comments": "cli.review_threads:build_parser",
    "rebase": "cli.pr_rebase:build_parser",
    "describe": "cli.pr_describe:build_parser",
}


def target_flags(ctx: pr_context.ResolvedContext, *,
                 original_pr: str | None = None,
                 original_branch: str | None = None) -> list[str]:
    """The one target flag a callee is told to resolve, in priority order.

    One owner rather than a copy at each call site: a callee that resolves a
    different target than its caller computes a different lock key and takes
    a second lock on the same checkout, which is the contention the run lock
    exists to prevent. Naming the target is what keeps the two agreeing.

    Here rather than in `cli.pr_commands`, where it lived while the binary's
    `_run_delegate` was its second caller: every caller now reaches it
    through `delegate_argv`, and argv construction is this module's subject.
    """
    if original_pr is not None:
        return ["--pr", str(original_pr)]
    if ctx.pr_number is not None:
        return ["--pr", str(ctx.pr_number)]
    if original_branch is not None:
        return ["--branch", original_branch]
    if ctx.branch:
        return ["--branch", ctx.branch]
    return []


def split_argv(argv: list[str]) -> tuple[list[str], list[str]]:
    """Split argv into (positionals, flags)."""
    positionals = [a for a in argv if not a.startswith("-")]
    flags = [a for a in argv if a.startswith("-")]
    return positionals, flags


def delegate_value_flags(spec: CommandSpec) -> frozenset[str]:
    """Which of *spec*'s delegate's options consume a following value.

    The delegate's own parser is the single source of truth, and it is an
    import away. This used to spawn the delegate to ask the same question
    through a pipe; `core.tool_parser`'s module docstring carries what that
    cost and why it is gone.

    Imported at call time rather than at module scope: `pr --help` must load
    no delegate (test_pr_help_imports_no_delegate), and all five together
    cost +48-61 ms on an 83 ms base. Only the one command being dispatched is
    loaded, and dispatch is about to import it anyway.

    An import that fails is left to propagate. A delegate whose module will
    not import cannot run either, and degrading here would misclassify the
    target and then fail dispatch two lines later — two confusing errors in
    place of one honest traceback.

    Commands with no delegate answer empty, which is the arity-blind scan
    `positional_index` degrades to.
    """
    factory = PARSER_FACTORIES.get(spec.name)
    if factory is None:
        return frozenset()
    return frozenset(tool_parser.value_taking_options(resolve(factory)()))


def positional_index(extra: list[str], value_flags: frozenset[str]) -> int:
    """Index in *extra* of the first bare positional, or -1 if there is none.

    Options are skipped, and so is the token after an option that takes a
    value — that token is the flag's argument, not the command's target.
    The ``--flag=value`` form carries its own value and skips nothing.
    """
    consumed = False
    for i, token in enumerate(extra):
        if consumed:
            consumed = False
            continue
        if token.startswith("-"):
            consumed = token in value_flags
            continue
        return i
    return -1


def print_delegate_help(spec: CommandSpec) -> None:
    """Print *spec*'s delegate's own help, in this process.

    `pr <command> --help` is answered by the delegate, because a delegating
    subparser declares no flags of its own. This used to spawn the script
    with `--help` and let argparse exit; it asks the parser directly instead,
    because a delegate `main` resolves context and claims a run lock before
    argparse ever sees the flag — in-process, running one to print its usage
    would take a lock to answer a question about syntax.

    A command with no delegate parser prints nothing, and has already been
    excluded by its caller: those declare their own flags, so argparse
    answers for them.
    """
    factory = PARSER_FACTORIES.get(spec.name)
    if factory is None:
        return
    resolve(factory)().print_help()


def resolve(handler: str):
    """Import a ``"<module>:<attr>"`` handler and return the callable.

    Resolved at call time, not at import: eagerly importing every handler
    would charge `pr --help` and `pr status` for the whole library, which is
    the cost `cli/registry.py` keeps handler as a string to avoid.
    """
    module_name, attr = handler.split(":", 1)
    return getattr(importlib.import_module(module_name), attr)


def delegate_argv(spec: CommandSpec, argv: list[str],
                  ctx: pr_context.ResolvedContext, *,
                  original_pr: str | None = None,
                  original_branch: str | None = None) -> list[str]:
    """The argv a delegate is called with, context flags injected.

    Exactly one of --pr or --branch (never both), so the delegate's own
    `pr_context.resolve()` does not hit the mutual-exclusivity check.
    Priority: explicit --pr > resolved PR number > explicit --branch >
    auto-detected branch. The PR number is preferred because it is a stable
    identifier, and because it prevents a mismatched --repo-dir/--branch pair
    when the worktree is on a different branch than the target.

    The delegate still resolves its own context from these flags rather than
    being handed `ctx`. That is what the subprocess did, and a handler that
    took a resolved context instead would be a different contract than the
    one `ai/bin/<script>` still honours for its direct callers — the flags
    are the interface both paths share.
    """
    injected: list[str] = []
    if ctx.worktree_root:
        injected += ["--repo-dir", str(ctx.worktree_root)]
    injected += target_flags(ctx, original_pr=original_pr,
                             original_branch=original_branch)
    return injected + list(argv)

