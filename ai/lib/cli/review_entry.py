"""Run the configured review agent on a PR with local worktree checkout and iterative review support.

The entry point, and only the entry point: the parser, the flag contradictions,
the signal handler, the run lock, and the choice of which flow to run. The
review itself is `review.run`'s.

Three things stay here rather than moving down a layer, each for its own reason.
The signal handler is process-level state, so it is installed only when this
module is the process — `install_signal_handler=False` for an in-process caller
that has already installed its own, since `signal.signal` overwrites without
chaining and nothing restores it. The run lock is claimed between resolving the
self-review target and switching the checkout to it — a resolver that did both
would take a process-lifetime lock from inside the library.

Exit codes, remote writes and examples are `CONTRACT` below.
"""

# doc-group: cli

from __future__ import annotations

import argparse
import contextlib
import os
import sys
from dataclasses import replace
from pathlib import Path
from agent.registry import add_phase_skip_flags, phase_skips
import cli.boundary
import cli.locks
import core.command_contract
import core.log
import core.proc
import core.publishing
import core.run_lock
import core.version
import core.workbench_paths
from core.exit_codes import ExitCode
from core.trail import Trail, add_trail_args
import pr.context
import pr.state
import review.completion
import review.file_tracked
import review.focus
import review.paths
import review.run
import review.worktree
from review.paths import review_file_path
from review.pipeline import DEFAULT_MAX_PARALLEL
from review.summary import json_summary

SCRIPT = "review"
BIN_DIR = Path(__file__).resolve().parent.parent.parent / "bin"

# The subcommands this binary used to carry, and where each went. Kept as a
# refusal rather than dropped: the names were in people's shell history and in
# docs, and an unrecognised one would otherwise be read as a branch to review.
_REMOVED = {
    "gc": "pr gc",
    "post": "pr review --post",
    "rebuild": "pr review --repair",
    "summary": "pr review --summary",
    "threads": "pr comments",
}

_PR_POST_HOW = (
    "review.publish.resolve prompts unless --no-post (or the review "
    "binary's --post, which pr review does not reach); then "
    "review.publish.post -> cli.review_post -> review.posting.post_review, "
    "a PENDING review with inline comments. --submit then "
    "review.publish.submit_pending as a COMMENT review. A self-review "
    "files no GitHub review (review.run.run_self_review)."
)
_PR_POST_REFUSED = (
    "declining a prompt leaves the review on disk, unposted; a failed "
    "post is not rolled back"
)
_FIX_PUSH_HOW = (
    "review.invoke.run forwards --post to orchestrate when may_publish "
    "(--push or the binary's --post). fix.engine._land -> "
    "git.land.land(gated=True) -> git.push.push, plain, never forced."
)
_FIX_PUSH_REFUSED = (
    "a refused or held push leaves the commit local; git.push.report "
    "prints Resume on REFUSED/LOST, nothing on HELD"
)
_MODE_POST_HOW = (
    "cli.review_modes.post -> core.publishing.call_entry_point("
    "cli.review_post:main). --event picks comment/approve/"
    "request_changes; approve and request_changes imply --submit."
)
_MODE_POST_REFUSED = (
    "no PR number or no review file: exit 1, GitHub unchanged; a "
    "failed post is not rolled back"
)
_TRACK_HOW = (
    "review.file_tracked.run -> review.finding_issue.file_and_link -> "
    "review.issue.create_issue (GitHub through gh.client; Linear "
    "through the linear CLI), gated by core.publishing.enabled(). "
    "Under pr review, --post is a mode and cannot combine with "
    "--track; the publishing spelling is review --self --track ID --post."
)
_TRACK_REFUSED = (
    "without the publishing gate the filing is drafted; a failed "
    "create leaves the findings unfiled"
)

