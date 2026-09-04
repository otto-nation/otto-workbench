"""One way to run git, and the reads every caller was hand-rolling.

`ai/` invoked `git` as a literal argv head in 131 places across 18 files, and
each one re-decided the same four things: whether to pass `-C` or `cwd=`,
whether to capture, whether a non-zero exit is a failure or an answer, and what
to do with stderr. The spread is why a fix applied to one call site — a
timeout, a retry, quoting non-ASCII paths — was never a fix for the other
hundred and thirty.

The runner is `run`, and `out`, `ok` and `lines` are the three shapes callers
actually wanted from it. Below them sit the reads that appeared at two or more
call sites — `head_sha`, `current_branch`, `is_dirty`, `commit_exists`; a read
used once belongs at its call site, spelled out with `run`.

| Call | What it gives you |
|---|---|
| `run(*args, cwd=, config=)` | The full `CmdResult`. Never raises on a non-zero exit — `diff --quiet`, `cat-file -e` and `rev-parse --verify` all answer a question with theirs. |
| `out(*args, default="")` | Stripped stdout, or `default` when git exited non-zero. |
| `ok(*args)` | Whether git exited cleanly, for the subcommands that answer a question that way. |
| `lines(*args)` | Stdout split into non-empty lines. |

`abbrev` is the one call here that runs no git at all: it shortens a sha already
in hand for display, the way `head_sha(short=True)` shortens one it just asked
for. How much of a sha a reader is shown is a convention this module owns, and
before it did, thirty-one call sites each spelled the seven out for themselves.
`bin/local/validate-magic-values` keeps it that way: under `ai/`, slicing
anything named for a sha to a literal length is rejected whatever the length,
since a site free to pick eight is a site free to disagree with the rest.

There is no `timeout` parameter. The bound follows from the subcommand the same
way `core.quotePath` does — `fetch` takes `TRANSFER`, the subcommands that write
the tree or run somebody's hooks run `UNBOUNDED`, and everything else is a
flat-cost metadata read at `LOCAL` — so the knowledge lives with the client that
owns it rather than at every call site, one of which used to pass a number of
its own. Where a single subcommand spans both classes the second argv word
decides: `checkout --theirs <file>` and `stash drop` are named exceptions to the
tier their subcommand otherwise takes.

`config={"key": "value"}` becomes `-c key=value` ahead of the subcommand.
`diff`, `ls-files` and `status` get `core.quotePath=false` by default: git
escapes a non-ASCII path in that output unless told otherwise, and an escaped
name is not a pathspec a later `git add` can resolve — so a fix touching such a
file was staged as nothing and reported as applied. Applying the flag to the
subcommand rather than to each caller is what stops the next call site from
forgetting it.

Callers that still invoke git as literal argv are migrating across; a new one
should go through the client. The one difference to know before moving a call
site is that the client passes the worktree as `cwd` rather than as `git -C`, so
a root that does not exist raises `FileNotFoundError` out of Python before git is
reached rather than coming back as a non-zero exit that `out` and `ok` degrade
away. An absent worktree is a broken caller, not a question git declined to
answer — but a call site relying on the old degradation will start failing
loudly.

`out` returning `default` on a non-zero exit is the one place here that
discards a failure, and it is deliberate: it is what the wrappers it replaces
already did, because most of these reads are questions with a reasonable
"don't know" answer. When the exit code or stderr matters — and for a write it
always does — call `run` and read the `CmdResult`. A yes/no read whose caller
gates destructive or discarding work on the answer belongs in that second group
too: through `out` its "don't know" is spelled the same way as its "no", which
is how `is_dirty` used to report a killed `status` as a clean tree.

Writes are not modelled beyond `run`. Committing and pushing gets an owner of
its own, with the publishing gate over it, rather than a convenience wrapper
here that would turn four gate-less push sites into five.

Depends on `proc`, and on `log` for the one read that has to announce a failure
it absorbs: `is_dirty` answering "dirty" because git never answered would
otherwise be indistinguishable from a genuinely dirty tree. Whether any other
failed read is worth logging stays the caller's decision, and most of them have
already decided it is not.
"""

# doc-group: platform

from __future__ import annotations

from pathlib import Path

from core import log
from core import proc
from core import timeouts
from core.proc import CmdResult

