"""Fetch CI run data, classify failures, and output status.

Renders a human-readable dashboard to stderr. A single-shot run writes the
structured JSON report to stdout only when there is a failure in it; `--wait`
writes one on every poll that finds something new and a final one when the run
finishes, whether it failed or not.

Manages local state in <state_dir()>/pr/<repo-key>-<branch-slug>/state.json, keyed
on the run's target rather than on the checkout it was invoked from.

Usage:
  ci-check                      # latest run for current branch
  ci-check --branch <name>      # specific branch (works from bare repos)
  ci-check --run <run_id>       # specific run
  ci-check --pr <number_or_url> # discover branch from PR
  ci-check --repo-dir <path>    # specify worktree directory
  ci-check --fix                # diagnose then invoke AI to fix failures
  ci-check --head-sha <sha>     # runs and checks of this commit, not local HEAD
  ci-check --fix --no-rebase    # fix without rebasing first (pr batch rebases itself)
"""

# doc-group: cli

from __future__ import annotations

import sys

import core.log
import core.proc
import core.publishing
import core.run_lock
from core.tool_parser import ToolParser
from core.trail import Trail, add_trail_args
import pr.ci_check
import pr.ci_runs
import pr.context
import pr.domains
import pr.state
import rebase.ci_fix


def build_parser() -> ToolParser:
    """This command's parser, before anything has been parsed with it.

    Public because `pr` reads it to learn which of these options consume a
    following token, which is how a bare positional is classified as a PR
    number or a branch.
    """
    parser = ToolParser(
        prog=pr.ci_check.SCRIPT,
        description="CI failure status",
        output_schema=pr.domains.CIDomain,
    )
    parser.add_argument("--pr", metavar="NUM|URL", help="PR number or URL")
    parser.add_argument("--branch", metavar="NAME", help="Branch name (overrides git detection)")
    parser.add_argument("--run", type=int, metavar="ID", help="Specific run ID")
    parser.add_argument("--repo-dir", "--worktree", metavar="PATH",
                        help="Git worktree directory")
    parser.add_argument("--fix", action="store_true",
                        help="Invoke AI to fix failures after diagnosis")
    parser.add_argument("--post", action="store_true",
                        help="Push the fixes; without it the push is drafted")
    parser.add_argument("--wait", action="store_true",
                        help="Poll until all jobs complete, emitting incremental reports")
    parser.add_argument("--wait-timeout", type=int, default=900, metavar="SEC",
                        help="Max wait time in seconds (default: 900)")
    parser.add_argument("--wait-interval", type=int, default=30, metavar="SEC",
                        help="Poll interval in seconds (default: 30)")
    parser.add_argument("--head-sha", default="",
                        help="Report the runs and checks of this commit instead of "
                             "the worktree's HEAD")
    parser.add_argument("--no-rebase", action="store_true",
                        help="With --fix: do not rebase onto main before fixing")
    add_trail_args(parser)
    return parser


def main(argv: list[str] | None = None, *,
         install_signal_handler: bool = True) -> int:
    """Parse *argv* and run the CI check it asks for.

    `install_signal_handler` defaults True because the common caller is the
    `ai/bin` shim, for which this is the whole process. An in-process caller
    that owns its own handler passes False.
    """
    # Only when this module is the process. `pr ci` reaches here having
    # installed the identical handler at its own entry point, and a second
    # install would replace the caller's without chaining or restoring it.
    if install_signal_handler:
        core.proc.install_stop_handler(core.log.interrupted)

    parser = build_parser()
    argv = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(argv)

    # Before anything runs, so no code path can push ahead of the decision.
    with core.publishing.run(post=args.post):
        ctx = pr.context.resolve(
            pr_ref=args.pr, branch=args.branch, repo_dir=args.repo_dir,
        )

        # --fix rebases and commits in this checkout, in this process, so the tree
        # is locked alongside the target. Not required here: the dashboard path
        # reads GitHub and needs no worktree at all, and a bare-repo run of it is
        # legitimate. None means the target lock alone, which is what it had.
        worktree = ctx.worktree_root if args.fix and ctx.worktree_root else None

        # A no-op when pr launched us — we resolve the same target and find its key
        # already in WORKBENCH_RUN_LOCK.
        # Acquired before Trail.start so contention costs no trail artifacts.
        core.run_lock.claim_for_process(
            ctx.target_dir,
            command=" ".join([pr.ci_check.SCRIPT] + argv),
            started=pr.state.now_iso(),
            worktree=worktree,
        )

        trail = Trail.start(
            script=pr.ci_check.SCRIPT,
            context={"repo": ctx.repo, "pr": ctx.pr_number, "branch": ctx.branch},
            debug=args.debug,
        )
        try:
            report = pr.ci_check.run_ci_wait(trail, args, ctx) if args.wait else pr.ci_check.run_ci(trail, args, ctx)
            return rebase.ci_fix.run_fix(trail, report, ctx, rebase_first=not args.no_rebase) if args.fix else 0
        except pr.ci_runs.RunUnavailable as exc:
            # Expected: there is no run to report on. Trailed where it was raised,
            # so it is the exit code that is left to decide.
            core.log.error(str(exc))
            return 1
        except Exception as exc:
            trail.error("unexpected_error", str(exc))
            raise
        finally:
            trail.finish()