CONTRACT = core.command_contract.CommandContract(
    summary="Run the configured review agent on a PR, or handle a mode "
            "(--post an existing review, --repair, --summary, --dismiss, "
            "--recover, --list). A plain PR run prompts before posting.",
    groups=(
        core.command_contract.FlagGroup(
            "Target",
            ("args", "--base", "--self", "--skip-user-verification")),
        core.command_contract.FlagGroup(
            "Publish", ("--no-post", "--submit", "--force")),
        core.command_contract.FlagGroup(
            "Fix", ("--fix", "--push", "--track")),
        core.command_contract.FlagGroup(
            "Phases",
            ("--no-holistic", "--no-scout", "--no-group",
             "--no-synthesis", "--no-disprove", "--disprove")),
        core.command_contract.FlagGroup(
            "Budget",
            ("--max-parallel", "--max-cost", "--model", "--effort",
             "--max-groups", "--generated")),
        core.command_contract.FlagGroup(
            "Steering", ("--issue", "--focus", "--focus-file")),
        core.command_contract.FlagGroup(
            "Output", ("--json-summary", "-V")),
        core.command_contract.FlagGroup(
            "Modes",
            ("--post", "--repair", "--summary", "--dismiss",
             "--recover", "--list", "--event", "--if-changed")),
    ),
    remote_effects=(
        core.command_contract.RemoteEffect(
            "",
            "a PENDING review with inline comments; --submit submits it "
            "as a COMMENT review",
            "GitHub", _PR_POST_HOW, _PR_POST_REFUSED),
        core.command_contract.RemoteEffect(
            "--self --fix --push",
            "the fix commit pushed to refs/heads/<branch>",
            "origin", _FIX_PUSH_HOW, _FIX_PUSH_REFUSED),
        core.command_contract.RemoteEffect(
            "--post",
            "posts an existing review; --event picks "
            "comment/approve/request_changes",
            "GitHub", _MODE_POST_HOW, _MODE_POST_REFUSED),
        core.command_contract.RemoteEffect(
            "--track",
            "files the finding on the tracker (GitHub or Linear issue)",
            "GitHub or Linear", _TRACK_HOW, _TRACK_REFUSED),
    ),
    exits=(
        core.command_contract.ExitOutcome(
            ExitCode.OK,
            "review completed (a partial run included), --version "
            "printed, a mode succeeded, or --track drafted/filed"),
        core.command_contract.ExitOutcome(
            ExitCode.FAILURE,
            "flag conflicts (--fix without --self, --push without "
            "--fix, --recover with --force, --no-post with --post, "
            "--track with a mode or without --self); no target; a "
            "removed subcommand; bad --focus; unresolvable --base; "
            "every agent failed; no PR/review file for a mode; --fix "
            "on a drifted recover; owner boundary"),
        core.command_contract.ExitOutcome(
            ExitCode.USAGE,
            "bad flags; --if-changed without --schema-version"),
        core.command_contract.ExitOutcome(
            ExitCode.REFUSED,
            "the branch looks superseded; refused before reviewing; "
            "JSON names override --force",
            (
                core.command_contract.NextStep("", (
                    "pr review --force … to review it anyway",
                )),
            ),
        ),
        core.command_contract.ExitOutcome(
            ExitCode.TEMPFAIL,
            "another pr run holds this branch or checkout; retry once "
            "it finishes"),
    ),
    examples=(
        core.command_contract.Example(
            "pr review --no-post 42",
            "review PR 42; do not post and do not prompt"),
        core.command_contract.Example(
            "pr review --self --fix --push",
            "self-review, apply findings, push the fix commit"),
        core.command_contract.Example(
            "pr review --post --event comment",
            "post an existing review as a COMMENT review"),
        core.command_contract.Example(
            "pr review --json-summary 42",
            "review PR 42; stdout is one REVIEW_SUMMARY:{json} line"),
    ),
    notes=(
        "Under `pr review`, `--post` names a mode that posts an "
        "existing review; the `review` binary's own `--post` (post "
        "after running) is not reachable through `pr`.",
        "A plain PR run prompts before posting and before replacing a "
        "pending review; --no-post skips both. --force skips the "
        "stale/pending prompts, not the post prompts, and overrides "
        "the superseded refusal.",
        "A self-review files no GitHub review.",
    ),
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=SCRIPT,
        description="Run the configured review agent on a PR",
        add_help=True,
    )
    parser.add_argument("--no-post", action="store_true",
                        help="Do not post to GitHub and do not prompt (a plain "
                             "run prompts before posting and before replacing "
                             "a pending review)")
    parser.add_argument("--post", action="store_true",
                        help="Post the review to GitHub when it finishes")
    parser.add_argument("--submit", action="store_true",
                        help="Submit the GitHub review after posting, as a COMMENT review "
                             "(default: leave PENDING)")
    parser.add_argument("--self", action="store_true", dest="self_review",
                        help="Review a local checkout of the current branch, or of "
                             "the branch or PR ref given")
    parser.add_argument("--fix", action="store_true",
                        help="Apply findings after the review (requires --self)")
    parser.add_argument("--track", action="append", default=[], metavar="FINDING_ID",
                        help="File this open finding from the branch's existing "
                             "self-review (repeatable). Requires --self; does not "
                             "run a review. Cannot combine with --fix, --force, "
                             "or --recover")
    parser.add_argument("--push", action="store_true",
                        help="Push the --fix commit (requires --fix)")
    parser.add_argument("--skip-user-verification", action="store_true",
                        help="Skip the PR-ownership check when --self is given a PR ref")
    parser.add_argument("--force", action="store_true",
                        help="Skip stale-review and pending-review prompts; not with --recover")
    parser.add_argument("--recover", action="store_true",
                        help="Finish a review whose agents failed, at the commit it started from")
    add_phase_skip_flags(parser)
    parser.add_argument("--disprove", action="store_true", default=None,
                        help="Enable the disprove-it gate (default: effort-based)")
    parser.add_argument("--json-summary", action="store_true",
                        help="Print one REVIEW_SUMMARY:{json} line on stdout "
                             "(the only stdout)")
    parser.add_argument("--issue", metavar="URL",
                        help="Related issue to include in the review prompt")
    review.focus.add_focus_args(parser)
    # `--onto` is accepted for symmetry with `pr rebase`, which spells the same
    # question that way. The value differs in kind from that one, though: this
    # is a bare branch name resolved as `origin/<name>`, where rebase takes any
    # ref verbatim. A review's every range is anchored to the remote-tracking
    # ref, so accepting an arbitrary ref here would silently anchor half of them
    # somewhere else.
    parser.add_argument("--base", "--onto", dest="base", default="",
                        help="Branch to review against, as a bare name. Default: "
                             "the PR's base, else the branch this one is stacked "
                             "on, else the repo's default branch")
    parser.add_argument(
        "--max-parallel", type=int, default=DEFAULT_MAX_PARALLEL, metavar="N",
        help="Max concurrent group reviews (default: from the machine slot pool, cap 4)",
    )
    parser.add_argument("--max-cost", type=float, metavar="USD",
                        help="Max total review cost in USD")
    parser.add_argument("--model", metavar="NAME",
                        help="Override the model for all agents: an id from the provider's catalogue (e.g. claude-sonnet-5)")
    parser.add_argument("--effort", choices=["low", "medium", "high"], default=None,
                        help="Effort preset (default: review.effort in config.yml, else medium)")
    parser.add_argument("--max-groups", type=int, default=None, metavar="N",
                        help="Max file groups in multi-phase reviews (default: effort-based)")
    parser.add_argument("--generated", action="store_true",
                        help="Include tier3-generated files (skipped by default)")
    parser.add_argument("--repo-dir", "--worktree", dest="repo_dir", metavar="PATH",
                        help="Git worktree directory")
    parser.add_argument("--branch", metavar="NAME",
                        help="Branch to review (injected by the pr dispatcher)")
    parser.add_argument("--pr", metavar="NUM|URL",
                        help="PR number or URL (injected by the pr dispatcher)")
    parser.add_argument("-V", "--version", action="store_true",
                        help="Print version and exit")
    parser.add_argument("args", nargs="*", metavar="PR|BRANCH",
                        help="PR number, URL, or branch name")
    add_trail_args(parser)
    return parser


