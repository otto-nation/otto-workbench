"""Rebase current branch onto its base with conflict detection and AI resolution.

The base is resolved per run, most authoritative source first: an explicit
--onto (also spelled --base, as `pr create` and `pr review` spell theirs), then
the branch's PR base branch, then the repo's default branch. Unlike those two,
which take a bare branch name, the value here is a ref used verbatim: a bare
`main` means the local `main`, which may be stale, so name `origin/main` to
rebase onto the remote.

Manages the git rebase lifecycle: start, resume, abort, and force-push.
Without --fix, generated-class conflicts rebuild via a regenerate=
gitattributes attribute, a declared rebase.regenerate, or lockfile
regenerators; anything else aborts to a clean tree. --fix resolves with AI.
--stop-on-conflict keeps today's paused tree instead of aborting.
Outputs structured JSON on stdout and status messages on stderr.
Updates local state in <state_dir()>/pr/<repo-key>-<branch-slug>/state.json, keyed
on the run's target rather than on the checkout it was invoked from.

Exit codes, remote writes and examples are `CONTRACT` below, rendered into
`pr rebase --help` and the docs.
"""

# doc-group: cli

from __future__ import annotations

import argparse
import sys

import cli.locks
import cli.needs
import core.command_contract
import core.errors
import core.trail
from core.exit_codes import ExitCode
from core.tool_parser import ToolParser
from core.trail import Trail, add_trail_args
import pr.context
from pr.domains import RebaseSummary
import rebase.commands
import rebase.inspect
import rebase.lease
import rebase.types

SCRIPT = "pr-rebase"
# Trails and the usage ledger are keyed on the hyphenated name; help shows
# the command as typed (`pr rebase`).
TRAIL_KEY = SCRIPT

_LEASE = rebase.lease.PushLease(branch="<branch>", expect="<expect>").args[0]
_HOOK = ("The git pre-push hook runs unless --no-verify; the workbench owner check "
         "(gh.owner_hooks.refuse_outgoing) runs either way.")
_STALE = ("origin moved since the lease was taken: git reports stale info, nothing is "
          "written, exit 1. Resume line: git ls-remote origin refs/heads/<branch>.")

