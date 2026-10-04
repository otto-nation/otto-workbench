"""Fetch PR review threads, compute lifecycle states, and output status.

What is left here is what an entry point is: argument parsing, the flag
conflicts that have to be refused before anything runs, context resolution, the
run lock and the trail, and the dispatch that picks one of five phases.
`review.comment_threads.run_threads` is the one call every phase goes through;
that module is what knows which of `pr.triage`, `fix.comments`,
`pr.settlement`, `pr.thread_replies`, `review.closeout` to call and in what
order, and it owns the dashboard-to-stderr/JSON-to-stdout rendering and the
local state file this command used to manage directly.

Two axes, and they have to stay apart: `--triage`/`--fix`/`--finish`/`--reply`/
`--settle` choose the work; `--post` decides whether it leaves the machine.
`--settle` publishes nothing and refuses `--post` and every other phase flag
alongside it. `--track` / `--track-all` are not implied by `--finish`.

A comment `pr comments` has already read is dropped from triage decomposition
(`review.comment_threads.mark_seen`), keyed on the comment id *and*
`last_edited_at` — see `pr.comments` for how that stamp is fetched.

`main` returns rather than exits, as every module under `cli/` does; the shim at
`ai/bin/review-threads` owns the process exit. That now holds through the
phases too: `closeout.finish_deferred_work` reports a refused `--track` rather
than exiting from under this module.

Usage:
  review-threads [--pr NUMBER] [--branch NAME] [--repo-dir PATH]
  review-threads --triage
  review-threads --fix
  review-threads --settle THREAD_ID [--as fixed|dismissed|already_addressed]
  review-threads --finish
"""

# doc-group: cli

from __future__ import annotations

import argparse
import sys

import core.log
import core.publishing
import core.run_lock
from core.trail import Trail, add_trail_args
import pr.comments_fix
import pr.context
import pr.settlement
import pr.state
import pr.thread_replies
from pr.fix import FixOutcome
import review.closeout
import review.comment_threads

# A literal, not `Path(__file__).name`: this module is `review_threads.py` while
# the command is `review-threads`, and the trail records the command.
SCRIPT = "review-threads"