def _flags(args, argv: list[str], generator_version: str) -> review.run.ReviewFlags:
    """The parsed argv as the value the flows read.

    *argv* is the list `main` was given, not `sys.argv`. Called in-process
    from `pr fix`, `sys.argv` is the *parent's* — so the command recorded
    against the review, and the one a run lock reports to whoever it turns
    away, read `pr fix --post` for a review that was never invoked that way.
    """
    return review.run.ReviewFlags(
        bin_dir=BIN_DIR,
        generator_version=generator_version,
        command=" ".join([SCRIPT] + argv),
        base=args.base or "",
        issue_link=args.issue or "",
        # getattr: suites build `args` by hand, without `_main_body`'s validation.
        focus=getattr(args, "focus_texts", ()),
        max_parallel=args.max_parallel,
        max_cost=args.max_cost,
        model=args.model,
        effort=args.effort,
        max_groups=args.max_groups,
        skip_phases=phase_skips(args),
        disprove=args.disprove,
        generated=args.generated,
        recover=getattr(args, "recover", False),
        force=args.force,
        fix=args.fix,
        may_publish=args.post or args.push,
        no_post=args.no_post,
        auto_post=args.post,
        auto_submit=args.submit,
        repo_dir=args.repo_dir or "",
    )


def _emit_json_summary(fd: int | None, outcome: review.run.ReviewOutcome) -> None:
    """Write the machine-readable summary to the descriptor stdout was saved to.

    Takes the outcome rather than re-deriving the path: a self review's file
    lives under a branch-derived directory that `review_file_path` does not
    produce, so recomputing it here emitted an all-zero report for a review
    that had just run.
    """
    if fd is None:
        return
    summary = json_summary(outcome.repo, outcome.pr_number, str(outcome.review_file))
    os.write(fd, (summary + "\n").encode())


