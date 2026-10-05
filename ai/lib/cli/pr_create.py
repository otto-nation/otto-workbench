"""`pr create`'s parser and handler.

The parser is argparse over the argv the shell already split (D12), so a
quoted title is one token because the shell said so — nothing re-parses a
string. It is also `pr`'s parser factory for create, which is what answers
`pr create --help` and `pr --tool-schema create`.

`--body-file` is read here rather than in `pr.create`: an unreadable file is
a usage error (exit 2, D11), and the orchestration only ever sees the body.
The target comes from `pr`'s global `--branch`/`--repo-dir`, already resolved
into the context — create declares no positional and no target flag.
"""

# doc-group: cli

from __future__ import annotations

import argparse

import pr.context
import pr.create
from core.tool_parser import EXIT_USAGE, ToolParser, read_file_arg

SCRIPT = "pr create"


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
    opts = pr.create.CreateOptions(
        draft=args.draft,
        no_verify=args.no_verify,
        dry_run=args.dry_run,
        base=args.base,
        title=args.title,
        body=body,
        issue=args.issue,
        closes=tuple(args.closes),
    )
    return pr.create.run_create(ctx, opts, trail=trail)
