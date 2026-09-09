"""Rebase current branch onto its base with conflict detection and AI resolution.

The base is resolved per run, most authoritative source first: an explicit
--onto, then the branch's PR base branch, then the repo's default branch.

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
  pr-rebase --onto origin/release/1.2 # rebase onto an explicit ref
  pr-rebase --repo-dir <path>         # specify worktree directory
"""

# doc-group: cli

from __future__ import annotations

import argparse
import traceback

from core import log
from core import publishing
from core import trail as core_trail
from core.tool_parser import ToolParser
from core.trail import Trail, add_trail_args
from git import client as git_client
from pr import context as pr_context
from pr.domains import RebaseStatus, RebaseSummary
from rebase import inspect as rebase_inspect
from rebase import land as rebase_land
from rebase import lifecycle
from rebase import stash
from rebase import target as rebase_target
from rebase import types as rebase_types

SCRIPT = "pr-rebase"

RebaseOutcome = rebase_types.RebaseOutcome
RunMode = rebase_types.RunMode

REFUSAL_EXIT = rebase_types.REFUSAL_EXIT
REFUSAL_OVERRIDE_FLAG = rebase_types.REFUSAL_OVERRIDE_FLAG


# ── Subcommands ─────────────────────────────────────────────────────────────


def cmd_abort(
    cwd: str, ctx: pr_context.ResolvedContext, *, target_ref: str,
) -> int:
    """Abort an in-progress rebase and reset state."""
    log.info("Aborting rebase...")
    r = git_client.run("rebase", "--abort", cwd=cwd)
    if r.ok:
        RebaseOutcome(status=RebaseStatus.ABORTED, target_base=target_ref).save(ctx)
        log.ok("Rebase aborted.")
    return r.returncode


def cmd_push(
    cwd: str, ctx: pr_context.ResolvedContext, *, target_ref: str,
    trail: Trail | None = None,
) -> int:
    """Force-push after a completed rebase."""
    if rebase_inspect.rebase_in_progress(cwd):
        core_trail.terr(trail, "push", "rebase still in progress")
        log.error("Cannot push — rebase still in progress.")
        return 1

    log.info("Force-pushing...")
    landed = rebase_land.land_rebased(cwd, trail=trail)
    if not landed.ok:
        core_trail.terr(
            trail, "push", "force-push failed",
            data={"status": str(landed.status), "resume": landed.resume},
        )
        return 1

    state = rebase_types.load_or_init(ctx)
    RebaseOutcome(
        commits_replayed=(state.rebase.commits_replayed
                          or git_client.commits_ahead(cwd, target_ref=target_ref)),
        conflicts_resolved=state.rebase.conflicts_resolved,
        files_resolved=state.rebase.files_resolved,
        files_stale=state.rebase.files_stale,
        force_pushed=True,
        target_base=target_ref,
    ).save(ctx)
    log.ok("Force-pushed successfully.")
    return 0


def cmd_start(
    cwd: str, ctx: pr_context.ResolvedContext, mode: RunMode,
    force: bool = False, *, target_ref: str, trail: Trail | None = None,
) -> int:
    """Start or resume a rebase onto the resolved target ref.

    ``force`` waives the already-landed preflight, which only a fresh rebase
    runs: a resumed one is already past the point the refusal protects.

    The conflict budget is waived on the resume path for the same reason, and
    for a sharper one — it refuses by aborting, and a resumed rebase is one an
    operator may have half-resolved by hand.
    """
    if rebase_inspect.rebase_in_progress(cwd):
        target_ref = rebase_target.resume_target_ref(ctx, target_ref, trail=trail)
        core_trail.tdecision(
            trail, "rebase_state", "detected in-progress rebase",
            reason="rebase-merge or rebase-apply directory exists",
        )
        log.info("Detected in-progress rebase — resuming...")
        return lifecycle.drive_to_completion(
            cwd, ctx, mode, target_ref=target_ref, force=True, trail=trail,
        )

    stashed = stash.auto_stash(cwd, trail=trail)
    if stashed is None:
        return 1

    core_trail.tdecision(
        trail, "rebase_state", "starting fresh rebase",
        reason="no in-progress rebase detected",
    )
    rc = lifecycle.fresh(
        cwd, ctx, mode, force=force, target_ref=target_ref, trail=trail,
    )

    if stashed:
        stash.auto_unstash(cwd, mode, trail=trail)

    return rc