def _track_error(parsed) -> str:
    """Why `--track` cannot run as parsed, or empty."""
    if not parsed.self_review:
        return "--track requires --self"
    conflicts = [
        name for name, on in (
            ("--fix", parsed.fix),
            ("--force", parsed.force),
            ("--recover", parsed.recover),
        ) if on
    ]
    if not conflicts:
        return ""
    return (
        "--track files an existing self-review and cannot run with "
        + ", ".join(conflicts)
    )


def _run_file_tracked(parsed, argv: list[str]) -> int:
    """File `--track` ids from the branch's current self-review. No agent."""
    ctx = pr.context.resolve_at(
        pr.context.ContextDepth.LOCAL,
        repo_dir=parsed.repo_dir or None,
    )
    cli.locks.claim_declared(
        "review", argv, ctx, command=" ".join([SCRIPT] + argv),
    )
    with core.publishing.run(post=parsed.post):
        return review.file_tracked.run(ctx, frozenset(parsed.track))


def _exit_status(outcome: review.run.ReviewOutcome) -> int:
    """1 when no part of the review ran, else 0.

    Decided after the review is shown, summarised and recorded: the failures
    table is the diagnosis, and a caller — a background job, `pr batch` — needs
    the status to know the review step reviewed nothing. A partial run exits 0:
    the surviving findings are real, and the derived line already reflects
    whether anything blocking is open.
    """
    if not outcome.nothing_ran:
        return 0
    core.log.error("No part of the review ran — every agent failed. See Agent Failures above.")
    return 1


def _run_review(args, argv: list[str], ctx: pr.context.ResolvedContext,
                generator_version: str) -> review.run.ReviewOutcome:
    """Review the PR *ctx* names.

    ``ctx`` comes from ``main()``, which resolved it to take the run lock.
    Resolving it again from the same inputs cost a ``gh`` round trip per review
    and left two independently derived copies of the one value this branch
    exists to derive once.
    """
    pr_number = str(ctx.pr_number)
    review_file = review_file_path(ctx.repo, pr_number)
    review_file.parent.mkdir(parents=True, exist_ok=True)

    trail = Trail.start(
        script=SCRIPT,
        context={"repo": ctx.repo, "pr": pr_number, "branch": ctx.branch, "mode": "pr"},
        debug=getattr(args, "debug", False),
    )

    try:
        return review.run.run_pr_review(
            ctx, _flags(args, argv, generator_version), review_file, trail=trail)
    except Exception as exc:
        cli.boundary.reraise(trail, exc)
    finally:
        trail.finish()


