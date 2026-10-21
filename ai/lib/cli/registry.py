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
Every spec names an importable function, resolved and called through
`core.publishing.call_entry_point` — by `cli.dispatch` for most of them, and
directly by `ai/bin/pr`'s `cmd_review`/`cmd_comments` and by
`cli.review_modes`'s `post`/`repair` for the rest.

`script` outlives the spawn it used to name. Nothing in `pr` runs it any
more — dispatch imports `handler` instead — but MCP still executes the shim
by path, so the field is the declaration of which `ai/bin` name that is. The
command/domain/phase join in `tests/cli_join_test.py` is what keeps it from
going stale now that no `pr` code path would notice if it did.
"""

# doc-group: cli

import json
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import batch.store
import batch.targets
import cli.review_modes
import pr.context
import pr.target
from cli.needs import (
    LOCAL, NO_LOCK, NONE, REMOTE, TARGET_AND_CHECKOUT, TARGET_ONLY, Need,
    ci_need, comments_need, has_flag, rebase_need,
)


@dataclass(frozen=True)
class CommandSpec:
    """One `pr` subcommand's declaration.

    * ``name`` — the subcommand as the user types it, and this spec's key.
    * ``help`` — the one-line description, in `pr --help` and in the subparser.
    * ``need`` — what dispatch resolves, fetches and locks before the handler
      runs. A command whose axes vary by flag declares a callable over its argv
      instead of a constant; `need_for` reads both shapes, so nothing
      downstream has to know which kind a spec carries. No default: a command
      silent about the four axes is exactly what `validate_needs` refuses, and
      a default would answer for it before the check ever ran. (`Need` itself
      refuses CREATE at LOCAL and NONE, so no declaration can carry it.)
    * ``handler`` — ``"<module>:<attr>"`` naming the in-process callable, or
      None when the wrapper is still binary-local. A string, not a callable:
      dispatch imports it at call time so `pr --help` does not. Defaulted to
      None so a new command that forgets to name one fails a test that pins
      every value as a literal, rather than shipping as None. `review` and
      `comments` both have handlers.
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
    * ``parses_own_argv`` — whether a command `pr` runs itself has a parser of
      its own, subcommands included. Its empty subparser then declares no
      `-h`, so `pr batch plan -h` reaches the batch parser instead of argparse
      answering with a usage line naming none of its subcommands. False for
      the commands that ignore their argv: with no `-h` of their own they
      would run on `-h`.
    * ``parser`` — ``"<module>:<attr>"`` naming the argparse factory for a
      command that parses its own argv. Dispatch asks this parser whether
      the command's argv is help before taking locks, so a live batch still
      prints `pr batch run --help` and a `--help` consumed as a flag value
      does not skip the lock. None for every other command.
    """

    name: str
    help: str
    need: Need | Callable[[Sequence[str]], Need]
    script: str | None = None
    takes_target: bool = True
    handler: str | None = None
    parses_own_argv: bool = False
    parser: str | None = None


# `ci_need`, `comments_need` and `rebase_need` live in `cli.needs` because the
# delegates import them to pass `worktree=` to `pr.context.resolve`. These two
# are read only by this table, so they stay beside it.
def _create_need(argv: Sequence[str]) -> Need:
    """`create`'s need: a `--dry-run` pushes and creates nothing, so it takes no lock."""
    locks = NO_LOCK if has_flag(argv, "--dry-run") else TARGET_AND_CHECKOUT
    return Need(REMOTE, update=False, locks=locks)


# The batch subcommands that start or continue a run, and so must be alone
# over each repo they cover.
_BATCH_EXCLUSIVE = frozenset({"run", "resume"})


def _flag_values(argv: Sequence[str], name: str) -> list[str]:
    """Values of *name* in *argv*, accepting `--flag DIR` and `--flag=DIR`."""
    values: list[str] = []
    prefix = f"{name}="
    awaiting = False
    for token in argv:
        if awaiting:
            awaiting = False
            values += [token] if token != "--" and not token.startswith("-") else []
            continue
        if token == name:
            awaiting = True
            continue
        if token.startswith(prefix) and token[len(prefix):]:
            values.append(token[len(prefix):])
    return values


def _origin_key(directory: str) -> str | None:
    """`repo_key_from_origin(*directory*)`, or None when it cannot be read."""
    try:
        return pr.target.repo_key_from_origin(directory)
    except OSError:
        return None


def _keys_from_plan(path: str) -> set[str]:
    """Origin keys recorded on each row of a saved plan file."""
    data = json.loads(Path(path).read_text())
    keys: set[str] = set()
    for row in data.get("rows") or []:
        if not isinstance(row, dict):
            continue
        key = row.get("repo_key") or ""
        if not key:
            key = _origin_key(row.get("repo_dir") or "") or ""
        if key:
            keys.add(key)
    return keys


def _batch_keys_for_run(argv: Sequence[str]) -> set[str]:
    keys: set[str] = set()
    for directory in _flag_values(argv, "--checkout"):
        if key := _origin_key(directory):
            keys.add(key)
    for path in _flag_values(argv, "--plan"):
        keys.update(_keys_from_plan(path))
    return keys


def _batch_keys_for_resume(argv: Sequence[str]) -> set[str]:
    positionals = [a for a in argv if not a.startswith("-")]
    run_id = positionals[1] if len(positionals) > 1 else ""
    if run_id:
        return set(batch.targets.run_repo_keys(batch.store.load(run_id)))
    identity = pr.target.repo_identity_from_origin(os.getcwd())
    if identity is None:
        return set()
    found = batch.store.covering_run_id(batch.store.RepoRef(identity.key, identity.label))
    if not found:
        return set()
    return set(batch.targets.run_repo_keys(batch.store.load(found)))


