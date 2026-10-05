"""The three `pr` subcommands that used to be defined inside the binary.

`status`, `fix` and `gc` ran inside `ai/bin/pr`, which is not an importable
module, so `CommandSpec.handler` could not name them. They live here so the
field means one thing across the ten: a `"<module>:<attr>"` string that
importlib can resolve, or None.

`cmd_fix`'s three passes are in-process calls through `cli.dispatch`.
`create` has since moved to `cli.pr_create`, which owns its parser as well.

`cmd_review` and `cmd_comments` stay in `ai/bin/pr`. Both are argv shaping
ahead of a delegate the registry already names — `--self` injection, mode
routing — rather than commands in their own right, which is why the registry
points `review` and `comments` at the delegates themselves.

`pr fix` decides whether to run each pass by reading the same cached state
`pr status` prints (`_worth_running`). A cached "nothing to do" is honoured only
when the domain can say it was measured against the current HEAD —
`ReviewSummary.head_sha` for the review, the latest stored run's `headSha` for
CI. A verdict about another commit, or one that names no commit, re-runs the
pass and says why. Skipping a necessary pass is silent and permanent, so the
gate errs toward running whenever it cannot place the verdict. Each pass is
asked about the commit *its own child* will act on, and under `--pr` those
differ: `ctx.head_sha` is then the PR's remote head — right for CI, wrong for
the review, which `--self` runs against the worktree. Asking the review about
the remote head skips it after a clean review followed by unpushed commits.
The comment hint is not gated this way: it never spawns the comment pass, and
`CommentsSummary` records no commit, so a stale count costs a misleading line
rather than skipped work.
"""

# doc-group: cli

import json
import subprocess
import sys
from pathlib import Path

import cli.dispatch
from cli.registry import COMMANDS
import core.log
import core.publishing
from core.trail import Trail
import gh.budget
import pr.context
import pr.domains
import pr.state
import pr.supersession
import review.gc

# A command that stopped because the account's hourly GitHub quota is spent.
# Its own code because the remedy is nothing the caller did wrong and nothing
# a retry now would fix: the scheduled maintenance script reads this to log a
# skip rather than a failure, which is the distinction that sent someone
# looking for a broken token the first time.
#
# 75 is sysexits.h's EX_TEMPFAIL — "temporary failure, the user is invited to
# retry" — which is this situation exactly, and a code a reader can look up
# rather than one chosen for being free.
#
# Lives here, next to `cmd_gc`, the only Python producer. The binary re-exports
# it so `pr_cli.EXIT_BUDGET_EXHAUSTED` in the tests keeps reading the same
# object; the maintenance script cannot import it and compares against 75.
EXIT_BUDGET_EXHAUSTED = 75


def _run_pass(command: str, argv: list[str], ctx: pr.context.ResolvedContext, *,
              original_pr: str | None = None,
              original_branch: str | None = None,
              **kwargs) -> int:
    """Run one of `pr fix`'s passes in this process, and return its code."""
    spec = COMMANDS[command]
    return core.publishing.call_entry_point(
        spec.handler,
        cli.dispatch.delegate_argv(spec, argv, ctx,
                               original_pr=original_pr,
                               original_branch=original_branch),
        **kwargs,
    )


def cmd_status(argv: list[str], ctx: pr.context.ResolvedContext, **_kw) -> int:
    """Render unified status dashboard from cached state.

    The dashboard is a fold over the domain registry, not a list of renderers:
    a domain prints what it says about itself, in the order `PRState` declares
    it, and one that says nothing takes up no room. Push is the exception that
    is refreshed rather than read — it is a local git question, so `pr status`
    answers it now instead of reporting whatever the last write happened to see.

    The identity's head SHA is refreshed on the same grounds, and it reaches
    stdout the same way `push` does: both are written onto the loaded object
    before `state_to_dict` dumps it, so the JSON agrees with the dashboard
    rendered from it. A consumer reading `identity.head_sha` out of that dump
    gets the commit the checkout is on now, which is the one the staleness
    markers above were computed against.
    """
    wt = ctx.require_worktree()
    state = pr.state.load_state(ctx.target_dir)

    branch = state.identity.branch if state else ctx.branch
    repo = state.identity.repo if state else ctx.repo
    push = pr.domains.PushDomain.observed(wt, branch, updated_at=pr.state.now_iso())

    # Refreshed for the same reason push is, and it is the same kind of
    # question: `identity.head_sha` is from whenever state was last *written*,
    # so a commit made since leaves it naming the commit the domains were
    # measured against. Comparing them to it then always matches, and a verdict
    # about the previous commit renders as current — the supersession check
    # would be asking a stale value about itself. Held in memory: the dashboard
    # is a read, and persisting this would date the file by looking at it.
    if state:
        state.identity.head_sha = _worktree_head(wt, state.identity.head_sha)

    lines = pr.state.render_dashboard(state, push, repo=repo, branch=branch)
    print("\n".join(lines), file=sys.stderr)
    if state:
        json.dump(pr.state.state_to_dict(state), sys.stdout, indent=2)
        print()
    return 0


