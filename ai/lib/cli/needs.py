"""What a `pr` subcommand needs of dispatch before its handler runs.

`Need` is the declaration; `review_need` is the one resolver that reads a mode
flag off an argv. Both were written inside `ai/bin/pr`, where nothing could
import them and no test could reach them without executing the binary.

The mode table itself lives in `cli.review_modes`, beside the handlers it
names. The resolvers here still take a table rather than reaching for one:
`review_modes` would otherwise have to be imported from below it, and taking it
as an argument is also what lets a test declare a table of its own.
"""

# doc-group: cli

import dataclasses
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

import core.run_lock
import pr.context

NONE = pr.context.ContextDepth.NONE
LOCAL = pr.context.ContextDepth.LOCAL
REMOTE = pr.context.ContextDepth.REMOTE


@dataclass(frozen=True)
class Need:
    """What one command needs of dispatch before its handler runs.

    Three independent axes, declared rather than opted out of. Independent
    because they routinely disagree: `rebase` needs `gh` to name its PR but
    must not fetch (it does its own), and `gc` writes nothing to the remote yet
    still locks, because it deletes the directory other runs key on.

    * ``depth`` — how far ``pr.context`` resolves. LOCAL is git alone; REMOTE
      adds the ``gh`` calls that name the repo and the PR. LOCAL never learns a
      branch's PR number, even when one already exists — `resolve_local`'s
      `pr_number` is unconditionally None, so a caller reaching for LOCAL to
      skip a repo-naming `gh` call also loses PR-number discovery, silently.
    * ``update`` — whether to fetch and fast-forward the worktree
      (``update_to_remote``) before the handler runs.
    * ``locks`` — the run locks held for the whole dispatch, as ``core.run_lock``
      scopes: ``TARGET`` (this PR is being worked on), ``CHECKOUT`` (this
      working tree is being written to), a ``Global`` (only one of these may run
      on the machine). ``NO_LOCK`` only for a command that neither writes
      ``state.json`` nor touches the worktree, so it is safe alongside another
      run. `cli.locks` takes them, and its docstring tables every command's.
    """

    depth: pr.context.ContextDepth
    update: bool
    locks: frozenset[core.run_lock.LockScope]

    def __post_init__(self) -> None:
        # Frozen, so equality and hashing hold whatever set literal a caller wrote.
        object.__setattr__(self, "locks", frozenset(self.locks))

    @property
    def records_a_trail(self) -> bool:
        """Whether this invocation is one the audit trail is for.

        Resolving nothing and holding no lock is the shape of a query: it names
        no repo, PR, or branch to record the run against, and nothing it does
        is unsafe alongside another run, because it reads the state root and
        prints. There is no action to trace afterwards.

        The trail is otherwise unconditional, and this is the one hole in it
        because the listing is built to be polled. Two records a tick is more
        expensive than the query itself, and it lands in the file every
        `otto-log` query then has to read — see `trail.TRAIL_KEEP_MONTHS`,
        which bounds that file for the writers this does not cover.

        A global lock alone does not count. It names no repo, PR or branch to
        record against, and the command that holds one — `pr batch run` —
        keeps a trail of its own.
        """
        resolves_nothing = self.depth is pr.context.ContextDepth.NONE
        holds_a_target = any(not isinstance(s, core.run_lock.Global) for s in self.locks)
        return holds_a_target or not resolves_nothing


# The lock sets commands declare. Named once so a declaration reads as one of a
# few shapes rather than as a set literal each time.
NO_LOCK = core.run_lock.NO_LOCK
TARGET_ONLY = frozenset({core.run_lock.TARGET})
TARGET_AND_CHECKOUT = frozenset({core.run_lock.TARGET, core.run_lock.CHECKOUT})

# What `pr review` needs with no mode flag: a review run, which reads the PR
# and holds its target for the whole thing. The tree it reviews in is the PR's
# own, set up later and locked by `core.run_lock.lock_checkout` once known. The
# fetch belongs to this one invocation — it is the only one whose subject is
# the branch's current state.
REVIEW_DEFAULT_NEED = Need(REMOTE, update=True, locks=TARGET_ONLY)

# What `pr review --self` needs: the same run against the local branch, which
# has no PR to name. Its subject is the working tree, so nothing it resolves
# comes from the network — the repo label it does need is the one
# `repo_identity_from_origin` reads off the git remote. That label is not
# byte-identical to what `gh repo view` would return: it folds case and drops
# the host (see `pr.target.RepoIdentity`), which is fine for what a self-review
# uses it for.
#
# LOCAL rather than REMOTE because a self-review is the pass that runs *before*
# a PR exists, routinely on a branch the remote has never seen. Spending a
# `gh` call to name a repo git already names made the pre-PR gate fail for
# reasons that have nothing to do with the branch: an exhausted GraphQL budget,
# an expired token, a plane. `resolve_at` still escalates to REMOTE if a `--pr`
# is passed, so `--self --pr` is not silently downgraded.
# It holds the checkout too: its subject is the working tree, and a `--fix`
# pass commits to it.
REVIEW_SELF_NEED = Need(LOCAL, update=True, locks=TARGET_AND_CHECKOUT)