def _run_self_review(args, argv: list[str],
                     generator_version: str = "") -> review.run.ReviewOutcome:
    """Review a local checkout.

    Resolution lives here rather than in `review.run` because acquiring the
    checkout is what the run lock protects: the lock is claimed between
    resolving the target and switching to it, and a resolver that did both
    would have to take a process-lifetime lock from inside the library.
    """
    pr_input = args.positional[0] if args.positional else ""
    repo_dir = args.repo_dir or ""
    recover = getattr(args, "recover", False)

    is_pr = False
    is_branch = False
    if pr_input:
        if pr.context.is_pr_ref(pr_input):
            is_pr = True
        else:
            is_branch = True

    if is_branch:
        pr_input = review.worktree.resolve_branch_input(pr_input, repo_dir)

    # Only a branch name is a worktree target — passing a PR ref here would have
    # the resolver create a worktree on a branch named after the PR number.
    wt_path = review.worktree.resolve_wt_path(repo_dir, pr_input if is_branch else "")

    # Reached only when --self was passed, so there is no PR to name and the
    # context resolves from git alone. Invoked through `pr`, the same depth is
    # declared by REVIEW_SELF_NEED; this is the direct-invocation path, and the
    # two have to agree or `review --self` would still spend the `gh`
    # call `pr review --self` no longer does. resolve_at escalates to REMOTE on
    # its own when a PR reference is passed alongside.
    ctx = pr.context.resolve_at(
        pr.context.ContextDepth.LOCAL,
        pr_ref=pr_input if is_pr else None,
        branch=pr_input if is_branch else None,
        repo_dir=wt_path,
    )
    repo = ctx.repo

    # The LOCAL rung leaves pr_number None by contract, so a self-review asks
    # for it here rather than getting it from the context. It is wanted but not
    # needed: when the branch already has an open PR, the number is what fetches
    # the reply threads that keep a re-review from repeating findings already
    # answered there — the `--self --fix --push` flow in self-review.md. When
    # GitHub cannot be reached the lookup yields nothing and the run proceeds as
    # a pre-PR self-review, which is the whole point of resolving locally first.
    #
    # The same call carries the PR's base, which is what a stacked branch is
    # measured against. Folded back onto the context rather than carried
    # alongside it, so everything downstream reads one resolved value — the
    # number reaching `run_self_review` through `ctx.pr_number` is what makes
    # the PR-aware metadata path reachable from a plain `--self` at all.
    found = (
        pr.context.BranchPR(number=ctx.pr_number, base=ctx.base) if ctx.pr_number
        else pr.context.pr_number_if_reachable(repo, ctx.branch)
    )
    ctx = replace(ctx, pr_number=found.number, base=found.base)
    pr_number = str(found.number) if found.number else ""

    # Before the checkout is switched or a --fix pass edits it: two `--self`
    # runs on one branch are two processes committing to it. What `review`
    # declares for this argv in cli.registry — `--self` holds the target and
    # the resolved checkout. A no-op when `pr` launched us.
    #
    # Given a PR or a branch, the body may switch to another tree below; that
    # one is locked once it is known. The resolved checkout is held from here
    # regardless, even where the switch then writes elsewhere: an accepted
    # over-lock, which can refuse an unrelated run in that tree but never lets
    # two runs write one checkout, and keeps the declaration the one source.
    cli.locks.claim_declared("review", argv, ctx, command=" ".join([SCRIPT] + argv))

    return _run_self_review_body(
        ctx, pr_number, args, argv, generator_version, repo, is_pr, is_branch,
        pr_input, wt_path, recover, repo_dir,
    )


