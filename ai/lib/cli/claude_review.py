"""Run Claude's reviewer agent on a PR with local worktree checkout and iterative review support.

The entry point, and only the entry point: the parser, the flag contradictions,
the signal handler, the run lock, and the choice of which flow to run. The
review itself is `review.run`'s.

Three things stay here rather than moving down a layer, each for its own reason.
The signal handler is process-level state, which a library must not install.
The run lock is claimed between resolving the self-review target and switching
the checkout to it — a resolver that did both would take a process-lifetime lock
from inside the library. And `version_string` lives in `ai/bin`, which nothing
under `ai/lib` can import, so the caller passes it in.

Usage:
  claude-review <pr_url_or_number>
  claude-review --no-post <pr_url_or_number>
  claude-review --self [<pr_url_or_number>]
  claude-review [--self] --recover [<pr_url_or_number>]
"""

# doc-group: cli

from __future__ import annotations

import argparse
import os
import signal
import sys
from pathlib import Path
from typing import Callable

from agent.registry import add_phase_skip_flags, phase_skips
from core import log
from core import proc
from core import run_lock
from core import workbench_paths
from core.tool_parser import handle_value_flags
from core.trail import Trail, add_trail_args
from pr import context as pr_context
from pr import state as pr_state
from review import completion as review_completion
from review import run as review_run
from review import worktree as review_worktree
from review.paths import review_file_path
from review.summary import json_summary

SCRIPT = "claude-review"
BIN_DIR = Path(__file__).resolve().parent.parent.parent / "bin"

DEFAULT_MAX_PARALLEL = 1

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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=SCRIPT,
        description="Run Claude's reviewer agent on a PR",
        add_help=True,
    )
    parser.add_argument("--no-post", action="store_true")
    parser.add_argument("--post", action="store_true")
    parser.add_argument("--submit", action="store_true")
    parser.add_argument("--self", action="store_true", dest="self_review")
    parser.add_argument("--fix", action="store_true")
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--skip-user-verification", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--recover", action="store_true")
    add_phase_skip_flags(parser)
    parser.add_argument("--disprove", action="store_true", default=None)
    parser.add_argument("--json-summary", action="store_true")
    parser.add_argument("--issue")
    parser.add_argument("--max-parallel", type=int, default=DEFAULT_MAX_PARALLEL)
    parser.add_argument("--max-cost", type=float)
    parser.add_argument("--model")
    parser.add_argument("--effort", choices=["low", "medium", "high"], default=None,
                        help="Effort preset (default: review.effort in config.yml, else medium)")
    parser.add_argument("--max-groups", type=int, default=None)
    parser.add_argument("--generated", action="store_true")
    parser.add_argument("--repo-dir", "--worktree", dest="repo_dir")
    parser.add_argument("--branch")
    parser.add_argument("--pr")
    parser.add_argument("-V", "--version", action="store_true")
    parser.add_argument("args", nargs="*")
    add_trail_args(parser)
    handle_value_flags(parser)
    return parser


