"""What a `pr` subcommand needs of dispatch before its handler runs.

`Need` is the declaration; `review_need` is the one resolver that reads a mode
flag off an argv. Both were written inside `ai/bin/pr`, where nothing could
import them and no test could reach them without executing the binary.

The mode table itself stays with the handlers it names — a mode is a need and a
callable, and only the need half has a home below the entry point. The
resolvers therefore take the table rather than reaching for one, which is also
what lets a test declare a table of its own.
"""

# doc-group: cli

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from pr import context as pr_context

NONE = pr_context.ContextDepth.NONE
LOCAL = pr_context.ContextDepth.LOCAL
REMOTE = pr_context.ContextDepth.REMOTE


@dataclass(frozen=True)
class Need:
    """What one command needs of dispatch before its handler runs.

    Three independent axes, declared rather than opted out of. Independent
    because they routinely disagree: `rebase` needs `gh` to name its PR but
    must not fetch (it does its own), and `gc` writes nothing to the remote yet
    still locks, because it deletes the directory other runs key on.

    * ``depth`` — how far ``pr_context`` resolves. LOCAL is git alone; REMOTE
      adds the ``gh`` calls that name the repo and the PR.
    * ``update`` — whether to fetch and fast-forward the worktree
      (``update_to_remote``) before the handler runs.
    * ``lock`` — whether to hold the target's run lock for the whole dispatch.
      False only for a command that neither writes ``state.json`` nor touches
      the worktree, so it is safe alongside another run.
    """

    depth: pr_context.ContextDepth
    update: bool
    lock: bool

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
        """
        resolves_nothing = self.depth is pr_context.ContextDepth.NONE
        return self.lock or not resolves_nothing


# What `pr review` needs with no mode flag: a review run, which reads the PR,
# resets the worktree it is about to review, and holds the lock for the whole
# thing. The fetch belongs to this one invocation — it is the only one whose
# subject is the branch's current state.
REVIEW_DEFAULT_NEED = Need(REMOTE, update=True, lock=True)

# What a mode flag needs unless it declares otherwise. Every mode acts on a
# review that already exists on disk, at the commit that review describes, so
# none of them may fast-forward the worktree out from under it: `--summary` and
# `--post` would report or publish a review of a commit the worktree no longer
# sits on, and `--recover` would move HEAD off the SHA it is about to pin a
# throwaway worktree back to. They still resolve the PR and hold the lock.
REVIEW_MODE_NEED = Need(REMOTE, update=False, lock=True)


@dataclass(frozen=True)
class ReviewMode:
    """One of `pr review`'s mutually-exclusive mode flags.

    The single declaration of what a mode is: the exclusivity check, the
    routing in `cmd_review`, the per-argv need resolver, and the
    `--schema-version` gate all read this table rather than each restating the
    set of flags.

    * ``handler`` — what runs locally, or None for a mode `claude-review`
      handles. A `None` handler still belongs here: `--recover` collides with
      the others whoever ends up running it.
    * ``need`` — this mode's dispatch need. Defaults to what acting on an
      existing review takes; a mode that needs less says so.
    * ``schema_versions`` — the row-schema versions this mode can serve, empty
      for a mode with no versioned document to hand a caller.
    """

    handler: Callable[..., int] | None = None
    need: Need = REVIEW_MODE_NEED
    schema_versions: tuple[int, ...] = ()


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
    if "--fix" in given:
        return [flag for flag in found if flag != "--post"]
    return found


def review_need(argv: Sequence[str], modes: Mapping[str, ReviewMode]) -> Need:
    """`review`'s need, which its mode flag decides.

    The one command that declares a callable rather than a constant. A mode
    flag means the subject is a review that already exists, and a bare `review`
    means the subject is the branch — which is the whole of why the fetch
    differs between them. Reading the declaration off the mode table is what
    keeps each mode off the paths it does not need structurally, rather than by
    an exemption someone has to remember.
    """
    found = review_modes(argv, modes)
    return modes[found[0]].need if found else REVIEW_DEFAULT_NEED
