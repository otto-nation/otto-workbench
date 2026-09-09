"""Force-pushing a replayed branch, with the hook-rejection recovery ladder."""

# doc-group: platform

from __future__ import annotations

from core import log
from core.trail import Trail
from git import land
from git import push

from . import prepush
from . import types as rebase_types

FORCE_PUSH_ARGS = rebase_types.FORCE_PUSH_ARGS
REGEN_MESSAGE = rebase_types.REGEN_MESSAGE


def land_rebased(
    cwd: str, resolved_files: list[str] | None = None, *,
    trail: Trail | None = None,
) -> land.LandResult:
    """Force-push the replayed branch, auto-recovering from a hook rejection.

    Two recoveries sit under this and only the second is the rebase's. `land`
    commits whatever the pre-push hook regenerated and pushes once more; when
    that second run still reports check failures, `prepush` hands them to the AI
    and lands the repair. A hook can both rewrite a file and fail
    a check, so the two are a ladder rather than alternatives — collapsing them
    would make the AI fix unreachable on any repo whose hooks regenerate
    anything.

    Whether any of it reaches the remote is the publishing gate's answer, not an
    argument here: the entry point opens the gate for the modes that push, so
    `--no-push` comes back `held`, with the force-push command in `resume`,
    rather than as a failure or as a hand-written hint.

    """
    landed = land.land_head(
        cwd, gated=True, args=FORCE_PUSH_ARGS, trail=trail, regen=REGEN_MESSAGE,
    )
    # Only a refusal leaves something an agent could repair. A held, lost, or
    # unverified push says nothing is wrong with the worktree, and handing one to
    # the fix pass asks an agent to rewrite code that passed every check.
    refused = landed.push is not None and landed.push.status is push.PushStatus.REFUSED
    if not refused or not resolved_files or not landed.error:
        return landed

    log.info("Attempting to fix pre-push check failures...")
    repaired = prepush.fix_push_failures(
        cwd, landed.error, resolved_files, trail=trail,
    )
    return repaired if repaired is not None else landed