# Subcommands whose output is a list of paths. git escapes a non-ASCII name in
# that output unless told otherwise, and an escaped name is not a pathspec a
# later `git add` can resolve — so a fix touching such a file is staged as
# nothing and reported as done. `review.fix` passed this flag by hand at one of
# the sites that needed it; applying it to the subcommand rather than to the
# caller is what stops the next one from forgetting.
_PATH_LISTING = frozenset({"diff", "ls-files", "status"})

# Subcommands whose cost is the input's rather than the operation's, where any
# fixed bound would report a large repository as a broken one.
#
# `worktree add` materializes every file in the tree — measured at roughly 6,300
# files a second, so a hundred-thousand-file repo needs longer than any tier
# here would give it, and it sits on the default review path rather than a
# fallback. `commit` and `push` run hooks belonging to whatever repository is
# being operated on, which is routinely a secret scan, a linter, or a full test
# suite; this repo's own pre-push runs three gates. Killing a push part-way is
# also the worst available outcome, since it leaves the remote's state in doubt.
#
# `rebase` is both at once: it replays commits — each one a `commit`, hooks
# included — and rewrites the tree between them. `checkout` and `stash` are the
# `worktree add` case without the clone, writing or restoring as much of the
# tree as the switch touches. Killing any of the three mid-run is the same bad
# outcome as a killed push, only locally: a half-replayed rebase, a detached
# index, or a stash entry holding work the tree no longer has.
#
# `add` reads and hashes everything in its scope, and its widest scope is the
# whole tree — `add -A` after a fix pass, `add -u` after a lockfile
# regeneration. Nothing about the argv says how much that is, so the bound would
# be a guess at the repository rather than at the operation.
_UNBOUNDED = frozenset({
    "worktree", "commit", "push", "rebase", "checkout", "stash", "add",
})

# The forms of an `_UNBOUNDED` subcommand whose cost is not the tree's after
# all, keyed on two words because one is not enough to tell them apart.
#
# `checkout --ours|--theirs <paths>` rewrites exactly the paths it is given,
# which during a conflict resolution is one file. `stash drop` deletes a ref and
# writes no tree at all. Both were bounded before their call sites moved onto
# this client, and leaving them unbounded trades a hang that a caller could have
# reported for one it waits out — the failure the tiers above exist to catch,
# arriving on the two calls least able to be a large input.
_LOCAL_FORMS = frozenset({
    ("checkout", "--ours"),
    ("checkout", "--theirs"),
    ("stash", "drop"),
})

# Data-proportional like the above, but over a socket that can genuinely stall,
# so a generous bound still catches a failure that waiting will not fix.
#
# `ls-remote` is the smallest of these by payload and still belongs here: it is
# how `push` confirms a push landed, so a bound tight enough to expire on a slow
# remote would report a landed push as unverified.
_TRANSFER = frozenset({"fetch", "ls-remote"})


def _timeout_for(args: tuple[str, ...]) -> float | None:
    """The bound for this subcommand — see `timeouts` for the tiers.

    Two words where one subcommand spans both cost classes — `_LOCAL_FORMS` —
    and the subcommand alone everywhere else.

    ceiling: the two-word read is a fixed exception list rather than a general
    rule, so `remote get-url` (local) and a `remote update` (network) still
    share a tier. Only `get-url` is called today. Upgrade trigger: when a
    network-side `remote` or `submodule` subcommand is added, give those
    subcommands their own two-word tiers the way `_LOCAL_FORMS` does.
    """
    subcommand = args[0] if args else ""
    if args[:2] in _LOCAL_FORMS:
        return timeouts.LOCAL
    if subcommand in _UNBOUNDED:
        return timeouts.UNBOUNDED
    if subcommand in _TRANSFER:
        return timeouts.TRANSFER
    return timeouts.LOCAL


def _argv(args: tuple[str, ...], config: dict[str, str] | None) -> list[str]:
    """Build the full argv, including any `-c key=value` overrides."""
    settings = dict(config or {})
    if args and args[0] in _PATH_LISTING:
        settings.setdefault("core.quotePath", "false")
    prefix: list[str] = []
    for key, value in settings.items():
        prefix.extend(["-c", f"{key}={value}"])
    return ["git", *prefix, *args]


def run(
    *args: str,
    cwd: str | Path | None = None,
    config: dict[str, str] | None = None,
) -> CmdResult:
    """Run git with *args* in *cwd*, capturing both streams.

    Never raises on a non-zero exit — for git that is routine, since
    `diff --quiet`, `cat-file -e` and `rev-parse --verify` all answer a
    question with their exit code. A timeout arrives the same way, as a
    `CmdResult` carrying `proc.TIMEOUT_RETURNCODE`.

    There is no `timeout` parameter. The bound follows from the subcommand, the
    same way `core.quotePath` does, so that the knowledge lives with the client
    that owns it rather than at every call site.
    """
    return proc.run(_argv(args, config), cwd=cwd, timeout=_timeout_for(args))