# ── CLI ─────────────────────────────────────────────────────────────────────


def _select_mode(args) -> tuple[RunMode, str]:
    """Resolve --fix and --no-push into the single mode the run is driven by.

    --fix and --no-push are independent: the first says the AI may resolve
    conflicts, the second says nothing reaches the remote. Collapsing them into
    one flag is what made `--fix --no-push` force-push.
    """
    if args.fix and args.push:
        return RunMode.FIX, "--fix flag set"
    if args.fix:
        return RunMode.FIX_ONLY, "--fix with --no-push"
    if args.push:
        return RunMode.PUSH, "default mode"
    return RunMode.REBASE_ONLY, "--no-push flag set"


def _parse_args(argv: list[str] | None):
    parser = ToolParser(
        prog=SCRIPT,
        description="Rebase onto the branch's base with conflict detection and force-push",
        output_schema=RebaseSummary,
        ok_exit_codes=[3, REFUSAL_EXIT],
    )
    parser.add_argument("--repo-dir", "--worktree",
                        dest="repo_dir",
                        help="Git worktree directory")
    parser.add_argument("--branch", help="Branch name (injected by pr dispatcher)")
    parser.add_argument("--pr", help="PR number (injected by pr dispatcher)")
    parser.add_argument("--onto", "--base", dest="onto",
                        help="Ref to rebase onto — overrides the PR's base branch "
                             "and the repo's default branch")
    parser.add_argument("--fix", action="store_true",
                        help="Autonomous mode — resolve conflicts with AI and rebase "
                             "(force-pushes unless --no-push)")
    parser.add_argument("--push", action="store_true", default=True,
                        help=argparse.SUPPRESS)
    parser.add_argument("--no-push", action="store_false", dest="push",
                        help="Skip the force-push — print the command instead")
    parser.add_argument("--force", action="store_true",
                        help="Rebase even when the branch's work already "
                             "landed on the target ref")
    parser.add_argument("--abort", action="store_true",
                        help="Abort in-progress rebase")
    add_trail_args(parser)
    return parser.parse_args(argv)


def _run(args, ctx: pr_context.ResolvedContext, cwd: str, trail: Trail) -> int:
    if args.abort:
        trail.decision("mode", "selected abort", reason="--abort flag set")
        # Abort is the escape hatch for a broken or hung rebase — it should
        # not depend on a network call. The target ref only needs recording
        # in the aborted outcome, so prefer what the run that started the
        # rebase already resolved and fall back to a fresh resolution only
        # when no prior state exists to read.
        target_ref = rebase_types.recorded_target_base(ctx) or (
            rebase_target.resolve_target_ref(cwd, ctx, args.onto, trail=trail)
        )
        return cmd_abort(cwd, ctx, target_ref=target_ref)

    target_ref = rebase_target.resolve_target_ref(cwd, ctx, args.onto, trail=trail)

    mode, reason = _select_mode(args)
    trail.decision("mode", f"selected {mode}", reason=reason)
    # The one entry point where force-pushing is the command rather than a
    # side effect of it, so it is the one that opens the gate. `--no-push`
    # leaves it shut, and every push below then drafts its command instead
    # of running it — which is where the resume line comes from.
    if mode.reaches_remote:
        publishing.enable()
    if args.force:
        trail.decision("preflight", "waiving the already-landed check",
                       reason=f"{REFUSAL_OVERRIDE_FLAG} flag set")
    rc = cmd_start(cwd, ctx, mode, force=args.force, target_ref=target_ref,
                   trail=trail)

    if rc == 0 and mode is RunMode.PUSH:
        rc = cmd_push(cwd, ctx, target_ref=target_ref, trail=trail)
    return rc


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    ctx = pr_context.resolve(
        repo_dir=args.repo_dir, branch=args.branch, pr=args.pr,
    )
    cwd = str(ctx.require_worktree())

    trail = Trail.start(
        script=SCRIPT,
        context={"repo": ctx.repo, "pr": ctx.pr_number, "branch": ctx.branch},
        debug=args.debug,
    )
    try:
        return _run(args, ctx, cwd, trail)
    except Exception as exc:
        trail.error("unexpected_error", str(exc),
                    data={"traceback": traceback.format_exc()})
        raise
    finally:
        trail.finish()