def build_parser() -> argparse.ArgumentParser:
    # Two axes, and the help text has to keep them apart: --triage/--fix/--finish
    # /--reply/--settle choose the work, --post decides whether it leaves the
    # machine. Read as peers, `--finish --post` looks like it says publish twice,
    # and the pair gets typed as one unit — which is how a bare --finish stops
    # being the way to preview a closeout.
    parser = argparse.ArgumentParser(
        description="PR review threads. Phase flags (--triage/--fix/--finish/"
                    "--reply/--settle) pick the work; --post publishes it.")
    parser.add_argument("--pr", metavar="NUM|URL", help="PR number or URL")
    parser.add_argument("--branch", metavar="NAME",
                        help="Branch name (resolved via resolve-branch)")
    parser.add_argument("--triage", action="store_true",
                        help="Phase: classify threads via AI and auto-resolve verified")
    parser.add_argument("--fix", action="store_true",
                        help="Phase: triage threads and apply mechanical fixes via "
                             "Claude agent")
    parser.add_argument("--no-verify", action="store_true",
                        help="Skip the verify gate after --fix. The gate runs the "
                             "project's own checks against each claimed fix and "
                             "demotes the ones that do not hold up; without it "
                             "every fix publishes as unverified")
    parser.add_argument("--finish", action="store_true", dest="finish",
                        help="Phase: close out deferred work — replies, tracking "
                             "issue, summary. Drafts them unless --post is given")
    parser.add_argument("--track", action="append", default=[], metavar="THREAD_ID",
                        help="File this deferred thread on the tracking issue "
                             "(repeatable). Deferral is a per-thread decision, so "
                             "--finish files nothing unless told which threads")
    parser.add_argument("--track-all", action="store_true",
                        help="File every deferred thread. Only for a set the user "
                             "has actually reviewed")
    parser.add_argument("--post", action="store_true",
                        help="Gate, not a phase: publish whatever the chosen phase "
                             "produced — replies, summaries, resolutions "
                             "(default: print drafts to stderr and post nothing)")
    parser.add_argument("--repo-dir", "--worktree", metavar="PATH",
                        help="Git worktree directory (skips git toplevel detection)")
    parser.add_argument("--reply", metavar="THREAD_OR_COMMENT_ID",
                        help="Reply to one thread, editing our standing reply if it "
                             "is still the last comment. Accepts a thread node ID, a "
                             "comment ID, or a #discussion_r... URL. A write like any "
                             "other: needs --post to leave the machine")
    parser.add_argument("--body-file", metavar="PATH",
                        help="File holding the --reply body ('-' for stdin)")
    parser.add_argument("--settle", action="append", default=[], metavar="THREAD_ID",
                        help="Phase: record that you settled this thread by hand "
                             "(repeatable). Writes local state and nothing else; "
                             "--finish then replies, resolves and reports it like "
                             "any other settled thread")
    parser.add_argument("--as", dest="settle_as", default=FixOutcome.FIXED.value,
                        choices=[o.value for o in pr.settlement.SETTLE_OUTCOMES],
                        help="What --settle records (default: fixed)")
    parser.add_argument("--reason", metavar="TEXT",
                        help="Why, for --settle --as dismissed — it becomes the "
                             "reply the reviewer reads")
    parser.add_argument("--commit", metavar="SHA",
                        help="The commit carrying a --settle --as fixed change, "
                             "for a fix that landed away from the line the thread "
                             "is anchored to (default: inferred from that line). "
                             "Applies to every --settle in the run, so a batch "
                             "where only some threads need it takes two runs")
    add_trail_args(parser)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Before --fix widens itself into --triage, so the conflict named is the one
    # that was typed.
    if args.settle:
        conflicting = [
            name for name, on in (
                ("--triage", args.triage), ("--fix", args.fix),
                ("--finish", args.finish), ("--reply", bool(args.reply)),
                ("--post", args.post),
            ) if on
        ]
        if conflicting:
            core.log.error(
                f"--settle records local state and publishes nothing, so it cannot "
                f"run with {', '.join(conflicting)}. Record the settlement, read "
                f"it back, then run `{pr.comments_fix.CLOSEOUT_COMMAND}`"
            )
            return 1

    # The reciprocal of the guard above, and for the same reason: --reply is
    # dispatched on its own and returns before any phase runs, so a phase named
    # beside it would be dropped. --post is not in the list — it is the gate
    # that lets the reply leave the machine, not a second phase.
    if args.reply:
        conflicting = [
            name for name, on in (
                ("--triage", args.triage), ("--fix", args.fix),
                ("--finish", args.finish),
            ) if on
        ]
        if conflicting:
            core.log.error(
                f"--reply writes one thread and nothing else, so it cannot run "
                f"with {', '.join(conflicting)}. Post the reply, then run the "
                f"phase as its own command"
            )
            return 1

    if args.fix:
        args.triage = True
    with core.publishing.run(post=args.post):
        if not args.post and not args.settle:
            core.log.info("Draft mode — nothing is posted to GitHub. Re-run with --post to publish.")

        ctx = pr.context.resolve(
            pr_ref=args.pr, branch=args.branch, repo_dir=args.repo_dir,
        )
        repo = ctx.repo
        pr_number = ctx.pr_number
        if pr_number is None:
            core.log.error("No PR found for current branch")
            return 1

        if args.reply:
            return pr.thread_replies.run_reply(ctx, args.reply, args.body_file)
        if args.settle:
            return pr.settlement.run_settle(
                ctx, args.settle, args.settle_as, args.reason or "", args.commit or "",
            )
        branch = ctx.branch
        # Called for its raise as much as its value: a bare-repo run must fail here,
        # before the lock and the trail.
        worktree = ctx.require_worktree()
        head_sha = ctx.head_sha

        # A no-op when pr launched us — we resolve the same target and find its key
        # already in WORKBENCH_RUN_LOCK.
        # Acquired before Trail.start so contention costs no trail artifacts.
        core.run_lock.claim_for_process(
            ctx.target_dir,
            command=" ".join([SCRIPT, *(argv if argv is not None else sys.argv[1:])]),
            started=pr.state.now_iso(),
            # This run's --fix pass commits in that checkout; no worktree switch
            # happens on this path, so it is the tree that gets written to.
            worktree=worktree,
        )

        trail = Trail.start(
            script=SCRIPT,
            context={"repo": repo, "pr": pr_number, "branch": branch},
            debug=args.debug,
        )

        try:
            return review.comment_threads.run_threads(trail, args, ctx)
        except Exception as exc:
            trail.error("unexpected_error", str(exc))
            raise
        finally:
            trail.finish()