CONTRACT = core.command_contract.CommandContract(
    summary="Rebase the branch onto its base, handle conflicts, and force-push the result "
            "under an explicit lease. Never edits the PR on GitHub.",
    groups=(
        core.command_contract.FlagGroup("Target", ("--onto", "--fork-point")),
        core.command_contract.FlagGroup("Conflicts", ("--fix", "--stop-on-conflict", "--abort")),
        core.command_contract.FlagGroup("Push", ("--no-push", "--push-only", "--expect", "--no-verify")),
        core.command_contract.FlagGroup("Overrides", ("--force-landed",)),
    ),
    remote_effects=(
        core.command_contract.RemoteEffect(
            "", "refs/heads/<branch> := the rebased HEAD", "origin",
            f"git push [--no-verify] {_LEASE}, where <expect> is origin/<branch> as this "
            "checkout saw it before the run's own fetch (empty when origin has no such "
            "branch, which creates it). " + _HOOK,
            _STALE + " A refused pre-push hook leaves the rebase complete and unpushed "
                     "(JSON force_pushed false)."),
        core.command_contract.RemoteEffect(
            "--fix", "the same push, after AI conflict resolution", "origin",
            f"git push [--no-verify] {_LEASE} (same lease as the bare invocation)",
            _STALE),
        core.command_contract.RemoteEffect(
            "--push-only", "refs/heads/<branch> := current HEAD, commits added since the "
                           "rebase included; no rebase runs", "origin",
            f"git push [--no-verify] {_LEASE}, where <expect> is RebaseSummary.lease_expect "
            "that the last completed rebase saved in the target's state.json",
            "No recorded rebase: exit 1. A recorded empty lease while origin has the "
            "branch: exit 1 ('recorded lease is stale'). " + _STALE),
        core.command_contract.RemoteEffect(
            "--push-only --expect", "refs/heads/<branch> := current HEAD; no rebase runs",
            "origin",
            f"git push [--no-verify] {_LEASE}, where <expect> is exactly the SHA given "
            "(use the full SHA); no recorded rebase is needed",
            _STALE),
        core.command_contract.RemoteEffect(
            "--no-verify", "nothing extra: changes how the push above runs", "origin",
            "adds --no-verify to git push, skipping the git pre-push hook; the owner check "
            "still runs. For a hook failure already understood — a flake, or a check the "
            "branch did not cause",
            "as for the push it modifies"),
    ),
    exits=(
        core.command_contract.ExitOutcome(ExitCode.OK, "rebased, and pushed unless --no-push (or a one-sided "
                                    "resolution held the push)"),
        core.command_contract.ExitOutcome(ExitCode.FAILURE, "pre-flight failure, git error, refused or lost "
                                         "push, missing or stale recorded lease", (
            core.command_contract.NextStep("", (
                "read the ✗ line and the Resume line above",
                "if the JSON reads \"status\": \"completed\" with \"force_pushed\": false, "
                "commit the fix for what the hook named, then: pr rebase --push-only "
                "--branch {branch}",
            )),
        )),
        core.command_contract.ExitOutcome(ExitCode.USAGE, "flags that cannot combine (--push-only with "
                                       "--fix/--abort/--no-push, --expect without "
                                       "--push-only, --fix with --stop-on-conflict)"),
        core.command_contract.ExitOutcome(ExitCode.CONFLICTS, "conflicts need a person; ConflictReport on "
                                           "stdout, its tree says paused or clean", (
            core.command_contract.NextStep("paused", (
                "resolve each file in `files` hunk by hunk (never take a side whole)",
                "git -C {worktree} add <file> for each resolved file",
                "git -C {worktree} rebase --continue (repeat until the rebase finishes)",
                "git -C {worktree} ls-remote origin refs/heads/{branch} — it must equal "
                "git -C {worktree} rev-parse refs/remotes/origin/{branch}; if not, someone "
                "pushed since the rebase started: stop",
                "pr rebase --push-only --expect <that full SHA> --branch {branch}",
                "(or let pr finish instead: pr rebase --fix --branch {branch} resumes here)",
            )),
            core.command_contract.NextStep("clean", (
                "the rebase was aborted; HEAD is back at pre_rebase_head",
                "pr rebase --fix --branch {branch} to resolve with AI, or "
                "pr rebase --stop-on-conflict --branch {branch} to pause and resolve by hand",
            )),
        )),
        core.command_contract.ExitOutcome(ExitCode.REFUSED, "refused before changing anything (already landed, "
                                         "unrelated history, conflicts over budget); "
                                         "RefusalReport on stdout", (
            core.command_contract.NextStep("", (
                "read `signal` in the JSON — already_landed usually means delete the branch",
                "to rebase anyway: pr rebase --force-landed --branch {branch}",
            )),
        )),
        core.command_contract.ExitOutcome(ExitCode.TEMPFAIL, "another pr run holds this branch or checkout; "
                                          "retry once it finishes"),
    ),
    examples=(
        core.command_contract.Example("pr rebase --fix", "rebase onto the PR's base, AI-resolve conflicts, "
                                      "force-push under the lease"),
        core.command_contract.Example("pr rebase --no-push", "rebase only; the push command is printed"),
        core.command_contract.Example("pr rebase --push-only", "push what a --no-push run, or a run whose push "
                                            "failed, left — under its recorded lease"),
        core.command_contract.Example("pr rebase --push-only --expect 0123456789abcdef0123456789abcdef01234567",
                   "push a branch finished outside pr, leasing on the SHA origin must hold"),
        core.command_contract.Example("pr rebase --stop-on-conflict", "pause on conflicts to resolve by hand"),
        core.command_contract.Example("pr rebase --onto origin/release/1.2", "rebase onto a ref, used verbatim"),
        core.command_contract.Example("pr rebase --force-landed --fix", "rebase even though the work landed"),
    ),
    notes=("`--force` is a deprecated alias of `--force-landed`.",),
    json_output=core.command_contract.JsonOutput(
        "exit 0/1: the outcome (status, commits_replayed, conflicts_resolved, "
        "files_resolved, files_stale, force_pushed, fork_point, squashed_commits); "
        "exit 3: ConflictReport (status, files, tree, classes, remaining_commits, "
        "pre_rebase_head); exit 4: RefusalReport (branch, signal, detail, status, override, "
        "commits_ahead — a count on the git signals, null on pr_merged, which is asked "
        "before checkout; pr_number — set on pr_merged only).",
        (
            core.command_contract.EnumDoc("classes", tuple(
                core.command_contract.EnumValue(s.value, f"{s.doc.what}. Without --fix: {s.doc.without_fix}. "
                                      f"With --fix: {s.doc.with_fix}.")
                for s in rebase.types.ConflictStrategy)),
            core.command_contract.EnumDoc("tree", (
                core.command_contract.EnumValue("paused", "the rebase is still in the worktree; what was "
                                       "resolved is staged; an auto-stash is held"),
                core.command_contract.EnumValue("clean", "the rebase was aborted; HEAD is at pre_rebase_head"),
            )),
            core.command_contract.EnumDoc("signal", tuple(
                core.command_contract.EnumValue(s.value, f"status {s.doc.status}: {s.doc.found}")
                for s in rebase.types.RefusalSignal)),
        ),
    ),
)


