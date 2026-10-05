"""Report a branch's CI: the run's failures, classified, as a dashboard and a structured report.

`run_ci` reads one run (the latest for the branch, or the one named);
`run_ci_wait` polls until it finishes, reporting each poll that finds
something new. Both render through `report_run`. `cli.ci_check` is the command
over these; the run reads are `gh.run_reads`, classification is
`pr.ci_failures`, and the report shape is `pr.ci_report`.
"""

# doc-group: pr-state

from __future__ import annotations

import sys

import core.log
import core.report
import gh.run_reads
import pr.ci_failures
import pr.ci_report
import pr.ci_runs
import pr.ci_wait
import pr.state


# The binary a user runs and the trail records, which is not this module's own
# name. Spelled out rather than derived, so the shim can be renamed only by
# changing the name in both places at once.
SCRIPT = "ci-check"


def report_run(trail, ctx, merged, run_ids, counts=None, show_status=False) -> pr.ci_report.CIReport:
    """Turn a merged run payload into the report, and record it against the branch.

    Both paths end here: a single-shot run and the last poll of a `--wait` run
    are the same run as far as the state file and the dashboard are concerned.
    """
    repo = ctx.repo
    branch = ctx.branch
    run_state = pr.ci_runs.parse_run(repo, merged)

    # Load unified PR state (or init fresh). Unconditional: the file is keyed on
    # the target, not on the caller's checkout, so a run from a bare repo has the
    # prior run's failures to compare against just like any other.
    state = pr.state.load_or_init(
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
    progression = pr.ci_failures.compute_progression(run_state.failures, prior_failures)

    trail.info(
        "compute_progression",
        f"{len(progression)} items tracked",
        data={"outcomes": {o.value: sum(1 for v in progression.values() if v == o) for o in pr.ci_failures.Outcome}},
    )

    pr.ci_failures.sync_ci_domain(ci_domain, run_state)

    trail.info("sync_state", f"state synced, {len(ci_domain.runs)} runs retained")

    dashboard = pr.ci_report.render_dashboard(
        run_state, progression, run_ids=run_ids, show_status=show_status,
    )
    print(dashboard, file=sys.stderr)

    # Check how far behind origin/main the branch is — runs on every invocation
    # (not just --fix) because the JSON report includes behind_main for SKILL.md consumers
    behind_main = gh.run_reads.commits_behind_main(
        repo, branch, str(ctx.worktree_root) if ctx.worktree_root else None,
    )

    report = pr.ci_report.CIReport.build(
        repo=repo, branch=branch, pr_number=ctx.pr_number,
        run_state=run_state, progression=progression, prior_run=prior_run,
        run_ids=run_ids, behind_main=behind_main, counts=counts,
    )

    try:
        pr.state.save_state(ctx.target_dir, state)
    except Exception as exc:
        trail.error("state_update", f"state update failed: {exc}")
        core.log.error(f"{SCRIPT}: state update failed: {exc}")

    return report


def run_ci(trail, args, ctx) -> pr.ci_report.CIReport:
    repo = ctx.repo
    branch = ctx.branch

    # --head-sha names the commit GitHub has: after a drafted rebase, local
    # HEAD is one it has never seen, and no run would be found for it.
    head_sha = args.head_sha or ctx.head_sha

    discovery = (gh.run_reads.RunDiscovery(rows=(gh.run_reads.RunRow(run_id=args.run),))
                 if args.run else gh.run_reads.fetch_latest_runs(repo, branch, head_sha))
    run_ids = [row.run_id for row in discovery.rows]

    trail.info("fetch_runs", f"fetching {len(run_ids)} run(s)", data={"run_ids": run_ids})

    # `ctx.head_sha` is the branch's current head, not the pinned run's commit —
    # a run requested by id can be for a commit the branch has since moved past.
    # Passing it through regardless would let `_commit_checks` answer a rollup
    # for the wrong commit and splice an unrelated commit's external checks into
    # this run's report. Withholding it here leaves rows[0].head_sha (empty for
    # a pinned run) as the only candidate, so `_commit_checks` finds nothing to
    # ask about and the rollup is skipped rather than answered for the wrong sha.
    rollup_head_sha = "" if args.run else head_sha

    # Not gated on there being a workflow run: a commit can be checked by
    # something that is not a workflow, and bailing here on an empty run list
    # is what made those checks unreportable rather than merely unseen.
    fetched = pr.ci_runs.fetch_merged(repo, discovery, head_sha=rollup_head_sha)
    if fetched is None:
        if discovery.failed:
            trail.error("list_runs", "could not list workflow runs")
            raise pr.ci_runs.RunUnavailable(f"Could not list workflow runs for '{branch}'")
        if not discovery.rows:
            trail.warn("no_runs", "no checks found")
            raise pr.ci_runs.RunUnavailable(f"No checks found for branch '{branch}'")
        trail.error("fetch_run_data", "failed to fetch run data")
        raise pr.ci_runs.RunUnavailable("Failed to fetch run data")

    for payload in fetched.payloads:
        trail.info(
            "fetch_run_data", f"fetched run {payload['_run_id']}",
            data={"run_id": payload["_run_id"], "conclusion": payload.get("conclusion")},
        )

    report = report_run(trail, ctx, fetched.merged, run_ids)

    if report.failures:
        core.report.emit_json(report.to_json())

    return report


def run_ci_wait(trail, args, ctx) -> pr.ci_report.CIReport:
    """Poll CI until all jobs complete, emitting partial reports as failures arrive."""
    poll = pr.ci_wait.poll_until_complete(
        ctx.repo, ctx.branch, run_id=args.run, head_sha=args.head_sha or ctx.head_sha,
        timeout=args.wait_timeout, interval=args.wait_interval, trail=trail,
    )

    report = report_run(
        trail, ctx, poll.merged, poll.run_ids, counts=poll.counts, show_status=True,
    )
    core.report.emit_stream_json(report.to_json(), pr.ci_report.FINAL_REPORT_TYPE)

    return report
