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
(`pr.owed_feedback.mark_seen`), keyed on the comment id *and*
`last_edited_at` — see `pr.comments` for how that stamp is fetched.

`main` returns rather than exits, as every module under `cli/` does; the shim at
`ai/bin/review-threads` owns the process exit. That now holds through the
phases too: `closeout.finish_deferred_work` reports a refused `--track` rather
than exiting from under this module. A contended run lock exits 75
(EX_TEMPFAIL) rather than 1 — retry once the holder finishes.

Exit codes, remote writes and examples are `CONTRACT` below.
"""

# doc-group: cli

from __future__ import annotations

import argparse
import sys

import cli.boundary
import cli.contract_notes
import cli.locks
import cli.needs
import core.command_contract
import core.log
import core.publishing
from core.exit_codes import ExitCode
from core.trail import Trail, add_trail_args
import gh.pr_data
import fix.resume
import pr.comments_fix
import pr.context
import pr.fix_checkpoint
import pr.settlement
import pr.thread_replies
from pr.fix import FixOutcome
import review.closeout
import review.comment_threads
import review.review_body_reply

# A literal, not `Path(__file__).name`: this module is `review_threads.py` while
# the command is `review-threads`, and the trail records the command.
SCRIPT = "review-threads"

_POST_HOW = (
    "REST via pr.comments._gh_post -> gh.client.api (thread replies, "
    "issue-comment summary, PR body PATCH); GraphQL via "
    "pr.comments.resolve_thread -> gh.client.graphql (resolutions). "
    "A --track/--track-all tracking issue at --finish is "
    "review.issue.create_issue (GitHub through gh.client; Linear through "
    "the linear CLI). Gated by core.publishing.enabled()."
)
_POST_REFUSED = (
    "a failed write is reported per thread (resolve_verified_threads "
    "and reply drains continue); nothing already posted is rolled back"
)
_FINISH_POST_HOW = (
    "review.closeout.push_held_commit -> git.push.push(gated=True), "
    "plain, never forced; args=('--no-verify',) when --no-push-verify. "
    "unpublished_commit runs that before any GitHub write; then the "
    "--post GitHub writes."
)
_FINISH_POST_REFUSED = (
    "the summary is not posted (run_threads returns 1). git.push.report "
    "prints Resume on REFUSED/LOST; a HELD push prints none. "
    "PushBlock.message names pr push --expect then "
    "pr comments --finish --post"
)
_FIX_POST_HOW = (
    "fix.engine._land -> git.land.land(gated=True) -> git.push.push, "
    "plain, never forced. A publishing hold "
    "(pr.triage_round.hold_after_verify / TriagedRound) drafts the push; "
    "--finish --post is what lands a held commit."
)
_FIX_POST_REFUSED = (
    "a refused or held push leaves the commit local (not exit 1 unless "
    "the commit itself failed); --finish --post lands a held one"
)

CONTRACT = core.command_contract.CommandContract(
    summary="Fetch and manage PR review threads. Phase flags pick the "
            "work; --post publishes it. Without --post, GitHub writes "
            "and the held-commit push are drafted.",
    groups=(
        core.command_contract.FlagGroup(
            "Phases",
            ("--triage", "--fix", "--finish", "--reply", "--settle")),
        core.command_contract.FlagGroup(
            "Resume (with --fix)", ("--resume", "--no-resume")),
        core.command_contract.FlagGroup(
            "Deferral", ("--track", "--track-all")),
        core.command_contract.FlagGroup(
            "Settle details",
            ("--as", "--reason", "--commit", "--body-file")),
        core.command_contract.FlagGroup(
            "Gates", ("--post", "--no-verify", "--no-push-verify")),
    ),
    remote_effects=(
        core.command_contract.RemoteEffect(
            "--post",
            "thread replies, thread resolutions, the round summary "
            "comment, and (with --track/--track-all at --finish) the "
            "tracking issue",
            "GitHub", _POST_HOW, _POST_REFUSED),
        core.command_contract.RemoteEffect(
            "--finish --post",
            "a held fix commit pushed to refs/heads/<branch>",
            "origin", _FINISH_POST_HOW, _FINISH_POST_REFUSED),
        core.command_contract.RemoteEffect(
            "--fix --post",
            "the fix commit pushed to refs/heads/<branch> when "
            "publishing is still enabled; a hold (discussion still "
            "open) leaves it local for --finish --post",
            "origin", _FIX_POST_HOW, _FIX_POST_REFUSED),
    ),
    exits=(
        core.command_contract.ExitOutcome(
            ExitCode.OK,
            "dashboard printed; the chosen phase completed (draft or "
            "published); --settle recorded locally"),
        core.command_contract.ExitOutcome(
            ExitCode.FAILURE,
            "no PR; --settle/--reply flag conflicts; missing --reply "
            "body; no worktree; unpublished held commit; --finish on "
            "an incomplete fetch; finish_deferred_work refused (bad "
            "--track, summary refused); fix commit failed; owner "
            "boundary"),
        core.command_contract.ExitOutcome(
            ExitCode.USAGE,
            "bad flags"),
        core.command_contract.ExitOutcome(
            ExitCode.TEMPFAIL,
            "another pr run holds this branch or checkout, or no otto-queue "
            "slot came in time; retry once it finishes"),
        core.command_contract.ExitOutcome(
            ExitCode.CANCELLED,
            "waiting for an otto-queue slot was cancelled"),
    ),
    examples=(
        core.command_contract.Example(
            "pr comments --triage --post",
            "classify threads and resolve verified ones on GitHub"),
        core.command_contract.Example(
            "pr comments --fix --post",
            "triage, apply mechanical fixes, push if publishing is "
            "still enabled"),
        core.command_contract.Example(
            "pr comments --finish --post",
            "push a held fix commit, then replies, resolutions, "
            "summary"),
        core.command_contract.Example(
            "pr comments --reply <id> --body-file reply.md --post",
            "post one thread reply"),
    ),
    notes=(
        cli.contract_notes.QUEUED,
        cli.contract_notes.PUSH_WAIT,
        "Without --post, GitHub writes and the held-commit push are "
        "drafted.",
        "--settle writes local state only and refuses --post.",
        "--no-verify skips the verify gate after --fix, not git's "
        "pre-push hook.",
        "--fix --post does not land a held commit; --finish --post "
        "does.",
    ),
)


def build_parser() -> argparse.ArgumentParser:
    # Two axes, and the help text has to keep them apart: --triage/--fix/--finish
    # /--reply/--settle choose the work, --post decides whether it leaves the
    # machine. Read as peers, `--finish --post` looks like it says publish twice,
    # and the pair gets typed as one unit — which is how a bare --finish stops
    # being the way to preview a closeout.
    parser = argparse.ArgumentParser(
        prog="pr comments",
        description="PR review threads. Phase flags (--triage/--fix/--finish/"
                    "--reply/--settle) pick the work; --post publishes it.")
    parser.add_argument("--pr", metavar="NUM|URL", help="PR number or URL")
    parser.add_argument("--branch", metavar="NAME",
                        help="Branch name (resolved via resolve-branch)")
    parser.add_argument("--triage", action="store_true",
                        help="Phase: classify threads via AI; each verified "
                             "thread needs --post to resolve on GitHub "
                             "(without it the resolutions are drafted)")
    parser.add_argument("--fix", action="store_true",
                        help="Phase: triage threads and apply mechanical fixes via "
                             "Claude agent")
    resume = parser.add_mutually_exclusive_group()
    resume.add_argument("--resume", action="store_true",
                        help="Require an interrupted --fix pass to resume "
                             "(exit 1 if none). Only with --fix")
    resume.add_argument("--no-resume", action="store_true",
                        help="Discard an interrupted --fix pass and start over. "
                             "Only with --fix")
    parser.add_argument("--no-verify", action="store_true",
                        help="Skip the verify gate after --fix. The gate runs the "
                             "project's own checks against each claimed fix and "
                             "demotes the ones that do not hold up; without it "
                             "every fix publishes as unverified")
    parser.add_argument("--no-push-verify", action="store_true",
                        help="Skip the pre-push hook when --finish --post pushes a "
                             "held commit. Distinct from --no-verify, which skips "
                             "the verify gate after --fix, not git hooks")
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
    parser.add_argument("--reply", metavar="THREAD_COMMENT_OR_REVIEW_ID",
                        help="Reply to one thread, editing our standing reply if it "
                             "is still the last comment. Accepts a thread node ID, a "
                             "comment ID, or a #discussion_r... URL. Also answers a "
                             "review body — a review ID, rb-REVIEW_ID or "
                             "rb-REVIEW_ID-N — in the summary comment, since "
                             "GitHub cannot reply to one. A write like any other: "
                             "needs --post to leave the machine")
    parser.add_argument("--body-file", metavar="PATH",
                        help="File holding the --reply body ('-' for stdin)")
    parser.add_argument("--settle", action="append", default=[], metavar="THREAD_ID",
                        help="Phase: record that you settled this thread by hand "
                             "(repeatable). An id on the PR but not in the fix "
                             "snapshot is recorded from the PR rather than refused. "
                             "Writes local state and nothing else; --finish then "
                             "replies, resolves and reports it like any other "
                             "settled thread")
    parser.add_argument("--as", dest="settle_as", default=FixOutcome.FIXED.value,
                        choices=[o.value for o in pr.settlement.SETTLE_OUTCOMES],
                        help="What --settle records (default: fixed)")
    parser.add_argument("--reason", metavar="TEXT",
                        help="Why, for --settle --as dismissed — it becomes the "
                             "reply the reviewer reads")
    parser.add_argument("--commit", metavar="SHA",
                        help="The commit carrying a --settle --as fixed change, "
                             "for a fix that landed away from the line the thread "
                             "is anchored to. Without it nothing is inferred and the row "
                             "is recorded with no commit cited. "
                             "Applies to every --settle in the run, so a batch "
                             "where only some threads need it takes two runs")
    add_trail_args(parser)
    return parser


def _github_only(
    ctx: pr.context.ResolvedContext, args: argparse.Namespace, route, body: str | None,
) -> int:
    """Run a thread `--reply` or a `--settle` write, which touches GitHub and no checkout."""
    if args.reply:
        return pr.thread_replies.run_reply(
            ctx, args.reply, body or "", pr_data=route.pr_data,
            review_body_ids=route.review_body_ids,
        )
    ckpt = pr.fix_checkpoint.load(ctx.target_dir)
    phase = ckpt.phase if ckpt else None
    status = pr.settlement.run_settle(
        ctx, args.settle, args.settle_as, args.reason or "", args.commit or "",
        answers=review.closeout.tracking_answers(ctx.target_dir, phase=phase),
        fetch_threads=lambda: {
            t.id: t
            for t in review.comment_threads.read_report(
                ctx, gh.pr_data.fetch_pr_data(ctx.repo, str(ctx.pr_number)),
            ).threads
        },
    )
    fix.resume.clear_if_adopted(ctx.target_dir)
    return status


def _run_locked(trail, args, ctx, route, body: str | None = None) -> int:
    """The work that runs under the lock: a review-body reply, or the phases."""
    if route is not None:
        return review.review_body_reply.run_review_body_reply(
            ctx, route, body or "", trail=trail)
    return review.comment_threads.run_threads(trail, args, ctx)


@cli.boundary.reports_owner_boundary
def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    argv = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(argv)

    if (args.resume or args.no_resume) and not args.fix:
        core.log.error("--resume and --no-resume require --fix")
        return 1

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
    # beside it would be dropped — whether it names a thread or a review body.
    # --post is not in the list — it is the gate that lets the reply leave the
    # machine, not a second phase.
    if args.reply:
        conflicting = [
            name for name, on in (
                ("--triage", args.triage), ("--fix", args.fix),
                ("--finish", args.finish),
            ) if on
        ]
        if conflicting:
            core.log.error(
                f"--reply writes one reply and nothing else, so it cannot run "
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
            worktree=cli.needs.comments_need(argv).worktree,
        )
        repo = ctx.repo
        pr_number = ctx.pr_number
        if pr_number is None:
            core.log.error("No PR found for current branch")
            return 1

        # Before the PR read, so a missing or empty body file costs no fetch.
        # Read once: `--body-file -` is stdin, and a second read would be empty.
        body = pr.thread_replies.load_reply_body(args.body_file) if args.reply else None
        if args.reply and body is None:
            return 1
        command = " ".join([SCRIPT, *argv])
        # Both reply paths take the loaded body. A thread target goes to
        # run_reply here; a review body is answered in the summary comment,
        # which writes the state file, so it falls through to the worktree
        # check, the lock and the trail below.
        route = review.review_body_reply.route_reply(ctx, args.reply) if args.reply else None
        if args.settle or (route is not None and not route.source):
            # GitHub-only writes: `comments` declares the target alone for
            # them. Taken before the early return, which used to skip it.
            cli.locks.claim_declared("comments", argv, ctx, command=command)
            return _github_only(ctx, args, route, body)
        branch = ctx.branch
        # Called for its raise: a bare-repo run must fail here, before the lock
        # and the trail. A review-body reply lands here too — it writes the state
        # file, so with no checkout of the branch it refuses rather than creating
        # one, though its lock is the target alone.
        ctx.require_worktree()

        # Target and checkout — this run's --fix pass commits in that checkout.
        # A no-op when pr launched us. Before Trail.start so contention costs
        # no trail artifacts.
        cli.locks.claim_declared("comments", argv, ctx, command=command)

        trail = Trail.start(
            script=SCRIPT,
            context={"repo": repo, "pr": pr_number, "branch": branch},
            debug=args.debug,
        )

        try:
            return _run_locked(trail, args, ctx, route, body)
        except Exception as exc:
            cli.boundary.reraise(trail, exc)
        finally:
            trail.finish()