def next_state(cwd: str) -> str:
    """The `when` key for this run's next steps: is a rebase still paused in *cwd*?

    Returns "" when *cwd* cannot be read, which selects no tree-specific steps.
    """
    # The Next: block is advice printed after the exit code is decided; a
    # worktree that vanished must not turn exit 3 into a traceback. The
    # ConflictReport on stdout still names the tree.
    try:
        return "paused" if rebase.inspect.rebase_in_progress(cwd) else "clean"
    except OSError:
        return ""


def build_parser() -> ToolParser:
    """This command's parser, before anything has been parsed with it.

    Public because `pr` reads it to learn which of these options consume a
    following token, which is how a bare positional is classified as a PR
    number or a branch.
    """
    parser = ToolParser(
        # The command as typed; TRAIL_KEY stays `pr-rebase` because trails and
        # the usage ledger are keyed on it.
        prog="pr rebase",
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
                        help="Replay only the commits after REF, overriding "
                             "the landed prefix pr rebase detects on its own")
    parser.add_argument("--fix", action="store_true",
                        help="Autonomous mode — resolve conflicts with AI and rebase "
                             "(force-pushes unless --no-push)")
    parser.add_argument("--stop-on-conflict", action="store_true",
                        help="Pause on conflicts (stash held) instead of aborting; "
                             "incompatible with --fix")
    parser.add_argument("--push", action="store_true", default=True,
                        help=argparse.SUPPRESS)
    parser.add_argument("--no-push", action="store_false", dest="push",
                        help="Skip the force-push — print the command instead")
    parser.add_argument(
        "--push-only", action="store_true",
        help="Push HEAD with the lease an earlier --no-push run recorded; do not rebase",
    )
    parser.add_argument(
        "--expect", metavar="SHA", default="",
        help="With --push-only: lease against SHA instead of the lease a recorded "
             "rebase saved; no recorded rebase is needed",
    )
    parser.add_argument(
        "--no-verify", action="store_true",
        help="Skip the pre-push hook on the force-push. For a hook failure "
             "already understood — a flake, or one the branch did not cause",
    )
    parser.add_argument("--force-landed", dest="force", action="store_true",
                        help="Rebase even when the branch's work already landed on the "
                             "target ref, and replay its landed prefix too. Not a force "
                             "push: every push is leased")
    # The old spelling, kept so scripts and muscle memory still work; hidden so
    # nothing new learns a name that reads as a force push.
    parser.add_argument("--force", dest="force", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--abort", action="store_true",
                        help="Abort in-progress rebase")
    add_trail_args(parser)
    return parser


def _parse_args(argv: list[str] | None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.push_only and (args.fix or args.abort or not args.push):
        parser.error("--push-only cannot be combined with --fix, --abort or --no-push")
    if args.expect and not args.push_only:
        parser.error("--expect only applies with --push-only")
    if args.fix and args.stop_on_conflict:
        parser.error("--fix cannot be combined with --stop-on-conflict")
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    argv = list(sys.argv[1:] if argv is None else argv)
    ctx = pr.context.resolve(
        repo_dir=args.repo_dir, branch=args.branch, pr_ref=args.pr,
        worktree=cli.needs.rebase_need(argv).worktree,
    )
    cwd = str(ctx.require_worktree())

    # What `rebase` declares in cli.registry: target and checkout, since the
    # replay rewrites this checkout's history in place. A no-op under `pr
    # rebase`, which already holds both; taken here so running this script
    # directly is guarded too. Before Trail.start so contention costs no trail
    # artifacts.
    cli.locks.claim_declared("rebase", argv, ctx, command=" ".join([SCRIPT, *argv]))

    trail = Trail.start(
        script=TRAIL_KEY,
        context={"repo": ctx.repo, "pr": ctx.pr_number, "branch": ctx.branch},
        debug=args.debug,
    )
    try:
        rc = rebase.commands._run(args, ctx, cwd, trail)
        # paused vs clean only selects Next: lines on exit 3; asking git on
        # every other exit would fail a run whose worktree was never a repo.
        when = next_state(cwd) if rc == int(ExitCode.CONFLICTS) else ""
        core.command_contract.print_next(CONTRACT, rc, when=when, branch=ctx.branch, worktree=cwd)
        return rc
    except Exception as exc:
        core.errors.reraise(trail, exc)
    finally:
        trail.finish()
