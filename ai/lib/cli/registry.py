"""Every `pr` subcommand, and the whole of what dispatch needs to know about it.

One spec per subcommand, and the spec is the whole declaration: the help line,
the backing script, the importable handler, what the invocation needs resolved
before that handler runs, and whether a bare token in its argv can name a
target. Four tables in `ai/bin/pr` said those things separately — `_COMMANDS`,
`_CUSTOM`, `_NO_TARGET_COMMANDS` and the mode table — and `_validate_needs`
was the only one of them with a check.

Written as a tuple and keyed afterwards, like `agent.registry`: a literal keyed
by hand spells every subcommand name twice and can drift between the two
spellings. **The tuple's order is the display order** — `pr --help`, the
subparsers and the MCP `command` enum all read it in sequence — so reordering
it is a user-visible change, not a cosmetic one.

`handler` is a `"<module>:<attr>"` string resolved by importlib at dispatch,
not a callable: an eager import would pull every delegate into `pr --help`.
Seven of the nine name an importable function today. `review` and `comments`
are None — their wrappers (`cmd_review`, `cmd_comments`) still live in
`ai/bin/pr` and call `_run_delegate`, which this commit does not move. Filling
those two is T7 commit 4c; an honest None beats a string that would resolve
to the wrong callable.
"""

# doc-group: cli

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from cli import review_modes
from cli.needs import LOCAL, REMOTE, Need


@dataclass(frozen=True)
class CommandSpec:
    """One `pr` subcommand's declaration.

    * ``name`` — the subcommand as the user types it, and this spec's key.
    * ``help`` — the one-line description, in `pr --help` and in the subparser.
    * ``need`` — what dispatch resolves, fetches and locks before the handler
      runs. A command whose axes vary by flag declares a callable over its argv
      instead of a constant; `need_for` reads both shapes, so nothing
      downstream has to know which kind a spec carries. No default: a command
      silent about the three axes is exactly what `validate_needs` refuses, and
      a default would answer for it before the check ever ran.
    * ``handler`` — ``"<module>:<attr>"`` naming the in-process callable, or
      None when the wrapper is still binary-local. A string, not a callable:
      dispatch imports it at call time so `pr --help` does not. Defaulted to
      None because two of the nine cannot honestly point anywhere yet;
      `review` and `comments` set that explicitly, and a test pins every
      value as a literal so a silent default on a new command fails the
      build rather than shipping as None.
    * ``script`` — the backing script's *name* under `ai/bin`, or None for a
      command `pr` runs itself. A name and not a path: under
      WORKBENCH_AI_LIB_DIR this module resolves inside the pinned checkout
      while the entry point's own BIN_DIR does not, so the caller joins the
      name to its own directory rather than being handed one from here.
    * ``takes_target`` — whether a bare token in this command's argv can name a
      PR or a branch. False for a command that always acts on the current
      branch, where every bare token belongs to the flag before it.

      Defaulted where ``need`` is not, and the asymmetry is deliberate but it
      is *not* because True is the safer side — it is the side that ate
      `pr create --title`. What makes the default acceptable is that the one
      dangerous combination is already asserted against: a command that is
      scriptless (so `_delegate_value_flags` has no parser to read and the
      scan degrades to "first bare token wins") *and* target-taking is covered
      by `test_a_command_with_no_delegate_declares_no_value_taking_flag`, which
      is parametrized over exactly that set and fails the build the moment one
      of them grows a value-taking flag. `need` has no such check available —
      there is no observable consequence to assert on until dispatch runs — so
      it is refused at construction instead.
    """

    name: str
    help: str
    need: Need | Callable[[Sequence[str]], Need]
    script: str | None = None
    takes_target: bool = True
    handler: str | None = None


