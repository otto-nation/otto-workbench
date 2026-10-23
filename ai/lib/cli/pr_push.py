"""Push HEAD to origin with a lease on the remote head the caller expects.

Both `pr push` and `pr rebase --push-only` are lease-guarded pushes of HEAD
through `rebase.commands.push_head` — a rewrite satisfies the lease the same
way a fast-forward does, so `pr push` is not "not a force push". Use
`pr rebase --push-only` after a rebase; use `pr push` for anything else. It
records where the branch stands (`PushDomain`) and records no rebase, so
`pr status` does not describe a push as a rewrite.

Exit codes:
  0  Pushed, and the remote holds HEAD
  1  Refused, lost, unverified, or a rebase is still in progress
  2  Usage error

Usage:
  pr push --expect <sha>               # push HEAD if origin still holds <sha>
  pr push --expect <sha> --no-verify   # skip the pre-push hook
  pr push --expect <sha> --repo-dir <path>
"""

# doc-group: cli

from __future__ import annotations

import core.errors
import core.publishing
from core.tool_parser import ToolParser
from core.trail import Trail, add_trail_args
import pr.context
import rebase.commands

SCRIPT = "pr push"


def build_parser() -> ToolParser:
    """This command's parser; `pr` reads it for arity and for `pr push --help`."""
    parser = ToolParser(prog=SCRIPT,
                        description="Push HEAD with a lease on the remote head you expect")
    parser.add_argument("--repo-dir", "--worktree", dest="repo_dir", metavar="PATH",
                        help="Git worktree directory")
    parser.add_argument("--branch", metavar="NAME",
                        help="Branch name (injected by pr dispatcher)")
    parser.add_argument("--pr", metavar="NUM|URL",
                        help="PR number or URL (injected by pr dispatcher)")
    parser.add_argument("--expect", metavar="SHA", required=True,
                        help="Push only if origin's branch is still at SHA")
    parser.add_argument("--no-verify", action="store_true",
                        help="Skip the pre-push hook. For a hook failure already "
                             "understood — a flake, or one the branch did not cause")
    add_trail_args(parser)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # required=True only rejects a missing flag; `--expect ""` still parses.
    if not args.expect:
        parser.error("--expect needs the SHA origin must still hold")

    ctx = pr.context.resolve_at(pr.context.ContextDepth.LOCAL, repo_dir=args.repo_dir,
                                branch=args.branch, pr_ref=args.pr)
    cwd = str(ctx.require_worktree())
    # No lock taken here: there is no direct entry point, and `pr`'s dispatcher
    # holds the target and checkout `push` declares in cli.registry.

    trail = Trail.start(script=SCRIPT,
                        context={"repo": ctx.repo, "pr": ctx.pr_number, "branch": ctx.branch},
                        debug=args.debug)
    try:
        # Pushing is the command, so it opens the gate, as `pr rebase` does.
        with core.publishing.run(post=True):
            return rebase.commands.cmd_push_head(cwd, ctx, expect=args.expect,
                                                 verify=not args.no_verify, trail=trail)
    except Exception as exc:
        return core.errors.boundary(trail, exc)
    finally:
        trail.finish()
