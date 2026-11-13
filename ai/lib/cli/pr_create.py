"""`pr create`'s parser and handler.

The parser is argparse over the argv the shell already split (D12), so a
quoted title is one token because the shell said so — nothing re-parses a
string. It is also `pr`'s parser factory for create, which is what answers
`pr create --help` and `pr --tool-schema create`.

`--body-file` is read here rather than in `pr.create`: an unreadable file is
a usage error (exit 2, D11), and the orchestration only ever sees the body.
The target comes from `pr`'s global `--branch`/`--repo-dir`, already resolved
into the context — create declares no positional and no target flag.
`--skip-review` needs a non-empty reason; a blank value is a usage error
and never reaches `run_create`. The self-review gate is built here from
the resolved repo and branch so `pr.create` never imports the review
pipeline.

Exit codes, remote writes and examples are `CONTRACT` below.
"""

# doc-group: cli

from __future__ import annotations

import argparse
import functools
import sys

import core.command_contract
import pr.context
import pr.create
import review.currency
import review.paths
from core.exit_codes import ExitCode
from core.tool_parser import EXIT_USAGE, ToolParser, read_file_arg

SCRIPT = "pr create"

_PUSH_HOW = (
    "plain git push (never forced) via pr.branch_sync.sync_branch / git.push.push: "
    "git push --set-upstream origin <branch> [--no-verify] when origin has no "
    "refs/heads/<branch>; otherwise git push [--no-verify] origin <branch>. Then "
    "gh pr create --title … --body-file - --assignee @me --base <base> [--draft]."
)
_PUSH_REFUSED = (
    "a refused push stops before any AI call or PR; git.push.report prints the "
    "Resume line. A refused gh pr create leaves the branch already on origin."
)

CONTRACT = core.command_contract.CommandContract(
    summary="Create a pull request for the current branch, or preview it with "
            "--dry-run. Pushes HEAD (plain, never forced) then opens a PR; refuses "
            "if one already exists.",
    groups=(
        core.command_contract.FlagGroup(
            "Publish", ("--draft", "--base", "--closes", "--issue")),
        core.command_contract.FlagGroup(
            "Content", ("--title", "--body", "--body-file")),
        core.command_contract.FlagGroup(
            "Gates", ("--no-verify", "--skip-review", "--dry-run")),
    ),
    remote_effects=(
        core.command_contract.RemoteEffect(
            "", "refs/heads/<branch> on origin, then a new pull request",
            "origin and GitHub", _PUSH_HOW, _PUSH_REFUSED),
        core.command_contract.RemoteEffect(
            "--no-verify", "nothing extra: changes how the push runs", "origin",
            "adds --no-verify to git push, skipping the git pre-push hook; the "
            "self-review gate and the workbench owner check still run",
            "as for the push"),
    ),
    exits=(
        core.command_contract.ExitOutcome(
            ExitCode.OK,
            "PR created (its URL on stdout), or the --dry-run preview printed"),
        core.command_contract.ExitOutcome(
            ExitCode.FAILURE,
            "refused in preflight (a PR already exists for the branch — use pr "
            "describe; run from the default branch; origin/<base> missing or "
            "unfetched; no commits ahead of the base; a --closes the tracker "
            "refuses), no checkout, the self-review gate (no self-review covers "
            "HEAD), the publishing token, the nesting gate, the push (including "
            "behind/diverged), content generation, or gh pr create",
        ),
        core.command_contract.ExitOutcome(
            ExitCode.USAGE,
            "bad flags, unreadable --body-file, empty --skip-review reason"),
        core.command_contract.ExitOutcome(
            ExitCode.TEMPFAIL,
            "another pr run holds this branch or checkout; retry once it finishes"),
    ),
    examples=(
        core.command_contract.Example(
            "pr create --draft", "push the branch and open a draft PR"),
        core.command_contract.Example(
            "pr create --draft --closes 941",
            "the same, closing issue 941 on merge"),
        core.command_contract.Example(
            "pr create --draft --dry-run",
            "print title and body; push nothing, create nothing"),
        core.command_contract.Example(
            'pr create --draft --skip-review "user asked to skip"',
            "skip the self-review gate; the reason is recorded on the trail"),
    ),
    notes=(
        "--dry-run takes no lock, pushes nothing and never calls gh.",
    ),
)


def build_parser() -> ToolParser:
    """This command's parser, before anything has been parsed with it."""
    parser = ToolParser(
        prog=SCRIPT,
        description="Create a pull request for the current branch, or preview "
                    "it with --dry-run",
    )
    parser.add_argument("--draft", action="store_true", help="Open the PR as a draft")
    parser.add_argument("--no-verify", action="store_true",
                        help="Skip the pre-push hook when pushing the branch")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the title and body; push nothing, create nothing")
    # Accepted and inert: nothing prompts for an issue any more, and rejecting
    # the flag would fail invocations already written with it.
    parser.add_argument("--no-issue", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--base", default="", metavar="BRANCH",
                        help="Branch the PR targets (default: the repo's default branch)")
    parser.add_argument("--title", default="", metavar="TEXT",
                        help="Use this title instead of generating one")
    body = parser.add_mutually_exclusive_group()
    body.add_argument("--body", default="", metavar="TEXT",
                      help="Use this body instead of generating one")
    body.add_argument("--body-file", default="", metavar="PATH",
                      help="Read the body from this file instead of generating one")
    parser.add_argument("--issue", default="", metavar="ID",
                        help="Issue to give the description context about; closes nothing")
    parser.add_argument("--closes", action="append", default=[], metavar="ID",
                        help="Issue to close on merge (repeatable)")
    parser.add_argument(
        "--skip-review", default=None, metavar="REASON",
        help="Skip the self-review gate; REASON is recorded on the trail",
    )
    return parser


def cmd_create(
    argv: list[str], ctx: pr.context.ResolvedContext, *, trail=None, **_kw,
) -> int:
    """Parse *argv* and open, or preview, the PR for ``ctx.branch``.

    *trail* is the one `cli.pr` started for this dispatch, passed on so the
    push records on it rather than nowhere.
    """
    args = build_parser().parse_args(argv)
    body = args.body
    if args.body_file:
        body = read_file_arg("--body-file", args.body_file)
        if body is None:
            return EXIT_USAGE
    skip_review = args.skip_review
    if skip_review is not None:
        skip_review = skip_review.strip()
        if not skip_review:
            print(
                "✗ --skip-review needs a reason — say why the self-review "
                "is being skipped",
                file=sys.stderr, flush=True,
            )
            return EXIT_USAGE
    else:
        skip_review = ""
    opts = pr.create.CreateOptions(
        draft=args.draft,
        no_verify=args.no_verify,
        dry_run=args.dry_run,
        base=args.base,
        title=args.title,
        body=body,
        issue=args.issue,
        closes=tuple(args.closes),
        skip_review=skip_review,
    )
    self_review = functools.partial(
        review.currency.self_review_verdict,
        review.paths.self_review_file_path(ctx.repo, ctx.branch),
    )
    rc = pr.create.run_create(ctx, opts, trail=trail, self_review=self_review)
    return rc
