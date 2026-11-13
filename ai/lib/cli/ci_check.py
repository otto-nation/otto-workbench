"""Fetch CI run data, classify failures, and output status.

Renders a human-readable dashboard to stderr. A single-shot run writes the
structured JSON report to stdout only when there is a failure in it; `--wait`
writes one on every poll that finds something new and a final one when the run
finishes, whether it failed or not.

Manages local state in <state_dir()>/pr/<repo-key>-<branch-slug>/state.json, keyed
on the run's target rather than on the checkout it was invoked from.

Exit codes, remote writes and examples are `CONTRACT` below.
"""

# doc-group: cli

from __future__ import annotations

import sys

import cli.boundary
import cli.contract_notes
import cli.locks
import cli.needs
import core.command_contract
import core.log
import core.proc
import core.publishing
from core.exit_codes import ExitCode
from core.tool_parser import ToolParser
from core.trail import Trail, add_trail_args
import pr.ci_check
import pr.ci_runs
import pr.context
import pr.domains
import rebase.ci_fix

_FIX_POST_HOW = (
    "unless --no-rebase, and only when the branch is behind origin/main: "
    "rebase.commands.cmd_start(..., RunMode.FIX) — the same force-push as "
    "pr rebase --fix, git push --force-with-lease=refs/heads/<branch>:<expect> "
    "(docs/ai-automation.md#pr-rebase-contract). Then git.land.land -> "
    "git.push.push of the fix commit (plain, never forced), gated by --post "
    "via core.publishing.enabled()."
)
_FIX_POST_REFUSED = (
    "A paused conflicted rebase skips the fix pass (exit 1). A refused, "
    "failed, or aborted rebase continues the fix pass on the current base. "
    "A refused or held push leaves the fix commit local (not exit 1 unless "
    "the commit itself failed)."
)

CONTRACT = core.command_contract.CommandContract(
    summary="Report CI failures for the branch. --fix diagnoses and applies "
            "fixes; --post pushes them. Without --fix, exit 0 even when CI "
            "failed — read the JSON.",
    groups=(
        core.command_contract.FlagGroup(
            "Report",
            ("--run", "--head-sha", "--wait", "--wait-timeout",
             "--wait-interval")),
        core.command_contract.FlagGroup(
            "Fix", ("--fix", "--no-rebase", "--no-verify")),
        core.command_contract.FlagGroup(
            "Publish", ("--post",)),
    ),
    remote_effects=(
        core.command_contract.RemoteEffect(
            "--fix --post",
            "a rebase force-push of refs/heads/<branch> (as pr rebase --fix) "
            "when behind and not --no-rebase, then the fix commit pushed to "
            "refs/heads/<branch>",
            "origin", _FIX_POST_HOW, _FIX_POST_REFUSED),
        core.command_contract.RemoteEffect(
            "--no-verify",
            "nothing extra: changes how --fix lands locally",
            "this machine",
            "skips the verify gate (an agent exercising each claimed fix "
            "against the tree); every fix lands unverified; not git's "
            "pre-push hook",
            "n/a — the flag does not write remotely"),
    ),
    exits=(
        core.command_contract.ExitOutcome(
            ExitCode.OK,
            "report printed; with --fix the fixes landed or there was nothing "
            "to fix. Without --fix, 0 even when CI failed — read the JSON. A "
            "held or refused push of the fix commit is still 0"),
        core.command_contract.ExitOutcome(
            ExitCode.FAILURE,
            "no run to report on, --fix without a worktree, a paused rebase "
            "in the worktree, a refused commit, or the fix pass failed"),
        core.command_contract.ExitOutcome(
            ExitCode.USAGE,
            "bad flags"),
        core.command_contract.ExitOutcome(
            ExitCode.TEMPFAIL,
            "another pr run holds this branch or checkout; retry once it "
            "finishes"),
    ),
    examples=(
        core.command_contract.Example(
            "pr ci", "print the latest run for the branch"),
        core.command_contract.Example(
            "pr ci --wait", "poll until the run finishes"),
        core.command_contract.Example(
            "pr ci --fix --post",
            "rebase if behind, fix, and push the fix commit"),
        core.command_contract.Example(
            "pr ci --fix --no-rebase --post",
            "fix and push without rebasing first"),
    ),
    notes=(
        cli.contract_notes.PUSH_WAIT,
        "--no-verify here skips the verify gate (an agent exercising each "
        "fix), not git's pre-push hook.",
        "--fix without --post still rebases locally when behind and commits; "
        "both pushes are drafted.",
        "--fix may gh run rerun --failed once when every remaining failure "
        "is transient rate-limit infra (not gated by --post).",
    ),
)


def build_parser() -> ToolParser:
    """This command's parser, before anything has been parsed with it.

    Public because `pr` reads it to learn which of these options consume a
    following token, which is how a bare positional is classified as a PR
    number or a branch.
    """
    parser = ToolParser(
        prog="pr ci",
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
    parser.add_argument("--no-verify", action="store_true",
                        help="Skip the verify gate after --fix. The gate has an "
                             "agent exercise each claimed fix against the tree and "
                             "demotes the ones that do not hold up; without it "
                             "every fix lands unverified")
    add_trail_args(parser)
    return parser


@cli.boundary.reports_owner_boundary
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
            worktree=cli.needs.ci_need(argv).worktree,
        )

        # What `ci` declares in cli.registry: the target, and with --fix the
        # checkout it rebases and commits in. A no-op when `pr` launched us.
        # Before Trail.start so contention costs no trail artifacts.
        cli.locks.claim_declared("ci", argv, ctx,
                                 command=" ".join([pr.ci_check.SCRIPT] + argv))

        trail = Trail.start(
            script=pr.ci_check.SCRIPT,
            context={"repo": ctx.repo, "pr": ctx.pr_number, "branch": ctx.branch},
            debug=args.debug,
        )
        try:
            report = pr.ci_check.run_ci_wait(trail, args, ctx) if args.wait else pr.ci_check.run_ci(trail, args, ctx)
            return rebase.ci_fix.run_fix(
                trail, report, ctx,
                rebase_first=not args.no_rebase, verify=not args.no_verify,
                wait_timeout=args.wait_timeout, wait_interval=args.wait_interval,
            ) if args.fix else 0
        except pr.ci_runs.RunUnavailable as exc:
            # Expected: there is no run to report on. Trailed where it was raised,
            # so it is the exit code that is left to decide.
            core.log.error(str(exc))
            return 1
        except Exception as exc:
            cli.boundary.reraise(trail, exc)
        finally:
            trail.finish()
