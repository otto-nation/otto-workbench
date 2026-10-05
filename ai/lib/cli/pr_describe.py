"""Revise a PR description against the repo's PR template.

Run after the branch stops moving — a description written before the fix passes
describes a PR that no longer exists. The pass is commit-aware: it records the
HEAD it described and whether the result was published, and a repeated run
against an unchanged, published branch is a no-op rather than another AI call,
which is what lets `pr fix` call it unconditionally at the end of every run.
A draft is not published, so the `--post` run after it goes ahead. `--force` ignores the recorded SHA; `--dry-run` prints
the revision instead of applying it.

The edit itself answers to the same publishing gate as every other GitHub write:
without `--post` the revised body is drafted to stderr and the PR is untouched.
`--dry-run` is the narrower request of the two — it prints the revision and
records nothing, where a draft records that the pass ran unpublished. `pr fix`
forwards `--post` to the description for this reason, and forwards nothing else.

The template is resolved by `core.pr_template`, which owns the candidate list
for every caller — this command, `pr create`, and the SessionStart context line.
It checks `pull_request_template.md`, in either case, in `.github/`, the repo
root, and `docs/`, and takes the first that exists. A repo with none of them
gets the built-in fallback (Summary / Changes / Testing only). A differently-named
template, and GitHub's `PULL_REQUEST_TEMPLATE/` directory form, are not detected.

`--title`, `--body`/`--body-file` and `--closes` are the hand overrides
`pr create` also takes. A hand body replaces the AI revision, after the
template check `pr create` applies; `--title` replaces the title (nothing
else changes it); `--closes` appends a closing line, under the `pr create`
contract (a tracker key only on Linear). Any of them runs whatever HEAD
says — explicit intent is never "already done" — though `--title` or
`--closes` alone, at a HEAD already published, skip the AI revision and
edit the current body. Closing references that the old
body carried are re-appended when a revision drops them. `--no-issue` is
accepted and does nothing. The range the AI reads is measured against the
PR's own base, fetched first. `--post` claims the publishing token before any
work (not under `--dry-run`, which publishes nothing), falling back to the
interactive `gh` login; `--closes` is checked ahead of it, so a
ref nothing can close is refused without a token lookup.

Exit codes:
  0  Success (description current, revised, or no PR and nothing asked of one)
  1  Error (no PR for --title/--body/--closes, gh failure, unusable AI output,
     refused --closes or --body, no publishing token under --post)
  2  Usage error (bad flags, unreadable --body-file)

Usage:
  pr-describe                         # revise if HEAD moved since the last pass
  pr-describe --force                 # revise regardless of HEAD
  pr-describe --dry-run               # print the revision, do not push it
  pr-describe --closes 941 --post     # link an issue for auto-close on merge
  pr-describe --title "feat: x" --body-file body.md --post
  pr-describe --repo-dir <path>       # specify worktree directory
"""

# doc-group: cli

import argparse
import sys

import core.log
import core.publishing
import core.run_lock
import pr.close_refs
import pr.context
import pr.describe
import pr.gh_token
import pr.state
from pr.domains import DescribeSummary
from core.tool_parser import EXIT_USAGE, ToolParser, read_file_arg
from core.trail import Trail, add_trail_args