def _batch_locks(argv: Sequence[str]) -> frozenset:
    if not argv or {"-h", "--help"} & set(argv):
        return NO_LOCK
    sub = next((a for a in argv if not a.startswith("-")), "")
    if sub not in _BATCH_EXCLUSIVE:
        return NO_LOCK
    keys = _batch_keys_for_run(argv) if sub == "run" else _batch_keys_for_resume(argv)
    return frozenset(batch.store.batch_lock(k) for k in sorted(keys))


def _batch_need(argv: Sequence[str]) -> Need:
    """`batch`'s need: `run` and `resume` hold one lock per repo they cover; the rest take nothing.

    Empty argv, no subcommand, or `-h`/`--help` anywhere takes nothing and
    does no I/O. A failure to compute keys also takes nothing: the handler
    then refuses if the chosen run's repos are not held. `resolve` and
    `cancel` declare nothing: they write the inbox and take the run resource
    as `applier` only when no holder is draining, never the per-repo lock, so
    they reach a live run. `status`, `next` and `plan` only read.
    """
    try:
        return Need(NONE, update=False, locks=_batch_locks(argv))
    except Exception:
        return Need(NONE, update=False, locks=NO_LOCK)


_SPECS: tuple[CommandSpec, ...] = (
    # `create` always acts on the current branch: its target comes from the
    # global `--branch`/`--repo-dir`, already resolved into the context, and it
    # declares no positional. `takes_target=False` keeps the positional scan
    # from reading a flag value — `pr create --title 3057` — as a PR target.
    # Its need varies by flag, like review's: a preview holds no run lock.
    CommandSpec("create",   "Create a PR, or preview it with --dry-run",
                _create_need, takes_target=False,
                handler="cli.pr_create:cmd_create"),
    CommandSpec("status",   "Show CI, review, and comment status dashboard",
                Need(LOCAL,  update=False, locks=NO_LOCK),
                handler="cli.pr_commands:cmd_status"),
    CommandSpec("ci",       "Check CI failures",
                ci_need,  script="ci-check",
                handler="cli.ci_check:main"),
    # Like create, a spec whose declaration its own argv resolves. `cli.review_modes`
    # owns the table the resolver reads, so this is an ordinary import rather
    # than something the entry point has to supply from above.
    #
    # The handler is the delegate, not `ai/bin/pr`'s `cmd_review` wrapper:
    # `--self` injection and mode-flag routing are the entry point's argv
    # shaping, and a consumer reading this field wants the callable that
    # performs the command.
    CommandSpec("review",   "Run code review",
                cli.review_modes.need_for,              script="review",
                handler="cli.review_entry:main"),
    CommandSpec("comments", "Fetch and manage PR review threads",
                comments_need,  script="review-threads",
                handler="cli.review_threads:main"),
    CommandSpec("fix",      "Fix CI + review + comments",
                Need(REMOTE, update=True,  locks=TARGET_AND_CHECKOUT,
                     worktree=pr.context.WorktreeNeed.CREATE),
                handler="cli.pr_commands:cmd_fix"),
    CommandSpec("rebase",   "Rebase onto the branch's base",
                rebase_need,  script="pr-rebase",
                handler="cli.pr_rebase:main"),
    # A plain leased push of HEAD. Git alone answers it, so LOCAL; an explicit
    # --pr still escalates through resolve_at. No backing script: the handler
    # and its parser are the whole command, as with create.
    CommandSpec("push",     "Push HEAD with a lease on the remote head you expect",
                Need(LOCAL,  update=False, locks=TARGET_AND_CHECKOUT),
                handler="cli.pr_push:main"),
    CommandSpec("describe", "Revise the PR description",
                Need(REMOTE, update=True,  locks=TARGET_AND_CHECKOUT),  script="pr-describe",
                handler="cli.pr_describe:main"),
    # Orchestrates other commands across many PRs, so it resolves no target of
    # its own. `run` and `resume` hold one batch lock per repo they cover (see
    # `_batch_need`) and every item's TARGET; children pass through on a marker
    # naming only their own item. The run holds a per-run lock under the batch
    # state root.
    CommandSpec("batch",    "Run rebase, CI fixes, comments and self-review across my open PRs",
                _batch_need, takes_target=False,
                handler="cli.pr_batch:cmd_batch", parses_own_argv=True,
                parser="cli.pr_batch:build_parser"),
    CommandSpec("gc",       "Clean up stale PR artifacts",
                Need(REMOTE, update=False, locks=TARGET_ONLY),
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
    plain invocation fails here too rather than at the first dispatch. That
    resolves one argv only, so it checks the shape and nothing flag-specific:
    the CREATE-needs-REMOTE invariant is `Need.__post_init__`'s, which holds
    for every flag a resolver is ever called with.

    Run at this module's import rather than at the entry point's, so a consumer
    that imports COMMANDS and never runs `pr` — the MCP server, once its
    discovery reads the registry — cannot be handed a registry nothing checked.
    """
    undeclared = [
        name for name, spec in commands.items()
        if not isinstance(need_for(spec, []), Need)
    ]
    if undeclared:
        raise RuntimeError(
            "pr: commands declare no dispatch need: " + ", ".join(undeclared)
            + " — add a Need(depth, update=…, locks=…) to each CommandSpec"
        )


validate_needs(COMMANDS)