def out(
    *args: str,
    cwd: str | Path | None = None,
    default: str = "",
    config: dict[str, str] | None = None,
) -> str:
    """Stripped stdout, or *default* when git exited non-zero."""
    r = run(*args, cwd=cwd, config=config)
    return r.stdout.strip() if r.ok else default


def ok(*args: str, cwd: str | Path | None = None) -> bool:
    """Whether git exited zero, for the subcommands that answer that way."""
    return run(*args, cwd=cwd).ok


def lines(
    *args: str, cwd: str | Path | None = None, config: dict[str, str] | None = None,
) -> list[str]:
    """Stdout split into non-empty lines, or empty when git exited non-zero."""
    return [line for line in out(*args, cwd=cwd, config=config).splitlines() if line]


# How much of a commit sha to show a reader. Seven is what git abbreviates to in
# a small repository and what every surface here had hand-sliced; the number is
# a display choice, not a property of the sha, so it belongs to one function
# rather than to the thirty-one places that were spelling it `sha[:7]`.
_ABBREV = 7


# ── Formatting ──────────────────────────────────────────────────────────────

def abbrev(sha: str) -> str:
    """`sha` abbreviated for display, and "" for a sha nobody recorded.

    Pure — this renders a sha already in hand, where `head_sha(short=True)` asks
    git for one. An empty input stays empty rather than becoming a stub, so a
    caller can still spell its own `or "unknown"` fallback and mean it.
    """
    return sha[:_ABBREV]


# ── Reads ───────────────────────────────────────────────────────────────────

def head_sha(cwd: str | Path | None = None, *, short: bool = False) -> str:
    """The commit HEAD points at, or "" in a repo with no commits."""
    args = ("rev-parse", "--short", "HEAD") if short else ("rev-parse", "HEAD")
    return out(*args, cwd=cwd)


def current_branch(cwd: str | Path | None = None) -> str:
    """The checked-out branch, "HEAD" when detached, or "" on failure.

    Detached is a real state here rather than an error: review worktrees are
    created that way on purpose, and `--abbrev-ref` reports it as "HEAD".
    """
    return out("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd)


def is_dirty(cwd: str | Path | None = None) -> bool:
    """Whether the tree has staged, unstaged, or untracked changes, reading a
    `status` that failed as dirty.

    Porcelain rather than `diff --quiet`: an agent that only adds files —
    tests, fixtures — leaves the tracked diff empty, and a diff-only gate then
    skips the commit while the caller still reports the work as done.

    `run` rather than `out` so the exit code survives. Callers gate destructive
    or discarding work on this answer — one hard-resets the worktree when it
    reads clean, another concludes the fix pass has nothing to commit — so a
    `status` killed by a SIGPIPE, a timeout, or a locked index must not be
    spelled the same way as an empty one. Over-reporting costs a skipped reset
    or a commit git then declines as empty; under-reporting costs the work.
    """
    r = run("status", "--porcelain", cwd=cwd)
    if r.ok:
        return bool(r.stdout.strip())
    log.warn(proc.failure_message(
        f"Could not read the state of {cwd or '.'} — treating it as dirty", r,
    ))
    return True


def commit_exists(sha: str, cwd: str | Path | None = None) -> bool:
    """Whether *sha* resolves to a commit object in this repo."""
    return ok("cat-file", "-e", f"{sha}^{{commit}}", cwd=cwd)


def commits_ahead(
    cwd: str | Path | None = None, *, target_ref: str, rev: str = "HEAD",
) -> int:
    """How many commits *rev* has that *target_ref* does not, and 0 when git
    cannot say.

    *rev* defaults to HEAD for the callers measuring the checked-out branch, and
    is spelled out by the one measuring a commit recorded earlier — a count read
    off HEAD there would describe whatever the worktree moved on to.

    A ref git cannot resolve is 0 rather than an error, which reads as "nothing
    to answer for" at both call sites: `pr rebase` skips the landed signals that
    only mean something for a branch with commits of its own, and the summary
    reports no commits replayed.
    """
    out_ = out("rev-list", "--count", f"{target_ref}..{rev}", cwd=cwd)
    return int(out_) if out_.isdigit() else 0
