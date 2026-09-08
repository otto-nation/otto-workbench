"""Fetch CI run data, classify failures, and output status.

Renders a human-readable dashboard to stderr. A single-shot run writes the
structured JSON report to stdout only when there is a failure in it; `--wait`
writes one on every poll that finds something new and a final one when the run
finishes, whether it failed or not.

Manages local state in <state_dir()>/pr/<repo-key>-<branch-slug>/state.json, keyed
on the run's target rather than on the checkout it was invoked from.

Usage:
  ci-check                      # latest run for current branch
  ci-check --branch <name>      # specific branch (works from bare repos)
  ci-check --run <run_id>       # specific run
  ci-check --pr <number_or_url> # discover branch from PR
  ci-check --repo-dir <path>    # specify worktree directory
  ci-check --fix                # diagnose then invoke AI to fix failures
"""

# doc-group: cli

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from core import log
from core import publishing
from core import report as core_report
from core import run_lock
from core import timeouts
from core.tool_parser import ToolParser
from core.trail import Trail, add_trail_args
from fix import ci as fix_ci
from fix import engine as fix_engine
from gh import run_reads
from git.land import CommitStatus
from pr import ci_failures as ci
from pr import ci_report
from pr import ci_runs
from pr import ci_wait
from pr import context as pr_context
from pr import domains as pr_domains
from pr import state as pr_state

# The binary a user runs and the trail records, which is not this module's own
# name. Spelled out rather than derived, so the shim can be renamed only by
# changing the name in both places at once.
SCRIPT = "ci-check"

_BIN_DIR = Path(__file__).resolve().parent.parent.parent / "bin"


def _report_run(trail, ctx, merged, run_ids, counts=None, show_status=False) -> ci_report.CIReport:
    """Turn a merged run payload into the report, and record it against the branch.

    Both paths end here: a single-shot run and the last poll of a `--wait` run
    are the same run as far as the state file and the dashboard are concerned.
    """
    repo = ctx.repo
    branch = ctx.branch
    run_state = ci_runs.parse_run(repo, merged)

    # Load unified PR state (or init fresh). Unconditional: the file is keyed on
    # the target, not on the caller's checkout, so a run from a bare repo has the
    # prior run's failures to compare against just like any other.
    state = pr_state.load_or_init(
        target_dir=ctx.target_dir, repo=repo, branch=branch,
        pr_number=ctx.pr_number, head_sha=run_state.head_sha,
        worktree_root=str(ctx.worktree_root) if ctx.worktree_root else "",
    )
    ci_domain = state.ci

    trail.decision(
        "classify_failures",
        f"classified {len(run_state.failures)} failure groups",
        reason=f"matched job patterns: {list(run_state.failures.keys())}",
        data={"failure_kinds": {k: g.kind.value for k, g in run_state.failures.items()}},
    )

    prior_run = (
        ci_domain.runs.get(ci_domain.latest_run_id)
        if ci_domain.latest_run_id is not None
        else None
    )
    prior_failures = prior_run.failures if prior_run else {}
    progression = ci.compute_progression(run_state.failures, prior_failures)

    trail.info(
        "compute_progression",
        f"{len(progression)} items tracked",
        data={"outcomes": {o.value: sum(1 for v in progression.values() if v == o) for o in ci.Outcome}},
    )

    ci.sync_ci_domain(ci_domain, run_state)

    trail.info("sync_state", f"state synced, {len(ci_domain.runs)} runs retained")

    dashboard = ci_report.render_dashboard(
        run_state, progression, run_ids=run_ids, show_status=show_status,
    )
    print(dashboard, file=sys.stderr)

    # Check how far behind origin/main the branch is — runs on every invocation
    # (not just --fix) because the JSON report includes behind_main for SKILL.md consumers
    behind_main = run_reads.commits_behind_main(repo, branch) if branch not in ("main", "master") else 0

    report = ci_report.CIReport.build(
        repo=repo, branch=branch, pr_number=ctx.pr_number,
        run_state=run_state, progression=progression, prior_run=prior_run,
        run_ids=run_ids, behind_main=behind_main, counts=counts,
    )

    try:
        pr_state.save_state(ctx.target_dir, state)
    except Exception as exc:
        trail.error("state_update", f"state update failed: {exc}")
        log.error(f"{SCRIPT}: state update failed: {exc}")

    return report


