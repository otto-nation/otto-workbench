"""Force-pushing a replayed branch, with the hook-rejection recovery ladder."""

# doc-group: platform

from __future__ import annotations

from collections.abc import Callable

from core import log
from core.trail import Trail
from git import land
from git import push

from . import types as rebase_types

FORCE_PUSH_ARGS = rebase_types.FORCE_PUSH_ARGS
REGEN_MESSAGE = rebase_types.REGEN_MESSAGE

# Given the worktree, what the failing hook printed, and the files this run
# resolved, repair the checks and land the repair — or None if it could not.
CheckFailureFix = Callable[[str, str, list[str]], land.LandResult | None]


def land_rebased(
    cwd: str, resolved_files: list[str] | None = None, *,
    trail: Trail | None = None,
    on_check_failure: CheckFailureFix | None = None,
) -> land.LandResult:
    """Force-push the replayed branch, auto-recovering from a hook rejection.

    Two recoveries sit under this and only the second is the rebase's. `land`
    commits whatever the pre-push hook regenerated and pushes once more; when
    that second run still reports check failures, the check-failure fix hands
    them to the AI and lands the repair. A hook can both rewrite a file and fail
    a check, so the two are a ladder rather than alternatives — collapsing them
    would make the AI fix unreachable on any repo whose hooks regenerate
    anything.

    Whether any of it reaches the remote is the publishing gate's answer, not an
    argument here: the entry point opens the gate for the modes that push, so
    `--no-push` comes back `held`, with the force-push command in `resume`,
    rather than as a failure or as a hand-written hint.

    # ceiling: the second rung is injected rather than imported, because the
    # pre-push fix loop is still in the pr-rebase binary and a module cannot
    # import a binary. Retire the parameter and import fix.prepush directly —
    # a downward layer-6-to-5 import, so it is already legal — once the
    # pre-push fix adapter lands.
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
    if on_check_failure is None:
        return landed

    log.info("Attempting to fix pre-push check failures...")
    repaired = on_check_failure(cwd, landed.error, resolved_files)
    return repaired if repaired is not None else landed