def _run_self_review_body(
    ctx: pr.context.ResolvedContext, pr_number: str, args, argv: list[str],
    generator_version: str,
    repo: str, is_pr: bool, is_branch: bool, pr_input: str, wt_path: str, recover: bool,
    repo_dir: str,
) -> review.run.ReviewOutcome:
    """The rest of a self-review, once its identity is resolved and locked."""
    wt_cleanup: review.worktree.WorktreeResult | None = None

    if is_pr:
        if not args.skip_user_verification:
            review.completion.verify_pr_ownership(pr_number, repo)
        wt_cleanup = review.worktree.switch_to_pr_branch(pr_number, repo, wt_path)
        if wt_cleanup:
            wt_path = wt_cleanup.path
    elif is_branch:
        wt_cleanup = review.worktree.switch_to_branch(pr_input, wt_path)
        if wt_cleanup:
            wt_path = wt_cleanup.path

    # The switch above is the first point at which the tree this run writes to
    # is known: the claim at entry held the resolved checkout, which for these
    # two paths need not be the one written. Lock it now, through the one late
    # acquisition, which requires the target already held — the claim at entry
    # holds it.
    #
    # Gated on `is_pr or is_branch`, not on `wt_cleanup` truthiness:
    # `switch_to_pr_branch`/`switch_to_branch` return None when the checkout is
    # already on the target branch, which is exactly the re-review flow this
    # lock exists to cover (`pr review --self --fix --push` run from inside a
    # branch that already has the PR checked out). `wt_path` names the right
    # tree whether or not an actual switch happened.
    if is_pr or is_branch:
        core.run_lock.lock_checkout(
            ctx.target_dir, Path(wt_path),
            command=" ".join([SCRIPT] + argv), started=pr.state.now_iso(),
        )

    # Read HEAD after the switch — checking out a PR hard-resets the worktree to
    # the remote head, so ctx.head_sha can predate the commit the pipeline recorded.
    recover_head_sha = pr.context.head_sha(wt_path) if recover else ""

    review_dir = review.paths.self_review_dir(repo, ctx.branch or "")
    review_dir.mkdir(parents=True, exist_ok=True)

    trail = Trail.start(
        script=SCRIPT,
        context={"repo": repo, "pr": pr_number, "branch": ctx.branch, "mode": "self"},
        debug=getattr(args, "debug", False),
    )

    try:
        return review.run.run_self_review(
            ctx, _flags(args, argv, generator_version), review_dir, wt_path,
            recover_head_sha=recover_head_sha, trail=trail,
        )
    except Exception as exc:
        cli.boundary.reraise(trail, exc)
    finally:
        trail.finish()
        if wt_cleanup:
            review.worktree.cleanup_self_review_worktree(wt_cleanup, repo_dir)


@cli.boundary.reports_owner_boundary
def main(argv: list[str] | None = None, *,
         install_signal_handler: bool = True) -> int:
    """Parse *argv* and run the review it asks for.

    The version is resolved rather than injected. It used to be a parameter,
    on the grounds that only `ai/bin` could read the release manifest — which
    meant the in-process caller, `pr review`, passed nothing and every review
    it produced recorded `generator: … unknown` while the shim recorded the
    real build. `core.version` resolves the same manifest from under `ai/lib`,
    so both entry points now state one answer.

    `install_signal_handler` defaults True because the common caller is the
    `ai/bin` shim, for which this is the whole process. An in-process caller
    that owns its own handler passes False.
    """
    # Only when this module is the process. `pr review` reaches here having
    # installed the identical handler at its own entry point, and a second
    # install would replace the caller's without chaining or restoring it.
    if install_signal_handler:
        core.proc.install_stop_handler(core.log.interrupted)

    argv = list(sys.argv[1:] if argv is None else argv)
    parsed = build_parser().parse_args(argv)

    if parsed.version:
        print(core.version.version_string(SCRIPT))
        return 0

    with _json_summary_stdout(parsed.json_summary) as json_stdout_fd:
        return _main_body(parsed, argv, json_stdout_fd)