def _worktree_head(wt: Path, fallback: str) -> str:
    """What HEAD the checkout is actually on, or `fallback` if it cannot say.

    The live answer, which two callers need and neither can take from the state
    they were handed. `pr fix` gates the review pass on it because
    `review --self` reads the worktree while `ctx.head_sha` under `--pr`
    is the PR's *remote* head (see `review.pipeline._with_local_diff`): asking
    the gate the remote question skips the review after a clean pass followed
    by unpushed commits. `pr status` needs it because `identity.head_sha` is
    from whenever state was last written, so a commit made since leaves every
    domain being compared against the very SHA it was measured at.

    Falls back rather than raising, and that covers both ways the call can
    fail. It shells out with `cwd=` set, so a removed worktree or a missing git
    raises `OSError` instead of returning ""; it also carries a timeout, and
    `TimeoutExpired` is a `SubprocessError` rather than an `OSError`, so
    catching the latter alone would still let a hung `git rev-parse` take down
    a read-only `pr status`. Neither a gate deciding what to run nor a
    dashboard read should be what ends the command.
    """
    try:
        return pr.context.head_sha(str(wt)) or fallback
    except (OSError, subprocess.TimeoutExpired):
        return fallback


def _worth_running(domain: pr.domains.Domain, head_sha: str, *,
                   has_work: bool, name: str) -> bool:
    """Whether to run a pass, given what the cache last said about `domain`.

    The two ways of being wrong here are not symmetrical, and that asymmetry is
    the whole reason this exists:

    * Running a pass that turns out to be unnecessary costs one spawn. Every
      pass re-fetches its own subject — `ci-check` refetches the run,
      `review` re-reads the tree — so it finds nothing and says so.
    * *Skipping* a pass that was necessary is silent and permanent. Nothing
      downstream re-checks, and `pr fix` reports success having done nothing.

    So a cached "nothing to do" is honoured only when it was measured against
    the commit in hand. A verdict about another commit, or one that cannot name
    its commit at all, is not evidence about this one — the pass runs and finds
    out. That is the rule `pr-describe` already applies to itself, extended to
    the two passes whose skip costs more than their spawn.

    Cached work outranks the commit check: a stale red is still a reason to
    look, and the child decides what is actually broken.
    """
    if has_work:
        return True
    if not domain.updated_at:
        # Never written. That is an absent verdict, not a clean one.
        return True
    if domain.describes(head_sha):
        return False
    core.log.dim(f"{name}: the last check was against a different commit — "
            f"re-running rather than trusting it")
    return True