def _flags(args, generator_version: str) -> review_run.ReviewFlags:
    """The parsed argv as the value the flows read."""
    return review_run.ReviewFlags(
        bin_dir=BIN_DIR,
        generator_version=generator_version,
        issue_link=args.issue or "",
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


def _emit_json_summary(fd: int | None, outcome: review_run.ReviewOutcome) -> None:
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


def _run_review(args, ctx: pr_context.ResolvedContext,
                generator_version: str) -> review_run.ReviewOutcome:
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
        return review_run.run_pr_review(
            ctx, _flags(args, generator_version), review_file, trail=trail)
    except Exception as exc:
        trail.error("unexpected_error", str(exc))
        raise
    finally:
        trail.finish()


def _run_self_review(args, generator_version: str) -> review_run.ReviewOutcome:
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
        if pr_context.is_pr_ref(pr_input):
            is_pr = True
        else:
            is_branch = True

    if is_branch:
        pr_input = review_worktree.resolve_branch_input(pr_input, repo_dir)

    # Only a branch name is a worktree target — passing a PR ref here would have
    # the resolver create a worktree on a branch named after the PR number.
    wt_path = review_worktree.resolve_wt_path(repo_dir, pr_input if is_branch else "")

    ctx = pr_context.resolve(
        pr=pr_input if is_pr else None,
        branch=pr_input if is_branch else None,
        repo_dir=wt_path,
    )
    repo = ctx.repo
    pr_number = str(ctx.pr_number) if ctx.pr_number else ""

    # Before the checkout is switched or a --fix pass edits it: two `--self`
    # runs on one branch are two processes committing to it. A no-op when `pr`
    # launched us — we resolve the same target and find its key already in
    # WORKBENCH_RUN_LOCK.
    run_lock.claim_for_process(
        ctx.target_dir,
        command=" ".join([SCRIPT] + sys.argv[1:]),
        started=pr_state.now_iso(),
    )

    wt_cleanup: review_worktree.WorktreeResult | None = None

    if is_pr:
        if not args.skip_user_verification:
            review_completion.verify_pr_ownership(pr_number, repo)
        wt_cleanup = review_worktree.switch_to_pr_branch(pr_number, repo, wt_path)
        if wt_cleanup:
            wt_path = wt_cleanup.path
    elif is_branch:
        wt_cleanup = review_worktree.switch_to_branch(pr_input, wt_path)
        if wt_cleanup:
            wt_path = wt_cleanup.path

    # Read HEAD after the switch — checking out a PR hard-resets the worktree to
    # the remote head, so ctx.head_sha can predate the commit the pipeline recorded.
    recover_head_sha = pr_context.head_sha(wt_path) if recover else ""

    repo_name = repo.split("/")[-1]
    branch_sanitized = (ctx.branch or "").replace("/", "-")
    review_dir = workbench_paths.reviews_dir() / f"{repo_name}-self-{branch_sanitized}"
    review_dir.mkdir(parents=True, exist_ok=True)

    trail = Trail.start(
        script=SCRIPT,
        context={"repo": repo, "pr": pr_number, "branch": ctx.branch, "mode": "self"},
        debug=getattr(args, "debug", False),
    )

    try:
        return review_run.run_self_review(
            ctx, _flags(args, generator_version), review_dir, wt_path,
            recover_head_sha=recover_head_sha, trail=trail,
        )
    except Exception as exc:
        trail.error("unexpected_error", str(exc))
        raise
    finally:
        trail.finish()
        if wt_cleanup:
            review_worktree.cleanup_self_review_worktree(wt_cleanup, repo_dir)


def main(argv: list[str] | None = None, *,
         version_string: Callable[[str], str] | None = None) -> int:
    """Parse *argv* and run the review it asks for.

    `version_string` is injected because it resolves the release manifest next
    to `ai/bin`, which this layer cannot import. The default keeps `--version`
    answering rather than crashing when a caller does not supply one — a test,
    or an import that only wants the parser.
    """
    version_of = version_string or (lambda name: f"{name} unknown")

    signal.signal(
        signal.SIGINT,
        lambda *_: (log.blank(), log.info("Interrupted"),
                    sys.exit(proc.INTERRUPT_RETURNCODE)))

    parsed = build_parser().parse_args(argv)

    if parsed.version:
        print(version_of(SCRIPT))
        return 0

    # JSON summary: save real stdout, redirect stdout to stderr
    json_stdout_fd = None
    if parsed.json_summary:
        json_stdout_fd = os.dup(sys.stdout.fileno())
        os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
        sys.stdout = os.fdopen(sys.stdout.fileno(), "w", closefd=False)

    if not parsed.repo_dir and os.environ.get("REPO_DIR"):
        parsed.repo_dir = os.environ["REPO_DIR"]

    # Flatten positional args, incorporating --pr and --branch from dispatcher
    parsed.positional = list(parsed.args)
    if parsed.pr and not parsed.positional:
        parsed.positional = [parsed.pr]
    elif parsed.branch and not parsed.positional:
        parsed.positional = [parsed.branch]

    workbench_paths.reviews_dir().mkdir(parents=True, exist_ok=True)

    if parsed.fix and not parsed.self_review:
        log.error("--fix requires --self")
        return 1

    if parsed.push and not parsed.fix:
        log.error("--push requires --fix")
        return 1

    if parsed.recover and parsed.force:
        log.error("--recover and --force are mutually exclusive")
        return 1

    # Above the --self dispatch rather than below it: the two flags contradict
    # each other whichever flow reads them, and a check that ran only on the PR
    # path let `--self --no-post --post` through to a self review that ignored
    # --no-post and forwarded --post to the orchestrate publishing gate.
    if parsed.no_post and parsed.post:
        log.error("--no-post and --post are mutually exclusive")
        return 1

    generator_version = version_of(SCRIPT).splitlines()[-1] or "unknown"

    if parsed.self_review:
        outcome = _run_self_review(parsed, generator_version)
        _emit_json_summary(json_stdout_fd, outcome)
        if json_stdout_fd is not None:
            os.close(json_stdout_fd)
        return 0

    command = parsed.positional[0] if parsed.positional else ""
    if not command:
        build_parser().print_help()
        return 1

    if command in _REMOVED:
        log.error(f"'{command}' subcommand removed. Use: {_REMOVED[command]}")
        return 1

    pr_arg, branch_arg = pr_context.classify_target(command)
    ctx = pr_context.resolve(pr=pr_arg, branch=branch_arg, repo_dir=parsed.repo_dir)
    # A no-op when pr launched us — we resolve the same target and find its key
    # already in WORKBENCH_RUN_LOCK.
    run_lock.claim_for_process(
        ctx.target_dir,
        command=" ".join([SCRIPT] + sys.argv[1:]),
        started=pr_state.now_iso(),
    )
    outcome = _run_review(parsed, ctx, generator_version)
    _emit_json_summary(json_stdout_fd, outcome)
    if json_stdout_fd is not None:
        os.close(json_stdout_fd)
    return 0
