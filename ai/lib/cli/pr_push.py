"""Push HEAD to origin with a lease on the remote tip the caller planned from.

`--expect` is that tip. A fast-forward from any ancestor of it pushes under a
lease on what origin holds now. A moved origin is refused with no force advice.
Both `pr push` and `pr rebase --push-only` are lease-guarded pushes of HEAD — a
rewrite satisfies the lease the same way a fast-forward does, so `pr push` is
not "not a force push". Use `pr rebase --push-only` after a rebase; use
`pr push` for anything else. It records where the branch stands (`PushDomain`)
and records no rebase, so `pr status` does not describe a push as a rewrite.

Exit codes, remote writes and examples are `CONTRACT` below, rendered into
`pr push --help` and the docs.
"""

# doc-group: cli

from __future__ import annotations

import cli.boundary
import cli.contract_notes
import core.command_contract
import core.publishing
from core.exit_codes import ExitCode
from core.tool_parser import ToolParser
from core.trail import Trail, add_trail_args
import pr.context
import rebase.commands
import rebase.lease

SCRIPT = "pr push"

CONTRACT = core.command_contract.CommandContract(
    summary="Push HEAD to origin under a lease on the remote tip you planned from. Never "
            "rebases, never creates a branch, never edits the PR.",
    groups=(core.command_contract.FlagGroup("Push", ("--expect", "--no-verify")),),
    remote_effects=(
        core.command_contract.RemoteEffect(
            "", "refs/heads/<branch> := HEAD", "origin",
            "git push [--no-verify] "
            + rebase.lease.PushLease(branch="<branch>", expect="<lease>").args[0]
            + ", where <lease> is the tip origin holds now (read with ls-remote) when it is "
              "--expect or an ancestor of --expect (a fast-forward); when origin cannot be "
              "asked, --expect itself. Records PushDomain in state.json, no rebase.",
            "origin at neither --expect nor an ancestor of it: refused before pushing "
            "(stale --expect), exit 1. origin has no such branch: exit 1 — pr push never "
            "creates a branch. origin moves between the read and the push: stale info, "
            "nothing written, exit 1."),
        core.command_contract.RemoteEffect(
            "--no-verify", "nothing extra: changes how the push runs", "origin",
            "adds --no-verify to git push, skipping the git pre-push hook; the workbench "
            "owner check still runs",
            "as for the push"),
    ),
    exits=(
        core.command_contract.ExitOutcome(ExitCode.OK, "pushed, and origin holds HEAD"),
        core.command_contract.ExitOutcome(ExitCode.FAILURE, "refused, lost, unverified, or a rebase is in "
                                         "progress", (
            core.command_contract.NextStep("", (
                "git -C {worktree} ls-remote origin refs/heads/{branch}",
                "if HEAD belongs on top of what origin holds, run pr rebase first; "
                "otherwise re-run pr push --expect <that SHA>",
            )),
        )),
        core.command_contract.ExitOutcome(ExitCode.USAGE, "--expect missing or empty"),
        core.command_contract.ExitOutcome(ExitCode.TEMPFAIL, "another pr run holds this branch or checkout; "
                                          "retry once it finishes"),
    ),
    examples=(
        core.command_contract.Example("pr push --expect 0123456789abcdef0123456789abcdef01234567",
                   "push HEAD if origin still holds that commit, or an ancestor of it"),
        core.command_contract.Example("pr push --expect <sha> --no-verify", "the same, skipping the pre-push hook"),
    ),
    notes=(cli.contract_notes.PUSH_WAIT,),
)


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
                        help="The remote tip you planned from. A fast-forward from "
                             "any ancestor of it pushes under a lease on what origin "
                             "holds. A moved origin is refused with no force advice.")
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
            rc = rebase.commands.cmd_push_head(cwd, ctx, expect=args.expect,
                                               verify=not args.no_verify, trail=trail)
        core.command_contract.print_next(CONTRACT, rc, branch=ctx.branch, worktree=cwd)
        return rc
    except Exception as exc:
        cli.boundary.reraise(trail, exc)
    finally:
        trail.finish()