def build_parser() -> ToolParser:
    """This command's parser, before anything has been parsed with it.

    Public because `pr` reads it to learn which of these options consume a
    following token, which is how a bare positional is classified as a PR
    number or a branch.
    """
    parser = ToolParser(
        prog=pr.describe.SCRIPT,
        description="Revise the PR description against the repo's PR template",
        output_schema=DescribeSummary,
    )
    parser.add_argument("--repo-dir", "--worktree", dest="repo_dir", metavar="PATH",
                        help="Git worktree directory")
    parser.add_argument("--branch", metavar="NAME",
                        help="Branch name (injected by pr dispatcher)")
    parser.add_argument("--pr", metavar="NUM|URL",
                        help="PR number or URL (injected by pr dispatcher)")
    parser.add_argument("--force", action="store_true",
                        help="Revise even when HEAD has not moved since the last pass")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the revision instead of applying it")
    parser.add_argument("--post", action="store_true",
                        help="Apply the revision to the PR; without it the edit "
                             "is drafted")
    parser.add_argument("--title", default="", metavar="TEXT",
                        help="Replace the PR title")
    body = parser.add_mutually_exclusive_group()
    body.add_argument("--body", default="", metavar="TEXT",
                      help="Replace the PR body instead of revising it")
    body.add_argument("--body-file", default="", metavar="PATH",
                      help="Read the replacement body from this file")
    parser.add_argument("--closes", action="append", default=[], metavar="ID",
                        help="Issue to close on merge (repeatable)")
    # Accepted and inert: the retired Taskfile updater took it, and rejecting
    # the flag would fail invocations already written with it.
    parser.add_argument("--no-issue", action="store_true", help=argparse.SUPPRESS)
    add_trail_args(parser)
    return parser


def _closes_refused(closes: tuple[str, ...], worktree) -> bool:
    """Print the refusal and answer True when a `--closes` value cannot close."""
    try:
        pr.close_refs.normalise_all(closes, worktree)
    except pr.close_refs.CloseRefError as exc:
        print(str(exc), file=sys.stderr, flush=True)
        return True
    return False


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    body = args.body
    if args.body_file:
        body = read_file_arg("--body-file", args.body_file)
        if body is None:
            return EXIT_USAGE
    opts = pr.describe.DescribeOptions(
        force=args.force, dry_run=args.dry_run, title=args.title, body=body,
        closes=tuple(args.closes),
    )

    ctx = pr.context.resolve(
        repo_dir=args.repo_dir, branch=args.branch, pr_ref=args.pr,
    )

    # A no-op when `pr describe` launched us — same target, same key, already
    # in WORKBENCH_RUN_LOCK. Taken so a direct invocation is guarded too: this
    # rewrites the PR body and the target's state file, both of which a
    # concurrent run reads and writes.
    # Acquired before Trail.start so contention costs no trail artifacts.
    #
    # The checkout is read to describe it and no worktree switch happens, so
    # the resolved tree is the one in play. Tested rather than required:
    # run_describe degrades without one, and a lock is not the place to start
    # refusing runs that already work.
    #
    # Spelled as an explicit test, not `or None`: the two are identical to
    # Python, but validate-worktree-guards reads the AST and only an `if` is
    # visible to it as the deliberate opt-out this is.
    worktree = ctx.worktree_root if ctx.worktree_root else None

    core.run_lock.claim_for_process(
        ctx.target_dir,
        command=" ".join([pr.describe.SCRIPT] + (argv if argv is not None else sys.argv[1:])),
        started=pr.state.now_iso(),
        worktree=worktree,
    )

    # After the lock and before the work, matching every other gated command.
    # `--dry-run` is a narrower request than a draft — it prints the revision
    # and writes no state — so it stays its own flag rather than folding in.
    with core.publishing.run(post=args.post):
        if not args.post and not args.dry_run:
            core.log.info("Draft mode — the PR body is not edited. "
                          "Re-run with --post to apply it; the draft does not "
                          "count as done.")
        # Before the token, as `pr create` orders it: a `--closes` nothing can
        # close is refused with its own error and costs no token lookup.
        # run_describe normalises the raw values again; this is the early
        # refusal, not the copy it uses.
        if ctx.pr_number and worktree and _closes_refused(opts.closes, worktree):
            return 1
        # Before the work, so a run that could never publish costs no AI call.
        # No PR has nothing to publish, a dry run publishes nothing whatever
        # --post says, and no worktree is refused, with guidance, by
        # run_describe itself.
        if args.post and not args.dry_run and ctx.pr_number and worktree \
                and not pr.gh_token.ready_for_publishing(worktree):
            return 1

        trail = Trail.start(
            script=pr.describe.SCRIPT,
            context={"repo": ctx.repo, "pr": ctx.pr_number, "branch": ctx.branch},
            debug=args.debug,
        )
        try:
            return pr.describe.run_describe(ctx, opts, trail=trail)
        finally:
            trail.finish()
