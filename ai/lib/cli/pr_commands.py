"""The four `pr` subcommands that used to be defined inside the binary.

`status`, `fix`, `create` and `gc` ran inside `ai/bin/pr`, which is not an
importable module, so `CommandSpec.handler` could not name them. They live
here so the field means one thing across the nine: a `"<module>:<attr>"`
string that importlib can resolve, or None.

Still spawning. `cmd_fix` runs `claude-review`, `ci-check` and `pr-describe`
as child processes, and `cmd_create` still shells out to `task pr:create`.
#909 T7 commit 4c turns those into calls; this module is the seam that makes
the four importable without changing how they run.

Each spawn is *given* the directory to run from rather than deriving one from
`__file__`. Under `WORKBENCH_AI_LIB_DIR` this module resolves inside the
pinned checkout while the entry point's own BIN_DIR does not, so a path
derived here would spawn a different tree's delegates than `ai/bin/pr` does.
Matches `cli.review_modes` and `review.publish.post`.

`cmd_review` and `cmd_comments` stay in the binary: they call `_run_delegate`,
which this commit does not move. Their `CommandSpec.handler` is None until 4c.
"""

# doc-group: cli

import json
import subprocess
import sys
from pathlib import Path

from cli.registry import COMMANDS
from core import log
from core import timeouts
from core.trail import Trail
from gh import budget as gh_budget
from pr import context as pr_context
from pr import domains as pr_domains
from pr import state as pr_state
from pr import supersession
from review import gc as review_gc

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


def _target_flags(ctx: pr_context.ResolvedContext, *,
                  original_pr: str | None = None,
                  original_branch: str | None = None) -> list[str]:
    """The one target flag a child is told to resolve, in priority order.

    Shared by every spawn rather than written out at each: a child that
    resolves a different target than its parent computes a different lock key
    and takes a second lock on the same checkout, which is the contention the
    run lock exists to prevent. Naming the target is what keeps the two
    agreeing, so there is one owner of what that name is.

    `ai/bin/pr._run_delegate` imports this rather than keeping a second copy.
    The two spawn sites (the binary's general dispatch, and `cmd_fix` below)
    have to inject the same flags; splitting them into two functions that
    merely look alike would drop the adjacency that was enforcing that.
    """
    if original_pr is not None:
        return ["--pr", str(original_pr)]
    if ctx.pr_number is not None:
        return ["--pr", str(ctx.pr_number)]
    if original_branch is not None:
        return ["--branch", original_branch]
    if ctx.branch:
        return ["--branch", ctx.branch]
    return []


def _spawn(script: str, argv: list[str], ctx: pr_context.ResolvedContext, *,
           bin_dir: Path,
           original_pr: str | None = None,
           original_branch: str | None = None) -> int:
    """Spawn a backing script with the same flag injection as the binary.

    Still a subprocess. The binary's `_run_delegate` is the one every
    delegating command uses; this copy exists so `cmd_fix` can spawn
    `ci-check` and `pr-describe` without importing the binary. Both inject
    `--repo-dir` and `_target_flags`. Commit 4c deletes the spawn.
    """
    cmd = [str(bin_dir / script)]
    if ctx.worktree_root:
        cmd += ["--repo-dir", str(ctx.worktree_root)]
    cmd += _target_flags(ctx, original_pr=original_pr,
                         original_branch=original_branch)
    cmd += list(argv)
    return subprocess.run(cmd, timeout=timeouts.UNBOUNDED).returncode


def cmd_status(argv: list[str], ctx: pr_context.ResolvedContext, **_kw) -> int:
    """Render unified status dashboard from cached state.

    The dashboard is a fold over the domain registry, not a list of renderers:
    a domain prints what it says about itself, in the order `PRState` declares
    it, and one that says nothing takes up no room. Push is the exception that
    is refreshed rather than read — it is a local git question, so `pr status`
    answers it now instead of reporting whatever the last write happened to see.
    """
    wt = ctx.require_worktree()
    state = pr_state.load_state(ctx.target_dir)

    branch = state.identity.branch if state else ctx.branch
    repo = state.identity.repo if state else ctx.repo
    push = pr_domains.PushDomain.observed(wt, branch, updated_at=pr_state.now_iso())

    lines = pr_state.render_dashboard(state, push, repo=repo, branch=branch)
    print("\n".join(lines), file=sys.stderr)
    if state:
        json.dump(pr_state.state_to_dict(state), sys.stdout, indent=2)
        print()
    return 0


