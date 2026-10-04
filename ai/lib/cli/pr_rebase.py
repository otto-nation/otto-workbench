"""Rebase current branch onto its base with conflict detection and AI resolution.

The base is resolved per run, most authoritative source first: an explicit
--onto (also spelled --base, as `pr create` and `pr review` spell theirs), then
the branch's PR base branch, then the repo's default branch. Unlike those two,
which take a bare branch name, the value here is a ref used verbatim: a bare
`main` means the local `main`, which may be stale, so name `origin/main` to
rebase onto the remote.

Manages the git rebase lifecycle: start, resume, abort, and force-push.
With --fix, automatically resolves merge conflicts using AI.
Outputs structured JSON on stdout and status messages on stderr.
Updates local state in <state_dir()>/pr/<repo-key>-<branch-slug>/state.json, keyed
on the run's target rather than on the checkout it was invoked from.

Exit codes:
  0  Success (clean rebase or operation completed)
  1  Error (pre-flight failure, git error)
  3  Conflicts detected (JSON report on stdout)
  4  Branch already landed — refused before touching the remote (JSON on stdout)

Usage:
  pr-rebase                           # rebase and force-push
  pr-rebase --no-push                 # rebase only, skip force-push
  pr-rebase --fix                     # resolve conflicts with AI, rebase, and force-push
  pr-rebase --fix --no-push           # resolve conflicts with AI, but do not push
  pr-rebase --force                   # rebase even when the branch already landed
  pr-rebase --abort                   # abort in-progress rebase
  pr-rebase --onto origin/release/1.2 # rebase onto an explicit ref, used as given (or --base)
  pr-rebase --fork-point <ref>        # replay only the commits after <ref>
  pr-rebase --no-verify               # force-push without running the pre-push hook
  pr-rebase --repo-dir <path>         # specify worktree directory
"""

# doc-group: cli

from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

import core.log
import core.publishing
import core.run_lock
import core.trail
from core.tool_parser import ToolParser
from core.trail import Trail, add_trail_args
import pr.context
import pr.state
from pr.domains import RebaseSummary
import rebase.inspect
import rebase.land
import rebase.lease
import rebase.lifecycle
import rebase.pr_snapshot
import rebase.stash
import rebase.target
import rebase.commands
import rebase.types

SCRIPT = "pr-rebase"



def build_parser() -> ToolParser:
    """This command's parser, before anything has been parsed with it.

    Public because `pr` reads it to learn which of these options consume a
    following token, which is how a bare positional is classified as a PR
    number or a branch.
    """
    parser = ToolParser(
        prog=SCRIPT,
        description="Rebase onto the branch's base with conflict detection and force-push",
        output_schema=RebaseSummary,
        ok_exit_codes=[rebase.types.CONFLICTS_EXIT, rebase.types.REFUSAL_EXIT],
    )
    parser.add_argument("--repo-dir", "--worktree",
                        dest="repo_dir", metavar="PATH",
                        help="Git worktree directory")
    parser.add_argument("--branch", metavar="NAME",
                        help="Branch name (injected by pr dispatcher)")
    parser.add_argument("--pr", metavar="NUM|URL",
                        help="PR number or URL (injected by pr dispatcher)")
    parser.add_argument("--onto", "--base", dest="onto", metavar="REF",
                        help="Ref to rebase onto — overrides the PR's base branch "
                             "and the repo's default branch")
    # git's `<upstream>` argument, which `--onto` alone cannot express: with
    # one ref, <newbase> and <upstream> are the same and the whole branch is
    # replayed. Takes a value, unlike git's own boolean `--fork-point`.
    parser.add_argument("--fork-point", dest="fork_point", metavar="REF",
                        help="Replay only the commits after REF, onto the "
                             "target — for a branch whose earlier commits "
                             "already landed. The partially-landed refusal "
                             "names the ref to pass")
    parser.add_argument("--fix", action="store_true",
                        help="Autonomous mode — resolve conflicts with AI and rebase "
                             "(force-pushes unless --no-push)")
    parser.add_argument("--push", action="store_true", default=True,
                        help=argparse.SUPPRESS)
    parser.add_argument("--no-push", action="store_false", dest="push",
                        help="Skip the force-push — print the command instead")
    parser.add_argument(
        "--push-only", action="store_true",
        help="Push HEAD with the lease an earlier --no-push run recorded; do not rebase",
    )
    parser.add_argument(
        "--no-verify", action="store_true",
        help="Skip the pre-push hook on the force-push. For a hook failure "
             "already understood — a flake, or one the branch did not cause",
    )
    parser.add_argument("--force", action="store_true",
                        help="Rebase even when the branch's work already "
                             "landed on the target ref")
    parser.add_argument("--abort", action="store_true",
                        help="Abort in-progress rebase")
    add_trail_args(parser)
    return parser


def _parse_args(argv: list[str] | None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.push_only and (args.fix or args.abort or not args.push):
        parser.error("--push-only cannot be combined with --fix, --abort or --no-push")
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    ctx = pr.context.resolve(
        repo_dir=args.repo_dir, branch=args.branch, pr_ref=args.pr,
    )
    cwd = str(ctx.require_worktree())

    # A no-op when `pr rebase` launched us — it resolves the same target and we
    # find its key already in WORKBENCH_RUN_LOCK. Taken here so that invoking
    # this script directly is guarded too: it rewrites a branch's history and
    # force-pushes the result, which is the last thing that should interleave
    # with another run against the same target.
    # Acquired before Trail.start so contention costs no trail artifacts.
    core.run_lock.claim_for_process(
        ctx.target_dir,
        command=" ".join([SCRIPT] + (argv if argv is not None else sys.argv[1:])),
        started=pr.state.now_iso(),
        # The replay rewrites this checkout's history in place — the strongest
        # reason in the codebase for a tree to have one writer at a time.
        # `cwd` above is require_worktree()'s answer, so it is never None here.
        worktree=Path(cwd),
    )

    trail = Trail.start(
        script=SCRIPT,
        context={"repo": ctx.repo, "pr": ctx.pr_number, "branch": ctx.branch},
        debug=args.debug,
    )
    try:
        return rebase.commands._run(args, ctx, cwd, trail)
    except Exception as exc:
        trail.error("unexpected_error", str(exc),
                    data={"traceback": traceback.format_exc()})
        raise
    finally:
        trail.finish()
