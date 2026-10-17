"""Fix a branch's failing CI: rebase it if it has fallen behind, then run the fix engine.

`run_fix` takes the report `pr.ci_check` produced; `rebase_if_behind` brings the
branch up to its target first when it is behind, through `rebase.commands`.
`cli.ci_check` is the command over these; the fix itself is `fix.ci` on
`fix.engine`.
"""

# doc-group: platform

from __future__ import annotations

from dataclasses import dataclass


import core.log
import core.publishing
import core.report
import fix.ci
import fix.engine
import fix.verify
import gh.client
from git.land import CommitStatus
import pr.ci_failures
import pr.ci_report
import pr.ci_wait
import pr.state
import rebase.commands
import rebase.inspect
import rebase.target
import rebase.types


def rebase_if_behind(trail, report: pr.ci_report.CIReport, ctx) -> bool:
    """Rebase onto origin/main if branch is behind. Returns True if rebased and pushed.

    Called in-process rather than spawned, so this run's publishing gate is the
    one the rebase's push asks. A draft run therefore rebases locally and drafts
    the force-push, where the subprocess used to perform it — the gate is a
    process-wide flag, and a child process was never told about it.

    No snapshot is passed: this run has no `gh pr view` of its own to hand on,
    and `refusals.tracker_landed_check` reads the tracker itself when it gets
    none. That read can be refused — `pr.context.resolve` has already spent
    GraphQL getting here — and the refusal comes back as `REFUSAL_EXIT`, which
    is reported apart from a failed rebase below.
    """
    behind = report.behind_main
    if behind <= 0:
        return False

    trail.decision(
        "rebase_check",
        f"branch is {behind} commit(s) behind main",
        reason="stale branch may cause CI failures",
    )
    core.log.info(f"Branch is {behind} commit(s) behind main — rebasing first...")

    cwd = str(ctx.require_worktree())
    target_ref = rebase.target.resolve_target_ref(cwd, ctx, None, trail=trail)
    rc = rebase.commands.cmd_start(
        cwd, ctx, rebase.types.RunMode.FIX, target_ref=target_ref, trail=trail,
    )

    # A refusal is not a failure: the rebase declined on purpose, having found
    # the work landed or having been unable to ask. Reported on its own so the
    # operator is not told a rebase broke when it deliberately stopped, and so
    # the fix pass that follows is known to be running on the un-rebased base.
    if rc == rebase.types.REFUSAL_EXIT:
        trail.warn("rebase_refused", "rebase refused its preflight")
        core.log.warn("Rebase refused — continuing with CI fixes on current base")
        return False

    # A paused rebase is not a failure either: the replay stopped with its
    # resolved work staged in the worktree, waiting on `pr rebase --fix` or
    # `--abort`. The fix pass below checks for exactly this and refuses, so
    # saying here that fixes will continue would just be wrong.
    if rc == rebase.types.CONFLICTS_EXIT:
        trail.warn("rebase_paused", "rebase paused with conflicts — not fixing")
        core.log.warn("Rebase paused with conflicts — not applying CI fixes until it is resolved")
        return False

    if rc != 0:
        trail.warn("rebase_failed", f"rebase failed (exit {rc})")
        core.log.warn("Rebase failed — continuing with CI fixes on current base")
        return False

    # Read from the outcome the rebase just saved, not inferred from the gate:
    # `pr rebase` holds its own push when it resolved a region to one side,
    # even in a run that may publish.
    if not rebase.types.load_or_init(ctx).rebase.force_pushed:
        held = core.publishing.held()
        trail.info("rebase_done", "rebased; force-push drafted",
                   data={"held": held} if held else None)
        core.log.ok("Rebased onto main — force-push drafted"
                    + (f" ({held})" if held else ", pass --post to send it"))
        return False

    trail.info("rebase_done", "rebased and force-pushed")
    core.log.ok("Rebased onto main and force-pushed — CI will re-run on new HEAD")
    return True


def _emit_tally(tally: pr.ci_report.CIFixTally) -> None:
    core.report.emit_stream_json(tally.to_json(), pr.ci_report.FIX_TALLY_TYPE)


def _run_ids(report: pr.ci_report.CIReport) -> list[int]:
    """Workflow runs to rerun: `run_ids` when present, else the single `run_id`."""
    ids = [run_id for run_id in report.run_ids if run_id]
    if ids:
        return ids
    return [report.run_id] if report.run_id else []