_SPECS: tuple[CommandSpec, ...] = (
    # `task pr:create` has no way to accept a target and `create` always acts
    # on the current branch, so the positional scan has nothing to find here —
    # only flag values to swallow. `takes_target=False` is what keeps
    # `pr create --title "…"` from arriving as a dangling --title, and it needs
    # no arity list of its own: parse_pr_flags in lib/ai/pr.sh stays the single
    # source of truth for which of create's flags take a value.
    CommandSpec("create",   "Create a PR (wraps task pr:create)",
                Need(REMOTE, update=False, lock=True), takes_target=False,
                handler="cli.pr_commands:cmd_create"),
    CommandSpec("status",   "Show CI, review, and comment status dashboard",
                Need(LOCAL,  update=False, lock=False),
                handler="cli.pr_commands:cmd_status"),
    CommandSpec("ci",       "Check CI failures",
                Need(REMOTE, update=True,  lock=True),  script="ci-check",
                handler="cli.ci_check:main"),
    # The one spec whose declaration its own argv resolves. `cli.review_modes`
    # owns the table the resolver reads, so this is an ordinary import rather
    # than something the entry point has to supply from above.
    #
    # handler is None: `cmd_review` still lives in `ai/bin/pr` and calls
    # `_run_delegate`. Pointing at `cli.claude_review:main` would skip the
    # --self injection and the mode-flag routing. Filling this is T7 commit 4c.
    CommandSpec("review",   "Run code review",
                review_modes.need_for,                  script="claude-review",
                handler=None),
    # handler is None: `cmd_comments` still lives in `ai/bin/pr` and calls
    # `_run_delegate`. Filling this is T7 commit 4c.
    CommandSpec("comments", "Fetch and manage PR review threads",
                Need(REMOTE, update=True,  lock=True),  script="review-threads",
                handler=None),
    CommandSpec("fix",      "Fix CI + review + comments",
                Need(REMOTE, update=True,  lock=True),
                handler="cli.pr_commands:cmd_fix"),
    CommandSpec("rebase",   "Rebase onto the branch's base",
                Need(REMOTE, update=False, lock=True),  script="pr-rebase",
                handler="cli.pr_rebase:main"),
    CommandSpec("describe", "Revise the PR description",
                Need(REMOTE, update=True,  lock=True),  script="pr-describe",
                handler="cli.pr_describe:main"),
    CommandSpec("gc",       "Clean up stale PR artifacts",
                Need(REMOTE, update=False, lock=True),
                handler="cli.pr_commands:cmd_gc"),
)

COMMANDS: dict[str, CommandSpec] = {s.name: s for s in _SPECS}


def need_for(spec: CommandSpec, argv: Sequence[str]) -> Need:
    """The Need this invocation declares.

    A command whose axes vary by flag declares a callable over its argv instead
    of a constant; every other one declares the constant. One reader for both
    shapes, so nothing downstream has to know which kind a spec carries.
    """
    return spec.need(argv) if callable(spec.need) else spec.need


def validate_needs(commands: dict[str, CommandSpec]) -> None:
    """Raise unless every command declares a Need.

    The check that replaces "remember to edit two opt-out sets": a command that
    declares nothing fails at import, where the old sets silently handed it
    whatever not being listed happened to mean. A raise rather than an assert,
    so the invariant survives -O.

    A callable declaration is resolved over an empty argv and its answer
    checked, so a resolver that returns something other than a Need for the
    plain invocation fails here too rather than at the first dispatch.

    Run at this module's import rather than at the entry point's, so a consumer
    that imports COMMANDS and never runs `pr` — the MCP server, once its
    discovery reads the registry — cannot be handed a registry nothing checked.
    """
    undeclared = sorted(
        name for name, spec in commands.items()
        if not isinstance(need_for(spec, []), Need)
    )
    if undeclared:
        raise RuntimeError(
            "pr: commands declare no dispatch need: " + ", ".join(undeclared)
            + " — add a Need(depth, update=…, lock=…) to each CommandSpec"
        )


validate_needs(COMMANDS)