def _run_ci(trail, args, ctx) -> ci_report.CIReport:
    repo = ctx.repo
    branch = ctx.branch

    if args.run:
        run_ids = [args.run]
    else:
        run_ids = run_reads.fetch_latest_run_ids(repo, branch)
        if not run_ids:
            trail.warn("no_runs", "no workflow runs found")
            raise ci_runs.RunUnavailable(f"No workflow runs found for branch '{branch}'")

    trail.info("fetch_runs", f"fetching {len(run_ids)} run(s)", data={"run_ids": run_ids})

    fetched = ci_runs.fetch_merged(repo, run_ids)
    if fetched is None:
        trail.error("fetch_run_data", "failed to fetch run data")
        raise ci_runs.RunUnavailable("Failed to fetch run data")

    for payload in fetched.payloads:
        trail.info(
            "fetch_run_data", f"fetched run {payload['_run_id']}",
            data={"run_id": payload["_run_id"], "conclusion": payload.get("conclusion")},
        )

    report = _report_run(trail, ctx, fetched.merged, run_ids)

    if report.failures:
        core_report.emit_json(report.to_json())

    return report


def _run_ci_wait(trail, args, ctx) -> ci_report.CIReport:
    """Poll CI until all jobs complete, emitting partial reports as failures arrive."""
    poll = ci_wait.poll_until_complete(
        ctx.repo, ctx.branch, run_id=args.run,
        timeout=args.wait_timeout, interval=args.wait_interval, trail=trail,
    )

    report = _report_run(
        trail, ctx, poll.merged, poll.run_ids, counts=poll.counts, show_status=True,
    )
    core_report.emit_stream_json(report.to_json(), "final")

    return report


# ── Fix phase ────────────────────────────────────────────────────────────────


def _rebase_if_behind(trail, report: ci_report.CIReport, ctx) -> bool:
    """Rebase onto origin/main if branch is behind. Returns True if rebased and pushed."""
    behind = report.behind_main
    if behind <= 0:
        return False

    trail.decision(
        "rebase_check",
        f"branch is {behind} commit(s) behind main",
        reason="stale branch may cause CI failures",
    )
    log.info(f"Branch is {behind} commit(s) behind main — rebasing first...")

    cmd = [str(_BIN_DIR / "pr-rebase"), "--fix",
           "--repo-dir", str(ctx.require_worktree())]
    if ctx.branch:
        cmd += ["--branch", ctx.branch]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeouts.UNBOUNDED)

    if r.returncode != 0:
        trail.warn("rebase_failed", f"rebase failed (exit {r.returncode})")
        log.warn("Rebase failed — continuing with CI fixes on current base")
        if r.stderr.strip():
            log.dim(r.stderr.strip())
        return False

    trail.info("rebase_done", "rebased and force-pushed")
    log.ok("Rebased onto main and force-pushed — CI will re-run on new HEAD")
    return True


