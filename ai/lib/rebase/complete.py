"""Finishing a completed rebase — tip regeneration, push holds, and landing."""

# doc-group: platform

from __future__ import annotations

import core.log
import core.publishing
from core.trail import Trail, terr, tinfo
import git.client
import pr.context

from . import land as rebase_land
from . import lease as rebase_lease
from . import pr_snapshot as rebase_pr_snapshot
from . import regen_dispatch
from . import types as rebase_types

RebaseOutcome = rebase_types.RebaseOutcome
ResolutionTally = rebase_types.ResolutionTally
RunMode = rebase_types.RunMode


def rebase_success(
    cwd: str, ctx: pr.context.ResolvedContext, mode: RunMode,
    tally: ResolutionTally | None = None, *, target_ref: str,
    lease: rebase_lease.PushLease | None = None,
    verify: bool = True,
    snapshot: rebase_pr_snapshot.PRSnapshot | None = None,
    trail: Trail | None = None,
) -> int:
    """Handle rebase completion — update state and optionally force-push."""
    tally = tally or ResolutionTally()

    # Counted before the push: its recovery paths add regeneration and
    # check-fix commits, which were never replayed from the old branch.
    replayed = git.client.commits_ahead(cwd, target_ref=target_ref)

    # RunMode.PUSH lands from main() via cmd_push; every other mode lands here.
    # A mode that does not reach the remote still lands, because the publishing
    # gate — shut for those modes — is what stops it, and a held landing is what
    # carries the force-push command back as data.
    lands_here = mode is not RunMode.PUSH
    if lands_here and tally.stale:
        regen_dispatch.retry_stale_at_tip(
            cwd, tally, trail=trail, conventional=mode.resolves_conflicts,
        )
    one_sided = sorted(set(tally.one_sided))
    stale = sorted(set(tally.stale))
    # Held, not refused: the replay is fine to keep, but a region resolved to
    # one side may have dropped the other side's change, so nothing reaches
    # the remote until somebody has looked — in a batch, in `pr ci --fix`, and
    # by hand alike. Under RunMode.PUSH the push is cmd_push's, after this
    # returns, and `pr rebase` skips it while the gate is held. The hold lasts
    # for the rest of this process, by design.
    if one_sided and mode.reaches_remote:
        core.publishing.hold(f"the rebase resolved {len(one_sided)} file(s) to one side")
    if stale and mode.reaches_remote:
        core.publishing.hold(f"the rebase left {len(stale)} generated file(s) unregenerated")

    label = "Rebase complete" if not tally.commits else (
        f"Rebase complete — resolved {len(tally.files)} file(s) across {tally.commits} commit(s)")

    if lands_here and lease is None:
        # Nothing safe to name: the remote has the branch and this run never
        # read the tip it was at. The two fallbacks are a bare lease, which the
        # fetch would satisfy, and an empty expect, which git rejects against a
        # ref that exists. Stopping leaves the replay in the worktree, which is
        # recoverable; a wrong lease would not be.
        terr(trail, "force_push", "no lease could be named for the push", data={"branch": ctx.branch})
        core.log.error("Refusing to force-push — cannot tell what the remote was at.")
        core.log.dim("The rebase is complete in the worktree. Push it by hand after "
                "checking what origin holds.")
        RebaseOutcome(
            commits_replayed=replayed,
            conflicts_resolved=len(tally.files),
            files_resolved=tally.files,
            files_stale=tally.stale,
            files_one_sided=tally.one_sided,
            one_sided_regions=tally.one_sided_regions,
            pre_rebase_head=tally.pre_rebase_head,
            force_pushed=False,
            resolution=tally.resolution,
            fork_point=tally.fork_point,
            squashed_commits=tally.squashed_commits,
            target_base=target_ref,
        ).save(ctx)
        return 1

    landed = None
    if lands_here:
        # Announced only when the push will actually happen; a held run says the
        # same thing once at the end, through the label and the resume line.
        if mode.reaches_remote and not one_sided and not stale:
            rebase_pr_snapshot.name_the_open_pr(snapshot, trail=trail)
            core.log.info(f"{label} — force-pushing...")
        # Deduplicated: this is the candidate set the pre-push repair matches a
        # failing hook's output against, and a file conflicting in several
        # replayed commits is listed once per commit in the tally.
        repairable = list(dict.fromkeys(tally.files))
        landed = rebase_land.land_rebased(
            cwd, resolved_files=repairable or None, args=lease.args,
            verify=verify, trail=trail, base_ref=target_ref)
        if landed.ok:
            tinfo(trail, "force_push", "force-pushed to remote", data={"sha": landed.sha})
        elif not landed.held:
            terr(trail, "force_push", "force-push failed", data={"status": str(landed.status), "resume": landed.resume})

    outcome = RebaseOutcome(
        commits_replayed=replayed,
        conflicts_resolved=len(tally.files),
        files_resolved=tally.files,
        files_stale=tally.stale,
        files_one_sided=tally.one_sided,
        one_sided_regions=tally.one_sided_regions,
        pre_rebase_head=tally.pre_rebase_head,
        resolution=tally.resolution,
        # None rather than False for a run that never tried: a held landing did
        # exactly what --no-push asked for, and recording it as a failed push
        # would be the summary's own invention.
        force_pushed=None if landed is None or landed.held else landed.ok,
        # Saved even on a held run — especially then: `--no-push` is exactly the
        # case where the push happens in a later process that can no longer read
        # this value for itself.
        lease_expect=lease.expect if lease is not None else "",
        fork_point=tally.fork_point,
        squashed_commits=tally.squashed_commits,
        target_base=target_ref,
    )
    outcome.save(ctx)
    outcome.emit()

    if landed is not None and not landed.held:
        if landed.ok:
            core.log.ok("Force-pushed.")
            return 0
        core.log.error("Force-push failed.")
        return 1

    if one_sided:
        core.log.warn(f"Resolved to one side: {', '.join(one_sided)} — push held. "
                      "Review the regions above, then publish with `pr rebase --push-only`.")
    if stale:
        core.log.warn(f"Not regenerated: {', '.join(stale)} — push held. "
                      "Regenerate at the rebased tip, commit, then publish with `pr rebase --push-only`.")

    # The label holds in every mode that reaches here — the rebase finished.
    # Under FIX_ONLY the AI's work is the whole point of the run and the user is
    # about to push it by hand, so name it rather than reporting a bare "clean".
    core.log.ok(label)
    # A one-sided hold already named `--push-only` above. "Re-run without
    # --no-push" would start a fresh rebase with no one-sided record to
    # replay, so it would force-push straight past the hold.
    if landed is not None and not one_sided and not stale:
        core.log.ok(f"Run `{landed.resume}` to push, or re-run without --no-push.")
    return 0