def cmd_fix(argv: list[str], ctx: pr.context.ResolvedContext, **_kw) -> int:
    """Run fix passes for CI, review, and comments.

    Three passes in one process. Each goes through `cli.dispatch`, which is
    what keeps them from leaking into each other: the review pass opening the
    publishing gate is not an authorisation for the describe pass to edit the
    PR body, and a pass that ends by calling `sys.exit` ends itself rather
    than the two after it.
    """
    wt = ctx.require_worktree()
    state = pr.state.load_state(ctx.target_dir)
    if not state:
        core.log.error("No state yet. Run pr ci, pr review, or pr comments first.")
        return 1

    exit_code = 0

    # Each pass is asked about the commit *its own child* will act on, and the
    # two are not the same under `--pr`. `ctx.head_sha` is then the PR's remote
    # head, which is the right question for CI — GitHub's runs are about what
    # was pushed — and the wrong one for the review, which `--self` runs
    # against the worktree (`review.pipeline._with_local_diff`). Asking the
    # review about the remote head skips it after a clean review followed by
    # unpushed commits: the local tree nobody has read is the one it declines
    # to look at, which is the silent miss this gate exists to prevent.
    review_sha = _worktree_head(wt, ctx.head_sha)

    review_findings = sum(state.review.finding_counts.values())
    if _worth_running(state.review, review_sha,
                      has_work=review_findings > 0, name="Review"):
        core.log.info(f"Fixing {review_findings} review finding(s)..." if review_findings
                 else "Reviewing...")
        # `--repo-dir` names the tree explicitly rather than letting the pass
        # re-derive one: without a target it resolves the worktree's current
        # branch, which is not always the one this run locked, and the two
        # then take separate locks on one checkout.
        rc = _run_pass(
            "review", ["--self", "--fix", "--repo-dir", str(wt)] + list(argv), ctx,
            original_pr=_kw.get("original_pr"),
            original_branch=_kw.get("original_branch"),
            # This process already has the handler `pr` installed; see
            # `cli.review_entry.main`.
            install_signal_handler=False,
        )
        if rc == pr.supersession.EXIT_SUPERSEDED:
            # Every remaining pass acts on the same branch, so a refusal that
            # says "this branch may not be worth working on" answers for all of
            # them. Continuing would spend the CI fix pass on the question the
            # review just declined to spend on.
            #
            # Reached in-process because `cli.dispatch` turns the pass's
            # `sys.exit(EXIT_SUPERSEDED)` back into a returncode. As a
            # subprocess the kernel did that; without the seam this branch
            # would be dead code and the refusal would end `pr fix` silently.
            core.log.error("Stopping — the review refused this branch as superseded.")
            core.log.dim(f"Resolve it, or re-run with {pr.supersession.OVERRIDE_FLAG} "
                    f"to override.")
            return pr.supersession.EXIT_SUPERSEDED
        if rc != 0:
            exit_code = 1

    ci_fixable = 0
    if state.ci.updated_at and state.ci.failure_count > 0:
        infra_count = state.ci.failure_kinds.get("infra", 0) + state.ci.failure_kinds.get("flaky", 0)
        ci_fixable = state.ci.failure_count - infra_count

    if _worth_running(state.ci, ctx.head_sha, has_work=ci_fixable > 0, name="CI"):
        core.log.blank()
        # The count is what the cache last saw, and the child re-fetches before
        # fixing anything — so it is reported as the reason for running, not as
        # the work about to be done.
        core.log.info(f"Fixing {ci_fixable} CI failure(s)..." if ci_fixable > 0
                 else "Checking CI...")
        rc = _run_pass(
            "ci", ["--fix"] + list(argv), ctx,
            original_pr=_kw.get("original_pr"),
            original_branch=_kw.get("original_branch"),
        )
        if rc != 0:
            exit_code = 1

    # Only ever a hint — this never spawns the comment pass — so a stale count
    # costs a misleading line rather than skipped work, and the gate above
    # would buy nothing: CommentsSummary records no commit, so it could never
    # say a verdict was about this one.
    actionable = state.comments.by_state.get("new", 0) + state.comments.by_state.get("contested", 0)
    if state.comments.updated_at and actionable > 0:
        core.log.blank()
        core.log.info(f"Comments: {actionable} actionable thread(s) — run pr comments --fix")

    # Last, because the description has to describe the branch as it ends up.
    # pr-describe re-resolves HEAD itself and no-ops when nothing landed, so
    # on a run where every fix pass was a skip it costs no AI call. It is not
    # free under --post: the publishing token is claimed before describe's HEAD
    # gate, which spends one or two gh calls even when nothing is revised. The
    # user's argv is not forwarded: --fix and friends mean nothing here.
    #
    # `--post` is the exception, and it is forwarded rather than passed along
    # with the rest for the same reason the rest are dropped: it is the one
    # flag that means something to describe. Editing the PR body is gated like
    # every other GitHub write, so without this a `pr fix --post` would push
    # its commits and post its replies and then draft the description alone.
    core.log.blank()
    describe_argv = ["--post"] if "--post" in argv else []
    if _run_pass("describe", describe_argv, ctx,
                 original_pr=_kw.get("original_pr"),
                 original_branch=_kw.get("original_branch")) != 0:
        exit_code = 1

    return exit_code


def cmd_gc(argv: list[str], ctx: pr.context.ResolvedContext, *, trail: Trail, **_kw) -> int:
    """Clean up stale PR artifacts across all domains.

    A sweep the GitHub budget cut short is reported as such, and exits
    non-zero. It is not "nothing to clean": nothing was *asked*, the artifacts
    it would have collected are still there, and the next scheduled cycle is
    the only thing that will look again. Reporting that as success is what let
    a maintenance run log "complete" for a cycle in which every question was
    refused.
    """
    # All user-scoped, so this works from a bare repo. Our own target is
    # skipped: we are holding its lock right now.
    local = review.gc.gc_reviews()
    outcome = (
        review.gc.prune_merged_reviews()
        + review.gc.prune_merged_targets(skip=ctx.target_dir, trail=trail)
    )
    total = local + outcome.pruned

    if outcome.cut_short:
        latch = gh.budget.latched(gh.budget.Resource.GRAPHQL)
        remedy = latch.remedy() if latch else gh.budget.BUDGET_EXHAUSTED_HINT
        core.log.warn(
            f"GC: stopped early — the GitHub API budget is spent, so the PRs "
            f"behind this sweep were never asked about ({remedy})")
        if total:
            core.log.info(f"GC: cleaned {total} item(s) before it ran out")
        trail.summary(
            "gc_cut_short",
            f"budget exhausted after cleaning {total} item(s)",
            data={"cleaned": total, "reset": (latch.reset if latch else None)},
        )
        return EXIT_BUDGET_EXHAUSTED

    if total > 0:
        core.log.info(f"GC: cleaned {total} item(s) total")
    else:
        core.log.info("GC: nothing to clean")
    return 0