def _all_skipped_transient(adapter: fix.ci.CIFixAdapter) -> bool:
    return bool(adapter.skipped) and all(
        pr.ci_failures.is_transient_infra(failure.group)
        for failure in adapter.skipped
    )


def _rereport_after_rerun(
    trail, report: pr.ci_report.CIReport, ctx, *,
    wait_timeout: int, wait_interval: int,
) -> pr.ci_report.CIReport | None:
    """Wait for the rerun jobs and rebuild the CI report.

    Pins no run id: a rate-limit incident can span several workflow runs, and
    `poll_until_complete` only pins one. Re-resolving by `head_sha` is how
    `ci-check --wait` watches the same commit.
    """
    # Imported here: `pr.ci_check` imports this module at load.
    import pr.ci_check
    import pr.ci_runs

    try:
        poll = pr.ci_wait.poll_until_complete(
            ctx.repo, ctx.branch, run_id=None,
            timeout=wait_timeout, interval=wait_interval,
            trail=trail, head_sha=report.head_sha,
        )
    except pr.ci_runs.RunUnavailable:
        return None
    # A wait that timed out still returns what it saw, and a rerun job still
    # queued has no failure to report yet. Rebuilding from that would read as
    # green while CI is still running, so an unfinished wait hands the step
    # back to a person with the original report.
    if poll.merged.get("status") != "completed" and not poll.counts.finished:
        core.log.warn(
            f"Rerun jobs did not finish within {wait_timeout}s — leaving them to a person"
        )
        return None
    return pr.ci_check.report_run(
        trail, ctx, poll.merged, poll.run_ids,
        counts=poll.counts, show_status=True,
    )


@dataclass(frozen=True)
class _Rerun:
    """The CI report rebuilt after an automatic rerun, and the adapter over it."""

    report: pr.ci_report.CIReport
    adapter: fix.ci.CIFixAdapter


def _try_rerun_transient(
    trail, report: pr.ci_report.CIReport, adapter: fix.ci.CIFixAdapter, ctx, *,
    wait_timeout: int, wait_interval: int,
) -> _Rerun | None:
    """Rerun rate-limit-only infra once, wait, and rebuild the adapter.

    Returns None when this is not a transient-only skip, when any `gh run rerun`
    fails (including a latched budget breaker), or when the wait cannot rebuild
    a report. Exactly one automatic rerun per `run_fix` invocation: the caller
    must not call this again on the rebuilt adapter.
    """
    if not _all_skipped_transient(adapter):
        return None
    run_ids = _run_ids(report)
    if not run_ids:
        return None
    core.log.info(
        f"Rerunning {len(run_ids)} failed job run(s) that hit GitHub API rate limits"
    )
    for run_id in run_ids:
        if not gh.client.run(
            "run", "rerun", str(run_id), "--failed",
            cwd=str(ctx.require_worktree()),
        ).ok:
            return None
    rebuilt = _rereport_after_rerun(
        trail, report, ctx,
        wait_timeout=wait_timeout, wait_interval=wait_interval,
    )
    if rebuilt is None:
        return None
    state = pr.state.load_or_init(
        target_dir=ctx.target_dir, repo=ctx.repo, branch=ctx.branch,
        pr_number=ctx.pr_number, head_sha=rebuilt.head_sha,
        worktree_root=str(ctx.worktree_root),
    )
    return _Rerun(report=rebuilt, adapter=fix.ci.CIFixAdapter(rebuilt, ctx, state))


def _tally(adapter: fix.ci.CIFixAdapter,
           run: fix.engine.FixRun | None = None) -> pr.ci_report.CIFixTally:
    """The tally for *adapter*'s failures, after *run* when the pass ran."""
    outcomes = run.outcomes if run is not None else []
    fixed = [o.id for o in outcomes if o.outcome.counts_as_fixed]
    return pr.ci_report.CIFixTally(
        failures=[f.id for f in adapter.fixable + adapter.skipped],
        fixed=fixed,
        unfixed=[f.id for f in adapter.fixable if f.id not in fixed],
        skipped=[{"id": f.id, "kind": f.group.kind.value} for f in adapter.skipped],
        suite_status=run.suite.status.value if run is not None else "",
        commit=run.landed.sha if run is not None and run.landed else "",
    )