@contextlib.contextmanager
def _json_summary_stdout(json_summary_wanted: bool):
    """Give the run a stdout the summary alone reaches, and put it back after.

    Under `--json-summary` the only thing on stdout is the summary line, so
    every log this run would have printed there is redirected to stderr and
    the real stdout is saved for `_emit_json_summary` to write to.

    Restoring is what makes this in-process-safe. As a subprocess the fd table
    died with the child; in one process, a `dup2` that is never undone leaves
    `pr`'s own stdout pointing at stderr for the whole rest of the invocation,
    so `pr fix`'s describe pass and `pr status`'s state dump would write their
    stdout to stderr and a consumer reading the JSON would get nothing.
    """
    if not json_summary_wanted:
        yield None
        return

    real_stdout_fd = os.dup(sys.stdout.fileno())
    saved_stdout = sys.stdout
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    sys.stdout = os.fdopen(sys.stdout.fileno(), "w", closefd=False)
    try:
        yield real_stdout_fd
    finally:
        # Order matters: flush what the run wrote to the redirected wrapper
        # before the descriptor under it is pointed back, or that output
        # lands on the caller's stdout as though it were the summary.
        sys.stdout.flush()
        os.dup2(real_stdout_fd, sys.stdout.fileno())
        sys.stdout = saved_stdout
        os.close(real_stdout_fd)


def _main_body(parsed, argv: list[str], json_stdout_fd: int | None) -> int:
    """The run itself, with stdout already arranged for the summary."""

    if not parsed.repo_dir and os.environ.get("REPO_DIR"):
        parsed.repo_dir = os.environ["REPO_DIR"]

    # Flatten positional args, incorporating --pr and --branch from dispatcher
    parsed.positional = list(parsed.args)
    if parsed.pr and not parsed.positional:
        parsed.positional = [parsed.pr]
    elif parsed.branch and not parsed.positional:
        parsed.positional = [parsed.branch]

    core.workbench_paths.reviews_dir().mkdir(parents=True, exist_ok=True)

    if parsed.fix and not parsed.self_review:
        core.log.error("--fix requires --self")
        return 1

    if parsed.push and not parsed.fix:
        core.log.error("--push requires --fix")
        return 1

    if parsed.recover and parsed.force:
        core.log.error("--recover and --force are mutually exclusive")
        return 1

    # Above the --self dispatch rather than below it: the two flags contradict
    # each other whichever flow reads them, and a check that ran only on the PR
    # path let `--self --no-post --post` through to a self review that ignored
    # --no-post and forwarded --post to the orchestrate publishing gate.
    if parsed.no_post and parsed.post:
        core.log.error("--no-post and --post are mutually exclusive")
        return 1

    if parsed.track:
        err = _track_error(parsed)
        if err:
            core.log.error(err)
            return 1
        return _run_file_tracked(parsed, argv)

    # Every --focus / --focus-file is read and checked here, before either
    # flow spends anything: oversized, unreadable, or untitled text is refused,
    # never truncated.
    try:
        parsed.focus_texts = review.focus.operator_texts(parsed)
        review.focus.operator_areas(parsed.focus_texts)
    except review.focus.FocusInputError as exc:
        core.log.error(str(exc))
        return 1

    # One line, and the whole of it: the review's `generator:` marker names
    # the tool and both versions behind it. The earlier form took the last
    # line of the two-line `--version` output, which dropped the tool's own
    # version and left the marker naming only the workbench build.
    generator_version = core.version.generator(SCRIPT)

    if parsed.self_review:
        outcome = _run_self_review(parsed, argv, generator_version)
        _emit_json_summary(json_stdout_fd, outcome)
        return _exit_status(outcome)

    command = parsed.positional[0] if parsed.positional else ""
    if not command:
        build_parser().print_help()
        return 1

    if command in _REMOVED:
        core.log.error(f"'{command}' subcommand removed. Use: {_REMOVED[command]}")
        return 1

    pr_arg, branch_arg = pr.context.classify_target(command)
    ctx = pr.context.resolve(pr_ref=pr_arg, branch=branch_arg, repo_dir=parsed.repo_dir)
    # A no-op when pr launched us. The target only: this path sets up a
    # worktree of its own for the PR and resets it, so the tree it writes to is
    # not the one resolved here. `review.run` locks that one once it exists.
    cli.locks.claim_declared("review", argv, ctx, command=" ".join([SCRIPT] + argv))
    outcome = _run_review(parsed, argv, ctx, generator_version)
    _emit_json_summary(json_stdout_fd, outcome)
    return _exit_status(outcome)
