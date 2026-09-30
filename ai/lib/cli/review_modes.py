"""`pr review`'s mutually-exclusive mode flags, and what each one does.

The table and the four handlers it names, in one importable module. They were
written inside `ai/bin/pr`, where `review`'s need — the one command declaration
an argv resolves rather than a constant — could not be stated anywhere a
library module could read it. `cli.registry` imports `need_for` from here for
exactly that reason.

`--post` and `--repair` call `review-post` and `review-rebuild` in this
process, through `core.publishing.call_entry_point` — the seam itself rather
than `cli.dispatch`, which wraps it. These two build their own argv and want
only the call, and reaching for the dispatcher would close a cycle:
`cli.registry` imports this module for the one need an argv resolves.

Both used to be spawns; `--repair` also used to capture the rebuild's stdout
and grep it for a `REVIEW_SUMMARY:` marker, which `cli.review_rebuild` has
never written — the domain is synced from the file the rebuild produced
instead.

Handlers still take `bin_dir`. Nothing in this module spawns any more, but the
four share one signature and `summary` never needed it either; the parameter
is the mode-handler contract rather than a path any of them uses today.
"""

# doc-group: cli

import json
import sys
from collections.abc import Sequence
from pathlib import Path

from cli import needs
from cli.needs import NONE as _NONE
from cli.needs import Need, ReviewMode
from core import log
from core import publishing
from pr import context as pr_context
from pr.review_sync import sync_review_domain
from pr.target import display_repo
from review import listing as review_listing
from review.paths import find_review_file, review_file_path
from review.summary import build_review_summary, json_summary


def post(argv: list[str], ctx: pr_context.ResolvedContext, *,
         bin_dir: Path, **_kw) -> int:
    """Post an existing review directly to GitHub via review-post."""
    pr_num = str(ctx.pr_number) if ctx.pr_number else None
    if not pr_num:
        log.error("Cannot determine PR number")
        return 1

    review_path = find_review_file(ctx.repo, pr_num)
    if not review_path:
        log.error(
            f"No review file found for {display_repo(ctx.repo, ctx.host)}#{pr_num}")
        log.dim("Run: pr review")
        return 1

    post_argv = ["--pr", pr_num, "--review-file", str(review_path)]
    # What this run is publishing for. The review file is found by PR number
    # while the run lock keys on the branch, so naming the branch is what lets
    # review-post tell this run's review from one written by a run the lock
    # never made contend with it.
    if ctx.branch:
        post_argv += ["--expect-ref", ctx.branch]
    if "--submit" in argv:
        post_argv.append("--submit")
        argv = [a for a in argv if a != "--submit"]
    post_argv += argv
    return publishing.call_entry_point("cli.review_post:main", post_argv)


def repair(argv: list[str], ctx: pr_context.ResolvedContext, *,
           bin_dir: Path, **_kw) -> int:
    """Repair broken review artifacts via summary or rebuild."""
    pr_num = str(ctx.pr_number) if ctx.pr_number else None
    if not pr_num:
        log.error("Cannot determine PR number")
        return 1

    review_path = find_review_file(ctx.repo, pr_num)
    if review_path:
        sync_review_domain(ctx, build_review_summary(ctx.repo, pr_num, str(review_path)))
        return 0

    log.info("No review file found, trying rebuild...")
    review_dir = review_file_path(ctx.repo, pr_num).parent
    if not review_dir.is_dir():
        log.error(f"No review directory found: {review_dir}")
        return 1

    rc = publishing.call_entry_point(
        "cli.review_rebuild:main",
        ["--review-dir", str(review_dir), "--pr", pr_num] + list(argv),
    )
    if rc != 0:
        return rc

    # The rebuild writes `review.md` and says nothing on stdout. This used to
    # capture its output and grep it for a `REVIEW_SUMMARY:` marker — a
    # protocol whose only writer was `review`, so on this path it
    # parsed a string nothing emitted and silently updated nothing. The
    # domain is synced from the file the rebuild just wrote instead, which is
    # the same thing `repair` does above when a review file already exists.
    review_path = find_review_file(ctx.repo, pr_num)
    if review_path:
        sync_review_domain(ctx, build_review_summary(ctx.repo, pr_num, str(review_path)))
    return 0


def summary(argv: list[str], ctx: pr_context.ResolvedContext, **_kw) -> int:
    """Output JSON summary of an existing review."""
    pr_num = str(ctx.pr_number) if ctx.pr_number else None
    if not pr_num:
        log.error("Cannot determine PR number")
        return 1

    review_path = find_review_file(ctx.repo, pr_num)
    if not review_path:
        log.error(
            f"No review file found for {display_repo(ctx.repo, ctx.host)}#{pr_num}")
        return 1

    print(json_summary(ctx.repo, pr_num, str(review_path)))
    return 0


def listing(argv: list[str], _ctx: pr_context.ResolvedContext, *,
            schema_version: int | None = None, **_kw) -> int:
    """List every review in the user's state root.

    The contract other repos read review state through — see
    `ai/lib/review/listing.py` for what a row carries and why it is a query
    rather than a path they derive.

    Two audiences, told apart by the handshake. With `--schema-version` this is
    a consumer, and the versioned document goes to stdout. Without it this is a
    person, and the table goes to stderr with stdout left empty — so a consumer
    that forgets the handshake gets a parse failure from an empty stream rather
    than a table it half-understands.

    The context is unresolved and the parameter is named for it: `--list`
    declares `ContextDepth.NONE`, so there is no repo, branch, or target here
    and nothing in this function may look for one.
    """
    if schema_version is None:
        print("\n".join(review_listing.render_table(review_listing.rows())),
              file=sys.stderr)
        return 0
    json.dump(review_listing.document(schema_version), sys.stdout, indent=2)
    print()
    return 0


# The one mode table. `cli.registry` resolves `review`'s need through it,
# `cmd_review` routes through it, and the two MCP schema helpers derive the
# versioned invocations from it — none of them restates the set of flags.
MODES: dict[str, ReviewMode] = {
    "--post":    ReviewMode(post),
    "--repair":  ReviewMode(repair),
    "--summary": ReviewMode(summary),
    "--recover": ReviewMode(),
    "--list":    ReviewMode(listing,
                            need=Need(_NONE, update=False, lock=False),
                            schema_versions=review_listing.SCHEMA_VERSIONS),
}


def flags_given(argv: Sequence[str]) -> list[str]:
    """The mode flags in *argv*, in table order."""
    return needs.review_modes(argv, MODES)


def need_for(argv: Sequence[str]) -> Need:
    """`review`'s need, which its mode flag decides."""
    return needs.review_need(argv, MODES)


def flags_prose() -> str:
    """The mode flags as an English list, for the exclusivity error."""
    flags = list(MODES)
    if len(flags) == 1:
        return flags[0]
    return f"{', '.join(flags[:-1])}, and {flags[-1]}"