def run_fix(trail, report: pr.ci_report.CIReport, ctx, *,
            rebase_first: bool = True, verify: bool = True,
            wait_timeout: int = 900, wait_interval: int = 30) -> int:
    """Apply AI-driven fixes for CI failures. Returns exit code.

    *rebase_first* False skips `rebase_if_behind`: `pr batch` rebases in a
    step of its own, and a drafted rebase has not moved `origin/<branch>`, so
    the behind count would draft a second force-push.

    *verify* runs the verify gate over every claimed fix, as the review and
    comments passes do: a ticked box is a claim an edit was made, not that it
    clears the failure, and the gate demotes the ones it finds broken. False
    (`--no-verify`) skips it and every fix lands unverified.

    When every remaining failure is transient GitHub API rate-limit infra,
    the failed jobs are rerun once (`gh run rerun --failed`), the run is
    waited on with *wait_timeout* / *wait_interval* (the same defaults as
    `ci-check --wait`), and the report is rebuilt. A clean rerun ends the
    pass with an empty tally; newly fixable failures continue into the
    engine; anything else still non-fixable is skipped for a person, as
    today. Timeouts and OOM are not rerun. Bound: one automatic rerun per
    invocation.
    """
    if not ctx.worktree_root:
        core.log.error("--fix requires a worktree (use --repo-dir)")
        return 1

    if not report.failures:
        core.log.info("No failures to fix")
        _emit_tally(pr.ci_report.CIFixTally())
        return 0

    state = pr.state.load_or_init(
        target_dir=ctx.target_dir, repo=ctx.repo, branch=ctx.branch,
        pr_number=ctx.pr_number, head_sha=report.head_sha,
        worktree_root=str(ctx.worktree_root),
    )
    adapter = fix.ci.CIFixAdapter(report, ctx, state)
    if not adapter.fixable:
        recovered = _try_rerun_transient(
            trail, report, adapter, ctx,
            wait_timeout=wait_timeout, wait_interval=wait_interval,
        )
        if recovered is not None:
            report, adapter = recovered.report, recovered.adapter
            if not report.failures:
                core.log.info("No failures to fix")
                _emit_tally(pr.ci_report.CIFixTally())
                return 0
        if not adapter.fixable:
            # `adapter.fixable` empty with `report.failures` non-empty means
            # every failure landed in `adapter.skipped` by elimination, so
            # `kinds` is never empty here.
            kinds = sorted({f.group.kind.value for f in adapter.skipped})
            core.log.info(f"No fixable failures (all {'/'.join(kinds)})")
            _emit_tally(_tally(adapter))
            return 0

    # Rebase before the pass, not before the report — the original run has the
    # failures we need to fix, but the branch should be current before applying
    # fixes.
    if rebase_first:
        rebase_if_behind(trail, report, ctx)

    # A rebase that stopped part-way leaves its replay in the worktree, and a
    # fix pass turned loose on a mid-rebase index edits files still carrying
    # conflict markers and commits them onto a detached HEAD. The rebase used
    # to abort itself on the way out of every failure, so "it failed, carry on"
    # was safe; now that a resolvable stop keeps the work it already did, this
    # is what keeps it safe.
    if rebase.inspect.rebase_in_progress(str(ctx.require_worktree())):
        trail.error("rebase_paused", "a rebase is in progress — not fixing")
        core.log.error("A rebase is paused in this worktree — not applying CI fixes.")
        core.log.dim("Finish it with `pr rebase --fix`, or discard it with "
                "`pr rebase --abort`, then re-run.")
        return 1

    trail.info("fix_start", f"{len(adapter.fixable)} fixable failure(s)")
    core.log.info(f"Fixing {len(adapter.fixable)} CI failure(s)...")

    run = fix.engine.run(
        adapter, trail=trail, verify=fix.verify.run if verify else None,
    )
    _emit_tally(_tally(adapter, run))

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
        core.log.info(f"{unresolved} failure(s) could not be auto-fixed")

    # A refused commit leaves the pass's work loose in the worktree. The record
    # `CIFixAdapter.record` just saved says so, but an exit code of zero over it
    # would still report a run that finished cleanly.
    if run.landed and run.landed.status is CommitStatus.COMMIT_FAILED:
        return 1

    return 0 if run.exit_code == 0 else 1