def cmd_fix(argv: list[str], ctx: pr_context.ResolvedContext, *,
            bin_dir: Path, **_kw) -> int:
    """Run fix passes for CI, review, and comments."""
    wt = ctx.require_worktree()
    state = pr_state.load_state(ctx.target_dir)
    if not state:
        log.error("No state yet. Run pr ci, pr review, or pr comments first.")
        return 1

    exit_code = 0

    if state.review.updated_at and sum(state.review.finding_counts.values()) > 0:
        log.info("Fixing review findings...")
        review_args = [str(bin_dir / "claude-review"), "--self", "--fix"]
        review_args += ["--repo-dir", str(wt)]
        # Named, not left to the child to re-derive: without a target it
        # resolves the worktree's current branch, which is not always the one
        # this run locked, and the two then hold separate locks on one checkout.
        review_args += _target_flags(
            ctx,
            original_pr=_kw.get("original_pr"),
            original_branch=_kw.get("original_branch"),
        )
        review_args += list(argv)
        r = subprocess.run(review_args, timeout=timeouts.UNBOUNDED)
        if r.returncode == supersession.EXIT_SUPERSEDED:
            # Every remaining pass acts on the same branch, so a refusal that
            # says "this branch may not be worth working on" answers for all of
            # them. Continuing would spend the CI fix pass on the question the
            # review just declined to spend on.
            log.error("Stopping — the review refused this branch as superseded.")
            log.dim(f"Resolve it, or re-run with {supersession.OVERRIDE_FLAG} "
                    f"to override.")
            return supersession.EXIT_SUPERSEDED
        if r.returncode != 0:
            exit_code = 1

    ci_fixable = 0
    if state.ci.updated_at and state.ci.failure_count > 0:
        infra_count = state.ci.failure_kinds.get("infra", 0) + state.ci.failure_kinds.get("flaky", 0)
        ci_fixable = state.ci.failure_count - infra_count

    if ci_fixable > 0:
        log.blank()
        log.info(f"Fixing {ci_fixable} CI failure(s)...")
        rc = _spawn(
            COMMANDS["ci"].script, ["--fix"] + list(argv), ctx,
            bin_dir=bin_dir,
            original_pr=_kw.get("original_pr"),
            original_branch=_kw.get("original_branch"),
        )
        if rc != 0:
            exit_code = 1

    actionable = state.comments.by_state.get("new", 0) + state.comments.by_state.get("contested", 0)
    if state.comments.updated_at and actionable > 0:
        log.blank()
        log.info(f"Comments: {actionable} actionable thread(s) — run pr comments --fix")

    # Last, because the description has to describe the branch as it ends up.
    # pr-describe re-resolves HEAD itself and no-ops when nothing landed, so
    # this is free on a run where every fix pass was a skip. The user's argv is
    # not forwarded: --fix and friends mean nothing here.
    #
    # `--post` is the exception, and it is forwarded rather than passed along
    # with the rest for the same reason the rest are dropped: it is the one
    # flag that means something to describe. Editing the PR body is gated like
    # every other GitHub write, so without this a `pr fix --post` would push
    # its commits and post its replies and then draft the description alone.
    log.blank()
    describe_argv = ["--post"] if "--post" in argv else []
    if _spawn(COMMANDS["describe"].script, describe_argv, ctx,
              bin_dir=bin_dir,
              original_pr=_kw.get("original_pr"),
              original_branch=_kw.get("original_branch")) != 0:
        exit_code = 1

    return exit_code


def cmd_create(argv: list[str], ctx: pr_context.ResolvedContext, **_kw) -> int:
    """Delegate PR creation to task pr:create."""
    cmd = ["task", "--global"]
    if ctx.worktree_root:
        cmd.append(f"REPO_DIR={ctx.worktree_root}")
    cmd.append("pr:create")
    if argv:
        cmd += ["--"] + list(argv)
    return subprocess.run(cmd, timeout=timeouts.UNBOUNDED).returncode


def cmd_gc(argv: list[str], ctx: pr_context.ResolvedContext, *, trail: Trail, **_kw) -> int:
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
    local = review_gc.gc_reviews()
    outcome = (
        review_gc.prune_merged_reviews()
        + review_gc.prune_merged_targets(skip=ctx.target_dir, trail=trail)
    )
    total = local + outcome.pruned

    if outcome.cut_short:
        latch = gh_budget.latched(gh_budget.Resource.GRAPHQL)
        remedy = latch.remedy() if latch else gh_budget.BUDGET_EXHAUSTED_HINT
        log.warn(
            f"GC: stopped early — the GitHub API budget is spent, so the PRs "
            f"behind this sweep were never asked about ({remedy})")
        if total:
            log.info(f"GC: cleaned {total} item(s) before it ran out")
        trail.summary(
            "gc_cut_short",
            f"budget exhausted after cleaning {total} item(s)",
            data={"cleaned": total, "reset": (latch.reset if latch else None)},
        )
        return EXIT_BUDGET_EXHAUSTED

    if total > 0:
        log.info(f"GC: cleaned {total} item(s) total")
    else:
        log.info("GC: nothing to clean")
    return 0