def _run_fix(trail, report: ci_report.CIReport, ctx) -> int:
    """Apply AI-driven fixes for CI failures. Returns exit code."""
    if not ctx.worktree_root:
        log.error("--fix requires a worktree (use --repo-dir)")
        return 1

    if not report.failures:
        log.info("No failures to fix")
        return 0

    state = pr_state.load_or_init(
        target_dir=ctx.target_dir, repo=ctx.repo, branch=ctx.branch,
        pr_number=ctx.pr_number, head_sha=report.head_sha,
        worktree_root=str(ctx.worktree_root),
    )
    adapter = fix_ci.CIFixAdapter(report, ctx, state)
    if not adapter.fixable:
        log.info("No fixable failures (all infra/flaky)")
        return 0

    # Rebase before the pass, not before the report — the original run has the
    # failures we need to fix, but the branch should be current before applying
    # fixes.
    #
    # ceiling: the publishing gate is process-wide and this is a subprocess, so
    # `pr-rebase` force-pushes whether or not this run was given `--post`. A
    # draft run therefore still moves the remote here, which is the one place it
    # can. Upgrade trigger: when `pr-rebase` takes a `--post` of its own,
    # forward this run's.
    _rebase_if_behind(trail, report, ctx)

    trail.info("fix_start", f"{len(adapter.fixable)} fixable failure(s)")
    log.info(f"Fixing {len(adapter.fixable)} CI failure(s)...")

    run = fix_engine.run(adapter, trail=trail)

    fixed = sum(1 for o in run.outcomes if o.outcome.counts_as_fixed)
    unresolved = len(run.outcomes) - fixed
    trail.info(
        "fix_result", f"{fixed} fixed, {unresolved} skipped",
        data={"fixed": fixed, "skipped": unresolved},
    )
    # The land owner has already reported the commit and whatever the push did,
    # including the command that would finish a held or refused one.
    if run.landed and run.landed.sha:
        trail.info("fix_committed", f"committed at {run.landed.sha}",
                   data={"status": str(run.landed.status),
                         "resume": run.landed.resume})

    if unresolved > 0:
        log.info(f"{unresolved} failure(s) could not be auto-fixed")

    # A refused commit leaves the pass's work loose in the worktree. The record
    # `CIFixAdapter.record` just saved says so, but an exit code of zero over it
    # would still report a run that finished cleanly.
    if run.landed and run.landed.status is CommitStatus.COMMIT_FAILED:
        return 1

    return 0 if run.exit_code == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = ToolParser(
        prog=SCRIPT,
        description="CI failure status",
        output_schema=pr_domains.CIDomain,
    )
    parser.add_argument("--pr", help="PR number or URL")
    parser.add_argument("--branch", help="Branch name (overrides git detection)")
    parser.add_argument("--run", type=int, help="Specific run ID")
    parser.add_argument("--repo-dir", "--worktree",
                        help="Git worktree directory")
    parser.add_argument("--fix", action="store_true",
                        help="Invoke AI to fix failures after diagnosis")
    parser.add_argument("--post", action="store_true",
                        help="Push the fixes; without it the push is drafted")
    parser.add_argument("--wait", action="store_true",
                        help="Poll until all jobs complete, emitting incremental reports")
    parser.add_argument("--wait-timeout", type=int, default=900,
                        help="Max wait time in seconds (default: 900)")
    parser.add_argument("--wait-interval", type=int, default=30,
                        help="Poll interval in seconds (default: 30)")
    add_trail_args(parser)
    argv = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(argv)

    # Before anything runs, so no code path can push ahead of the decision.
    if args.post:
        publishing.enable()

    ctx = pr_context.resolve(
        pr=args.pr, branch=args.branch, repo_dir=args.repo_dir,
    )

    # A no-op when pr launched us — we resolve the same target and find its key
    # already in WORKBENCH_RUN_LOCK.
    # Acquired before Trail.start so contention costs no trail artifacts.
    run_lock.claim_for_process(
        ctx.target_dir,
        command=" ".join([SCRIPT] + argv),
        started=pr_state.now_iso(),
    )

    trail = Trail.start(
        script=SCRIPT,
        context={"repo": ctx.repo, "pr": ctx.pr_number, "branch": ctx.branch},
        debug=args.debug,
    )
    try:
        report = _run_ci_wait(trail, args, ctx) if args.wait else _run_ci(trail, args, ctx)
        return _run_fix(trail, report, ctx) if args.fix else 0
    except ci_runs.RunUnavailable as exc:
        # Expected: there is no run to report on. Trailed where it was raised,
        # so it is the exit code that is left to decide.
        log.error(str(exc))
        return 1
    except Exception as exc:
        trail.error("unexpected_error", str(exc))
        raise
    finally:
        trail.finish()