# What a mode flag needs unless it declares otherwise. Every mode acts on a
# review that already exists on disk, at the commit that review describes, so
# none of them may fast-forward the worktree out from under it: `--summary` and
# `--post` would report or publish a review of a commit the worktree no longer
# sits on, and `--recover` would move HEAD off the SHA it is about to pin a
# throwaway worktree back to. They still resolve the PR and hold the lock.
REVIEW_MODE_NEED = Need(REMOTE, update=False, locks=TARGET_ONLY)


@dataclass(frozen=True)
class ReviewMode:
    """One of `pr review`'s mutually-exclusive mode flags.

    The single declaration of what a mode is: the exclusivity check, the
    routing in `cmd_review`, the per-argv need resolver, and the
    `--schema-version` gate all read this table rather than each restating the
    set of flags.

    * ``handler`` — what runs locally, or None for a mode `review`
      handles. A `None` handler still belongs here: `--recover` collides with
      the others whoever ends up running it.
    * ``need`` — this mode's dispatch need. Defaults to what acting on an
      existing review takes; a mode that needs less says so.
    * ``schema_versions`` — the row-schema versions this mode can serve, empty
      for a mode with no versioned document to hand a caller.
    * ``help`` — one-line description of the mode, for generated CLI reference.
    """

    handler: Callable[..., int] | None = None
    need: Need = REVIEW_MODE_NEED
    schema_versions: tuple[int, ...] = ()
    help: str = ""


def review_modes(argv: Sequence[str], modes: Mapping[str, ReviewMode]) -> list[str]:
    """The mode flags in *argv*, in *modes* order.

    The one place that reads a mode off an argv, so the exclusivity check, the
    routing, the need resolver, and the error that quotes the invocation back
    all agree about what was asked for.

    `--post` is a mode on its own — publish the review already on disk — and a
    modifier beside `--fix`, where it means publish what this run produces:
    post the findings, and push the commit the fix pass makes. Those are
    different requests, and only the first is a mode. Reading `--fix --post` as
    one would route to the poster, silently drop the fix pass, and publish a
    review nobody asked to publish.
    """
    given = set(argv)
    found = [flag for flag in modes if flag in given]
    if has_flag(argv, "--fix"):
        return [flag for flag in found if flag != "--post"]
    return found


def has_flag(argv: Sequence[str], flag: str) -> bool:
    """Whether *argv* passes the long option *flag*, spelled any way argparse accepts.

    argparse takes ``--flag=value`` and any unambiguous prefix (``--dry`` for
    ``--dry-run``). A token that is a prefix of *flag* and still parses can
    only be *flag*: a prefix shared with another option is refused as
    ambiguous before anything runs. Stops at ``--``, after which every token
    is positional.
    """
    for token in argv:
        if token == "--":
            return False
        name = token.split("=", 1)[0]
        if len(name) > 2 and name.startswith("--") and flag.startswith(name):
            return True
    return False


def review_need(argv: Sequence[str], modes: Mapping[str, ReviewMode]) -> Need:
    """`review`'s need, which its mode flag decides.

    One of the commands that declare a callable rather than a constant. A mode
    flag means the subject is a review that already exists, and a bare `review`
    means the subject is the branch — which is the whole of why the fetch
    differs between them. Reading the declaration off the mode table is what
    keeps each mode off the paths it does not need structurally, rather than by
    an exemption someone has to remember.

    `--self` is not in that table: it selects the subject rather than acting on
    an existing review, and it composes with `--fix` and `--post`, which a mode
    may not. It still lowers the depth, because a branch with no PR is the one
    subject `gh` cannot name.
    A mode `review` performs itself (one with no local handler — `--recover`)
    under `--self` writes the working tree, so it holds the checkout too.
    """
    found = review_modes(argv, modes)
    self_review = has_flag(argv, "--self")
    if found:
        mode = modes[found[0]]
        if self_review and mode.handler is None and mode.need.locks:
            return dataclasses.replace(
                mode.need, locks=mode.need.locks | {core.run_lock.CHECKOUT})
        return mode.need
    if self_review:
        return REVIEW_SELF_NEED
    return REVIEW_DEFAULT_NEED
